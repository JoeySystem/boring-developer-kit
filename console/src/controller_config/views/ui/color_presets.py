"""One row of key light colours (灯光页 排法二 v0.4, the designer 2026-11-18 final).

Spec: Console视觉稿/灯光页-排法二-v0.4-20261118-图/3-点键改颜色-一排预设色.png.

    键 3  ● ● ● ● ● ● ● ● ● ● ●  ⊘

- Only the 11 presets (style/tokens.py LIGHT_PRESETS) plus "不亮" (the slashed
  dot): no free colour picker any more.
- A colour saved earlier that is not a preset stays as it was: it shows as an
  extra dot in front, set apart by a thin line. Picking anything else drops
  it, and there is no way back to it.
- A pick applies at once (`color_chosen` fires); there is no "cancel".
- The chosen dot has a cream ring with a small gap around it.

Keyboard: Tab focuses the row, Left / Right / Home / End move the choice
(each move is a pick, like a radio group).

    row = ColorPresetRow()
    row.set_key("键 3", (91, 75, 255))
    row.color_chosen.connect(lambda rgb: ...)   # (r, g, b), or None for 不亮
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolTip, QWidget

from controller_config.i18n import translate_ui_text
from controller_config.style import tokens

RGB = tuple[int, int, int]

# Measured on the draft (1-整页-1280x800.png at 2x, 3-点键改颜色 for the extra colour).
DOT = 20.0                # dot diameter
GAP = 7.0                 # between dots
MIN_GAP = 3.0             # what the gap may shrink to when the row is short of room (an old colour adds a dot)
RING_GAP = 2.0            # between a dot and its ring
RING_WIDTH = 2.0
DIVIDER_SPACE = 10.0      # each side of the line after an old custom colour
LABEL_SPACE = 10          # label to the first dot
SLASH_WIDTH = 2.5         # "不亮"
SLASH_REACH = 0.72        # slash ends, of the radius
FOCUS_GAP = 2.0           # keyboard focus ring outside the cream ring
SIDE = DOT / 2 + RING_GAP + RING_WIDTH + FOCUS_GAP + 1   # dot centre to strip edge
HEIGHT = math.ceil(2 * SIDE)

OFF_NAME = "不亮"
CUSTOM_NAME = "原来的颜色"


def preset_colors() -> list[tuple[str, RGB]]:
    """The 11 presets in order, as (name, (r, g, b))."""
    return [(name, tuple(QColor(value).getRgb()[:3])) for name, value in tokens.LIGHT_PRESETS]


def _clean(rgb) -> RGB | None:
    """Tuple of ints; black means off (that is how "不亮" used to be stored)."""
    if rgb is None:
        return None
    value = tuple(max(0, min(255, int(channel))) for channel in rgb)
    return None if not any(value) else value


class _Swatches(QWidget):
    """The painted dots; ColorPresetRow owns the state."""

    def __init__(self, row: ColorPresetRow) -> None:
        super().__init__(row)
        self._row = row
        self._keyboard_focus = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def _length(self, gap: float) -> float:
        """Strip width with this gap between preset dots."""
        x = SIDE
        for kind, _rgb, _name in self._row.entries()[:-1]:
            x += DOT + (2 * DIVIDER_SPACE + 1 if kind == "custom" else gap)
        return x + SIDE

    def _gap(self) -> float:
        """GAP, or less (down to MIN_GAP) when the strip got less width than it asked for."""
        gaps = sum(1 for kind, _rgb, _name in self._row.entries()[:-1] if kind != "custom")
        if not gaps or self.width() >= self._length(GAP):
            return GAP
        return max(MIN_GAP, GAP - (self._length(GAP) - self.width()) / gaps)

    # Each entry: ("custom" | "preset" | "off", rgb | None, name).
    def centers(self) -> list[float]:
        x, result, gap = SIDE, [], self._gap()
        for kind, _rgb, _name in self._row.entries():
            result.append(x)
            x += DOT + (2 * DIVIDER_SPACE + 1 if kind == "custom" else gap)
        return result

    def divider_x(self) -> float | None:
        if self._row.entries()[0][0] != "custom":
            return None
        return self.centers()[0] + DOT / 2 + DIVIDER_SPACE + 0.5

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(math.ceil(self._length(GAP)), HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(math.ceil(self._length(MIN_GAP)), HEIGHT)

    def index_at(self, position: QPointF) -> int | None:
        middle = self.height() / 2
        for index, center in enumerate(self.centers()):
            if math.hypot(position.x() - center, position.y() - middle) <= DOT / 2 + RING_GAP:
                return index
        return None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = self.height() / 2
        radius = DOT / 2
        chosen = self._row.chosen_index()
        divider = self.divider_x()
        if divider is not None:
            painter.setPen(QPen(QColor(tokens.LIGHT_SWATCH_DIVIDER), 1))
            painter.drawLine(QPointF(divider, middle - radius), QPointF(divider, middle + radius))
        for index, ((kind, rgb, _name), center) in enumerate(zip(self._row.entries(), self.centers())):
            dot = QRectF(center - radius, middle - radius, DOT, DOT)
            painter.setPen(Qt.PenStyle.NoPen)
            if kind == "off":
                painter.setBrush(QColor(tokens.LIGHT_SWATCH_OFF))
                painter.drawEllipse(dot)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(tokens.LIGHT_SWATCH_OFF_EDGE), 1))
                painter.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
                slash = QPen(QColor(tokens.LIGHT_SWATCH_OFF_SLASH), SLASH_WIDTH)
                slash.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(slash)
                reach = radius * SLASH_REACH / math.sqrt(2)
                painter.drawLine(QPointF(center - reach, middle + reach), QPointF(center + reach, middle - reach))
            else:
                painter.setBrush(QColor(*rgb))
                painter.drawEllipse(dot)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(*tokens.LIGHT_SWATCH_RIM), 1))
                painter.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
            if index == chosen:
                ring = radius + RING_GAP + RING_WIDTH / 2
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(tokens.LIGHT_SWATCH_RING), RING_WIDTH))
                painter.drawEllipse(QPointF(center, middle), ring, ring)
            if index == chosen and self._keyboard_focus and self.hasFocus():
                focus = radius + RING_GAP + RING_WIDTH + FOCUS_GAP - 0.5
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1))
                painter.drawEllipse(QPointF(center, middle), focus, focus)
        painter.end()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            index = self.index_at(QPointF(event.pos()))
            if index is None:
                QToolTip.hideText()
                event.ignore()
            else:
                QToolTip.showText(event.globalPos(), translate_ui_text(self._row.entries()[index][2]), self)
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.index_at(event.position()) is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        index = self.index_at(event.position()) if event.button() == Qt.MouseButton.LeftButton else None
        if index is None:
            super().mousePressEvent(event)
            return
        self._keyboard_focus = False
        self._row.choose(index)
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
        }
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        count = len(self._row.entries())
        chosen = self._row.chosen_index()
        target = {
            Qt.Key.Key_Left: max(0, chosen - 1), Qt.Key.Key_Right: min(count - 1, chosen + 1),
            Qt.Key.Key_Home: 0, Qt.Key.Key_End: count - 1,
        }.get(event.key())
        if target is None:
            super().keyPressEvent(event)
            return
        self._keyboard_focus = True
        self._row.choose(target)
        event.accept()


class ColorPresetRow(QWidget):
    """The "键 N" label plus the colour dots; see the module docstring."""

    color_chosen = Signal(object)  # (r, g, b) or None for 不亮

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("colorPresetRow", True)
        self._presets = preset_colors()
        self._custom: RGB | None = None
        self._current: RGB | None = None
        self._label = QLabel(objectName="colorPresetLabel")
        self._swatches = _Swatches(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(LABEL_SPACE)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._swatches)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.set_key("", None)

    # -- public API ----------------------------------------------------------------

    def set_key(self, label: str, rgb: RGB | None) -> None:
        """Show the colour row for one key. `label` is shown as is ("键 3"); no signal.

        A colour that is not one of the presets gets its own dot in front.
        """
        self._label.setText(label)
        self._label.setVisible(bool(label))
        self._current = _clean(rgb)
        preset_values = [value for _name, value in self._presets]
        self._custom = self._current if self._current is not None and self._current not in preset_values else None
        self._refresh()

    def current(self) -> RGB | None:
        return self._current

    def has_custom_color(self) -> bool:
        """True while an old colour outside the presets is still shown."""
        return self._custom is not None

    def label_text(self) -> str:
        return self._label.text()

    # -- used by the dots ----------------------------------------------------------

    def entries(self) -> list[tuple[str, RGB | None, str]]:
        entries = [("custom", self._custom, CUSTOM_NAME)] if self._custom is not None else []
        entries += [("preset", value, name) for name, value in self._presets]
        entries.append(("off", None, OFF_NAME))
        return entries

    def chosen_index(self) -> int:
        for index, (kind, value, _name) in enumerate(self.entries()):
            if (kind == "off" and self._current is None) or (kind != "off" and value == self._current):
                return index
        return len(self.entries()) - 1

    def choose(self, index: int) -> None:
        kind, value, _name = self.entries()[index]
        if index == self.chosen_index():
            return
        self._current = value
        if kind != "custom":
            self._custom = None  # replaced: there is no way back to it
        self._refresh()
        self.color_chosen.emit(value)

    def _refresh(self) -> None:
        # Ask for the full spacing, take less when the tile is short of room.
        self._swatches.setFixedHeight(HEIGHT)
        self._swatches.updateGeometry()
        self._swatches.update()
        name = self.entries()[self.chosen_index()][2]
        self._swatches.setAccessibleName(translate_ui_text(f"{self._label.text()} 灯光颜色".strip()))
        self._swatches.setAccessibleDescription(translate_ui_text(name))
        self._swatches.update()
