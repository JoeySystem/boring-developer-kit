from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

from controller_config.prompt_library import PROMPT_SLOT_COUNT


DEVICE_EVENT_SCHEMA_VERSION = 1
HOST_ACTION_EVENT = "host_action.triggered"
HOST_ACTION_SOURCE = "usb.host_action"
PROMPT_TRIGGERED_EVENT = "prompt.triggered"
PROMPT_TRIGGERED_SOURCE = "usb.prompt"
AUTOMATION_MANUAL_TEST_EVENT = "automation.manual_test"
AUTOMATION_MANUAL_TEST_SOURCE = "console"


JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)


class DeviceEventContractError(ValueError):
    """A host event does not satisfy BORING device event contract v1."""


@dataclass(frozen=True)
class DeviceEvent:
    """Versioned semantic event shared by the event bus, scripts and extensions."""

    kind: str
    source: str
    device_serial: str
    event_id: int | None
    payload: Mapping[str, JsonValue]
    schema_version: int = DEVICE_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != DEVICE_EVENT_SCHEMA_VERSION:
            raise DeviceEventContractError(
                f"不支持设备事件 schema_version {self.schema_version}"
            )
        if not isinstance(self.device_serial, str) or not self.device_serial.strip():
            raise DeviceEventContractError("device_serial 不能为空")
        if not isinstance(self.payload, Mapping):
            raise DeviceEventContractError("event payload 必须是 object")

        if self.kind == HOST_ACTION_EVENT:
            if self.source != HOST_ACTION_SOURCE or not _is_integer(self.event_id) or self.event_id < 0:
                raise DeviceEventContractError("电脑任务事件来源或序号无效")
            payload = _exact_payload(self.payload, {"action_id", "task_token"}, HOST_ACTION_EVENT)
            if not _is_integer(payload["action_id"]) or not 1 <= payload["action_id"] <= 255:
                raise DeviceEventContractError("action_id 必须位于 1..255")
            token = payload["task_token"]
            if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
                raise DeviceEventContractError("电脑任务身份无效")
        elif self.kind == PROMPT_TRIGGERED_EVENT:
            payload = self._validate_prompt_triggered()
        elif self.kind == AUTOMATION_MANUAL_TEST_EVENT:
            payload = self._validate_manual_test()
        else:
            raise DeviceEventContractError(f"不支持设备事件 kind：{self.kind}")
        object.__setattr__(self, "payload", MappingProxyType(payload))

    @classmethod
    def from_mapping(cls, value: object) -> "DeviceEvent":
        if not isinstance(value, Mapping):
            raise DeviceEventContractError("device event 必须是 object")
        expected = {
            "schema_version",
            "kind",
            "source",
            "device_serial",
            "event_id",
            "payload",
        }
        keys = set(value)
        if keys != expected:
            missing = sorted(expected - keys)
            unknown = sorted(keys - expected)
            details = []
            if missing:
                details.append(f"缺少 {', '.join(missing)}")
            if unknown:
                details.append(f"未知字段 {', '.join(unknown)}")
            raise DeviceEventContractError("device event 字段不完整：" + "；".join(details))

        schema_version = value["schema_version"]
        kind = value["kind"]
        source = value["source"]
        device_serial = value["device_serial"]
        event_id = value["event_id"]
        payload = value["payload"]
        if not _is_integer(schema_version):
            raise DeviceEventContractError("schema_version 必须是整数")
        if not isinstance(kind, str):
            raise DeviceEventContractError("event kind 必须是字符串")
        if not isinstance(source, str):
            raise DeviceEventContractError("event source 必须是字符串")
        if not isinstance(device_serial, str):
            raise DeviceEventContractError("device_serial 必须是字符串")
        if event_id is not None and not _is_integer(event_id):
            raise DeviceEventContractError("event_id 必须是整数或 null")
        if not isinstance(payload, Mapping):
            raise DeviceEventContractError("event payload 必须是 object")
        return cls(
            kind=kind,
            source=source,
            device_serial=device_serial,
            event_id=event_id,
            payload=payload,
            schema_version=schema_version,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "source": self.source,
            "device_serial": self.device_serial,
            "event_id": self.event_id,
            "payload": dict(self.payload),
        }

    def _validate_prompt_triggered(self) -> dict[str, JsonValue]:
        if self.source != PROMPT_TRIGGERED_SOURCE:
            raise DeviceEventContractError(
                f"{PROMPT_TRIGGERED_EVENT} source 必须是 {PROMPT_TRIGGERED_SOURCE}"
            )
        if not _is_integer(self.event_id) or self.event_id < 0:
            raise DeviceEventContractError("prompt.triggered event_id 必须是非负整数")
        payload = _exact_payload(
            self.payload,
            {"prompt_id", "prompt_name", "prompt_body", "body_bytes"},
            PROMPT_TRIGGERED_EVENT,
        )
        prompt_id = payload["prompt_id"]
        prompt_name = payload["prompt_name"]
        prompt_body = payload["prompt_body"]
        body_bytes = payload["body_bytes"]
        if (
            not _is_integer(prompt_id)
            or not 1 <= prompt_id <= PROMPT_SLOT_COUNT
        ):
            raise DeviceEventContractError(
                f"prompt_id 必须位于 1..{PROMPT_SLOT_COUNT}"
            )
        if not isinstance(prompt_name, str):
            raise DeviceEventContractError("prompt_name 必须是字符串")
        if not isinstance(prompt_body, str):
            raise DeviceEventContractError("prompt_body 必须是字符串")
        if not _is_integer(body_bytes) or body_bytes < 0:
            raise DeviceEventContractError("body_bytes 必须是非负整数")
        if body_bytes != len(prompt_body.encode("utf-8")):
            raise DeviceEventContractError(
                "body_bytes 与 prompt_body UTF-8 长度不一致"
            )
        return dict(payload)

    def _validate_manual_test(self) -> dict[str, JsonValue]:
        if self.source != AUTOMATION_MANUAL_TEST_SOURCE:
            raise DeviceEventContractError(
                "automation.manual_test source 必须是 console"
            )
        if self.event_id is not None:
            raise DeviceEventContractError("automation.manual_test event_id 必须是 null")
        payload = _exact_payload(
            self.payload,
            {"prompt_id"},
            AUTOMATION_MANUAL_TEST_EVENT,
        )
        prompt_id = payload["prompt_id"]
        if (
            prompt_id is not None and (not _is_integer(prompt_id)
            or not 1 <= prompt_id <= PROMPT_SLOT_COUNT)
        ):
            raise DeviceEventContractError(
                f"prompt_id 必须位于 1..{PROMPT_SLOT_COUNT}"
            )
        return dict(payload)


