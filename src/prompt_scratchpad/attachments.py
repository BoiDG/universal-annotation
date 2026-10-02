"""Store local image assets referenced by the Markdown prompt."""

from pathlib import Path
import os
import re
import shutil
import uuid

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _caption(name: str) -> str:
    result = re.sub(r"[^\w .-]+", " ", Path(name).stem).strip()[:80]
    return result or "Attached image"


def image_markdown(path: Path, name: str) -> str:
    # Angle brackets allow the spaces in Windows' AppData path. Qt and agents
    # on the same host can open this local image file directly.
    return f"![{_caption(name)}](<{path.as_posix()}>)"


class AttachmentStore:
    def __init__(self, draft_path: Path):
        self.directory = draft_path.parent / "attachments"

    def _destination(self, suffix: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory / f"{uuid.uuid4().hex}{suffix}"

    def attach_file(self, source: Path) -> Path:
        source = Path(source)
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError("Choose a PNG, JPEG, GIF, WebP, or BMP image.")
        if not source.is_file() or source.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Choose an image file smaller than 20 MB.")
        reader = QImageReader(str(source))
        size = reader.size()
        if not reader.canRead() or not size.isValid() or size.width() * size.height() > MAX_IMAGE_PIXELS:
            raise ValueError("The image could not be read or exceeds 50 megapixels.")
        destination = self._destination(suffix)
        temporary = destination.with_suffix(suffix + ".tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def attach_clipboard(self, image: QImage) -> Path:
        if image.isNull() or image.width() * image.height() > MAX_IMAGE_PIXELS:
            raise ValueError("Clipboard has no usable image or it exceeds 50 megapixels.")
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
            raise ValueError("Could not encode the clipboard image as PNG.")
        buffer.close()
        if data.size() > MAX_IMAGE_BYTES:
            raise ValueError("Clipboard image exceeds 20 MB as PNG.")
        destination = self._destination(".png")
        temporary = destination.with_suffix(".png.tmp")
        try:
            temporary.write_bytes(bytes(data))
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination
