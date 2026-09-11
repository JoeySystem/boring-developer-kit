"""Community heading renderer using installed system fonts, without brand glyphs."""
from __future__ import annotations

import math
from dataclasses import dataclass
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFontDatabase, QFontMetricsF, QPainter


@dataclass(frozen=True)
class TextLayout:
    text: str
    width: float
    height: float


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


def layout_text(text: object, *, scale: float = 1.0) -> TextLayout:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("文字比例必须是大于 0 的有限数")
    value = normalize_text(text)
    _, metrics = _font_metrics()
    bounds = metrics.boundingRect(value)
    width = max(metrics.horizontalAdvance(value), bounds.right()) - min(0, bounds.left())
    # Keep the heading height used by existing device and card layouts.
    return TextLayout(value, width * 14 / metrics.height() * scale, 14 * scale)


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
