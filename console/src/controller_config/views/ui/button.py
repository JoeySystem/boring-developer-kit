"""The one button the Console uses (the designer, dialog B direction v0.4 final).

Kinds (spec `弹窗统一样式-B方向-v0.4-合成版-20261118-图/4-合成版-规矩和数值.png`, row 按钮):
    primary    orange fill, white text; at most one per dialog or screen
    secondary  outline
    text       words only, no outline
    cream      cream fill; only the "取消" of a danger confirmation

There is no red "danger" kind on purpose: the final rule for dangerous actions
is "no orange; 取消 is the cream button in the primary spot; the dangerous
action is an outline (secondary) button" (row 危险操作). `confirm(danger=True)`
in dialog.py puts that pair together.

Sizes: "large" 40 high, radius 20, at least 88 wide, 18 side padding;
"small" 34 high, radius 17, for buttons inside a row (same row of the spec).
Width follows the text. Looks live in style/sheets.py (COMPONENT_TEMPLATE).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

from controller_config.i18n import set_translatable_text

KINDS = ("primary", "secondary", "text", "cream")
SIZES = ("large", "small")


class Button(QPushButton):
    """Button(text, kind="secondary", size="large").

    The visible text is also the screen-reader name and follows the language
    switch. Buttons never become the dialog's default by accident
    (autoDefault off); call setDefault(True) where Enter should press it.
    """

    def __init__(self, text: str = "", kind: str = "secondary", *, size: str = "large",
                 object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if kind not in KINDS:
            raise ValueError(f"Unknown button kind: {kind}")
        if size not in SIZES:
            raise ValueError(f"Unknown button size: {size}")
        self.setProperty("boringButton", kind)
        self.setProperty("boringButtonSize", size)
        if object_name:
            self.setObjectName(object_name)
        if text:
            set_translatable_text(self, text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
