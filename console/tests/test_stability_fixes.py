from dataclasses import replace
import copy

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton

from controller_config.automation import AutomationError
from controller_config.prompt_library import PromptEntry
from controller_config.views.action_editor import ActionEditor
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.workflows import LocalWorkflow, WorkflowError, WorkflowStep
from test_session_recovery import session, _editor


def test_prompt_unsaved_input_cannot_disappear_on_slot_navigation_or_exit(session, monkeypatch):
    window, vm, gateway, snapshot, store = session
    vm.navigate("prompts")
    editor = _editor(window)
    editor._name.setText("Keep me")
    editor._body.setPlainText("Unsaved input")
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Cancel)
    editor._direction_buttons[1].click()
    assert editor._body.toPlainText() == "Unsaved input"
    editor._select_direction(2)
    assert editor._selected_prompt_id() == 1
    window._nav_buttons["settings"].click()
    assert vm.page == "prompts"
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert _editor(window)._body.toPlainText() == "Unsaved input"
    _editor(window)._save()
    _editor(window)._select_direction(2)
    window._nav_buttons["settings"].click()
    assert vm.page == "settings"
    assert store.load(snapshot.identity["serial"]).draft_entry(1).body == "Unsaved input"
    assert not any(command.name == "SET_PROMPT" for command in gateway.commands)


def test_prompt_save_while_switching_slots_updates_rebuilt_editor(session, monkeypatch):
    window, vm, gateway, snapshot, store = session
    vm.navigate("prompts")
    _editor(window)._name.setText("Saved on switch")
    _editor(window)._body.setPlainText("Original slot one")
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Save)
    _editor(window)._select_direction(2)
    assert _editor(window)._selected_prompt_id() == 2
    assert _editor(window)._body.toPlainText() != "Original slot one"
    assert store.load(snapshot.identity["serial"]).draft_entry(1).body == "Original slot one"


def test_failed_prompt_save_keeps_fields_dirty_and_blocks_leaving(session, monkeypatch):
    window, vm, gateway, snapshot, store = session
    vm.navigate("prompts")
    _editor(window)._name.setText("Keep on failure")
    _editor(window)._body.setPlainText("Do not lose")
    def fail(_library):
        raise OSError("disk unavailable")
    monkeypatch.setattr(store, "save", fail)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.Save)
    window._nav_buttons["settings"].click()
    assert vm.page == "prompts"
    assert _editor(window).has_unsaved_fields()
    assert _editor(window)._body.toPlainText() == "Do not lose"


@pytest.mark.parametrize("choice", [QMessageBox.Cancel, QMessageBox.Save, QMessageBox.Discard])
def test_all_device_preferences_are_guarded_before_navigation(session, monkeypatch, choice):
    window, vm, gateway, snapshot, store = session
    vm.navigate("lighting")
    editor = window._content.findChild(PreferencesEditor)
    editor._lighting_brightness.setValue(2)
    editor._haptic_strength.setCurrentIndex(editor._haptic_strength.findData(40))
    editor._display_brightness.setCurrentIndex(editor._display_brightness.findData(0))
    expected = editor.values()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: choice)
    window._nav_buttons["settings"].click()
    assert vm.page == ("lighting" if choice == QMessageBox.Cancel else "settings")
    if choice == QMessageBox.Save:
        assert tuple(vm.draft.config[key] for key in ("lighting", "haptic", "display")) == expected
    elif choice == QMessageBox.Cancel:
        assert window._content.findChild(PreferencesEditor).values() == expected
    else:
        assert vm.draft.config["display"] == snapshot.config["display"]
    assert not any(command.name == "SET_CONFIG" for command in gateway.commands)


