import copy
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QCheckBox, QLabel

from controller_config.config_files import export_config_package, load_config_package
from controller_config.drafts import LocalDraft
from controller_config.protocol.contract import ContractError
from controller_config.transactions import (
    ConfigTransactionState, HAPTIC_OPTIONAL_CHANNELS, configs_match_readback,
)
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.i18n import LanguageManager
from controller_config.appearance import V4_STYLE
from test_session_recovery import session
from test_write_transaction import FakeWriteGateway, _ack, _active_status


def snapshot_for(contract, capability=True):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities["features"].pop("haptic_channels", None)
    if capability is not None:
        capabilities["features"]["haptic_channels"] = capability
    return replace(snapshot, capabilities=capabilities)


def editor_for(qtbot, contract, snapshot, haptic=None):
    editor = PreferencesEditor(
        lighting=snapshot.config["lighting"],
        haptic=haptic if haptic is not None else snapshot.config["haptic"],
        display=snapshot.config["display"], features=snapshot.capabilities["features"],
        under_key_control_ids=(), agent_status_control_ids=frozenset(),
        rules=contract.editor_rules,
    )
    qtbot.addWidget(editor)
    return editor


@pytest.mark.parametrize("capability", [True, False, None, 1])
def test_capability_selects_four_channels_or_legacy_without_creating_edits(qtbot, contract, capability):
    snapshot = snapshot_for(contract, capability)
    editor = editor_for(qtbot, contract, snapshot)
    assert editor.values()[1] == snapshot.config["haptic"]
    assert (editor.findChild(QCheckBox, "hapticOnEncoder") is not None) == (capability is True)
    assert (editor.findChild(QCheckBox, "hapticOnProfile") is not None) == (capability is not True)
    if capability is True:
        assert all(widget.isChecked() for widget in editor._haptic_channels.values())
    else:
        assert any("固件不支持" in label.text() for label in editor.findChildren(QLabel))


def test_master_preserves_channels_and_encoder_edit_only_changes_encoder(qtbot, contract):
    snapshot = snapshot_for(contract)
    haptic = {**snapshot.config["haptic"], "enabled": True, "on_profile": False}
    editor = editor_for(qtbot, contract, snapshot, haptic)
    editor._haptic_channels["on_encoder"].setChecked(False)
    expected = {**haptic, "on_encoder": False}
    assert editor.values()[1] == expected
    editor._haptic_enabled.setChecked(False)
    assert not editor._haptic_on_press.isEnabled()
    assert all(not widget.isEnabled() for widget in editor._haptic_channels.values())
    assert editor.values()[1] == {**expected, "enabled": False}
    editor._haptic_enabled.setChecked(True)
    assert editor.values()[1] == expected
    editor._haptic_strength.setValue(27)
    editor._display_brightness.setValue(43)
    editor._lighting_brightness.setValue(31)
    assert editor.values()[1] == {**expected, "strength": 27}
    reopened = editor_for(qtbot, contract, snapshot, editor.values()[1])
    assert reopened.values()[1] == editor.values()[1]


def test_old_press_false_is_not_copied_into_new_default_channels(qtbot, contract):
    snapshot = snapshot_for(contract)
    haptic = {**snapshot.config["haptic"], "on_press": False, "on_profile": False}
    editor = editor_for(qtbot, contract, snapshot, haptic)
    assert not editor._haptic_on_press.isChecked()
    assert all(widget.isChecked() for widget in editor._haptic_channels.values())
    assert editor.values()[1] == haptic


@pytest.mark.parametrize("field", HAPTIC_OPTIONAL_CHANNELS)
@pytest.mark.parametrize("value", [True, False, None, 0, 1, "true"])
def test_channel_schema_requires_actual_boolean(contract, field, value):
    config = copy.deepcopy(snapshot_for(contract).config)
    config["haptic"][field] = value
    if isinstance(value, bool):
        contract.validate_config(config)
    else:
        with pytest.raises(ContractError):
            contract.validate_config(config)


