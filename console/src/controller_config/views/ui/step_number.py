"""A numbered step circle (Prompt 页 定稿 v1 `.steps i`, design decision).

17 px circle with a small bold number; the current step turns orange with a
white number, so it is clear at a glance which step is lit.

    mark = StepNumber(2)
    mark.set_current(True)
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from controller_config.style import tokens


class StepNumber(QWidget):
    """See the module docstring."""

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._number = number
        self._current = False
        self.setFixedSize(self.sizeHint())
        self.setAccessibleName(str(number))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(tokens.PROMPT_STEP_DIAMETER, tokens.PROMPT_STEP_DIAMETER)

    def text(self) -> str:
        return str(self._number)

    def is_current(self) -> bool:
        return self._current

    def set_current(self, current: bool) -> None:
        if current != self._current:
            self._current = current
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.PROMPT_STEP_HOT if self._current else tokens.PROMPT_STEP_WELL))
        painter.drawEllipse(QRectF(self.rect()))
        font = QFont()
        font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
        # Points, so 10.5 px stays 10.5 px; logical DPI is 72 on macOS, 96 on Windows.
        font.setPointSizeF(tokens.FONT_PROMPT_STEP * 72.0 / max(1, self.logicalDpiY()))
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(tokens.WHITE if self._current else tokens.PROMPT_STEP_TEXT))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, str(self._number))
        painter.end()
