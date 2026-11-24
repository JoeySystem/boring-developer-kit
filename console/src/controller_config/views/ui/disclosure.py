""""了解更多" that opens and closes a block of text in place.

No final design yet; it gathers the most common way today (about 35 places,
审查报告 4.2 折叠): a checkable words-only button above the hidden content,
same label open or closed (e.g. views/prompt_library_editor.py:555,
views/voice_guide.py:291). Its look is the words-only button of the dialog
final (Button kind "text"); open, the words turn from grey to cream.

The button only shows and hides the content; place both where they belong:

    more = Disclosure("了解更多", details)
    layout.addWidget(more)
    layout.addWidget(details)

Space or Enter toggles it; screen readers hear the label and whether it is open.
"""
from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QWidget

from controller_config.views.ui.button import Button


class Disclosure(Button):
    """Disclosure(label, content, expanded=False)."""

    def __init__(self, label: str, content: QWidget, *, expanded: bool = False,
                 object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(label, "text", object_name=object_name, parent=parent)
        self.content = content
        self.setCheckable(True)
        # A link-like line: as wide as its words, at the left of its column.
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.toggled.connect(content.setVisible)
        self.setChecked(expanded)
        content.setVisible(expanded)
