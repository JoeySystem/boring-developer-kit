from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import TypeAlias

from controller_config.device_events import (
    DEVICE_EVENT_SCHEMA_VERSION,
    HOST_ACTION_EVENT,
    PROMPT_TRIGGERED_EVENT,
    DeviceEvent,
    DeviceEventContractError,
)
from controller_config.protocol.contract import Contract, ContractError


API_MAJOR = 1
API_MINOR = 1
PUBLIC_SCHEMA_VERSION = DEVICE_EVENT_SCHEMA_VERSION
PROMPT_EVENT_KIND = PROMPT_TRIGGERED_EVENT


class ExtensionContractError(ValueError):
    """One public extension value does not satisfy BORING local API v1."""


JsonValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["JsonValue", ...]
    | Mapping[str, "JsonValue"]
)


_EXTENSION_ID = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)+")
_ACTION_ID = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN_PUBLIC_KEYS = {
    "command",
    "raw_command",
    "protocol_command",
    "gateway",
    "serial_gateway",
    "serial_port",
    "port_name",
    "firmware",
    "firmware_command",
    "firmware_image",
    "firmware_install",
    "firmware_package",
    "install_firmware",
}


@dataclass(frozen=True)
class ApiVersion:
    major: int = API_MAJOR
    minor: int = API_MINOR

    @classmethod
    def from_mapping(cls, value: object) -> "ApiVersion":
        raw = _strict_object(value, "api_version", {"major", "minor"})
        major = _integer(raw["major"], "api_version.major", minimum=0)
        minor = _integer(raw["minor"], "api_version.minor", minimum=0)
        if major != API_MAJOR:
            raise ExtensionContractError(
                f"不支持扩展 API major {major}；当前只支持 {API_MAJOR}"
            )
        if minor > API_MINOR:
            raise ExtensionContractError(
                f"不支持扩展 API {major}.{minor}；当前最高为 "
                f"{API_MAJOR}.{API_MINOR}"
            )
        return cls(major, minor)

    def as_mapping(self) -> dict[str, int]:
        return {"major": self.major, "minor": self.minor}


@dataclass(frozen=True)
class ExtensionActionDeclaration:
    action_id: str
    name: str

    @classmethod
    def from_mapping(cls, value: object) -> "ExtensionActionDeclaration":
        raw = _strict_object(value, "manifest.actions[]", {"id", "name"})
        action_id = _identifier(raw["id"], "manifest action id", _ACTION_ID)
        name = _non_empty_string(raw["name"], "manifest action name")
        return cls(action_id, name)

    def as_mapping(self) -> dict[str, str]:
        return {"id": self.action_id, "name": self.name}


@dataclass(frozen=True)
class ExtensionManifest:
    extension_id: str
    name: str
    version: str
    api_version: ApiVersion
    entrypoint: str
    observer_events: tuple[str, ...]
    actions: tuple[ExtensionActionDeclaration, ...]
    manifest_version: int = 1

    @classmethod
    def from_mapping(cls, value: object) -> "ExtensionManifest":
        raw = _strict_object(
            value,
            "extension manifest",
            {
                "manifest_version",
                "id",
                "name",
                "version",
                "api_version",
                "entrypoint",
                "observer_events",
                "actions",
            },
        )
        manifest_version = _integer(
            raw["manifest_version"], "manifest_version", minimum=1
        )
        if manifest_version != 1:
            raise ExtensionContractError(
                f"不支持扩展 manifest_version {manifest_version}"
            )
        extension_id = _identifier(raw["id"], "manifest id", _EXTENSION_ID)
        name = _non_empty_string(raw["name"], "manifest name")
        version = _non_empty_string(raw["version"], "manifest version")
        api_version = ApiVersion.from_mapping(raw["api_version"])
        entrypoint = _entrypoint(raw["entrypoint"])
        observer_events = _string_tuple(
            raw["observer_events"], "manifest observer_events"
        )
        unsupported_events = set(observer_events) - {PROMPT_EVENT_KIND, HOST_ACTION_EVENT}
        if unsupported_events:
            raise ExtensionContractError(
                "manifest 声明了不支持的 observer event："
                + ", ".join(sorted(unsupported_events))
            )
        if HOST_ACTION_EVENT in observer_events and api_version.minor < 1:
            raise ExtensionContractError("host_action.triggered 需要扩展 API 1.1")
        action_values = _array(raw["actions"], "manifest actions")
        actions = tuple(
            ExtensionActionDeclaration.from_mapping(item) for item in action_values
        )
        _require_unique((action.action_id for action in actions), "manifest action id")
        _require_unique(observer_events, "manifest observer event")
        return cls(
            extension_id=extension_id,
            name=name,
            version=version,
            api_version=api_version,
            entrypoint=entrypoint,
            observer_events=observer_events,
            actions=actions,
            manifest_version=manifest_version,
        )

    @property
    def declared_action_ids(self) -> frozenset[str]:
        return frozenset(action.action_id for action in self.actions)

    def as_mapping(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "id": self.extension_id,
            "name": self.name,
            "version": self.version,
            "api_version": self.api_version.as_mapping(),
            "entrypoint": self.entrypoint,
            "observer_events": list(self.observer_events),
            "actions": [action.as_mapping() for action in self.actions],
        }


