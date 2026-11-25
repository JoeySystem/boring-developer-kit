"""Changing pages (design decision; changed after the designer's 11-20 真屏验收: "我想象的是卡片从
左侧、右侧、上方或者下方滑进来，原本页面是空的" and, asked which side, "按页签方向").

The old page fades out sliding a little the other way; the page is then empty
and its cards slide in one after another from the side of the tab you went to
(a tab to the right: from the right). Pictures only: the real page is in place
but veiled (opacity 0) by the host until the overlay finishes, so nothing of it
shows through as a second, ghost layer; a click during the 0.46 s still goes
straight to it (the motion "jumps to the end").

    1 old page: snapshot, 8 the other way while fading, 瞬时 0.12
    2 the rest of the page (words painted on the page, not cards): fades in, 中 0.26
    3 each card: from PAGE_SLIDE_DISTANCE out on the tab's side to its place, 中 0.26,
      fully shown after the first third; no scale, no shadow
    4 order: the card nearest the incoming side first (centre x, then top);
      the last starts 0.20 later than the first; 长 0.46 in all
    7 a new click while it runs: the current frame becomes the old page
    8 reduce motion: no motion (the host does not call this)

All numbers are in style/tokens.py (PAGE_*, MOTION_*).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from controller_config.style import tokens
from controller_config.views.ui.motion import curve_swap, out_cubic

FRAME_MS = 16


@dataclass
class Block:
    """One card sliding in: a picture of a real widget, where it lands (host coordinates)."""

    rect: QRect
    picture: QPixmap
    start_ms: float = 0.0
    riders: list = field(default_factory=list)   # (rect, picture) that come in with it


def block_progress(elapsed_ms: float, start_ms: float) -> float:
    """0..1 through one block's fall."""
    return min(1.0, max(0.0, (elapsed_ms - start_ms) / tokens.MOTION_MEDIUM_MS))


def block_pose(p: float, direction: int) -> tuple[float, float]:
    """(x offset, opacity) of a card `p` of the way through sliding in from `direction`'s side."""
    offset = tokens.PAGE_SLIDE_DISTANCE * direction * (1.0 - out_cubic(p))
    opacity = min(1.0, p / tokens.PAGE_FADE_SHARE) if p > 0 else 0.0
    return offset, opacity


def stagger(centres: list[tuple[float, float]], direction: int) -> list[float]:
    """Start time of each block: nearest the incoming side first, last 0.20 s after the first."""
    if not centres:
        return []
    order = sorted(range(len(centres)),
                   key=lambda i: (-centres[i][0] if direction > 0 else centres[i][0], centres[i][1]))
    starts = [0.0] * len(centres)
    steps = max(1, len(order) - 1)
    for rank, index in enumerate(order):
        starts[index] = tokens.PAGE_STAGGER_MS * rank / steps if len(order) > 1 else 0.0
    return starts


