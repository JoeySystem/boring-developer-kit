"""A lighting tile opening two cells wide, and closing again (-08 答复第 3 件, design decision).

the designer approved the demo (tile-widening animation demo, 2026-11-19):

    open   240 ms; the moving edge runs about 50 past, then springs back
           (cubic-bezier(.3, 2.3, .55, 1)); the brighter edge and a faint
           shadow come in with the width; the closed content turns into the
           open content as it widens
    close  240 ms, no bounce: the open content is gone in the first 30 %,
           the tile shrinks, the closed content comes back last; never both
    reduce motion: no animation (the host does not call this)

The real tiles are already in their final places underneath; this overlay
only paints the change, then goes away. Numbers: tokens.TILE_*.

the designer 11-20 真屏验收 ("展开和缩回去的时候都会闪一下"、卡顿; "实现路径设计得有问题",
it should feel like the Dynamic Island): the overlay covers only the two cells,
not the whole window; the moving tile is veiled (opacity 0) while it plays, so
its blurred shadow is not re-rendered under every frame and it cannot show for a
frame before the overlay; the overlay's shadow is made by the same drop-shadow
effect the open tile uses, so the last frame and the real tile match.

Real-screen check 11-22: a still of the cells under the overlay (`base`) carried
the glass window's 40 % tint and doubled it while it played (a darker box that
came and went). So nothing is painted under the tile any more: the real tiles
stay there, and the one being covered while it opens stays shown (`reveal`)
until the open tile is over it.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QGraphicsPixmapItem, QGraphicsScene, QWidget,
)

from controller_config.style import tokens
from controller_config.views.ui.motion import bezier, curve_swap, out_cubic


def _peak(curve) -> float:
    return max(curve.valueForProgress(i / 200) for i in range(201)) - 1.0


def _bounce(growth: float):
    """The approved curve's shape (cubic-bezier(.3, y, .55, 1)) with y set so the moving
    edge runs TILE_OPEN_OVERSHOOT points past for this much growth. the designer approved
    both "约 50 点" and "约 cubic-bezier(.3,2.3,.55,1)"; at 200 -> 416 that curve alone
    runs about 79 past, so the 50 points win and the curve keeps its shape."""
    x1, _y1, x2, y2 = tokens.TILE_OPEN_CURVE
    want = tokens.TILE_OPEN_OVERSHOOT / growth
    low, high = 1.0, 6.0
    for _ in range(30):
        middle = (low + high) / 2
        if _peak(bezier(x1, middle, x2, y2)) < want:
            low = middle
        else:
            high = middle
    return bezier(x1, (low + high) / 2, x2, y2)


def _mix(a: tuple, b: tuple, t: float) -> QColor:
    return QColor(*(round(x + (y - x) * t) for x, y in zip(a, b)))


def shadow_picture(width: int, height: int, radius: float) -> tuple[QImage, int]:
    """The open tile's shadow, drawn by the same QGraphicsDropShadowEffect the tile
    wears (FoldingTile.restore_effect), and how far it reaches past the tile."""
    r, g, b, a = tokens.TILE_SHADOW
    margin = tokens.TILE_SHADOW_BLUR + tokens.TILE_SHADOW_OFFSET
    body = QPixmap(width, height)
    body.fill(Qt.GlobalColor.transparent)
    painter = QPainter(body)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.WALL_TILE))
    painter.drawRoundedRect(QRectF(0, 0, width, height), radius, radius)
    painter.end()
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(body)
    effect = QGraphicsDropShadowEffect()
    effect.setBlurRadius(tokens.TILE_SHADOW_BLUR)
    effect.setOffset(0, tokens.TILE_SHADOW_OFFSET)
    effect.setColor(QColor(r, g, b, a))
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    image = QImage(width + 2 * margin, height + 2 * margin, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    scene.render(painter, QRectF(image.rect()), QRectF(-margin, -margin, image.width(), image.height()))
    painter.end()
    return image, margin


class TileMorph(QWidget):
    def __init__(self, host: QWidget, *, base: QPixmap | None, base_at: QRect, start: QRectF, end: QRectF,
                 old: QPixmap, new: QPixmap, opening: bool, radius: float,
                 veiled: QWidget | None = None, reveal: QWidget | None = None) -> None:
        super().__init__(host)
        self.setObjectName("tileMorph")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._base, self._base_at = base, QRect(base_at)
        self._start, self._end = QRectF(start), QRectF(end)
        self._old, self._new = old, new
        self._opening = opening
        self._radius = radius
        self._curve = (_bounce(abs(end.width() - start.width()) or 1.0) if opening else curve_swap())
        wide = self._start if self._start.width() > self._end.width() else self._end
        self._shadow, self._shadow_margin = shadow_picture(round(wide.width()), round(wide.height()), radius)
        # Only the two cells (and room for the bounce and the shadow) repaint.
        self.setGeometry(self._base_at)
        self._veiled = veiled
        self._reveal = reveal
        if reveal is not None:
            reveal.show()   # stays under the opening tile until it is covered
        if veiled is not None:
            veil = QGraphicsOpacityEffect(veiled)
            veil.setOpacity(0.0)
            veiled.setGraphicsEffect(veil)   # replaces its shadow effect until we finish
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(tokens.TILE_OPEN_MS)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.valueChanged.connect(lambda _value: self.update())
        self.animation.finished.connect(self.finish)
        self.show()
        self.raise_()
        self.animation.start()

    def finish(self) -> None:
        """Hand back to the real tile (its own shadow again) and go."""
        self.animation.stop()
        reveal, self._reveal = self._reveal, None
        if reveal is not None:
            reveal.hide()
        veiled, self._veiled = self._veiled, None
        if veiled is not None:
            restore = getattr(veiled, "restore_effect", None)
            if restore is not None:
                restore()
            else:
                veiled.setGraphicsEffect(None)
        self.hide()
        self.deleteLater()

    def progress(self) -> float:
        return float(self.animation.currentValue() or 0.0)

    def body(self, p: float | None = None) -> QRectF:
        p = self.progress() if p is None else p
        g = self._curve.valueForProgress(p)
        s, e = self._start, self._end
        return QRectF(QPointF(s.left() + (e.left() - s.left()) * g, s.top()),
                      QPointF(s.right() + (e.right() - s.right()) * g, s.bottom()))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        p = self.progress()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(-QPointF(self._base_at.topLeft()))   # host coordinates from here
        if self._base is not None:
            painter.drawPixmap(self._base_at.topLeft(), self._base)
        rect = self.body(p)
        # How "raised" it looks: comes in with the width, goes with it.
        raised = out_cubic(p) if self._opening else 1.0 - out_cubic(p)
        self._paint_shadow(painter, rect, raised)
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.WALL_TILE))
        painter.drawPath(path)
        painter.save()
        painter.setClipPath(path)
        if self._opening:
            old_alpha = 1.0 - min(1.0, p / 0.4)
            new_alpha = min(1.0, p / 0.6)
        else:
            old_alpha = 1.0 - min(1.0, p / tokens.TILE_CONTENT_OUT_SHARE)
            new_alpha = max(0.0, (p - 0.8) / 0.2)
        # old: what the tile showed before (at the start's corner); new: what it shows after.
        for picture, alpha, at in ((self._old, old_alpha, self._start), (self._new, new_alpha, self._end)):
            if alpha > 0.0:
                painter.save()
                inner = QPainterPath()   # leave out the picture's own outline: ours is drawn on top
                inner.addRoundedRect(QRectF(at.left(), at.top(), at.width(), at.height()).adjusted(1.5, 1.5, -1.5, -1.5),
                                     self._radius - 1.5, self._radius - 1.5)
                painter.setClipPath(inner, Qt.ClipOperation.IntersectClip)
                painter.setOpacity(alpha)
                painter.drawPixmap(QPointF(at.left(), at.top()), picture)
                painter.restore()
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_mix(tokens.WALL_EDGE, tokens.WALL_EDGE_RAISED, raised), 1))
        painter.drawPath(path)
        painter.end()

    def _paint_shadow(self, painter: QPainter, rect: QRectF, strength: float) -> None:
        """The real effect's shadow, stretched in the middle to the width now."""
        if strength <= 0.01:
            return
        image, m = self._shadow, self._shadow_margin
        side = m + round(self._radius) + 2           # the rounded ends keep their shape
        middle_src = image.width() - 2 * side
        out = rect.adjusted(-m, -m, m, m)
        middle_dst = out.width() - 2 * side
        painter.save()
        painter.setOpacity(strength)
        h = image.height()
        painter.drawImage(QRectF(out.left(), out.top(), side, h), image, QRectF(0, 0, side, h))
        if middle_dst > 0 and middle_src > 0:
            painter.drawImage(QRectF(out.left() + side, out.top(), middle_dst, h), image,
                              QRectF(side, 0, middle_src, h))
        painter.drawImage(QRectF(out.right() - side, out.top(), side, h), image,
                          QRectF(image.width() - side, 0, side, h))
        painter.restore()