@dataclass(frozen=True)
class ApiHandshakeRequest:
    request_id: str
    extension_id: str
    api_version: ApiVersion

    @classmethod
    def from_mapping(cls, value: object) -> "ApiHandshakeRequest":
        raw = _strict_object(
            value,
            "handshake request",
            {"request_id", "kind", "extension_id", "api_version"},
        )
        _constant(raw["kind"], "handshake", "handshake request kind")
        return cls(
            request_id=_non_empty_string(raw["request_id"], "request_id"),
            extension_id=_identifier(
                raw["extension_id"], "extension_id", _EXTENSION_ID
            ),
            api_version=ApiVersion.from_mapping(raw["api_version"]),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": "handshake",
            "extension_id": self.extension_id,
            "api_version": self.api_version.as_mapping(),
        }


@dataclass(frozen=True)
class ApiHandshakeResult:
    request_id: str
    api_version: ApiVersion
    accepted: bool
    message: str

    @classmethod
    def from_mapping(cls, value: object) -> "ApiHandshakeResult":
        raw = _strict_object(
            value,
            "handshake result",
            {"request_id", "kind", "api_version", "accepted", "message"},
        )
        _constant(raw["kind"], "handshake.result", "handshake result kind")
        return cls(
            request_id=_non_empty_string(raw["request_id"], "request_id"),
            api_version=ApiVersion.from_mapping(raw["api_version"]),
            accepted=_boolean(raw["accepted"], "handshake accepted"),
            message=_string(raw["message"], "handshake message"),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "kind": "handshake.result",
            "api_version": self.api_version.as_mapping(),
            "accepted": self.accepted,
            "message": self.message,
        }


@dataclass(frozen=True)
class ExtensionContext:
    revision: int
    device_serial: str | None
    connection_state: str
    identity: Mapping[str, JsonValue]
    compatibility: Mapping[str, JsonValue]
    capabilities: Mapping[str, JsonValue]
    status: Mapping[str, JsonValue]
    active_profile: Mapping[str, JsonValue] | None
    config_summary: Mapping[str, JsonValue]
    draft: Mapping[str, JsonValue]
    prompt_listener: Mapping[str, JsonValue]
    schema_version: int = PUBLIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PUBLIC_SCHEMA_VERSION:
            raise ExtensionContractError(
                f"不支持 context schema_version {self.schema_version}"
            )
        _integer(self.revision, "context revision", minimum=0)
        if self.device_serial is not None:
            _non_empty_string(self.device_serial, "context device_serial")
        _non_empty_string(self.connection_state, "context connection_state")
        for field_name in (
            "identity",
            "compatibility",
            "capabilities",
            "status",
            "config_summary",
            "draft",
            "prompt_listener",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_object(getattr(self, field_name), f"context {field_name}"),
            )
        if self.active_profile is not None:
            object.__setattr__(
                self,
                "active_profile",
                _freeze_object(self.active_profile, "context active_profile"),
            )

    @classmethod
    def from_mapping(cls, value: object) -> "ExtensionContext":
        raw = _strict_object(
            value,
            "extension context",
            {
                "schema_version",
                "revision",
                "device_serial",
                "connection_state",
                "identity",
                "compatibility",
                "capabilities",
                "status",
                "active_profile",
                "config_summary",
                "draft",
                "prompt_listener",
            },
        )
        schema_version = _schema_version(raw["schema_version"], "context")
        device_serial = raw["device_serial"]
        if device_serial is not None:
            device_serial = _non_empty_string(device_serial, "context device_serial")
        active_profile = raw["active_profile"]
        if active_profile is not None and not isinstance(active_profile, Mapping):
            raise ExtensionContractError("context active_profile 必须是 object 或 null")
        return cls(
            schema_version=schema_version,
            revision=_integer(raw["revision"], "context revision", minimum=0),
            device_serial=device_serial,
            connection_state=_non_empty_string(
                raw["connection_state"], "context connection_state"
            ),
            identity=_mapping(raw["identity"], "context identity"),
            compatibility=_mapping(
                raw["compatibility"], "context compatibility"
            ),
            capabilities=_mapping(raw["capabilities"], "context capabilities"),
            status=_mapping(raw["status"], "context status"),
            active_profile=active_profile,
            config_summary=_mapping(
                raw["config_summary"], "context config_summary"
            ),
            draft=_mapping(raw["draft"], "context draft"),
            prompt_listener=_mapping(
                raw["prompt_listener"], "context prompt_listener"
            ),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "device_serial": self.device_serial,
            "connection_state": self.connection_state,
            "identity": _thaw(self.identity),
            "compatibility": _thaw(self.compatibility),
            "capabilities": _thaw(self.capabilities),
            "status": _thaw(self.status),
            "active_profile": (
                None if self.active_profile is None else _thaw(self.active_profile)
            ),
            "config_summary": _thaw(self.config_summary),
            "draft": _thaw(self.draft),
            "prompt_listener": _thaw(self.prompt_listener),
        }


