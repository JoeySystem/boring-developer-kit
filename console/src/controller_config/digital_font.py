"""Community heading renderer using installed system fonts, without brand glyphs."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFontDatabase, QFontMetricsF, QImage, QPainter


@dataclass(frozen=True)
class TextLayout:
    text: str
    width: float
    height: float
    # Dot centres and radius for the Mode page's dot morph, sampled from the
    # system-font rendering (the official build uses its own dot-matrix font).
    dots: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class _CommunityProfile:
    character_gap: float = 0.0


@dataclass(frozen=True)
class FontPack:
    """What callers read from the official dot-matrix font pack, for the system font."""
    profile: _CommunityProfile = _CommunityProfile()
    character_order: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def load_font_pack() -> FontPack:
    return FontPack()


def normalize_text(text: object) -> str:
    value = str(text).upper()
    if not supports_text(value):
        raise ValueError("标题不支持字符")
    return value


def supports_text(text: object) -> bool:
    return all(character.isascii() and character.isprintable() for character in str(text))


def _font_metrics():
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(14)
    return font, QFontMetricsF(font)


DOT_ROWS = 7  # grid rows the system-font text is sampled on for dots


def layout_text(text: object, *, scale: float = 1.0) -> TextLayout:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("文字比例必须是大于 0 的有限数")
    return _layout(normalize_text(text), float(scale))


@lru_cache(maxsize=256)
def _layout(value: str, scale: float) -> TextLayout:
    _, metrics = _font_metrics()
    bounds = metrics.boundingRect(value)
    width = max(metrics.horizontalAdvance(value), bounds.right()) - min(0, bounds.left())
    # Keep the heading height used by existing device and card layouts.
    width, height = width * 14 / metrics.height() * scale, 14 * scale
    return TextLayout(value, width, height, _sample_dots(value, width, height))


def _sample_dots(value: str, width: float, height: float):
    pitch = height / DOT_ROWS
    columns = max(1, math.ceil(width / pitch))
    image = QImage(columns, DOT_ROWS, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font, metrics = _font_metrics()
    factor = DOT_ROWS / metrics.height()
    painter.scale(factor, factor)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    left = min(0, metrics.boundingRect(value).left())
    painter.drawText(QPointF(-left, metrics.ascent()), value)
    painter.end()
    radius = pitch * 0.4
    return tuple(
        (column * pitch + pitch / 2, row * pitch + pitch / 2, radius)
        for row in range(DOT_ROWS) for column in range(columns)
        if QColor(image.pixel(column, row)).alpha() >= 96
    )


def draw_qpainter(painter: QPainter, text: object, x: float, y: float,
                  *, scale: float = 1.0, color="#000000") -> TextLayout:
    layout = layout_text(text, scale=scale)
    font, metrics = _font_metrics()
    factor = 14 / metrics.height() * scale
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.translate(x, y)
    painter.scale(factor, factor)
    painter.setFont(font)
    painter.setPen(QColor(color))
    left = min(0, metrics.boundingRect(layout.text).left())
    painter.drawText(QPointF(-left, metrics.ascent()), layout.text)
    painter.restore()
    return layout
