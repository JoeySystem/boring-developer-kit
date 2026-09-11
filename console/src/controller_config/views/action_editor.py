from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Signal, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller_config.actions import (
    ActionDefinition,
    ActionField,
    action_field_choices,
    action_field_label,
    describe_action,
    modifier_choice_label,
)
from controller_config.i18n import (
    SKIP_TRANSLATION_PROPERTY,
    set_translatable_accessible_name,
    set_translatable_text,
    translate_ui_text,
)


_MODIFIER_KEY_USAGES = {
    int(Qt.Key.Key_Control): 224,
    int(Qt.Key.Key_Shift): 225,
    int(Qt.Key.Key_Alt): 226,
    int(Qt.Key.Key_Meta): 227,
}

_MACOS_NATIVE_MODIFIER_KEY_USAGES = {
    0x3B: 224,  # Left Control
    0x38: 225,  # Left Shift
    0x3A: 226,  # Left Option
    0x37: 227,  # Left Command
    0x3E: 228,  # Right Control
    0x3C: 229,  # Right Shift
    0x3D: 230,  # Right Option
    0x36: 231,  # Right Command
}

_SPECIAL_KEY_USAGES = {
    int(Qt.Key.Key_Return): 40,
    int(Qt.Key.Key_Enter): 88,
    int(Qt.Key.Key_Escape): 41,
    int(Qt.Key.Key_Backspace): 42,
    int(Qt.Key.Key_Tab): 43,
    int(Qt.Key.Key_Backtab): 43,
    int(Qt.Key.Key_Space): 44,
    int(Qt.Key.Key_Minus): 45,
    int(Qt.Key.Key_Equal): 46,
    int(Qt.Key.Key_BracketLeft): 47,
    int(Qt.Key.Key_BracketRight): 48,
    int(Qt.Key.Key_Backslash): 49,
    int(Qt.Key.Key_Semicolon): 51,
    int(Qt.Key.Key_Apostrophe): 52,
    int(Qt.Key.Key_QuoteLeft): 53,
    int(Qt.Key.Key_Comma): 54,
    int(Qt.Key.Key_Period): 55,
    int(Qt.Key.Key_Slash): 56,
    int(Qt.Key.Key_CapsLock): 57,
    int(Qt.Key.Key_Print): 70,
    int(Qt.Key.Key_ScrollLock): 71,
    int(Qt.Key.Key_Pause): 72,
    int(Qt.Key.Key_Insert): 73,
    int(Qt.Key.Key_Home): 74,
    int(Qt.Key.Key_PageUp): 75,
    int(Qt.Key.Key_Delete): 76,
    int(Qt.Key.Key_End): 77,
    int(Qt.Key.Key_Right): 79,
    int(Qt.Key.Key_Left): 80,
    int(Qt.Key.Key_Down): 81,
    int(Qt.Key.Key_Up): 82,
    int(Qt.Key.Key_NumLock): 83,
}

_SHIFTED_KEY_USAGES = {
    int(Qt.Key.Key_Exclam): 30,
    int(Qt.Key.Key_At): 31,
    int(Qt.Key.Key_NumberSign): 32,
    int(Qt.Key.Key_Dollar): 33,
    int(Qt.Key.Key_Percent): 34,
    int(Qt.Key.Key_AsciiCircum): 35,
    int(Qt.Key.Key_Ampersand): 36,
    int(Qt.Key.Key_Asterisk): 37,
    int(Qt.Key.Key_ParenLeft): 38,
    int(Qt.Key.Key_ParenRight): 39,
    int(Qt.Key.Key_Underscore): 45,
    int(Qt.Key.Key_Plus): 46,
    int(Qt.Key.Key_BraceLeft): 47,
    int(Qt.Key.Key_BraceRight): 48,
    int(Qt.Key.Key_Bar): 49,
    int(Qt.Key.Key_Colon): 51,
    int(Qt.Key.Key_QuoteDbl): 52,
    int(Qt.Key.Key_AsciiTilde): 53,
    int(Qt.Key.Key_Less): 54,
    int(Qt.Key.Key_Greater): 55,
    int(Qt.Key.Key_Question): 56,
}


