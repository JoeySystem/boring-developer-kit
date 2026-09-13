from __future__ import annotations

import pytest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from controller_config.actions import action_definitions
from controller_config.views.action_editor import ActionEditor


_MACOS_NATIVE_MODIFIERS = (
    (Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier, 0x36, 231),
    (Qt.Key.Key_Meta, Qt.KeyboardModifier.MetaModifier, 0x3E, 228),
    (Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier, 0x3C, 229),
    (Qt.Key.Key_Alt, Qt.KeyboardModifier.AltModifier, 0x3D, 230),
)


def _send_native_key_event(
    target: QWidget,
    event_type: QEvent.Type,
    key: Qt.Key,
    modifiers: Qt.KeyboardModifier,
    native_virtual_key: int,
) -> None:
    QApplication.sendEvent(
        target,
        QKeyEvent(
            event_type,
            key,
            modifiers,
            0,
            native_virtual_key,
            0,
        ),
    )


def test_action_editor_records_a_keyboard_shortcut_and_single_key(
    qtbot, contract
) -> None:
    definitions = action_definitions(contract, ("key", "consumer", "none"))
    editor = ActionEditor(
        definitions,
        {"type": "key", "usage": 25, "modifiers": [227]},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()

    record = editor.findChild(QPushButton, "shortcutRecordButton")
    preview = editor.findChild(QLabel, "shortcutRecorderPreview")
    manual = editor.findChild(QWidget, "manualActionEditor")
    assert record is not None and preview is not None and manual is not None
    assert preview.text() == "⌘V"
    assert not manual.isVisible()

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    assert record.text() == "取消录制"
    qtbot.keyClick(
        record,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier,
    )
    assert editor.action() == {
        "type": "key",
        "usage": 25,
        "modifiers": [225, 227],
    }
    assert preview.text() == "⇧⌘V"

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    qtbot.keyClick(
        record,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.MetaModifier,
    )
    assert editor.action() == {
        "type": "key",
        "usage": 25,
        "modifiers": [224],
    }
    assert preview.text() == "⌃V"

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    qtbot.keyClick(record, Qt.Key.Key_Backspace)
    assert editor.action() == {"type": "key", "usage": 42}
    assert preview.text() == "退格"


def test_action_editor_records_shifted_symbol_as_base_key_plus_shift(
    qtbot, contract
) -> None:
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 4},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()

    record = editor.findChild(QPushButton, "shortcutRecordButton")
    assert record is not None
    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    qtbot.keyClick(record, Qt.Key.Key_Plus)

    assert editor.action() == {
        "type": "key",
        "usage": 46,
        "modifiers": [225],
    }


@pytest.mark.parametrize(
    ("qt_key", "qt_modifier", "native_virtual_key", "expected_usage"),
    _MACOS_NATIVE_MODIFIERS,
)
def test_action_editor_preserves_right_macos_modifier_in_shortcut(
    qtbot,
    contract,
    qt_key,
    qt_modifier,
    native_virtual_key,
    expected_usage,
) -> None:
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 4},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()
    record = editor.findChild(QPushButton, "shortcutRecordButton")
    assert record is not None

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    _send_native_key_event(
        record,
        QEvent.Type.KeyPress,
        qt_key,
        qt_modifier,
        native_virtual_key,
    )
    QApplication.sendEvent(
        record,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_V,
            qt_modifier,
            0,
            0,
            0,
        ),
    )

    assert editor.action() == {
        "type": "key",
        "usage": 25,
        "modifiers": [expected_usage],
    }


@pytest.mark.parametrize(
    ("qt_key", "qt_modifier", "native_virtual_key", "expected_usage"),
    _MACOS_NATIVE_MODIFIERS,
)
def test_action_editor_preserves_right_macos_modifier_as_single_key(
    qtbot,
    contract,
    qt_key,
    qt_modifier,
    native_virtual_key,
    expected_usage,
) -> None:
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 4},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()
    record = editor.findChild(QPushButton, "shortcutRecordButton")
    assert record is not None

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    _send_native_key_event(
        record,
        QEvent.Type.KeyPress,
        qt_key,
        qt_modifier,
        native_virtual_key,
    )
    _send_native_key_event(
        record,
        QEvent.Type.KeyRelease,
        qt_key,
        Qt.KeyboardModifier.NoModifier,
        native_virtual_key,
    )

    assert editor.action() == {"type": "key", "usage": expected_usage}


