from __future__ import annotations

import json

import pytest

from controller_config.automation import DeviceEvent
from controller_config.extensions.contracts import (
    ActionInvocation,
    ActionInvocationResult,
    ApiHandshakeRequest,
    ApiHandshakeResult,
    ExtensionContext,
    ExtensionContractError,
    ExtensionManifest,
    SemanticEvent,
    SetMappingProposal,
    SetMappingProposalResult,
)


SERIAL = "CP01-AABBCCDDEEFF"
DIGEST = "a" * 64


def _manifest_payload() -> dict[str, object]:
    return {
        "manifest_version": 1,
        "id": "com.example.prompt-tools",
        "name": "Prompt tools",
        "version": "1.0.0",
        "api_version": {"major": 1, "minor": 0},
        "entrypoint": "src/main.py",
        "observer_events": ["prompt.triggered"],
        "actions": [{"id": "use_prompt", "name": "Use prompt"}],
    }


def _event_payload() -> dict[str, object]:
    body = "你好 BORING"
    return {
        "schema_version": 1,
        "kind": "prompt.triggered",
        "source": "usb.prompt",
        "device_serial": SERIAL,
        "event_id": 7,
        "payload": {
            "prompt_id": 2,
            "prompt_name": "快捷提示词",
            "prompt_body": body,
            "body_bytes": len(body.encode("utf-8")),
        },
    }


def _context_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": 4,
        "device_serial": SERIAL,
        "connection_state": "ready",
        "identity": {
            "product_id": "wired-macro-pad-v1",
            "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
            "serial": SERIAL,
        },
        "compatibility": {"read": True, "write": True, "reason": ""},
        "capabilities": {
            "controls": ["key.1", "joystick.up"],
            "actions": ["key", "prompt"],
            "limits": {"profiles": 8},
            "features": {"firmware_update": True, "prompt_storage": True},
        },
        "status": {"state": "active", "operating_mode": "normal"},
        "active_profile": {"id": 0, "name": "Default"},
        "config_summary": {
            "schema_version": 1,
            "generation": 6,
            "digest": DIGEST,
            "profile_count": 1,
        },
        "draft": {"dirty": False, "change_count": 0},
        "prompt_listener": {"state": "online", "online": True},
    }


def _proposal_payload(action: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposal_id": "proposal-1",
        "extension_id": "com.example.prompt-tools",
        "device_serial": SERIAL,
        "base_generation": 6,
        "base_digest": DIGEST,
        "profile_id": 0,
        "control_id": "key.12",
        "action": action or {"type": "key", "usage": 40, "modifiers": []},
    }


@pytest.mark.parametrize(
    ("factory", "payload"),
    [
        (ExtensionManifest.from_mapping, _manifest_payload()),
        (
            ApiHandshakeRequest.from_mapping,
            {
                "request_id": "request-1",
                "kind": "handshake",
                "extension_id": "com.example.prompt-tools",
                "api_version": {"major": 1, "minor": 0},
            },
        ),
        (
            ApiHandshakeResult.from_mapping,
            {
                "request_id": "request-1",
                "kind": "handshake.result",
                "api_version": {"major": 1, "minor": 0},
                "accepted": True,
                "message": "ready",
            },
        ),
        (ExtensionContext.from_mapping, _context_payload()),
        (SemanticEvent.from_mapping, _event_payload()),
        (
            ActionInvocationResult.from_mapping,
            {
                "schema_version": 1,
                "invocation_id": "invocation-1",
                "status": "accepted",
                "message": "accepted",
            },
        ),
        (
            SetMappingProposalResult.from_mapping,
            {
                "schema_version": 1,
                "proposal_id": "proposal-1",
                "status": "pending",
                "message": "waiting for review",
            },
        ),
    ],
)
def test_public_values_round_trip_as_utf8_json(factory, payload) -> None:
    parsed = factory(payload)

    encoded = json.dumps(parsed.as_mapping(), ensure_ascii=False, allow_nan=False)

    assert json.loads(encoded) == payload


def test_manifest_rejects_unknown_api_major_and_invalid_entrypoint() -> None:
    unknown_api = _manifest_payload()
    unknown_api["api_version"] = {"major": 2, "minor": 0}
    with pytest.raises(ExtensionContractError, match="API major 2"):
        ExtensionManifest.from_mapping(unknown_api)

    escaped_entry = _manifest_payload()
    escaped_entry["entrypoint"] = "../main.py"
    with pytest.raises(ExtensionContractError, match="relative|\u76f8\u5bf9"):
        ExtensionManifest.from_mapping(escaped_entry)


def test_manifest_rejects_duplicate_or_unknown_declarations() -> None:
    duplicate = _manifest_payload()
    duplicate["actions"] = [
        {"id": "use_prompt", "name": "One"},
        {"id": "use_prompt", "name": "Two"},
    ]
    with pytest.raises(ExtensionContractError, match="action id.*\u91cd\u590d"):
        ExtensionManifest.from_mapping(duplicate)

    unknown_event = _manifest_payload()
    unknown_event["observer_events"] = ["device.raw"]
    with pytest.raises(ExtensionContractError, match="observer event"):
        ExtensionManifest.from_mapping(unknown_event)