class PageArrival(QWidget):
    """An overlay over the page area that draws the change, then goes away."""

    finished = Signal()   # gone: the host shows the real page again

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("pageArrival")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._background: QPixmap | None = None
        self._old: QPixmap | None = None
        self._old_at = QPointF()
        self._rest: QPixmap | None = None
        self._rest_at = QPointF()
        self._blocks: list[Block] = []
        self._direction = 1
        self._clock = QElapsedTimer()
        self._frozen_ms: float | None = None   # tests and frame captures
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # -- the host's side ------------------------------------------------------

    def running(self) -> bool:
        return self.isVisible()

    def cover(self, old: QPixmap, at: QPointF, background: QPixmap | None, direction: int) -> None:
        """Show the old page still, before the new page is built under it."""
        self._old, self._old_at = old, QPointF(at)
        self._rest = None
        self._blocks = []
        self._background = background
        self._direction = 1 if direction >= 0 else -1
        self._timer.stop()
        self._frozen_ms = 0.0
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def snapshot(self) -> QPixmap:
        """The frame on screen now: the old page for the next change."""
        return clear_grab(self)

    def play(self, rest: QPixmap, rest_at: QPointF, blocks: list[Block]) -> None:
        self._rest, self._rest_at = rest, QPointF(rest_at)
        self._blocks = blocks
        self._frozen_ms = None
        self._clock.start()
        self._timer.start()
        self.raise_()   # a page host made for this page is a newer sibling, above us
        self.update()

    def finish(self) -> None:
        self._timer.stop()
        self._old = self._rest = None
        self._blocks = []
        self.hide()
        self.finished.emit()

    def elapsed_ms(self) -> float:
        if self._frozen_ms is not None:
            return self._frozen_ms
        return float(self._clock.elapsed())

    def freeze_at(self, ms: float | None) -> None:
        """Hold the picture at `ms` (None: run on the clock again). For frame captures."""
        if ms is None:
            self._clock.start()
            self._frozen_ms = None
            self._timer.start()
        else:
            self._timer.stop()
            self._frozen_ms = float(ms)
        self.update()

    def _tick(self) -> None:
        if self.elapsed_ms() >= tokens.MOTION_LONG_MS:
            self.finish()
            return
        self.update()

    # -- drawing --------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        # No fill of our own: the window shows through as it really is (the real
        # page under us is veiled), so the glass is not tinted twice.
        if self._background is not None:
            painter.drawPixmap(0, 0, self._background)
        t = self.elapsed_ms()
        d = self._direction
        if self._rest is not None:
            painter.save()
            painter.setOpacity(out_cubic((t - tokens.MOTION_INSTANT_MS) / tokens.MOTION_MEDIUM_MS))
            painter.drawPixmap(self._rest_at, self._rest)
            painter.restore()
        if self._old is not None:
            p = min(1.0, t / tokens.MOTION_INSTANT_MS) if self._rest is not None else 0.0
            if p < 1.0:
                painter.save()
                painter.setOpacity(1.0 - out_cubic(p))
                painter.drawPixmap(self._old_at + QPointF(-tokens.PAGE_OLD_SHIFT * d * out_cubic(p), 0),
                                   self._old)
                painter.restore()
        for block in self._blocks:
            self._paint_block(painter, block, t)
        painter.end()

    def _paint_block(self, painter: QPainter, block: Block, t: float) -> None:
        p = block_progress(t, block.start_ms)
        if p <= 0:
            return
        offset, opacity = block_pose(p, self._direction)
        painter.save()
        painter.setOpacity(opacity)
        painter.drawPixmap(QPointF(block.rect.topLeft()) + QPointF(offset, 0), block.picture)
        for rider_rect, picture in block.riders:
            painter.drawPixmap(QPointF(rider_rect.topLeft()) + QPointF(offset, 0), picture)
        painter.restore()


def clear_grab(widget: QWidget, *, own_background: bool = False) -> QPixmap:
    """`widget` and its children on a transparent picture.

    `QWidget.grab()` fills the picture with the palette's window colour first,
    which shows as a light box round a page or a rounded card.
    """
    ratio = widget.devicePixelRatioF()
    picture = QPixmap(round(widget.width() * ratio), round(widget.height() * ratio))
    picture.setDevicePixelRatio(ratio)
    picture.fill(Qt.GlobalColor.transparent)
    flags = QWidget.RenderFlag.DrawChildren
    if own_background:   # a card's fill comes from its stylesheet; a page's would be the light palette
        flags |= QWidget.RenderFlag.DrawWindowBackground
    widget.render(picture, QPoint(0, 0), QRegion(), flags)
    return picture


def hidden_grab(page: QWidget, hide: list[QWidget]) -> QPixmap:
    """`page` drawn without the widgets in `hide` (they keep their place)."""
    effects = []
    borrowed = []   # widgets with their own opacity effect: turned to 0 and back
    for widget in hide:
        own = widget.graphicsEffect()
        if isinstance(own, QGraphicsOpacityEffect):
            borrowed.append((own, own.opacity(), own.isEnabled()))
            own.setOpacity(0.0)
            own.setEnabled(True)
            continue
        if own is not None and not hasattr(widget, "restore_effect"):
            continue   # its own effect would be lost; it simply stays in the picture
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        effects.append(widget)
    try:
        return clear_grab(page)
    finally:
        for widget in effects:
            widget.setGraphicsEffect(None)
            if hasattr(widget, "restore_effect"):
                widget.restore_effect()   # e.g. an open lighting tile's shadow
        for effect, opacity, enabled in borrowed:
            effect.setOpacity(opacity)
            effect.setEnabled(enabled)


class CrossFade(QLabel):
    """A still of `widget` before a change, fading out over it (the tab icons, 瞬时)."""

    def __init__(self, widget: QWidget, before: QPixmap, duration_ms: int) -> None:
        super().__init__(widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setPixmap(before)
        ratio = before.devicePixelRatio() or 1.0
        width, height = round(before.width() / ratio), round(before.height() / ratio)
        self.setGeometry(QRect(0, 0, width, height))
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(max(1, duration_ms))
        self.animation.setStartValue(1.0)
        self.animation.setEndValue(0.0)
        self.animation.setEasingCurve(curve_swap())
        self.animation.valueChanged.connect(lambda value: self._effect.setOpacity(float(value)))
        self.animation.finished.connect(self.deleteLater)
        self.animation.start()
        self.show()
        self.raise_()
