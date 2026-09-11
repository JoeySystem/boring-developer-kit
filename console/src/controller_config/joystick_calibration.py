from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from controller_config.protocol.bootstrap import Command


class CalibrationError(ValueError):
    """A calibration response or local transaction state is invalid."""


class CalibrationState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    CENTERING = "centering"
    SAMPLING = "sampling"
    CAPTURING = "capturing"
    READY_TO_CONFIRM = "ready_to_confirm"
    CONFIRMING = "confirming"
    PENDING = "pending"
    VERIFYING = "verifying"
    UNKNOWN = "unknown"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class CalibrationSample:
    session_id: int
    state: str
    raw_x: int
    raw_y: int
    center_x: int | None = None
    center_y: int | None = None
    minimum_x: int | None = None
    minimum_y: int | None = None
    maximum_x: int | None = None
    maximum_y: int | None = None
    travel_complete: bool = False
    center_window_ms: int = 0
    timeout_ms: int = 0

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        expected_session_id: int | None = None,
        require_start_timing: bool = False,
    ) -> "CalibrationSample":
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise CalibrationError("校准响应缺少 result object")
        session_id = result.get("session_id")
        if not _positive_int(session_id):
            raise CalibrationError("校准 session_id 无效")
        if expected_session_id is not None and session_id != expected_session_id:
            raise CalibrationError("校准响应不属于当前会话")
        state = result.get("state")
        if state not in {"CENTERING", "CAPTURING"}:
            raise CalibrationError("校准响应 state 无效")
        raw_x, raw_y = _point(result.get("raw"), "raw")
        travel_complete = result.get("travel_complete")
        if not isinstance(travel_complete, bool):
            raise CalibrationError("校准 travel_complete 必须是布尔值")

        center_x = center_y = minimum_x = minimum_y = maximum_x = maximum_y = None
        if state == "CAPTURING":
            center_x, center_y = _point(result.get("center"), "center")
            minimum_x, minimum_y = _point(result.get("minimum"), "minimum")
            maximum_x, maximum_y = _point(result.get("maximum"), "maximum")
            if not (
                minimum_x <= center_x <= maximum_x
                and minimum_y <= center_y <= maximum_y
            ):
                raise CalibrationError("校准端点与中心关系无效")
        elif travel_complete:
            raise CalibrationError("CENTERING 阶段不能报告行程完成")

        center_window_ms = result.get("center_window_ms", 0)
        timeout_ms = result.get("timeout_ms", 0)
        if require_start_timing and (
            not _positive_int(center_window_ms) or not _positive_int(timeout_ms)
        ):
            raise CalibrationError("校准响应缺少有效的采样时间")
        return cls(
            session_id=int(session_id),
            state=state,
            raw_x=raw_x,
            raw_y=raw_y,
            center_x=center_x,
            center_y=center_y,
            minimum_x=minimum_x,
            minimum_y=minimum_y,
            maximum_x=maximum_x,
            maximum_y=maximum_y,
            travel_complete=travel_complete,
            center_window_ms=int(center_window_ms) if _positive_int(center_window_ms) else 0,
            timeout_ms=int(timeout_ms) if _positive_int(timeout_ms) else 0,
        )


@dataclass(frozen=True)
class CalibrationTransaction:
    state: CalibrationState = CalibrationState.IDLE
    message: str = "尚未开始摇杆校准"
    technical: str = ""
    session_id: int = 0
    raw_x: int = 2048
    raw_y: int = 2048
    center_x: int | None = None
    center_y: int | None = None
    minimum_x: int | None = None
    minimum_y: int | None = None
    maximum_x: int | None = None
    maximum_y: int | None = None
    travel_complete: bool = False
    center_window_ms: int = 0
    timeout_ms: int = 0
    base_generation: int | None = None
    base_digest: str = ""
    pending_generation: int | None = None
    pending_digest: str = ""
    cancel_requested: bool = False

    @property
    def is_session_active(self) -> bool:
        return self.session_id > 0 and self.state in {
            CalibrationState.CENTERING,
            CalibrationState.SAMPLING,
            CalibrationState.CAPTURING,
            CalibrationState.READY_TO_CONFIRM,
            CalibrationState.CONFIRMING,
            CalibrationState.CANCELLING,
            CalibrationState.FAILED,
        }

    @property
    def blocks_editing(self) -> bool:
        return self.state in {
            CalibrationState.STARTING,
            CalibrationState.CENTERING,
            CalibrationState.SAMPLING,
            CalibrationState.CAPTURING,
            CalibrationState.READY_TO_CONFIRM,
            CalibrationState.CONFIRMING,
            CalibrationState.PENDING,
            CalibrationState.VERIFYING,
            CalibrationState.UNKNOWN,
            CalibrationState.CANCELLING,
        } or self.is_session_active

    @property
    def can_cancel(self) -> bool:
        return self.session_id > 0 and self.state in {
            CalibrationState.CENTERING,
            CalibrationState.SAMPLING,
            CalibrationState.CAPTURING,
            CalibrationState.READY_TO_CONFIRM,
            CalibrationState.FAILED,
        }

    @property
    def can_confirm(self) -> bool:
        return self.state is CalibrationState.READY_TO_CONFIRM and self.travel_complete


def calibration_start_command() -> Command:
    return Command("CALIBRATION_START", 0x20, {}, timeout_ms=4000, retries=1)


def calibration_sample_command(session_id: int) -> Command:
    return Command(
        "CALIBRATION_SAMPLE",
        0x21,
        {"session_id": session_id},
        timeout_ms=2500,
    )


def calibration_confirm_command(session_id: int, base_generation: int) -> Command:
    return Command(
        "CALIBRATION_CONFIRM",
        0x22,
        {"session_id": session_id, "base_generation": base_generation},
        timeout_ms=5000,
    )


def calibration_cancel_command(session_id: int) -> Command:
    return Command(
        "CALIBRATION_CANCEL",
        0x23,
        {"session_id": session_id},
        timeout_ms=4000,
        retries=1,
    )


def parse_calibration_confirmation(payload: dict[str, Any]) -> tuple[int, str]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or result.get("state") != "PENDING_ACTIVATION":
        raise CalibrationError("设备没有确认校准候选配置")
    generation = result.get("generation")
    digest = result.get("digest")
    if not _positive_int(generation):
        raise CalibrationError("校准候选 generation 无效")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise CalibrationError("校准候选 digest 无效")
    return int(generation), digest


def _point(value: object, label: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        raise CalibrationError(f"校准 {label} 必须是 object")
    x = value.get("x")
    y = value.get("y")
    if not _adc_value(x) or not _adc_value(y):
        raise CalibrationError(f"校准 {label}.x/y 必须是 0..4095 整数")
    return int(x), int(y)


def _adc_value(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4095


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
