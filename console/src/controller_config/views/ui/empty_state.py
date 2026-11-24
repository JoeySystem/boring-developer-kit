"""What a list or card shows while it has nothing yet: one sentence, maybe one button.

No final design yet; it gathers today's empty states (审查报告 4.2 空状态, 11
places): a grey wrapped sentence at the top left of the card (e.g.
views/actions.py:337 `actionEmptyState`, the 41 `muted` labels), 13 px. The
optional button is a small outline Button under it. A dot-matrix picture can
go above the sentence later without changing callers.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.views.ui.button import Button

GAP = 12


class EmptyState(QWidget):
    """EmptyState(text, action_text=""); connect empty.action.clicked when there is one."""

    def __init__(self, text: str, *, action_text: str = "", object_name: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(GAP)
        self.label = QLabel(objectName="emptyStateText")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        set_translatable_text(self.label, text)
        box.addWidget(self.label)
        self.action: Button | None = None
        if action_text:
            self.action = Button(action_text, "secondary", size="small")
            box.addWidget(self.action, 0, Qt.AlignmentFlag.AlignLeft)

    def set_text(self, text: str) -> None:
        set_translatable_text(self.label, text)
