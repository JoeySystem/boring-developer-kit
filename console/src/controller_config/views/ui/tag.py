"""A small capsule tag for a state or a kind ("测试", "PROMPT").

No final design yet; it gathers the only tag drawn today, the settings wall's
"测试" tag (views/settings_wall.py WALL_STYLE `wallTag`): 11 px medium grey
text, 1 px outline, radius 6, 6 px side padding. One look only; tones
(beta / warn …) wait for the designer.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from controller_config.i18n import set_translatable_text


class Tag(QLabel):
    """Tag(text). Sized to its text; findChildren(Tag) finds it."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("boringTag")  # the stylesheet matches this name
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        set_translatable_text(self, text)
