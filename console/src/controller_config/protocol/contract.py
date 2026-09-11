from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from controller_config.protocol.device_auth import AUTH_SIGNATURE_ALGORITHM


class ContractError(RuntimeError):
    """The shared protocol assets are missing or internally unusable."""


def validate_agent_press(value: object) -> None:
    if not isinstance(value, dict) or not {"sequence", "agent", "transport"} <= value.keys():
        raise ValueError("GET_STATUS codex_agent_press 必须是 object")
    sequence, agent, transport = (value.get(key) for key in ("sequence", "agent", "transport"))
    if type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFF:
        raise ValueError("codex_agent_press.sequence 必须是 uint32")
    if agent is None and transport is None:
        return
    if (sequence == 0 or type(agent) is not int or not 0 <= agent < 6
            or transport not in ("usb", "ble")):
        raise ValueError("codex_agent_press 的 Agent 槽位或连接类型无效")


@dataclass(frozen=True)
class IntegerRange:
    minimum: int
    maximum: int


@dataclass(frozen=True)
class ConfigEditorRules:
    profile_name_max_length: int
    mapping_short_name_max_length: int
    max_macros: int
    macro_id: IntegerRange
    macro_name_max_length: int
    macro_steps_max_items: int
    macro_text_max_length: int
    macro_delay_ms: IntegerRange
    lighting_brightness: IntegerRange
    haptic_strength: IntegerRange
    haptic_duration_ms: IntegerRange
    display_brightness: IntegerRange
    display_rotations: tuple[int, ...]


