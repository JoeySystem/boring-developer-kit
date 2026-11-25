"""Shared motion helpers (design decision): the four durations, the two curves and
"reduce motion" in one place, so pages and components do not each decide.

Durations and curve names live in `style/tokens.py` (MOTION_*); this module
only turns them into Qt objects and small curve functions.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QVariantAnimation

from controller_config.accessibility import system_reduces_motion
from controller_config.style import tokens

INSTANT = tokens.MOTION_INSTANT_MS
SHORT = tokens.MOTION_SHORT_MS
MEDIUM = tokens.MOTION_MEDIUM_MS
LONG = tokens.MOTION_LONG_MS


def reduces_motion() -> bool:
    """The system's "reduce motion" setting (macOS); False elsewhere."""
    return system_reduces_motion()


def curve_in() -> QEasingCurve:
    """Coming in and pressing: OutCubic."""
    return QEasingCurve(getattr(QEasingCurve.Type, tokens.MOTION_CURVE_IN))


def curve_swap() -> QEasingCurve:
    """Two pictures taking turns: InOutCubic."""
    return QEasingCurve(getattr(QEasingCurve.Type, tokens.MOTION_CURVE_SWAP))


def bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    """A CSS-style cubic-bezier(x1, y1, x2, y2) as a Qt curve."""
    easing = QEasingCurve(QEasingCurve.Type.BezierSpline)
    easing.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return easing


def out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def in_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t ** 3


def back_out(t: float, c1: float) -> float:
    """Ease-out that runs past 1 and comes back (the "back-out" curve)."""
    t = min(1.0, max(0.0, t))
    c3 = c1 + 1.0
    u = t - 1.0
    return 1.0 + c3 * u ** 3 + c1 * u ** 2


def back_out_peak(c1: float) -> float:
    """How far past 1 `back_out` goes at its peak."""
    if c1 <= 0:
        return 0.0
    c3 = c1 + 1.0
    u = 2.0 * c1 / (3.0 * c3)       # 1 - t at the peak
    return u * u * (c1 - c3 * u)


def back_out_for(overshoot: float, distance: float) -> float:
    """The c1 that makes a slide of `distance` run `overshoot` past its end."""
    if distance <= 0 or overshoot <= 0:
        return 0.0
    want = overshoot / distance
    low, high = 0.0, 20.0
    for _ in range(40):
        middle = (low + high) / 2
        if back_out_peak(middle) < want:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def pop_scale(elapsed_ms: float) -> float:
    """A check mark popping in (-15 第 5 处): 0.6 → 1.12 in 短, back to 1 in 瞬时."""
    up, down = SHORT, INSTANT
    if elapsed_ms <= 0:
        return tokens.CHECK_POP_FROM
    if elapsed_ms < up:
        return tokens.CHECK_POP_FROM + (tokens.CHECK_POP_PEAK - tokens.CHECK_POP_FROM) * out_cubic(elapsed_ms / up)
    if elapsed_ms < up + down:
        return tokens.CHECK_POP_PEAK + (1.0 - tokens.CHECK_POP_PEAK) * out_cubic((elapsed_ms - up) / down)
    return 1.0


class Pop(QObject):
    """Scale for a small painted mark that pops once; the widget reads `scale()` when it paints."""

    def __init__(self, widget) -> None:
        super().__init__(widget)
        self._widget = widget
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(float(SHORT + INSTANT))
        self.animation.setDuration(SHORT + INSTANT)
        self.animation.valueChanged.connect(lambda _value: widget.update())
        self.animation.finished.connect(widget.update)

    def start(self) -> None:
        if reduces_motion():
            return
        self.animation.stop()
        self.animation.start()

    def scale(self) -> float:
        if self.animation.state() != QVariantAnimation.State.Running and self.animation.currentTime() in (0, self.animation.duration()):
            return 1.0
        return pop_scale(float(self.animation.currentValue() or 0.0))
