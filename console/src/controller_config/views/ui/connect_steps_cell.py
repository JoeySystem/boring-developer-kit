"""The Mode bar's right cell while Console connects (design decision, 单 design ticket).

A 36 ring of dots on the left, the step on top, a hint under it:

    ◌  确认是你的 BORING…
       这次要把设置完整读一遍，多等几秒

What it says comes from connection_steps.StepsView; this widget only shows it.
It changes like the iPhone Dynamic Island (v1 定稿 demo): its width follows the
words with a spring that runs a little past (CONNECT_STEPS_SPRING_MS /
_OVERSHOOT), old words rise and fade while new ones rise in from below, title
and hint each on their own. With "reduce motion": no spring, words swap at once
(the ring keeps its own reduce-motion rules). When the target changes mid-way
the new spring starts at the speed the old one had (as the v1 demo does), so the
edge never stops dead and jumps off again.
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from controller_config.connection_steps import StepsView
from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.progress_ring import ProgressRing

FRAME_MS = 16


def _font(pixels: float, weight: QFont.Weight) -> QFont:
    font = QFont()
    font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
    font.setPixelSize(max(1, round(pixels)))
    font.setWeight(weight)
    return font


class SwapText(QWidget):
    """One line of words; a new text rises in from below as the old one rises away."""

    def __init__(self, parent: QWidget | None = None, *, strong: bool = False) -> None:
        super().__init__(parent)
        self._strong = strong
        self._text = ""
        self._old = ""
        self._clock = QElapsedTimer()
        self._clock.start()
        self._changed_at = -10_000
        self._scale = 1.0
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._frame)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._apply_font()

    def text(self) -> str:
        return self._text

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self._apply_font()

    def _apply_font(self) -> None:
        if self._strong:
            self.setFont(_font(14 * self._scale, QFont.Weight.DemiBold))
        else:
            self.setFont(_font(12 * self._scale, QFont.Weight.Normal))
        self.setFixedHeight(max(1, round(QFontMetricsF(self.font()).height())))
        self.updateGeometry()

    def text_width(self, text: str | None = None) -> int:
        return round(QFontMetricsF(self.font()).horizontalAdvance(self._text if text is None else text)) + 1

    def set_text(self, text: str, *, animate: bool = True) -> None:
        if text == self._text:
            return
        self._old, self._text = self._text, text
        self._changed_at = self._clock.elapsed() if animate and not motion.reduces_motion() else -10_000
        if self._changing():
            self._timer.start()
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def _changing(self) -> bool:
        out, rise_in = tokens.CONNECT_STEPS_TEXT_OUT_MS, tokens.CONNECT_STEPS_TEXT_IN_MS
        return self._clock.elapsed() - self._changed_at < out // 2 + rise_in

    def _frame(self) -> None:
        if not self._changing():
            self._timer.stop()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(self.text_width(), self.height())

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        colour = QColor(tokens.TEXT_PRIMARY if self._strong else tokens.TEXT_SECONDARY)
        rise = tokens.CONNECT_STEPS_TEXT_RISE * self._scale
        out, rise_in = tokens.CONNECT_STEPS_TEXT_OUT_MS, tokens.CONNECT_STEPS_TEXT_IN_MS
        elapsed = self._clock.elapsed() - self._changed_at
        box = QRectF(self.rect())
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if elapsed < out and self._old:
            t = motion.out_cubic(elapsed / out)
            fading = QColor(colour)
            fading.setAlphaF(colour.alphaF() * (1 - t))
            painter.setPen(fading)
            painter.drawText(box.translated(0, -rise * t), flags, self._old)
        start = out // 2          # the new words start while the old ones are half gone
        if elapsed < start:
            painter.end()
            return
        t = motion.out_cubic((elapsed - start) / rise_in)
        arriving = QColor(colour)
        arriving.setAlphaF(colour.alphaF() * t)
        painter.setPen(arriving)
        painter.drawText(box.translated(0, rise * (1 - t)), flags, self._text)
        painter.end()


class ConnectStepsCell(QWidget):
    """Ring + step + hint. `show_view(view)`; `width_changed` while the spring runs."""

    width_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeBarSteps")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._scale = 1.0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self._row = row
        self.ring = ProgressRing(tokens.CONNECT_STEPS_RING, self, number=False,
                                 done_hold_ms=tokens.CONNECT_STEPS_CHECK_MS,
                                 glow_floor=tokens.CONNECT_STEPS_HEAD_GLOW_FLOOR)
        row.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(0)
        self.title = SwapText(self, strong=True)
        self.title.setObjectName("modeBarStepsTitle")
        self.hint = SwapText(self)
        self.hint.setObjectName("modeBarStepsHint")
        words.addStretch(1)
        words.addWidget(self.title)
        words.addWidget(self.hint)
        words.addStretch(1)
        row.addLayout(words, 1)
        self._view: StepsView | None = None
        self._clock = QElapsedTimer()
        self._clock.start()
        self._spring_from = 0
        self._spring_to = 0
        self._spring_at = -10_000
        self._spring_c1 = 0.0
        self._spring_carry = 0.0    # px: the speed carried over from a spring cut short
        self._spring = QTimer(self)
        self._spring.setInterval(FRAME_MS)
        self._spring.timeout.connect(self._spring_frame)
        self.set_scale(1.0)

    def view(self) -> StepsView | None:
        return self._view

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        # The ring grows and shrinks with the bar (it was always 36: cut at the bottom when the
        # window made the bar smaller, the designer 11-23 on the PR 105 GIF).
        self.ring.set_display_side(round(tokens.CONNECT_STEPS_RING * scale))
        self._row.setSpacing(round(10 * scale))
        self.title.set_scale(scale)
        self.hint.set_scale(scale)
        if self._view is not None:
            self._settle_width(self._target_width())

    def _target_width(self) -> int:
        words = max(self.title.text_width(), self.hint.text_width() if self.hint.text() else 0)
        return self.ring.width() + self._row.spacing() + words

    def show_view(self, view: StepsView, *, from_width: int | None = None) -> None:
        """Show `view`; on its first showing the width grows from `from_width`."""
        first = self._view is None
        self._view = view
        self.ring.set_state(view.ring)
        if view.ring != "done":
            self.ring.set_value(view.value)
        animate = not first
        self.title.set_text(tr(view.title), animate=animate)
        self.hint.set_text(tr(view.hint) if view.hint else "", animate=animate)
        self.hint.setVisible(bool(view.hint) or self.hint._changing())
        target = self._target_width()
        if first:
            self._spring_to = from_width if from_width is not None else target
            self.setFixedWidth(max(1, self._spring_to))
        if target != self._spring_to:
            self._spring_toward(target)

    def clear(self) -> None:
        """Back to the bar's own cell; the next view starts afresh."""
        self._view = None
        self._spring.stop()
        self.title.set_text("", animate=False)
        self.hint.set_text("", animate=False)
        self.ring.set_state("running")
        self.ring.set_value(0)

    def _settle_width(self, width: int) -> None:
        self._spring.stop()
        self._spring_to = width
        self.setFixedWidth(max(1, width))
        self.width_changed.emit()

    def _spring_toward(self, target: int) -> None:
        if motion.reduces_motion():
            self._settle_width(target)
            return
        moving = self._spring.isActive()
        speed = self._spring_speed() if moving else 0.0   # px per whole spring run
        self._spring_from = self.width()
        self._spring_to = target
        self._spring_at = self._clock.elapsed()
        distance = target - self._spring_from
        self._spring_c1 = motion.back_out_for(tokens.CONNECT_STEPS_OVERSHOOT * target, abs(distance))
        # From rest: plain back_out. Cut short: back_out starts at (c1 + 3) x distance per run;
        # the extra t(1-t)^2 term makes the start speed what the old spring had, and is 0
        # (with 0 speed) at the end.
        self._spring_carry = speed - distance * (self._spring_c1 + 3.0) if moving else 0.0
        self._spring.start()

    def _spring_t(self) -> float:
        return min(1.0, max(0.0, (self._clock.elapsed() - self._spring_at) / tokens.CONNECT_STEPS_SPRING_MS))

    def _spring_width(self, t: float) -> float:
        share = motion.back_out(t, self._spring_c1)
        return (self._spring_from + (self._spring_to - self._spring_from) * share
                + self._spring_carry * t * (1.0 - t) ** 2)

    def _spring_speed(self) -> float:
        t = self._spring_t()
        c1 = self._spring_c1
        u = t - 1.0
        slope = 3.0 * (c1 + 1.0) * u * u + 2.0 * c1 * u          # d back_out / dt
        return ((self._spring_to - self._spring_from) * slope
                + self._spring_carry * (1.0 - t) * (1.0 - 3.0 * t))

    def _spring_frame(self) -> None:
        t = self._spring_t()
        if t >= 1:
            self._settle_width(self._spring_to)
            return
        self.setFixedWidth(max(1, round(self._spring_width(t))))
        self.width_changed.emit()

    def springing(self) -> bool:
        return self._spring.isActive()
