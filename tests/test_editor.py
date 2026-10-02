import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from prompt_scratchpad.context import Context
from prompt_scratchpad.editor import PromptEditor
from prompt_scratchpad.session import load_session, save_session

app = QApplication.instance() or QApplication([])


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.editor = PromptEditor()

    def tearDown(self):
        self.editor.deleteLater()
        app.processEvents()

    def test_insert_at_caret_preserves_surrounding_notes_and_editor_selection(self):
        self.editor.setPlainText("before\nafter")
        cursor = self.editor.textCursor()
        cursor.setPosition(7)
        cursor.setPosition(10, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.insert_context(Context("print('x')", application="VS Code"))
        text = self.editor.toPlainText()
        self.assertTrue(text.startswith("before\naft\n\n### Context"))
        self.assertTrue(text.endswith("\n\ner"))
        self.editor.undo_capture()
        self.assertEqual(self.editor.toPlainText(), "before\nafter")

    def test_unicode_offsets_and_notes_before_and_after_survive_undo(self):
        self.editor.setPlainText("😀 instructions\n")
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.insert_context(Context("𝄞 code\nline two"))
        self.editor.insertPlainText("notes after")
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.insertText("notes before\n")
        self.editor.undo_capture()
        self.assertEqual(self.editor.toPlainText(), "notes before\n😀 instructions\nnotes after")

    def test_edited_capture_cannot_discard_user_edits(self):
        self.editor.insert_context(Context("sample"))
        cursor = self.editor.document().find("sample")
        cursor.insertText("edited sample")
        before = self.editor.toPlainText()
        with self.assertRaisesRegex(ValueError, "was edited"):
            self.editor.undo_capture()
        self.assertEqual(self.editor.toPlainText(), before)

    def test_only_most_recent_capture_is_removed(self):
        self.editor.insert_context(Context("first"))
        before = self.editor.toPlainText()
        self.editor.insert_context(Context("second"))
        self.editor.undo_capture()
        self.assertEqual(self.editor.toPlainText(), before)
        self.assertEqual(len(self.editor.spans), 1)

    def test_session_roundtrip_retains_unicode_and_capture_undo(self):
        self.editor.insert_context(Context("漢字 😀\n\n```nested```"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            save_session(path, {"text": self.editor.toPlainText(), "captures": self.editor.capture_records()})
            draft = load_session(path)
        other = PromptEditor()
        other.setPlainText(draft["text"])
        other.restore_records(draft["captures"])
        other.undo_capture()
        self.assertEqual(other.toPlainText(), "")
        other.deleteLater()

    def test_normal_undo_and_clear_are_reversible(self):
        self.editor.setPlainText("notes")
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.insert_context(Context("code"))
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "notes")
        self.assertEqual(len(self.editor.spans), 0)
        self.editor.reset()
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "notes")

    def test_corrupt_session_does_not_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            path.write_text('"not a draft object"', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_session(path)
