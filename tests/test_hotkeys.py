import unittest
from prompt_scratchpad.hotkeys import bindings, CAPTURE, ARM, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT


class HotkeyTests(unittest.TestCase):
    def test_capture_and_mode_are_distinct_nonrepeating_native_bindings(self):
        first, second = bindings()
        self.assertEqual(first, (CAPTURE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("A")))
        self.assertEqual(second, (ARM, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, ord("A")))
        self.assertNotEqual(first[1], second[1])
