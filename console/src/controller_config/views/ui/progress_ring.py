"""Progress as a ring of dots (the designer, 进度条点阵样式 定稿 v1, 2026-11-19; design decision).

Dots light clockwise from the top, cream up to the value, dim grey past it.
The number in the middle is the real value with no percent sign; it does
not round to the dots. Three sizes:

    48  20 dots  firmware update (ring left, text right), screen icon upload
    36  16 dots  Settings "更新" tile, banners, menu bar quota panel
    18  12 dots  top Settings tab only; too small for a number or a shape

States (`set_state`):

    running  value known: the dot it has reached blinks; value None: a few
             lit dots chase round the ring ("还不知道多久")
    paused   stopped, lit dots and number grey, nothing moves ("等着")
    done     all lit, a short hold, then the green check in the middle
    failed   every lit dot orange, orange triangle in the middle (the designer 11-20
             真屏验收: "所有被点亮的白色点阵应该全部变成橙色")

With the system "reduce motion" setting the reached dot does not blink and
the chase becomes the whole ring slowly brightening and dimming.

Quota (`quota=True`) shows what is left rather than progress: it never moves,
and the lit dots turn orange at or below `low_percent`.

The ring cannot be dragged and does not react to the pointer.
Spec: Console视觉稿/进度条点阵样式-v1-定稿-20261119-图/1-…, 5-动起来…, 6-数值和谁定的.png.
Timings are the spec's estimates, not tried by hand yet; tune them here.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from controller_config.accessibility import system_reduces_motion
from controller_config.style import tokens
from controller_config.views.ui.status_icon import StatusIcon

# size -> (dots, dot diameter, number pixel size or None)
SIZES = {
    48: (20, 4.0, tokens.PROGRESS_RING_NUMBER_MEDIUM),
    36: (16, 3.6, tokens.PROGRESS_RING_NUMBER_SMALL),
    18: (12, 2.4, None),
}
STATES = ("running", "paused", "done", "failed")
FRAME_MS = 40            # repaint while something moves; 25 fps is plenty for 4 px dots
BLINK_MS = 1000          # the reached dot: one bright-dim-bright cycle
GROW_MS = tokens.MOTION_INSTANT_MS  # a newly reached dot fading in (瞬时, design decision; was 150)
CHASE_MS = 1200          # "还不知道多久": the lit dots go round once
CHASE_TAIL = (1.0, 0.65, 0.4, 0.2)  # head bright, tail dim
BREATHE_MS = 2400        # reduce motion: the whole ring brightens and dims once
DONE_HOLD_MS = 600       # done: all lit for a moment before the check appears
ICON_SHARE = 0.34        # check / triangle width as a share of the ring (measured on 1-一圈点…png)


def _number_font(widget: QWidget, pixels: float) -> QFont:
    font = QFont()
    font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
    font.setPointSizeF(pixels * 72.0 / max(1, widget.logicalDpiY()))
    font.setWeight(QFont.Weight.DemiBold)
    # Equal-width digits (design decision 第 5 件), so "18" -> "19" does not shift.
    font.setFeature(QFont.Tag("tnum"), 1)
    return font


def _mix(base: QColor, over: QColor, amount: float) -> QColor:
    amount = min(1.0, max(0.0, amount))
    return QColor.fromRgbF(
        base.redF() + (over.redF() - base.redF()) * amount,
        base.greenF() + (over.greenF() - base.greenF()) * amount,
        base.blueF() + (over.blueF() - base.blueF()) * amount,
        base.alphaF() + (over.alphaF() - base.alphaF()) * amount,
    )


class ProgressRing(QWidget):
    """A ring of dots; `set_value(0..100 | None)`, `set_state(...)`."""

    def __init__(self, size: int = 48, parent: QWidget | None = None, *, quota: bool = False,
                 low_percent: float = tokens.QUOTA_LOW_PERCENT,
                 lit: QColor | str | tuple | None = None,
                 unlit: QColor | str | tuple | None = None,
                 number: bool = True, done_hold_ms: int | None = None, glow_floor: float = 0.0) -> None:
        """number=False leaves the middle empty while running (the connecting steps,
        design decision); done_hold_ms is how long "done" stays all lit before the check;
        glow_floor is how dim the reached dot gets while it breathes (0 = down to unlit)."""
        super().__init__(parent)
        if size not in SIZES:
            raise ValueError(f"ProgressRing size must be one of {sorted(SIZES)}, not {size}")
        self._size = size
        self._dots, self._dot, self._number_px = SIZES[size]
        self._quota = quota
        self._number = number
        self._done_hold_ms = DONE_HOLD_MS if done_hold_ms is None else int(done_hold_ms)
        self._glow_floor = min(1.0, max(0.0, float(glow_floor)))
        self._low_percent = float(low_percent)
        self._lit = QColor(*lit) if isinstance(lit, tuple) else QColor(lit or tokens.PROGRESS_RING_LIT)
        self._unlit = (QColor(*unlit) if isinstance(unlit, tuple)
                       else QColor(unlit or tokens.PROGRESS_RING_UNLIT))
        self._value: float | None = 0.0
        self._state = "running"
        self._reduced_motion = system_reduces_motion()
        self._clock = QElapsedTimer()
        self._clock.start()
        self._grown_from = 0          # lit dots before the last increase
        self._grown_at = -GROW_MS     # when it happened (clock ms)
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self.update)
        self._icon: StatusIcon | None = None
        if self._number_px is not None:  # the 18 ring has no room for a shape
            self._icon = StatusIcon("ok", self, size=max(8, round(size * ICON_SHARE)))
            self._icon.hide()
            self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._reveal = QTimer(self)
        self._reveal.setSingleShot(True)
        self._reveal.timeout.connect(self._show_check)
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._refresh()

    # -- API -------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(self._size, self._size)

    def ring_size(self) -> int:
        return self._size

    def set_display_side(self, side: int) -> None:
        """Draws the ring `side` px across, as the `size` ring scaled (dots, gaps, number and
        shape grow or shrink together). For rings inside something that scales with the
        window, such as the Mode bar's connecting steps."""
        side = max(1, int(side))
        if side == self.width():
            return
        self.setFixedSize(side, side)
        if self._icon is not None:
            self._icon.set_side(max(8, round(side * ICON_SHARE)))
        self._place_icon()
        self.update()

    def dot_count(self) -> int:
        return self._dots

    def value(self) -> float | None:
        """0..100, or None while it is not known how long it will take."""
        return self._value

    def state(self) -> str:
        return self._state

    def is_quota(self) -> bool:
        return self._quota

    def set_value(self, value: float | None) -> None:
        if value is not None:
            value = min(100.0, max(0.0, float(value)))
        if value == self._value:
            return
        before = self.lit_dots()
        self._value = value
        after = self.lit_dots()
        if value is not None and after > before and self._state == "running":
            self._grown_from, self._grown_at = before, self._clock.elapsed()
        self._refresh()

    def set_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"Unknown progress state: {state}")
        if state == self._state:
            return
        self._state = state
        self._apply_state()
        self._refresh()

    def lit_dots(self) -> int:
        """Dots lit for the value: any start lights the first dot, 100 lights all."""
        if self._state == "done":
            return self._dots
        if self._value is None:
            return 0
        return math.ceil(self._value / 100 * self._dots - 1e-9)

    def number_text(self) -> str:
        """What the middle says: the value without a percent sign, or nothing."""
        if self._number_px is None or self._value is None or not self._number:
            return ""
        if self._state in {"done", "failed"} and not self._quota:
            return ""  # the check or the triangle takes the middle
        return str(int(round(self._value)))

    def is_low(self) -> bool:
        """Quota only: nearly used up, so the lit dots are orange."""
        return self._quota and self._value is not None and self._value <= self._low_percent

    def is_animating(self) -> bool:
        return self._timer.isActive()

    def check_visible(self) -> bool:
        return self._icon is not None and self._icon.isVisibleTo(self) and self._icon.state() == "ok"

    def triangle_visible(self) -> bool:
        return self._icon is not None and self._icon.isVisibleTo(self) and self._icon.state() == "error"

    # -- state bookkeeping -------------------------------------------------------

    def _needs_frames(self) -> bool:
        if self._quota or self._state != "running":
            return False
        if self._value is None:
            return True                      # chase, or breathe with reduce motion
        return not self._reduced_motion and self.lit_dots() > 0

    def _apply_state(self) -> None:
        """The shape in the middle; only when the state changes, so value updates do not restart it."""
        self._reveal.stop()
        if self._icon is None:
            return
        self._icon.hide()
        if self._state == "failed":
            self._icon.set_state("error")
            self._icon.show()
        elif self._state == "done":
            self._icon.set_state("ok")
            if self._reduced_motion:
                self._icon.show()
            else:
                self._reveal.start(self._done_hold_ms)
        self._place_icon()

    def _refresh(self) -> None:
        if self._needs_frames() and self.isVisible():
            self._timer.start()
        elif not self._needs_frames():
            self._timer.stop()
        self.setAccessibleName(self._spoken())
        self.update()

    def _show_check(self) -> None:
        if self._state == "done" and self._icon is not None:
            self._icon.show()

    def _place_icon(self) -> None:
        if self._icon is not None:
            side = self._icon.width()
            self._icon.move(round((self.width() - side) / 2), round((self.height() - side) / 2))

    def _spoken(self) -> str:
        if self._quota:
            return "" if self._value is None else f"剩余 {self.number_text()}%"
        if self._state == "done":
            return "已完成"
        if self._state == "failed":
            return "未完成"
        if self._value is None:
            return "正在进行"
        word = "已暂停" if self._state == "paused" else "进度"
        return f"{word} {int(round(self._value))}%"

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self._needs_frames():
            self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._timer.stop()
        super().hideEvent(event)

    # -- painting -------------------------------------------------------------

    def _dot_centre(self, index: int) -> QPointF:
        radius = self._size / 2 - self._dot / 2 - 0.5
        angle = 2 * math.pi * index / self._dots
        centre = self._size / 2
        return QPointF(centre + radius * math.sin(angle), centre - radius * math.cos(angle))

    def dot_colours(self) -> list[QColor]:
        """The colour of every dot for the current frame, top first, clockwise."""
        lit, unlit = QColor(self._lit), QColor(self._unlit)
        if self._quota and self.is_low():
            lit = QColor(tokens.PROGRESS_RING_LOW)
        if self._state == "paused":
            lit = QColor(tokens.PROGRESS_RING_PAUSED)
        elif self._state == "failed":
            lit = QColor(tokens.PROGRESS_RING_FAILED)
        count = self.lit_dots()
        colours = [lit if index < count else unlit for index in range(self._dots)]
        now = self._clock.elapsed()
        if self._state == "running" and not self._quota:
            if self._value is None:
                colours = self._chase(now, lit, unlit)
            elif not self._reduced_motion and count > 0:
                for index in range(self._grown_from, count):
                    colours[index] = _mix(unlit, lit, (now - self._grown_at) / GROW_MS)
                if now - self._grown_at >= GROW_MS:
                    # Counted from when the dot finished lighting, so it starts bright and eases
                    # down: on the global clock it could drop dark the frame after it lit, which
                    # read as the ring stepping back (the designer 11-23 on the PR 105 GIF).
                    phase = (now - self._grown_at - GROW_MS) % BLINK_MS / BLINK_MS
                    glow = (1 + math.cos(2 * math.pi * phase)) / 2
                    floor = self._glow_floor
                    colours[count - 1] = _mix(unlit, lit, floor + (1 - floor) * glow)
        return colours

    def _chase(self, now: int, lit: QColor, unlit: QColor) -> list[QColor]:
        if self._reduced_motion:
            glow = 0.35 + 0.3 * (1 - math.cos(2 * math.pi * (now % BREATHE_MS) / BREATHE_MS)) / 2
            return [_mix(unlit, lit, glow)] * self._dots
        colours = [QColor(unlit) for _ in range(self._dots)]
        head = int(now % CHASE_MS / CHASE_MS * self._dots)
        for back, amount in enumerate(CHASE_TAIL):
            colours[(head - back) % self._dots] = _mix(unlit, lit, amount)
        return colours

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.width() != self._size:   # set_display_side: the same ring, scaled
            painter.scale(self.width() / self._size, self.height() / self._size)
        radius = self._dot / 2
        for index, colour in enumerate(self.dot_colours()):
            painter.setBrush(colour)
            painter.drawEllipse(self._dot_centre(index), radius, radius)
        text = self.number_text()
        if text:
            colour = QColor(self._lit)
            if self._state == "paused":
                colour = QColor(tokens.PROGRESS_RING_PAUSED)
            elif self.is_low():
                colour = QColor(tokens.PROGRESS_RING_LOW)
            painter.setPen(colour)
            painter.setFont(_number_font(self, self._number_px))
            painter.drawText(QRectF(0, 0, self._size, self._size), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class ProgressRingRow(QWidget):
    """A ring with its text beside it: ring left, text right (固件更新页, 屏幕图标上传).

    `ring` and `label` are public so a page can set the value, the state and
    the words separately; `addWidget` puts a button (取消上传) at the far right.
    """

    def __init__(self, size: int = 48, parent: QWidget | None = None, *, object_name: str = "") -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setProperty("progressRingRow", True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14 if size >= 48 else 10)
        self.ring = ProgressRing(size, self)
        self.label = QLabel(objectName="progressRingText")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self._layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802 - Qt naming
        self._layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def text(self) -> str:
        return self.label.text()

    def set_text(self, text: str) -> None:
        self.label.setText(text)
