"""Import and render the MIST's 128-pixel custom home icon."""

from __future__ import annotations

import math
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QImageReader, QPainter


ICON_SIZE = 128
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
_ICON_BYTES = ICON_SIZE * ICON_SIZE * 2


class IconImageError(ValueError):
    """A material check failure with a stable, translatable reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _reject_animated_png(data: bytes) -> None:
    # Qt's PNG plugin can report a static image for an APNG. Read chunk headers
    # so we reject animation even when the installed plugin ignores its frames.
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        chunk_type = data[offset + 4 : offset + 8]
        if offset + length + 12 > len(data):
            raise IconImageError("damaged", "The PNG file is incomplete or damaged.")
        if chunk_type == b"acTL":
            raise IconImageError("animated", "Animated images are not supported. Choose a static PNG or JPEG.")
        offset += length + 12
        if chunk_type == b"IEND":
            return
    raise IconImageError("damaged", "The PNG file is incomplete or damaged.")


def load_icon_image(path: str | Path) -> QImage:
    """Decode a static PNG/JPEG, applying orientation and owning its pixel data.

    Raises ValueError for unsupported, damaged, animated or oversized images;
    file access errors retain their ordinary OSError type.
    """
    with Path(path).open("rb") as source:
        data = source.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise IconImageError("file_size", "Choose an image no larger than 10 MiB.")

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    image_format = bytes(reader.format()).lower()
    if image_format not in (b"png", b"jpeg", b"jpg"):
        raise IconImageError("format", "Choose a static PNG or JPEG image.")
    if image_format == b"png":
        _reject_animated_png(data)
    if reader.supportsAnimation() or reader.imageCount() > 1:
        raise IconImageError("animated", "Animated images are not supported. Choose a static PNG or JPEG.")
    size = reader.size()
    if not size.isValid() or size.isEmpty():
        raise IconImageError("damaged", "The image is incomplete or damaged.")
    if size.width() * size.height() > MAX_IMAGE_PIXELS:
        raise IconImageError("pixels", "Choose an image with no more than 16 million pixels.")
    image = reader.read()
    if image.isNull():
        raise IconImageError("damaged", "The image could not be decoded: " + reader.errorString())
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    if not any(image.constBits()[3::4]):
        raise IconImageError("transparent", "The image is fully transparent. Choose an image with visible content.")
    return image.convertToFormat(QImage.Format.Format_ARGB32).copy()


def render_icon(
    image: QImage, *, zoom: float = 1.0, center_x: float = 0.5, center_y: float = 0.5
) -> bytes:
    """Return the circular, black-composited crop as RGB565 little-endian.

    Zoom is constrained to 1–4. Centers are normalized source coordinates and
    clamped to keep the square crop entirely inside the source image.
    """
    if image.isNull():
        raise ValueError("Choose an image before rendering an icon.")
    if not all(math.isfinite(value) for value in (zoom, center_x, center_y)):
        raise ValueError("Crop coordinates and zoom must be finite numbers.")
    zoom = max(1.0, min(4.0, zoom))
    side = min(image.width(), image.height()) / zoom
    left = max(0.0, min(image.width() - side, center_x * image.width() - side / 2))
    top = max(0.0, min(image.height() - side, center_y * image.height() - side / 2))
    rendered = QImage(ICON_SIZE, ICON_SIZE, QImage.Format.Format_RGB32)
    rendered.fill(0xFF000000)
    painter = QPainter(rendered)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(0, 0, ICON_SIZE, ICON_SIZE), image, QRectF(left, top, side, side))
    painter.end()

    output = bytearray(_ICON_BYTES)
    for y in range(ICON_SIZE):
        for x in range(ICON_SIZE):
            if (2 * x + 1 - ICON_SIZE) ** 2 + (2 * y + 1 - ICON_SIZE) ** 2 > 125**2:
                continue
            color = rendered.pixel(x, y)
            value = ((color >> 8) & 0xF800) | ((color >> 5) & 0x07E0) | ((color >> 3) & 0x001F)
            struct.pack_into("<H", output, 2 * (y * ICON_SIZE + x), value)
    return bytes(output)


def icon_preview(data: bytes) -> QImage:
    """Decode the exact transmitted RGB565 pixels into an owned opaque QImage."""
    if len(data) != _ICON_BYTES:
        raise ValueError("A screen icon must contain exactly 32768 bytes.")
    image = QImage(ICON_SIZE, ICON_SIZE, QImage.Format.Format_RGB32)
    for index, (value,) in enumerate(struct.iter_unpack("<H", data)):
        red = (value >> 11) & 31
        green = (value >> 5) & 63
        blue = value & 31
        red = (red << 3) | (red >> 2)
        green = (green << 2) | (green >> 4)
        blue = (blue << 3) | (blue >> 2)
        image.setPixel(index % ICON_SIZE, index // ICON_SIZE, 0xFF000000 | (red << 16) | (green << 8) | blue)
    return image
