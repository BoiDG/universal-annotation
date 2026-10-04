"""Exercise the real bundled Chromium editor, Qt bridge, and capture insertion."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")

from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtWidgets import QFileDialog
from PySide6.QtWidgets import QApplication

from prompt_scratchpad.app import ScratchpadWindow
from prompt_scratchpad.context import Context


app = QApplication([])


def pump(seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def javascript(window, script):
    results = []
    window.composer.page.runJavaScript(script, results.append)
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        pump(0.01)
    assert results, "JavaScript evaluation timed out"
    pump()
    return results[0]


def memory_snapshot(renderer_pid):
    if os.name != "nt":
        return None
    import win32api
    import win32process
    totals = {"working_set_mb": 0, "private_mb": 0}
    for pid in (os.getpid(), renderer_pid):
        try:
            handle = win32api.OpenProcess(0x410, False, pid)
        except Exception:
            continue  # A discarded renderer has already exited.
        try:
            if win32process.GetExitCodeProcess(handle) != 259:
                continue
            info = win32process.GetProcessMemoryInfo(handle)
            totals["working_set_mb"] += info["WorkingSetSize"] / 1024**2
            totals["private_mb"] += info["PagefileUsage"] / 1024**2
        finally:
            handle.Close()
    return {name: round(value, 1) for name, value in totals.items()}


with tempfile.TemporaryDirectory() as directory:
    window = ScratchpadWindow(Path(directory) / "draft.json", desktop_enabled=False, ipc_enabled=False)
    errors = []
    window.composer.error.connect(errors.append)
    window.show()
    deadline = time.monotonic() + 15
    while not window.composer.ready and time.monotonic() < deadline:
        pump(0.05)
    assert window.composer.ready, f"Markdown component failed to start: {errors}"
    original = "# Fix the validator\n\nExplain **why** it fails.\n\n````python\nprint('😀')\n# ``` embedded fence\n````\n"
    window.editor.setPlainText(original)
    pump()
    assert javascript(window, "window.scratchpad.text()") == original
    assert javascript(window, "document.querySelector('.cm-h1') !== null")
    assert javascript(window, "getComputedStyle(document.querySelector('.cm-cursor')).borderLeftColor") == "rgb(242, 230, 211)"
    javascript(window, "window.scratchpad.setSource(true); window.scratchpad.setSource(false)")
    assert window.editor.toPlainText() == original, "Mode switching rewrote Markdown"
    with patch("pyperclip.copy") as copy:
        window.copy_prompt()
        copy.assert_called_once_with(original)
    # A formatted-view edit changes source only where the edit was made.
    javascript(window, "window.scratchpad.view.dispatch({selection:{anchor:2,head:19}}); window.scratchpad.command('bold')")
    edited = window.editor.toPlainText()
    assert edited.startswith("# **Fix the validator**"), repr(edited)
    assert "````python\nprint('😀')\n# ``` embedded fence\n````\n" in edited
    # Browser caret -> Qt capture insertion -> browser, including UTF-16 offsets.
    javascript(window, "window.scratchpad.view.dispatch({selection:{anchor:window.scratchpad.text().length,head:window.scratchpad.text().length}})")
    window.receive_context(Context("line α\n```literal```", application="Fixture"))
    pump()
    assert "line α\n```literal```" in javascript(window, "window.scratchpad.text()")
    assert len(window.editor.spans) == 1
    window.undo_capture()
    pump()
    assert javascript(window, "window.scratchpad.text()") == edited
    # Keyboard input in the actual contenteditable editor reaches Python.
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window.composer.focus_editor()
    pump()
    QTest.keyClicks(window.composer.web.focusProxy(), "Notes after")
    pump()
    assert window.editor.toPlainText().endswith("Notes after")
    # The web editor's copy shortcut uses the complete canonical document.
    with patch("pyperclip.copy") as copy:
        QTest.keyClick(window.composer.web.focusProxy(), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        pump()
        copy.assert_called_once_with(window.editor.toPlainText())
    # An attached image remains a Markdown path in the canonical document but
    # loads as a visible preview while editing.
    sample = QImage(2000, 1200, QImage.Format.Format_ARGB32)
    sample.fill(0xff22aa66)
    source = Path(directory) / "sample.png"
    assert sample.save(str(source))
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "Images")):
        javascript(window, "document.getElementById('image').click()")
        pump(0.25)
    attached = next(window.attachments.directory.glob("*.png"))
    pump(0.4)
    assert f"![sample](<{attached.as_posix()}>)" in window.editor.toPlainText()
    assert javascript(window, "Boolean(document.querySelector('.attached-image img')?.complete && document.querySelector('.attached-image img')?.naturalWidth)")
    assert javascript(window, "document.querySelector('.attached-image img').naturalWidth <= 1240 && document.querySelector('.attached-image img').naturalHeight <= 680")
    assert QImage(str(attached)).size() == sample.size(), "Preview resizing changed the original image"
    # Release the actual renderer in the tray, then restore editable state and
    # undo/redo history. Use a short idle interval only for this fixture.
    from PySide6.QtWebEngineCore import QWebEnginePage
    javascript(window, "window.scratchpad.view.dispatch({changes:{from:window.scratchpad.text().length,insert:' sleep-check'}})")
    before_sleep = window.editor.toPlainText()
    javascript(window, "window.scratchpad.setSource(true)")
    caret = window.editor.textCursor().position()
    # Reopening while the asynchronous state snapshot is pending must cancel
    # suspension, and a stale snapshot must never discard newer prompt text.
    import json
    saved_state = javascript(window, "JSON.stringify(window.scratchpad.saveState())")
    callbacks = []
    window.hide()
    window.composer.idle_timer.stop()
    with patch.object(window.composer.page, "runJavaScript", side_effect=lambda script, callback: callbacks.append(callback)):
        window.composer.suspend_editor()
    window.show()
    callbacks.pop()(saved_state)
    assert window.composer.ready
    assert window.composer.page.lifecycleState() == QWebEnginePage.LifecycleState.Active
    window.hide()
    window.composer.idle_timer.stop()
    stale_state = json.loads(saved_state)
    stale_state["editor"]["doc"] += "stale"
    with patch.object(window.composer.page, "runJavaScript", side_effect=lambda script, callback: callback(json.dumps(stale_state))):
        window.composer.suspend_editor()
    assert window.composer.ready
    assert window.composer.page.lifecycleState() == QWebEnginePage.LifecycleState.Active
    window.show()
    renderer_pid = window.composer.page.renderProcessPid()
    visible_memory = memory_snapshot(renderer_pid)
    window.composer.idle_timer.setInterval(50)
    window.hide()
    deadline = time.monotonic() + 5
    while window.composer.page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded and time.monotonic() < deadline:
        pump(0.02)
    assert window.composer.page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded, (window.composer.ready, window.composer.page.isVisible(), window.composer.resume_state, errors)
    assert not window.composer.ready
    assert window.composer.resume_state is not None
    pump(0.4)
    sleeping_memory = memory_snapshot(renderer_pid)
    print(f"Fixture main + renderer memory (MiB): visible={visible_memory}, asleep={sleeping_memory}")
    with patch("pyperclip.copy") as copy:
        window.copy_prompt()
        copy.assert_called_once_with(before_sleep)
    window.show()
    deadline = time.monotonic() + 10
    while not window.composer.ready and time.monotonic() < deadline:
        pump(0.02)
    assert window.composer.ready
    assert javascript(window, "window.scratchpad.text()") == before_sleep
    assert window.editor.textCursor().position() == caret
    assert javascript(window, "document.body.classList.contains('source-mode')")
    window.composer.focus_editor()
    pump()
    QTest.keyClick(window.composer.web.focusProxy(), Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    pump()
    assert not window.editor.toPlainText().endswith(' sleep-check'), "Undo history was lost while sleeping"
    QTest.keyClick(window.composer.web.focusProxy(), Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    pump()
    assert window.editor.toPlainText() == before_sleep, "Redo history was lost while sleeping"
    javascript(window, "window.scratchpad.setSource(false)")
    window.hide()
    deadline = time.monotonic() + 5
    while window.composer.page.lifecycleState() != QWebEnginePage.LifecycleState.Discarded and time.monotonic() < deadline:
        pump(0.02)
    assert window.composer.page.lifecycleState() == QWebEnginePage.LifecycleState.Discarded
    window.receive_context(Context("captured while asleep", application="Fixture"))
    deadline = time.monotonic() + 10
    while not window.composer.ready and time.monotonic() < deadline:
        pump(0.02)
    assert window.composer.ready
    assert "captured while asleep" in javascript(window, "window.scratchpad.text()")
    assert not javascript(window, "document.body.classList.contains('source-mode')")
    assert not errors, errors
    window.autosave.stop()
    window.composer.shutdown()
    window.server.close()
    window.executor.shutdown(wait=True)
    window.hide()
    pump()
print("PASS: real Markdown component, caret, image preview, mode round-trip, toolbar, capture, undo, typing, Ctrl+Enter, renderer sleep/reload, history restoration, and capture while asleep")
