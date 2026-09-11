"""Accessible heading label using the community system-font renderer."""

from __future__ import annotations

import math

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QStyleOption

from controller_config.digital_font import draw_qpainter, layout_text, normalize_text


class Boring5RLabel(QLabel):
    """QLabel-compatible wrapper; class name retained for source compatibility."""

    def __init__(
        self,
        text: str,
        *,
        scale: float = 1.0,
        color: str | QColor | None = None,
        alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft
        | Qt.AlignmentFlag.AlignVCenter,
        objectName: str | None = None,
    ) -> None:
        super().__init__()
        self._scale = scale
        self._digital_color = QColor(color) if color is not None else None
        if objectName is not None:
            self.setObjectName(objectName)
        self.setAlignment(alignment)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setText(text)

    @property
    def scale(self) -> float:
        return self._scale

    def setText(self, text: str) -> None:  # noqa: N802 - Qt virtual method
        normalized = normalize_text(text)
        super().setText(normalized)
        self.setAccessibleName(normalized)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        layout = layout_text(self.text(), scale=self._scale)
        margins = self.contentsMargins()
        return QSize(
            math.ceil(layout.width) + margins.left() + margins.right() + 1,
            math.ceil(layout.height) + margins.top() + margins.bottom() + 1,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_Widget,
            option,
            painter,
            self,
        )

        layout = layout_text(self.text(), scale=self._scale)
        rect = self.contentsRect()
        alignment = self.alignment()
        if alignment & Qt.AlignmentFlag.AlignHCenter:
            x = rect.x() + (rect.width() - layout.width) / 2
        elif alignment & Qt.AlignmentFlag.AlignRight:
            x = rect.right() - layout.width + 1
        else:
            x = rect.x()
        if alignment & Qt.AlignmentFlag.AlignTop:
            y = rect.y()
        elif alignment & Qt.AlignmentFlag.AlignBottom:
            y = rect.bottom() - layout.height + 1
        else:
            y = rect.y() + (rect.height() - layout.height) / 2

        if self._digital_color is not None:
            color = self._digital_color
        else:
            group = (
                QPalette.ColorGroup.Normal
                if self.isEnabled()
                else QPalette.ColorGroup.Disabled
            )
            color = self.palette().color(group, QPalette.ColorRole.WindowText)
        draw_qpainter(
            painter,
            self.text(),
            x,
            y,
            scale=self._scale,
            color=color,
        )
