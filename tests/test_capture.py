import unittest
from unittest.mock import MagicMock

from prompt_scratchpad.capture import CaptureError, capture_selection


class FakeDesktop:
    def __init__(self, selection="selected code"):
        self.clipboard = "original clipboard"
        self.sequence_number = 1
        self.selection = selection
        self.window = 7
        self.restore_calls = 0
        self.fail_snapshot = False
        self.fail_copy = False
        self.interference = False

    def wait_for_hotkey_release(self):
        pass

    def foreground(self):
        return self.window

    def describe(self, window):
        return "Cursor", "main.py", "editor"

    def snapshot(self):
        if self.fail_snapshot:
            raise CaptureError("unsafe clipboard")
        return self.clipboard

    def sentinel(self, text):
        self.clipboard = text
        self.sequence_number += 1
        return self.sequence_number

    def sequence(self):
        return self.sequence_number

    def copy_selection(self, window, window_class):
        if self.fail_copy:
            raise CaptureError("copy blocked")
        if self.selection is not None:
            self.clipboard = self.selection
            self.sequence_number += 1

    def read_text(self):
        return self.sequence_number, self.clipboard

    def restore(self, snapshot, expected_sequence):
        self.restore_calls += 1
        if self.interference:
            self.clipboard = "new clipboard from another app"
            self.sequence_number += 1
        if self.sequence_number != expected_sequence:
            return False
        self.clipboard = snapshot
        return True


class CaptureTests(unittest.TestCase):
    def test_success_restores_previous_clipboard(self):
        desktop = FakeDesktop()
        context = capture_selection(desktop, 7)
        self.assertEqual(context.text, "selected code")
        self.assertEqual(context.application, "Cursor")
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_selection_equal_to_original_still_captures(self):
        desktop = FakeDesktop("original clipboard")
        self.assertEqual(capture_selection(desktop, 7).text, "original clipboard")

    def test_no_selection_never_captures_stale_clipboard(self):
        desktop = FakeDesktop(None)
        with self.assertRaisesRegex(CaptureError, "No selection"):
            capture_selection(desktop, 7, timeout=0.01)
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_blocked_input_restores_clipboard(self):
        desktop = FakeDesktop()
        desktop.fail_copy = True
        with self.assertRaisesRegex(CaptureError, "copy blocked"):
            capture_selection(desktop, 7)
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_failed_backup_leaves_original_untouched(self):
        desktop = FakeDesktop()
        desktop.fail_snapshot = True
        with self.assertRaisesRegex(CaptureError, "unsafe clipboard"):
            capture_selection(desktop, 7)
        self.assertEqual(desktop.sequence_number, 1)
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_focus_change_before_capture_leaves_clipboard_untouched(self):
        desktop = FakeDesktop()
        with self.assertRaisesRegex(CaptureError, "Focus changed"):
            capture_selection(desktop, 8)
        self.assertEqual(desktop.sequence_number, 1)

    def test_intervening_clipboard_change_is_not_overwritten(self):
        desktop = FakeDesktop()
        desktop.interference = True
        with self.assertRaisesRegex(CaptureError, "left untouched"):
            capture_selection(desktop, 7)
        self.assertEqual(desktop.clipboard, "new clipboard from another app")

    def test_empty_copy_restores_clipboard(self):
        desktop = FakeDesktop(" ")
        with self.assertRaisesRegex(CaptureError, "no usable text"):
            capture_selection(desktop, 7)
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_intermediate_empty_clipboard_waits_for_completed_copy(self):
        desktop = FakeDesktop("")
        original_read = desktop.read_text
        reads = []
        def read():
            result = original_read()
            reads.append(result)
            if len(reads) == 1:
                desktop.clipboard = "finished selection"
                desktop.sequence_number += 1
            return result
        desktop.read_text = read
        self.assertEqual(capture_selection(desktop, 7).text, "finished selection")
        self.assertEqual(len(reads), 2)
        self.assertEqual(desktop.clipboard, "original clipboard")

    def test_accessibility_selection_never_touches_clipboard(self):
        desktop = FakeDesktop()
        desktop.read_selection = MagicMock(return_value="selected via accessibility")
        desktop.snapshot = MagicMock()
        result = capture_selection(desktop, 7)
        self.assertEqual(result.text, "selected via accessibility")
        desktop.snapshot.assert_not_called()
        self.assertEqual(desktop.sequence_number, 1)
