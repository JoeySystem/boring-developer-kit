"""One row of orange dots for readings and demos (进度条点阵样式 定稿 v1; design decision).

Not progress and not a slider: three rows of cream dots are a slider you
drag, a ring of cream dots is progress, one row of orange dots is a reading
or a demo. Dots are 4 across with a 3 gap; lit orange, the rest dim grey.

    joystick calibration X / Y   64 dots, 0..4095 (one dot per 64 readings)
    voice guide demo card        48 dots, 8 steps of 6; while "说话" a short
                                 run of orange dots goes back and forth

`set_value(v)` lights up to v; `set_running(True)` shows the back-and-forth
run instead. It takes the dots it asks for when there is room and fewer when
a layout is tighter, never wider. It does not react to the pointer.
Spec: Console视觉稿/进度条点阵样式-v1-定稿-20261119-图/3-摇杆读数和语音演示-单行点阵.png.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from controller_config.style import tokens

DOT = 4.0
GAP = 3.0
PITCH = DOT + GAP
MIN_DOTS = 12
RUN_DOTS = 8          # the back-and-forth run (measured on the spec)
RUN_MS = 1400         # one way across (the spec's estimate, not tried by hand)
FRAME_MS = 40


class DotRow(QWidget):
    def __init__(self, dots: int = 64, parent: QWidget | None = None, *, maximum: int = 100) -> None:
        super().__init__(parent)
        self._dots = max(MIN_DOTS, int(dots))
        self._maximum = max(1, int(maximum))
        self._value = 0
        self._running = False
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self.update)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMaximumWidth(self.sizeHint().width())
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(math.ceil(self._dots * PITCH - GAP) + 2, math.ceil(DOT) + 2)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(math.ceil(MIN_DOTS * PITCH - GAP) + 2, self.sizeHint().height())

    def dots(self) -> int:
        return self._dots

    def visible_dots(self) -> int:
        return max(MIN_DOTS, min(self._dots, int((self.width() - 2 + GAP) / PITCH)))

    def maximum(self) -> int:
        return self._maximum

    def setMaximum(self, maximum: int) -> None:  # noqa: N802 - Qt naming
        self._maximum = max(1, int(maximum))
        self.update()

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = min(self._maximum, max(0, int(value)))
        self.setAccessibleName(f"{self._value} / {self._maximum}")
        self.update()

    def is_running(self) -> bool:
        return self._running

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if self._running and self.isVisible():
            self._timer.start()
        elif not self._running:
            self._timer.stop()
        self.update()

    def lit_dots(self) -> int:
        """Dots lit for the value; the first lights as soon as it is above 0."""
        return math.ceil(self._value / self._maximum * self.visible_dots() - 1e-9)

    def lit_mask(self) -> list[bool]:
        count = self.visible_dots()
        if not self._running:
            lit = self.lit_dots()
            return [index < lit for index in range(count)]
        span = max(1, count - RUN_DOTS)
        phase = self._clock.elapsed() % (2 * RUN_MS) / RUN_MS   # 0..2: there and back
        start = round((phase if phase <= 1 else 2 - phase) * span)
        return [start <= index < start + RUN_DOTS for index in range(count)]

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self._running:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        lit, unlit = QColor(tokens.DOT_ROW_LIT), QColor(tokens.DOT_ROW_UNLIT)
        y = self.height() / 2
        for index, on in enumerate(self.lit_mask()):
            painter.setBrush(lit if on else unlit)
            painter.drawEllipse(QPointF(1 + DOT / 2 + index * PITCH, y), DOT / 2, DOT / 2)
        painter.end()
