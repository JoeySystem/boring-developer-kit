from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

from .devices import DeviceKind, detect_devices, load_ports, select_runtime_ports
from .ble_recovery import clear_authorized_slot, clear_single_slot, inspect_device
from .error_text import friendly_error
from .errors import ProvisioningError, TransportError, VerificationError
from .esptool_runner import EsptoolRunner
from .models import ProvisionMode, RecordKind
from .models import ProvisionRequest
from .joystick_recovery import (
    advance_direction_verification,
    new_direction_verification,
    parse_calibration_sample,
    validate_calibration_activation,
)
from .ota import CdcOtaUpdater
from .paths import (
    bundled_acceptance_test,
    discover_production_authority_path,
    history_root,
    resource_root,
)
from .protocol import WmpClient
from .voice_key import ensure_voice_key_default, key8_matches_default
from .queue import SequentialBatch
from .release import ReleasePackage
from .runtime import RuntimeVerifier
from .summaries import package_fingerprint, package_summary
from .workflow import Provisioner


ALLOWED_AUTHORITY_STATES = {"sample-verified", "rc", "released"}
# Only an App built with --acceptance-test may carry an unverified build.
ACCEPTANCE_TEST_STATES = ALLOWED_AUTHORITY_STATES | {"built"}
ACCEPTANCE_TEST_COMPLETION_MESSAGE = (
    "验收测试已完成。本台装的是未经样机验证的测试固件，不能交付；"
    "测试结束后请用正式工具重新更新。"
)
TERMINAL_JOB_STATUSES = {"passed", "failed", "action-required", "retryable"}
PREFLIGHT_BUSY_TIMEOUT_S = 8.0
SHUTDOWN_DRAIN_TIMEOUT_S = 20.0
ESP_ROM_DOWNLOAD_USB = (0x303A, 0x1001)
TERMINAL_REPAIR_PHASES = {"failed", "completed"}
# board_matrix12_v1.c: JOYSTICK_X/Y_HARDWARE_INVERTED. On Power V2 a larger raw
# ADC value means the stick is pushed left (X) or up (Y) unless the user's
# invert setting cancels it; the page needs this to draw the live position.
POWER_V2_JOYSTICK_HARDWARE_INVERTED = (True, True)


def _relevant_device(device) -> bool:
    """Only BORING MIST itself matters: runtime CDC or ESP32-S3 ROM download.

    Generic USB serial adapters (CH340, CP210x, FTDI, Windows "USB Serial
    Device") belong to unrelated equipment and must not block the tool.
    """
    if device.kind is DeviceKind.RUNTIME:
        return True
    pair = (getattr(device, "vid", None), getattr(device, "pid", None))
    return pair == ESP_ROM_DOWNLOAD_USB


def _with_mark(repair: dict, name: str) -> dict:
    """Return a copy of repair with a monotonic timestamp for a milestone.

    The first time a milestone is reached wins, so retries do not shorten
    the measured stage.
    """
    marks = dict(repair.get("marks") or {})
    marks.setdefault(name, time.monotonic())
    return {**repair, "marks": marks}


REPAIR_STAGE_SPANS = (
    ("ota_s", "started", "ota_done"),
    ("slot_check_and_clear_s", "ota_done", "calibration_started"),
    ("calibration_capture_s", "calibration_started", "calibration_confirm"),
    ("calibration_save_readback_s", "calibration_confirm", "calibration_saved"),
    ("direction_verification_s", "calibration_saved", "direction_done"),
    ("total_s", "started", "ended"),
)


def repair_timings(marks: dict) -> dict:
    timings = {}
    for key, start, end in REPAIR_STAGE_SPANS:
        if start in marks and end in marks:
            timings[key] = round(marks[end] - marks[start], 2)
        else:
            timings[key] = None
    return timings


def host_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


COMPLETION_MESSAGE = (
    "固件更新与摇杆校准已完成，蓝牙槽位已全部清空。拔下 USB 即可交付；"
    "请勿用本机测试蓝牙连接，否则会占用一个槽位。"
)


def screen_orientation(before: dict | None) -> dict:
    """How raw calibration samples map to the screen for this device.

    flip_x/flip_y true means a larger raw value is left/up on screen.
    """
    joystick = {}
    if isinstance(before, dict):
        config = (before.get("raw_config") or {}).get("config") or {}
        joystick = config.get("joystick") or {}
    hardware_x, hardware_y = POWER_V2_JOYSTICK_HARDWARE_INVERTED
    user_x = bool((joystick.get("x") or {}).get("invert"))
    user_y = bool((joystick.get("y") or {}).get("invert"))
    return {"flip_x": hardware_x != user_x, "flip_y": hardware_y != user_y}


class PreflightPortBusy(ProvisioningError):
    pass