def prompt_triggered_event(
    *,
    device_serial: str,
    event_id: int,
    prompt_id: int,
    prompt_name: str,
    prompt_body: str,
) -> DeviceEvent:
    return DeviceEvent(
        kind=PROMPT_TRIGGERED_EVENT,
        source=PROMPT_TRIGGERED_SOURCE,
        device_serial=device_serial,
        event_id=event_id,
        payload={
            "prompt_id": prompt_id,
            "prompt_name": prompt_name,
            "prompt_body": prompt_body,
            "body_bytes": len(prompt_body.encode("utf-8")),
        },
    )


def automation_manual_test_event(
    *, device_serial: str, prompt_id: int | None
) -> DeviceEvent:
    return DeviceEvent(
        kind=AUTOMATION_MANUAL_TEST_EVENT,
        source=AUTOMATION_MANUAL_TEST_SOURCE,
        device_serial=device_serial,
        event_id=None,
        payload={"prompt_id": prompt_id},
    )


def _exact_payload(
    value: Mapping[str, JsonValue], expected: set[str], label: str
) -> dict[str, JsonValue]:
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        details = []
        if missing:
            details.append(f"缺少 {', '.join(missing)}")
        if unknown:
            details.append(f"未知字段 {', '.join(unknown)}")
        raise DeviceEventContractError(
            f"{label} payload 字段不完整：" + "；".join(details)
        )
    return dict(value)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