def test_action_invocation_requires_manifest_declaration_and_matching_device() -> None:
    manifest = ExtensionManifest.from_mapping(_manifest_payload())
    payload = {
        "schema_version": 1,
        "invocation_id": "invocation-1",
        "extension_id": manifest.extension_id,
        "action_id": "use_prompt",
        "device_serial": SERIAL,
        "context_revision": 4,
        "event": _event_payload(),
    }
    invocation = ActionInvocation.from_mapping(payload, manifest=manifest)
    assert invocation.as_mapping() == payload

    payload["action_id"] = "not_declared"
    with pytest.raises(ExtensionContractError, match="\u672a在 manifest"):
        ActionInvocation.from_mapping(payload, manifest=manifest)

    payload["action_id"] = "use_prompt"
    payload["device_serial"] = "CP01-112233445566"
    with pytest.raises(ExtensionContractError, match="\u5e8f列号"):
        ActionInvocation.from_mapping(payload, manifest=manifest)


def test_semantic_event_integrates_with_existing_device_event_value() -> None:
    existing = DeviceEvent(
        kind="prompt.triggered",
        source="usb.prompt",
        device_serial=SERIAL,
        event_id=7,
        payload=dict(_event_payload()["payload"]),
    )

    public = SemanticEvent.from_mapping(
        {"schema_version": 1, **existing.as_mapping()}
    )

    assert public.as_mapping() == _event_payload()


def test_context_is_deeply_immutable_and_serialization_returns_a_copy() -> None:
    payload = _context_payload()
    context = ExtensionContext.from_mapping(payload)

    with pytest.raises(TypeError):
        context.capabilities["actions"] = []  # type: ignore[index]
    exported = context.as_mapping()
    exported["capabilities"]["actions"].append("mouse")  # type: ignore[index,union-attr]

    assert context.as_mapping() == payload


@pytest.mark.parametrize(
    "field",
    [
        "raw_command",
        "firmware",
        "firmware_command",
        "firmware_image",
        "serial_port",
        "gateway",
    ],
)
def test_public_objects_reject_raw_device_and_firmware_access_fields(field) -> None:
    payload = _context_payload()
    payload["status"][field] = "not-public"  # type: ignore[index]

    with pytest.raises(ExtensionContractError, match=field):
        ExtensionContext.from_mapping(payload)


def test_set_mapping_proposal_reuses_authoritative_action_schema(contract) -> None:
    payload = _proposal_payload()
    proposal = SetMappingProposal.from_mapping(payload, contract=contract)

    assert proposal.as_mapping() == payload

    invalid_action = _proposal_payload({"type": "key", "usage": 999})
    with pytest.raises(ExtensionContractError, match="Schema"):
        SetMappingProposal.from_mapping(invalid_action, contract=contract)

    raw_action = _proposal_payload(
        {"type": "key", "usage": 40, "raw_command": "SET_CONFIG"}
    )
    with pytest.raises(ExtensionContractError, match="raw_command"):
        SetMappingProposal.from_mapping(raw_action, contract=contract)


def test_set_mapping_proposal_rejects_unknown_control_and_stale_shape(contract) -> None:
    unknown_control = _proposal_payload()
    unknown_control["control_id"] = "key.99"
    with pytest.raises(ExtensionContractError, match="control_id"):
        SetMappingProposal.from_mapping(unknown_control, contract=contract)

    missing_base = _proposal_payload()
    del missing_base["base_digest"]
    with pytest.raises(ExtensionContractError, match="base_digest"):
        SetMappingProposal.from_mapping(missing_base, contract=contract)


def test_strict_objects_reject_unknown_fields() -> None:
    payload = _manifest_payload()
    payload["download_url"] = "https://example.invalid/extension.zip"

    with pytest.raises(ExtensionContractError, match="download_url"):
        ExtensionManifest.from_mapping(payload)


def test_host_task_event_requires_api_11_without_prompt_fields():
    manifest_payload = _manifest_payload()
    manifest_payload['observer_events'] = ['host_action.triggered']
    with pytest.raises(ExtensionContractError, match='1.1'):
        ExtensionManifest.from_mapping(manifest_payload)
    manifest_payload['api_version']['minor'] = 1
    manifest = ExtensionManifest.from_mapping(manifest_payload)
    event = {
        'schema_version': 1, 'kind': 'host_action.triggered', 'source': 'usb.host_action',
        'device_serial': SERIAL, 'event_id': 9, 'payload': {'action_id': 255, 'task_token': '0123456789abcdef0123456789abcdef'},
    }
    value = {
        'schema_version': 1, 'invocation_id': 'host-1', 'extension_id': manifest.extension_id,
        'action_id': 'use_prompt', 'device_serial': SERIAL, 'context_revision': 1, 'event': event,
    }
    assert ActionInvocation.from_mapping(value, manifest=manifest).event.as_mapping() == event
    old_manifest = ExtensionManifest.from_mapping(_manifest_payload())
    with pytest.raises(ExtensionContractError, match='1.1'):
        ActionInvocation.from_mapping(value, manifest=old_manifest)
    event['payload']['prompt_id'] = 1
    with pytest.raises(ExtensionContractError):
        SemanticEvent.from_mapping(event)
