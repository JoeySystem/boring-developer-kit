"""The BORING orbital logo as a quiet watermark (the designer 11-19, design decision item 1).

Geometry is the 2026-09-15 frozen BOR / ING master logo
(`assets/boring-orbital-master.svg`, copied unchanged); only the colour is
swapped for `tokens.ORBITAL_MARK`, the icon-well colour, so it reads like an
emboss. It draws at most `ORBITAL_MARK_HEIGHT` tall, keeps its proportions,
and sits in the bottom-right corner of whatever room it gets.
"""
from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QWidget

from controller_config.style import tokens

ASSET = "boring-orbital-master.svg"
SOURCE_FILL = re.compile(r'fill="[^"]*"')  # the master file paints every letter one colour


@lru_cache(maxsize=4)
def _svg(colour: str) -> bytes:
    resource = files("controller_config.assets").joinpath(ASSET)
    if not resource.is_file():
        # Community build: the brand logo is not included, the watermark stays empty.
        return b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="1"/>'
    source = resource.read_text(encoding="utf-8")
    return SOURCE_FILL.sub(f'fill="{colour}"', source).encode()


class OrbitalMark(QWidget):
    """`OrbitalMark()`; give it room and it fills the bottom-right of it."""

    def __init__(self, parent: QWidget | None = None, *, colour: str = tokens.ORBITAL_MARK) -> None:
        super().__init__(parent)
        self.setObjectName("orbitalMark")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMaximumHeight(tokens.ORBITAL_MARK_HEIGHT)
        self._renderer = QSvgRenderer(QByteArray(_svg(colour)), self)

    def aspect(self) -> float:
        size = self._renderer.defaultSize()
        return size.width() / size.height() if size.height() else 1.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        height = tokens.ORBITAL_MARK_HEIGHT
        return QSize(round(height * self.aspect()), height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(0, 0)

    def drawn_rect(self) -> QRectF:
        """Where the logo lands: as large as fits, at most the set height, bottom-right."""
        aspect = self.aspect()
        height = min(float(self.height()), self.width() / aspect, float(tokens.ORBITAL_MARK_HEIGHT))
        width = height * aspect
        return QRectF(self.width() - width, self.height() - height, width, height)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        rect = self.drawn_rect()
        if rect.height() < 8:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._renderer.render(painter, rect)