def test_action_editor_does_not_add_left_shift_to_right_shifted_symbol(
    qtbot,
    contract,
) -> None:
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 4},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()
    record = editor.findChild(QPushButton, "shortcutRecordButton")
    assert record is not None

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    _send_native_key_event(
        record,
        QEvent.Type.KeyPress,
        Qt.Key.Key_Shift,
        Qt.KeyboardModifier.ShiftModifier,
        0x3C,
    )
    QApplication.sendEvent(
        record,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Question,
            Qt.KeyboardModifier.ShiftModifier,
            0,
            0,
            0,
        ),
    )

    assert editor.action() == {
        "type": "key",
        "usage": 56,
        "modifiers": [229],
    }


def test_unsupported_shortcut_does_not_turn_into_released_modifier(
    qtbot,
    contract,
) -> None:
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 4},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()
    record = editor.findChild(QPushButton, "shortcutRecordButton")
    message = editor.findChild(QLabel, "shortcutRecorderMessage")
    assert record is not None and message is not None

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    _send_native_key_event(
        record,
        QEvent.Type.KeyPress,
        Qt.Key.Key_Control,
        Qt.KeyboardModifier.ControlModifier,
        0x37,
    )
    QApplication.sendEvent(
        record,
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_unknown,
            Qt.KeyboardModifier.ControlModifier,
        ),
    )
    QApplication.sendEvent(
        record,
        QKeyEvent(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_unknown,
            Qt.KeyboardModifier.ControlModifier,
        ),
    )
    _send_native_key_event(
        record,
        QEvent.Type.KeyRelease,
        Qt.Key.Key_Control,
        Qt.KeyboardModifier.NoModifier,
        0x37,
    )

    assert editor.action() == {"type": "key", "usage": 4}
    assert record.text() == "取消录制"
    assert "按下按键或组合键" in message.text()

    qtbot.keyClick(record, Qt.Key.Key_V)
    assert editor.action() == {"type": "key", "usage": 25}


def test_action_editor_uses_friendly_key_and_consumer_selectors(qtbot, contract) -> None:
    definitions = action_definitions(
        contract,
        ("key", "consumer", "mouse", "macro", "profile", "device", "none"),
    )
    editor = ActionEditor(
        definitions,
        {"type": "key", "usage": 40, "modifiers": [224, 227]},
        platform="macos",
    )
    qtbot.addWidget(editor)

    type_selector = editor.findChild(QComboBox, "actionTypeEditor")
    usage = editor.findChild(QComboBox, "actionField_usage")
    modifiers = editor.findChild(QWidget, "actionField_modifiers")
    assert type_selector is not None and type_selector.count() == 7
    assert type_selector.itemText(type_selector.findData("macro")) == "按键序列"
    assert usage is not None and usage.currentData() == 40
    assert "Enter" in usage.currentText()
    assert editor.findChild(QSpinBox, "actionField_usage") is None
    assert modifiers is not None
    field_labels = [label.text() for label in editor.findChildren(QLabel)]
    assert "按键" in field_labels and "组合键" in field_labels
    assert "Usage" not in field_labels and "修饰键 Usage" not in field_labels
    selected_modifiers = {
        checkbox.property("usage")
        for checkbox in modifiers.findChildren(QCheckBox)
        if checkbox.isChecked()
    }
    assert selected_modifiers == {224, 227}
    assert any("Command" in checkbox.text() for checkbox in modifiers.findChildren(QCheckBox))
    modifier_layout = modifiers.layout()
    assert isinstance(modifier_layout, QGridLayout)
    positions = {
        modifier_layout.getItemPosition(index)[:2]
        for index in range(modifier_layout.count())
    }
    assert positions == {(row, column) for row in range(4) for column in range(2)}
    assert editor.action() == {"type": "key", "usage": 40, "modifiers": [224, 227]}

    type_selector.setCurrentIndex(type_selector.findData("consumer"))
    usage = editor.findChild(QComboBox, "actionField_usage")
    assert usage is not None
    usage.setCurrentIndex(usage.findData(233))
    assert usage.currentText() == "音量提高"
    assert editor.action() == {"type": "consumer", "usage": 233}


