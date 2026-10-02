import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest

from PySide6.QtGui import QImage

from prompt_scratchpad.attachments import AttachmentStore, image_markdown


class AttachmentTests(unittest.TestCase):
    def test_clipboard_image_is_saved_and_referenced_as_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AttachmentStore(Path(directory) / "draft.json")
            image = QImage(4, 3, QImage.Format.Format_ARGB32)
            image.fill(0xff88cc44)
            path = store.attach_clipboard(image)
            self.assertTrue(path.is_file())
            self.assertEqual(QImage(str(path)).size(), image.size())
            self.assertIn(path.as_posix(), image_markdown(path, "Screenshot.png"))

    def test_non_image_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "notes.txt"
            source.write_text("not an image", encoding="utf-8")
            with self.assertRaises(ValueError):
                AttachmentStore(Path(directory) / "draft.json").attach_file(source)
