"""Layers the Mode page lays over the whole window, not just its own rectangle.

The stage (views/mode_page.py) is a widget inside the page area: it stops short of
the window's edges (the content margins, the strip under the page) and cannot draw
past them. Two things in the Mode页 定稿 do reach the window's edges (design decision,
design ticket):

    ModeBackdrop    under everything in the window, over the window's own dotted
                    field: the screens either side, cut by the window's edge and
                    not by the stage's, and (later) the dot floor down to the
                    window's bottom.
    ModeSweep       over everything: the sheet of dots that wells up on connecting
                    and runs back down on disconnecting, across the whole window,
                    top bar included, all but the three window buttons.

Both are children of the frame filling the window: over its fill (the console
frame paints an opaque one where macOS glass is off), under or over its rows.
One stage owns them: it shows, hides and deletes them with itself. They draw in the stage's 960×532 page
units, so whatever they draw lines up with the stage.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath

from controller_config.style import tokens
from controller_config.views.mode_motion import paint_sweep
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid


def window_root(widget: QWidget) -> QWidget | None:
    """The widget filling the window that `widget` sits in: the child of the window's
    central widget holding it (the console frame, which paints the window's fill), or
    None outside a window."""
    window = widget.window()
    central = getattr(window, "centralWidget", lambda: None)()
    if central is None or central is widget or not central.isAncestorOf(widget):
        return None
    host = widget
    while host.parentWidget() is not central:
        host = host.parentWidget()
    return host if host is not widget else central


class _WindowLayer(QWidget):
    """A see-through layer the size of the window's frame, for one stage."""

    def __init__(self, stage, root: QWidget) -> None:
        super().__init__(root)
        self._stage = stage
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(root.rect())
        root.installEventFilter(self)
        stage.destroyed.connect(self.deleteLater)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt virtual method
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def _stage_here(self) -> bool:
        """False once the stage is gone, out of this window, or hidden: draw nothing then."""
        stage = self._stage
        return (isValid(stage) and self.parentWidget() is not None
                and self.parentWidget().isAncestorOf(stage) and stage.isVisible())

    def _begin(self, painter: QPainter) -> None:
        """Clip to the window's rounded corners and move into the stage's page units."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        window = self.window().centralWidget() if hasattr(self.window(), "centralWidget") else None
        radius = float(getattr(window, "_corner_radius", 0.0) or 0.0)
        if radius > 0:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()), radius, radius)
            painter.setClipPath(clip, Qt.ClipOperation.IntersectClip if painter.hasClipping()
                                else Qt.ClipOperation.ReplaceClip)
        stage = self._stage
        at = stage.mapTo(self.parentWidget(), QPoint(0, 0))
        origin = stage.origin()
        painter.translate(at.x() + origin.x(), at.y() + origin.y())
        painter.scale(stage.scale(), stage.scale())


class ModeBackdrop(_WindowLayer):
    """Under the whole window: what the Mode page draws out to the window's edges."""

    def __init__(self, stage, root: QWidget) -> None:
        super().__init__(stage, root)
        self.lower()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if not self._stage_here():
            return
        painter = QPainter(self)
        self._begin(painter)
        self._stage.paint_backdrop(painter)
        painter.end()


class ModeSweep(_WindowLayer):
    """Over the whole window while the connect / disconnect sheet runs (design decision)."""

    def __init__(self, stage, root: QWidget) -> None:
        super().__init__(stage, root)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        link = self._stage.link() if self._stage_here() else None
        if link is None:
            return
        painter = QPainter(self)
        x, y, width, height = tokens.MODE_SWEEP_SKIP
        skip = QPainterPath()
        skip.addRoundedRect(QRectF(x, y, width, height), height / 2, height / 2)
        keep = QPainterPath()
        keep.addRect(QRectF(self.rect()))
        painter.setClipPath(keep.subtracted(skip))
        self._begin(painter)
        dots = paint_sweep(painter, link.sweep_progress(), self._stage.sweep_span())
        self._stage.link_overlay.count(dots)
        painter.end()
