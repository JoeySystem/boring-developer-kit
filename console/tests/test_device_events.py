from __future__ import annotations

import pytest

from controller_config.device_events import (
    DEVICE_EVENT_SCHEMA_VERSION,
    PROMPT_TRIGGERED_EVENT,
    DeviceEvent,
    DeviceEventContractError,
    automation_manual_test_event,
    prompt_triggered_event,
)


SERIAL = "CP01-AABBCCDDEEFF"


def test_prompt_event_round_trips_one_versioned_contract() -> None:
    event = prompt_triggered_event(
        device_serial=SERIAL,
        event_id=7,
        prompt_id=2,
        prompt_name="快捷提示词",
        prompt_body="你好 BORING",
    )

    mapping = event.as_mapping()

    assert mapping == {
        "schema_version": DEVICE_EVENT_SCHEMA_VERSION,
        "kind": PROMPT_TRIGGERED_EVENT,
        "source": "usb.prompt",
        "device_serial": SERIAL,
        "event_id": 7,
        "payload": {
            "prompt_id": 2,
            "prompt_name": "快捷提示词",
            "prompt_body": "你好 BORING",
            "body_bytes": len("你好 BORING".encode("utf-8")),
        },
    }
    assert DeviceEvent.from_mapping(mapping) == event


def test_prompt_event_rejects_values_not_produced_by_current_protocol_path() -> None:
    with pytest.raises(DeviceEventContractError, match="source"):
        DeviceEvent(
            kind="prompt.triggered",
            source="hid.raw",
            device_serial=SERIAL,
            event_id=7,
            payload={
                "prompt_id": 2,
                "prompt_name": "提示词",
                "prompt_body": "你好",
                "body_bytes": 6,
            },
        )

    with pytest.raises(DeviceEventContractError, match="UTF-8"):
        DeviceEvent(
            kind="prompt.triggered",
            source="usb.prompt",
            device_serial=SERIAL,
            event_id=7,
            payload={
                "prompt_id": 2,
                "prompt_name": "提示词",
                "prompt_body": "你好",
                "body_bytes": 2,
            },
        )


def test_manual_test_uses_same_envelope_but_is_not_a_device_event() -> None:
    event = automation_manual_test_event(device_serial=SERIAL, prompt_id=3)

    assert event.as_mapping() == {
        "schema_version": 1,
        "kind": "automation.manual_test",
        "source": "console",
        "device_serial": SERIAL,
        "event_id": None,
        "payload": {"prompt_id": 3},
    }


def test_contract_rejects_unknown_event_kind_and_fields() -> None:
    with pytest.raises(DeviceEventContractError, match="不支持设备事件 kind"):
        DeviceEvent(
            kind="key.raw",
            source="usb",
            device_serial=SERIAL,
            event_id=1,
            payload={},
        )

    mapping = prompt_triggered_event(
        device_serial=SERIAL,
        event_id=1,
        prompt_id=1,
        prompt_name="一",
        prompt_body="body",
    ).as_mapping()
    mapping["timestamp"] = "not-part-of-v1"
    with pytest.raises(DeviceEventContractError, match="未知字段 timestamp"):
        DeviceEvent.from_mapping(mapping)
