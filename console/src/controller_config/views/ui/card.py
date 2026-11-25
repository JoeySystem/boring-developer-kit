"""Card and its header row.

Card is the existing V4Card (views/v4_widgets.py: four roles, optional dot
texture, painted surface) with the padding most cards use today:
24, 20, 24, 22 and 10 between blocks (17 cards, e.g. views/actions.py:336;
审查报告 3.5). Add content with card.layout().addWidget(...).

CardHeader is the header row of the settings wall final (`WallTile.header`,
views/settings_wall.py; also the lighting page v0.4 final): optional icon,
title, then the one action on the right. Title 15 semibold like the wall's
tile titles, in the window's text colour.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.views.v4_widgets import V4Card

PADDING = (24, 20, 24, 22)
SPACING = 10
HEADER_SPACING = 9


class CardHeader(QWidget):
    """CardHeader(title, icon=None, action=None): [icon] title ........ [action]."""

    def __init__(self, title: str, *, icon: QWidget | None = None, action: QWidget | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardHeader")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(HEADER_SPACING)
        if icon is not None:
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title_label = QLabel(objectName="cardTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        set_translatable_text(self.title_label, title)
        row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.action = action
        if action is not None:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)


class Card(V4Card):
    """Card(title="", role="secondary", dots=False, icon=None, action=None).

    role is V4Card's: primary, secondary, widget or focus. With a title the
    card starts with a CardHeader (card.header); without one it is a plain card.
    """

    def __init__(self, title: str = "", *, role: str = "secondary", dots: bool = False,
                 icon: QWidget | None = None, action: QWidget | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent, role=role, dots=dots)
        box = QVBoxLayout(self)
        box.setContentsMargins(*PADDING)
        box.setSpacing(SPACING)
        self.header: CardHeader | None = None
        if title:
            self.header = CardHeader(title, icon=icon, action=action)
            box.addWidget(self.header)
            self.setAccessibleName(self.header.title_label.text())
        elif icon is not None or action is not None:
            raise ValueError("A card header needs a title")
