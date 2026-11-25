"""Primary shortcut choices edit values; native voice never guesses a hotkey."""
import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from controller_config.actions import action_definitions
from controller_config.i18n import translate_ui_text
from controller_config.views.action_editor import ActionEditor


def make_editor(qtbot, contract, action=None, **options):
    editor = ActionEditor(
        action_definitions(contract, ("key", "none", "consumer", "key_gesture")),
        action or {"type": "none"},
        platform="macos",
        **options,
    )
    qtbot.addWidget(editor)
    editor.show()
    return editor


@pytest.mark.parametrize("platform,modifier", [("macos", 227), ("windows", 224)])
@pytest.mark.parametrize("name,usage", [("copy", 6), ("paste", 25), ("undo", 29), ("redo", None)])
def test_common_shortcut_uses_existing_platform_mapping(qtbot, contract, platform, modifier, name, usage):
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "none"},
        platform=platform,
    )
    qtbot.addWidget(editor)
    editor.show()
    menu = editor.findChild(QPushButton, "commonShortcutMenu").menu()
    preset = next(action for action in menu.actions() if action.objectName() == f"commonShortcut_{name}")
    preset.trigger()

    if name == "redo":
        expected = ({"type": "key", "usage": 29, "modifiers": [225, modifier]}
                    if platform == "macos" else {"type": "key", "usage": 28, "modifiers": [modifier]})
    else:
        expected = {"type": "key", "usage": usage, "modifiers": [modifier]}
    assert editor.action() == expected
    assert editor._shortcut_recorder.isVisible()
    assert not editor.findChild(QWidget, "manualActionEditor").isVisible()
    common = editor.findChild(QPushButton, "commonShortcutMenu")
    manual = editor.findChild(QPushButton, "shortcutManualToggle")
    assert common.geometry().y() == manual.geometry().y()


def test_native_purpose_explains_recording_without_changing_existing_action(qtbot, contract):
    original = {"type": "consumer", "usage": 233}
    editor = make_editor(qtbot, contract, original)

    assert editor.set_purpose("native")
    assert editor.action() == original
    assert editor.findChild(QLabel, "nativeVoiceShortcutNotice").isVisible()
    assert editor._shortcut_recorder.isVisible()
    assert not editor.findChild(QWidget, "manualActionEditor").isVisible()
    assert not editor.findChild(QPushButton, "commonShortcutMenu").isVisible()
    assert editor.findChild(QLabel, "shortcutRecorderHeading").text() == translate_ui_text("AI 语音快捷键")

    recorded = {"type": "key", "usage": 21, "modifiers": [225, 227]}
    editor._shortcut_recorder.shortcut_recorded.emit(recorded)
    assert editor.action() == recorded
    assert editor._purpose.currentData() == "native"
    assert editor.set_purpose("shortcut")
    assert editor.action() == recorded
    assert not editor.findChild(QLabel, "nativeVoiceShortcutNotice").isVisible()


def test_native_and_external_switch_preserves_custom_shortcut(qtbot, contract):
    shortcut = {"type": "key", "usage": 21, "modifiers": [227]}
    editor = make_editor(qtbot, contract, shortcut, allow_voice_preset=True)
    assert editor._purpose.itemData(1) == "voice"
    assert editor.set_purpose("native")
    assert editor.set_purpose("voice")
    assert editor.action() == {"type": "key_gesture", "usage": 104, "double_usage": 40}
    assert editor.set_purpose("native")
    assert editor.action() == shortcut
    assert editor._purpose.currentData() == "native"


def test_no_external_purpose_is_added_without_existing_permission(qtbot, contract):
    editor = make_editor(qtbot, contract)
    assert editor._purpose.findData("voice") == -1
    assert not editor.set_purpose("voice")
    assert editor.action() == {"type": "none"}


def test_other_actions_remain_explicit_and_preserve_current_media(qtbot, contract):
    original = {"type": "consumer", "usage": 233}
    editor = make_editor(qtbot, contract, original)
    toggle = editor.findChild(QPushButton, "shortcutManualToggle")
    assert toggle.isVisible() and toggle.isChecked()
    assert toggle.text() == "其他动作…"
    assert toggle.accessibleName() == translate_ui_text("手动设置或选择其他动作")
    assert editor.findChild(QWidget, "manualActionEditor").isVisible()
    toggle.click()
    assert editor.action() == original
    toggle.click()
    assert editor.action() == original


def test_key_only_nested_recorder_keeps_its_existing_small_interface(qtbot, contract):
    editor = ActionEditor(
        action_definitions(contract, ("key",)),
        {"type": "key", "usage": 104},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()
    assert editor._purpose is None
    assert editor.findChild(QPushButton, "commonShortcutMenu") is None
    assert not editor.set_purpose("native")


def test_explicit_manual_nonkey_choice_leaves_native_purpose(qtbot, contract):
    editor = make_editor(qtbot, contract)
    editor.set_purpose("native")
    editor._manual_toggle.click()
    editor._type_selector.setCurrentIndex(editor._type_selector.findData("consumer"))
    assert editor.action()["type"] == "consumer"
    assert editor._purpose.currentData() == "shortcut"


def test_codex_native_restores_official_without_losing_external_choice(qtbot, contract):
    custom = {"type": "key_gesture", "usage": 231, "double_usage": 40, "double_modifiers": [227]}
    editor = make_editor(qtbot, contract, copy.deepcopy(custom), allow_voice_preset=True, codex_voice=True)
    assert editor._purpose.count() == 2
    assert editor.set_purpose("native")
    assert editor.action() == {"type": "none"}
    assert editor.set_purpose("voice")
    assert editor.action() == custom
    assert editor.findChild(QPushButton, "commonShortcutMenu") is None


def test_unsupported_external_action_cannot_be_selected(qtbot, contract):
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 44},
        platform="macos", allow_voice_preset=True,
    )
    qtbot.addWidget(editor)
    editor.show()
    assert not editor.set_purpose("voice")
    assert editor.action() == {"type": "key", "usage": 44}
    assert not editor.findChild(QLabel, "voiceFirmwareNotice").isVisible()
    assert "此设备固件尚不支持" in editor._purpose.toolTip()
    assert "此设备固件尚不支持" in editor._purpose.itemData(1, Qt.ToolTipRole)
    assert editor.set_purpose("native")
    assert editor.action() == {"type": "key", "usage": 44}