@dataclass(frozen=True)
class Contract:
    protocol_dir: Path
    config_schema: dict[str, Any]
    product_id: str
    hardware_ids: frozenset[str]
    schema_version: int
    usb_vid: int
    usb_pid: int
    serial_pattern: str
    protocol_major: int
    protocol_minor: int
    max_payload_bytes: int
    editor_rules: ConfigEditorRules

    @classmethod
    def load(cls, protocol_dir: Path | None = None) -> "Contract":
        root = protocol_dir or _find_protocol_dir()
        schema = _read_json(root / "config-schema.json")
        manifest = _read_json(root / "fixtures" / "manifest.json")
        usb = _read_json(root / "fixtures" / "usb-descriptor-v1.json")

        try:
            product_id = schema["properties"]["product_id"]["const"]
            hardware_ids = frozenset(schema["properties"]["hardware_id"]["enum"])
            schema_version = int(schema["properties"]["schema_version"]["const"])
            protocol = manifest["protocol"]
            device = usb["device"]
            return cls(
                protocol_dir=root,
                config_schema=schema,
                product_id=product_id,
                hardware_ids=hardware_ids,
                schema_version=schema_version,
                usb_vid=int(device["vid"]),
                usb_pid=int(device["pid"]),
                serial_pattern=str(device["serial_pattern"]),
                protocol_major=int(protocol["major"]),
                protocol_minor=int(protocol["minor"]),
                max_payload_bytes=int(protocol["max_payload_bytes"]),
                editor_rules=_editor_rules(schema),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"共享协议资产结构无效：{exc}") from exc

    def validate_hello(self, payload: dict[str, Any]) -> None:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ContractError("HELLO result 必须是 object")
        identity = result.get("identity")
        versions = result.get("versions")
        hello_config = result.get("config")
        compatibility = result.get("compatibility")
        if not all(
            isinstance(value, dict)
            for value in (identity, versions, hello_config, compatibility)
        ):
            raise ContractError("HELLO 响应缺少权威身份或兼容性 object")

        if identity.get("product_id") != self.product_id:
            raise ContractError("设备 product_id 与当前产品合同不匹配")
        if identity.get("hardware_id") not in self.hardware_ids:
            raise ContractError("设备 hardware_id 不在当前 Schema 支持范围内")
        if identity.get("usb_vid") != self.usb_vid or identity.get("usb_pid") != self.usb_pid:
            raise ContractError("设备 HELLO USB ID 与当前协议合同不匹配")
        serial = identity.get("serial")
        if not isinstance(serial, str) or re.fullmatch(self.serial_pattern, serial) is None:
            raise ContractError("设备序列号不符合当前 USB fixture")
        if versions.get("protocol_major") != self.protocol_major:
            raise ContractError("设备 protocol major 与客户端不兼容")
        if versions.get("protocol_minor") != self.protocol_minor:
            raise ContractError("设备 protocol minor 与客户端不兼容")
        if versions.get("schema_version") != self.schema_version:
            raise ContractError("设备配置 schema 与客户端不兼容")
        if not isinstance(versions.get("firmware"), str):
            raise ContractError("HELLO firmware 版本不是字符串")
        build_id = versions.get("build_id")
        if build_id is not None and (not isinstance(build_id, str) or not build_id):
            raise ContractError("HELLO build_id 必须是非空字符串")
        self._validate_generation_digest(hello_config, "HELLO config")
        if not isinstance(compatibility.get("read"), bool) or not isinstance(
            compatibility.get("write"), bool
        ):
            raise ContractError("HELLO compatibility.read/write 不是布尔值")
        if not isinstance(compatibility.get("reason"), str):
            raise ContractError("HELLO compatibility.reason 不是字符串")

    def validate_config(self, config: dict[str, Any]) -> None:
        self._validate_schema(config, self.config_schema, "GET_CONFIG", "<root>")

    def validate_action(self, action: dict[str, Any]) -> None:
        try:
            schema = self.config_schema["$defs"]["action"]
        except (KeyError, TypeError) as exc:
            raise ContractError("配置 Schema 缺少 $defs.action") from exc
        self._validate_schema(action, schema, "Action", "<action>")

    @staticmethod
    def _validate_schema(
        value: dict[str, Any],
        schema: dict[str, Any],
        label: str,
        root_location: str,
    ) -> None:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors:
            return
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or root_location
        raise ContractError(f"{label} 不符合当前 Schema（{location}）：{error.message}")

    def validate_status(self, payload: dict[str, Any]) -> None:
        result = self._result(payload, "GET_STATUS")
        build_id = result.get("build_id")
        if build_id is not None and (not isinstance(build_id, str) or not build_id):
            raise ContractError("GET_STATUS build_id 必须是非空字符串")
        if not isinstance(result.get("state"), str):
            raise ContractError("GET_STATUS state 必须是字符串")
        self._validate_generation_digest(result.get("active"), "GET_STATUS active")
        pending = result.get("pending")
        if pending is not None:
            self._validate_generation_digest(pending, "GET_STATUS pending")
        if not isinstance(result.get("inputs_neutral"), bool):
            raise ContractError("GET_STATUS inputs_neutral 必须是布尔值")
        if not isinstance(result.get("activation_failed"), bool):
            raise ContractError("GET_STATUS activation_failed 必须是布尔值")
        if result.get("platform") not in {"unselected", "macos", "windows_linux", "custom"}:
            raise ContractError("GET_STATUS platform 不在协议允许范围内")
        if result.get("operating_mode") not in {
            "normal", "codex", "eda", "claude_code"
        }:
            raise ContractError("GET_STATUS operating_mode 不在协议允许范围内")
        codex_micro = result.get("codex_micro")
        if "codex_agent_press" in result:
            try:
                validate_agent_press(result["codex_agent_press"])
            except ValueError as exc:
                raise ContractError(str(exc)) from exc
        if "claude_code_status" in result:
            from controller_config.agent_status import validate_agent_readback
            try:
                validate_agent_readback(result["claude_code_status"])
            except ValueError as exc:
                raise ContractError(str(exc)) from exc
        if codex_micro is not None:
            self._validate_ble_slots(codex_micro)
        active_controls = result.get("active_controls")
        if active_controls is not None:
            if (
                not isinstance(active_controls, list)
                or any(not isinstance(item, str) or not item for item in active_controls)
                or len(active_controls) != len(set(active_controls))
            ):
                raise ContractError("GET_STATUS active_controls 必须是无重复的非空字符串数组")
        action_engine = result.get("action_engine")
        if action_engine is not None:
            self._validate_action_engine_status(action_engine)
        diagnostic_capture = result.get("diagnostic_capture")
        if diagnostic_capture is not None:
            self._validate_diagnostic_capture(diagnostic_capture)
        joystick = result.get("joystick_diagnostics")
        if joystick is not None:
            self._validate_joystick_diagnostics(joystick)

    @staticmethod
    def _validate_action_engine_status(value: object) -> None:
        if not isinstance(value, dict):
            raise ContractError("GET_STATUS action_engine 必须是 object")
        for field in ("host_output_state", "local_page", "round_setting_state"):
            if not isinstance(value.get(field), str) or not value[field]:
                raise ContractError(f"GET_STATUS action_engine.{field} 必须是非空字符串")
        for field in (
            "quick_config_active",
            "local_confirm_selected",
            "round_feedback_active",
            "idle_circle_active",
            "idle_standby_active",
            "usb_standby_active",
        ):
            if not isinstance(value.get(field), bool):
                raise ContractError(f"GET_STATUS action_engine.{field} 必须是布尔值")

    @staticmethod
    def _validate_diagnostic_capture(value: object) -> None:
        if not isinstance(value, dict):
            raise ContractError("GET_STATUS diagnostic_capture 必须是 object")
        if not isinstance(value.get("active"), bool):
            raise ContractError("GET_STATUS diagnostic_capture.active 必须是布尔值")
        for field in ("timeout_ms", "event_sequence"):
            field_value = value.get(field)
            if (
                not isinstance(field_value, int)
                or isinstance(field_value, bool)
                or field_value < 0
            ):
                raise ContractError(
                    f"GET_STATUS diagnostic_capture.{field} 必须是非负整数"
                )
        last_control = value.get("last_control")
        last_pressed = value.get("last_pressed")
        if last_control is None:
            if last_pressed is not None:
                raise ContractError(
                    "GET_STATUS diagnostic_capture.last_pressed 必须与 last_control 同时为空"
                )
        elif (
            not isinstance(last_control, str)
            or not last_control
            or not isinstance(last_pressed, bool)
        ):
            raise ContractError(
                "GET_STATUS diagnostic_capture 最近事件格式无效"
            )

    @staticmethod
    def _validate_joystick_diagnostics(value: object) -> None:
        if not isinstance(value, dict):
            raise ContractError("GET_STATUS joystick_diagnostics 必须是 object")
        for field in (
            "raw_x",
            "raw_y",
            "filtered_x",
            "filtered_y",
            "center_x",
            "center_y",
            "minimum_x",
            "maximum_x",
            "minimum_y",
            "maximum_y",
            "deadzone_x",
            "deadzone_y",
            "directions",
        ):
            if not isinstance(value.get(field), int) or isinstance(value[field], bool):
                raise ContractError(f"GET_STATUS joystick_diagnostics.{field} 必须是整数")
        if not 0 <= value["directions"] <= 0x0F:
            raise ContractError("GET_STATUS joystick_diagnostics.directions 必须是 0..15")
        if value["deadzone_x"] < 0 or value["deadzone_y"] < 0:
            raise ContractError("GET_STATUS joystick_diagnostics deadzone 不得为负数")
        for field in ("radial_active", "radial_valid"):
            if not isinstance(value.get(field), bool):
                raise ContractError(f"GET_STATUS joystick_diagnostics.{field} 必须是布尔值")
        angle = value.get("radial_angle_turns")
        if not isinstance(angle, (int, float)) or isinstance(angle, bool):
            raise ContractError(
                "GET_STATUS joystick_diagnostics.radial_angle_turns 必须是数字"
            )

    @staticmethod
    def _validate_ble_slots(codex_micro: object) -> None:
        if not isinstance(codex_micro, dict):
            raise ContractError("GET_STATUS codex_micro 必须是 object")
        active_slot = codex_micro.get("active_slot")
        slots = codex_micro.get("slots")
        if active_slot is None and slots is None:
            return
        if not _is_non_negative_int(active_slot) or active_slot not in {1, 2, 3}:
            raise ContractError("GET_STATUS codex_micro.active_slot 必须是 1..3")
        if not isinstance(slots, list) or len(slots) != 3:
            raise ContractError("GET_STATUS codex_micro.slots 必须包含三个槽位")
        for index, item in enumerate(slots, start=1):
            if not isinstance(item, dict) or item.get("slot") != index:
                raise ContractError("GET_STATUS codex_micro.slots 顺序必须为 1..3")
            if not isinstance(item.get("paired"), bool) or not isinstance(
                item.get("connected"), bool
            ):
                raise ContractError("GET_STATUS BLE 槽位状态必须是布尔值")
            if item["connected"] and (not item["paired"] or active_slot != index):
                raise ContractError("GET_STATUS 已连接槽位必须是当前已配对槽位")

    def validate_config_response(self, payload: dict[str, Any]) -> None:
        result = self._result(payload, "GET_CONFIG")
        self._validate_generation_digest(result, "GET_CONFIG result")
        config = result.get("config")
        if not isinstance(config, dict):
            raise ContractError("GET_CONFIG 响应缺少 result.config object")
        self.validate_config(config)

    def validate_capabilities(self, payload: dict[str, Any]) -> None:
        result = self._result(payload, "CAPABILITIES")
        limits = result.get("limits")
        controls = result.get("controls")
        actions = result.get("actions")
        features = result.get("features")
        if not isinstance(limits, dict) or not all(
            _is_non_negative_int(value) for value in limits.values()
        ):
            raise ContractError("CAPABILITIES limits 必须是非负整数 object")
        required_limits = {
            "profiles",
            "controls_per_profile",
            "macro_bytes",
            "all_macro_bytes",
            "config_bytes",
        }
        missing_limits = sorted(required_limits - limits.keys())
        if missing_limits:
            raise ContractError(
                f"CAPABILITIES limits 缺少控制台必需字段：{', '.join(missing_limits)}"
            )
        if not isinstance(controls, list) or not controls or not all(
            isinstance(value, str) for value in controls
        ):
            raise ContractError("CAPABILITIES controls 必须是非空字符串数组")
        if limits.get("controls_per_profile") != len(controls):
            raise ContractError("CAPABILITIES controls 数量与 limits 不一致")
        if not isinstance(actions, list) or not actions or not all(
            isinstance(value, str) for value in actions
        ):
            raise ContractError("CAPABILITIES actions 必须是非空字符串数组")
        if not isinstance(features, dict):
            raise ContractError("CAPABILITIES features 必须是 object")
        authentication_enabled = features.get("device_authentication")
        if authentication_enabled is not None and not isinstance(
            authentication_enabled, bool
        ):
            raise ContractError(
                "CAPABILITIES features.device_authentication 必须是布尔值"
            )
        authentication = result.get("device_authentication")
        if authentication_enabled is True and not isinstance(authentication, dict):
            raise ContractError(
                "CAPABILITIES device_authentication 能力说明必须是 object"
            )
        if authentication is not None:
            if not isinstance(authentication, dict):
                raise ContractError(
                    "CAPABILITIES device_authentication 必须是 object"
                )
            if authentication.get("version") != 1:
                raise ContractError(
                    "CAPABILITIES device_authentication.version 必须为 1"
                )
            if authentication.get("signature_algorithm") != AUTH_SIGNATURE_ALGORITHM:
                raise ContractError(
                    "CAPABILITIES device_authentication.signature_algorithm 不受支持"
                )

    @staticmethod
    def _result(payload: dict[str, Any], command: str) -> dict[str, Any]:
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ContractError(f"{command} result 必须是 object")
        return result

    @staticmethod
    def _validate_generation_digest(value: object, label: str) -> None:
        if not isinstance(value, dict):
            raise ContractError(f"{label} 必须是 object")
        if not _is_non_negative_int(value.get("generation")):
            raise ContractError(f"{label}.generation 必须是非负整数")
        digest = value.get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ContractError(f"{label}.digest 必须是 64 位小写十六进制")


