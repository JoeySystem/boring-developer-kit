"""Three big language keys for Settings · 通用 (the designer 11-19, design decision; 演示页 v0.5 and v0.7).

Each key shows one big character (文 / A / あ) over the language's own name.
The selected key is cream all over. Switching plays “点阵长满”: on the key
you pressed, cream dots grow outward from where you pressed and join into one
block; on the old key the cream shrinks from the edge inward into dots and
disappears. About 0.48 s, dot spacing 7 (both numbers are the Agent's).

Changing the language rebuilds the whole page, so the animation cannot live in
one widget. The window keeps a `LanguageSwitch` (what changed, where, when)
and hands it to the freshly built keys, which carry on from where it is now.

    keys = LanguageKeys([("zh_CN", "文", "简体中文"), ...], on_choose=window.choose)
    keys.set_active("zh_CN")
    keys.play(switch)          # optional, right after a language change
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.style import tokens

MIN_KEY_HEIGHT = 64
DOT_COVER = 0.72      # dot radius at full size, × pitch: just over half the diagonal, so the dots close up
RAMP_PITCHES = 3      # how many dot rows grow at once along the front


@dataclass(frozen=True)
class LanguageSwitch:
    """One language change: `old` → `new`, pressed at `origin` (in the key), at `started`."""

    old: str
    new: str
    origin: QPointF
    started: float

    def progress(self, now: float | None = None) -> float:
        elapsed = (time.monotonic() if now is None else now) - self.started
        return max(0.0, min(1.0, elapsed * 1000 / tokens.LANGUAGE_FILL_MS))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


class LanguageKey(QPushButton):
    """One key. `property("active")` says whether it is selected; `set_fill` overrides that while animating."""

    def __init__(self, code: str, glyph: str, name: str, parent: QWidget | None = None) -> None:
        super().__init__(name, parent)
        self.code = code
        self.glyph = glyph
        self.setObjectName("settingsLanguageButton")
        self.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.setAccessibleName(name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(MIN_KEY_HEIGHT)
        self.setMaximumHeight(tokens.LANGUAGE_KEY_HEIGHT)
        self.press_position: QPointF | None = None
        self._fill: tuple[float, QPointF] | None = None

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(96, tokens.LANGUAGE_KEY_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(48, MIN_KEY_HEIGHT)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.press_position = event.position()
        super().mousePressEvent(event)

    def set_fill(self, level: float | None, origin: QPointF | None = None) -> None:
        """Cream coverage 0..1 growing from `origin`; None goes back to following `active`."""
        self._fill = None if level is None else (level, origin or QRectF(self.rect()).center())
        self.update()

    def fill_level(self) -> float:
        if self._fill is not None:
            return self._fill[0]
        return 1.0 if self.property("active") is True else 0.0

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        shape = QPainterPath()
        shape.addRoundedRect(rect, tokens.LANGUAGE_KEY_RADIUS, tokens.LANGUAGE_KEY_RADIUS)
        painter.fillPath(shape, QColor(tokens.LANGUAGE_KEY))
        level = self.fill_level()
        cream = QColor(tokens.LANGUAGE_KEY_SELECTED)
        if level >= 1.0:
            painter.fillPath(shape, cream)
        elif level > 0.0:
            origin = self._fill[1] if self._fill is not None else rect.center()
            painter.save()
            painter.setClipPath(shape)
            self._paint_dots(painter, rect, origin, level, cream)
            painter.restore()
        self._paint_text(painter, rect, level)

    @staticmethod
    def _paint_dots(painter: QPainter, rect: QRectF, origin: QPointF, level: float, cream: QColor) -> None:
        pitch = tokens.LANGUAGE_FILL_PITCH
        ramp = pitch * RAMP_PITCHES
        reach = max(math.hypot(x - origin.x(), y - origin.y())
                    for x in (rect.left(), rect.right()) for y in (rect.top(), rect.bottom()))
        front = level * (reach + ramp)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(cream)
        # the grid goes through the origin, so the first dot sits exactly where you pressed
        first_x = origin.x() - math.ceil((origin.x() - rect.left()) / pitch) * pitch
        first_y = origin.y() - math.ceil((origin.y() - rect.top()) / pitch) * pitch
        y = first_y
        while y <= rect.bottom() + pitch:
            x = first_x
            while x <= rect.right() + pitch:
                grown = (front - math.hypot(x - origin.x(), y - origin.y())) / ramp
                if grown > 0:
                    radius = min(1.0, grown) * pitch * DOT_COVER
                    painter.drawEllipse(QPointF(x, y), radius, radius)
                x += pitch
            y += pitch

    def _paint_text(self, painter: QPainter, rect: QRectF, level: float) -> None:
        colour = _mix(QColor(tokens.LANGUAGE_KEY_TEXT), QColor(tokens.LANGUAGE_KEY_TEXT_SELECTED), level)
        painter.setPen(colour)
        glyph_font = QFont(self.font())
        glyph_font.setPixelSize(tokens.FONT_LANGUAGE_GLYPH)
        glyph_font.setWeight(QFont.Weight.Medium if level >= 0.5 else QFont.Weight.Normal)
        name_font = QFont(self.font())
        name_font.setPixelSize(tokens.FONT_BODY)
        name_font.setWeight(QFont.Weight.DemiBold if level >= 0.5 else QFont.Weight.Normal)
        glyph_height = tokens.FONT_LANGUAGE_GLYPH + 6
        name_height = tokens.FONT_BODY + 6
        top = rect.center().y() - (glyph_height + name_height) / 2
        painter.setFont(glyph_font)
        painter.drawText(QRectF(rect.left(), top, rect.width(), glyph_height),
                         Qt.AlignmentFlag.AlignCenter, self.glyph)
        painter.setFont(name_font)
        painter.drawText(QRectF(rect.left() + 4, top + glyph_height, rect.width() - 8, name_height),
                         Qt.AlignmentFlag.AlignCenter, self.text())


class LanguageKeys(QWidget):
    """The row of keys; `on_choose(code, origin)` is called on a press of a key that is not selected."""

    def __init__(self, options: Sequence[tuple[str, str, str]],
                 on_choose: Callable[[str, QPointF], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsLanguageKeys")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMaximumHeight(tokens.LANGUAGE_KEY_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(tokens.LANGUAGE_KEY_GAP)
        self.keys: dict[str, LanguageKey] = {}
        for code, glyph, name in options:
            key = LanguageKey(code, glyph, name, self)
            key.clicked.connect(lambda _checked=False, k=key: on_choose(
                k.code, k.press_position or QRectF(k.rect()).center()))
            self.keys[code] = key
            row.addWidget(key, 1)
        self._switch: LanguageSwitch | None = None
        self._animation = QVariantAnimation(self)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self._settle)
        self._easing = QEasingCurve(QEasingCurve.Type.OutCubic)

    def set_active(self, code: str) -> None:
        for key_code, key in self.keys.items():
            key.setProperty("active", key_code == code)
            key.update()

    def play(self, switch: LanguageSwitch) -> None:
        """Carry on `switch` from where it is now; does nothing once it is over."""
        progress = switch.progress()
        if progress >= 1.0 or switch.new not in self.keys:
            return
        self._switch = switch
        self._animation.stop()
        self._animation.setStartValue(progress)
        self._animation.setDuration(max(1, round((1.0 - progress) * tokens.LANGUAGE_FILL_MS)))
        self._step(progress)
        self._animation.start()

    def _step(self, value) -> None:
        switch = self._switch
        if switch is None:
            return
        eased = self._easing.valueForProgress(float(value))
        self.keys[switch.new].set_fill(eased, switch.origin)
        if switch.old in self.keys and switch.old != switch.new:
            self.keys[switch.old].set_fill(1.0 - eased)

    def _settle(self) -> None:
        self._switch = None
        for key in self.keys.values():
            key.set_fill(None)
