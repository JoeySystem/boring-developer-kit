from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from controller_config import __version__
from controller_config.models import DeviceSnapshot, _generation_digest
from controller_config.protocol.contract import Contract, ContractError
from controller_config.protocol.device_auth import (
    AUTH_CHALLENGE,
    AUTH_GET_CERTIFICATE,
    DeviceAuthenticationError,
    DeviceAuthenticator,
    DeviceTrust,
    TrustBundle,
    TrustPolicy,
    VerifiedDeviceCertificate,
)
from controller_config.protocol.framing import ERROR_FLAG, RESPONSE_FLAG, Frame, FrameError

if TYPE_CHECKING:
    from controller_config.config_cache import DeviceConfigCache


ACK = 0x7E
NACK = 0x7F


class BootstrapKind(str, Enum):
    DISCOVERY_INTERRUPTED = "discovery_interrupted"
    AUTHENTICATION_INTERRUPTED = "authentication_interrupted"
    FIRMWARE_UPDATE_REQUIRED = "firmware_update_required"
    INCOMPATIBLE = "incompatible"
    AUTHENTICITY_FAILED = "authenticity_failed"
    READ_FAILED = "read_failed"


class BootstrapError(RuntimeError):
    def __init__(
        self,
        kind: BootstrapKind,
        message: str,
        technical: str = "",
        *,
        error_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.technical = technical
        self.error_name = error_name
        self.details = dict(details or {})

    @classmethod
    def from_connection(cls, technical: str) -> "BootstrapError":
        return cls(BootstrapKind.READ_FAILED, "设备连接已中断", technical)

    @classmethod
    def command_dropped(cls, command_name: str, reason: str) -> "BootstrapError":
        """A queued command that was discarded without being sent (A-04)."""
        return cls(
            BootstrapKind.READ_FAILED,
            "命令未执行：设备连接已关闭",
            f"{command_name} 在发送前被丢弃：{reason}",
            error_name=COMMAND_DROPPED,
        )


# error_name for commands discarded before they reached the device. Their
# callers learn the command did not run; configuration writes, firmware and
# calibration keep being reconciled by the connection-loss handler instead.
COMMAND_DROPPED = "CONNECTION_CLOSED"


@dataclass(frozen=True)
class Command:
    name: str
    message_type: int
    payload: dict[str, Any]
    timeout_ms: int = 2500
    retries: int = 0


class BootstrapSession:
    """Read-only PC-U2 handshake coordinator, independent of Qt and serial I/O."""

    def __init__(
        self,
        *,
        contract: Contract,
        port_name: str,
        send: Callable[[Command, int], None],
        completed: Callable[[DeviceSnapshot], None],
        failed: Callable[[BootstrapError], None],
        authenticator: DeviceAuthenticator | None = None,
        usb_serial: str = "",
        next_request_id: Callable[[], int] | None = None,
        config_cache: "DeviceConfigCache | None" = None,
        step_completed: Callable[[str, str], None] | None = None,
        command_started: Callable[[str], None] | None = None,
    ) -> None:
        self._contract = contract
        self._config_cache = config_cache
        # (step, detail) after each accepted response, for connection timing.
        self._step_completed = step_completed
        # The name of each command as it is sent, for the connecting steps (design decision).
        self._command_started = command_started
        self._allow_config_maintenance = authenticator is not None and not port_name.startswith("ble:")
        self._port_name = port_name
        self._send = send
        self._completed = completed
        self._failed = failed
        self._authenticator = authenticator or DeviceAuthenticator(
            TrustBundle.from_document(
                {"version": 1, "roots": []},
                policy=TrustPolicy.DEVELOPMENT,
            )
        )
        self._usb_serial = usb_serial
        self._next_request_id = next_request_id or self._local_request_id
        self._commands = [
            Command(
                "HELLO",
                0x01,
                {
                    "client": {"name": "controller-config", "version": __version__},
                    "protocol": {
                        "min_major": contract.protocol_major,
                        "max_major": contract.protocol_major,
                        "max_minor": contract.protocol_minor,
                    },
                },
            ),
            Command("CAPABILITIES", 0x02, {}),
        ]
        self._index = -1
        self._request_id = 0
        self._responses: dict[str, dict[str, Any]] = {}
        self._nonce = ""
        self._certificate: VerifiedDeviceCertificate | None = None
        self._trust: DeviceTrust | None = None
        self._config_compatibility_error: BootstrapError | None = None
        self._maintenance_error: BootstrapError | None = None
        # "device" after a GET_CONFIG read, "cache" when HELLO's digest
        # matched the configuration saved on this computer (SH-005).
        self.config_source = ""
        self.finished = False

    @property
    def current_command(self) -> Command | None:
        if 0 <= self._index < len(self._commands):
            return self._commands[self._index]
        return None

    @property
    def current_request_id(self) -> int:
        return self._request_id

    def start(self) -> None:
        if self._index != -1:
            raise RuntimeError("bootstrap session 已启动")
        self._advance()

    def accept(self, frame: Frame) -> None:
        if self.finished:
            return
        command = self.current_command
        if command is None or frame.request_id != self._request_id:
            return
        try:
            payload = frame.json_payload()
            _validate_response(command, frame, payload)
            if command.name == "HELLO":
                self._contract.validate_hello(
                    payload, allow_config_incompatible=self._allow_config_maintenance
                )
                result = payload["result"]
                build = str(result["versions"].get("build_id", ""))
                # These archived builds have a confirmed BTC authentication stack overflow.
                # HELLO is untrusted here: use it only to stop, never to grant access.
                if (self._port_name.startswith("ble:")
                        and result["identity"]["hardware_id"] == "WMP-S3-MATRIX12-POWER-V2"
                        and build.split("-", 1)[0] in {"20260907.16", "20260909.08"}):
                    raise BootstrapError(
                        BootstrapKind.FIRMWARE_UPDATE_REQUIRED,
                        "请通过 USB 更新固件后再使用蓝牙配置",
                        "当前固件的蓝牙认证可能导致设备重启，已停止连接。"
                        "请连接 USB 数据线后重新扫描，认证成功后进入「设置 → 固件维护」更新。"
                        f"\n连接：蓝牙；固件：{build}；停止步骤：HELLO（尚未发送认证请求）",
                        error_name="ble_auth_stack_overflow",
                    )
                compatibility = result["compatibility"]
                schema_incompatible = result["versions"]["schema_version"] != self._contract.schema_version
                if schema_incompatible or compatibility.get("read") is not True:
                    reason = str(compatibility.get("reason", "device_read_not_supported"))
                    error = BootstrapError(
                        BootstrapKind.INCOMPATIBLE,
                        "这台设备与当前 BORING 控制台不兼容",
                        ("设备配置 schema 与客户端不兼容；" if schema_incompatible else "") + reason,
                    )
                    if not self._allow_config_maintenance:
                        raise error
                    self._config_compatibility_error = error
                elif self._allow_config_maintenance and compatibility.get("write") is not True:
                    self._config_compatibility_error = BootstrapError(
                        BootstrapKind.READ_FAILED, "设备配置只读",
                        str(compatibility.get("reason", "device_write_not_supported")),
                    )
            elif command.name == "GET_STATUS":
                self._contract.validate_status(payload)
            elif command.name == "GET_CONFIG":
                self._contract.validate_config_response(payload)
            elif command.name == "CAPABILITIES":
                self._contract.validate_capabilities(payload)
                self._prepare_authentication(payload)
            elif command.name == "AUTH_GET_CERTIFICATE":
                hello_result = self._responses["HELLO"]["result"]
                self._certificate = self._authenticator.verify_certificate_response(
                    payload,
                    hello_identity=hello_result["identity"],
                    usb_serial=self._usb_serial,
                )
            elif command.name == "AUTH_CHALLENGE":
                if self._certificate is None:
                    raise DeviceAuthenticationError(
                        "missing_certificate",
                        "设备挑战响应前没有完成证书验证",
                    )
                self._trust = self._authenticator.verify_challenge_response(
                    payload,
                    certificate=self._certificate,
                    nonce=self._nonce,
                )
            elif command.name == "FW_STATUS":
                from controller_config.firmware_update import FirmwareStatus

                status = FirmwareStatus.from_payload(payload)
                hello_version = self._responses["HELLO"]["result"]["versions"]["firmware"]
                if status.running_partition not in {"ota_0", "ota_1"}:
                    raise ValueError("FW_STATUS running_partition 必须是 ota_0 或 ota_1")
                if status.running_version != hello_version:
                    raise ValueError("FW_STATUS running_version 与已认证 HELLO 版本不一致")
                if status.rollback_pending is None:
                    raise ValueError("FW_STATUS rollback_pending 必须是布尔值")
                self._complete_maintenance()
                return
            self._responses[command.name] = payload
            if command.name == "GET_CONFIG":
                self.config_source = "device"
            self._report_step(command.name, f"{len(frame.payload_bytes)} bytes")
            if command.name == "GET_STATUS" and self._use_cached_config():
                self._report_step("GET_CONFIG", "skipped: cached config matches HELLO digest")
                self._index += 1
            if command.name == "AUTH_CHALLENGE" and self._config_compatibility_error is not None:
                if self._begin_maintenance(self._config_compatibility_error):
                    return
                if self._config_compatibility_error.kind is BootstrapKind.INCOMPATIBLE:
                    raise self._config_compatibility_error
            self._advance()
        except DeviceAuthenticationError as exc:
            self._finish_error(
                BootstrapError(
                    BootstrapKind.AUTHENTICITY_FAILED,
                    ("当前固件未提供设备认证能力，请先恢复官方固件。"
                     if exc.reason == "authentication_required"
                     else "无法确认这是 BORING 设备"),
                    f"{exc.reason}: {exc}",
                    error_name=exc.reason,
                )
            )
        except BootstrapError as exc:
            self._finish_error(exc)
        except (ContractError, FrameError, KeyError, TypeError, ValueError) as exc:
            kind = BootstrapKind.INCOMPATIBLE if command.name == "HELLO" else BootstrapKind.READ_FAILED
            self._finish_error(BootstrapError(kind, _message_for(kind), str(exc)))

    def _report_step(self, step: str, detail: str) -> None:
        if self._step_completed is not None:
            self._step_completed(step, detail)

    def _use_cached_config(self) -> bool:
        """Answer GET_CONFIG from the local cache when the device's active
        configuration is the one saved here. Only for an authenticated device
        whose HELLO and GET_STATUS agree on the same generation and digest."""

        following = self._index + 1
        if (self._config_cache is None
                or self._trust is None or not self._trust.is_authenticated
                or not self._trust.serial
                or self._config_compatibility_error is not None
                or following >= len(self._commands)
                or self._commands[following].name != "GET_CONFIG"):
            return False
        hello = self._responses["HELLO"]["result"]
        revision = _generation_digest(hello.get("config"))
        status = self._responses["GET_STATUS"].get("result", {})
        if revision is None or _generation_digest(status.get("active")) != revision:
            return False
        result = self._config_cache.lookup(self._trust.serial, *revision)
        if result is None:
            return False
        payload = {"command": "GET_CONFIG", "result": result}
        try:
            self._contract.validate_config_response(payload)
            DeviceSnapshot.from_protocol(
                hello=self._responses["HELLO"],
                status=self._responses["GET_STATUS"],
                config=payload,
                capabilities=self._responses["CAPABILITIES"],
                port_name=self._port_name,
                trust=self._trust,
            )
        except (ContractError, KeyError, TypeError, ValueError):
            return False
        self._responses["GET_CONFIG"] = payload
        self.config_source = "cache"
        return True

    def expects_config_read(self) -> bool:
        """Right after HELLO: will the configuration be read over the link this time?

        The cache is keyed by the authenticated serial, and authentication only
        passes when the certificate's serial is HELLO's (device_auth
        _validate_identity), so HELLO's serial and config generation / digest
        already say whether a saved copy exists. No copy: the read will happen.
        A copy: it is skipped, unless GET_STATUS reports another revision in the
        few seconds between (or the copy fails its checks); the worker then says
        so when GET_CONFIG is sent (design decision, 单 design ticket)."""
        if self._config_cache is None:
            return True
        hello = self._responses.get("HELLO", {}).get("result", {})
        identity = hello.get("identity") if isinstance(hello, dict) else None
        serial = identity.get("serial") if isinstance(identity, dict) else None
        revision = _generation_digest(hello.get("config")) if isinstance(hello, dict) else None
        if not isinstance(serial, str) or not serial or revision is None:
            return True
        return self._config_cache.lookup(serial, *revision) is None

    @property
    def authenticating_over_ble(self) -> bool:
        command = self.current_command
        return (not self.finished and self._port_name.startswith("ble:")
                and command is not None
                and command.name in {"AUTH_GET_CERTIFICATE", "AUTH_CHALLENGE"})

    def authentication_interrupted(self, detail: str) -> None:
        command = self.current_command
        hello = self._responses.get("HELLO", {}).get("result", {})
        build = hello.get("versions", {}).get("build_id", "未知")
        self._finish_error(BootstrapError(
            BootstrapKind.AUTHENTICATION_INTERRUPTED,
            "蓝牙认证中断，正在重新连接",
            "控制台将重新建立蓝牙连接，并从头验证设备身份。"
            f"\n连接：蓝牙；固件：{build}；失败步骤：{command.name if command else 'UNKNOWN'}；{detail}",
            error_name="ble_auth_interrupted",
        ))

    def timeout(self) -> None:
        if self.authenticating_over_ble:
            self.authentication_interrupted("响应超时")
            return
        if self.finished:
            return
        command = self.current_command
        name = command.name if command else "UNKNOWN"
        kind = BootstrapKind.READ_FAILED
        self._finish_error(BootstrapError(kind, "读取设备超时", f"{name} 未在超时前返回"))

    def _advance(self) -> None:
        self._index += 1
        if self._index >= len(self._commands):
            try:
                if self._trust is None:
                    raise ValueError("设备信任状态缺失")
                snapshot = DeviceSnapshot.from_protocol(
                    hello=self._responses["HELLO"],
                    status=self._responses["GET_STATUS"],
                    config=self._responses["GET_CONFIG"],
                    capabilities=self._responses["CAPABILITIES"],
                    port_name=self._port_name,
                    trust=self._trust,
                )
            except (KeyError, TypeError, ValueError) as exc:
                error = BootstrapError(BootstrapKind.READ_FAILED, "设备返回的配置不完整", str(exc))
                if not self._begin_maintenance(error):
                    self._finish_error(error)
                return
            self.finished = True
            self._completed(snapshot)
            return
        self._request_id = self._next_request_id()
        if self._command_started is not None:
            self._command_started(self._commands[self._index].name)
        self._send(self._commands[self._index], self._request_id)

    def _prepare_authentication(self, payload: dict[str, Any]) -> None:
        result = payload.get("result")
        features = result.get("features") if isinstance(result, dict) else None
        supported = (
            isinstance(features, dict)
            and features.get("device_authentication") is True
        )
        if supported:
            try:
                self._nonce = self._authenticator.new_nonce()
            except ValueError as exc:
                raise DeviceAuthenticationError("nonce_generation", str(exc)) from exc
            self._commands.extend(
                (
                    Command("AUTH_GET_CERTIFICATE", AUTH_GET_CERTIFICATE, {}),
                    Command(
                        "AUTH_CHALLENGE",
                        AUTH_CHALLENGE,
                        {"nonce": self._nonce},
                    ),
                )
            )
        else:
            self._trust = self._authenticator.trust_without_device_identity(
                "CAPABILITIES.features.device_authentication is not true"
            )
            if (self._config_compatibility_error is not None
                    and self._config_compatibility_error.kind is BootstrapKind.INCOMPATIBLE):
                raise self._config_compatibility_error
        self._commands.extend(
            (
                Command("GET_STATUS", 0x13, {}),
                Command("GET_CONFIG", 0x10, {}),
            )
        )

    def _local_request_id(self) -> int:
        return (self._request_id % 0xFFFFFFFF) + 1

    def _finish_error(self, error: BootstrapError) -> None:
        command = self.current_command
        if command is not None and command.name in {"GET_STATUS", "GET_CONFIG"}:
            if self._begin_maintenance(error):
                return
        if self._maintenance_error is not None:
            error = BootstrapError(
                error.kind, "设备固件维护协议不可用",
                f"{self._maintenance_error}：{self._maintenance_error.technical}\n"
                f"FW_STATUS 验证失败：{error.technical}",
                error_name=error.error_name, details=error.details,
            )
        self.finished = True
        self._failed(error)

    def _begin_maintenance(self, error: BootstrapError) -> bool:
        if (self._maintenance_error is not None or self._port_name.startswith("ble:")
                or self._trust is None or not self._trust.is_authenticated):
            return False
        capabilities = self._responses.get("CAPABILITIES", {}).get("result", {})
        features = capabilities.get("features", {})
        limits = capabilities.get("limits", {})
        if (features.get("firmware_update") is not True
                or any(type(limits.get(key)) is not int or limits[key] <= 0
                       for key in ("firmware_chunk_bytes", "firmware_image_bytes"))):
            return False
        self._maintenance_error = error
        self._commands = [Command("FW_STATUS", 0x42, {}, timeout_ms=4000)]
        self._index = -1
        self._advance()
        return True

    def _complete_maintenance(self) -> None:
        hello = self._responses["HELLO"]["result"]
        assert self._trust is not None and self._maintenance_error is not None
        error = self._maintenance_error
        snapshot = DeviceSnapshot(
            identity=dict(hello["identity"]), versions=dict(hello["versions"]),
            compatibility=dict(hello["compatibility"]), hello_config=dict(hello["config"]),
            status=dict(self._responses.get("GET_STATUS", {}).get("result", {})),
            config_result={}, capabilities=dict(self._responses["CAPABILITIES"]["result"]),
            port_name=self._port_name, trust=self._trust,
            maintenance_reason=f"{error}：{error.technical}",
        )
        self.finished = True
        self._completed(snapshot)


class CommandSession:
    """One validated protocol command on an established device connection."""

    def __init__(
        self,
        *,
        command: Command,
        send: Callable[[Command, int], None],
        completed: Callable[[dict[str, Any]], None],
        failed: Callable[[BootstrapError], None],
        request_id: int,
        validator: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._command = command
        self._send = send
        self._completed = completed
        self._failed = failed
        self._request_id = request_id
        self._validator = validator
        self._retries_remaining = max(0, command.retries)
        self.finished = False

    @property
    def current_command(self) -> Command:
        return self._command

    @property
    def current_request_id(self) -> int:
        return self._request_id

    def start(self) -> None:
        self._send(self._command, self._request_id)

    def accept(self, frame: Frame) -> None:
        if self.finished or frame.request_id != self._request_id:
            return
        try:
            payload = frame.json_payload()
            _validate_response(self._command, frame, payload)
            if self._validator is not None:
                self._validator(payload)
            self.finished = True
            self._completed(payload)
        except BootstrapError as exc:
            self._finish_error(exc)
        except (ContractError, FrameError, KeyError, TypeError, ValueError) as exc:
            self._finish_error(
                BootstrapError(BootstrapKind.READ_FAILED, "设备命令执行失败", str(exc))
            )

    def timeout(self) -> None:
        if self.finished:
            return
        if self._retries_remaining > 0:
            self._retries_remaining -= 1
            # Reuse the exact request ID and payload. WMP1's replay cache makes
            # this safe for chunk writes and finalization after a lost ACK.
            self._send(self._command, self._request_id)
            return
        self._finish_error(
            BootstrapError(
                BootstrapKind.READ_FAILED,
                "设备命令响应超时",
                f"{self._command.name} 未在超时前返回",
                error_name="TRANSPORT_TIMEOUT",
            )
        )

    def _finish_error(self, error: BootstrapError) -> None:
        self.finished = True
        self._failed(error)


class StatusSession:
    """One validated GET_STATUS request on an established device connection."""

    def __init__(
        self,
        *,
        contract: Contract,
        send: Callable[[Command, int], None],
        completed: Callable[[dict[str, Any]], None],
        failed: Callable[[BootstrapError], None],
        request_id: int,
    ) -> None:
        self._contract = contract
        self._send = send
        self._completed = completed
        self._failed = failed
        self._command = Command("GET_STATUS", 0x13, {})
        self._request_id = request_id
        self._retry_remaining = True
        self.finished = False

    @property
    def current_command(self) -> Command:
        return self._command

    @property
    def current_request_id(self) -> int:
        return self._request_id

    def start(self) -> None:
        self._send(self._command, self._request_id)

    def accept(self, frame: Frame) -> None:
        if self.finished or frame.request_id != self._request_id:
            return
        try:
            payload = frame.json_payload()
            _validate_response(self._command, frame, payload)
            self._contract.validate_status(payload)
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ContractError("GET_STATUS result 必须是 object")
            self.finished = True
            self._completed(dict(result))
        except BootstrapError as exc:
            self._finish_error(exc)
        except (ContractError, FrameError, KeyError, TypeError, ValueError) as exc:
            self._finish_error(
                BootstrapError(BootstrapKind.READ_FAILED, "设备状态同步失败", str(exc))
            )

    def timeout(self) -> None:
        if not self.finished and self._retry_remaining:
            # Only the established connection's read-only status poll retries.
            # A second timeout still closes the connection; writes and the
            # authentication handshake retain their existing failure rules.
            self._retry_remaining = False
            self.start()
            return
        if not self.finished:
            self._finish_error(
                BootstrapError(BootstrapKind.READ_FAILED, "设备状态同步超时", "GET_STATUS 未在超时前返回")
            )

    def _finish_error(self, error: BootstrapError) -> None:
        self.finished = True
        self._failed(error)


def _validate_response(command: Command, frame: Frame, payload: dict[str, Any]) -> None:
    if not (frame.flags & RESPONSE_FLAG):
        raise FrameError("收到的帧不是响应")
    if frame.message_type == NACK or frame.flags & ERROR_FLAG:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        name = str(error.get("name", "UNKNOWN"))
        message = str(error.get("message", "设备拒绝请求"))
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        incompatible_names = {"UNSUPPORTED_PROTOCOL", "UNSUPPORTED_SCHEMA", "HARDWARE_MISMATCH"}
        if command.name.startswith("AUTH_"):
            kind = BootstrapKind.AUTHENTICITY_FAILED
        elif command.name == "HELLO" and name in incompatible_names:
            kind = BootstrapKind.INCOMPATIBLE
        else:
            kind = BootstrapKind.READ_FAILED
        raise BootstrapError(
            kind,
            _message_for(kind),
            f"{name}: {message}",
            error_name=name,
            details=details,
        )
    if frame.message_type != ACK:
        raise FrameError(f"响应类型 0x{frame.message_type:02x} 不是 ACK/NACK")
    if payload.get("command") != command.name:
        raise FrameError(f"响应 command 与请求 {command.name} 不匹配")


def _message_for(kind: BootstrapKind) -> str:
    if kind is BootstrapKind.INCOMPATIBLE:
        return "这台设备与当前 BORING 控制台不兼容"
    if kind is BootstrapKind.AUTHENTICITY_FAILED:
        return "无法确认这是 BORING 设备"
    return "未能完整读取设备配置"