def _modifier_usages(
    modifiers: Qt.KeyboardModifier,
    *,
    platform: str,
) -> list[int]:
    usages: list[int] = []
    control_usage, meta_usage = (227, 224) if platform == "macos" else (224, 227)
    for modifier, usage in (
        (Qt.KeyboardModifier.ControlModifier, control_usage),
        (Qt.KeyboardModifier.ShiftModifier, 225),
        (Qt.KeyboardModifier.AltModifier, 226),
        (Qt.KeyboardModifier.MetaModifier, meta_usage),
    ):
        if modifiers & modifier:
            usages.append(usage)
    usages.sort()
    return usages


def _modifier_key_usage(
    key: int,
    *,
    platform: str,
    native_virtual_key: int = 0,
) -> int | None:
    if platform == "macos":
        native_usage = _MACOS_NATIVE_MODIFIER_KEY_USAGES.get(native_virtual_key)
        if native_usage is not None:
            return native_usage
        return {
            int(Qt.Key.Key_Control): 227,
            int(Qt.Key.Key_Meta): 224,
            int(Qt.Key.Key_Shift): 225,
            int(Qt.Key.Key_Alt): 226,
        }.get(key)
    return _MODIFIER_KEY_USAGES.get(key)


def _effective_modifier_usages(
    modifiers: Qt.KeyboardModifier,
    *,
    platform: str,
    pressed_usages: set[int],
    released_usage: int | None = None,
) -> list[int]:
    pressed_kinds = {(usage - 224) % 4 for usage in pressed_usages}
    released_kind = (
        (released_usage - 224) % 4 if released_usage is not None else None
    )
    usages = [
        usage
        for usage in _modifier_usages(modifiers, platform=platform)
        if (usage - 224) % 4 not in pressed_kinds
        and (usage - 224) % 4 != released_kind
    ]
    usages.extend(pressed_usages)
    return sorted(set(usages))


def _key_usage(
    key: int,
    modifiers: Qt.KeyboardModifier,
) -> tuple[int | None, bool]:
    """Translate a Qt key event into the keyboard usage stored by the device."""
    if key in _MODIFIER_KEY_USAGES:
        return _MODIFIER_KEY_USAGES[key], False

    keypad = bool(modifiers & Qt.KeyboardModifier.KeypadModifier)
    if keypad:
        if int(Qt.Key.Key_1) <= key <= int(Qt.Key.Key_9):
            return 89 + key - int(Qt.Key.Key_1), False
        if key == int(Qt.Key.Key_0):
            return 98, False
        keypad_usages = {
            int(Qt.Key.Key_Slash): 84,
            int(Qt.Key.Key_Asterisk): 85,
            int(Qt.Key.Key_Minus): 86,
            int(Qt.Key.Key_Plus): 87,
            int(Qt.Key.Key_Enter): 88,
            int(Qt.Key.Key_Period): 99,
        }
        if key in keypad_usages:
            return keypad_usages[key], False

    if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
        return 4 + key - int(Qt.Key.Key_A), False
    if int(Qt.Key.Key_1) <= key <= int(Qt.Key.Key_9):
        return 30 + key - int(Qt.Key.Key_1), False
    if key == int(Qt.Key.Key_0):
        return 39, False
    if int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F12):
        return 58 + key - int(Qt.Key.Key_F1), False
    if int(Qt.Key.Key_F13) <= key <= int(Qt.Key.Key_F24):
        return 104 + key - int(Qt.Key.Key_F13), False
    if key in _SHIFTED_KEY_USAGES:
        return _SHIFTED_KEY_USAGES[key], True
    return _SPECIAL_KEY_USAGES.get(key), key == int(Qt.Key.Key_Backtab)


