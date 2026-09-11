from PySide6.QtWidgets import QBoxLayout, QPushButton, QSlider

from controller_config.appearance import V4_STYLE
from controller_config.prompt_device import PromptListenerStatus
from controller_config.prompt_library import PromptEntry, PromptLibrary
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.prompt_library_editor import PromptLibraryEditor
from controller_config.views.v4_widgets import V4Card


def prompt_editor(qtbot):
    saved = []
    editor = PromptLibraryEditor(
        PromptLibrary("CP01-AABBCCDDEEFF", draft=(PromptEntry(1, "审查", "你好"),)),
        device_storage_available=True, protocol_message="可用", device_busy=False,
        helper_message="尚未粘贴", listener_status=PromptListenerStatus(),
        event_log=(), background_message="助手离线",
        save_draft=lambda *args: saved.append(args), delete_draft=lambda *_: None,
        discard_draft=lambda *_: None, refresh_device=lambda: None,
        write_device=lambda *_: None, delete_device=lambda *_: None,
    )
    qtbot.addWidget(editor)
    editor.setStyleSheet(V4_STYLE)
    return editor, saved


def test_prompt_cards_keep_utf8_counts_and_local_only_save(qtbot):
    editor, saved = prompt_editor(qtbot)
    focus = editor.findChild(V4Card, "promptFocusCard")
    assert focus.property("v4Role") == "focus"
    assert editor._name_bytes.text() == "6/48 UTF-8 字节"
    assert editor._body_bytes.text() == "6/4096 UTF-8 字节"
    editor._name.setText("英文 A")
    editor._body.setPlainText("一\n二")
    editor.findChild(QPushButton, "savePromptDraft").click()
    assert saved == [(1, "英文 A", "一\n二")]
    assert editor._body_bytes.text() == "7/4096 UTF-8 字节"
    assert len(editor._direction_buttons) == 4
    assert editor.findChild(V4Card, "promptPasteCard") is not None
    assert editor.findChild(V4Card, "promptEventsCard") is not None


def test_prompt_columns_stack_without_losing_current_input(qtbot):
    editor, _ = prompt_editor(qtbot)
    editor.resize(1180, 700)
    editor.show()
    qtbot.waitUntil(lambda: editor._workspace_layout.direction() == QBoxLayout.Direction.LeftToRight)
    editor._body.setPlainText("保留尚未保存的输入")
    editor.resize(760, 700)
    qtbot.waitUntil(lambda: editor._workspace_layout.direction() == QBoxLayout.Direction.TopToBottom)
    assert editor._body.toPlainText() == "保留尚未保存的输入"
    assert editor.widget().width() <= editor.viewport().width()


def test_preferences_cards_keep_schema_values_and_stack(qtbot, contract):
    lighting = {"enabled": True, "brightness": 40, "status": [], "under_key": []}
    haptic = {"enabled": True, "strength": 40, "duration_ms": 80, "on_press": True, "on_profile": False}
    display = {"brightness": 50, "rotation": 0, "show_control_hints": True}
    editor = PreferencesEditor(
        lighting=lighting, haptic=haptic, display=display,
        features={"haptic": True, "display": True}, under_key_control_ids=(),
        agent_status_control_ids=frozenset(), rules=contract.editor_rules,
        lighting_levels=(0, 10, 20, 40, 80),
    )
    qtbot.addWidget(editor)
    editor.setStyleSheet(V4_STYLE)
    assert editor.values() == (lighting, haptic, display)
    assert editor._light_card.property("v4Role") == "primary"
    assert editor._screen_card.property("v4Role") == "widget"
    assert editor._haptic_card.property("v4Role") == "secondary"
    assert editor._lighting_brightness.maximum() == 4
    slider = editor.findChild(QSlider, "hapticStrengthSlider")
    slider.setValue(33)
    assert editor.values()[1]["strength"] == 33
    editor._haptic_strength.setValue(27)
    assert slider.value() == 27
    editor.resize(1180, 700)
    editor.show()
    qtbot.waitUntil(lambda: not editor._compact_cards)
    before = editor.values()
    editor.resize(760, 900)
    qtbot.waitUntil(lambda: editor._compact_cards)
    assert editor.values() == before
    assert not editor._light_card.geometry().intersects(editor._screen_card.geometry())
    assert not editor._screen_card.geometry().intersects(editor._haptic_card.geometry())
