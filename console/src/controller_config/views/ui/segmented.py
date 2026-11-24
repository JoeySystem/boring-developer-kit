"""Pick one of a few: a dark track with equal-width options, the chosen one cream.

No final design for it yet; it gathers today's screen-rotation control
(views/preferences_editor.py `_display_group`, before: appearance.py
`displayRotationControl` / `displayRotationOption`), which the lighting page
v0.4 final draws the same way (`灯光页-排法二-v0.4-20261118-图/2-屏幕和震动方块点开以后.png`,
画面旋转 0°/90°/180°/270° and 震动强度 标准/强). Track: control fill, 1 px
outline, radius 18, 4 px inset, 2 px between options; option 30 high, radius 14.

Keyboard: Tab reaches the chosen option, the arrow keys move the choice
(Qt does this for buttons in an exclusive group). Screen readers hear each
option's text and its checked state.
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QWidget

from controller_config.i18n import set_translatable_text

INSET = 3  # + the 1 px outline = options 4 px from the track's edge
OPTION_GAP = 2


class SegmentedControl(QFrame):
    """SegmentedControl([(value, label), ...], value=None).

    value() / setValue() / valueChanged(value) work with the option values,
    not indices. Without a value the first option is chosen.
    """

    valueChanged = Signal(object)

    def __init__(self, options: Sequence[tuple[Hashable, str]], value: Hashable | None = None, *,
                 object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not options:
            raise ValueError("A segmented control needs at least one option")
        self.setProperty("segmented", True)
        if object_name:
            self.setObjectName(object_name)
        row = QHBoxLayout(self)
        row.setContentsMargins(INSET, INSET, INSET, INSET)
        row.setSpacing(OPTION_GAP)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._values: list[Hashable] = []
        for option_value, label in options:
            button = QPushButton()
            button.setProperty("segmentOption", True)
            set_translatable_text(button, label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)
            self._group.addButton(button, len(self._values))
            self._values.append(option_value)
            row.addWidget(button, 1)
        self.setValue(value if value in self._values else self._values[0])
        self._group.idToggled.connect(self._on_toggled)

    def buttons(self) -> list[QPushButton]:
        """The option buttons, in order (for tests and screen-reader names)."""
        return [self._group.button(index) for index in range(len(self._values))]

    def value(self) -> Hashable:
        return self._values[self._group.checkedId()]

    def setValue(self, value: Hashable) -> None:  # noqa: N802 - matches Qt's naming
        """Choose the option with this value; an unknown value raises ValueError."""
        self._group.button(self._values.index(value)).setChecked(True)

    def _on_toggled(self, index: int, checked: bool) -> None:
        if checked:
            self.valueChanged.emit(self._values[index])