def test_import_rebuilds_selected_mapping_from_new_config(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot, store = session
    action = {"type": "key", "usage": 27}
    vm.set_mapping(0, "key.12", "X", action)
    path = tmp_path / "incoming.json"
    vm.export_configuration(path, kind="draft")
    vm.discard_draft()
    window._select_physical_control("key.12")
    assert window._content.findChild(ActionEditor).action() != action
    selected = []
    def choose(prompt):
        selected.append(next(button for button in prompt.buttons() if button.text() == "导入到本地草稿"))
        return 0
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(QMessageBox, "exec", choose)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda _prompt: selected[-1])
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.Ok)
    window._import_configuration()
    assert window._content.findChild(ActionEditor).action() == action
    window._content.findChild(QPushButton, "saveMappingDraft").click()
    assert vm.draft.mapping(0, "key.12")["action"] == action


def test_trigger_enable_requires_device_readback_not_local_draft(session, tmp_path):
    window, vm, gateway, snapshot, store = session
    workflow = LocalWorkflow("test", "Test", 1, (WorkflowStep("read_clipboard", {}),), tested=True, enabled=True)
    # Mimic the protocol's complete empty-list response, not an old disk cache.
    vm.prompt_device._read_entries = []
    vm.prompt_device._finish_full_read()
    with pytest.raises(WorkflowError, match="尚未写入设备"):
        vm.save_workflow(workflow)
    vm.save_prompt_draft(1, "Local", "Local only")
    with pytest.raises(WorkflowError, match="尚未写入设备"):
        vm.save_workflow(workflow)
    script = tmp_path / "action.py"
    script.write_text("print('hello')\n")
    with pytest.raises(AutomationError, match="尚未写入设备"):
        vm.save_automation(automation_id=None, name="test", trigger_prompt_id=1, script_path=str(script), enabled=True)
    vm.prompt_device._read_entries = [PromptEntry(1, "Device", "Already written")]
    vm.prompt_device._finish_full_read()
    vm.save_workflow(workflow)
    assert vm.host_actions[0].available
    assert vm.host_actions[0].physically_bound
    vm.prompt_device._read_entries = []
    vm.prompt_device._finish_full_read()
    assert not vm.host_actions[0].available
    assert not vm.host_actions[0].physically_bound
    assert "尚未写入设备" in vm.host_actions[0].availability_reason


def test_single_prompt_write_readback_is_enough_to_enable_its_trigger(session):
    window, vm, gateway, snapshot, store = session
    assert "读取设备提示词" in vm.prompt_trigger_problem(1)
    entry = vm.save_prompt_draft(1, "Trigger", "Written to device")
    vm.write_prompt_draft(1)
    assert vm.prompt_device.status.is_busy
    vm.prompt_device.handle_completed("SET_PROMPT", {
        "command": "SET_PROMPT", "result": {"prompt_id": 1},
    })
    vm.prompt_device.handle_completed("GET_PROMPT", {
        "command": "GET_PROMPT", "result": {**entry.as_mapping(), "body_bytes": entry.body_bytes},
    })
    assert not vm.prompt_trigger_problem(1)
    assert "读取设备提示词" in vm.prompt_trigger_problem(2)


def test_advanced_trigger_requires_confirmed_mapping_and_reconnect_readback(session):
    window, vm, gateway, snapshot, store = session
    vm.prompt_device._read_entries = [PromptEntry(5, "Advanced", "trigger")]
    vm.prompt_device._finish_full_read()
    assert "不在四向提示词盘" in vm.prompt_trigger_problem(5)
    config = copy.deepcopy(snapshot.config)
    profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
    profile["mappings"] = [m for m in profile["mappings"] if m["control_id"] != "key.12"]
    profile["mappings"].append({"control_id": "key.12", "action": {"type": "prompt", "prompt_id": 5}})
    mapped = replace(snapshot, config_result={**snapshot.config_result, "config": config})
    gateway.snapshot_ready.emit(mapped)
    assert not vm.prompt_trigger_problem(5)
    assert 5 in vm.prompt_trigger_choices
    gateway.disconnected.emit("removed")
    gateway.snapshot_ready.emit(mapped)
    assert "读取设备提示词" in vm.prompt_trigger_problem(5)
