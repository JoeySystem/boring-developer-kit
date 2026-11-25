from PySide6.QtWidgets import QBoxLayout, QPushButton, QSlider, QWidget

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
        device_storage_available=False, protocol_message="设备离线", device_busy=False,
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
    assert editor.findChild(QWidget, "promptFocusCard").span == (2, 2)  # 定稿 v1: the editor tile
    # Characters, not bytes: "审查" is 2 characters of a 16-character name.
    assert editor._name_count.text() == "2 / 16 字"
    assert editor._body_count.text() == ""
    editor._name.setText("英文 A")
    editor._body.setPlainText("一\n二")
    editor.findChild(QPushButton, "savePromptDraft").click()
    assert saved == [(1, "英文 A", "一\n二")]
    assert editor._name_count.text() == "4 / 17 字"
    assert editor._body_count.text() == ""
    assert editor.findChild(QWidget, "promptDirectionCross") is not None
    assert editor.findChild(QWidget, "promptDiagnostics") is not None  # helper, log and paste help


def test_prompt_wall_shrinks_without_losing_current_input(qtbot):
    editor, _ = prompt_editor(qtbot)
    editor.resize(1180, 700)
    editor.show()
    qtbot.waitUntil(lambda: editor._wall.width() > 0)
    wide = editor._wall.width()
    editor._body.setPlainText("保留尚未保存的输入")
    editor.resize(760, 700)
    qtbot.waitUntil(lambda: editor._wall.width() < wide)
    assert editor._body.toPlainText() == "保留尚未保存的输入"
    qtbot.waitUntil(lambda: editor.widget().width() <= editor.viewport().width())


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
    # 灯光与反馈 is one wall (the designer, 灯光页 v0.4): fixed cells, nothing re-stacks.
    assert editor._light_card.span == (2, 2)
    assert editor._screen_card.span == (1, 1) and editor._haptic_card.span == (1, 1)
    assert editor._icon_card.span == (1, 2)
    assert editor._lighting_brightness.maximum() == 4
    slider = editor.findChild(QSlider, "hapticStrengthSlider")
    slider.setValue(33)
    assert editor.values()[1]["strength"] == 33
    editor._haptic_strength.setValue(27)
    assert slider.value() == 27
    editor.show()
    before = editor.values()
    editor.open_tile(editor._haptic_card)
    editor.open_tile(None)
    assert editor.values() == before
    for first, second in ((editor._light_card, editor._screen_card),
                          (editor._screen_card, editor._haptic_card),
                          (editor._screen_card, editor._icon_card)):
        assert not first.geometry().intersects(second.geometry())