def _find_protocol_dir() -> Path:
    override = os.environ.get("BORING_PROTOCOL_DIR")
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    executable = Path(sys.executable).resolve()
    candidates.extend(
        (
            executable.parent / "protocol",
            executable.parent.parent / "Resources" / "protocol",
        )
    )
    source_file = Path(__file__).resolve()
    candidates.extend(parent / "protocol" for parent in source_file.parents)
    for candidate in candidates:
        if (candidate / "protocol.md").is_file() and (candidate / "config-schema.json").is_file():
            return candidate
    raise ContractError(
        "找不到共享协议目录；源码应保留 software/protocol，安装包应携带 protocol 资源"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取共享协议资产 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"共享协议资产必须是 JSON object: {path}")
    return value


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _editor_rules(schema: dict[str, Any]) -> ConfigEditorRules:
    try:
        definitions = _object(schema["$defs"], "$defs")
        profile = _object(definitions["profile"], "$defs.profile")
        profile_properties = _properties(profile, "$defs.profile")
        mappings = _object(profile_properties["mappings"], "$defs.profile.properties.mappings")
        mapping_item = _object(mappings["items"], "$defs.profile.properties.mappings.items")
        mapping_properties = _properties(mapping_item, "$defs.profile.properties.mappings.items")

        macro = _object(definitions["macro"], "$defs.macro")
        macro_properties = _properties(macro, "$defs.macro")
        macros = _object(_properties(schema, "<root>")["macros"], "properties.macros")
        macro_step = _object(definitions["macro_step"], "$defs.macro_step")
        step_variants = macro_step["oneOf"]
        if not isinstance(step_variants, list):
            raise TypeError("$defs.macro_step.oneOf must be array")
        text_schema: dict[str, Any] | None = None
        delay_schema: dict[str, Any] | None = None
        for index, raw_variant in enumerate(step_variants):
            variant = _object(raw_variant, f"$defs.macro_step.oneOf[{index}]")
            properties = _properties(variant, f"$defs.macro_step.oneOf[{index}]")
            op = _object(properties["op"], f"$defs.macro_step.oneOf[{index}].properties.op")
            if op.get("const") == "text":
                text_schema = _object(properties["text"], "macro text")
            elif op.get("const") == "delay":
                delay_schema = _object(properties["duration_ms"], "macro delay")
        if text_schema is None or delay_schema is None:
            raise KeyError("macro_step text/delay variants")

        lighting = _properties(_object(definitions["lighting"], "$defs.lighting"), "$defs.lighting")
        haptic = _properties(_object(definitions["haptic"], "$defs.haptic"), "$defs.haptic")
        display = _properties(_object(definitions["display"], "$defs.display"), "$defs.display")
        rotations = _object(display["rotation"], "$defs.display.properties.rotation")["enum"]
        if not isinstance(rotations, list) or not rotations or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in rotations
        ):
            raise TypeError("display rotation enum must contain integers")

        return ConfigEditorRules(
            profile_name_max_length=_positive_int(
                _object(profile_properties["name"], "profile name")["maxLength"],
                "profile name maxLength",
            ),
            mapping_short_name_max_length=_positive_int(
                _object(mapping_properties["short_name"], "mapping short_name")["maxLength"],
                "mapping short_name maxLength",
            ),
            max_macros=_positive_int(macros["maxItems"], "macros maxItems"),
            macro_id=_integer_range(_object(macro_properties["id"], "macro id"), "macro id"),
            macro_name_max_length=_positive_int(
                _object(macro_properties["name"], "macro name")["maxLength"],
                "macro name maxLength",
            ),
            macro_steps_max_items=_positive_int(
                _object(macro_properties["steps"], "macro steps")["maxItems"],
                "macro steps maxItems",
            ),
            macro_text_max_length=_positive_int(text_schema["maxLength"], "macro text maxLength"),
            macro_delay_ms=_integer_range(delay_schema, "macro delay"),
            lighting_brightness=_integer_range(
                _object(lighting["brightness"], "lighting brightness"),
                "lighting brightness",
            ),
            haptic_strength=_integer_range(
                _object(haptic["strength"], "haptic strength"),
                "haptic strength",
            ),
            haptic_duration_ms=_integer_range(
                _object(haptic["duration_ms"], "haptic duration"),
                "haptic duration",
            ),
            display_brightness=_integer_range(
                _object(display["brightness"], "display brightness"),
                "display brightness",
            ),
            display_rotations=tuple(rotations),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"配置 Schema 缺少控制台所需约束：{exc}") from exc


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be object")
    return value


def _properties(schema: dict[str, Any], label: str) -> dict[str, Any]:
    return _object(schema["properties"], f"{label}.properties")


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be positive integer")
    return value


def _integer_range(schema: dict[str, Any], label: str) -> IntegerRange:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or minimum > maximum
    ):
        raise ValueError(f"{label} must define a valid integer range")
    return IntegerRange(minimum, maximum)