def _serial_port_holders(port: str) -> list[dict]:
    """Return processes that currently have the serial device open."""
    if sys.platform == "win32":
        # Windows reports exclusive COM-port access when opening the port.
        return []
    try:
        result = subprocess.run(
            ["lsof", "-Fpc", "--", port],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode not in {0, 1}:
        return []

    holders = []
    current_pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            current_pid = int(line[1:])
        elif line.startswith("c") and current_pid is not None:
            holders.append({"pid": current_pid, "command": line[1:] or "其他程序"})
            current_pid = None
    return holders


def _port_busy_message(port: str, holders: list[dict]) -> str:
    if holders:
        programs = "、".join(
            f"{holder['command']}（PID {holder['pid']}）" for holder in holders
        )
        subject = f"串口被 {programs}占用"
    else:
        subject = f"串口 {port} 被其他程序占用"
    return (
        f"{subject}。请先退出占用串口的程序；"
        "工具检测到端口释放后会自动允许重试。"
    )


def _describe_serial_failure(
    port: str,
    error: str,
    *,
    holders: list[dict] | None = None,
) -> dict:
    lowered = error.lower()
    if sys.platform == "win32" and any(
        marker in lowered
        for marker in ("permissionerror(13", "winerror 5", "access is denied", "拒绝访问")
    ):
        return {
            "retryable": True,
            "message": (
                f"无法打开串口 {port}：Windows 拒绝访问。请先关闭 BORING Console、"
                "串口助手或其他烧录程序，再重试；若仍失败，请重新插入 USB。"
            ),
        }
    retryable = "resource busy" in lowered or "errno 16" in lowered
    if not retryable:
        return {"retryable": False, "message": friendly_error(error)}
    active_holders = _serial_port_holders(port) if holders is None else holders
    return {
        "retryable": True,
        "message": _port_busy_message(port, active_holders),
    }


FIRMWARE_SNAPSHOT_FIELDS = (
    "running_version",
    "running_partition",
    "rollback_pending",
    "state",
    "current_boot_sequence",
    "current_boot_stage",
    "current_confirm_error",
    "previous_boot_sequence",
    "previous_boot_stage",
    "previous_reset_reason",
    "previous_confirm_error",
)


def _firmware_snapshot(inspection: dict | None) -> dict | None:
    """Build and boot diagnostics from one inspect_device() result."""
    if not isinstance(inspection, dict):
        return None
    identity = inspection.get("identity") or {}
    status = (inspection.get("raw_status") or {}).get("firmware_update") or {}
    snapshot = {"build_id": identity.get("build_id")}
    snapshot.update({key: status.get(key) for key in FIRMWARE_SNAPSHOT_FIELDS})
    return snapshot


def _already_running_package(inspection: dict, authority: dict) -> bool:
    """True when the device already runs exactly this build, confirmed.

    Flashing it again only adds a restart, and with firmware that restarts
    without leaving the bus every restart risks Windows error 433.
    """
    identity = inspection.get("identity") or {}
    status = (inspection.get("raw_status") or {}).get("firmware_update") or {}
    build_id = authority.get("build_id")
    return (
        isinstance(build_id, str)
        and bool(build_id)
        and identity.get("build_id") == build_id
        and status.get("state") == "IDLE"
        and status.get("rollback_pending") is False
        and status.get("reboot_pending") is not True
    )


CALIBRATION_BUSY_RETRY_S = 10.0
# Idle acceptance waits up to this long for about 1 s of neutral readings.
IDLE_SETTLE_TIMEOUT_S = 5.0
IDLE_SETTLE_STABLE_READS = 5


def explain_calibration_busy(status: dict | None) -> str:
    """Say in Chinese why the device refused CALIBRATION_START."""
    status = status if isinstance(status, dict) else {}
    action = status.get("action_engine") or {}
    calibration = status.get("calibration") or {}
    firmware = status.get("firmware_update") or {}
    reasons = []
    if status.get("pending"):
        reasons.append("设备正在保存配置")
    if firmware.get("state") not in (None, "IDLE"):
        reasons.append("设备仍处于固件更新状态")
    if action.get("quick_config_active"):
        reasons.append("设备停在快捷设置界面")
    if action.get("idle_standby_active"):
        reasons.append("设备处于闲置待机（屏幕变暗）")
    if action.get("usb_standby_active"):
        reasons.append("设备处于 USB 关机待机")
    page = action.get("local_page")
    if page not in (None, "none"):
        reasons.append(f"设备屏幕停在本机页面（{page}）")
    output = action.get("host_output_state")
    if output in ("calibration", "calibration_acquiring") or calibration.get("state") not in (
        None, "INACTIVE", "WAIT_NEUTRAL"
    ):
        reasons.append("设备上已有一项未结束的校准")
    if not reasons:
        reasons.append(f"设备输出状态为 {output or '未知'}")
    return (
        "设备暂时不能开始摇杆校准：" + "；".join(reasons)
        + "。请确认屏幕回到主页或待机画面，再点“重新开始”；工具会跳过烧录直接继续。"
    )


def idle_failure_reasons(samples: list, before_adc_failures) -> str:
    """Which part of the post-calibration idle check failed, in Chinese."""
    reasons = []
    if any(item.get("directions") != 0 for item in samples):
        reasons.append("松手时仍检测到方向输入（回中不良或中心偏移）")
    if any(item.get("inputs_neutral") is not True for item in samples):
        reasons.append("设备报告输入未回到中立（可能是固件仍在等待回中）")
    if any(item.get("joystick_suspect") is not False for item in samples):
        reasons.append("设备判定摇杆读数可疑")
    counts = [item.get("sample_count") for item in samples]
    if not all(isinstance(value, int) for value in counts) or (
        counts and counts[-1] <= counts[0]
    ):
        reasons.append("摇杆采样没有更新")
    failures = [item.get("adc_failure_count") for item in samples]
    if failures and failures[-1] != before_adc_failures:
        reasons.append("静置期间出现 ADC 读数失败")
    return "；".join(reasons) or "原因未知"


def _is_busy_refusal(error: Exception) -> bool:
    return "设备拒绝请求：BUSY" in str(error)


def _receipt_timings(path) -> dict | None:
    try:
        timings = json.loads(Path(path).read_text(encoding="utf-8")).get("timings_s")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return timings if isinstance(timings, dict) else None


def _read_target_serial(port: str, package: ReleasePackage) -> str:
    import serial

    with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
        client = WmpClient(connection, timeout_s=4.0)
        target_serial, _, _ = CdcOtaUpdater()._verify_identity(
            client, package.ota_application
        )
        return target_serial


class AuthorityUpdateState:
    """Restricted state for one-device, runtime-only authority updates."""

    MAX_RETAINED_JOBS = 10
    MAX_EVENTS_PER_JOB = 500

    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._requests_idle = threading.Condition(self.lock)
        self._active_requests = 0
        self._authority_cache: tuple[Path, dict, object] | None = None
        self.on_shutdown_requested = None
        self.busy_job: str | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.latest_job_id: str | None = None
        self.repair: dict | None = None
        self.calibration: dict | None = None
        self._calibration_connection = None
        self._calibration_client: WmpClient | None = None

    @staticmethod
    def _authority_path() -> Path:
        path = discover_production_authority_path()
        if path is None:
            raise ProvisioningError("当前权威包未配置，请由负责人更新工具")
        return path.resolve()

    def _authority_entry(self) -> tuple[Path, dict, object]:
        """Validate the bundled package once; scans reuse the cached summary.

        Opening a package copies and hashes every image, which is too costly
        for the 120-250 ms calibration polls. start_authority_update re-opens
        the package and compares fingerprints before any write.
        """
        path = self._authority_path()
        with self.lock:
            cached = self._authority_cache
        if cached is not None and cached[0] == path:
            return cached
        with ReleasePackage.open(path) as package:
            if package.hardware_id != "WMP-S3-MATRIX12-POWER-V2":
                raise ProvisioningError("当前固件包不适用于 BORING MIST")
            acceptance_test = getattr(sys, "frozen", False) and bundled_acceptance_test()
            allowed = ACCEPTANCE_TEST_STATES if acceptance_test else ALLOWED_AUTHORITY_STATES
            if package.validation_state not in allowed:
                raise ProvisioningError("当前权威包尚未通过样机验证，不能用于临时更新")
            if package.ota_application is None:
                raise ProvisioningError("当前固件包不支持安全 USB 更新")
            summary = package_summary(package, path)
            summary["acceptance_test"] = bool(acceptance_test)
            entry = (path, summary, package.runtime_usb)
        with self.lock:
            self._authority_cache = entry
        return entry

    def _acceptance_test(self) -> bool:
        with self.lock:
            cached = self._authority_cache
        return bool(cached is not None and cached[1].get("acceptance_test"))

    def invalidate_authority(self) -> None:
        with self.lock:
            self._authority_cache = None

    def authority(self) -> dict:
        return deepcopy(self._authority_entry()[1])

    def scan(self) -> dict:
        _, cached_authority, runtime_usb = self._authority_entry()
        authority = deepcopy(cached_authority)
        devices = [
            device.to_dict()
            for device in detect_devices(runtime_usb)
            if _relevant_device(device)
        ]
        runtime = [device for device in devices if device["kind"] == "runtime"]
        block_reason = None
        if len(devices) == 1 and len(runtime) == 1:
            with self.lock:
                calibration_port = (
                    self.calibration.get("port") if self.calibration is not None else None
                )
            holders = (
                []
                if calibration_port == runtime[0]["device"]
                else _serial_port_holders(runtime[0]["device"])
            )
            if holders:
                readiness = "blocked"
                block_reason = "port-busy"
                message = _port_busy_message(runtime[0]["device"], holders)
            else:
                readiness = "ready"
                message = "已识别 1 台正常模式设备"
        elif not devices:
            readiness = "waiting"
            message = "请将 BORING MIST 正常开机，并使用支持数据传输的 USB 线连接"
        elif len(runtime) == 0:
            readiness = "blocked"
            block_reason = "boot-mode"
            message = "设备没有正常启动；请断开 USB 后重新连接，并保持设备正常开机"
        else:
            readiness = "blocked"
            block_reason = "multiple-devices"
            message = "一次只能连接 1 台设备，请拔下其他设备"
        return {
            "authority": authority,
            "devices": devices,
            "readiness": readiness,
            "block_reason": block_reason,
            "message": message,
        }

    def bootstrap(self) -> dict:
        result = self.scan()
        with self.lock:
            latest_job_id = self.latest_job_id
            busy = self.busy_job is not None
            repair = self._public_repair_locked()
            calibration = self._public_calibration_locked()
        result.update(
            {
                "token": self.token,
                "platform": host_platform(),
                "busy": busy,
                "latest_job": self.job(latest_job_id) if latest_job_id else None,
                "repair": repair,
                "calibration": calibration,
            }
        )
        return result

    def _public_repair_locked(self) -> dict | None:
        repair = self.repair
        if repair is None:
            return None
        return {
            key: deepcopy(repair.get(key))
            for key in (
                "id",
                "phase",
                "status",
                "progress",
                "message",
                "port",
                "serial",
                "job_id",
                "cleared_slots",
                "clear_outcome",
                "final_bond_count",
                "ota_receipt_path",
                "clear_receipt_paths",
                "calibration_receipt_path",
                "session_receipt_path",
                "timings_s",
                "started_at",
                "ota_skipped",
                "step",
                "voice_key",
            )
        }

    def _public_calibration_locked(self) -> dict | None:
        session = self.calibration
        if session is None:
            return None
        activation = session.get("activation")
        public_activation = None
        if isinstance(activation, dict):
            public_activation = {
                key: deepcopy(activation.get(key))
                for key in ("generation", "calibrated", "x", "y", "directions")
            }
        return {
            "phase": session.get("phase"),
            "port": session.get("port"),
            "serial": session.get("serial"),
            "session_id": session.get("session_id"),
            "base_generation": session.get("base_generation"),
            "sample": deepcopy(session.get("last_sample")),
            "activation": public_activation,
            "idle_neutral_passed": session.get("idle_neutral_passed"),
            "direction_verification": deepcopy(session.get("direction_verification")),
            "receipt_path": session.get("receipt_path"),
            "confirmation_error": session.get("confirmation_error"),
            "orientation": screen_orientation(session.get("before")),
        }

    def _validated_runtime_port(
        self,
        requested_port: str,
        *,
        allow_calibration: bool = False,
    ) -> str:
        if not isinstance(requested_port, str) or not requested_port:
            raise ProvisioningError("请先接入并识别设备")
        scanned = self.scan()
        if scanned["readiness"] != "ready":
            raise ProvisioningError(scanned["message"])
        port = scanned["devices"][0]["device"]
        if port != requested_port:
            raise ProvisioningError("设备状态已变化，请重新扫描")
        with self.lock:
            if self.closing:
                raise ProvisioningError("工具正在关闭")
            if self.busy_job is not None:
                raise ProvisioningError("固件更新正在进行，请完成后再操作")
            if self.calibration is not None and not allow_calibration:
                raise ProvisioningError("摇杆校准正在进行，请先完成或取消校准")
        return port

    def _validated_calibration_port(self, requested_port: str, session: dict) -> str:
        """Cheap per-poll check for an open calibration session.

        Calibration polls arrive 4-8 times a second. Re-enumerating every
        serial port each time (SetupAPI on Windows) adds latency to the live
        joystick readout and proves nothing new: the session already holds the
        port open on a device whose serial was verified when it started, and
        an unplugged device fails the next read on that handle immediately.
        """
        with self.lock:
            if self.closing:
                raise ProvisioningError("工具正在关闭")
            if self.busy_job is not None:
                raise ProvisioningError("固件更新正在进行，请完成后再操作")
        if requested_port != session.get("port"):
            raise ProvisioningError("校准期间设备端口发生变化")
        return session["port"]

    def inspect_runtime(self, requested_port: str) -> dict:
        import serial

        port = self._validated_runtime_port(requested_port)
        try:
            with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
                return inspect_device(WmpClient(connection, timeout_s=4.0))
        except OSError as exc:
            raise ProvisioningError(_describe_serial_failure(port, str(exc))["message"]) from exc

    def clear_runtime_slot(
        self,
        requested_port: str,
        *,
        slot: int,
        expected_serial: str,
        confirmation: str,
    ) -> dict:
        import serial

        port = self._validated_runtime_port(requested_port)
        if not isinstance(expected_serial, str) or not expected_serial:
            raise ProvisioningError("请先读取设备状态，再选择需要清除的槽位")
        try:
            with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
                result = clear_single_slot(
                    WmpClient(connection, timeout_s=4.0),
                    slot=slot,
                    expected_serial=expected_serial,
                    confirmation=confirmation,
                )
        except OSError as exc:
            raise ProvisioningError(_describe_serial_failure(port, str(exc))["message"]) from exc

        return self._record_slot_clear(port, result)

    def _record_slot_clear(self, port: str, result: dict) -> dict:
        expected_serial = result["serial"]
        slot = result["slot"]
        receipt = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": True,
            "record_kind": RecordKind.VALIDATION.value,
            "mode": "ble-slot-clear",
            "port": port,
            **result,
        }
        receipt_root = history_root() / "ble-recovery"
        receipt_root.mkdir(parents=True, exist_ok=True)
        receipt_path = receipt_root / (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
            f"{expected_serial}-slot-{slot}.json"
        )
        temporary = receipt_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(receipt_path)
        return {**result, "receipt_path": str(receipt_path)}

    def _clear_repair_slot(
        self,
        port: str,
        *,
        slot: int,
        expected_serial: str,
    ) -> dict:
        import serial

        try:
            with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
                result = clear_authorized_slot(
                    WmpClient(connection, timeout_s=4.0),
                    slot=slot,
                    expected_serial=expected_serial,
                )
        except OSError as exc:
            raise ProvisioningError(_describe_serial_failure(port, str(exc))["message"]) from exc
        return self._record_slot_clear(port, result)

    def _repair_failed(self, repair: dict, error: Exception) -> None:
        failed = self._finish_repair_record(
            {
                **repair,
                "phase": "failed",
                "status": "failed",
                "message": friendly_error(str(error)),
            },
            outcome="failed",
        )
        with self.lock:
            self.repair = failed

    def _finish_repair_record(self, repair: dict, *, outcome: str) -> dict:
        """Write one record per repaired device with the time each stage took.

        Written for passed and failed repairs alike, so slow or failing
        stations can be compared from the history folder alone.
        """
        repair = _with_mark(repair, "ended")
        timings = repair_timings(repair["marks"])
        with self.lock:
            cached = self._authority_cache
        serial = repair.get("serial") or "unknown"
        payload = {
            "schema": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "record_kind": RecordKind.VALIDATION.value,
            "mode": "repair-session",
            "success": outcome == "passed",
            "outcome": outcome,
            "serial": serial,
            "platform": host_platform(),
            "build_id": cached[1].get("build_id") if cached is not None else None,
            "started_at": repair.get("started_at"),
            "message": repair.get("message"),
            "cleared_slots": repair.get("cleared_slots"),
            "clear_outcome": repair.get("clear_outcome"),
            "final_bond_count": repair.get("final_bond_count"),
            "voice_key": repair.get("voice_key"),
            "timings_s": timings,
            "ota_timings_s": repair.get("ota_timings_s"),
            "ota_skipped": bool(repair.get("ota_skipped")),
            "acceptance_test": bool(
                cached is not None and cached[1].get("acceptance_test")
            ),
            # What ran before the update and how the device booted after it:
            # previous_boot_stage/reset_reason show where a failed restart
            # stopped (see firmware boot diagnostics).
            "device_before": _firmware_snapshot(repair.get("before")),
            "device_after": repair.get("device_after"),
            "receipts": {
                "ota": repair.get("ota_receipt_path"),
                "slot_clear": repair.get("clear_receipt_paths"),
                "calibration": repair.get("calibration_receipt_path"),
            },
        }
        repair = {**repair, "timings_s": timings}
        try:
            root = history_root() / "repair-sessions"
            root.mkdir(parents=True, exist_ok=True)
            path = root / (
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{serial}-{outcome}.json"
            )
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
            repair["session_receipt_path"] = str(path)
        except OSError:
            # A missing record must never turn a good repair into a failure.
            pass
        return repair

    def _repair_for_calibration(self, serial: str, session_id: int) -> dict | None:
        with self.lock:
            repair = deepcopy(self.repair)
            calibration = deepcopy(self.calibration)
        if (
            repair is None
            or repair.get("phase") not in {"calibrating", "verifying-joystick"}
            or repair.get("serial") != serial
        ):
            return None
        if calibration is not None and calibration.get("session_id") != session_id:
            return None
        return repair

    def start_repair(self, requested_port: str) -> dict:
        with self.lock:
            existing = deepcopy(self.repair)
        if existing is not None and existing.get("phase") not in TERMINAL_REPAIR_PHASES:
            raise ProvisioningError("上一台还在进行中，请按当前页面继续")

        # A finished or failed repair never blocks the next device: each
        # repair is bound to the serial read here and verified at every step.
        started = time.monotonic()
        inspection = self.inspect_runtime(requested_port)
        serial = inspection["identity"]["serial"]

        skipped = bool(inspection["identity"].get("build_id")) and (
            _already_running_package(inspection, self.authority())
        )
        if skipped:
            job_id = self._start_skipped_update(requested_port, inspection)
        else:
            job_id = self.start_authority_update(requested_port)
        repair = {
            "id": str(uuid.uuid4()),
            "phase": "ota",
            "status": "running",
            "progress": 5,
            "message": "正在刷入目标固件并精确读回",
            "port": requested_port,
            "serial": serial,
            "job_id": job_id,
            "before": inspection,
            "ota_skipped": skipped,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "marks": {"started": started},
        }
        with self.lock:
            self.repair = repair
            public = self._public_repair_locked()
        return public

    def _start_skipped_update(self, port: str, inspection: dict) -> str:
        """Record an already-passed job for a device that runs this build.

        The repair flow continues exactly as after a real update (slot clear,
        calibration), so the page and records need no special case.
        """
        identity = inspection["identity"]
        status = (inspection.get("raw_status") or {}).get("firmware_update") or {}
        now = datetime.now(timezone.utc).isoformat()
        message = f"设备已运行目标固件 {identity.get('build_id')}，跳过烧录，不重启设备"
        job_id = str(uuid.uuid4())
        with self.lock:
            if self.busy_job is not None:
                raise ProvisioningError("已有更新任务正在运行")
            self.jobs[job_id] = {
                "id": job_id,
                "status": "passed",
                "progress": 100,
                "stage": "done",
                "message": message,
                "release_id": self._authority_cache[1].get("release_id")
                if self._authority_cache is not None else None,
                "mode": ProvisionMode.UPDATE.value,
                "record_kind": RecordKind.VALIDATION.value,
                "ports": [port],
                "events": [
                    {"time": now, "port": port, "stage": "done",
                     "message": message, "progress": 100}
                ],
                "event_base": 0,
                "results": [
                    {
                        "port": port,
                        "success": True,
                        "skipped": True,
                        "mac": str(identity.get("serial", "")).removeprefix("CP01-"),
                        "verification": "already-running",
                        "runtime": {
                            **identity,
                            "port": port,
                            "running_partition": status.get("running_partition"),
                            "rollback_pending": status.get("rollback_pending"),
                        },
                        "runtime_port": port,
                        "receipt_path": None,
                        "timings_s": None,
                    }
                ],
                "created_at": now,
                "completed_at": now,
            }
            self.latest_job_id = job_id
        return job_id

    def reset_repair(self) -> dict:
        """Clear a finished or failed repair so the next device can start."""
        with self.lock:
            repair = self.repair
            if repair is not None and repair.get("phase") not in TERMINAL_REPAIR_PHASES:
                raise ProvisioningError("上一台还在进行中，请按当前页面继续")
            if self.calibration is not None:
                raise ProvisioningError("摇杆校准正在进行，请先完成或取消校准")
            self.repair = None
        return {"repair": None}

    def _clear_all_repair_slots(self, port: str, *, expected_serial: str) -> dict:
        """Clear every occupied BLE slot so the device ships with none in use.

        Each slot goes through the same guarded single-slot clear (serial pin,
        before/after readback, other slots unchanged); the final readback must
        show all three slots empty.
        """
        inspection = self.inspect_runtime(port)
        if inspection["identity"]["serial"] != expected_serial:
            raise ProvisioningError("清空前设备序列号发生变化")
        occupied = [
            item["slot"]
            for item in inspection["ble"]["slots"]
            if item.get("paired") and item.get("slot") in {1, 2, 3}
        ]
        receipts = []
        for slot in occupied:
            result = self._clear_repair_slot(port, slot=slot, expected_serial=expected_serial)
            receipts.append(result["receipt_path"])
        final = self.inspect_runtime(port) if occupied else inspection
        if final["identity"]["serial"] != expected_serial:
            raise ProvisioningError("清空后设备序列号发生变化")
        remaining = [item["slot"] for item in final["ble"]["slots"] if item.get("paired")]
        if remaining:
            labels = "、".join(str(slot) for slot in remaining)
            raise ProvisioningError(f"清空后槽位 {labels} 仍报告已配对，请重新开始")
        return {
            "cleared_slots": occupied,
            "receipts": receipts,
            "final_bond_count": final["ble"].get("bond_count"),
            "firmware": _firmware_snapshot(final),
        }

    def _apply_voice_key_default(self, port: str, *, expected_serial: str) -> dict:
        """Give Key 8 the factory voice/send gesture on this exact unit."""
        import serial

        with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
            client = WmpClient(connection, timeout_s=4.0)
            self._verify_calibration_device(client, expected_serial)
            return ensure_voice_key_default(client)

    def _confirm_voice_key_final(self, port: str, *, expected_serial: str) -> None:
        """Read-only last look: the finished unit must still have the factory Key 8."""
        import serial

        with serial.Serial(port, 115200, timeout=0.05, write_timeout=1) as connection:
            client = WmpClient(connection, timeout_s=4.0)
            self._verify_calibration_device(client, expected_serial)
            config = client.get_config().get("config")
        if not key8_matches_default(config):
            raise VerificationError("交付前最终读回：第 8 键不是出厂语音手势，停止交付")

    def _start_repair_calibration(self, repair: dict, port: str) -> dict:
        self.start_joystick_calibration(
            port,
            expected_serial=repair["serial"],
        )
        with self.lock:
            self.repair = {
                **_with_mark(repair, "calibration_started"),
                "phase": "calibrating",
                "step": "calibrating",
                "status": "running",
                "progress": 65,
                "port": port,
                "message": "请按页面提示完成摇杆四向全行程和回中",
            }
            public = self._public_repair_locked()
            calibration = self._public_calibration_locked()
        return {"repair": public, "calibration": calibration}

    def advance_repair(self, job_id: str) -> dict:
        with self.lock:
            repair = deepcopy(self.repair)
            job = deepcopy(self.jobs.get(job_id))
        if repair is None or repair.get("job_id") != job_id:
            raise ProvisioningError("当前任务不存在或已变化")
        if repair.get("phase") != "ota":
            with self.lock:
                return {"repair": self._public_repair_locked()}
        if job is None or job.get("status") not in TERMINAL_JOB_STATUSES:
            with self.lock:
                return {"repair": self._public_repair_locked()}
        if job.get("status") != "passed":
            error = ProvisioningError(job.get("message") or "固件更新未完成")
            self._repair_failed(repair, error)
            with self.lock:
                return {"repair": self._public_repair_locked()}

        try:
            results = job.get("results") or []
            runtime = results[0].get("runtime", {}) if results else {}
            port = results[0].get("runtime_port") if results else None
            if not isinstance(port, str) or not port:
                raise ProvisioningError("固件读回未返回运行态串口")
            if runtime.get("serial") != repair["serial"]:
                raise ProvisioningError("固件更新后设备序列号与本台不一致")
            repair = _with_mark(repair, "ota_done")
            repair.update(
                phase="post-ota",
                step="slots",
                progress=52,
                port=port,
                ota_timings_s=results[0].get("timings_s") if results else None,
                ota_receipt_path=results[0].get("receipt_path") if results else None,
                message="固件已精确读回，正在清空蓝牙槽位",
            )
            with self.lock:
                self.repair = repair
            cleared = self._clear_all_repair_slots(port, expected_serial=repair["serial"])
            repair.update(
                cleared_slots=cleared["cleared_slots"],
                clear_outcome="cleared-all" if cleared["cleared_slots"] else "already-empty",
                clear_receipt_paths=cleared["receipts"],
                final_bond_count=cleared["final_bond_count"],
                device_after=cleared.get("firmware"),
                step="voice-key",
                progress=58,
                message="蓝牙槽位已全部清空，正在设置第 8 键默认行为",
            )
            with self.lock:
                self.repair = repair
            voice_key = self._apply_voice_key_default(port, expected_serial=repair["serial"])
            repair.update(
                voice_key=voice_key,
                step="calibration-start",
                message="第 8 键已设为单击说话、双击发送，正在开始摇杆校准",
            )
            return self._start_repair_calibration(repair, port)
        except Exception as exc:
            self._repair_failed(repair, exc)
            raise

    def _calibration_snapshot(
        self,
        *,
        expected_serial: str,
        session_id: int,
        phases: set[str],
    ) -> dict:
        with self.lock:
            session = deepcopy(self.calibration)
        if session is None:
            raise ProvisioningError("当前没有进行中的摇杆校准")
        if session.get("serial") != expected_serial or session.get("session_id") != session_id:
            raise ProvisioningError("校准会话与已确认设备不一致")
        if session.get("phase") not in phases:
            raise ProvisioningError("当前校准阶段不允许执行此操作")
        return session

    def _active_calibration_client(self) -> WmpClient:
        with self.lock:
            client = self._calibration_client
        if client is None:
            raise ProvisioningError("校准串口会话已经中断")
        return client

    def _close_calibration_connection(self) -> None:
        with self.lock:
            connection = self._calibration_connection
            self._calibration_connection = None
            self._calibration_client = None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _verify_calibration_device(client: WmpClient, expected_serial: str):
        verification = client.verify(expected_hardware_id="WMP-S3-MATRIX12-POWER-V2")
        if verification.serial != expected_serial:
            raise ProvisioningError(
                f"当前设备 {verification.serial} 与校准设备 {expected_serial} 不一致"
            )
        return verification

    def start_joystick_calibration(
        self,
        requested_port: str,
        *,
        expected_serial: str,
    ) -> dict:
        import serial

        port = self._validated_runtime_port(requested_port)
        if not expected_serial:
            raise ProvisioningError("请先读取设备状态，再开始摇杆校准")
        connection = None
        try:
            connection = serial.Serial(port, 115200, timeout=0.05, write_timeout=1)
            client = WmpClient(connection, timeout_s=4.0)
            before = inspect_device(client, expected_serial=expected_serial)
            capabilities = client.get_capabilities()
            features = capabilities["features"]
            if features.get("joystick_calibration") is not True:
                raise ProvisioningError("当前固件不支持摇杆校准")
            # The device may still be settling after its restart (boot
            # animation, saving the platform): retry a refusal briefly, then
            # explain which state is blocking instead of the raw NACK.
            deadline = time.monotonic() + CALIBRATION_BUSY_RETRY_S
            while True:
                try:
                    started = client.calibration_start()
                    break
                except VerificationError as exc:
                    if not _is_busy_refusal(exc):
                        raise
                    if time.monotonic() >= deadline:
                        try:
                            status = client.get_status()
                        except Exception:
                            status = None
                        raise ProvisioningError(explain_calibration_busy(status)) from exc
                    time.sleep(0.5)
            sample = parse_calibration_sample(started)
        except Exception as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, OSError):
                raise ProvisioningError(
                    _describe_serial_failure(port, str(exc))["message"]
                ) from exc
            raise
        session = {
            "phase": "calibrating",
            "port": port,
            "serial": expected_serial,
            "session_id": sample["session_id"],
            "base_generation": before["identity"]["config_generation"],
            "before": before,
            "capabilities": capabilities,
            "last_sample": sample,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            if self.calibration is not None:
                connection.close()
                raise ProvisioningError("另一项摇杆校准已经开始")
            self.calibration = session
            self._calibration_connection = connection
            self._calibration_client = client
        return {
            "serial": expected_serial,
            "base_generation": session["base_generation"],
            "sample": sample,
        }

    def sample_joystick_calibration(
        self,
        requested_port: str,
        *,
        expected_serial: str,
        session_id: int,
    ) -> dict:
        session = self._calibration_snapshot(
            expected_serial=expected_serial,
            session_id=session_id,
            phases={"calibrating"},
        )
        self._validated_calibration_port(requested_port, session)
        client = self._active_calibration_client()
        try:
            sample = parse_calibration_sample(client.calibration_sample(session_id))
        except VerificationError as exc:
            if "NOT_FOUND" not in str(exc):
                raise
            repair = self._repair_for_calibration(expected_serial, session_id)
            with self.lock:
                self.calibration = None
            self._close_calibration_connection()
            if repair is not None:
                self._repair_failed(
                    repair,
                    ProvisioningError("摇杆校准会话已超时，设备原有校准配置未改变"),
                )
            raise ProvisioningError(
                "校准会话已超时结束，旧配置未改变。请重新开始校准。"
            ) from exc
        if sample["session_id"] != session_id:
            raise ProvisioningError("设备返回了其他校准会话")
        with self.lock:
            if self.calibration is not None:
                self.calibration["last_sample"] = sample
        return {"serial": expected_serial, "sample": sample}

    def _write_calibration_receipt(self, session: dict) -> Path:
        receipt_root = history_root() / "joystick-calibration"
        receipt_root.mkdir(parents=True, exist_ok=True)
        receipt_path = session.get("receipt_path")
        if receipt_path:
            path = Path(receipt_path)
        else:
            path = receipt_root / (
                f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
                f"{session['serial']}.json"
            )
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": bool(
                session.get("idle_neutral_passed") is True
                and session.get("direction_verification", {}).get("complete")
            ),
            "record_kind": RecordKind.VALIDATION.value,
            "mode": "joystick-calibration",
            "port": session["port"],
            "serial": session["serial"],
            "base_generation": session["base_generation"],
            "before": session["before"],
            "activation": session.get("activation"),
            "idle_neutral_samples": session.get("idle_neutral_samples", []),
            "diagnostic_capture_status": session.get("diagnostic_capture_status"),
            "direction_verification": session.get("direction_verification"),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    def _mark_calibration_readback_needed(self, session: dict, error: Exception) -> None:
        updated = {
            **session,
            "phase": "readback-needed",
            "pending_generation": session["base_generation"] + 1,
            "confirmation_error": str(error),
        }
        with self.lock:
            self.calibration = updated

    def _complete_calibration_activation(
        self,
        session: dict,
        client: WmpClient,
        pending_generation: int,
    ) -> dict:
        deadline = time.monotonic() + 8.0
        last_error: Exception | None = None
        activation = None
        config_result = None
        status = None
        while time.monotonic() < deadline:
            config_result = client.get_config()
            status = client.get_status()
            try:
                activation = validate_calibration_activation(
                    config_result,
                    status,
                    expected_generation=pending_generation,
                )
                break
            except VerificationError as exc:
                last_error = exc
                time.sleep(0.15)
        if activation is None or config_result is None or status is None:
            raise VerificationError(
                f"校准配置未在时限内完成读回：{last_error or '状态未知'}"
            )

        # The last calibration push often ends just as the save reads back, so
        # wait for the operator's hand to leave before idle acceptance starts.
        # A stick that never returns to neutral still fails the samples below.
        settle_deadline = time.monotonic() + IDLE_SETTLE_TIMEOUT_S
        stable_reads = 0
        while (
            stable_reads < IDLE_SETTLE_STABLE_READS
            and time.monotonic() < settle_deadline
        ):
            time.sleep(0.2)
            current_status = client.get_status()
            neutral = (
                current_status.get("inputs_neutral") is True
                and current_status.get("joystick_diagnostics", {}).get("directions") == 0
            )
            stable_reads = stable_reads + 1 if neutral else 0

        idle_samples = []
        for index in range(10):
            current_status = client.get_status()
            diagnostics = current_status.get("joystick_diagnostics", {})
            idle_samples.append(
                {
                    "sample": index + 1,
                    "directions": diagnostics.get("directions"),
                    "raw_x": diagnostics.get("raw_x"),
                    "raw_y": diagnostics.get("raw_y"),
                    "sample_count": diagnostics.get("sample_count"),
                    "adc_failure_count": diagnostics.get("adc_failure_count"),
                    "inputs_neutral": current_status.get("inputs_neutral"),
                    "joystick_suspect": current_status.get("joystick_suspect"),
                }
            )
            if index < 9:
                time.sleep(0.2)

        direction_verification = new_direction_verification()
        direction_verification.update(
            next_direction="up",
            message="静止读回完成，请依次推动上、下、左、右，并在每次后松手回中。",
        )
        before_joystick = session["before"]["raw_config"]["config"].get("joystick", {})
        after_joystick = config_result["config"].get("joystick", {})
        if before_joystick.get("filter") != after_joystick.get("filter"):
            raise VerificationError("校准意外改变了摇杆 filter")
        for axis in ("x", "y"):
            before_axis = before_joystick.get(axis, {})
            after_axis = after_joystick.get(axis, {})
            if before_axis.get("invert") != after_axis.get("invert"):
                raise VerificationError(f"校准意外改变了摇杆 {axis}.invert")
        before_diagnostics = session["before"]["raw_status"].get(
            "joystick_diagnostics", {}
        )
        sample_counts = [item["sample_count"] for item in idle_samples]
        failure_counts = [item["adc_failure_count"] for item in idle_samples]
        idle_neutral_passed = (
            all(item["directions"] == 0 for item in idle_samples)
            and all(item["inputs_neutral"] is True for item in idle_samples)
            and all(item["joystick_suspect"] is False for item in idle_samples)
            and all(isinstance(value, int) for value in sample_counts)
            and sample_counts[-1] > sample_counts[0]
            and all(isinstance(value, int) for value in failure_counts)
            and isinstance(before_diagnostics.get("adc_failure_count"), int)
            and failure_counts[-1] == before_diagnostics.get("adc_failure_count")
        )
        direction_status = self._status_with_diagnostic_capture(client)
        updated = {
            **session,
            "phase": "direction-verification",
            "pending_generation": pending_generation,
            "confirmation_error": None,
            "activation": {
                **activation,
                "raw_config": config_result,
                "raw_status": status,
            },
            "idle_neutral_samples": idle_samples,
            "idle_neutral_passed": idle_neutral_passed,
            "diagnostic_capture_active": True,
            "diagnostic_capture_status": direction_status,
            "direction_verification": direction_verification,
        }
        receipt_path = self._write_calibration_receipt(updated)
        updated["receipt_path"] = str(receipt_path)
        with self.lock:
            self.calibration = updated
            repair = deepcopy(self.repair)
            if (
                repair is not None
                and repair.get("phase") == "calibrating"
                and repair.get("serial") == session["serial"]
            ):
                self.repair = {
                    **_with_mark(repair, "calibration_saved"),
                    "phase": "verifying-joystick",
                    "progress": 82,
                    "calibration_receipt_path": str(receipt_path),
                    "message": (
                        "校准已保存并读回，已暂停原 Profile 动作。"
                        "请依次推动上、下、左、右，每次后松手回中"
                    ),
                }
        return {
            "serial": session["serial"],
            "activation": activation,
            "idle_neutral_passed": idle_neutral_passed,
            "idle_neutral_samples": idle_samples,
            "diagnostic_capture_active": True,
            "direction_verification": direction_verification,
            "receipt_path": str(receipt_path),
        }

    @staticmethod
    def _status_with_diagnostic_capture(client: WmpClient) -> dict:
        # Right after the calibration is saved the device may still be
        # handing input back; retry a refusal briefly, then explain it.
        deadline = time.monotonic() + CALIBRATION_BUSY_RETRY_S
        while True:
            try:
                client.diagnostic_start()
                break
            except VerificationError as exc:
                if not _is_busy_refusal(exc):
                    raise
                if time.monotonic() >= deadline:
                    try:
                        status = client.get_status()
                    except Exception:
                        status = None
                    raise VerificationError(
                        "校准已保存，但设备没能进入四向验收。"
                        + explain_calibration_busy(status).removeprefix(
                            "设备暂时不能开始摇杆校准："
                        )
                    ) from exc
                time.sleep(0.5)
        deadline = time.monotonic() + 3.0
        last_status = None
        while time.monotonic() < deadline:
            last_status = client.get_status()
            capture = last_status.get("diagnostic_capture", {})
            action = last_status.get("action_engine", {})
            if (
                capture.get("active") is True
                and action.get("host_output_state") == "diagnostic_capture"
            ):
                return last_status
            time.sleep(0.05)
        raise VerificationError(
            "方向验收前未能暂停 Profile 动作，已停止验收"
        )

    def confirm_joystick_calibration(
        self,
        requested_port: str,
        *,
        expected_serial: str,
        session_id: int,
    ) -> dict:
        session = self._calibration_snapshot(
            expected_serial=expected_serial,
            session_id=session_id,
            phases={"calibrating"},
        )
        if not session.get("last_sample", {}).get("ready_to_confirm"):
            raise ProvisioningError("请先完成四向全行程和两次稳定回中")
        self._validated_calibration_port(requested_port, session)
        client = self._active_calibration_client()
        with self.lock:
            self.calibration = {**session, "phase": "confirming"}
            repair = self.repair
            if (
                repair is not None
                and repair.get("phase") == "calibrating"
                and repair.get("serial") == expected_serial
            ):
                self.repair = _with_mark(repair, "calibration_confirm")
        try:
            confirmation = client.calibration_confirm(
                session_id,
                session["base_generation"],
            )
        except TransportError as exc:
            self._mark_calibration_readback_needed(session, exc)
            raise ProvisioningError(
                "保存回包中断，结果尚未确认。请保持 USB 连接，点击“只读对账”，不要再次保存。"
            ) from exc
        except Exception:
            with self.lock:
                self.calibration = session
            raise

        try:
            if confirmation.get("state") != "PENDING_ACTIVATION":
                raise VerificationError("设备没有确认校准候选配置")
            pending_generation = confirmation.get("generation")
            if not isinstance(pending_generation, int) or isinstance(pending_generation, bool):
                raise VerificationError("校准候选 generation 无效")
            if pending_generation != session["base_generation"] + 1:
                raise VerificationError("校准候选 generation 不是配置的下一代")
            session = {**session, "pending_generation": pending_generation}
            with self.lock:
                self.calibration = {**session, "phase": "confirming"}
            return self._complete_calibration_activation(
                session, client, pending_generation
            )
        except Exception as exc:
            self._mark_calibration_readback_needed(session, exc)
            raise ProvisioningError(
                "校准可能已保存，但读回未完成。请保持 USB 连接，点击“只读对账”。"
            ) from exc

    def sample_direction_verification(
        self,
        requested_port: str,
        *,
        expected_serial: str,
        session_id: int,
    ) -> dict:
        session = self._calibration_snapshot(
            expected_serial=expected_serial,
            session_id=session_id,
            phases={"direction-verification"},
        )
        self._validated_calibration_port(requested_port, session)
        client = self._active_calibration_client()
        raw_status = self._status_with_diagnostic_capture(client)
        directions = raw_status.get("joystick_diagnostics", {}).get("directions")
        direction_verification = advance_direction_verification(
            session["direction_verification"],
            directions,
        )
        updated = {
            **session,
            "direction_verification": direction_verification,
            "last_direction_status": raw_status,
        }
        receipt_path = self._write_calibration_receipt(updated)
        updated["receipt_path"] = str(receipt_path)
        with self.lock:
            self.calibration = None if direction_verification["complete"] else updated
        if direction_verification["complete"]:
            try:
                client.diagnostic_stop()
            finally:
                self._close_calibration_connection()
        success = bool(
            session.get("idle_neutral_passed") is True
            and direction_verification["complete"]
        )
        if direction_verification["complete"]:
            repair = self._repair_for_calibration(expected_serial, session_id)
            if repair is not None:
                key8_error = None
                if success:
                    try:
                        self._confirm_voice_key_final(
                            repair.get("port") or requested_port,
                            expected_serial=expected_serial,
                        )
                    except Exception as exc:
                        key8_error = exc
                if key8_error is not None:
                    self._repair_failed(repair, key8_error)
                    success = False
                elif success:
                    passed = self._finish_repair_record(
                        {
                            **_with_mark(repair, "direction_done"),
                            "phase": "completed",
                            "status": "passed",
                            "progress": 100,
                            "calibration_receipt_path": str(receipt_path),
                            "message": (
                                ACCEPTANCE_TEST_COMPLETION_MESSAGE
                                if self._acceptance_test() else COMPLETION_MESSAGE
                            ),
                        },
                        outcome="passed",
                    )
                    with self.lock:
                        self.repair = passed
                else:
                    self._repair_failed(
                        repair,
                        ProvisioningError(
                            "摇杆静止验收未通过："
                            + idle_failure_reasons(
                                session.get("idle_neutral_samples") or [],
                                (session.get("before", {}).get("raw_status", {})
                                 .get("joystick_diagnostics", {})
                                 .get("adc_failure_count")),
                            )
                            + "。停止交付并检查摇杆"
                        ),
                    )
        return {
            "serial": expected_serial,
            "direction_verification": direction_verification,
            "idle_neutral_passed": session.get("idle_neutral_passed") is True,
            "success": success,
            "receipt_path": str(receipt_path),
        }

    def reconcile_joystick_calibration(
        self,
        requested_port: str,
        *,
        expected_serial: str,
        session_id: int,
    ) -> dict:
        import serial

        session = self._calibration_snapshot(
            expected_serial=expected_serial,
            session_id=session_id,
            phases={"confirming", "readback-needed"},
        )
        port = self._validated_runtime_port(requested_port, allow_calibration=True)
        if port != session["port"]:
            raise ProvisioningError("校准期间设备端口发生变化")

        self._close_calibration_connection()
        connection = None
        try:
            connection = serial.Serial(port, 115200, timeout=0.05, write_timeout=1)
            client = WmpClient(connection, timeout_s=4.0)
            self._verify_calibration_device(client, expected_serial)
        except Exception as exc:
            if connection is not None:
                connection.close()
            self._mark_calibration_readback_needed(session, exc)
            if isinstance(exc, OSError):
                raise ProvisioningError(
                    _describe_serial_failure(port, str(exc))["message"]
                ) from exc
            raise
        with self.lock:
            self._calibration_connection = connection
            self._calibration_client = client

        expected_generation = session["base_generation"] + 1
        try:
            return {
                "outcome": "saved",
                **self._complete_calibration_activation(
                    session, client, expected_generation
                ),
            }
        except Exception as exc:
            try:
                config_result = client.get_config()
                status = client.get_status()
            except Exception as read_error:
                self._mark_calibration_readback_needed(session, read_error)
                raise ProvisioningError(
                    "仍无法读回校准结果。请保持 USB 连接后再点一次“只读对账”。"
                ) from read_error

            active = status.get("active") if isinstance(status, dict) else None
            pending = status.get("pending") if isinstance(status, dict) else None
            if (
                config_result.get("generation") == session["base_generation"]
                and isinstance(active, dict)
                and active.get("generation") == session["base_generation"]
                and pending is None
            ):
                calibration_state = status.get("calibration", {}).get("state")
                if calibration_state in {"ACQUIRING", "CAPTURING"}:
                    try:
                        client.calibration_cancel(session_id)
                    except VerificationError:
                        pass
                with self.lock:
                    self.calibration = None
                self._close_calibration_connection()
                repair = self._repair_for_calibration(expected_serial, session_id)
                if repair is not None:
                    self._repair_failed(
                        repair,
                        ProvisioningError("校准保存未发生，设备原配置保持不变"),
                    )
                return {
                    "outcome": "not-saved",
                    "serial": expected_serial,
                    "generation": session["base_generation"],
                    "message": "已读回确认未保存新校准，原配置保持不变。",
                }

            self._mark_calibration_readback_needed(session, exc)
            if (
                config_result.get("generation") == expected_generation
                and isinstance(active, dict)
                and active.get("generation") == expected_generation
                and pending is None
            ):
                # Saved and active: only the step after saving failed.
                raise ProvisioningError(
                    f"校准已保存并生效，但后续步骤未完成：{exc}"
                    "。请保持 USB 连接后点“只读对账”继续，不会重复保存。"
                ) from exc
            raise ProvisioningError(
                "读回仍显示配置激活中或状态不一致，未再次保存。请保持 USB 连接后重试只读对账。"
            ) from exc

    def cancel_joystick_calibration(
        self,
        requested_port: str,
        *,
        expected_serial: str,
        session_id: int,
    ) -> dict:
        session = self._calibration_snapshot(
            expected_serial=expected_serial,
            session_id=session_id,
            phases={
                "calibrating",
                "confirming",
                "readback-needed",
                "direction-verification",
            },
        )
        if session["phase"] == "calibrating":
            # Cancelling must always succeed so the operator is never stuck:
            # nothing has been saved yet, and if the device is unreachable its
            # own 120 s calibration timeout ends the session unchanged.
            try:
                client = self._active_calibration_client()
                result = client.calibration_cancel(session_id)
            except (TransportError, OSError):
                result = {"state": "DEVICE_UNREACHABLE", "calibration_saved": False}
            except VerificationError as exc:
                if "NOT_FOUND" not in str(exc):
                    raise
                result = {"state": "ALREADY_ENDED", "calibration_saved": False}
            except ProvisioningError:
                # The session's serial handle is already gone.
                result = {"state": "DEVICE_UNREACHABLE", "calibration_saved": False}
        elif session["phase"] in {"confirming", "readback-needed"}:
            result = {"state": "READBACK_ENDED", "calibration_saved": "unknown"}
        else:
            result = {"state": "VERIFICATION_ENDED", "calibration_saved": True}
            if session.get("diagnostic_capture_active"):
                try:
                    client = self._active_calibration_client()
                    client.diagnostic_stop()
                except Exception:
                    pass
        with self.lock:
            self.calibration = None
        self._close_calibration_connection()
        repair = self._repair_for_calibration(expected_serial, session_id)
        if repair is not None:
            self._repair_failed(repair, ProvisioningError("摇杆校准已取消，本台未完成"))
        return {"serial": expected_serial, "result": result}

    def start_authority_update(self, requested_port: str) -> str:
        if not isinstance(requested_port, str) or not requested_port:
            raise ProvisioningError("请先接入并识别设备")
        scanned = self.scan()
        if scanned["readiness"] != "ready":
            raise ProvisioningError(scanned["message"])
        device = scanned["devices"][0]
        if device["device"] != requested_port:
            raise ProvisioningError("设备状态已变化，请重新扫描")
        authority = scanned["authority"]
        package = ReleasePackage.open(authority["path"])
        try:
            if (
                package.release_id != authority["release_id"]
                or package_fingerprint(package) != authority["fingerprint"]
            ):
                self.invalidate_authority()
                raise ProvisioningError("权威包内容在扫描后已变化，请重试")
            selected = select_runtime_ports(load_ports(), package.runtime_usb, [requested_port])
            with self.lock:
                if self.closing:
                    raise ProvisioningError("工具正在关闭")
                if self.busy_job is not None:
                    raise ProvisioningError("已有更新任务正在运行")
                if self.calibration is not None:
                    raise ProvisioningError("摇杆校准正在进行，请先完成或取消校准")
                job_id = str(uuid.uuid4())
                self.jobs[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "progress": 0,
                    "stage": "queued",
                    "message": "等待开始",
                    "release_id": package.release_id,
                    "mode": ProvisionMode.UPDATE.value,
                    "record_kind": RecordKind.VALIDATION.value,
                    "ports": [requested_port],
                    "events": [],
                    "event_base": 0,
                    "results": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self.busy_job = job_id
                self.latest_job_id = job_id
                self.worker = threading.Thread(
                    target=self._run_job,
                    args=(job_id, package, selected[0]),
                    daemon=False,
                    name=f"authority-update-{job_id[:8]}",
                )
                try:
                    self.worker.start()
                except RuntimeError:
                    self.jobs.pop(job_id, None)
                    self.busy_job = None
                    self.latest_job_id = None
                    self.worker = None
                    raise
        except Exception:
            package.close()
            raise
        return job_id

    def _update_job(self, job_id: str, **changes) -> None:
        with self.lock:
            self.jobs[job_id].update(changes)

    def _event(self, job_id: str, port: str):
        def callback(stage: str, message: str, progress: int) -> None:
            described = _describe_serial_failure(port, message)
            message = described["message"]
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "port": port,
                "stage": stage,
                "message": message,
                "progress": progress,
            }
            with self.lock:
                job = self.jobs[job_id]
                job.update(
                    status="running",
                    stage=stage,
                    message=f"{port} · {message}",
                    progress=progress,
                )
                job["events"].append(event)
                overflow = len(job["events"]) - self.MAX_EVENTS_PER_JOB
                if overflow > 0:
                    del job["events"][:overflow]
                    job["event_base"] += overflow

        return callback

    def _run_job(self, job_id: str, package: ReleasePackage, device) -> None:
        try:
            with package:
                self._event(job_id, device.device)(
                    "inspect", "正在读取设备身份，确认本轮更新对象", 3
                )
                deadline = time.monotonic() + PREFLIGHT_BUSY_TIMEOUT_S
                retrying_port = False
                while True:
                    if retrying_port:
                        present = [
                            item for item in detect_devices(package.runtime_usb)
                            if _relevant_device(item)
                        ]
                        if present != [device]:
                            raise ProvisioningError("设备状态已变化，请重新扫描后开始")
                    try:
                        target_serial = _read_target_serial(device.device, package)
                        break
                    except OSError as exc:
                        described = _describe_serial_failure(device.device, str(exc))
                        if not described["retryable"]:
                            raise
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise PreflightPortBusy(
                                f"等待串口释放已超过 {PREFLIGHT_BUSY_TIMEOUT_S:g} 秒。"
                                f"{described['message']}"
                            ) from exc
                        self._event(job_id, device.device)(
                            "inspect", "串口暂被占用，正在等待原设备释放", 3
                        )
                        retrying_port = True
                        time.sleep(min(0.25, remaining))
                usb_serial = getattr(device, "serial_number", None)
                if usb_serial and usb_serial.startswith("CP01-") and target_serial != usb_serial:
                    raise ProvisioningError("设备序列号已变化，请重新扫描后开始")
                request = ProvisionRequest(
                    package=package,
                    port=device.device,
                    mode=ProvisionMode.UPDATE,
                    operator="standalone-authority-updater",
                    record_kind=RecordKind.VALIDATION,
                    verify_timeout_s=60.0,
                )
                provisioner = Provisioner(
                    EsptoolRunner(),
                    RuntimeVerifier(),
                    history_root(),
                )
                batch_result = SequentialBatch(
                    lambda selected: provisioner.run_ota(
                        request,
                        self._event(job_id, selected.device),
                        restart_mismatched=True,
                        expected_serial=target_serial,
                    )
                ).run([device])[0]
                if batch_result.success:
                    value = batch_result.value
                    results = [
                        {
                            "port": device.device,
                            "success": True,
                            "mac": value.runtime.serial.removeprefix("CP01-"),
                            "verification": "cdc-ota-runtime-wmp",
                            "runtime": asdict(value.runtime),
                            "runtime_port": value.runtime.port,
                            "receipt_path": str(value.receipt_path),
                            "timings_s": _receipt_timings(value.receipt_path),
                        }
                    ]
                    status = "passed"
                    message = "OTA、重启与精确版本回读全部通过"
                else:
                    described = _describe_serial_failure(
                        device.device,
                        batch_result.error or "更新未完成",
                    )
                    results = [
                        {
                            "port": device.device,
                            "success": False,
                            "error": described["message"],
                            "action_required": batch_result.action_required,
                        }
                    ]
                    if described["retryable"]:
                        status = "retryable"
                    elif batch_result.action_required:
                        status = "action-required"
                    else:
                        status = "failed"
                    message = described["message"]
                with self.lock:
                    current_progress = self.jobs[job_id]["progress"]
                final_progress = current_progress
                final_stage = "done"
                if status == "passed":
                    final_progress = 100
                elif status == "action-required":
                    final_progress = 85
                    final_stage = "operator-action-required"
                elif status == "retryable":
                    final_stage = "port-busy"
                self._update_job(
                    job_id,
                    status=status,
                    progress=final_progress,
                    stage=final_stage,
                    message=message,
                    results=results,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
        except Exception as exc:
            described = (
                {"retryable": True, "message": str(exc)}
                if isinstance(exc, PreflightPortBusy)
                else _describe_serial_failure(device.device, str(exc))
            )
            with self.lock:
                current_progress = self.jobs[job_id]["progress"]
            self._update_job(
                job_id,
                status="retryable" if described["retryable"] else "failed",
                stage="port-busy" if described["retryable"] else "error",
                progress=current_progress,
                message=described["message"],
                results=[
                    {
                        "port": device.device,
                        "success": False,
                        "error": described["message"],
                    }
                ],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            with self.lock:
                if self.busy_job == job_id:
                    self.busy_job = None
                if self.worker is threading.current_thread():
                    self.worker = None
                completed = sorted(
                    (
                        job for job in self.jobs.values()
                        if job["status"] in TERMINAL_JOB_STATUSES
                    ),
                    key=lambda job: job.get("completed_at", ""),
                    reverse=True,
                )
                for stale in completed[self.MAX_RETAINED_JOBS:]:
                    self.jobs.pop(stale["id"], None)

    def job(self, job_id: str | None, after_event: int = 0) -> dict | None:
        if not job_id:
            return None
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            snapshot = deepcopy(job)
            started_at = datetime.fromisoformat(snapshot["created_at"])
            finished_at = datetime.fromisoformat(
                snapshot.get("completed_at") or datetime.now(timezone.utc).isoformat()
            )
            snapshot["elapsed_seconds"] = max(
                0, int((finished_at - started_at).total_seconds())
            )
            event_base = snapshot.pop("event_base")
            if after_event < event_base:
                snapshot["events_reset"] = True
                start = 0
            else:
                snapshot["events_reset"] = False
                start = min(after_event - event_base, len(snapshot["events"]))
            snapshot["events"] = snapshot["events"][start:]
            snapshot["event_cursor"] = event_base + len(job["events"])
            return snapshot

    def begin_request(self) -> None:
        with self.lock:
            if self.closing:
                raise ProvisioningError("工具正在关闭")
            self._active_requests += 1

    def end_request(self) -> None:
        with self.lock:
            self._active_requests -= 1
            self._requests_idle.notify_all()

    def require_shutdown_idle(self) -> None:
        """Allow exit unless firmware is being written.

        An active calibration is safe to abandon: shutdown() cancels the
        uncommitted session or stops diagnostic capture, and the device keeps
        its previous (or already read-back) configuration either way.
        """
        with self.lock:
            if self.busy_job is not None:
                raise ProvisioningError("固件正在写入，完成后再退出工具")
            self.closing = True

    def shutdown(self) -> None:
        with self.lock:
            self.closing = True
            deadline = time.monotonic() + SHUTDOWN_DRAIN_TIMEOUT_S
            while self._active_requests > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._requests_idle.wait(remaining)
            worker = self.worker
            session = deepcopy(self.calibration)
            client = self._calibration_client
        if session is not None and client is not None:
            try:
                if session.get("phase") == "calibrating":
                    client.calibration_cancel(session["session_id"])
                elif session.get("phase") == "direction-verification":
                    client.diagnostic_stop()
            except Exception:
                pass
        self._close_calibration_connection()
        if worker is not None and worker is not threading.current_thread():
            worker.join()


class AuthorityUpdateHandler(BaseHTTPRequestHandler):
    state: AuthorityUpdateState
    static_root: Path

    def log_message(self, format, *args):
        return

    def _headers(self, content_type: str, length: int, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(body), status)
        self.wfile.write(body)

    def _error(self, message: str, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _require_local_host(self) -> bool:
        hostname = urlparse(f"//{self.headers.get('Host', '')}").hostname
        if hostname not in {"127.0.0.1", "::1", "localhost"}:
            self._error("仅允许从本机地址访问", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _require_token(self) -> bool:
        if self.headers.get("X-Authority-Updater-Token") != self.state.token:
            self._error("请求令牌无效", HTTPStatus.FORBIDDEN)
            return False
        return True

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ProvisioningError("请求长度无效") from exc
        if length <= 0 or length > 4096:
            raise ProvisioningError("请求内容为空或过大")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvisioningError("请求 JSON 无效") from exc
        if not isinstance(value, dict):
            raise ProvisioningError("请求必须是 JSON 对象")
        return value

    def do_GET(self):
        if not self._require_local_host():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/authority-update/bootstrap":
                self._json(self.state.bootstrap())
                return
            if parsed.path == "/api/authority-update/scan":
                self._json(self.state.scan())
                return
            if parsed.path.startswith("/api/authority-update/jobs/"):
                try:
                    after_event = int(parse_qs(parsed.query).get("after_event", ["0"])[0])
                except ValueError:
                    after_event = 0
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = self.state.job(job_id, max(0, after_event))
                if job is None:
                    self._error("任务不存在", HTTPStatus.NOT_FOUND)
                else:
                    self._json(job)
                return
            self._serve_static(parsed.path)
        except ProvisioningError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"内部错误：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        if not self._require_local_host() or not self._require_token():
            return
        if self.path == "/api/authority-update/shutdown":
            try:
                self.state.require_shutdown_idle()
            except ProvisioningError as exc:
                self._error(str(exc))
                return
            self._json({"status": "shutting-down"})
            threading.Thread(
                target=self._shutdown_server,
                daemon=True,
                name="authority-updater-shutdown",
            ).start()
            return
        try:
            self.state.begin_request()
        except ProvisioningError as exc:
            self._error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            self._handle_post()
        finally:
            self.state.end_request()

    def _shutdown_server(self) -> None:
        self.server.shutdown()
        hook = self.state.on_shutdown_requested
        if callable(hook):
            try:
                hook()
            except Exception:
                pass

    def _handle_post(self):
        try:
            payload = self._read_json()
            if self.path == "/api/authority-update/jobs":
                job_id = self.state.start_authority_update(str(payload.get("port", "")))
                self._json({"job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/authority-update/repair/start":
                self._json(
                    {"repair": self.state.start_repair(str(payload.get("port", "")))},
                    HTTPStatus.ACCEPTED,
                )
                return
            if self.path == "/api/authority-update/repair/reset":
                self._json(self.state.reset_repair())
                return
            if self.path == "/api/authority-update/repair/advance":
                self._json(self.state.advance_repair(str(payload.get("job_id", ""))))
                return
            if self.path == "/api/authority-update/inspect":
                self._json(self.state.inspect_runtime(str(payload.get("port", ""))))
                return
            if self.path == "/api/authority-update/clear-slot":
                try:
                    slot = int(payload.get("slot", 0))
                except (TypeError, ValueError) as exc:
                    raise ProvisioningError("蓝牙槽位必须是 1、2 或 3") from exc
                self._json(
                    self.state.clear_runtime_slot(
                        str(payload.get("port", "")),
                        slot=slot,
                        expected_serial=str(payload.get("expected_serial", "")),
                        confirmation=str(payload.get("confirmation", "")),
                    )
                )
                return
            if self.path == "/api/authority-update/calibration/start":
                self._json(
                    self.state.start_joystick_calibration(
                        str(payload.get("port", "")),
                        expected_serial=str(payload.get("expected_serial", "")),
                    )
                )
                return
            if self.path in {
                "/api/authority-update/calibration/sample",
                "/api/authority-update/calibration/confirm",
                "/api/authority-update/calibration/reconcile",
                "/api/authority-update/calibration/cancel",
                "/api/authority-update/calibration/verify-direction",
            }:
                try:
                    session_id = int(payload.get("session_id", 0))
                except (TypeError, ValueError) as exc:
                    raise ProvisioningError("校准 session_id 无效") from exc
                arguments = {
                    "requested_port": str(payload.get("port", "")),
                    "expected_serial": str(payload.get("expected_serial", "")),
                    "session_id": session_id,
                }
                if self.path.endswith("/sample"):
                    result = self.state.sample_joystick_calibration(**arguments)
                elif self.path.endswith("/confirm"):
                    result = self.state.confirm_joystick_calibration(**arguments)
                elif self.path.endswith("/reconcile"):
                    result = self.state.reconcile_joystick_calibration(**arguments)
                elif self.path.endswith("/cancel"):
                    result = self.state.cancel_joystick_calibration(**arguments)
                else:
                    result = self.state.sample_direction_verification(**arguments)
                self._json(result)
                return
            self._error("接口不存在", HTTPStatus.NOT_FOUND)
        except ProvisioningError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"内部错误：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, request_path: str):
        aliases = {
            "": "authority-update.html",
            "/": "authority-update.html",
            "/authority-update.js": "authority-update.js",
            "/authority-update.css": "authority-update.css",
        }
        relative = aliases.get(request_path)
        if relative is None:
            self._error("页面不存在", HTTPStatus.NOT_FOUND)
            return
        candidate = (self.static_root / relative).resolve()
        if not candidate.is_relative_to(self.static_root.resolve()) or not candidate.is_file():
            self._error("页面不存在", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._headers(content_type, len(body))
        self.wfile.write(body)


def create_authority_update_server(host: str = "127.0.0.1", port: int = 0):
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ProvisioningError("权威包更新工具只允许监听本机回环地址")
    state = AuthorityUpdateState()
    handler = type(
        "BoundAuthorityUpdateHandler",
        (AuthorityUpdateHandler,),
        {"state": state, "static_root": resource_root() / "static"},
    )
    server = ThreadingHTTPServer((host, port), handler)
    return server, state
