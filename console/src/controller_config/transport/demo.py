from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
    LightingPreviewError,
    validate_lighting_preview_snapshot,
)
from controller_config.models import DeviceSnapshot, PortCandidate
from controller_config.agent_status import AGENT_COMMANDS, SET_AGENT_STATUS, validate_states
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind, Command
from controller_config.protocol.contract import Contract, ContractError
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.protocol.framing import FrameError, canonical_json_bytes


class DemoGateway(QObject):
    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)

    def __init__(self, contract: Contract, demo_state: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._contract = contract
        self._demo_state = demo_state
        self._candidate = PortCandidate(
            port_name="demo://power-v2",
            description="BORING Power V2 fixture",
            manufacturer="Custom Peripheral Project",
            serial_number="CP01-AABBCCDDEEFF",
        )
        self._snapshot: DeviceSnapshot | None = None
        self._firmware_update = _idle_firmware_status()
        self._firmware_image = bytearray()
        self._installed_firmware: dict[str, str] | None = None
        self._calibration_session_id = 0
        self._calibration_step = 0
        self._calibration_points: list[tuple[int, int]] = []
        self._prompts: dict[int, dict[str, object]] = {}
        self._diagnostic_capture_active = False
        self._lighting_preview: dict[str, Any] | None = None

    def scan(self, *, usb_only: bool = False) -> None:
        self.progress.emit("正在读取只读演示 fixture…")
        if self._demo_state == "no-device":
            QTimer.singleShot(80, lambda: self.candidates_found.emit(tuple()))
            return
        if self._demo_state == "disconnected":
            QTimer.singleShot(80, lambda: self.disconnected.emit("演示：设备已从 USB 拔出"))
            return
        QTimer.singleShot(80, lambda: self.candidates_found.emit((self._candidate,)))

    def connect_port(self, _port_name: str) -> None:
        if self._demo_state == "incompatible":
            QTimer.singleShot(
                100,
                lambda: self.failure.emit(
                    BootstrapKind.INCOMPATIBLE,
                    "这台设备与当前 BORING 控制台不兼容",
                    "演示：UNSUPPORTED_PROTOCOL",
                ),
            )
            return
        if self._demo_state == "authenticity-failed":
            QTimer.singleShot(
                100,
                lambda: self.failure.emit(
                    BootstrapKind.AUTHENTICITY_FAILED,
                    "无法确认这是 BORING 设备",
                    "certificate_signature: 演示证书签名验证失败",
                ),
            )
            return
        if self._demo_state == "read-failed":
            QTimer.singleShot(
                100,
                lambda: self.failure.emit(
                    BootstrapKind.READ_FAILED,
                    "未能完整读取设备配置",
                    "演示：GET_CONFIG timeout",
                ),
            )
            return
        self._snapshot = _power_v2_snapshot(
            self._contract, read_only=self._demo_state == "read-only"
        )
        if self._installed_firmware is not None:
            self._snapshot = replace(
                self._snapshot,
                versions={**self._snapshot.versions, **self._installed_firmware},
            )
        QTimer.singleShot(100, lambda: self.snapshot_ready.emit(self._snapshot))

    def execute_command(self, command: Command) -> None:
        snapshot = self._snapshot
        if command.name in AGENT_COMMANDS:
            try:
                if snapshot is None or snapshot.is_read_only or snapshot.capabilities.get("features", {}).get("claude_code_status") is not True:
                    raise ValueError("READ_ONLY")
                expected = {"source", "states"} if command.name == SET_AGENT_STATUS else {"source"}
                if set(command.payload) != expected or command.payload["source"] != "claude_code":
                    raise ValueError("VALIDATION_FAILED")
                states = command.payload.get("states", ["idle"] * 6)
                validate_states(states)
            except ValueError as exc:
                self.agent_status_failed.emit(command, exc)
                return
            active = command.name == SET_AGENT_STATUS
            self._snapshot = replace(snapshot, status={**snapshot.status, "claude_code_status": {
                "active": active, "selected": snapshot.status.get("operating_mode") == "claude_code",
                "lease_ms": 5000, "states": list(states),
            }})
            QTimer.singleShot(20, self, lambda: self.agent_status_completed.emit(command, {
                "command": command.name,
                "result": {"source": "claude_code", "active": active, "lease_ms": 5000},
            }))
            return
        if snapshot is None:
            self.command_failed.emit(
                command.name,
                BootstrapError.from_connection("演示设备尚未连接"),
            )
            return
        if command.name in {"VALIDATE_CONFIG", "SET_CONFIG"}:
            config = command.payload.get("config")
            digest = command.payload.get("digest")
            try:
                if not isinstance(config, dict):
                    raise ValueError("config 必须是 object")
                self._contract.validate_config(config)
                calculated = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
                if digest != calculated:
                    raise ValueError("digest 与候选配置不一致")
            except (ContractError, FrameError, ValueError) as exc:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备拒绝候选配置",
                        str(exc),
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
        if command.name == "VALIDATE_CONFIG":
            QTimer.singleShot(
                50,
                lambda: self.command_completed.emit(
                    "VALIDATE_CONFIG", {"command": "VALIDATE_CONFIG", "result": {}}
                ),
            )
            return
        if command.name == "SET_CONFIG":
            base_generation = command.payload.get("base_generation")
            current_generation = snapshot.config_result.get("generation")
            if base_generation != current_generation:
                self.command_failed.emit(
                    "SET_CONFIG",
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备配置版本冲突",
                        "GENERATION_CONFLICT",
                        error_name="GENERATION_CONFLICT",
                        details={"current_generation": current_generation},
                    ),
                )
                return
            config = copy.deepcopy(command.payload["config"])
            digest = str(command.payload["digest"])
            generation = int(current_generation) + 1
            QTimer.singleShot(
                50,
                lambda: self.command_completed.emit(
                    "SET_CONFIG", {"command": "SET_CONFIG", "result": {}}
                ),
            )
            pending_status = {
                **snapshot.status,
                "state": "PENDING_ACTIVATION",
                "pending": {"generation": generation, "digest": digest},
            }
            QTimer.singleShot(100, lambda: self.status_updated.emit(pending_status))

            def activate() -> None:
                active_status = {
                    **snapshot.status,
                    "state": "ACTIVE",
                    "active": {"generation": generation, "digest": digest},
                    "pending": None,
                }
                self._snapshot = replace(
                    snapshot,
                    hello_config={"generation": generation, "digest": digest},
                    status=active_status,
                    config_result={"generation": generation, "digest": digest, "config": config},
                )
                self.status_updated.emit(active_status)

            QTimer.singleShot(180, activate)
            return
        if command.name == "GET_CONFIG":
            result = copy.deepcopy(self._snapshot.config_result)
            QTimer.singleShot(
                50,
                lambda: self.command_completed.emit(
                    "GET_CONFIG", {"command": "GET_CONFIG", "result": result}
                ),
            )
            return
        if command.name == SET_LIGHTING_PREVIEW:
            features = snapshot.capabilities.get("features")
            under_key_count = (
                features.get("under_key_rgb_count")
                if isinstance(features, dict)
                else None
            )
            try:
                if (
                    not isinstance(under_key_count, int)
                    or isinstance(under_key_count, bool)
                ):
                    raise LightingPreviewError(
                        "under_key_rgb_count is unavailable"
                    )
                validate_lighting_preview_snapshot(
                    command.payload,
                    under_key_rgb_count=under_key_count,
                )
            except LightingPreviewError as exc:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备拒绝灯光预览",
                        str(exc),
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            self._lighting_preview = copy.deepcopy(command.payload)
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    SET_LIGHTING_PREVIEW,
                    {
                        "command": SET_LIGHTING_PREVIEW,
                        "result": {"state": "ACTIVE"},
                    },
                ),
            )
            return
        if command.name == CLEAR_LIGHTING_PREVIEW:
            self._lighting_preview = None
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    CLEAR_LIGHTING_PREVIEW,
                    {
                        "command": CLEAR_LIGHTING_PREVIEW,
                        "result": {"state": "CLEARED"},
                    },
                ),
            )
            return
        if command.name == "FACTORY_DEFAULT":
            generation = snapshot.config_result.get("generation")
            if (
                command.payload.get("confirmation") != "FACTORY_DEFAULT"
                or command.payload.get("base_generation") != generation
            ):
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备拒绝恢复出厂设置",
                        "GENERATION_CONFLICT",
                        error_name="GENERATION_CONFLICT",
                    ),
                )
                return
            QTimer.singleShot(
                50,
                lambda: self.command_completed.emit(
                    "FACTORY_DEFAULT",
                    {
                        "command": "FACTORY_DEFAULT",
                        "result": {
                            "state": "PENDING_ACTIVATION",
                            "generation": int(generation) + 1,
                        },
                    },
                ),
            )
            return
        if command.name == "GET_PROMPT_LIST":
            prompts = [
                {
                    "prompt_id": prompt_id,
                    "name": value["name"],
                    "body_bytes": len(str(value["body"]).encode("utf-8")),
                }
                for prompt_id, value in sorted(self._prompts.items())
            ]
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "GET_PROMPT_LIST",
                    {"command": "GET_PROMPT_LIST", "result": {"prompts": prompts}},
                ),
            )
            return
        if command.name == "GET_PROMPT":
            prompt_id = command.payload.get("prompt_id")
            value = self._prompts.get(prompt_id) if isinstance(prompt_id, int) else None
            if value is None:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示提示词不存在",
                        "NOT_FOUND",
                        error_name="NOT_FOUND",
                    ),
                )
                return
            result = {
                "prompt_id": prompt_id,
                "name": value["name"],
                "body": value["body"],
                "body_bytes": len(str(value["body"]).encode("utf-8")),
            }
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "GET_PROMPT", {"command": "GET_PROMPT", "result": result}
                ),
            )
            return
        if command.name == "SET_PROMPT":
            prompt_id = command.payload.get("prompt_id")
            name = command.payload.get("name")
            body = command.payload.get("body")
            if (
                not isinstance(prompt_id, int)
                or not isinstance(name, str)
                or not isinstance(body, str)
            ):
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示提示词无效",
                        "VALIDATION_FAILED",
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            self._prompts[prompt_id] = {"name": name, "body": body}
            result = {
                "prompt_id": prompt_id,
                "name": name,
                "body_bytes": len(body.encode("utf-8")),
            }
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "SET_PROMPT", {"command": "SET_PROMPT", "result": result}
                ),
            )
            return
        if command.name == "DELETE_PROMPT":
            prompt_id = command.payload.get("prompt_id")
            if isinstance(prompt_id, int):
                self._prompts.pop(prompt_id, None)
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "DELETE_PROMPT",
                    {
                        "command": "DELETE_PROMPT",
                        "result": {"prompt_id": prompt_id, "deleted": True},
                    },
                ),
            )
            return
        if command.name == "GET_PROMPT_EVENT":
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "GET_PROMPT_EVENT",
                    {
                        "command": "GET_PROMPT_EVENT",
                        "result": {"poll_after_ms": 100, "event": None},
                    },
                ),
            )
            return
        if command.name in {"BLE_SLOT_SELECT", "BLE_SLOT_CLEAR"}:
            slot = command.payload.get("slot")
            if slot not in {1, 2, 3}:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "蓝牙槽位无效",
                        "slot must be 1..3",
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            codex_micro = copy.deepcopy(snapshot.status.get("codex_micro", {}))
            slots = codex_micro.get("slots", [])
            if command.name == "BLE_SLOT_CLEAR" and len(slots) == 3:
                slots[slot - 1] = {"slot": slot, "paired": False, "connected": False}
            for item in slots:
                if isinstance(item, dict):
                    item["connected"] = bool(
                        item.get("slot") == slot and item.get("paired")
                    )
            codex_micro["active_slot"] = slot
            codex_micro["slots"] = slots
            updated_status = {**snapshot.status, "codex_micro": codex_micro}
            self._snapshot = replace(snapshot, status=updated_status)
            result = slots[slot - 1] if len(slots) == 3 else {"slot": slot}
            QTimer.singleShot(
                30,
                lambda: self.command_completed.emit(
                    command.name, {"command": command.name, "result": result}
                ),
            )
            QTimer.singleShot(50, lambda: self.status_updated.emit(updated_status))
            return
        if command.name == "DIAGNOSTIC_START":
            status = copy.deepcopy(snapshot.status)
            status["action_engine"] = {
                **status.get("action_engine", {}),
                "host_output_state": "local_ui",
            }
            capture = status.setdefault("diagnostic_capture", {})
            capture.update(
                active=True,
                timeout_ms=3000,
                event_sequence=int(capture.get("event_sequence", 0)),
                last_control=capture.get("last_control"),
                last_pressed=capture.get("last_pressed"),
            )
            self._diagnostic_capture_active = True
            self._snapshot = replace(snapshot, status=status)
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "DIAGNOSTIC_START",
                    {
                        "command": "DIAGNOSTIC_START",
                        "result": {"active": True, "timeout_ms": 3000},
                    },
                ),
            )
            QTimer.singleShot(30, lambda: self.status_updated.emit(status))
            return
        if command.name == "DIAGNOSTIC_STOP":
            status = copy.deepcopy(snapshot.status)
            status["action_engine"] = {
                **status.get("action_engine", {}),
                "host_output_state": "enabled",
            }
            capture = status.setdefault("diagnostic_capture", {})
            capture.update(active=False, last_control=None, last_pressed=None)
            self._diagnostic_capture_active = False
            self._snapshot = replace(snapshot, status=status)
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "DIAGNOSTIC_STOP",
                    {"command": "DIAGNOSTIC_STOP", "result": {"active": False}},
                ),
            )
            QTimer.singleShot(30, lambda: self.status_updated.emit(status))
            return
        if command.name == "CALIBRATION_START":
            features = snapshot.capabilities.get("features")
            if (
                snapshot.compatibility.get("write") is not True
                or not isinstance(features, dict)
                or features.get("joystick_calibration") is not True
            ):
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备不可校准",
                        "READ_ONLY",
                        error_name="READ_ONLY",
                    ),
                )
                return
            if self._calibration_session_id:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备校准忙",
                        "BUSY",
                        error_name="BUSY",
                    ),
                )
                return
            self._calibration_session_id = 1
            self._calibration_step = 0
            self._calibration_points = [(2048, 2048)]
            result = {
                "session_id": 1,
                "state": "CENTERING",
                "raw": {"x": 2048, "y": 2048},
                "center": None,
                "minimum": None,
                "maximum": None,
                "travel_complete": False,
                "center_window_ms": 50,
                "timeout_ms": 30_000,
            }
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "CALIBRATION_START",
                    {"command": "CALIBRATION_START", "result": result},
                ),
            )
            return
        if command.name == "CALIBRATION_SAMPLE":
            if command.payload.get("session_id") != self._calibration_session_id:
                self._emit_calibration_not_found(command.name)
                return
            sequence = ((260, 340), (3820, 3750), (2048, 2048))
            point = sequence[min(self._calibration_step, len(sequence) - 1)]
            self._calibration_step += 1
            self._calibration_points.append(point)
            xs = [item[0] for item in self._calibration_points]
            ys = [item[1] for item in self._calibration_points]
            travel_complete = self._calibration_step >= len(sequence)
            result = {
                "session_id": self._calibration_session_id,
                "state": "CAPTURING",
                "raw": {"x": point[0], "y": point[1]},
                "center": {"x": 2048, "y": 2048},
                "minimum": {"x": min(xs), "y": min(ys)},
                "maximum": {"x": max(xs), "y": max(ys)},
                "travel_complete": travel_complete,
            }
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "CALIBRATION_SAMPLE",
                    {"command": "CALIBRATION_SAMPLE", "result": result},
                ),
            )
            return
        if command.name == "CALIBRATION_CANCEL":
            if command.payload.get("session_id") != self._calibration_session_id:
                self._emit_calibration_not_found(command.name)
                return
            session_id = self._calibration_session_id
            self._clear_demo_calibration()
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "CALIBRATION_CANCEL",
                    {
                        "command": "CALIBRATION_CANCEL",
                        "result": {"state": "CANCELLED", "session_id": session_id},
                    },
                ),
            )
            return
        if command.name == "CALIBRATION_CONFIRM":
            if command.payload.get("session_id") != self._calibration_session_id:
                self._emit_calibration_not_found(command.name)
                return
            base_generation = command.payload.get("base_generation")
            current_generation = snapshot.config_result.get("generation")
            if base_generation != current_generation:
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备配置版本冲突",
                        "GENERATION_CONFLICT",
                        error_name="GENERATION_CONFLICT",
                        details={"current_generation": current_generation},
                    ),
                )
                return
            if self._calibration_step < 3 or self._calibration_points[-1] != (2048, 2048):
                self.command_failed.emit(
                    command.name,
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备校准未完成",
                        "return the joystick to center before confirming",
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            config = copy.deepcopy(snapshot.config)
            joystick = config["joystick"]
            xs = [item[0] for item in self._calibration_points]
            ys = [item[1] for item in self._calibration_points]
            joystick["calibrated"] = True
            joystick["x"].update(
                minimum=min(xs), center=2048, maximum=max(xs), deadzone=80
            )
            joystick["y"].update(
                minimum=min(ys), center=2048, maximum=max(ys), deadzone=80
            )
            digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
            generation = int(current_generation) + 1
            self._clear_demo_calibration()
            pending_status = {
                **snapshot.status,
                "state": "PENDING_ACTIVATION",
                "pending": {"generation": generation, "digest": digest},
            }
            QTimer.singleShot(
                20,
                lambda: self.command_completed.emit(
                    "CALIBRATION_CONFIRM",
                    {
                        "command": "CALIBRATION_CONFIRM",
                        "result": {
                            "state": "PENDING_ACTIVATION",
                            "generation": generation,
                            "digest": digest,
                        },
                    },
                ),
            )
            QTimer.singleShot(50, lambda: self.status_updated.emit(pending_status))

            def activate_calibration() -> None:
                active_status = {
                    **snapshot.status,
                    "state": "ACTIVE",
                    "active": {"generation": generation, "digest": digest},
                    "pending": None,
                }
                self._snapshot = replace(
                    snapshot,
                    hello_config={"generation": generation, "digest": digest},
                    status=active_status,
                    config_result={
                        "generation": generation,
                        "digest": digest,
                        "config": config,
                    },
                )
                self.status_updated.emit(active_status)

            QTimer.singleShot(100, activate_calibration)
            return
        if command.name == "FW_STATUS":
            payload = {
                "command": "FW_STATUS",
                "result": {"firmware_update": copy.deepcopy(self._firmware_update)},
            }
            QTimer.singleShot(
                30, lambda: self.command_completed.emit("FW_STATUS", payload)
            )
            return
        if command.name == "FW_BEGIN":
            self._firmware_image = bytearray()
            running_partition = self._firmware_update["running_partition"]
            target_partition = "ota_0" if running_partition == "ota_1" else "ota_1"
            self._firmware_update = {
                "state": "RECEIVING",
                "expected_size": command.payload.get("size"),
                "received_size": 0,
                "target_capacity": 2 * 1024 * 1024,
                "expected_sha256": command.payload.get("sha256"),
                "expected_version": command.payload.get("version"),
                "running_version": snapshot.versions.get("firmware", "0.0.0-demo"),
                "running_partition": running_partition,
                "target_partition": target_partition,
                "rollback_pending": False,
                "reboot_pending": False,
            }
            payload = {
                "command": "FW_BEGIN",
                "result": {
                    "state": "RECEIVING",
                    "offset": 0,
                    "chunk_bytes": 4096,
                    "target_partition": target_partition,
                },
            }
            QTimer.singleShot(
                30, lambda: self.command_completed.emit("FW_BEGIN", payload)
            )
            return
        if command.name == "FW_DATA":
            offset = command.payload.get("offset")
            try:
                chunk = base64.b64decode(command.payload.get("data", ""), validate=True)
            except (TypeError, ValueError):
                chunk = b""
            if offset != len(self._firmware_image) or not chunk:
                self.command_failed.emit(
                    "FW_DATA",
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备拒绝固件分片",
                        "offset or base64 data is invalid",
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            self._firmware_image.extend(chunk)
            self._firmware_update["received_size"] = len(self._firmware_image)
            payload = {
                "command": "FW_DATA",
                "result": {
                    "state": "RECEIVING",
                    "received_size": len(self._firmware_image),
                    "expected_size": self._firmware_update["expected_size"],
                },
            }
            QTimer.singleShot(
                10, lambda: self.command_completed.emit("FW_DATA", payload)
            )
            return
        if command.name == "FW_END":
            size_ok = len(self._firmware_image) == self._firmware_update.get("expected_size")
            digest_ok = (
                hashlib.sha256(self._firmware_image).hexdigest()
                == self._firmware_update.get("expected_sha256")
            )
            if not size_ok or not digest_ok:
                self.command_failed.emit(
                    "FW_END",
                    BootstrapError(
                        BootstrapKind.READ_FAILED,
                        "演示设备固件校验失败",
                        "image size or sha256 mismatch",
                        error_name="VALIDATION_FAILED",
                    ),
                )
                return
            version = str(self._firmware_update["expected_version"])
            target_partition = self._firmware_update["target_partition"]
            self._firmware_update["state"] = "FINALIZING"
            payload = {
                "command": "FW_END",
                "result": {
                    "state": "FINALIZING",
                    "version": version,
                    "reboot_scheduled": False,
                },
            }
            QTimer.singleShot(30, lambda: self.command_completed.emit("FW_END", payload))

            def ready() -> None:
                self._firmware_update["state"] = "READY_TO_REBOOT"
                self._firmware_update["reboot_pending"] = True

            QTimer.singleShot(100, ready)

            def reboot() -> None:
                self._installed_firmware = {
                    "firmware": version,
                    "build_id": version,
                }
                self._firmware_update = _idle_firmware_status(
                    running_version=version,
                    running_partition=target_partition,
                )
                self.disconnected.emit("演示：固件维护完成并重启")

            QTimer.singleShot(500, reboot)
            return
        if command.name == "FW_ABORT":
            self._firmware_update = _idle_firmware_status(
                running_version=str(snapshot.versions.get("firmware", "0.0.0-demo")),
                running_partition=self._firmware_update["running_partition"],
            )
            self._firmware_image = bytearray()
            QTimer.singleShot(
                30,
                lambda: self.command_completed.emit(
                    "FW_ABORT", {"command": "FW_ABORT", "result": {"state": "IDLE"}}
                ),
            )
            return

    def set_status_poll_interval(self, _interval_ms: int) -> None:
        pass

    def _emit_calibration_not_found(self, command_name: str) -> None:
        self.command_failed.emit(
            command_name,
            BootstrapError(
                BootstrapKind.READ_FAILED,
                "演示校准会话不存在",
                "calibration session is not active",
                error_name="NOT_FOUND",
            ),
        )

    def _clear_demo_calibration(self) -> None:
        self._calibration_session_id = 0
        self._calibration_step = 0
        self._calibration_points = []

    def shutdown(self) -> None:
        self._lighting_preview = None


def _power_v2_snapshot(contract: Contract, *, read_only: bool) -> DeviceSnapshot:
    fixture_dir = contract.protocol_dir / "fixtures"
    config = _read(fixture_dir / "config-matrix12-power-v2-v1.json")
    capabilities = _read(fixture_dir / "capabilities-matrix12-power-v2-v1.json")
    capabilities = copy.deepcopy(capabilities)
    capabilities.setdefault("result", {}).setdefault("features", {})[
        "diagnostic_capture"
    ] = True
    capabilities["result"]["features"]["lighting_preview"] = True
    # The demo gateway does not implement home-icon or glyph-image transfers.
    capabilities["result"]["features"]["custom_home_icon"] = False
    capabilities["result"]["features"]["custom_glyph_icons"] = False
    # Independent device preferences are exercised with a protocol-capable gateway.
    capabilities["result"]["features"]["normal_agent_key_behavior"] = False
    digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
    generation = 1
    hello = {
        "command": "HELLO",
        "result": {
            "identity": {
                "product_id": contract.product_id,
                "hardware_id": config["hardware_id"],
                "serial": "CP01-AABBCCDDEEFF",
                "usb_vid": contract.usb_vid,
                "usb_pid": contract.usb_pid,
            },
            "versions": {
                "firmware": "0.0.0-demo",
                "protocol_major": contract.protocol_major,
                "protocol_minor": contract.protocol_minor,
                "schema_version": contract.schema_version,
            },
            "config": {"generation": generation, "digest": digest},
            "compatibility": {
                "read": True,
                "write": not read_only,
                "reason": "demo_read_only" if read_only else "compatible",
            },
        },
    }
    status = {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "activation_failed": False,
            "active": {"generation": generation, "digest": digest},
            "pending": None,
            "inputs_neutral": True,
            "platform": "macos",
            "operating_mode": "codex",
            "battery": 73,
            "battery_valid": True,
            "is_charging": None,
            "active_controls": [],
            "diagnostic_capture": {
                "active": False,
                "timeout_ms": 3000,
                "event_sequence": 0,
                "last_control": None,
                "last_pressed": None,
            },
            "action_engine": {
                "host_output_state": "enabled",
                "local_page": "none",
                "round_setting_state": "idle",
                "quick_config_active": False,
                "local_confirm_selected": False,
                "round_feedback_active": False,
                "idle_circle_active": False,
                "idle_standby_active": False,
                "usb_standby_active": False,
            },
            "joystick_diagnostics": {
                "raw_x": 2048,
                "raw_y": 2048,
                "filtered_x": 2048,
                "filtered_y": 2048,
                "center_x": 2048,
                "center_y": 2048,
                "minimum_x": 0,
                "maximum_x": 4095,
                "minimum_y": 0,
                "maximum_y": 4095,
                "deadzone_x": 180,
                "deadzone_y": 180,
                "directions": 0,
                "radial_active": False,
                "radial_valid": True,
                "radial_angle_turns": 0.0,
            },
            "codex_micro": {
                "connected": True,
                "usb_connected": True,
                "ble_connected": False,
                "mode": "codex",
                "active_slot": 1,
                "slots": [
                    {"slot": 1, "paired": True, "connected": False},
                    {"slot": 2, "paired": False, "connected": False},
                    {"slot": 3, "paired": False, "connected": False},
                ],
            },
        },
    }
    config_payload = {
        "command": "GET_CONFIG",
        "result": {"generation": generation, "digest": digest, "config": config},
    }
    return DeviceSnapshot.from_protocol(
        hello=hello,
        status=status,
        config=config_payload,
        capabilities=capabilities,
        port_name="demo://power-v2",
        trust=DeviceTrust(
            DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED,
            "开发设备，未认证",
            "demo_transport",
        ),
    )


def _idle_firmware_status(
    *, running_version: str = "0.0.0-demo", running_partition: str = "ota_0"
) -> dict[str, Any]:
    return {
        "state": "IDLE",
        "expected_size": 0,
        "received_size": 0,
        "target_capacity": 2 * 1024 * 1024,
        "expected_sha256": "",
        "expected_version": "",
        "running_version": running_version,
        "running_partition": running_partition,
        "target_partition": "ota_0" if running_partition == "ota_1" else "ota_1",
        "rollback_pending": False,
        "reboot_pending": False,
    }


def _read(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"fixture must be an object: {path}")
    return value
