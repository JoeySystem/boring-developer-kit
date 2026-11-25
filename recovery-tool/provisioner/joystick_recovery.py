from __future__ import annotations

from typing import Any

from .errors import VerificationError


DIRECTION_SEQUENCE = (
    ("up", "上", 1 << 2),
    ("down", "下", 1 << 3),
    ("left", "左", 1 << 0),
    ("right", "右", 1 << 1),
)


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_calibration_sample(result: dict) -> dict:
    if not isinstance(result, dict):
        raise VerificationError("校准响应无效")
    session_id = _integer(result.get("session_id"))
    state = result.get("state")
    raw = result.get("raw")
    if session_id is None or session_id <= 0:
        raise VerificationError("校准 session_id 无效")
    if state not in {"CENTERING", "CAPTURING"}:
        raise VerificationError("校准 state 无效")
    if (
        not isinstance(raw, dict)
        or _integer(raw.get("x")) is None
        or _integer(raw.get("y")) is None
    ):
        raise VerificationError("校准 raw.x/y 无效")
    travel_complete = result.get("travel_complete")
    ready_to_confirm = result.get("ready_to_confirm")
    if not isinstance(travel_complete, bool) or not isinstance(ready_to_confirm, bool):
        raise VerificationError("校准行程状态无效")
    if ready_to_confirm and not travel_complete:
        raise VerificationError("校准确认状态与行程状态不一致")
    parsed = {
        "session_id": session_id,
        "owner_state": str(result.get("owner_state", "unknown")),
        "state": state,
        "raw": {"x": raw["x"], "y": raw["y"]},
        "travel_complete": travel_complete,
        "ready_to_confirm": ready_to_confirm,
        "center_window_ms": _integer(result.get("center_window_ms")) or 0,
        "timeout_ms": _integer(result.get("timeout_ms")) or 0,
    }
    for field in ("center", "minimum", "maximum"):
        point = result.get(field)
        if point is None:
            parsed[field] = None
            continue
        if (
            not isinstance(point, dict)
            or _integer(point.get("x")) is None
            or _integer(point.get("y")) is None
        ):
            raise VerificationError(f"校准 {field}.x/y 无效")
        parsed[field] = {"x": point["x"], "y": point["y"]}
    return parsed


def validate_calibration_activation(
    config_result: dict,
    status: dict,
    *,
    expected_generation: int,
) -> dict:
    if not isinstance(config_result, dict) or not isinstance(status, dict):
        raise VerificationError("校准激活读回无效")
    if config_result.get("generation") != expected_generation:
        raise VerificationError("校准配置尚未激活")
    active = status.get("active")
    if status.get("pending") is not None or not isinstance(active, dict):
        raise VerificationError("校准配置仍在等待激活")
    if active.get("generation") != expected_generation:
        raise VerificationError("运行态 generation 尚未切换到校准配置")
    config = config_result.get("config")
    joystick = config.get("joystick") if isinstance(config, dict) else None
    diagnostics = status.get("joystick_diagnostics") if isinstance(status, dict) else None
    if not isinstance(joystick, dict) or joystick.get("calibrated") is not True:
        raise VerificationError("校准激活后未读到 calibrated=true")
    if not isinstance(diagnostics, dict):
        raise VerificationError("校准激活后缺少摇杆诊断")
    axes = {}
    for axis in ("x", "y"):
        axis_config = joystick.get(axis)
        if not isinstance(axis_config, dict):
            raise VerificationError(f"校准激活后缺少 {axis} 轴配置")
        center = _integer(axis_config.get("center"))
        deadzone = _integer(axis_config.get("deadzone"))
        diagnostic_center = _integer(diagnostics.get(f"center_{axis}"))
        diagnostic_deadzone = _integer(diagnostics.get(f"deadzone_{axis}"))
        if center is None or deadzone is None:
            raise VerificationError(f"校准激活后 {axis} 轴参数无效")
        if center != diagnostic_center or deadzone != diagnostic_deadzone:
            raise VerificationError(f"校准激活后 {axis} 轴配置与运行值不一致")
        axes[axis] = {"center": center, "deadzone": deadzone}
    return {
        "generation": expected_generation,
        "calibrated": True,
        "x": axes["x"],
        "y": axes["y"],
        "directions": _integer(diagnostics.get("directions")),
    }


def new_direction_verification() -> dict:
    return {
        "index": 0,
        "awaiting_neutral": False,
        "completed": [],
        "complete": False,
    }


def advance_direction_verification(state: dict, directions: int) -> dict:
    if not isinstance(directions, int) or isinstance(directions, bool) or directions < 0:
        raise VerificationError("摇杆方向诊断无效")
    if state.get("complete"):
        return {**state, "directions": directions, "message": "四向与回中验收已完成。"}
    index = int(state.get("index", 0))
    if index < 0 or index >= len(DIRECTION_SEQUENCE):
        raise VerificationError("四向验收状态无效")
    key, label, expected = DIRECTION_SEQUENCE[index]
    awaiting_neutral = bool(state.get("awaiting_neutral", False))
    completed = list(state.get("completed", []))
    event = "waiting"
    if not awaiting_neutral and directions == expected:
        awaiting_neutral = True
        event = "direction-seen"
        message = f"已识别向{label}，请松手回中。"
    elif awaiting_neutral and directions == 0:
        completed.append(key)
        index += 1
        awaiting_neutral = False
        event = "neutral-seen"
        if index == len(DIRECTION_SEQUENCE):
            message = "四向与每次回中均已通过。"
        else:
            message = f"已完成向{label}并回中，请推动摇杆向{DIRECTION_SEQUENCE[index][1]}。"
    elif directions not in {0, expected}:
        message = f"当前不是要求的向{label}，请回中后按提示操作。"
    else:
        message = (
            f"请推动摇杆向{label}。"
            if not awaiting_neutral
            else f"请松手，让摇杆从向{label}回到中心。"
        )
    complete = index == len(DIRECTION_SEQUENCE)
    next_direction = None if complete else DIRECTION_SEQUENCE[index][0]
    return {
        "index": index,
        "awaiting_neutral": awaiting_neutral,
        "completed": completed,
        "complete": complete,
        "next_direction": next_direction,
        "directions": directions,
        "event": event,
        "message": message,
    }