def test_import_export_profiles_and_mapping_keep_explicit_false(contract, tmp_path):
    snapshot = snapshot_for(contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    draft.config["haptic"].update({field: False for field in HAPTIC_OPTIONAL_CHANNELS})
    expected = copy.deepcopy(draft.config["haptic"])
    profile_id = draft.copy_profile(snapshot.active_profile_id)
    draft.set_active_profile(profile_id)
    draft.set_mapping(profile_id, "key.8", "Enter", {"type": "key", "usage": 40, "modifiers": []})
    path = tmp_path / "channels.boring-config.json"
    export_config_package(path, config=draft.config, hardware_ids=(draft.hardware_id,),
                          kind="draft", base_generation=draft.base_generation, base_digest=draft.base_digest)
    package = load_config_package(path, contract, current_hardware_id=draft.hardware_id)
    # Offline import remains portable; incompatibility is handled at device write.
    legacy = LocalDraft.from_snapshot(snapshot_for(contract, None), contract)
    legacy.replace_config(package.config, contract)
    assert legacy.config["haptic"] == expected
    assert draft.config["haptic"] == expected


@pytest.mark.parametrize("capability", [False, None])
def test_old_device_never_receives_channel_fields_but_keeps_import(contract, capability):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = snapshot_for(contract, capability)
    gateway.snapshot_ready.emit(snapshot)
    candidate = copy.deepcopy(snapshot.config)
    candidate["haptic"]["on_task"] = False
    vm.draft.replace_config(candidate, contract)
    with pytest.raises(ValueError, match="当前固件不支持"):
        vm.prepare_device_write()
    assert not gateway.commands
    assert vm.draft.config == candidate
    vm.draft.discard()
    vm.rename_profile(snapshot.active_profile_id, "Legacy edit")
    vm.prepare_device_write()
    assert not any(field in gateway.commands[-1].payload["config"]["haptic"] for field in HAPTIC_OPTIONAL_CHANNELS)


def prepare_write(contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = snapshot_for(contract)
    gateway.snapshot_ready.emit(snapshot)
    vm.draft.config["haptic"].update(on_encoder=False, on_joystick=True, on_task=True)
    vm.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    vm.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    assert vm.write_transaction.state is ConfigTransactionState.PENDING
    result = {
        "generation": snapshot.config_result["generation"] + 1,
        "digest": vm.write_transaction.candidate_digest,
        "config": copy.deepcopy(vm.draft.config),
    }
    return vm, gateway, snapshot, result


@pytest.mark.parametrize("reconnect", [False, True])
def test_readback_accepts_omitted_true_not_lost_false_and_keeps_wire_digest(contract, reconnect):
    vm, gateway, snapshot, result = prepare_write(contract)
    raw_candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    result["config"]["haptic"].pop("on_task")
    result["config"]["haptic"].pop("on_joystick")
    status = _active_status(snapshot, result["digest"], result["generation"])
    if reconnect:
        gateway.disconnected.emit("USB/BLE disconnected")
        assert vm.write_transaction.state is not ConfigTransactionState.ACTIVE
        gateway.snapshot_ready.emit(replace(snapshot, status=status, config_result=result))
    else:
        gateway.status_updated.emit(status)
        gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", result))
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert not vm.draft.is_dirty
    assert vm.draft.config["haptic"]["on_encoder"] is False
    assert raw_candidate["haptic"]["on_task"] is True
    assert vm.draft.base_digest == result["digest"]


@pytest.mark.parametrize("fault", ["false_lost", "activation_failed", "pending", "digest", "other_setting"])
def test_no_success_with_incomplete_or_mismatching_activation(contract, fault):
    vm, gateway, snapshot, result = prepare_write(contract)
    status = _active_status(snapshot, result["digest"], result["generation"])
    if fault == "false_lost":
        result["config"]["haptic"].pop("on_encoder")
    elif fault == "activation_failed":
        status["activation_failed"] = True
    elif fault == "pending":
        status["pending"] = {"digest": result["digest"], "generation": result["generation"]}
    elif fault == "digest":
        result["digest"] = "f" * 64
    else:
        result["config"]["haptic"]["strength"] += 1
    gateway.status_updated.emit(status)
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", result))
    assert vm.write_transaction.state is not ConfigTransactionState.ACTIVE
    assert vm.draft.is_dirty


def test_effective_comparison_does_not_coerce_invalid_boolean(contract):
    candidate = copy.deepcopy(snapshot_for(contract).config)
    actual = copy.deepcopy(candidate)
    actual["haptic"]["on_task"] = 1
    assert not configs_match_readback(candidate, actual)


def test_channel_edits_use_existing_page_leave_save_flow(session, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, vm, gateway, snapshot, _store = session
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities["features"]["haptic_channels"] = True
    gateway.snapshot_ready.emit(replace(snapshot, capabilities=capabilities))
    vm.navigate("lighting")
    editor = window._content.findChild(PreferencesEditor)
    editor._haptic_channels["on_task"].setChecked(False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Save)
    window._nav_buttons["settings"].click()
    assert vm.draft.config["haptic"]["on_task"] is False
    vm.navigate("lighting")
    editor = window._content.findChild(PreferencesEditor)
    assert not editor._haptic_channels["on_task"].isChecked()
    assert not any(command.name in {"VALIDATE_CONFIG", "SET_CONFIG"} for command in gateway.commands)


@pytest.mark.parametrize("language", ["zh_CN", "en_US"])
@pytest.mark.parametrize("width", [320, 460])
def test_haptic_card_translates_and_fits(qtbot, qapp, contract, tmp_path, language, width):
    manager = LanguageManager(qapp, initial_language=language, persist=False)
    editor = editor_for(qtbot, contract, snapshot_for(contract))
    card = editor._haptic_card
    editor.layout().removeWidget(card)
    card.setParent(None)
    qtbot.addWidget(card)
    card.setStyleSheet(V4_STYLE)
    manager.retranslate_widget_tree(card)
    card.resize(width, 650)
    card.show()
    qtbot.wait(20)
    assert card.width() == width
    assert card.rect().contains(card.childrenRect())
    if language == "en_US":
        assert editor._haptic_channels["on_encoder"].text() == "Knob Feedback"
        assert all("固件" not in label.text() for label in card.findChildren(QLabel))
    for checkbox in card.findChildren(QCheckBox):
        assert checkbox.width() >= checkbox.sizeHint().width()
    for label in card.findChildren(QLabel, "hapticHelp"):
        assert label.height() >= label.heightForWidth(label.width())
    assert card.grab().save(str(tmp_path / f"haptic-{language}-{width}.png"))
    manager.set_language("zh_CN")
