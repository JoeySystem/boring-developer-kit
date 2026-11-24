from __future__ import annotations

import copy
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
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller_config.voice_presets import DEFAULT_VOICE_ACTION, DEFAULT_VOICE_USAGE
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
from controller_config.profile_templates import agent_profile_mappings
from controller_config.views.voice_guide import VoiceDemo


_MODIFIER_KEY_USAGES = {
    int(Qt.Key.Key_Control): 224,
    int(Qt.Key.Key_Shift): 225,
    int(Qt.Key.Key_Alt): 226,
    int(Qt.Key.Key_Meta): 227,
}

_FIRST_MODIFIER_USAGE = 224  # Left Control
_LAST_MODIFIER_USAGE = 231   # Right GUI (Command / Win)

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
                "这个按键暂不支持录制，请换一个按键或组合键。"
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
        capture_platform: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("shortcutRecorder")
        self.setMinimumHeight(82)
        self._platform = platform
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 11, 12, 11)
        layout.setSpacing(7)

        heading = QLabel("快捷键", objectName="shortcutRecorderHeading")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._preview = QLabel(objectName="shortcutRecorderPreview")
        self._preview.setMinimumWidth(0)
        self._preview.setWordWrap(True)
        self._preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self._preview, 1)
        self._capture = _ShortcutCaptureButton(platform=capture_platform or platform)
        self._capture.shortcut_recorded.connect(self._recorded)
        row.addWidget(self._capture, 0, Qt.AlignRight)
        layout.addLayout(row)
        self._message = QLabel(objectName="shortcutRecorderMessage")
        self._message.setWordWrap(True)
        set_translatable_text(
            self._message,
            "点击录制后，直接在电脑键盘上按下目标按键。",
        )
        self._message.hide()
        self._capture.capture_message.connect(self._set_capture_message)
        layout.addWidget(self._message)
        self.set_action(action)

    describe_all = False   # True: a non-key action is written out instead of "—"
    # True where the expected answer is one modifier on its own (the voice key
    # must match the voice app, e.g. right Option), so a lone modifier is not
    # treated as a possibly swallowed combination.
    voice_key = False

    def _set_capture_message(self, message: str) -> None:
        set_translatable_text(self._message, message)
        self._message.setVisible(
            self._capture.is_recording
            or message != "点击录制后，直接在电脑键盘上按下目标按键。"
        )

    @property
    def is_recording(self) -> bool:
        return self._capture.is_recording

    def start_recording(self) -> None:
        self._capture.start_recording()

    def set_action(self, action: dict[str, Any]) -> None:
        if action.get("type") == "key":
            set_translatable_text(self._preview,
                                  describe_action(action, platform=self._platform, compact=True))
            set_translatable_accessible_name(
                self._preview, f"准备修改为 {describe_action(action, platform=self._platform)}")
        elif self.describe_all and action.get("type") not in {None, "none"}:
            # The Mode card writes out any kind here (现在的动作, Mode页 定稿 v1, 11).
            set_translatable_text(self._preview, describe_action(action, platform=self._platform, compact=True))
            set_translatable_accessible_name(self._preview, describe_action(action, platform=self._platform))
        else:
            set_translatable_text(self._preview, "—")
            set_translatable_accessible_name(self._preview, "尚未录制键盘快捷键")

    def _recorded(self, action: dict[str, Any]) -> None:
        self.set_action(action)
        set_translatable_text(self._message, self._recorded_message(action))
        self._message.show()
        self.shortcut_recorded.emit(action)

    def _recorded_message(self, action: dict[str, Any]) -> str:
        # Only a modifier release ends a recording on a modifier. When another
        # app (Doubao, Raycast, an input method) owns e.g. Option+Space, macOS
        # hands it the Space, so the user releases and gets just the Option.
        if not _FIRST_MODIFIER_USAGE <= action.get("usage", 0) <= _LAST_MODIFIER_USAGE:
            return "已识别，可继续修改或应用到设备。"
        keys = describe_action(action, platform=self._platform)
        if self.voice_key:
            return (f"已录到 {keys}。语音键单独用这个键就行；要是还按了别的键，"
                    "可能被语音软件（比如豆包）先收走了，先退出它再录。")
        return f"只录到了 {keys}。另一个键可能被别的应用（比如豆包、输入法）占用了，先退出它再录。"


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
    action_changed = Signal()
    voice_preset_selected = Signal()
    voice_demo_pressed = Signal()
    voice_setup_requested = Signal()

    def __init__(
        self,
        definitions: tuple[ActionDefinition, ...],
        action: dict[str, Any],
        parent: QWidget | None = None,
        *,
        platform: str = "",
        capture_platform: str | None = None,
        show_manual_controls: bool = True,
        allow_voice_preset: bool = False,
        codex_voice: bool = False,
        reference_choices: dict[
            tuple[str, str], tuple[tuple[str, Any], ...]
        ] | None = None,
    ) -> None:
        super().__init__(parent)
        if not definitions:
            raise ValueError("设备没有可编辑的动作类型")
        self._codex_voice = codex_voice
        self._voice_action_types = {"key_gesture"} | ({"key"} if codex_voice else set())
        self._codex_voice_action = copy.deepcopy(action) if action.get("type") in self._voice_action_types else copy.deepcopy(DEFAULT_VOICE_ACTION)
        self._definitions = {definition.action_type: definition for definition in definitions}
        self._field_widgets: dict[str, QWidget] = {}
        self._unsupported_action: dict | None = None
        self._bound_host_action: dict | None = None
        self._platform = platform
        self._reference_choices = reference_choices or {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._purpose: QComboBox | None = None
        self._voice_panel: QWidget | None = None
        self._voice_firmware_notice: QLabel | None = None
        self._native_voice_note: QLabel | None = None
        self._common_shortcuts: QPushButton | None = None
        self._ordinary_action = copy.deepcopy(action) if action.get("type") not in self._voice_action_types else {"type": "none"}
        shortcut_purposes = not codex_voice and "key" in self._definitions and len(self._definitions) > 1
        if allow_voice_preset or shortcut_purposes:
            self._purpose = QComboBox(objectName="mappingPurpose")
            self._purpose.setAccessibleName("按键用途")
            self._purpose.addItem("Codex 自带语音" if codex_voice else "快捷键", "shortcut")
            if allow_voice_preset:
                self._purpose.addItem("第三方语音输入软件" if codex_voice else "语音输入", "voice")
            if shortcut_purposes:
                self._purpose.addItem("AI 软件自带语音", "native")
            layout.addWidget(self._purpose)
            if allow_voice_preset and not self._voice_action_types.intersection(self._definitions):
                self._purpose.model().item(1).setEnabled(False)
                notice = "此设备固件尚不支持单击语音、双击发送。"
                self._purpose.setToolTip(notice)
                self._purpose.setItemData(1, notice, Qt.ToolTipRole)
                self._voice_firmware_notice = QLabel(notice, objectName="voiceFirmwareNotice")
                self._voice_firmware_notice.setWordWrap(True)
                layout.addWidget(self._voice_firmware_notice)

        if shortcut_purposes:
            self._native_voice_note = QLabel(
                "启用 AI 语音快捷键后录制。",
                objectName="nativeVoiceShortcutNotice",
            )
            self._native_voice_note.setWordWrap(True)
            self._native_voice_note.setStyleSheet("font-size: 12px;")
            self._native_voice_note.setToolTip("先在目标 AI 软件中确认并启用语音快捷键，再在这里录制。没有快捷键的语音入口暂不能绑定；保存后请到目标应用中试用。")
            layout.addWidget(self._native_voice_note)

        tools = QHBoxLayout()
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(7)
        self._shortcut_recorder: ShortcutRecorder | None = None
        if "key" in self._definitions:
            self._shortcut_recorder = ShortcutRecorder(
                action,
                platform=platform,
                capture_platform=capture_platform,
            )
            self._shortcut_recorder.shortcut_recorded.connect(
                self._shortcut_was_recorded
            )
            layout.addWidget(self._shortcut_recorder)

            preset_platform = "darwin" if platform in {"macos", "darwin"} else "win32" if platform.startswith("win") else None
            if shortcut_purposes and preset_platform is not None:
                self._common_shortcuts = QPushButton("常用操作…", objectName="commonShortcutMenu")
                self._common_shortcuts.setProperty("buttonRole", "secondary")
                self._common_shortcuts.setStyleSheet("QPushButton { padding-left: 8px; padding-right: 8px; font-size: 12px; }")
                menu = QMenu(self._common_shortcuts)
                mappings = {mapping["control_id"]: mapping for mapping in
                            agent_profile_mappings("basic", platform=preset_platform)}
                for key, control in (("copy", "key.11"), ("paste", "key.12"),
                                     ("undo", "key.9"), ("redo", "key.10")):
                    mapping = mappings[control]
                    preset = menu.addAction(mapping["short_name"])
                    preset.setObjectName(f"commonShortcut_{key}")
                    preset.triggered.connect(
                        lambda _checked=False, action=mapping["action"]: self._shortcut_was_recorded(action)
                    )
                self._common_shortcuts.setMenu(menu)
                tools.addWidget(self._common_shortcuts, 1)

        self._manual_toggle: QPushButton | None = None
        if show_manual_controls or self._shortcut_recorder is None:
            self._manual_toggle = QPushButton("其他动作…" if self._common_shortcuts is not None else "手动设置或选择其他动作")
            self._manual_toggle.setObjectName("shortcutManualToggle")
            set_translatable_accessible_name(self._manual_toggle, "手动设置或选择其他动作")
            self._manual_toggle.setToolTip("手动设置或选择其他动作")
            self._manual_toggle.setProperty("buttonRole", "secondary")
            if self._common_shortcuts is not None:
                self._manual_toggle.setStyleSheet("QPushButton { padding-left: 8px; padding-right: 8px; font-size: 12px; }")
            self._manual_toggle.setCheckable(True)
            tools.addWidget(self._manual_toggle, 1)
        if tools.count():
            layout.addLayout(tools)

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
        if self._manual_toggle is not None:
            self._manual_toggle.toggled.connect(self._manual_panel.setVisible)
        self._type_selector.currentIndexChanged.connect(self._type_changed)
        if allow_voice_preset and "key_gesture" in self._definitions:
            self._voice_panel = QWidget(objectName="voiceInputSetup")
            voice_layout = QVBoxLayout(self._voice_panel)
            voice_layout.setContentsMargins(0, 0, 0, 0)
            voice_layout.setSpacing(9)
            daily = QLabel("单击说话／结束\n文字出现后，双击发送")
            daily.setWordWrap(True)
            voice_layout.addWidget(daily)
            status = QLabel(objectName="voiceSetupStatus")
            status.setWordWrap(True)
            voice_layout.addWidget(status)
            connect = QPushButton("设置语音输入", objectName="voiceConnectApp")
            connect.setProperty("buttonRole", "primary")
            connect.clicked.connect(self.voice_setup_requested)
            voice_layout.addWidget(connect)
            demo = VoiceDemo()
            demo.key_pressed.connect(self.voice_demo_pressed)
            demo_toggle = QPushButton("查看操作演示", objectName="voiceDemoToggle")
            demo_toggle.setProperty("buttonRole", "ghost")
            demo_toggle.setCheckable(True)
            demo_toggle.toggled.connect(demo.setVisible)
            voice_layout.addWidget(demo_toggle)
            demo.hide()
            voice_layout.addWidget(demo)
            setup = QPushButton("设置帮助", objectName="voiceSetupToggle")
            setup.setProperty("buttonRole", "ghost")
            setup.setCheckable(True)
            voice_layout.addWidget(setup)
            instructions = QWidget(objectName="voiceSetupInstructions")
            instructions_layout = QVBoxLayout(instructions)
            instructions_layout.setContentsMargins(0, 0, 0, 0)
            note = QLabel("语音软件需保持运行，并支持用快捷键切换开始／结束。")
            note.setWordWrap(True)
            instructions_layout.addWidget(note)
            setup.toggled.connect(instructions.setVisible)
            instructions.hide()
            voice_layout.addWidget(instructions)
            details = QPushButton("高级快捷键", objectName="voiceShortcutDetailsToggle")
            details.setProperty("buttonRole", "ghost")
            details.setCheckable(True)
            instructions_layout.addWidget(details)
            self._voice_details = QWidget(objectName="voiceShortcutDetails")
            details_layout = QVBoxLayout(self._voice_details)
            details_layout.setContentsMargins(0, 0, 0, 0)
            key_note = QLabel("这里修改设备发送的快捷键；需要与语音输入软件中的设置一致。")
            key_note.setWordWrap(True)
            details_layout.addWidget(key_note)
            details_layout.addWidget(QLabel("语音快捷键（与你的语音软件一致）"))
            self._voice_recorder = ShortcutRecorder(
                {"type": "key", "usage": DEFAULT_VOICE_USAGE}, platform=platform, capture_platform=capture_platform,
            )
            self._voice_recorder.voice_key = True
            details_layout.addWidget(self._voice_recorder)
            details_layout.addWidget(QLabel("发送快捷键（与目标应用一致）"))
            self._send_recorder = ShortcutRecorder(
                {"type": "key", "usage": 40}, platform=platform, capture_platform=capture_platform,
            )
            details_layout.addWidget(self._send_recorder)
            self._voice_recorder.shortcut_recorded.connect(lambda value: self._record_voice_shortcut(value, double=False))
            self._send_recorder.shortcut_recorded.connect(lambda value: self._record_voice_shortcut(value, double=True))
            details.toggled.connect(self._voice_details.setVisible)
            self._voice_details.hide()
            instructions_layout.addWidget(self._voice_details)
            layout.addWidget(self._voice_panel)
        if self._purpose is not None:
            self._purpose.currentIndexChanged.connect(self._purpose_changed)
        self.set_action(action)
        if self._manual_toggle is not None:
            self._manual_toggle.setChecked(action.get("type") not in {"key", "none", "key_gesture"})
            self._manual_panel.setVisible(self._manual_toggle.isChecked())
        else:
            # Preserve existing non-key actions in the draft/readback model
            # without exposing the old engineering form in the normal UI.
            self._manual_panel.hide()

    def set_action(self, action: dict[str, Any]) -> None:
        action_type = action.get("type")
        index = self._type_selector.findData(action_type)
        if action_type is None:
            index = 0
            action = self._definitions[self._type_selector.itemData(index)].default_action()
        elif index < 0:
            if action_type in {"key_gesture", "host_action"}:
                # A firmware rollback may leave an unapplied gesture draft.
                # Keep it intact until the user explicitly replaces it.
                self._unsupported_action = copy.deepcopy(action)
                self._type_selector.blockSignals(True)
                self._type_selector.setCurrentIndex(-1)
                self._type_selector.blockSignals(False)
                while self._fields.rowCount():
                    self._fields.removeRow(0)
                self._field_widgets.clear()
                self._sync_voice_ui(action)
                self.action_changed.emit()
                return
            raise ValueError(f"当前动作 {action_type} 不在设备能力中")
        self._unsupported_action = None
        if action_type == "host_action":
            self._bound_host_action = copy.deepcopy(action)
        self._type_selector.blockSignals(True)
        self._type_selector.setCurrentIndex(index)
        self._type_selector.blockSignals(False)
        self._rebuild_fields(action)
        if self._shortcut_recorder is not None:
            self._shortcut_recorder.set_action(action)
        self._sync_voice_ui(action)
        self.action_changed.emit()

    def set_purpose(self, value: str) -> bool:
        """Route a task to the existing editor without writing device state."""
        if self._purpose is None:
            return False
        if self._codex_voice and value == "native":
            value = "shortcut"
        index = self._purpose.findData(value)
        if index < 0 or not self._purpose.model().item(index).isEnabled():
            return False
        self._purpose.setCurrentIndex(index)
        return True

    def set_remembered_voice_action(self, action: dict) -> None:
        """Keep the confirmed custom choice while official voice stays active."""
        if action.get("type") in self._voice_action_types:
            self._codex_voice_action = copy.deepcopy(action)

    def _purpose_changed(self) -> None:
        if self._purpose.currentData() in {"native", "voice"} and self._manual_toggle is not None:
            self._manual_toggle.setChecked(False)
        if self._purpose.currentData() == "voice":
            self._ordinary_action = copy.deepcopy(self.action())
            self.set_action(self._codex_voice_action if self._codex_voice else
                            copy.deepcopy(DEFAULT_VOICE_ACTION))
            self.voice_preset_selected.emit()
        else:
            if self._codex_voice:
                self._codex_voice_action = copy.deepcopy(self.action())
                self.set_action({"type": "none"})
            elif self.action().get("type") in self._voice_action_types:
                self.set_action(self._ordinary_action)
            else:
                # Native AI shortcuts are app-defined. Selecting this purpose
                # explains recording, but does not replace the existing action.
                self._sync_voice_ui(self.action())
                self.action_changed.emit()

    def _record_voice_shortcut(self, shortcut: dict, *, double: bool) -> None:
        action = self.action()
        prefix = "double_" if double else ""
        action[prefix + "usage"] = shortcut["usage"]
        action[prefix + "modifiers"] = shortcut.get("modifiers", [])
        self.set_action(action)

    def _sync_voice_ui(self, action: dict) -> None:
        voice = action.get("type") in self._voice_action_types
        gesture = action.get("type") == "key_gesture"
        if self._purpose is not None:
            native = self._purpose.currentData() == "native" and not voice
            self._purpose.blockSignals(True)
            purpose = "voice" if voice else "native" if native else "shortcut"
            index = self._purpose.findData(purpose)
            self._purpose.setCurrentIndex(index if index >= 0 else 0)
            self._purpose.blockSignals(False)
        native = self._purpose is not None and self._purpose.currentData() == "native"
        if self._native_voice_note is not None:
            self._native_voice_note.setVisible(native)
        if self._voice_firmware_notice is not None:
            self._voice_firmware_notice.setVisible(voice)
        if self._common_shortcuts is not None:
            self._common_shortcuts.setVisible(not voice and not native)
        if self._voice_panel is not None:
            self._voice_panel.setVisible(gesture)
            if gesture:
                for recorder, prefix in ((self._voice_recorder, ""), (self._send_recorder, "double_")):
                    recorder.set_action({"type": "key", "usage": action[prefix + "usage"], "modifiers": action.get(prefix + "modifiers", [])})
        if self._shortcut_recorder is not None:
            heading = self._shortcut_recorder.findChild(QLabel, "shortcutRecorderHeading")
            set_translatable_text(heading, "AI 语音快捷键" if native else "快捷键")
            self._shortcut_recorder.setVisible(
                action.get("type") == "key" if self._codex_voice
                else not voice or self._voice_panel is None
            )

    def action(self) -> dict[str, Any]:
        if self._unsupported_action is not None:
            return copy.deepcopy(self._unsupported_action)
        action_type = self._type_selector.currentData()
        if action_type == "host_action" and self._bound_host_action is not None:
            return copy.deepcopy(self._bound_host_action)
        definition = self._definitions[str(action_type)]
        result: dict[str, Any] = {"type": definition.action_type}
        for field in definition.fields:
            value = self._read_field(field, self._field_widgets[field.name])
            if field.required or value not in (None, [], ""):
                result[field.name] = value
        return result

    def _type_changed(self) -> None:
        self._unsupported_action = None
        action_type = str(self._type_selector.currentData())
        action = (copy.deepcopy(self._bound_host_action)
                  if action_type == "host_action" and self._bound_host_action is not None
                  else self._definitions[action_type].default_action())
        self._rebuild_fields(action)
        if self._shortcut_recorder is not None:
            self._shortcut_recorder.set_action(action)
        if self._purpose is not None and self._purpose.currentData() == "native" and action_type != "key":
            self._purpose.setCurrentIndex(self._purpose.findData("shortcut"))
        self._sync_voice_ui(action)
        self.action_changed.emit()

    def _shortcut_was_recorded(self, action: dict[str, Any]) -> None:
        self.set_action(action)
        if self._manual_toggle is not None:
            self._manual_toggle.setChecked(False)
        self.shortcut_recorded.emit(action)

    def _rebuild_fields(self, action: dict[str, Any]) -> None:
        while self._fields.rowCount():
            self._fields.removeRow(0)
        self._field_widgets.clear()
        definition = self._definitions[str(self._type_selector.currentData())]
        if definition.action_type == "host_action":
            # The task editor owns the complete binding. A key editor must not
            # change its slot without also changing the task identity and owner.
            return
        for field in definition.fields:
            widget = self._field_widget(
                definition.action_type,
                field,
                action.get(field.name, field.default_value()),
            )
            widget.setObjectName(f"actionField_{field.name}")
            self._field_widgets[field.name] = widget
            self._fields.addRow(action_field_label(definition.action_type, field.name), widget)
            if isinstance(widget, ModifierSelector):
                for checkbox in widget.findChildren(QCheckBox):
                    checkbox.toggled.connect(self.action_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.action_changed)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self.action_changed)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.action_changed)

    def _field_widget(self, action_type: str, field: ActionField, value: Any) -> QWidget:
        if action_type in {"key", "key_gesture"} and field.name in {"modifiers", "double_modifiers"}:
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
