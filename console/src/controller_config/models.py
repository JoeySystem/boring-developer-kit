from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from controller_config.protocol.device_auth import DeviceTrust


DEVICE_DISPLAY_NAME = "BORING MIST"


class AppState(str, Enum):
    SCANNING = "scanning"
    NO_DEVICE = "no_device"
    MULTIPLE_DEVICES = "multiple_devices"
    CONNECTING = "connecting"
    READY = "ready"
    READ_ONLY = "read_only"
    FIRMWARE_UPDATE_REQUIRED = "firmware_update_required"
    INCOMPATIBLE = "incompatible"
    AUTHENTICITY_FAILED = "authenticity_failed"
    READ_FAILED = "read_failed"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class PortCandidate:
    port_name: str
    description: str = ""
    manufacturer: str = ""
    serial_number: str = ""
    transport: str = "usb"

    @property
    def display_name(self) -> str:
        if self.transport == "bluetooth":
            detail = self.description or self.manufacturer or DEVICE_DISPLAY_NAME
            return f"{detail} · 蓝牙"
        detail = self.description or self.manufacturer or "USB CDC"
        return f"{detail} · {self.port_name}"


@dataclass(frozen=True)
class DeviceSnapshot:
    identity: dict[str, Any]
    versions: dict[str, Any]
    compatibility: dict[str, Any]
    hello_config: dict[str, Any]
    status: dict[str, Any]
    config_result: dict[str, Any]
    capabilities: dict[str, Any]
    port_name: str
    trust: DeviceTrust

    @classmethod
    def from_protocol(
        cls,
        *,
        hello: dict[str, Any],
        status: dict[str, Any],
        config: dict[str, Any],
        capabilities: dict[str, Any],
        port_name: str,
        trust: DeviceTrust,
    ) -> "DeviceSnapshot":
        hello_result = _required_object(hello, "result")
        identity = _required_object(hello_result, "identity")
        config_result = _required_object(config, "result")
        config_value = _required_object(config_result, "config")
        capabilities_result = _required_object(capabilities, "result")
        if config_value.get("product_id") != identity.get("product_id"):
            raise ValueError("GET_CONFIG product_id 与 HELLO 身份不一致")
        if config_value.get("hardware_id") != identity.get("hardware_id"):
            raise ValueError("GET_CONFIG hardware_id 与 HELLO 身份不一致")
        _validate_mapping_capabilities(config_value, capabilities_result)
        return cls(
            identity=identity,
            versions=_required_object(hello_result, "versions"),
            compatibility=_required_object(hello_result, "compatibility"),
            hello_config=_required_object(hello_result, "config"),
            status=_required_object(status, "result"),
            config_result=config_result,
            capabilities=capabilities_result,
            port_name=port_name,
            trust=trust,
        )

    @property
    def config(self) -> dict[str, Any]:
        value = self.config_result.get("config")
        return value if isinstance(value, dict) else {}

    @property
    def connection_kind(self) -> str:
        return "bluetooth" if self.port_name.startswith("ble:") else "usb"

    @property
    def active_profile_id(self) -> int | None:
        value = self.config.get("active_profile")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def active_profile(self) -> dict[str, Any] | None:
        profile_id = self.active_profile_id
        profiles = self.config.get("profiles")
        if not isinstance(profiles, list):
            return None
        for profile in profiles:
            if isinstance(profile, dict) and profile.get("id") == profile_id:
                return profile
        return None

    @property
    def profile_name(self) -> str:
        profile = self.active_profile
        if profile and isinstance(profile.get("name"), str):
            return profile["name"]
        if self.active_profile_id is not None:
            return f"Profile {self.active_profile_id + 1}"
        return "未知 Profile"

    @property
    def controls(self) -> tuple[str, ...]:
        controls = self.capabilities.get("controls")
        if not isinstance(controls, list):
            return ()
        return tuple(value for value in controls if isinstance(value, str))

    @property
    def mappings(self) -> dict[str, dict[str, Any]]:
        profile = self.active_profile or {}
        mappings = profile.get("mappings")
        if not isinstance(mappings, list):
            return {}
        return {
            mapping["control_id"]: mapping
            for mapping in mappings
            if isinstance(mapping, dict) and isinstance(mapping.get("control_id"), str)
        }

    @property
    def is_read_only(self) -> bool:
        return self.compatibility.get("read") is True and self.compatibility.get("write") is not True

    @property
    def config_is_synced(self) -> bool:
        hello_config = self._hello_generation_digest
        status_active = self.status.get("active")
        status_pair = _generation_digest(status_active)
        config_pair = _generation_digest(self.config_result)
        pairs = [pair for pair in (hello_config, status_pair, config_pair) if pair is not None]
        return len(pairs) == 3 and all(pair == pairs[0] for pair in pairs[1:])

    @property
    def config_status_label(self) -> str:
        if self.status.get("activation_failed") is True:
            return "激活失败 · 需要恢复"
        if self.status.get("pending") is not None:
            return "等待设备激活"
        if not self.config_is_synced:
            return "读取结果需对账"
        if self.is_read_only:
            return "已同步 · 只读"
        return "已同步 · 可配置"

    @property
    def _hello_generation_digest(self) -> tuple[int, str] | None:
        return _generation_digest(self.hello_config)


@dataclass(frozen=True)
class ScreenModel:
    state: AppState
    message: str = ""
    technical_message: str = ""
    candidates: tuple[PortCandidate, ...] = field(default_factory=tuple)
    snapshot: DeviceSnapshot | None = None


def _required_object(container: dict[str, Any], key: str) -> dict[str, Any]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"响应字段 {key} 必须是 object")
    return dict(value)


def _generation_digest(value: object) -> tuple[int, str] | None:
    if not isinstance(value, dict):
        return None
    generation = value.get("generation")
    digest = value.get("digest")
    if isinstance(generation, int) and not isinstance(generation, bool) and isinstance(digest, str):
        return generation, digest
    return None


def _validate_mapping_capabilities(
    config: dict[str, Any],
    capabilities: dict[str, Any],
) -> None:
    supported_actions = capabilities.get("actions")
    if not isinstance(supported_actions, list):
        return
    profiles = config.get("profiles")
    if not isinstance(profiles, list):
        return
    for profile in profiles:
        mappings = profile.get("mappings") if isinstance(profile, dict) else None
        if not isinstance(mappings, list):
            continue
        for mapping in mappings:
            action = mapping.get("action") if isinstance(mapping, dict) else None
            action_type = action.get("type") if isinstance(action, dict) else None
            if action_type not in supported_actions:
                control_id = mapping.get("control_id", "unknown")
                raise ValueError(
                    f"{control_id} 的动作 {action_type} 不在 CAPABILITIES actions 中"
                )
