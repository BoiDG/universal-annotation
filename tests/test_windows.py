"""Native ABI and clipboard tests without touching the system clipboard."""

import ctypes
import sys
import unittest
from unittest.mock import patch, MagicMock

if sys.platform == "win32":
    from prompt_scratchpad import windows


@unittest.skipUnless(sys.platform == "win32", "Windows adapter")
class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.desktop = windows.WindowsDesktop(123)

    def test_input_abi_and_safe_copy_shortcuts(self):
        self.assertEqual(ctypes.sizeof(windows.INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        for window_class, expected_keys in (("editor", [0x11, 0x2D]), ("CASCADIA_HOSTING_WINDOW_CLASS", [0x11, 0x10, ord("C")])):
            sent = []

            def send(count, events, size):
                sent.extend((events[index].ki.wVk, events[index].ki.dwFlags) for index in range(count))
                return count

            with patch.object(self.desktop, "foreground", return_value=7), patch.object(self.desktop, "describe", return_value=("Cursor", "test", window_class)), patch.object(windows.user32, "SendInput", side_effect=send):
                self.desktop.copy_selection(7, window_class)
            self.assertEqual([key for key, flags in sent if flags == 0], expected_keys)
            self.assertEqual([key for key, flags in sent if flags == 2], list(reversed(expected_keys)))

    def test_browser_and_codex_receive_their_copy_shortcut(self):
        for app in ("Codex", "chrome", "msedge", "firefox"):
            sent = []
            def send(count, events, size):
                sent.extend(events[index].ki.wVk for index in range(count) if events[index].ki.dwFlags == 0)
                return count
            with patch.object(self.desktop, "foreground", return_value=7), patch.object(self.desktop, "describe", return_value=(app, "test", "Chrome_WidgetWin_1")), patch.object(windows.user32, "SendInput", side_effect=send):
                self.desktop.copy_selection(7, "Chrome_WidgetWin_1")
            self.assertEqual(sent, [0x11, ord("C")])

    def test_hglobal_snapshot_and_restore_without_system_clipboard(self):
        raw = ("existing α 😀\0").encode("utf-16-le")
        handle = windows.kernel32.GlobalAlloc(2, len(raw))
        pointer = windows.kernel32.GlobalLock(handle)
        ctypes.memmove(pointer, raw, len(raw))
        windows.kernel32.GlobalUnlock(handle)
        transferred = []
        clipboard = MagicMock()
        clipboard.EnumClipboardFormats.side_effect = [13, 0]

        def receive(fmt, new_handle):
            transferred.append(new_handle)
            data_pointer = windows.kernel32.GlobalLock(new_handle)
            try:
                self.assertEqual(ctypes.string_at(data_pointer, windows.kernel32.GlobalSize(new_handle)), raw)
            finally:
                windows.kernel32.GlobalUnlock(new_handle)
            return new_handle

        try:
            with patch.object(windows, "win32clipboard", clipboard), patch.object(windows.user32, "GetClipboardData", return_value=handle), patch.object(windows.user32, "SetClipboardData", side_effect=receive):
                backup = self.desktop.snapshot()
                self.assertEqual(backup, [(13, raw)])
                self.desktop._replace_locked(backup)
                clipboard.EmptyClipboard.assert_called_once()
        finally:
            windows.kernel32.GlobalFree(handle)
            for transferred_handle in transferred:
                windows.kernel32.GlobalFree(transferred_handle)

    def test_non_memory_clipboard_is_rejected_before_any_mutation(self):
        clipboard = MagicMock()
        clipboard.EnumClipboardFormats.side_effect = [2, 0]
        with patch.object(windows, "win32clipboard", clipboard), self.assertRaisesRegex(windows.CaptureError, "cannot be safely restored"):
            self.desktop.snapshot()
        clipboard.EmptyClipboard.assert_not_called()

    def test_restore_checks_sequence_under_clipboard_lock(self):
        with patch.object(self.desktop, "_open"), patch.object(self.desktop, "sequence", return_value=2), patch.object(self.desktop, "_replace_locked") as replace, patch.object(windows.win32clipboard, "CloseClipboard"):
            self.assertFalse(self.desktop.restore([], expected_sequence=1))
            replace.assert_not_called()

    def test_sentinel_rejects_clipboard_changed_after_backup(self):
        self.desktop._backup = [(13, b"old clipboard")]
        self.desktop._snapshot_sequence = 1
        with patch.object(self.desktop, "_open"), patch.object(self.desktop, "sequence", return_value=2), patch.object(self.desktop, "_replace_locked") as replace, patch.object(windows.win32clipboard, "CloseClipboard"):
            with self.assertRaisesRegex(windows.CaptureError, "changed before capture"):
                self.desktop.sentinel("marker")
            replace.assert_not_called()
        self.assertIsNone(self.desktop._backup)
        self.assertIsNone(self.desktop._snapshot_sequence)

    def test_sentinel_failure_restores_then_releases_backup(self):
        backup = [(13, b"old clipboard")]
        self.desktop._backup = backup
        self.desktop._snapshot_sequence = 1
        with patch.object(self.desktop, "_open"), patch.object(self.desktop, "sequence", return_value=1), patch.object(self.desktop, "_replace_locked", side_effect=[RuntimeError("write failed"), None]) as replace, patch.object(windows.win32clipboard, "CloseClipboard"):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.desktop.sentinel("marker")
        self.assertEqual(replace.call_args_list[-1].args[0], backup)
        self.assertIsNone(self.desktop._backup)

    def test_sentinel_success_releases_backup(self):
        self.desktop._backup = [(13, b"old clipboard")]
        self.desktop._snapshot_sequence = 1
        with patch.object(self.desktop, "_open"), patch.object(self.desktop, "sequence", side_effect=[1, 2]), patch.object(self.desktop, "_replace_locked"), patch.object(windows.win32clipboard, "CloseClipboard"):
            self.assertEqual(self.desktop.sentinel("marker"), 2)
        self.assertIsNone(self.desktop._backup)
