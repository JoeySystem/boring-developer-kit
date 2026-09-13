from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSaveFile

from controller_config.drafts import LocalDraft
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.update_recovery import UpdateRecoveryError, UpdateRecoveryStore
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway


@pytest.fixture
def recovery(tmp_path, contract):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, trust=DeviceTrust(
        DeviceTrustState.AUTHENTICATED, "已认证", "test",
        serial=snapshot.identity["serial"],
    ))
    draft = LocalDraft.from_snapshot(snapshot, contract)
    draft.rename_profile(snapshot.active_profile_id, "更新前的方案")
    workspace = {
        "page": "mapping", "selected_control": "key.9",
        "editor": {"short_name": "未保存", "action": {"type": "key", "usage": 17, "modifiers": [227]}},
    }
    store = UpdateRecoveryStore(tmp_path / "update-workspace.json")
    store.save(snapshot, draft, workspace)
    return store, snapshot, draft, workspace


def _connected(contract, snapshot):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(snapshot)
    gateway.commands.clear()
    return vm, gateway


def test_same_device_recovers_complete_draft_without_device_commands(qapp, contract, recovery):
    store, snapshot, original, workspace = recovery
    vm, gateway = _connected(contract, snapshot)
    changed = []
    vm.changed.connect(changed.append)

    saved = store.load()
    assert saved["workspace"] == workspace
    assert store.apply(vm, saved)
    assert vm.draft.config == original.config
    assert vm.draft.confirmed_config == original.confirmed_config
    assert vm.draft.is_dirty
    assert changed
    assert gateway.commands == []
    assert store.path.exists()  # Parent has not restored the editor yet.
    assert store.apply(vm, saved)  # Retry after an interrupted UI restore.
    store.clear()
    assert store.load() is None


@pytest.mark.parametrize("reason", ["different_serial", "different_hardware", "unauthenticated", "disconnected"])
def test_recovery_waits_for_authenticated_original_device(qapp, contract, recovery, reason):
    store, snapshot, _, _ = recovery
    if reason == "different_serial":
        snapshot = replace(snapshot, identity={**snapshot.identity, "serial": "ANOTHER-DEVICE"})
    elif reason == "different_hardware":
        snapshot = replace(snapshot, identity={**snapshot.identity, "hardware_id": "ANOTHER-HARDWARE"})
    elif reason == "unauthenticated":
        snapshot = replace(snapshot, trust=replace(snapshot.trust, state=DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED))
    vm, gateway = _connected(contract, snapshot)
    if reason == "disconnected":
        gateway.disconnected.emit("test disconnect")
    before = copy.deepcopy(vm.draft.config)
    gateway.commands.clear()
    assert not store.apply(vm, store.load())
    assert store.last_error
    assert vm.draft.config == before
    assert gateway.commands == []
    assert store.path.exists()


def test_changed_device_configuration_keeps_recovery_for_manual_review(qapp, contract, recovery):
    store, snapshot, _, _ = recovery
    config = copy.deepcopy(snapshot.config)
    config["profiles"][0]["name"] = "设备端的新配置"
    snapshot = replace(snapshot, config_result={**snapshot.config_result, "config": config})
    vm, gateway = _connected(contract, snapshot)
    assert not store.apply(vm, store.load())
    assert "配置已变化" in store.last_error
    assert vm.draft.config == config
    assert gateway.commands == []
    assert store.path.exists()


def test_new_local_edits_are_not_overwritten(qapp, contract, recovery):
    store, snapshot, _, _ = recovery
    vm, gateway = _connected(contract, snapshot)
    vm.rename_profile(snapshot.active_profile_id, "重启后的新修改")
    before = copy.deepcopy(vm.draft.config)
    assert not store.apply(vm, store.load())
    assert "新的本地修改" in store.last_error
    assert vm.draft.config == before
    assert gateway.commands == []


def test_candidate_schema_is_validated_before_restore(qapp, contract, recovery):
    store, snapshot, _, _ = recovery
    vm, gateway = _connected(contract, snapshot)
    saved = store.load()
    saved["draft"]["candidate_config"]["profiles"][0]["mappings"][0]["action"] = {"type": "not-supported"}
    assert not store.apply(vm, saved)
    assert "无法恢复" in store.last_error
    assert not vm.draft.is_dirty
    assert gateway.commands == []
    assert store.path.exists()


@pytest.mark.parametrize("content", ["{broken", "[]", '{"device":null,"draft":{},"workspace":{}}'])
def test_malformed_recovery_file_is_reported_and_retained(tmp_path, content):
    path = tmp_path / "update-workspace.json"
    path.write_text(content)
    with pytest.raises(UpdateRecoveryError):
        UpdateRecoveryStore(path).load()
    assert path.read_text() == content


def test_failed_atomic_commit_preserves_previous_workspace(recovery, monkeypatch):
    store, snapshot, draft, _ = recovery
    before = store.path.read_bytes()

    class FailedCommit(QSaveFile):
        def commit(self):
            self.cancelWriting()
            return False

    monkeypatch.setattr("controller_config.update_recovery.QSaveFile", FailedCommit)
    with pytest.raises(UpdateRecoveryError):
        store.save(snapshot, draft, {"page": "settings"})
    assert store.path.read_bytes() == before


def test_workspace_without_device_survives_update(tmp_path):
    store = UpdateRecoveryStore(tmp_path / "update-workspace.json")
    store.save(None, None, {"page": "settings"})
    assert store.load() == {"device": None, "draft": None, "workspace": {"page": "settings"}}


def test_offline_draft_uses_its_own_device_identity(recovery):
    store, _, draft, workspace = recovery
    store.save(None, draft, workspace)
    assert store.load()["device"] == {"serial": draft.serial, "hardware_id": draft.hardware_id}
