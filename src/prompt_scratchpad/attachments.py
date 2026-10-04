"""Store local image assets referenced by the Markdown prompt."""

from pathlib import Path
import hashlib
import os
import re
import shutil
import uuid

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PREVIEW_SIZE = QSize(1240, 680)  # Twice the editor's 620 x 340 CSS-pixel limit.


def _caption(name: str) -> str:
    result = re.sub(r"[^\w .-]+", " ", Path(name).stem).strip()[:80]
    return result or "Attached image"


def image_markdown(path: Path, name: str) -> str:
    # Angle brackets allow the spaces in Windows' AppData path. Qt and agents
    # on the same host can open this local image file directly.
    return f"![{_caption(name)}](<{path.as_posix()}>)"


class AttachmentPreviews:
    """Bound static preview pixels while keeping original prompt assets intact.

    The disk cache survives renderer suspension and does not retain decoded
    QImages in Python. Animated images keep their original playback behavior.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()

    def path_for(self, source: str | Path) -> Path | None:
        source = Path(source).resolve()
        if not source.is_relative_to(self.directory) or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return None
        try:
            stat = source.stat()
            if stat.st_size > MAX_IMAGE_BYTES:
                return source
            reader = QImageReader(str(source))
            size = reader.size()
            if (not reader.canRead() or not size.isValid() or reader.supportsAnimation()
                    or size.width() * size.height() > MAX_IMAGE_PIXELS
                    or (size.width() <= PREVIEW_SIZE.width() and size.height() <= PREVIEW_SIZE.height())):
                return source
            key = hashlib.sha256(f'{source}:{stat.st_size}:{stat.st_mtime_ns}:1240x680'.encode()).hexdigest()
            destination = self.directory / '.previews' / f'{key}.png'
            if destination.is_file():
                return destination
            reader.setAutoTransform(True)
            reader.setScaledSize(size.scaled(PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                return source
            # EXIF rotation can swap the decoder's requested dimensions.
            if image.width() > PREVIEW_SIZE.width() or image.height() > PREVIEW_SIZE.height():
                image = image.scaled(PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            try:
                if not image.save(str(temporary), 'PNG'):
                    return source
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            return destination
        except OSError:
            # A read-only cache or missing asset must not break the editor.
            return source


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
