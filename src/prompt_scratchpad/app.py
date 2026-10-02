"""Desktop entry point; all editor mutations stay on the Qt GUI thread."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import time

from PySide6.QtCore import QByteArray, QLockFile, QObject, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from .capture import capture_selection
from .attachments import AttachmentStore, image_markdown
from .context import Context
from .editor import PromptEditor
from .ipc import CaptureServer, SERVER_NAME
from .session import load_session, save_session


class Events(QObject):
    capture = Signal(int)
    arm = Signal()
    cancel = Signal()
    completed = Signal(object, str)
    failed = Signal(str)


def app_icon() -> QIcon:
    return QIcon(str(Path(__file__).with_name("assets") / "app.ico"))


class ScratchpadWindow(QMainWindow):
    def __init__(self, draft_path: Path, *, desktop_enabled=True, ipc_enabled=True, web_enabled=True):
        super().__init__()
        self.setWindowTitle("Prompt Scratchpad")
        self.setWindowIcon(app_icon())
        self.resize(800, 620)
        self.setMinimumSize(520, 360)
        self.draft_path = draft_path
        self.attachments = AttachmentStore(draft_path)
        self.busy = False
        self.armed = False
        self.quitting = False
        self.hotkeys = None
        self.desktop = None
        self.web_enabled = web_enabled
        self.composer = None
        self.editor_selection = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="selection-capture")
        self.events = Events(self)
        self.events.capture.connect(self.capture)
        self.events.arm.connect(self.toggle_capture_mode)
        self.events.cancel.connect(self.cancel_capture_mode)
        self.events.completed.connect(self.capture_completed)
        self.events.failed.connect(self.report)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.setInterval(600)
        self.autosave.timeout.connect(self.save)
        self.mode_timeout = QTimer(self)
        self.mode_timeout.setSingleShot(True)
        self.mode_timeout.setInterval(30000)
        self.mode_timeout.timeout.connect(self.cancel_capture_mode)
        self._build_ui()
        self._restore()
        self.editor.textChanged.connect(self.schedule_save)
        self._build_tray()
        self.server = CaptureServer(self.receive_context, self, show=self.show_from_bridge, selection=self.receive_editor_selection)
        if ipc_enabled and not self.server.start():
            self.executor.shutdown(wait=False)
            raise RuntimeError("Prompt Scratchpad is already running, or its local bridge could not start. Check the system tray.")
        if desktop_enabled:
            self._start_desktop()

    def _build_ui(self):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 18, 20, 14)
        header = QHBoxLayout()
        title = QLabel("Prompt Scratchpad")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.count = QLabel("0 contexts")
        self.count.setObjectName("count")
        header.addWidget(self.count)
        layout.addLayout(header)
        self.hint = QLabel("Select → Ctrl+Alt+A → compose → Ctrl+Enter")
        self.hint.setObjectName("hint")
        layout.addWidget(self.hint)
        self.editor = PromptEditor()
        self.editor.setParent(body)
        self.editor.capture_count_changed.connect(lambda count: self.count.setText(f"{count} context{'s' if count != 1 else ''}"))
        if self.web_enabled:
            from .composer import MarkdownComposer
            self.composer = MarkdownComposer(self.editor, self.attachments.directory, body)
            self.composer.copy_requested.connect(self.copy_prompt)
            self.composer.cancel_requested.connect(self.cancel_capture_mode)
            self.composer.error.connect(self.report)
            self.composer.image_dialog_requested.connect(self.attach_image_dialog)
            self.composer.clipboard_image_requested.connect(self.attach_clipboard_image)
            self.composer.loaded.connect(self.composer.focus_editor)
            layout.addWidget(self.composer, 1)
        else:
            layout.addWidget(self.editor, 1)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("Copy Prompt")
        self.copy_button.setObjectName("primary")
        self.copy_button.setToolTip("Copy the complete Markdown document · Ctrl+Enter")
        self.copy_button.clicked.connect(self.copy_prompt)
        actions.addWidget(self.copy_button)
        self.clipboard_button = QPushButton("Capture Clipboard")
        self.clipboard_button.setToolTip("Insert text you already copied using the source app's own shortcut")
        self.clipboard_button.clicked.connect(self.capture_clipboard)
        actions.addWidget(self.clipboard_button)
        undo = QPushButton("Undo Last Capture")
        undo.clicked.connect(self.undo_capture)
        actions.addWidget(undo)
        clear = QPushButton("Clear")
        clear.setToolTip("Clear this draft · Ctrl+Z restores the text")
        clear.clicked.connect(self.editor.reset)
        actions.addWidget(clear)
        layout.addLayout(actions)
        footer = QHBoxLayout()
        self.on_top = QCheckBox("Always on top")
        self.on_top.toggled.connect(self.set_on_top)
        footer.addWidget(self.on_top)
        footer.addStretch()
        self.saved = QLabel("Draft saved locally")
        self.saved.setObjectName("hint")
        footer.addWidget(self.saved)
        layout.addLayout(footer)
        self.setCentralWidget(body)
        self.statusBar().showMessage("Ready · Capture mode: Ctrl+Alt+Shift+A")
        for shortcut in ("Ctrl+Return", "Ctrl+Enter"):
            key = QShortcut(QKeySequence(shortcut), self)
            key.activated.connect(self.copy_prompt)
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(self.cancel_capture_mode)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #1e1e1e; color: #e5e4e2; font-family: 'Segoe UI'; font-size: 12px; }
            QLabel#title { font-size: 16px; font-weight: 650; }
            QLabel#hint { color: #979797; }
            QLabel#count { color: #dbc6a9; background: #30302e; border-radius: 9px; padding: 5px 10px; }
            QPlainTextEdit { background: #10151e; border: 1px solid #334155; border-radius: 8px; padding: 14px; selection-background-color: #355986; font-family: 'Cascadia Code', 'Consolas'; font-size: 14px; }
            QPlainTextEdit:focus { border-color: #77adf5; }
            QPushButton { background: #292929; border: 1px solid #444440; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #393733; }
            QPushButton#primary { background: #c9a77b; color: #1d1914; border-color: #c9a77b; font-weight: 650; }
            QPushButton#primary:hover { background: #dbc09c; }
            QStatusBar { color: #a9a7a3; border-top: 1px solid #343434; }
            QMenu { background: #202a39; border: 1px solid #3a4960; }
            QMenu::item:selected { background: #355986; }
        """)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Prompt Scratchpad · Ctrl+Alt+A")
        menu = QMenu(self)
        for title, callback in (("Show Scratchpad", self.bring_forward), ("Arm Capture Mode", self.toggle_capture_mode), ("Copy Prompt", self.copy_prompt), ("Quit", self.quit)):
            action = QAction(title, menu)
            action.triggered.connect(callback)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.bring_forward() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _start_desktop(self):
        if sys.platform != "win32":
            self.report("Global selection capture is available on Windows. Editor and IPC remain available.")
            return
        try:
            from .hotkeys import GlobalHotkeys
            from .windows import WindowsDesktop
            self.desktop = WindowsDesktop(int(self.winId()))
            self.hotkeys = GlobalHotkeys(lambda: self.events.capture.emit(self.desktop.foreground()), self.events.arm.emit, self.events.cancel.emit, self.events.failed.emit)
            self.hotkeys.start()
        except Exception as error:
            self.report(f"Global capture unavailable: {error}")

    def _restore(self):
        try:
            draft = load_session(self.draft_path)
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            self.draft_path = self.draft_path.with_name("draft-recovered.json")
            self.statusBar().showMessage(f"Could not restore draft: {error}. Original file preserved; saving to draft-recovered.json.")
            try:
                draft = load_session(self.draft_path)
            except (OSError, ValueError, TypeError, UnicodeError):
                self.draft_path = self.draft_path.with_name("draft-recovered-2.json")
                draft = {}
        self.editor.setPlainText(draft.get("text", ""))
        self.editor.restore_records(draft.get("captures", []))
        geometry = draft.get("geometry")
        if isinstance(geometry, str) and geometry.isascii():
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        self.on_top.setChecked(draft.get("always_on_top") is True)

    def schedule_save(self):
        self.saved.setText("Saving…")
        self.autosave.start()

    def save(self):
        try:
            save_session(self.draft_path, {"version": 1, "text": self.editor.toPlainText(), "captures": self.editor.capture_records(), "always_on_top": self.on_top.isChecked(), "geometry": bytes(self.saveGeometry().toBase64()).decode("ascii")})
        except (OSError, ValueError) as error:
            self.saved.setText("Draft save failed")
            self.report(f"Could not save draft: {error}")
            return False
        self.saved.setText("Draft saved locally")
        return True

    def set_on_top(self, enabled):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()
        if self.desktop:
            self.desktop.owner = int(self.winId())
        self.schedule_save()

    def bring_forward(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.composer:
            self.composer.focus_editor()
        else:
            self.editor.setFocus()

    def show_from_bridge(self):
        if self.busy:
            raise RuntimeError("Selection capture is in progress. Open the scratchpad shortly.")
        self.bring_forward()

    def toggle_capture_mode(self):
        if self.armed:
            self.cancel_capture_mode()
            return
        if self.busy:
            self.report("A capture is already in progress.")
            return
        self.armed = True
        if self.hotkeys:
            self.hotkeys.set_capture_mode(True)
        self.mode_timeout.start()
        self.hint.setText("Capture mode · select text, then press Ctrl+Alt+A · Esc cancels")
        self.statusBar().showMessage("Capture mode armed for 30 seconds. Select text, then press Ctrl+Alt+A.")
        self.tray.showMessage("Capture mode", "Select text, then press Ctrl+Alt+A. Esc cancels.", QSystemTrayIcon.MessageIcon.Information, 3500)

    def cancel_capture_mode(self):
        if not self.armed:
            return
        self.armed = False
        if self.hotkeys:
            self.hotkeys.set_capture_mode(False)
        self.mode_timeout.stop()
        self.hint.setText("Select → Ctrl+Alt+A → compose → Ctrl+Enter")
        self.statusBar().showMessage("Capture mode ended.")

    def capture(self, window):
        if self.busy or self.quitting:
            return
        if not self.desktop:
            self.report("Windows capture is unavailable. Copy text and use Capture Clipboard.")
            return
        if window == int(self.winId()):
            self.report("Select text in another application first.")
            return
        try:
            application, title, _ = self.desktop.describe(window)
        except Exception as error:
            self.report(f"Could not identify the source window: {error}")
            return
        if application.casefold() in {"cursor", "code", "code - insiders", "vscodium"}:
            cached = self.editor_selection
            process = application.casefold()
            bridge_name = cached[0].application.casefold() if cached else ""
            same_editor = (process == "cursor" and "cursor" in bridge_name) or (process == "vscodium" and "vscodium" in bridge_name) or (process.startswith("code") and ("visual studio code" in bridge_name or bridge_name.startswith("code")))
            if not cached or not same_editor or time.monotonic() - cached[1] > 30 or Path(cached[0].file_path).name.casefold() not in title.casefold() or (cached[0].workspace and cached[0].workspace.casefold() not in title.casefold()):
                self.report("Editor location unavailable. Install/enable the Prompt Scratchpad editor bridge, select code, and try again.")
                return
            selection = cached[0]
            try:
                self.receive_context(Context(text="", application=selection.application, file_path=selection.file_path, start_line=selection.start_line, end_line=selection.end_line, reference_only=True))
            except ValueError as error:
                self.report(str(error))
            return
        self.busy = True
        self.copy_button.setEnabled(False)
        self.clipboard_button.setEnabled(False)
        self.statusBar().showMessage("Capturing selection…")
        future = self.executor.submit(capture_selection, self.desktop, window)

        def completed(result):
            try:
                context = result.result()
            except Exception as error:
                self.events.completed.emit(None, str(error))
            else:
                self.events.completed.emit(context, "")

        future.add_done_callback(completed)

    def capture_completed(self, context, error):
        self.busy = False
        self.copy_button.setEnabled(True)
        self.clipboard_button.setEnabled(True)
        if error:
            self.report(error)
        elif context:
            try:
                if self.quitting:
                    self.editor.insert_context(context)
                else:
                    self.receive_context(context)
            except ValueError as error:
                self.report(str(error))
        if self.quitting:
            self.quit()

    def receive_context(self, context):
        if self.busy or self.quitting:
            raise RuntimeError("Capture is in progress or the app is closing. Try again shortly.")
        self.editor.insert_context(context)
        self.cancel_capture_mode()
        self.statusBar().showMessage("Context inserted at the caret.")
        self.bring_forward()

    def receive_editor_selection(self, selection):
        self.editor_selection = (selection, time.monotonic()) if selection else None

    def capture_clipboard(self):
        if self.busy:
            return
        try:
            import pyperclip
            self.receive_context(Context(text=pyperclip.paste(), application="Clipboard"))
        except Exception as error:
            self.report(str(error))

    def _insert_image(self, path, display_name):
        cursor = self.editor.textCursor()
        preceding = QTextCursor(cursor)
        preceding.clearSelection()
        has_previous = preceding.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor)
        prefix = "\n" if has_previous and preceding.selectedText() not in {"\u2029", "\n"} else ""
        cursor.beginEditBlock()
        cursor.insertText(prefix + image_markdown(path, display_name) + "\n")
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.statusBar().showMessage("Image attached to this draft and referenced in the Markdown prompt.")

    def attach_image_dialog(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Attach image", "", "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        if not filename:
            return
        try:
            path = self.attachments.attach_file(Path(filename))
            self._insert_image(path, Path(filename).name)
        except (OSError, ValueError) as error:
            self.report(f"Could not attach image: {error}")

    def attach_clipboard_image(self):
        try:
            path = self.attachments.attach_clipboard(QApplication.clipboard().image())
            self._insert_image(path, "Pasted image")
        except (OSError, ValueError) as error:
            self.report(f"Could not attach clipboard image: {error}")

    def copy_prompt(self):
        if self.busy:
            self.report("Wait for selection capture to finish before copying the prompt.")
            return
        try:
            import pyperclip
            pyperclip.copy(self.editor.toPlainText())
        except Exception as error:
            self.report(f"Could not copy prompt: {error}")
        else:
            self.statusBar().showMessage("Full Markdown prompt copied. Paste it into your agent.")

    def undo_capture(self):
        try:
            self.editor.undo_capture()
        except ValueError as error:
            self.report(str(error))
        else:
            self.statusBar().showMessage("Last capture removed. Your surrounding notes are preserved.")

    def report(self, message):
        self.statusBar().showMessage(message)
        if hasattr(self, "tray") and not self.isActiveWindow():
            self.tray.showMessage("Prompt Scratchpad", message, QSystemTrayIcon.MessageIcon.Warning, 5000)

    def closeEvent(self, event):
        if self.busy and not self.tray.isVisible():
            self.quit()
            event.ignore()
        elif self.save():
            if self.tray.isVisible() and not self.quitting:
                self.hide()
                event.ignore()
            else:
                event.accept()
                self.quit()
        else:
            event.ignore()

    def quit(self):
        if self.busy:
            self.quitting = True
            self.statusBar().showMessage("Finishing clipboard restoration before quitting…")
            return
        if not self.save():
            self.quitting = False
            self.bring_forward()
            return
        self.quitting = True
        self.autosave.stop()
        self.mode_timeout.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        if self.composer:
            self.composer.shutdown()
        self.server.close()
        self.tray.hide()
        self.executor.shutdown(wait=False)
        QApplication.instance().quit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Floating Markdown prompt composer")
    parser.add_argument("--draft", type=Path, help="Override the local draft file location")
    parser.add_argument("--no-hotkeys", action="store_true", help="Run the editor and IPC without global hotkeys")
    parser.add_argument("--no-ipc", action="store_true", help="Disable the future editor bridge")
    options = parser.parse_args(argv)
    if sys.platform == "win32":
        # Give the taskbar its own identity instead of grouping under Python.
        import ctypes
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        result = set_app_id("PromptScratchpad.Desktop")
        if result < 0:
            raise OSError(f"Could not set Windows app identity: {result:#x}")
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(app_icon())
    app.setOrganizationName("PromptScratchpad")
    app.setApplicationName("Prompt Scratchpad")
    app.setQuitOnLastWindowClosed(False)
    state_directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
    try:
        state_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        QMessageBox.warning(None, "Prompt Scratchpad", f"Could not create draft directory: {error}")
        return 1
    lock = QLockFile(str(state_directory / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(300):
            socket.write(b'{"version":1,"type":"show"}\n')
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
        else:
            QMessageBox.information(None, "Prompt Scratchpad", "Prompt Scratchpad is already running. Open it from the system tray.")
        return 0
    path = options.draft or state_directory / "draft.json"
    try:
        window = ScratchpadWindow(path, desktop_enabled=not options.no_hotkeys, ipc_enabled=not options.no_ipc)
    except RuntimeError as error:
        QMessageBox.information(None, "Prompt Scratchpad", str(error))
        return 1
    window.show()
    if window.composer:
        window.composer.focus_editor()
    else:
        window.editor.setFocus()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
