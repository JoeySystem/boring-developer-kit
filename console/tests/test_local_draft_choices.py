import copy

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
from controller_config.profile_templates import agent_profile_templates_for_platform
from controller_config.views.action_editor import ActionEditor
from controller_config.views.draft_dialog import confirm_local_draft
from controller_config.views.preferences_editor import PreferencesEditor
from test_session_recovery import session


def test_dialog_names_local_choices_and_defaults_to_keep_editing(session, qapp):
    window, *_ = session
    observed = []

    def inspect():
        dialog = qapp.activeModalWidget()
        observed.append({b: dialog.button(b).text() for b in
                         (QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel)})
        assert dialog.defaultButton() is dialog.button(QMessageBox.Cancel)
        dialog.button(QMessageBox.Cancel).click()

    QTimer.singleShot(0, inspect)
    assert confirm_local_draft(window, "按键修改尚未应用", "尚未应用到设备") == QMessageBox.Cancel
    assert observed == [{QMessageBox.Save: "保留草稿", QMessageBox.Discard: "放弃修改",
                         QMessageBox.Cancel: "继续编辑"}]


@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def test_switching_controls_keeps_discards_or_retains_editor_without_device_write(session, monkeypatch, choice):
    window, vm, gateway, *_ = session
    window._select_physical_control("key.9")
    original = copy.deepcopy(vm.draft.mapping(0, "key.9"))
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("我的动作")
    editor = window.findChild(ActionEditor)
    editor.set_action({"type": "key", "usage": 27, "modifiers": []})
    commands = list(gateway.commands)
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: choice)
    window._select_physical_control("key.10")
    assert gateway.commands == commands
    if choice == QMessageBox.Cancel:
        assert window._selected_control_id == "key.9"
        assert window.findChild(QLineEdit, "mappingShortNameEditor") is name
        assert window.findChild(ActionEditor) is editor
    else:
        assert window._selected_control_id == "key.10"
        window._select_physical_control("key.9")
    if choice == QMessageBox.Save:
        assert vm.draft.mapping(0, "key.9")["short_name"] == "我的动作"
        assert vm.draft.mapping(0, "key.9")["action"]["usage"] == 27
        assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "我的动作"
        assert vm.draft.is_dirty
    else:
        assert vm.draft.mapping(0, "key.9") == original
        if choice == QMessageBox.Discard:
            assert window.findChild(QLineEdit, "mappingShortNameEditor").text() != "我的动作"


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

    def choose_export(dialog):
        assert "草稿只保留在本进程中" in dialog.text()
        next(b for b in dialog.buttons() if dialog.buttonRole(b) == QMessageBox.AcceptRole).click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose_export)
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
    active = vm.draft.config["active_profile"]
    for control in MATRIX12_AGENT_STATUS_KEYS:
        assert vm.draft.mapping(active, control)["action"] == {"type": "none"}
    assert vm.draft.mapping(active, "key.8")["action"]["usage"] == 40
    assert not any(c.name == "SET_CONFIG" for c in gateway.commands)


@pytest.mark.parametrize("choice", [QMessageBox.Save, QMessageBox.Discard, QMessageBox.Cancel])
def test_leaving_appearance_handles_local_input_without_applying(session, monkeypatch, choice):
    window, vm, gateway, *_ = session
    window._navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    before = copy.deepcopy(editor.values())
    editor._haptic_enabled.setChecked(not editor._haptic_enabled.isChecked())
    changed = copy.deepcopy(editor.values())
    monkeypatch.setattr("controller_config.views.main_window.confirm_local_draft", lambda *_: choice)
    window._navigate("overview")
    assert vm.page == ("lighting" if choice == QMessageBox.Cancel else "overview")
    if choice == QMessageBox.Cancel:
        assert window.findChild(PreferencesEditor) is editor
        assert editor.values() == changed
    else:
        window._navigate("lighting")
        assert window.findChild(PreferencesEditor).values() == (changed if choice == QMessageBox.Save else before)
    assert not any(c.name in {"SET_CONFIG", "VALIDATE_CONFIG"} for c in gateway.commands)


@pytest.mark.parametrize("mode,name", [("normal", "对应对话"), ("codex", "Agent 1"), ("claude_code", "Submit")])
def test_official_key_explains_actual_mode_and_stays_readonly(session, mode, name):
    window, vm, gateway, *_ = session
    gateway.status_updated.emit({**vm.model.snapshot.status, "operating_mode": mode})
    window._select_physical_control("key.1")
    assert window.findChild(ActionEditor) is None
    assert name in window.findChild(QLabel, "officialControlDefinitionNote").text()
    if mode == "claude_code":
        assert window.findChild(QLabel, "officialControlDefinitionTitle").text() == "CC 专用功能"
