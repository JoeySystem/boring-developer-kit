from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .errors import VerificationError
from .protocol import WmpClient


POWER_V2_HARDWARE_ID = "WMP-S3-MATRIX12-POWER-V2"


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _integer(value: Any, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _slot_rows(codex_micro: dict) -> list[dict]:
    runtime_slots = {
        _integer(item.get("slot")): item
        for item in codex_micro.get("slots", [])
        if isinstance(item, dict) and _integer(item.get("slot")) in {1, 2, 3}
    }
    diagnostic_slots = {
        _integer(item.get("slot")): item
        for item in codex_micro.get("ble_slots", [])
        if isinstance(item, dict) and _integer(item.get("slot")) in {1, 2, 3}
    }
    rows = []
    for slot in range(1, 4):
        runtime = runtime_slots.get(slot, {})
        diagnostic = diagnostic_slots.get(slot, {})
        rows.append(
            {
                "slot": slot,
                "paired": bool(runtime.get("paired", diagnostic.get("paired", False))),
                "connected": bool(runtime.get("connected", False)),
                "address_kind": str(diagnostic.get("address_kind", "unknown")),
                "identity_known": bool(diagnostic.get("identity_known", False)),
            }
        )
    return rows


def summarize_status(status: dict) -> dict:
    if not isinstance(status, dict):
        raise VerificationError("GET_STATUS result 无效")
    codex_micro = _object(status.get("codex_micro"))
    firmware_update = _object(status.get("firmware_update"))
    return {
        "active_slot": _integer(codex_micro.get("active_slot")),
        "bond_count": _integer(codex_micro.get("bond_count")),
        "active_slot_bond_state": _integer(
            codex_micro.get("active_slot_bond_state"), -1
        ),
        "auth_fail_events": _integer(codex_micro.get("auth_fail_events")),
        "last_auth_fail_reason": _integer(
            codex_micro.get("last_auth_fail_reason")
        ),
        "init_error": _integer(codex_micro.get("init_error")),
        "ble_services_ready": bool(codex_micro.get("ble_services_ready", False)),
        "slots": _slot_rows(codex_micro),
        "ble_rejects": _object(codex_micro.get("ble_rejects")),
        "ble_last_disconnect": _object(codex_micro.get("ble_last_disconnect")),
        "ble_last_slot_op": _object(codex_micro.get("ble_last_slot_op")),
        "firmware_update": {
            "state": str(firmware_update.get("state", "unknown")),
            "running_partition": str(
                firmware_update.get("running_partition", "unknown")
            ),
            "rollback_pending": firmware_update.get("rollback_pending"),
            "current_boot_stage": str(
                firmware_update.get("current_boot_stage", "unknown")
            ),
        },
    }


def summarize_joystick(config_result: dict, status: dict) -> dict:
    config = _object(config_result.get("config"))
    joystick = _object(config.get("joystick"))
    diagnostics = _object(status.get("joystick_diagnostics"))
    calibrated = joystick.get("calibrated")
    if not isinstance(calibrated, bool):
        calibrated = None
    directions = _integer(diagnostics.get("directions"), -1)
    if calibrated is False:
        decision = "needs-calibration"
        message = "该设备尚未保存摇杆校准；蓝牙清理后应单独完成校准。"
    elif directions > 0:
        decision = "recheck-neutral"
        message = "本次读取时摇杆不在中立状态；松手复读仍非 0 时再校准。"
    elif calibrated is True and directions == 0:
        decision = "calibrated-neutral"
        message = "摇杆已有校准，本次读取处于中立状态。"
    else:
        decision = "unknown"
        message = "当前固件未提供完整摇杆判断数据。"
    return {
        "calibrated": calibrated,
        "directions": directions,
        "raw_x": diagnostics.get("raw_x"),
        "raw_y": diagnostics.get("raw_y"),
        "filtered_x": diagnostics.get("filtered_x"),
        "filtered_y": diagnostics.get("filtered_y"),
        "center_x": diagnostics.get("center_x"),
        "center_y": diagnostics.get("center_y"),
        "deadzone_x": diagnostics.get("deadzone_x"),
        "deadzone_y": diagnostics.get("deadzone_y"),
        "decision": decision,
        "message": message,
    }


def cleanup_safety_summary(ble: dict, joystick: dict) -> dict:
    active_slot = _integer(ble.get("active_slot"))
    slots = {
        _integer(item.get("slot")): item
        for item in ble.get("slots", [])
        if isinstance(item, dict)
    }
    active_paired = bool(slots.get(active_slot, {}).get("paired", False))
    stale_active_slot = (
        active_slot in {1, 2, 3}
        and active_paired
        and _integer(ble.get("active_slot_bond_state"), -1) == 0
    )
    if stale_active_slot:
        ble_decision = "clear-active-slot"
        ble_message = f"槽位 {active_slot} 有配对记录但无对应 bond，可只清除此槽位。"
    else:
        ble_decision = "manual-slot-selection"
        ble_message = "未读到明确的活动槽位失配；只能按实际使用记录选择单个槽位。"
    return {
        "runtime_writes_allowed": True,
        "boot_flash_allowed": False,
        "factory_default_allowed": False,
        "identity_writes_allowed": False,
        "ble_decision": ble_decision,
        "ble_message": ble_message,
        "stale_active_slot": active_slot if stale_active_slot else None,
        "joystick_decision": joystick["decision"],
        "joystick_message": joystick["message"],
        "message": "设备通信正常；只允许清理一个已确认槽位，不会恢复出厂或改写设备身份。",
    }


def inspect_device(client: WmpClient, *, expected_serial: str | None = None) -> dict:
    verification = client.verify(expected_hardware_id=POWER_V2_HARDWARE_ID)
    if expected_serial is not None and verification.serial != expected_serial:
        raise VerificationError(
            f"当前设备 {verification.serial} 与已确认设备 {expected_serial} 不一致"
        )
    config_result = client.get_config()
    if config_result["generation"] != verification.config_generation:
        raise VerificationError("读取期间配置 generation 发生变化，请重新读取")
    raw_status = client.get_status()
    ble = summarize_status(raw_status)
    joystick = summarize_joystick(config_result, raw_status)
    return {
        "identity": asdict(verification),
        "ble": ble,
        "joystick": joystick,
        "cleanup_safety": cleanup_safety_summary(ble, joystick),
        "raw_config": config_result,
        "raw_status": raw_status,
    }


def clear_single_slot(
    client: WmpClient,
    *,
    slot: int,
    expected_serial: str,
    confirmation: str,
) -> dict:
    if slot not in {1, 2, 3}:
        raise VerificationError("蓝牙槽位必须是 1、2 或 3")
    required_confirmation = f"清除槽位 {slot}"
    if confirmation.strip() != required_confirmation:
        raise VerificationError(f"请输入“{required_confirmation}”确认本次单槽清除")

    return clear_authorized_slot(
        client,
        slot=slot,
        expected_serial=expected_serial,
    )


def clear_authorized_slot(
    client: WmpClient,
    *,
    slot: int,
    expected_serial: str,
) -> dict:
    """Clear one slot after the caller has already obtained explicit authorization."""
    if slot not in {1, 2, 3}:
        raise VerificationError("蓝牙槽位必须是 1、2 或 3")

    before = inspect_device(client, expected_serial=expected_serial)
    before_slots = {item["slot"]: item for item in before["ble"]["slots"]}
    if not before_slots[slot]["paired"]:
        raise VerificationError(f"槽位 {slot} 当前为空，没有需要清除的配对")

    response = client.clear_ble_slot(slot)
    after = inspect_device(client, expected_serial=expected_serial)
    after_slots = {item["slot"]: item for item in after["ble"]["slots"]}
    if after_slots[slot]["paired"]:
        raise VerificationError(f"槽位 {slot} 清除后仍报告已配对")
    if before["identity"]["config_generation"] != after["identity"]["config_generation"]:
        raise VerificationError("清槽后配置 generation 发生变化，结果未通过保护检查")
    for other_slot in {1, 2, 3} - {slot}:
        before_other = before_slots[other_slot]
        after_other = after_slots[other_slot]
        for field in ("paired", "address_kind", "identity_known"):
            if before_other[field] != after_other[field]:
                raise VerificationError(
                    f"清除槽位 {slot} 时槽位 {other_slot} 的 {field} 发生变化"
                )
    before_bonds = before["ble"]["bond_count"]
    after_bonds = after["ble"]["bond_count"]
    active_slot = before["ble"]["active_slot"]
    active_bond_state = before["ble"]["active_slot_bond_state"]
    if before_bonds >= 0 and after_bonds >= 0:
        expected_counts = {before_bonds, max(0, before_bonds - 1)}
        if slot == active_slot and active_bond_state == 1:
            expected_counts = {max(0, before_bonds - 1)}
        elif slot == active_slot and active_bond_state == 0:
            expected_counts = {before_bonds}
        if after_bonds not in expected_counts:
            raise VerificationError(
                "清槽后的 bond 数量不符合只删除所选配对的预期"
            )
    return {
        "slot": slot,
        "serial": expected_serial,
        "command_result": response.get("result", {}),
        "before": before,
        "after": after,
    }