@dataclass(frozen=True)
class SemanticEvent:
    kind: str
    source: str
    device_serial: str
    event_id: int
    payload: Mapping[str, JsonValue]
    schema_version: int = PUBLIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            event = DeviceEvent(
                schema_version=self.schema_version,
                kind=self.kind,
                source=self.source,
                device_serial=self.device_serial,
                event_id=self.event_id,
                payload=self.payload,
            )
        except DeviceEventContractError as exc:
            raise ExtensionContractError(str(exc)) from exc
        object.__setattr__(
            self,
            "payload",
            _freeze_object(dict(event.payload), "event payload"),
        )

    @classmethod
    def from_mapping(cls, value: object) -> "SemanticEvent":
        try:
            event = DeviceEvent.from_mapping(value)
        except DeviceEventContractError as exc:
            raise ExtensionContractError(str(exc)) from exc
        return cls(
            schema_version=event.schema_version,
            kind=event.kind,
            source=event.source,
            device_serial=event.device_serial,
            event_id=event.event_id,
            payload=event.payload,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "source": self.source,
            "device_serial": self.device_serial,
            "event_id": self.event_id,
            "payload": _thaw(self.payload),
        }


@dataclass(frozen=True)
class ActionInvocation:
    invocation_id: str
    extension_id: str
    action_id: str
    device_serial: str
    context_revision: int
    event: SemanticEvent
    schema_version: int = PUBLIC_SCHEMA_VERSION

    @classmethod
    def from_mapping(
        cls, value: object, *, manifest: ExtensionManifest
    ) -> "ActionInvocation":
        raw = _strict_object(
            value,
            "action invocation",
            {
                "schema_version",
                "invocation_id",
                "extension_id",
                "action_id",
                "device_serial",
                "context_revision",
                "event",
            },
        )
        extension_id = _identifier(
            raw["extension_id"], "invocation extension_id", _EXTENSION_ID
        )
        if extension_id != manifest.extension_id:
            raise ExtensionContractError("action invocation 与 manifest 扩展 ID 不一致")
        action_id = _identifier(raw["action_id"], "invocation action_id", _ACTION_ID)
        if action_id not in manifest.declared_action_ids:
            raise ExtensionContractError(
                f"action {action_id} 未在 manifest 中声明"
            )
        event = SemanticEvent.from_mapping(raw["event"])
        if event.kind == HOST_ACTION_EVENT and manifest.api_version.minor < 1:
            raise ExtensionContractError("独立电脑任务需要扩展 API 1.1")
        device_serial = _non_empty_string(
            raw["device_serial"], "invocation device_serial"
        )
        if event.device_serial != device_serial:
            raise ExtensionContractError("action invocation 与 event 的设备序列号不一致")
        return cls(
            schema_version=_schema_version(raw["schema_version"], "action invocation"),
            invocation_id=_non_empty_string(
                raw["invocation_id"], "invocation_id"
            ),
            extension_id=extension_id,
            action_id=action_id,
            device_serial=device_serial,
            context_revision=_integer(
                raw["context_revision"], "context_revision", minimum=0
            ),
            event=event,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "extension_id": self.extension_id,
            "action_id": self.action_id,
            "device_serial": self.device_serial,
            "context_revision": self.context_revision,
            "event": self.event.as_mapping(),
        }


