"""One setting: title (and an optional note) on the left, its control on the right.

No final design for the row itself yet; it gathers today's two ways: the
settings wall's `value_row` (views/settings_wall.py, name 13 regular in
cream, value 12 grey) and the `QFormLayout` rows of the lighting and screen
cards (views/preferences_editor.py `_display_group`). Title 13 regular in the
window's text colour, note 12 grey under it, 12 px to the control.

A control that wants width (a slider) takes the rest of the row; a compact
one (a toggle, a value) sits at the right end. The control gets the title
as its screen-reader name unless it already has one.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text, translate_ui_text

GAP = 12
NOTE_GAP = 2


class SettingRow(QWidget):
    """SettingRow(title, control=None, note="")."""

    def __init__(self, title: str, control: QWidget | None = None, *, note: str = "",
                 object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(GAP)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(NOTE_GAP)
        self.title_label = QLabel(objectName="settingRowTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        set_translatable_text(self.title_label, title)
        text.addWidget(self.title_label)
        self.note_label: QLabel | None = None
        if note:
            self.note_label = QLabel(objectName="settingRowNote")
            self.note_label.setTextFormat(Qt.TextFormat.PlainText)
            self.note_label.setWordWrap(True)
            set_translatable_text(self.note_label, note)
            text.addWidget(self.note_label)
        self.control = control
        wide = control is not None and bool(
            control.sizePolicy().horizontalPolicy().value & QSizePolicy.PolicyFlag.ExpandFlag.value)
        row.addLayout(text, 0 if wide else 1)
        if control is not None:
            row.addWidget(control, 1 if wide else 0, Qt.AlignmentFlag.AlignVCenter)
            if not control.accessibleName():
                control.setAccessibleName(translate_ui_text(title))
            self.title_label.setBuddy(control)
