import unittest

from prompt_scratchpad.context import Context, context_from_message, format_context, selection_from_message


class ContextTests(unittest.TestCase):
    def test_selection_is_verbatim_and_fence_cannot_be_closed_by_code(self):
        text = "α = 1\n```python\nprint('hello')\n```\n````\n"
        result = format_context(Context(text, application="Cursor", window="app.py"))
        self.assertIn("### Context — Cursor — app.py\n\n`````text\n", result)
        self.assertIn(text, result)
        self.assertTrue(result.endswith("`````\n"))

    def test_bridge_metadata_and_language(self):
        context = context_from_message({"version": 1, "type": "capture", "text": "print('hi')", "file_path": "C:/repo/main.py", "workspace": "repo", "language": "python", "start_line": 2, "end_line": 4})
        result = format_context(context)
        self.assertIn("File: C:/repo/main.py", result)
        self.assertIn("Lines: 2–4", result)
        self.assertIn("```python\nprint('hi')\n```", result)

    def test_metadata_cannot_inject_headings_or_fences(self):
        result = format_context(Context("safe", application="App\n### injected", language="text\n```"))
        self.assertNotIn("\n### injected", result)
        self.assertIn("```text\nsafe", result)

    def test_empty_and_invalid_bridge_requests(self):
        base = {"version": 1, "type": "capture", "text": "x"}
        for change in ({"text": " \n"}, {"text": 5}, {"version": 2}, {"start_line": True}, {"start_line": 3, "end_line": 2}, {"end_line": 1}, {"application": []}, {"unknown": "x"}, {"text": "😀" * 250001}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                context_from_message(base | change)

    def test_editor_reference_contains_only_path_and_lines(self):
        selection = selection_from_message({"version": 1, "type": "selection", "active": True, "application": "Cursor", "file_path": "C:\\repo\\src\\main.py", "start_line": 12, "end_line": 18})
        result = format_context(Context("", application=selection.application, file_path=selection.file_path, start_line=selection.start_line, end_line=selection.end_line, reference_only=True))
        self.assertEqual(result, "### Code reference — Cursor\n\nFile: C:/repo/src/main.py\nLines: 12–18\n")
        self.assertNotIn("```", result)

    def test_invalid_editor_location_rejected(self):
        base = {"version": 1, "type": "selection", "active": True, "application": "Cursor", "file_path": "C:/repo/file.ts", "start_line": 4, "end_line": 5}
        for change in ({"start_line": 0}, {"end_line": 3}, {"file_path": ""}, {"extra": "x"}, {"start_line": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                selection_from_message(base | change)
        self.assertIsNone(selection_from_message({"version": 1, "type": "selection", "active": False}))


if __name__ == "__main__":
    unittest.main()