@dataclass(frozen=True)
class ActionInvocationResult:
    invocation_id: str
    status: str
    message: str
    schema_version: int = PUBLIC_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: object) -> "ActionInvocationResult":
        raw = _strict_object(
            value,
            "action invocation result",
            {"schema_version", "invocation_id", "status", "message"},
        )
        status = _string(raw["status"], "action result status")
        if status not in {"accepted", "rejected", "completed", "failed"}:
            raise ExtensionContractError(f"action result status {status} 不受支持")
        return cls(
            schema_version=_schema_version(raw["schema_version"], "action result"),
            invocation_id=_non_empty_string(
                raw["invocation_id"], "invocation_id"
            ),
            status=status,
            message=_string(raw["message"], "action result message"),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "invocation_id": self.invocation_id,
            "status": self.status,
            "message": self.message,
        }


@dataclass(frozen=True)
class SetMappingProposal:
    proposal_id: str
    extension_id: str
    device_serial: str
    base_generation: int
    base_digest: str
    profile_id: int
    control_id: str
    action: Mapping[str, JsonValue]
    schema_version: int = PUBLIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "action", _freeze_object(self.action, "set_mapping action")
        )

    @classmethod
    def from_mapping(
        cls, value: object, *, contract: Contract
    ) -> "SetMappingProposal":
        raw = _strict_object(
            value,
            "set_mapping proposal",
            {
                "schema_version",
                "proposal_id",
                "extension_id",
                "device_serial",
                "base_generation",
                "base_digest",
                "profile_id",
                "control_id",
                "action",
            },
        )
        action = _plain_json_object(raw["action"], "set_mapping action")
        try:
            contract.validate_action(action)
        except ContractError as exc:
            raise ExtensionContractError(str(exc)) from exc
        control_id = _non_empty_string(raw["control_id"], "control_id")
        try:
            allowed_controls = contract.config_schema["$defs"]["control_id"]["enum"]
        except (KeyError, TypeError) as exc:
            raise ExtensionContractError("当前 Schema 缺少 control_id 定义") from exc
        if control_id not in allowed_controls:
            raise ExtensionContractError(f"control_id {control_id} 不在当前 Schema 中")
        base_digest = _non_empty_string(raw["base_digest"], "base_digest")
        if _DIGEST.fullmatch(base_digest) is None:
            raise ExtensionContractError("base_digest 必须是 64 位小写十六进制")
        return cls(
            schema_version=_schema_version(raw["schema_version"], "set_mapping"),
            proposal_id=_non_empty_string(raw["proposal_id"], "proposal_id"),
            extension_id=_identifier(
                raw["extension_id"], "extension_id", _EXTENSION_ID
            ),
            device_serial=_non_empty_string(
                raw["device_serial"], "device_serial"
            ),
            base_generation=_integer(
                raw["base_generation"], "base_generation", minimum=0
            ),
            base_digest=base_digest,
            profile_id=_integer(raw["profile_id"], "profile_id", minimum=0, maximum=7),
            control_id=control_id,
            action=action,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "extension_id": self.extension_id,
            "device_serial": self.device_serial,
            "base_generation": self.base_generation,
            "base_digest": self.base_digest,
            "profile_id": self.profile_id,
            "control_id": self.control_id,
            "action": _thaw(self.action),
        }


