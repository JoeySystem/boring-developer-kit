"""Wire format of the generic `desktop` Agent status source. IDs stay on the host.

Console side of protocol.md "Desktop Agent status source" and "Desktop encoder
task navigation": SET/CLEAR_AGENT_STATUS requests and ACKs, and the
GET_STATUS desktop_agent_status, desktop_agent_press, desktop_navigation and
codex_micro.status_age_ms readbacks. Readback objects may grow new fields in a
later protocol minor; wrong types and values of the known fields fail.
"""
from controller_config.protocol.bootstrap import Command

SOURCE = "desktop"
SET_AGENT_STATUS = "SET_AGENT_STATUS"
CLEAR_AGENT_STATUS = "CLEAR_AGENT_STATUS"
SET_AGENT_STATUS_TYPE = 0x28
CLEAR_AGENT_STATUS_TYPE = 0x29
STATES = frozenset({"unknown", "idle", "standby", "working", "completed",
                    "approval", "reply", "error", "codex"})
SLOTS = 6
UINT32_MAX = 0xFFFFFFFF
LEASE_MS = 5000
EVENT_LIFETIME_MS = 2000
# Working BLE GET_STATUS poll interval until the firmware reports a measured
# one (protocol.md, design decision).
POLL_INTERVAL_MS = 250
ENCODER_NAVIGATION = "encoder_navigation"
FEATURE_STATUS = "desktop_agent_status"
FEATURE_NAVIGATION = "desktop_task_navigation"
STATUS_FIELD = "desktop_agent_status"
PRESS_FIELD = "desktop_agent_press"
NAVIGATION_FIELD = "desktop_navigation"
TRANSPORTS = ("usb", "ble")


def _uint32(value) -> bool:
    return type(value) is int and 0 <= value <= UINT32_MAX


def _slot(value) -> bool:
    return type(value) is int and 0 <= value < SLOTS


def is_desktop(command) -> bool:
    return getattr(command, "payload", {}).get("source") == SOURCE


def validate_snapshot(states, completion_seq) -> None:
    if (not isinstance(states, (list, tuple)) or len(states) != SLOTS
            or any(not isinstance(s, str) or s not in STATES for s in states)):
        raise ValueError("desktop 状态必须是六个有效状态")
    if (not isinstance(completion_seq, (list, tuple)) or len(completion_seq) != SLOTS
            or not all(_uint32(n) for n in completion_seq)):
        raise ValueError("desktop 完成序号必须是六个 uint32")


def validate_set_request(payload) -> None:
    """Exactly what the firmware accepts for SET_AGENT_STATUS(source=desktop)."""
    if not isinstance(payload, dict) or payload.get("source") != SOURCE:
        raise ValueError("SET_AGENT_STATUS 的 source 必须是 desktop")
    allowed = {"source", "states", "completion_seq", ENCODER_NAVIGATION}
    if not {"states", "completion_seq"} <= payload.keys() or not payload.keys() <= allowed:
        raise ValueError("SET_AGENT_STATUS 字段与 desktop 来源不符")
    validate_snapshot(payload["states"], payload["completion_seq"])
    if ENCODER_NAVIGATION in payload and type(payload[ENCODER_NAVIGATION]) is not bool:
        raise ValueError("encoder_navigation 必须是布尔值")


def validate_clear_request(payload) -> None:
    if payload != {"source": SOURCE}:
        raise ValueError("CLEAR_AGENT_STATUS 只接受 {\"source\":\"desktop\"}")


def status_command(states, completion_seq, *, encoder_navigation=False) -> Command:
    payload = {"source": SOURCE, "states": list(states), "completion_seq": list(completion_seq)}
    if encoder_navigation:
        payload[ENCODER_NAVIGATION] = True
    validate_set_request(payload)
    return Command(SET_AGENT_STATUS, SET_AGENT_STATUS_TYPE, payload, timeout_ms=1200)


def clear_status_command() -> Command:
    return Command(CLEAR_AGENT_STATUS, CLEAR_AGENT_STATUS_TYPE, {"source": SOURCE},
                   timeout_ms=1200)


def validate_response(command, payload) -> int:
    """Check an ACK against its request; returns the lease in milliseconds."""
    result = payload.get("result") if isinstance(payload, dict) else None
    if (not isinstance(payload, dict) or command.name not in (SET_AGENT_STATUS, CLEAR_AGENT_STATUS)
            or payload.get("command") != command.name or not isinstance(result, dict)
            or result.get("source") != SOURCE
            or result.get("active") is not (command.name == SET_AGENT_STATUS)
            or type(result.get("lease_ms")) is not int or result["lease_ms"] != LEASE_MS):
        raise ValueError("desktop 状态应答与请求不一致")
    return result["lease_ms"]


def validate_readback(value) -> None:
    if (not isinstance(value, dict) or value.get("source") != SOURCE
            or type(value.get("active")) is not bool or type(value.get("selected")) is not bool
            or type(value.get("lease_ms")) is not int or value["lease_ms"] != LEASE_MS):
        raise ValueError("GET_STATUS desktop_agent_status 无效")
    validate_snapshot(value.get("states"), value.get("completion_seq"))
    if ENCODER_NAVIGATION in value and type(value[ENCODER_NAVIGATION]) is not bool:
        raise ValueError("GET_STATUS desktop_agent_status.encoder_navigation 无效")


def validate_press(value) -> None:
    if (not isinstance(value, dict) or value.get("source") != SOURCE
            or not _uint32(value.get("sequence"))
            or not {"agent", "transport"} <= value.keys()):
        raise ValueError("GET_STATUS desktop_agent_press 无效")
    if value["agent"] is None and value["transport"] is None:
        return
    if value["sequence"] == 0 or not _slot(value["agent"]) or value["transport"] not in TRANSPORTS:
        raise ValueError("GET_STATUS desktop_agent_press 的槽位或连接无效")


def validate_navigation(value) -> None:
    if (not isinstance(value, dict) or value.get("source") != SOURCE
            or not _uint32(value.get("sequence")) or not _uint32(value.get("anchor_sequence"))
            or not _slot(value.get("offset"))
            or not {"agent", "transport"} <= value.keys()
            or (value["agent"] is not None and not _slot(value["agent"]))
            or value["transport"] not in (None, *TRANSPORTS)
            or (value["transport"] is None and value["agent"] is not None)
            or (value["transport"] is not None and value["sequence"] == 0)):
        raise ValueError("GET_STATUS desktop_navigation 无效")


def validate_codex_status_age(codex_micro) -> None:
    """codex_micro.status_age_ms is a uint32 or null; older firmware omits it."""
    if (isinstance(codex_micro, dict) and "status_age_ms" in codex_micro
            and codex_micro["status_age_ms"] is not None
            and not _uint32(codex_micro["status_age_ms"])):
        raise ValueError("GET_STATUS codex_micro.status_age_ms 必须是 uint32 或 null")
