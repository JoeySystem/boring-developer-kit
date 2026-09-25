from __future__ import annotations

import pytest

from controller_config.extensions.contracts import SetMappingProposal
from controller_config.extensions.proposals import (
    ExtensionProposalCoordinator,
    ProposalState,
)
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_viewmodel import FakeGateway


def _proposal(view_model: MainViewModel, contract, **changes) -> SetMappingProposal:
    draft = view_model.draft
    assert draft is not None
    payload = {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "extension_id": "com.example.prompt-tools",
        "device_serial": draft.serial,
        "base_generation": draft.base_generation,
        "base_digest": draft.base_digest,
        "profile_id": draft.config["active_profile"],
        "control_id": "key.8",
        "action": {"type": "key", "usage": 40, "modifiers": []},
    }
    payload.update(changes)
    return SetMappingProposal.from_mapping(payload, contract=contract)


def _coordinator(view_model: MainViewModel, contract, active=True):
    return ExtensionProposalCoordinator(
        view_model,
        contract=contract,
        extension_is_active=lambda _extension_id: active,
        extension_name=lambda _extension_id: "Prompt tools",
    )


def test_submit_and_approve_only_changes_local_draft(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    gateway.commands.clear()
    coordinator = _coordinator(view_model, contract)

    result = coordinator.submit(_proposal(view_model, contract))

    assert result.status == "pending"
    record = coordinator.record("proposal-1")
    assert record.state is ProposalState.REVIEWABLE
    assert record.changes
    assert gateway.commands == []

    approved = coordinator.approve("proposal-1")

    assert approved.state is ProposalState.APPROVED_TO_DRAFT
    assert view_model.draft is not None and view_model.draft.is_dirty
    assert gateway.commands == []


def test_discarding_approved_change_marks_proposal_stale_and_removable(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)
    coordinator.submit(_proposal(view_model, contract))
    coordinator.approve("proposal-1")

    view_model.discard_draft()
    coordinator.reconcile()

    record = coordinator.record("proposal-1")
    assert record.state is ProposalState.STALE
    assert "不再包含" in record.message
    coordinator.remove_record("proposal-1")
    assert coordinator.records == ()


def test_overwriting_approved_target_marks_proposal_stale(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)
    coordinator.submit(_proposal(view_model, contract))
    coordinator.approve("proposal-1")
    draft = view_model.draft
    assert draft is not None

    view_model.set_mapping(
        int(draft.config["active_profile"]),
        "key.8",
        "Other action",
        {"type": "key", "usage": 41, "modifiers": []},
    )
    coordinator.reconcile()

    assert coordinator.record("proposal-1").state is ProposalState.STALE


def test_proposal_is_blocked_by_existing_dirty_draft(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    draft = view_model.draft
    assert draft is not None
    profile_id = int(draft.config["active_profile"])
    view_model.set_mapping(
        profile_id,
        "key.2",
        "Local change",
        {"type": "key", "usage": 41, "modifiers": []},
    )
    coordinator = _coordinator(view_model, contract)

    result = coordinator.submit(_proposal(view_model, contract))

    assert result.status == "blocked"
    assert coordinator.record("proposal-1").state is ProposalState.BLOCKED

    view_model.discard_draft()
    coordinator.reconcile()

    assert coordinator.record("proposal-1").state is ProposalState.REVIEWABLE


def test_extension_can_propose_a_custom_matrix12_function_key(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)

    result = coordinator.submit(
        _proposal(view_model, contract, control_id="key.3")
    )

    assert result.status == "pending"
    assert coordinator.record("proposal-1").state is ProposalState.REVIEWABLE


def test_extension_cannot_propose_a_custom_matrix12_status_key(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)

    result = coordinator.submit(
        _proposal(view_model, contract, control_id="key.1")
    )

    assert result.status == "stale"
    assert "状态灯键" in result.message


def test_proposal_for_old_generation_is_stale(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)
    proposal = _proposal(view_model, contract, base_generation=999)

    result = coordinator.submit(proposal)

    assert result.status == "stale"
    assert gateway.commands == []


def test_inactive_extension_cannot_submit_reviewable_proposal(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract, active=False)

    result = coordinator.submit(_proposal(view_model, contract))

    assert result.status == "blocked"
    assert "未启用" in result.message


def test_terminal_proposal_record_can_be_removed(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    coordinator = _coordinator(view_model, contract)
    coordinator.submit(_proposal(view_model, contract))

    with pytest.raises(ValueError, match="已结束"):
        coordinator.remove_record("proposal-1")

    coordinator.reject("proposal-1")
    coordinator.remove_record("proposal-1")
    assert coordinator.records == ()