@dataclass(frozen=True)
class SetMappingProposalResult:
    proposal_id: str
    status: str
    message: str
    schema_version: int = PUBLIC_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: object) -> "SetMappingProposalResult":
        raw = _strict_object(
            value,
            "set_mapping proposal result",
            {"schema_version", "proposal_id", "status", "message"},
        )
        status = _string(raw["status"], "proposal result status")
        if status not in {"pending", "approved", "rejected", "blocked", "stale"}:
            raise ExtensionContractError(
                f"set_mapping proposal result status {status} 不受支持"
            )
        return cls(
            schema_version=_schema_version(raw["schema_version"], "proposal result"),
            proposal_id=_non_empty_string(raw["proposal_id"], "proposal_id"),
            status=status,
            message=_string(raw["message"], "proposal result message"),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "proposal_id": self.proposal_id,
            "status": self.status,
            "message": self.message,
        }


def _strict_object(
    value: object, label: str, expected_fields: set[str]
) -> dict[str, object]:
    raw = _mapping(value, label)
    keys = set(raw)
    missing = expected_fields - keys
    unknown = keys - expected_fields
    if missing:
        raise ExtensionContractError(
            f"{label} 缺少字段：{', '.join(sorted(missing))}"
        )
    if unknown:
        raise ExtensionContractError(
            f"{label} 包含未声明字段：{', '.join(sorted(unknown))}"
        )
    _reject_forbidden_keys(raw, label)
    return dict(raw)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ExtensionContractError(f"{label} 必须是 JSON object")
    return value


def _array(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ExtensionContractError(f"{label} 必须是 JSON array")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ExtensionContractError(f"{label} 必须是字符串")
    return value


def _non_empty_string(value: object, label: str) -> str:
    result = _string(value, label)
    if not result.strip():
        raise ExtensionContractError(f"{label} 不能为空")
    return result


def _integer(
    value: object,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ExtensionContractError(f"{label} 必须是整数")
    if minimum is not None and value < minimum:
        raise ExtensionContractError(f"{label} 不得小于 {minimum}")
    if maximum is not None and value > maximum:
        raise ExtensionContractError(f"{label} 不得大于 {maximum}")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ExtensionContractError(f"{label} 必须是布尔值")
    return value


def _constant(value: object, expected: str, label: str) -> None:
    if value != expected:
        raise ExtensionContractError(f"{label} 必须是 {expected}")


def _identifier(value: object, label: str, pattern: re.Pattern[str]) -> str:
    result = _non_empty_string(value, label)
    if pattern.fullmatch(result) is None:
        raise ExtensionContractError(f"{label} 格式无效")
    return result


def _entrypoint(value: object) -> str:
    result = _non_empty_string(value, "manifest entrypoint")
    if "\\" in result:
        raise ExtensionContractError("manifest entrypoint 必须使用包内 POSIX 相对路径")
    path = PurePosixPath(result)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ExtensionContractError(
            "manifest entrypoint 必须是扩展包内的 .py 相对路径"
        )
    return result


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    return tuple(_non_empty_string(item, label) for item in _array(value, label))


def _require_unique(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if len(materialized) != len(set(materialized)):
        raise ExtensionContractError(f"{label} 不得重复")


def _schema_version(value: object, label: str) -> int:
    version = _integer(value, f"{label} schema_version", minimum=1)
    if version != PUBLIC_SCHEMA_VERSION:
        raise ExtensionContractError(
            f"不支持 {label} schema_version {version}"
        )
    return version


def _reject_forbidden_keys(value: Mapping[str, object], label: str) -> None:
    for key, item in value.items():
        if key in _FORBIDDEN_PUBLIC_KEYS:
            raise ExtensionContractError(f"{label} 不得公开字段 {key}")
        if isinstance(item, Mapping):
            _reject_forbidden_keys(item, f"{label}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                if isinstance(child, Mapping):
                    _reject_forbidden_keys(child, f"{label}.{key}[{index}]")


def _plain_json_object(value: object, label: str) -> dict[str, object]:
    raw = _mapping(value, label)
    frozen = _freeze_object(raw, label)
    thawed = _thaw(frozen)
    assert isinstance(thawed, dict)
    return thawed


def _freeze_object(value: object, label: str) -> Mapping[str, JsonValue]:
    raw = _mapping(value, label)
    _reject_forbidden_keys(raw, label)
    return MappingProxyType(
        {key: _freeze_json(item, f"{label}.{key}") for key, item in raw.items()}
    )


def _freeze_json(value: object, label: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExtensionContractError(f"{label} 不得使用非有限数值")
        return value
    if isinstance(value, Mapping):
        return _freeze_object(value, label)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    raise ExtensionContractError(f"{label} 不是可序列化的 JSON 值")


def _thaw(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
