import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
import hashlib
import struct

from PySide6.QtGui import QImage

from prompt_scratchpad.attachments import AttachmentPreviews, AttachmentStore, image_markdown


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


class PreviewTests(unittest.TestCase):
    def make_image(self, path, width=2400, height=1600, color=0xff88cc44):
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(color)
        self.assertTrue(image.save(str(path)))

    def test_preview_bounds_preserve_original_bytes_and_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'screenshot 😀.png'
            self.make_image(source)
            original = hashlib.sha256(source.read_bytes()).hexdigest()
            markdown = image_markdown(source, source.name)
            preview = AttachmentPreviews(directory).path_for(source)
            self.assertNotEqual(preview, source)
            self.assertTrue(preview.is_relative_to(Path(directory).resolve()))
            resized = QImage(str(preview))
            self.assertLessEqual(resized.width(), 1240)
            self.assertLessEqual(resized.height(), 680)
            self.assertAlmostEqual(resized.width() / resized.height(), 1.5, delta=0.002)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
            self.assertEqual(image_markdown(source, source.name), markdown)

    def test_reuses_disk_preview_and_invalidates_when_original_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'image.png'
            self.make_image(source)
            previews = AttachmentPreviews(directory)
            first = previews.path_for(source)
            timestamp = first.stat().st_mtime_ns
            self.assertEqual(previews.path_for(source), first)
            self.assertEqual(first.stat().st_mtime_ns, timestamp)
            self.make_image(source, color=0xff112244)
            os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1_000_000_000))
            self.assertNotEqual(previews.path_for(source), first)

    def test_small_images_use_original_without_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'small.png'
            self.make_image(source, 10, 6)
            self.assertEqual(AttachmentPreviews(directory).path_for(source), source.resolve())
            self.assertFalse((Path(directory) / '.previews').exists())

    def test_paths_outside_attachment_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'attachments').mkdir()
            source = root / 'private.png'
            self.make_image(source, 10, 6)
            self.assertIsNone(AttachmentPreviews(root / 'attachments').path_for(source))
            self.assertIsNone(AttachmentPreviews(root / 'attachments').path_for('https://example.com/image.png'))

    def test_cache_failure_falls_back_to_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'image.png'
            self.make_image(source)
            (Path(directory) / '.previews').write_text('blocked', encoding='utf-8')
            self.assertEqual(AttachmentPreviews(directory).path_for(source), source.resolve())

    def test_gif_keeps_original_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'animated.gif'
            # Large logical canvas with a single tiny frame. The GIF decoder
            # supports animation, so even this fixture must bypass resizing.
            source.write_bytes(b'GIF89a' + struct.pack('<HH', 4000, 3000)
                               + bytes.fromhex('800000000000ffffff2c00000000010001000002024401003b'))
            self.assertEqual(AttachmentPreviews(directory).path_for(source), source.resolve())
            self.assertFalse((Path(directory) / '.previews').exists())
