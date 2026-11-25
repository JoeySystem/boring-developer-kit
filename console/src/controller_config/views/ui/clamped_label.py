"""A wrapped label that never grows its tile: extra lines end in "…".

Settings walls have fixed tiles (design decision: no state may make a page taller,
no scroll bars inside a card). Text whose length is not ours to choose — a
release note, a device message, a translation — goes in a ClampedLabel: it
shows as many whole lines as its space holds (or ``lines`` at most), ends the
last one with "…", and keeps the whole text in text() and in the tooltip.
Where the rest matters, the tile offers a "more" link next to it.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class ClampedLabel(QLabel):
    """``lines=None`` fills the height the layout gives it; a number caps it there."""

    clamped_changed = Signal(bool)

    def __init__(self, text: str = "", *, lines: int | None = None, object_name: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        if object_name:
            self.setObjectName(object_name)
        self._lines = lines
        self._clamped = False
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Expanding if lines is None else QSizePolicy.Policy.Preferred)

    # -- sizes ---------------------------------------------------------------
    def _line_height(self) -> int:
        return self.fontMetrics().lineSpacing()

    def _cap(self, height: int) -> int:
        margins = self.contentsMargins().top() + self.contentsMargins().bottom()
        if self._lines is None:
            return min(height, self._line_height() + margins) if self.text() else 0
        return min(height, self._lines * self._line_height() + margins)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt virtual method
        # Filling: ask for one line and stretch into what the tile has left. A box layout
        # takes height-for-width as the minimum, which would push the tile's other rows out.
        return self._lines is not None

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt virtual method
        return self._cap(super().heightForWidth(width))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        hint = super().sizeHint()
        return QSize(hint.width(), self._cap(hint.height()))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        # Narrow on purpose: wrapping and the "…" handle long words, the tile keeps its size.
        margins = self.contentsMargins().top() + self.contentsMargins().bottom()
        one_line = self._line_height() + margins if self.text() else 0
        return QSize(0, one_line)

    # -- state ---------------------------------------------------------------
    def is_clamped(self) -> bool:
        return self._clamped

    def full_height(self) -> int:
        """The height the whole text would need at the current width."""
        return QLabel.heightForWidth(self, max(1, self.width()))

    def _visible_lines(self) -> int:
        room = max(0, self.contentsRect().height()) // max(1, self._line_height())
        if self._lines is not None:
            room = min(room, self._lines)
        return max(1, room)

    def _update_clamped(self) -> None:
        clamped = bool(self.text()) and self.full_height() > self.height() + 1
        if clamped != self._clamped:
            self._clamped = clamped
            self.setToolTip(self.text() if clamped else "")
            self.clamped_changed.emit(clamped)
        elif clamped and self.toolTip() != self.text():
            self.setToolTip(self.text())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt method
        super().setText(text)
        self._update_clamped()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._update_clamped()

    # -- painting --------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if not self._clamped:
            super().paintEvent(event)
            return
        rect = self.contentsRect()
        # QTextLayout breaks lines at U+2028 only; same length, so the offsets still match text().
        text = self.text().replace("\n", "\u2028")
        layout = QTextLayout(text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        wanted = self._visible_lines()
        lines = []
        layout.beginLayout()
        while len(lines) < wanted:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            lines.append(line)
        layout.endLayout()
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        metrics = self.fontMetrics()
        y = rect.top()
        for index, line in enumerate(lines):
            start, length = line.textStart(), line.textLength()
            if index == len(lines) - 1:
                rest = text[start:].replace("\u2028", " ")
                shown = metrics.elidedText(rest, Qt.TextElideMode.ElideRight, rect.width())
            else:
                shown = text[start:start + length].rstrip("\u2028")
            painter.drawText(QPointF(rect.left(), y + metrics.ascent()), shown)
            y += self._line_height()
        painter.end()
