import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from prompt_scratchpad.app import ScratchpadWindow
from prompt_scratchpad.context import Context, EditorSelection
from prompt_scratchpad.session import load_session

app = QApplication.instance() or QApplication([])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "draft.json"
        self.window = ScratchpadWindow(self.path, desktop_enabled=False, ipc_enabled=False, web_enabled=False)

    def tearDown(self):
        self.window.autosave.stop()
        self.window.mode_timeout.stop()
        self.window.server.close()
        self.window.executor.shutdown(wait=True)
        self.window.tray.hide()
        self.window.hide()
        self.window.deleteLater()
        app.processEvents()
        self.directory.cleanup()

    def test_copy_does_not_transform_document(self):
        text = "Instructions\n\n```python\nprint('α 😀')\n```\n"
        self.window.editor.setPlainText(text)
        with patch("pyperclip.copy") as copy:
            self.window.copy_prompt()
        copy.assert_called_once_with(text)

    def test_capture_mode_success_disarms_and_saves(self):
        self.window.toggle_capture_mode()
        self.assertTrue(self.window.armed)
        self.window.receive_context(Context("print('hi')", application="Cursor"))
        self.assertFalse(self.window.armed)
        self.assertEqual(self.window.count.text(), "1 context")
        self.assertTrue(self.window.save())
        self.assertEqual(load_session(self.path)["text"], self.window.editor.toPlainText())

    def test_copy_is_blocked_while_clipboard_capture_is_in_progress(self):
        self.window.busy = True
        with patch("pyperclip.copy") as copy:
            self.window.copy_prompt()
        copy.assert_not_called()
        self.window.busy = False

    def test_always_on_top_updates_native_clipboard_owner(self):
        from unittest.mock import MagicMock
        self.window.desktop = MagicMock()
        self.window.on_top.setChecked(True)
        self.assertEqual(self.window.desktop.owner, int(self.window.winId()))

    def test_quitting_during_capture_restores_then_keeps_captured_draft(self):
        self.window.busy = True
        with patch.object(self.window, "save") as save:
            self.window.quit()
        save.assert_not_called()
        self.assertTrue(self.window.quitting)
        with patch.object(self.window, "quit") as quit_app, patch.object(self.window, "bring_forward") as show:
            self.window.capture_completed(Context("last captured text"), "")
        quit_app.assert_called_once()
        show.assert_not_called()
        self.assertIn("last captured text", self.window.editor.toPlainText())

    def test_corrupt_draft_is_preserved(self):
        self.path.write_text("broken json", encoding="utf-8")
        self.window._restore()
        self.window.editor.setPlainText("new text")
        self.assertTrue(self.window.save())
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken json")
        self.assertEqual(load_session(self.path.with_name("draft-recovered.json"))["text"], "new text")
        self.window.draft_path = self.path
        self.window._restore()
        self.assertEqual(self.window.editor.toPlainText(), "new text")

    def test_ide_hotkey_inserts_reference_without_copying_code(self):
        from unittest.mock import MagicMock
        self.window.desktop = MagicMock()
        self.window.desktop.describe.return_value = ("Cursor", "main.py - Workspace - Cursor", "Chrome_WidgetWin_1")
        self.window.receive_editor_selection(EditorSelection("Cursor", "C:/repo/main.py", 12, 18))
        self.window.capture(123)
        self.assertEqual(self.window.editor.toPlainText().strip(), "### Code reference — Cursor\n\nFile: C:/repo/main.py\nLines: 12–18")
        self.window.desktop.snapshot.assert_not_called()

    def test_ide_hotkey_does_not_use_unavailable_location(self):
        from unittest.mock import MagicMock
        self.window.desktop = MagicMock()
        self.window.desktop.describe.return_value = ("Cursor", "main.py - Workspace - Cursor", "Chrome_WidgetWin_1")
        self.window.capture(123)
        self.assertEqual(self.window.editor.toPlainText(), "")
        self.window.desktop.snapshot.assert_not_called()

    def test_ide_hotkey_rejects_location_from_another_workspace(self):
        from unittest.mock import MagicMock
        self.window.desktop = MagicMock()
        self.window.desktop.describe.return_value = ("Cursor", "main.py - workspace-b - Cursor", "Chrome_WidgetWin_1")
        self.window.receive_editor_selection(EditorSelection("Cursor", "C:/workspace-a/main.py", 12, 18, "workspace-a"))
        self.window.capture(123)
        self.assertEqual(self.window.editor.toPlainText(), "")

    def test_ide_hotkey_rejects_location_from_another_editor(self):
        from unittest.mock import MagicMock
        self.window.desktop = MagicMock()
        self.window.desktop.describe.return_value = ("Cursor", "main.py - workspace - Cursor", "Chrome_WidgetWin_1")
        self.window.receive_editor_selection(EditorSelection("Visual Studio Code", "C:/workspace/main.py", 12, 18, "workspace"))
        self.window.capture(123)
        self.assertEqual(self.window.editor.toPlainText(), "")
