from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from controller_config.actions import action_definitions
from controller_config.automation import AutomationStore
from controller_config.prompt_library import PromptLibraryStore
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.workflows import WorkflowStore
from test_write_transaction import FakeWriteGateway


GESTURE = {
    "type": "key_gesture", "usage": 104, "modifiers": [],
    "double_usage": 40, "double_modifiers": [],
}


@pytest.fixture
def capability_session(qapp, contract, tmp_path):
    gateway = FakeWriteGateway()
    vm = MainViewModel(
        gateway, contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"),
    )
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    yield vm, gateway, snapshot
    vm.shutdown()


def _with_gesture(snapshot):
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities["actions"].append("key_gesture")
    return replace(snapshot, capabilities=capabilities)


@pytest.mark.parametrize("dirty", [False, True])
def test_upgrade_refreshes_cached_capabilities_without_replacing_edits(
    capability_session, contract, dirty,
):
    vm, gateway, snapshot = capability_session
    draft = vm.draft
    if dirty:
        draft.rename_profile(snapshot.active_profile_id, "尚未应用的修改")
    config = copy.deepcopy(draft.config)
    baseline = draft.confirmed_config
    base = (draft.base_generation, draft.base_digest)
    upgraded = _with_gesture(snapshot)
    upgraded.capabilities["limits"]["profiles"] = 4
    upgraded.capabilities["limits"]["config_bytes"] = 20480
    upgraded.capabilities["features"]["haptic_channels"] = False

    gateway.snapshot_ready.emit(upgraded)

    assert vm.draft is draft
    assert draft.config == config and draft.confirmed_config == baseline
    assert (draft.base_generation, draft.base_digest) == base
    assert draft.is_dirty is dirty
    assert draft.max_profiles == 4 and draft.limits["config_bytes"] == 20480
    assert draft.features["haptic_channels"] is False
    assert "key_gesture" in draft.actions
    assert "key_gesture" in {item.action_type for item in action_definitions(contract, draft.actions)}
    draft.set_mapping(snapshot.active_profile_id, "key.8", "语音", GESTURE)
    assert draft.validate(contract) == ()
    assert gateway.commands == []


def test_upgrade_keeps_dirty_write_base_when_device_generation_changed(capability_session):
    vm, gateway, snapshot = capability_session
    draft = vm.draft
    draft.rename_profile(snapshot.active_profile_id, "我的修改")
    config = copy.deepcopy(draft.config)
    baseline = draft.confirmed_config
    upgraded = _with_gesture(snapshot)
    generation = snapshot.config_result["generation"] + 1
    upgraded = replace(
        upgraded,
        config_result={**upgraded.config_result, "generation": generation},
        hello_config={**upgraded.hello_config, "generation": generation},
        status={**upgraded.status, "active": {**upgraded.status["active"], "generation": generation}},
    )

    gateway.snapshot_ready.emit(upgraded)

    assert vm.draft is draft and draft.is_dirty
    assert draft.config == config and draft.confirmed_config == baseline
    assert draft.base_generation == snapshot.config_result["generation"]
    assert draft.base_digest == snapshot.config_result["digest"]
    assert "key_gesture" in draft.actions
    assert gateway.commands == []


def test_downgrade_retains_gesture_draft_but_rejects_applying_it(capability_session, contract):
    vm, gateway, snapshot = capability_session
    gateway.snapshot_ready.emit(_with_gesture(snapshot))
    draft = vm.draft
    draft.set_mapping(snapshot.active_profile_id, "key.8", "语音", GESTURE)
    config = copy.deepcopy(draft.config)

    gateway.snapshot_ready.emit(snapshot)

    assert vm.draft is draft and draft.config == config and draft.is_dirty
    assert "key_gesture" not in draft.actions
    assert "设备不支持 key_gesture 动作" in draft.validate(contract)
    with pytest.raises(ValueError, match="不支持 key_gesture"):
        vm.prepare_device_write()
    assert gateway.commands == []


def test_reselecting_cached_device_refreshes_its_capabilities(capability_session):
    vm, gateway, snapshot = capability_session
    original = vm.draft
    other = replace(snapshot, identity={**snapshot.identity, "serial": "CP01-112233445566"})
    gateway.snapshot_ready.emit(other)
    assert vm.draft is not original

    gateway.snapshot_ready.emit(_with_gesture(snapshot))

    assert vm.draft is original and "key_gesture" in original.actions
    assert not original.is_dirty
    assert gateway.commands == []
