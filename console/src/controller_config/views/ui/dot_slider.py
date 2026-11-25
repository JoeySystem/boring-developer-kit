"""The dot matrix slider every Console slider uses (the designer, 2026-11-18 final).

Three rows of dots; dots up to the value are lit cream, the rest dim grey.
Hovering raises a Dock-like bulge under the pointer: the column under it
grows to 1.7x and its three dots spread apart, neighbours rise a little less.
While dragging, the bulge rides on the lit/dim boundary; it settles back when
the pointer leaves. Columns never move sideways, so a position always maps to
the same value. The value keeps its full precision; the dots are for the eye.

It is a QSlider: value(), setRange(), valueChanged, keyboard steps and
accessibility all behave as before, so swapping it in is a one-line change.
Spec: Console视觉稿/灯光页-排法二-v0.4-20261118-图/4-点阵拖动条-三排点和鼓起来.png.

snap=True is for a slider whose range is a few stops (lighting: 0..4 for
off, low, medium, high, max). It still looks like one continuous bar: while
dragging, the lit part follows the pointer freely and the value is the
nearest stop; on release the lit part slides onto that stop. Clicking a spot
also lands on the nearest stop, arrow keys move one stop. A saved value that
sits between stops can be shown with set_between(fraction) until the next
touch. Spec: 灯光页-排法二-v0.4-20261118-图/0-灯光亮度-一整段每25%吸过去.png.

With the system's "reduce motion" on, the bulge and the snap jump straight to
where they end; nothing animates (Console动效盘点 11-19, item 7).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QSlider, QWidget

from controller_config.accessibility import system_reduces_motion
from controller_config.style import tokens

ROWS = 3
DOT = 4.0                # dot diameter at rest
GAP = 3.0                # gap between dots at rest
PITCH = DOT + GAP
MAX_SCALE = 1.7          # column under the pointer; larger and dots touch
SPREAD = 3.0             # columns on each side that rise with it
MIN_COLUMNS = 2          # as narrow as the old QSlider could get; only old, tight cards need it
# Durations are the motion tiers (design decision, -12 答复第 2 件 "照四档"): see tokens.DOT_SLIDER_*.
# Room for the biggest column, so a bulge never pushes neighbouring widgets:
# sideways only the dot grows; vertically its three dots also spread apart.
SIDE_ROOM = DOT * (MAX_SCALE - 1) / 2
BULGE_ROOM = (ROWS - 1) * PITCH * (MAX_SCALE - 1) / 2 + SIDE_ROOM


class DotMatrixSlider(QSlider):
    def __init__(self, orientation=Qt.Orientation.Horizontal, parent: QWidget | None = None,
                 *, columns: int = 36, snap: bool = False, reduce_motion: bool | None = None,
                 **kwargs) -> None:
        super().__init__(orientation, parent, **kwargs)
        self._reduce_motion = system_reduces_motion() if reduce_motion is None else reduce_motion
        self._columns = max(2, int(columns))
        self._snap = snap
        self._free = None            # snap: where the lit part is drawn, 0..1, while it is not on a stop
        self._between = None         # snap: a saved value between stops, until the next touch
        self._snap_animation = QVariantAnimation(self)
        self._snap_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._snap_animation.valueChanged.connect(self._set_free)
        self._snap_animation.finished.connect(self._land)
        # Any new value, from the user or from code, is on a stop again.
        self.valueChanged.connect(lambda _value: self.set_between(None))
        self._bulge = 0.0            # 0 flat .. 1 fully raised
        self._bulge_x = 0.0          # where the bulge is centred, widget x
        self._hovering = False
        self._keyboard_focus = False  # draw the focus ring for Tab focus only
        self._animation = QVariantAnimation(self)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_bulge)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Takes the columns it asked for when there is room, fewer (down to
        # MIN_COLUMNS) when a layout is tighter; never wider than asked.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(self.sizeHint().width())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- geometry ------------------------------------------------------------

    def columns(self) -> int:
        """Columns asked for: 18 on a small tile, 36 when expanded or in a dialog."""
        return self._columns

    def visible_columns(self) -> int:
        """Columns actually drawn at the current width."""
        room = (self.width() - 2 * self._margin() + GAP) / PITCH
        return max(MIN_COLUMNS, min(self._columns, int(room)))

    def setColumns(self, columns: int) -> None:  # noqa: N802 - Qt naming
        self._columns = max(2, int(columns))
        self.setMaximumWidth(self.sizeHint().width())
        self.updateGeometry()
        self.update()

    def _margin(self) -> float:
        """Left and right padding."""
        return math.ceil(SIDE_ROOM) + 1

    def _vertical_margin(self) -> float:
        return math.ceil(BULGE_ROOM) + 1

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        width = self._columns * PITCH - GAP + 2 * self._margin()
        height = ROWS * PITCH - GAP + 2 * self._vertical_margin()
        return QSize(math.ceil(width), math.ceil(height))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        width = MIN_COLUMNS * PITCH - GAP + 2 * self._margin()
        return QSize(math.ceil(width), self.sizeHint().height())



    def _column_x(self, column: int) -> float:
        return self._margin() + DOT / 2 + column * PITCH

    def _fraction(self) -> float:
        span = self.maximum() - self.minimum()
        return 0.0 if span <= 0 else (self.sliderPosition() - self.minimum()) / span

    def shown_fraction(self) -> float:
        """How far the lit part reaches, 0..1; off a stop only for a snap slider."""
        for fraction in (self._free, self._between):
            if fraction is not None:
                return fraction
        return self._fraction()

    def lit_columns(self) -> int:
        """How many columns are lit for the current position."""
        return round(self.shown_fraction() * self.visible_columns())

    def snaps(self) -> bool:
        return self._snap

    def set_between(self, fraction: float | None) -> None:
        """Snap slider: light up to `fraction` (a saved value between stops)
        without changing value(); the next press, key or value change clears it."""
        self._between = None if fraction is None else min(1.0, max(0.0, float(fraction)))
        self.update()

    def _set_free(self, value) -> None:
        self._free = float(value)
        self.update()

    def _land(self) -> None:
        self._free = None
        self.update()

    def _boundary_x(self) -> float:
        lit = self.lit_columns()
        if lit <= 0:
            return self._column_x(0)
        return self._column_x(lit - 1)

    def _fraction_at(self, x: float) -> float:
        left = self._column_x(0) - PITCH / 2
        right = self._column_x(self.visible_columns() - 1) + PITCH / 2
        return min(1.0, max(0.0, (x - left) / (right - left)))

    def _value_at(self, x: float) -> int:
        return self.minimum() + round(self._fraction_at(x) * (self.maximum() - self.minimum()))

    # -- bulge ---------------------------------------------------------------

    def bulge(self) -> float:
        return self._bulge

    def _set_bulge(self, value) -> None:
        self._bulge = float(value)
        self.update()

    def _animate_bulge(self, target: float) -> None:
        self._animation.stop()
        if self._reduce_motion:
            self._set_bulge(target)
            return
        self._animation.setDuration(tokens.DOT_SLIDER_RISE_MS if target > self._bulge
                                    else tokens.DOT_SLIDER_SETTLE_MS)
        self._animation.setStartValue(self._bulge)
        self._animation.setEndValue(target)
        self._animation.start()

    def _column_scale(self, column: int) -> float:
        if self._bulge <= 0:
            return 1.0
        distance = abs(self._column_x(column) - self._bulge_x) / PITCH
        if distance >= SPREAD + 1:
            return 1.0
        weight = (math.cos(math.pi * distance / (SPREAD + 1)) + 1) / 2
        return 1 + (MAX_SCALE - 1) * weight * self._bulge

    # -- events ---------------------------------------------------------------

    def event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.HoverEnter and self.isEnabled():
            self._hovering = True
            self._bulge_x = event.position().x()
            self._animate_bulge(1.0)
        elif kind == QEvent.Type.HoverMove and self.isEnabled() and not self.isSliderDown():
            self._bulge_x = event.position().x()
            self.update()
        elif kind == QEvent.Type.HoverLeave:
            self._hovering = False
            if not self.isSliderDown():
                self._animate_bulge(0.0)
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            return super().mousePressEvent(event)
        self._keyboard_focus = False
        self._snap_animation.stop()
        self._between = None
        self.setSliderDown(True)
        self._move_to(event.position().x())
        if self._bulge < 1.0:
            self._animate_bulge(1.0)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.isSliderDown():
            self._move_to(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._move_to(event.position().x())
            self.setSliderDown(False)
            if self._snap:
                self._slide_onto_stop()
            self._bulge_x = event.position().x()
            if not self._hovering or not self.rect().contains(event.position().toPoint()):
                self._animate_bulge(0.0)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _move_to(self, x: float) -> None:
        if self._snap:
            self._free = self._fraction_at(x)
        self.setSliderPosition(self._value_at(x))
        # While dragging, the bulge sits on the lit/dim boundary.
        self._bulge_x = self._boundary_x()
        self.update()

    def _slide_onto_stop(self) -> None:
        start, end = self.shown_fraction(), self._fraction()
        if abs(start - end) < 1e-6 or self._reduce_motion:
            self._snap_animation.stop()
            self._land()
            return
        self._snap_animation.stop()
        self._snap_animation.setDuration(tokens.DOT_SLIDER_SNAP_MS)  # read each time, not once
        self._snap_animation.setStartValue(start)
        self._snap_animation.setEndValue(end)
        self._snap_animation.start()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._snap_animation.stop()
        self._free = self._between = None
        super().keyPressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        }
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._animation.stop()
            self._set_bulge(0.0)
        super().changeEvent(event)

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        lit_colour = QColor(tokens.DOT_SLIDER_LIT)
        dim_colour = QColor(tokens.DOT_SLIDER_UNLIT)
        if not self.isEnabled():
            lit_colour.setAlphaF(0.4)
            dim_colour.setAlphaF(0.5)
        lit = self.lit_columns()
        centre_y = self.height() / 2
        for column in range(self.visible_columns()):
            scale = self._column_scale(column)
            radius = DOT / 2 * scale
            spacing = PITCH * (1 + (scale - 1) / 2)
            painter.setBrush(lit_colour if column < lit else dim_colour)
            x = self._column_x(column)
            for row in range(ROWS):
                y = centre_y + (row - (ROWS - 1) / 2) * spacing
                painter.drawEllipse(QPointF(x, y), radius, radius)
        if self.hasFocus() and self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1.5))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        painter.end()
