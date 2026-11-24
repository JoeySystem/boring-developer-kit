import copy

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox, QPushButton

from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
from controller_config.i18n import translate_ui_text
from controller_config.profile_templates import agent_profile_templates_for_platform
from controller_config.views.action_editor import ActionEditor
from controller_config.views.draft_dialog import confirm_local_draft
from controller_config.views.preferences_editor import PreferencesEditor
from test_session_recovery import session
from notice_helpers import pick


@pytest.mark.parametrize("language", ["zh_CN", "en_US", "ja_JP"])
@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def test_dialog_names_local_choices_and_defaults_to_keep_editing(session, qapp, language, choice, monkeypatch):
    window, *_ = session
    # Opening an editor immediately after changing language must keep the local
    # choices, rather than let Qt restore its generic Save/Discard/Cancel labels.
    window._language_manager.set_language("ja_JP" if language == "zh_CN" else "zh_CN")
    window._language_manager.set_language(language)
    observed = []
    keys = {QMessageBox.Save: "keep", QMessageBox.Discard: "discard", QMessageBox.Cancel: "cancel"}

    def inspect(dialog):
        buttons = {result: dialog.findChild(QPushButton, f"choice_{key}") for result, key in keys.items()}
        observed.append({result: button.text() for result, button in buttons.items()})
        assert buttons[QMessageBox.Cancel].isDefault()  # Enter keeps editing; so does Esc
        buttons[choice].click()
        return dialog.result()

    from controller_config.views.ui import notice
    monkeypatch.setattr(notice, "_run", inspect)
    assert confirm_local_draft(window, "按键修改尚未应用", "尚未应用到设备") == choice
    assert observed == [{QMessageBox.Save: translate_ui_text("保留草稿"),
                         QMessageBox.Discard: translate_ui_text("放弃修改"),
                         QMessageBox.Cancel: translate_ui_text("继续编辑")}]


def test_switching_controls_keeps_valid_editor_as_draft_without_prompt_or_device_write(session, monkeypatch):
    window, vm, gateway, *_ = session
    window._select_physical_control("key.9")
    original = copy.deepcopy(vm.draft.mapping(0, "key.9"))
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("我的动作")
    editor = window.findChild(ActionEditor)
    editor.set_action({"type": "key", "usage": 27, "modifiers": []})
    commands = list(gateway.commands)
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: pytest.fail("ordinary selection prompted"))
    window._select_physical_control("key.10")
    assert gateway.commands == commands
    assert window._selected_control_id == "key.10"
    window._select_physical_control("key.9")
    assert vm.draft.mapping(0, "key.9")["short_name"] == "我的动作"
    assert vm.draft.mapping(0, "key.9")["action"]["usage"] == 27
    assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "我的动作"
    assert vm.draft.is_dirty


def test_keep_mapping_draft_survives_profile_switch_and_reconnect(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    window._select_physical_control("key.9")
    window.findChild(QLineEdit, "mappingShortNameEditor").setText("待应用")
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: QMessageBox.Save)
    window._activate_profile(second)
    assert vm.draft.mapping(0, "key.9")["short_name"] == "待应用"
    gateway.disconnected.emit("unplugged")
    gateway.snapshot_ready.emit(snapshot)
    window._activate_profile(0)
    window._select_physical_control("key.9")
    assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "待应用"
    assert not any(c.name in {"SET_CONFIG", "VALIDATE_CONFIG"} for c in gateway.commands)


@pytest.mark.parametrize("navigate_away", [False, True])
@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def test_quit_handles_mapping_input_before_export(session, monkeypatch, navigate_away, choice):
    window, vm, gateway, *_ = session
    window._select_physical_control("key.9")
    window.findChild(QLineEdit, "mappingShortNameEditor").setText("退出前的输入")
    if navigate_away:
        vm.navigate("settings")
    commands = list(gateway.commands)
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: choice)
    exported = []

    def choose_export(_title, text, _keys):
        assert "草稿只保留在本进程中" in text
        return "export"

    pick(monkeypatch, choose_export)
    monkeypatch.setattr(window, "_export_configuration", lambda *_: exported.append(copy.deepcopy(vm.draft.config)) or True)
    monkeypatch.setattr(window, "_confirm_discard_screen_icons", lambda: True)
    monkeypatch.setattr(window, "_confirm_discard_ble_names", lambda: True)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is (choice != QMessageBox.Cancel)
    if choice == QMessageBox.Save:
        assert len(exported) == 1
        assert vm.draft.mapping(0, "key.9")["short_name"] == "退出前的输入"
    else:
        assert exported == []
    if choice == QMessageBox.Cancel:
        vm.navigate("overview")
        assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "退出前的输入"
    assert gateway.commands == commands


def test_new_agent_profile_uses_hardware_ownership_and_preserves_current_profile(session):
    window, vm, gateway, *_ = session
    previous = copy.deepcopy(vm.draft.profile(0))
    window._create_agent_profile(agent_profile_templates_for_platform("darwin")[1])
    assert vm.draft.profile(0) == previous
    active = window._editing_profile(vm.draft)
    assert vm.draft.config["active_profile"] == 0
    for control in MATRIX12_AGENT_STATUS_KEYS:
        assert vm.draft.mapping(active, control)["action"] == {"type": "none"}
    assert vm.draft.mapping(active, "key.8")["action"]["usage"] == 40
    assert not any(c.name == "SET_CONFIG" for c in gateway.commands)


def test_leaving_appearance_keeps_valid_local_input_without_prompt_or_device_write(session, monkeypatch):
    window, vm, gateway, *_ = session
    window._navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    before = copy.deepcopy(editor.values())
    editor._haptic_enabled.setChecked(not editor._haptic_enabled.isChecked())
    changed = copy.deepcopy(editor.values())
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: pytest.fail("ordinary navigation prompted"))
    window._navigate("overview")
    assert vm.page == "overview"
    window._navigate("lighting")
    assert window.findChild(PreferencesEditor).values() == changed
    assert not any(c.name in {"SET_CONFIG", "VALIDATE_CONFIG"} for c in gateway.commands)


@pytest.mark.parametrize(
    "mode,title,note",
    [
        ("normal", "按下状态灯键时", "不会把应用带到前台"),
        ("codex", "固定功能", "Agent 1"),
    ],
)
def test_official_key_explains_actual_mode_and_stays_readonly(
    session, mode, title, note
):
    window, vm, gateway, *_ = session
    gateway.status_updated.emit({**vm.model.snapshot.status, "operating_mode": mode})
    window._select_configuration_mode(mode)
    window._select_physical_control("key.1")
    assert window.findChild(ActionEditor) is None
    assert note in window.findChild(QLabel, "officialControlDefinitionNote" if mode == "normal" else "officialControlAction").text()
    assert window.findChild(QLabel, "officialControlDefinitionTitle").text() == title