class _ShortcutCaptureButton(QPushButton):
    shortcut_recorded = Signal(dict)
    capture_message = Signal(str)
    recording_changed = Signal(bool)

    def __init__(
        self,
        *,
        platform: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._recording = False
        self._platform = platform
        self._pressed_modifier_usages: set[int] = set()
        self._unsupported_key_pending = False
        self.setObjectName("shortcutRecordButton")
        self.setProperty("buttonRole", "primary")
        self.clicked.connect(self._toggle_recording)
        set_translatable_text(self, "录制新快捷键")
        set_translatable_accessible_name(self, "录制键盘快捷键")

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._pressed_modifier_usages.clear()
        self._unsupported_key_pending = False
        self.setProperty("recording", True)
        self.style().unpolish(self)
        self.style().polish(self)
        set_translatable_text(self, "取消录制")
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.capture_message.emit("正在录制，请按下一个按键或快捷键组合。")
        self.recording_changed.emit(True)

    def cancel_recording(self) -> None:
        if not self._recording:
            return
        self._finish_recording()
        self.capture_message.emit("点击录制后，直接在电脑键盘上按下目标按键。")

    def event(self, event: QEvent) -> bool:
        if self._recording and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._recording:
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return
        if self._unsupported_key_pending:
            self.capture_message.emit("请先松开当前按键，再重新输入快捷键。")
            return
        modifier_usage = _modifier_key_usage(
            event.key(),
            platform=self._platform,
            native_virtual_key=event.nativeVirtualKey(),
        )
        if modifier_usage is not None:
            self._pressed_modifier_usages.add(modifier_usage)
            self.capture_message.emit(
                "继续按下主按键；只映射修饰键时，直接松开即可。"
            )
            return
        usage, implied_shift = _key_usage(event.key(), event.modifiers())
        if usage is None:
            self._unsupported_key_pending = True
            self.capture_message.emit(
                "这个按键暂不支持直接录制，请使用手动设置。"
            )
            return
        modifiers = _effective_modifier_usages(
            event.modifiers(),
            platform=self._platform,
            pressed_usages=self._pressed_modifier_usages,
        )
        if implied_shift and not any(
            (modifier - 224) % 4 == 1 for modifier in modifiers
        ):
            modifiers.append(225)
            modifiers.sort()
        action: dict[str, Any] = {"type": "key", "usage": usage}
        if modifiers:
            action["modifiers"] = modifiers
        self._finish_recording()
        self.shortcut_recorded.emit(action)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._recording:
            super().keyReleaseEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return
        usage = _modifier_key_usage(
            event.key(),
            platform=self._platform,
            native_virtual_key=event.nativeVirtualKey(),
        )
        if usage is None:
            if (
                self._unsupported_key_pending
                and not self._pressed_modifier_usages
                and not _modifier_usages(event.modifiers(), platform=self._platform)
            ):
                self._unsupported_key_pending = False
                self.capture_message.emit(
                    "正在录制，请按下一个按键或快捷键组合。"
                )
            return
        self._pressed_modifier_usages.discard(usage)
        modifiers = _effective_modifier_usages(
            event.modifiers(),
            platform=self._platform,
            pressed_usages=self._pressed_modifier_usages,
            released_usage=usage,
        )
        if self._unsupported_key_pending:
            if not modifiers:
                self._unsupported_key_pending = False
                self.capture_message.emit(
                    "正在录制，请按下一个按键或快捷键组合。"
                )
            return
        action: dict[str, Any] = {"type": "key", "usage": usage}
        if modifiers:
            action["modifiers"] = modifiers
        self._finish_recording()
        self.shortcut_recorded.emit(action)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        if self._recording:
            self.cancel_recording()
        super().focusOutEvent(event)

    def _toggle_recording(self) -> None:
        if self._recording:
            self.cancel_recording()
        else:
            self.start_recording()

    def _finish_recording(self) -> None:
        self._recording = False
        self._pressed_modifier_usages.clear()
        self._unsupported_key_pending = False
        self.setProperty("recording", False)
        self.style().unpolish(self)
        self.style().polish(self)
        set_translatable_text(self, "重新录制")
        self.recording_changed.emit(False)


class ShortcutRecorder(QFrame):
    shortcut_recorded = Signal(dict)

    def __init__(
        self,
        action: dict[str, Any],
        *,
        platform: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("shortcutRecorder")
        self.setMinimumHeight(150)
        self._platform = platform
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(7)

        layout.addWidget(QLabel("直接输入快捷键", objectName="shortcutRecorderHeading"))
        row = QVBoxLayout()
        row.setSpacing(8)
        self._preview = QLabel(objectName="shortcutRecorderPreview")
        self._preview.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._preview.setMinimumWidth(0)
        self._preview.setWordWrap(True)
        self._preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self._preview)
        self._capture = _ShortcutCaptureButton(platform=platform)
        self._capture.shortcut_recorded.connect(self._recorded)
        row.addWidget(self._capture, 0, Qt.AlignRight)
        layout.addLayout(row)
        self._message = QLabel(objectName="shortcutRecorderMessage")
        self._message.setWordWrap(True)
        set_translatable_text(
            self._message,
            "点击录制后，直接在电脑键盘上按下目标按键。",
        )
        self._capture.capture_message.connect(
            lambda message: set_translatable_text(self._message, message)
        )
        layout.addWidget(self._message)
        self.set_action(action)

    @property
    def is_recording(self) -> bool:
        return self._capture.is_recording

    def start_recording(self) -> None:
        self._capture.start_recording()

    def set_action(self, action: dict[str, Any]) -> None:
        if action.get("type") == "key":
            self._preview.setText(
                describe_action(action, platform=self._platform, compact=True)
            )
            self._preview.setAccessibleName(
                f"{translate_ui_text('准备修改为')} "
                f"{describe_action(action, platform=self._platform)}"
            )
        else:
            self._preview.setText("—")
            set_translatable_accessible_name(self._preview, "尚未录制键盘快捷键")

    def _recorded(self, action: dict[str, Any]) -> None:
        self.set_action(action)
        set_translatable_text(self._message, "已识别，可继续修改或应用到设备。")
        self.shortcut_recorded.emit(action)


class ModifierSelector(QWidget):
    def __init__(
        self,
        values: object,
        platform: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        selected = set(values) if isinstance(values, list) else set()
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(5)
        self._checkboxes: list[QCheckBox] = []
        for offset, usage in enumerate(range(224, 232)):
            checkbox = QCheckBox(modifier_choice_label(usage, platform))
            checkbox.setProperty("usage", usage)
            checkbox.setChecked(usage in selected)
            layout.addWidget(checkbox, offset % 4, offset // 4)
            self._checkboxes.append(checkbox)

    def values(self) -> list[int]:
        return [
            int(checkbox.property("usage"))
            for checkbox in self._checkboxes
            if checkbox.isChecked()
        ]


class ActionEditor(QWidget):
    shortcut_recorded = Signal(dict)

    def __init__(
        self,
        definitions: tuple[ActionDefinition, ...],
        action: dict[str, Any],
        parent: QWidget | None = None,
        *,
        platform: str = "",
        reference_choices: dict[
            tuple[str, str], tuple[tuple[str, Any], ...]
        ] | None = None,
    ) -> None:
        super().__init__(parent)
        if not definitions:
            raise ValueError("设备没有可编辑的动作类型")
        self._definitions = {definition.action_type: definition for definition in definitions}
        self._field_widgets: dict[str, QWidget] = {}
        self._platform = platform
        self._reference_choices = reference_choices or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        self._shortcut_recorder: ShortcutRecorder | None = None
        if "key" in self._definitions:
            self._shortcut_recorder = ShortcutRecorder(
                action,
                platform=platform,
            )
            self._shortcut_recorder.shortcut_recorded.connect(
                self._shortcut_was_recorded
            )
            layout.addWidget(self._shortcut_recorder)

        self._manual_toggle = QPushButton("手动设置或选择其他动作")
        self._manual_toggle.setObjectName("shortcutManualToggle")
        self._manual_toggle.setProperty("buttonRole", "secondary")
        self._manual_toggle.setCheckable(True)
        layout.addWidget(self._manual_toggle)

        self._manual_panel = QWidget(objectName="manualActionEditor")
        manual_layout = QVBoxLayout(self._manual_panel)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(9)
        self._type_selector = QComboBox(objectName="actionTypeEditor")
        for definition in definitions:
            self._type_selector.addItem(definition.label, definition.action_type)
        manual_layout.addWidget(self._type_selector)
        self._fields = QFormLayout()
        self._fields.setContentsMargins(0, 0, 0, 0)
        manual_layout.addLayout(self._fields)
        layout.addWidget(self._manual_panel)
        self._manual_toggle.toggled.connect(self._manual_panel.setVisible)
        self._type_selector.currentIndexChanged.connect(self._type_changed)
        self.set_action(action)
        self._manual_toggle.setChecked(action.get("type") not in {"key", "none"})
        self._manual_panel.setVisible(self._manual_toggle.isChecked())

    def set_action(self, action: dict[str, Any]) -> None:
        action_type = action.get("type")
        index = self._type_selector.findData(action_type)
        if action_type is None:
            index = 0
            action = self._definitions[self._type_selector.itemData(index)].default_action()
        elif index < 0:
            raise ValueError(f"当前动作 {action_type} 不在设备能力中")
        self._type_selector.blockSignals(True)
        self._type_selector.setCurrentIndex(index)
        self._type_selector.blockSignals(False)
        self._rebuild_fields(action)
        if self._shortcut_recorder is not None:
            self._shortcut_recorder.set_action(action)

    def action(self) -> dict[str, Any]:
        action_type = self._type_selector.currentData()
        definition = self._definitions[str(action_type)]
        result: dict[str, Any] = {"type": definition.action_type}
        for field in definition.fields:
            value = self._read_field(field, self._field_widgets[field.name])
            if field.required or value not in (None, [], ""):
                result[field.name] = value
        return result

    def _type_changed(self) -> None:
        action_type = str(self._type_selector.currentData())
        action = self._definitions[action_type].default_action()
        self._rebuild_fields(action)
        if self._shortcut_recorder is not None:
            self._shortcut_recorder.set_action(action)

    def _shortcut_was_recorded(self, action: dict[str, Any]) -> None:
        self.set_action(action)
        self._manual_toggle.setChecked(False)
        self.shortcut_recorded.emit(action)

    def _rebuild_fields(self, action: dict[str, Any]) -> None:
        while self._fields.rowCount():
            self._fields.removeRow(0)
        self._field_widgets.clear()
        definition = self._definitions[str(self._type_selector.currentData())]
        for field in definition.fields:
            widget = self._field_widget(
                definition.action_type,
                field,
                action.get(field.name, field.default_value()),
            )
            widget.setObjectName(f"actionField_{field.name}")
            self._field_widgets[field.name] = widget
            self._fields.addRow(action_field_label(definition.action_type, field.name), widget)

    def _field_widget(self, action_type: str, field: ActionField, value: Any) -> QWidget:
        if action_type == "key" and field.name == "modifiers":
            return ModifierSelector(value, self._platform)
        reference_choices = self._reference_choices.get((action_type, field.name))
        if reference_choices is not None:
            selector = QComboBox()
            selector.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            for label, choice in reference_choices:
                selector.addItem(label, choice)
            index = selector.findData(value)
            selector.setCurrentIndex(max(0, index))
            return selector
        choices = action_field_choices(
            action_type,
            field,
            value,
            platform=self._platform,
        )
        if choices:
            selector = QComboBox()
            selector.setMaxVisibleItems(18)
            for label, choice in choices:
                selector.addItem(label, choice)
            index = selector.findData(value)
            selector.setCurrentIndex(max(0, index))
            return selector
        if field.value_type == "array":
            values = value if isinstance(value, list) else []
            editor = QLineEdit(", ".join(str(item) for item in values))
            if field.minimum is not None and field.maximum is not None:
                editor.setPlaceholderText(f"逗号分隔，可选范围 {field.minimum}–{field.maximum}")
            return editor
        editor = QSpinBox()
        minimum = field.minimum if field.minimum is not None else -2_147_483_648
        maximum = field.maximum if field.maximum is not None else 2_147_483_647
        editor.setRange(minimum, maximum)
        editor.setValue(value if isinstance(value, int) and not isinstance(value, bool) else minimum)
        return editor

    @staticmethod
    def _read_field(field: ActionField, widget: QWidget) -> Any:
        if isinstance(widget, ModifierSelector):
            return widget.values()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QSpinBox):
            return widget.value()
        if not isinstance(widget, QLineEdit):
            raise ValueError(f"{field.label} 编辑器不可用")
        text = widget.text().strip()
        if not text:
            return []
        try:
            values = [int(part.strip()) for part in text.split(",")]
        except ValueError as exc:
            raise ValueError(f"{field.label} 必须使用逗号分隔的整数") from exc
        if field.unique and len(values) != len(set(values)):
            raise ValueError(f"{field.label} 不能包含重复值")
        if field.max_items is not None and len(values) > field.max_items:
            raise ValueError(f"{field.label} 最多允许 {field.max_items} 项")
        if field.minimum is not None and field.maximum is not None:
            if any(value < field.minimum or value > field.maximum for value in values):
                raise ValueError(f"{field.label} 必须在 {field.minimum}–{field.maximum} 范围内")
        return values
