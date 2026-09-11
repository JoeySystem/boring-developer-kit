from __future__ import annotations

import struct
import zlib

import pytest
from PySide6.QtGui import QColor, QImage

from controller_config import screen_icon
from controller_config.screen_icon import ICON_SIZE, icon_preview, load_icon_image, render_icon


def _image(width=128, height=128, color="red"):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _pixel(data, x=64, y=64):
    offset = 2 * (y * ICON_SIZE + x)
    return data[offset : offset + 2]


@pytest.mark.parametrize("color,expected", [("red", b"\x00\xf8"), ("lime", b"\xe0\x07"), ("blue", b"\x1f\x00")])
def test_primary_colors_are_rgb565_little_endian(color, expected):
    data = render_icon(_image(color=color))
    assert len(data) == 32768
    assert _pixel(data) == expected
    assert icon_preview(data).pixelColor(64, 64) == QColor(color)


def test_aperture_matches_pixel_center_condition_without_border():
    data = render_icon(_image(color="white"))
    for y in range(128):
        for x in range(128):
            inside = (2 * x + 1 - 128) ** 2 + (2 * y + 1 - 128) ** 2 <= 125**2
            assert _pixel(data, x, y) == (b"\xff\xff" if inside else b"\0\0")


def test_transparent_pixels_composite_on_black():
    image = _image(color=QColor(255, 0, 0, 0))
    assert _pixel(render_icon(image)) == b"\0\0"
    image.fill(QColor(255, 0, 0, 128))
    assert _pixel(render_icon(image)) == b"\0\x80"


def test_non_square_source_pan_zoom_and_center_clamping():
    image = _image(256, 128)
    for y in range(128):
        for x in range(128, 256):
            image.setPixelColor(x, y, QColor("blue"))
    center = render_icon(image)
    assert _pixel(center, 32) == b"\0\xf8"
    assert _pixel(center, 96) == b"\x1f\0"
    assert _pixel(render_icon(image, center_x=-10, center_y=10)) == b"\0\xf8"
    assert _pixel(render_icon(image, center_x=10, center_y=-10)) == b"\x1f\0"
    assert render_icon(image, zoom=2, center_x=0.25) == render_icon(_image())
    assert render_icon(image, zoom=99, center_x=0.25) == render_icon(image, zoom=4, center_x=0.25)


def test_preview_is_quantized_payload_and_owned():
    original = _image(color=QColor(123, 231, 87))
    data = render_icon(original)
    preview = icon_preview(data)
    assert preview.pixelColor(64, 64) == QColor(123, 231, 82)
    assert render_icon(preview) == data
    assert preview.pixelColor(0, 0) == QColor("black")


def test_load_uses_actual_format_and_survives_source_removal(tmp_path):
    path = tmp_path / "actually-png.jpg"
    assert _image(17, 9).save(str(path), "PNG")
    image = load_icon_image(path)
    path.unlink()
    assert (image.width(), image.height()) == (17, 9)
    assert _pixel(render_icon(image)) == b"\0\xf8"


def test_jpeg_orientation_is_applied(tmp_path, qapp):
    path = tmp_path / "oriented.jpg"
    source = _image(40, 20)
    for y in range(20):
        for x in range(20, 40):
            source.setPixelColor(x, y, QColor("blue"))
    assert source.save(str(path), "JPEG", 100)
    jpeg = path.read_bytes()
    # EXIF TIFF: one Orientation SHORT with value 6 (rotate 90 clockwise).
    tiff = b"II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHIHHI", 0x112, 3, 1, 6, 0, 0)
    exif = b"Exif\0\0" + tiff
    path.write_bytes(jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:])
    image = load_icon_image(path)
    assert (image.width(), image.height()) == (20, 40)
    assert image.pixelColor(10, 5).red() > 240
    assert image.pixelColor(10, 35).blue() > 240


def test_truncated_png_is_rejected(tmp_path):
    path = tmp_path / "truncated.png"
    assert _image().save(str(path), "PNG")
    path.write_bytes(path.read_bytes()[:-12])
    with pytest.raises(ValueError, match="incomplete|damaged"):
        load_icon_image(path)


@pytest.mark.parametrize("contents", [b"not an image", b"\x89PNG\r\n\x1a\n", b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"])
def test_invalid_or_unsupported_files_are_rejected(tmp_path, contents):
    path = tmp_path / "wrong.png"
    path.write_bytes(contents)
    with pytest.raises(ValueError):
        load_icon_image(path)


def test_animated_png_is_rejected_even_if_qt_only_reads_first_frame(tmp_path):
    path = tmp_path / "animated.png"
    assert _image(8, 8).save(str(path), "PNG")
    png = path.read_bytes()
    payload = b"acTL" + struct.pack(">II", 2, 0)
    chunk = struct.pack(">I", 8) + payload + struct.pack(">I", zlib.crc32(payload))
    path.write_bytes(png[:33] + chunk + png[33:])
    with pytest.raises(ValueError, match="Animated"):
        load_icon_image(path)


def test_file_and_decode_pixel_limits(tmp_path, monkeypatch):
    path = tmp_path / "large.png"
    assert _image(8, 8).save(str(path), "PNG")
    monkeypatch.setattr(screen_icon, "MAX_FILE_BYTES", path.stat().st_size - 1)
    with pytest.raises(ValueError, match="10 MiB"):
        load_icon_image(path)
    monkeypatch.setattr(screen_icon, "MAX_FILE_BYTES", path.stat().st_size)
    monkeypatch.setattr(screen_icon, "MAX_IMAGE_PIXELS", 63)
    with pytest.raises(ValueError, match="16 million"):
        load_icon_image(path)


def test_invalid_render_or_preview_data_is_rejected():
    with pytest.raises(ValueError):
        render_icon(QImage())
    with pytest.raises(ValueError):
        render_icon(_image(), center_x=float("nan"))
    with pytest.raises(ValueError):
        icon_preview(b"\0" * 32767)
