from __future__ import annotations

from controller_config.extensions.context import build_extension_context
from controller_config.models import AppState, ScreenModel
from controller_config.prompt_device import PromptListenerStatus
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.drafts import LocalDraft


def test_context_without_device_contains_no_transport_details() -> None:
    context = build_extension_context(
        model=ScreenModel(AppState.NO_DEVICE, "none"),
        draft=None,
        listener=PromptListenerStatus(),
        revision=3,
    ).as_mapping()

    assert context["device_serial"] is None
    assert context["connection_state"] == "no_device"
    assert context["identity"] == {}
    assert context["draft"] == {
        "available": False,
        "dirty": False,
        "change_count": 0,
    }
    assert "port_name" not in context


def test_context_copies_authoritative_snapshot_and_draft_summary(contract) -> None:
    snapshot = _power_v2_snapshot(contract, read_only=False)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    context = build_extension_context(
        model=ScreenModel(AppState.READY, snapshot=snapshot),
        draft=draft,
        listener=PromptListenerStatus(),
        revision=5,
    ).as_mapping()

    assert context["revision"] == 5
    assert context["device_serial"] == snapshot.identity["serial"]
    assert context["config_summary"]["generation"] == snapshot.config_result["generation"]
    assert context["active_profile"]["id"] == snapshot.active_profile_id
    assert context["draft"]["dirty"] is False
    assert "port_name" not in context

    context["identity"]["serial"] = "changed"
    assert snapshot.identity["serial"] != "changed"
