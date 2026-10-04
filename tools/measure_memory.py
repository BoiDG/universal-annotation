"""Measure a disposable Windows editor fixture and every child process.

No real draft, clipboard, global hotkeys, or bridge is used. --native measures
the existing plain-text test editor; it is not a feature-equivalent replacement.
"""

import argparse
import ctypes
from ctypes import wintypes
import gc
import json
import os
from pathlib import Path
import tempfile
import time

import win32api
import win32process
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtWidgets import QApplication

from prompt_scratchpad.app import ScratchpadWindow
from prompt_scratchpad.attachments import image_markdown


def descendants(root):
    class Entry(ctypes.Structure):
        _fields_ = [
            ('size', wintypes.DWORD), ('usage', wintypes.DWORD),
            ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
            ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
            ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
            ('flags', wintypes.DWORD), ('name', wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        found = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            parents[entry.pid] = (entry.parent, entry.name)
            found = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    selected = {root}
    while True:
        children = {pid for pid, (parent, _) in parents.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    return [(pid, parents[pid][1]) for pid in sorted(selected) if pid in parents]


def memory():
    rows = []
    for pid, name in descendants(os.getpid()):
        try:
            handle = win32api.OpenProcess(0x410, False, pid)
        except Exception:
            continue  # Child exited between enumeration and OpenProcess.
        try:
            if win32process.GetExitCodeProcess(handle) != 259:
                continue
            info = win32process.GetProcessMemoryInfo(handle)
            rows.append(dict(pid=pid, name=name,
                             working_set_mib=round(info['WorkingSetSize'] / 1024**2, 1),
                             private_mib=round(info['PagefileUsage'] / 1024**2, 1)))
        finally:
            handle.Close()
    return dict(working_set_mib=round(sum(row['working_set_mib'] for row in rows), 1),
                private_mib=round(sum(row['private_mib'] for row in rows), 1), processes=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--software', action='store_true', help='Measure Chromium software rendering')
    parser.add_argument('--original-images', action='store_true', help='Bypass preview resizing for an A/B comparison')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.software:
        os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu --disable-gpu-compositing')
    app = QApplication([])

    def pump(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)

    def wait_ready():
        deadline = time.monotonic() + 15
        while window.composer and not window.composer.ready and time.monotonic() < deadline:
            pump(0.02)
        if window.composer and not window.composer.ready:
            raise RuntimeError('Editor did not load')

    report = dict(native=args.native, software=args.software, original_images=args.original_images)
    with tempfile.TemporaryDirectory() as directory:
        window = ScratchpadWindow(Path(directory) / 'draft.json', desktop_enabled=False,
                                  ipc_enabled=False, web_enabled=not args.native)
        # Keep this fixture out of the user's taskbar and tray.
        window.setWindowFlag(Qt.WindowType.Tool)
        window.resize(1000, 1500)
        window.tray.hide()
        if args.original_images and window.composer and hasattr(window.composer, 'previews'):
            window.composer.previews.path_for = lambda path: Path(path)
        window.editor.setPlainText('# Memory fixture\n\nKeep **Markdown** and 😀 exactly.\n')
        window.show()
        wait_ready()
        pump(2)
        report['text_only'] = memory()
        image = QImage(4000, 3000, QImage.Format.Format_RGB32)
        image.fill(0xff4488aa)
        for index in range(3):
            source = Path(directory) / f'sample-{index}.jpg'
            if not image.save(str(source), 'JPEG'):
                raise RuntimeError('Fixture image could not be saved')
            attached = window.attachments.attach_file(source)
            window.editor.moveCursor(QTextCursor.MoveOperation.End)
            window.editor.insertPlainText(image_markdown(attached, source.name) + '\n\n')
        del image
        gc.collect()
        pump(3)
        if window.composer:
            results = []
            window.composer.page.runJavaScript(
                'JSON.stringify(Array.from(document.querySelectorAll(".attached-image img"), img => '
                '({loaded: img.complete && img.naturalWidth > 0, width: img.naturalWidth, height: img.naturalHeight})))',
                results.append)
            deadline = time.monotonic() + 5
            while not results and time.monotonic() < deadline:
                pump(0.02)
            dimensions = json.loads(results[0]) if results and results[0] else []
            if len(dimensions) != 3 or not all(item['loaded'] for item in dimensions):
                raise RuntimeError(f'Fixture images did not load: {results}')
            report['preview_dimensions'] = dimensions
        report['three_12mp_images'] = memory()
        text = window.editor.toPlainText()
        if window.composer:
            window.composer.idle_timer.setInterval(50)
        window.hide()
        pump(3)
        if window.composer and (window.composer.ready or window.composer.resume_state is None):
            raise RuntimeError('Editor did not suspend')
        report['tray'] = memory()
        window.show()
        wait_ready()
        pump(2)
        assert window.editor.toPlainText() == text, 'Suspension changed the prompt'
        report['reopened'] = memory()
        window.autosave.stop()
        if window.composer:
            window.composer.shutdown()
        window.server.close()
        window.executor.shutdown(wait=True)
        window.hide()
        window.deleteLater()
        pump(0.2)
    output = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + '\n', encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
