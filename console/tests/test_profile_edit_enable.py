import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from controller_config.transactions import ConfigTransactionState
from controller_config.views.mode_page import ModeStage
from test_session_recovery import session
from test_write_transaction import _ack, _active_status
from notice_helpers import answer_warnings


def test_select_and_save_another_profile_keeps_device_active(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    original_active = snapshot.active_profile_id
    with monkeypatch.context() as scope:
        scope.setattr("controller_config.views.main_window.confirm_local_draft",
                      lambda *_: pytest.fail("profile selection prompted"))
        used = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible()).state().active
        window._activate_profile(second)
    assert vm.draft.config["active_profile"] == original_active
    # The page dot of the set the device uses keeps its ring; the bar names the set on show.
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    screens = window._mode_screens()
    assert stage.state().active == used == window._active_mode_screen(vm.model.snapshot)
    assert stage.state().viewed == screens.index(("normal", second))
    assert stage.bar.state().profile_name == vm.draft.profile(second)["name"]
    assert not stage.bar.state().profile_active
    assert stage.bar.enable.isVisible()   # 启用到设备 writes the new set and everything unsaved
    assert not any(command.name in {"VALIDATE_CONFIG", "SET_CONFIG"} for command in gateway.commands)

    window._select_physical_control("key.9")
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("另一套按键")
    window._select_physical_control("key.10")
    assert vm.draft.mapping(second, "key.9")["short_name"] == "另一套按键"
    assert vm.draft.config["active_profile"] == original_active
    assert vm.draft.config["active_profile"] == original_active


def test_explicit_enable_reviews_all_changes_and_waits_for_readback(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    window._activate_profile(second)
    vm.rename_profile(second, "会议方案")
    confirmations = []
    with monkeypatch.context() as scope:
        answer_warnings(scope, lambda _parent, title, body, *_args: confirmations.append((title, body)) or QMessageBox.Yes)
        window._enable_profile(second)
    assert confirmations[0][0] == "让设备改用“会议方案”这一套？"
    assert "会议方案" in confirmations[0][1] and "同时保存" in confirmations[0][1]
    assert "新增配置方案" in confirmations[0][1]
    validate = gateway.commands[-1]
    assert validate.name == "VALIDATE_CONFIG"
    assert validate.payload["config"]["active_profile"] == second
    assert snapshot.active_profile_id != second
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert gateway.commands[-1].name == "SET_CONFIG"
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    digest = validate.payload["digest"]
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", {
        "generation": generation, "digest": digest, "config": validate.payload["config"],
    }))
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert vm.model.snapshot.active_profile_id == second
    assert not vm.draft.is_dirty


def test_saving_another_profile_does_not_claim_it_is_running(session):
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    window._activate_profile(second)
    window._prepare_device_write(confirm_after_validation=True)
    validate = gateway.commands[-1]
    assert validate.name == "VALIDATE_CONFIG"
    assert validate.payload["config"]["active_profile"] == snapshot.active_profile_id
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    digest = validate.payload["digest"]
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", {
        "generation": generation, "digest": digest, "config": validate.payload["config"],
    }))
    assert vm.model.snapshot.active_profile_id == snapshot.active_profile_id
    window._select_physical_control("key.9")
    assert window.findChild(QLabel, "actualActionValue").text() != "此方案尚未保存到设备"
    assert "明确启用" in window.findChild(QLabel, "mappingSaveScope").text()


def test_cancel_enable_keeps_editing_selection_without_activating(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    second = vm.create_profile()
    window._activate_profile(second)
    with monkeypatch.context() as scope:
        answer_warnings(scope, lambda *_: QMessageBox.Cancel)
        window._enable_profile(second)
    assert window._editing_profile_id == second
    assert vm.draft.config["active_profile"] == snapshot.active_profile_id
    assert not any(command.name in {"VALIDATE_CONFIG", "SET_CONFIG"} for command in gateway.commands)