def test_action_editor_labels_device_enum_and_preserves_unknown_legal_usage(qtbot, contract) -> None:
    definitions = action_definitions(contract, ("key", "consumer", "device"))
    editor = ActionEditor(definitions, {"type": "key", "usage": 200})
    qtbot.addWidget(editor)
    usage = editor.findChild(QComboBox, "actionField_usage")
    assert usage is not None and usage.currentData() == 200
    assert "Usage 200" in usage.currentText()
    assert editor.action() == {"type": "key", "usage": 200}

    type_selector = editor.findChild(QComboBox, "actionTypeEditor")
    type_selector.setCurrentIndex(type_selector.findData("consumer"))
    usage = editor.findChild(QComboBox, "actionField_usage")
    assert usage is not None and usage.currentData() == 0
    assert "未分配" in usage.currentText()

    editor.set_action({"type": "consumer", "usage": 999})
    usage = editor.findChild(QComboBox, "actionField_usage")
    assert usage is not None and usage.currentData() == 999
    assert "Usage 999" in usage.currentText()
    assert editor.action() == {"type": "consumer", "usage": 999}

    type_selector.setCurrentIndex(type_selector.findData("device"))
    device = editor.findChild(QComboBox, "actionField_name")
    assert device is not None
    assert device.itemText(device.findData("lighting_toggle")) == "切换灯光"
    device.setCurrentIndex(device.findData("haptic_toggle"))
    assert editor.action() == {"type": "device", "name": "haptic_toggle"}


def test_action_editor_does_not_silently_replace_unsupported_current_action(qtbot, contract) -> None:
    definitions = action_definitions(contract, ("none",))

    try:
        ActionEditor(definitions, {"type": "key", "usage": 4})
    except ValueError as exc:
        assert "不在设备能力" in str(exc)
    else:
        raise AssertionError("unsupported current action must not be replaced")


def test_action_editor_uses_names_for_macro_and_profile_references(qtbot, contract) -> None:
    definitions = action_definitions(contract, ("macro", "profile"))
    editor = ActionEditor(
        definitions,
        {"type": "macro", "macro_id": 7},
        reference_choices={
            ("macro", "macro_id"): (("Build and send", 7),),
            ("profile", "profile_id"): (("macOS", 0), ("Windows", 3)),
        },
    )
    qtbot.addWidget(editor)

    macro = editor.findChild(QComboBox, "actionField_macro_id")
    assert macro is not None and macro.currentText() == "Build and send"
    assert editor.findChild(QSpinBox, "actionField_macro_id") is None
    assert editor.action() == {"type": "macro", "macro_id": 7}

    type_selector = editor.findChild(QComboBox, "actionTypeEditor")
    type_selector.setCurrentIndex(type_selector.findData("profile"))
    profile = editor.findChild(QComboBox, "actionField_profile_id")
    assert profile is not None
    profile.setCurrentIndex(profile.findData(3))
    assert profile.currentText() == "Windows"
    assert editor.action() == {"type": "profile", "profile_id": 3}


def test_action_editor_uses_prompt_library_names(qtbot, contract) -> None:
    definitions = action_definitions(contract, ("prompt", "none"))
    editor = ActionEditor(
        definitions,
        {"type": "prompt", "prompt_id": 2},
        reference_choices={
            ("prompt", "prompt_id"): (("01 · 代码审查", 1), ("02 · 翻译", 2)),
        },
    )
    qtbot.addWidget(editor)

    selector = editor.findChild(QComboBox, "actionField_prompt_id")
    assert selector is not None and selector.currentText() == "02 · 翻译"
    assert editor.findChild(QSpinBox, "actionField_prompt_id") is None
    assert editor.action() == {"type": "prompt", "prompt_id": 2}
