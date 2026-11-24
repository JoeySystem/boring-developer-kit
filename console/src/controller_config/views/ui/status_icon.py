"""The five status shapes (the designer, status graphics v1 final, 2026-11-18).

They show how far something has got, not whether it is on or off:

    off      dashed circle       not started / not set up
    busy     spinner             the Console is working on it
    wait     dot with three dots waiting: orange = waiting for you, cream = waiting for the device
    ok       green check         done / normal / on
    error    orange rounded triangle

Spec: Console视觉稿/状态图形-v1-定稿-20261118-图/1-五种状态图形.png.

With the system's "reduce motion" on, "busy" does not spin (Console动效盘点
11-19, item 10). PLACEHOLDER until the designer draws the still "busy" shape
(design ticket): it holds the spinner's first frame, the arc over the top
right quarter. Not the three dots: those already mean "wait".
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QWidget

from controller_config.accessibility import system_reduces_motion
from controller_config.style import tokens

STATES = ("off", "busy", "wait", "ok", "error")
DASHES = 8                 # dashed circle: equal segments, never touching
SPIN_INTERVAL_MS = 16
SPIN_DEGREES_PER_TICK = 6  # one turn a second


class StatusIcon(QWidget):
    """One status shape; `set_state(state, waiting_for="you" | "device")`."""

    def __init__(self, state: str = "off", parent: QWidget | None = None, *,
                 size: int = tokens.CHECK_SIZE, waiting_for: str = "you", reduce_motion: bool | None = None) -> None:
        super().__init__(parent)
        self._reduce_motion = system_reduces_motion() if reduce_motion is None else reduce_motion
        self._size = size
        self._state = "off"
        self._waiting_for = "you"
        self._angle = 0.0
        self._cream = False
        self._timer = QTimer(self)
        self._timer.setInterval(SPIN_INTERVAL_MS)
        self._timer.timeout.connect(self._spin)
        self.setFixedSize(size, size)
        self._pop = None   # made on first pop()
        self.set_state(state, waiting_for=waiting_for)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(self._size, self._size)

    def set_side(self, side: int) -> None:
        """Redraws at `side` px (a ring that scales with the window keeps its check in step)."""
        self._size = max(1, int(side))
        self.setFixedSize(self._size, self._size)
        self.update()

    def pop(self) -> None:
        """Pop once: 0.6 → 1.12 → 1 (授权回来以后的绿勾, -15 第 5 处)."""
        from controller_config.views.ui.motion import Pop   # motion imports nothing from here
        if self._reduce_motion:
            return
        if self._pop is None:
            self._pop = Pop(self)
        self._pop.start()

    def state(self) -> str:
        return self._state

    def set_state(self, state: str, *, waiting_for: str = "you") -> None:
        if state not in STATES:
            raise ValueError(f"Unknown status: {state}")
        if waiting_for not in {"you", "device"}:
            raise ValueError(f"Unknown waiting_for: {waiting_for}")
        self._state, self._waiting_for = state, waiting_for
        self.setAccessibleName({
            "off": "还没开始", "busy": "正在进行", "ok": "已完成", "error": "出错",
            "wait": "等你操作" if waiting_for == "you" else "等待设备",
        }[state])
        if state == "busy" and self.isVisible() and not self._reduce_motion:
            self._timer.start()
        elif state != "busy":
            self._timer.stop()
        self.update()

    def set_cream(self, cream: bool) -> None:
        """Draw the dashed circle cream instead of grey (top bar: 开发设备 · 未认证)."""
        self._cream = cream
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self._state == "busy" and not self._reduce_motion:
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._timer.stop()
        super().hideEvent(event)

    def _spin(self) -> None:
        self._angle = (self._angle + SPIN_DEGREES_PER_TICK) % 360
        self.update()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = float(self._size)
        stroke = max(1.2, side / 11)
        # Keep strokes inside the box: radius accounts for the pen width.
        circle = QRectF(stroke / 2 + 0.5, stroke / 2 + 0.5, side - stroke - 1, side - stroke - 1)
        scale = self._pop.scale() if self._pop is not None else 1.0
        if scale != 1.0:
            painter.translate(side / 2, side / 2)
            painter.scale(scale, scale)
            painter.translate(-side / 2, -side / 2)
        getattr(self, f"_paint_{self._state}")(painter, circle, stroke)
        painter.end()

    def _paint_off(self, painter: QPainter, circle: QRectF, stroke: float) -> None:
        colour = tokens.TEXT_PRIMARY if self._cream else tokens.TEXT_SECONDARY
        pen = QPen(QColor(colour), stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        span = 360 / DASHES
        for index in range(DASHES):
            start = index * span + span * 0.2
            painter.drawArc(circle, round(start * 16), round(span * 0.6 * 16))

    def _paint_busy(self, painter: QPainter, circle: QRectF, stroke: float) -> None:
        track = QColor(tokens.TEXT_PRIMARY)
        track.setAlphaF(0.2)
        painter.setPen(QPen(track, stroke))
        painter.drawEllipse(circle)
        painter.setPen(QPen(QColor(tokens.TEXT_PRIMARY), stroke, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        painter.drawArc(circle, round(-self._angle * 16), 90 * 16)

    def _paint_wait(self, painter: QPainter, circle: QRectF, _stroke: float) -> None:
        fill = QColor(tokens.ACCENT if self._waiting_for == "you" else tokens.TEXT_PRIMARY)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        whole = QRectF(0.5, 0.5, self._size - 1, self._size - 1)
        painter.drawEllipse(whole)
        dot = self._size / 9
        centre = whole.center()
        painter.setBrush(QColor(tokens.SURFACE_BASE))
        for offset in (-2.6, 0.0, 2.6):
            painter.drawEllipse(QPointF(centre.x() + offset * dot, centre.y()), dot, dot)

    def _paint_ok(self, painter: QPainter, _circle: QRectF, _stroke: float) -> None:
        whole = QRectF(0.5, 0.5, self._size - 1, self._size - 1)
        disc = QPainterPath()
        disc.addEllipse(whole)
        # The check is cut out of the disc, about 40% of its width.
        width = whole.width() * 0.4
        centre = whole.center()
        check = QPainterPath()
        check.moveTo(centre.x() - width / 2, centre.y())
        check.lineTo(centre.x() - width / 8, centre.y() + width * 0.38)
        check.lineTo(centre.x() + width / 2, centre.y() - width * 0.38)
        stroker_pen = QPen(Qt.GlobalColor.black, max(1.4, self._size / 9), Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        stroker = QPainterPathStroker(stroker_pen)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.STATUS_DONE))
        painter.drawPath(disc.subtracted(stroker.createStroke(check)))

    def _paint_error(self, painter: QPainter, _circle: QRectF, stroke: float) -> None:
        side = self._size - 1.0
        top, left, right, bottom = 1.0, 0.5, side + 0.5, side * 0.9 + 0.5
        triangle = QPainterPath()
        triangle.moveTo(side / 2 + 0.5, top)
        triangle.lineTo(right, bottom)
        triangle.lineTo(left, bottom)
        triangle.closeSubpath()
        painter.setPen(QPen(QColor(tokens.ACCENT), stroke * 1.6, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(QColor(tokens.ACCENT))
        painter.drawPath(triangle)  # the round join rounds the three corners
        painter.setPen(QPen(QColor(tokens.SURFACE_BASE), max(1.2, self._size / 12),
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        centre_x = side / 2 + 0.5
        painter.drawLine(QPointF(centre_x, bottom * 0.42), QPointF(centre_x, bottom * 0.68))
        painter.drawPoint(QPointF(centre_x, bottom * 0.84))

