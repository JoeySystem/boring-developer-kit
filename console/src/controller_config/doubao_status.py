"""Authenticated USB/BLE Normal-mode Doubao contract. IDs stay on the host."""
from controller_config.protocol.bootstrap import Command

STATES = frozenset({"unknown", "idle", "working", "completed", "approval", "reply", "error"})


def validate_snapshot(states, completion_seq):
    if (not isinstance(states, (list, tuple)) or len(states) != 6
            or any(not isinstance(s, str) or s not in STATES for s in states)):
        raise ValueError("豆包状态必须是六个有效状态")
    if (not isinstance(completion_seq, (list, tuple)) or len(completion_seq) != 6
            or any(type(n) is not int or not 0 <= n <= 0xFFFFFFFF for n in completion_seq)):
        raise ValueError("豆包完成序号必须是六个 uint32")


def status_command(states, completion_seq, *, encoder_navigation=False):
    validate_snapshot(states, completion_seq)
    payload = {"source": "doubao", "states": list(states), "completion_seq": list(completion_seq)}
    if encoder_navigation:
        payload["encoder_navigation"] = True
    return Command("SET_AGENT_STATUS", 0x28, payload, timeout_ms=1200)


def clear_status_command():
    return Command("CLEAR_AGENT_STATUS", 0x29, {"source": "doubao"}, timeout_ms=1200)


def validate_response(command, payload):
    result = payload.get("result")
    if (payload.get("command") != command.name or not isinstance(result, dict)
            or result.get("source") != "doubao"
            or result.get("active") is not (command.name == "SET_AGENT_STATUS")
            or type(result.get("lease_ms")) is not int or result["lease_ms"] != 5000):
        raise ValueError("豆包状态响应与请求不一致")


def validate_readback(value):
    if (not isinstance(value, dict) or value.get("source") != "doubao"
            or type(value.get("active")) is not bool or type(value.get("selected")) is not bool
            or type(value.get("lease_ms")) is not int or value["lease_ms"] != 5000):
        raise ValueError("GET_STATUS doubao_status 无效")
    validate_snapshot(value.get("states"), value.get("completion_seq"))
    if "encoder_navigation" in value and type(value["encoder_navigation"]) is not bool:
        raise ValueError("豆包旋钮接管状态无效")


def validate_navigation(value):
    if (not isinstance(value, dict) or value.get("source") != "doubao"
            or any(type(value.get(name)) is not int or not 0 <= value[name] <= 0xFFFFFFFF
                   for name in ("sequence", "anchor_sequence"))
            or type(value.get("offset")) is not int or not 0 <= value["offset"] < 6
            or "agent" not in value or "transport" not in value
            or (value["agent"] is not None and
                (type(value["agent"]) is not int or not 0 <= value["agent"] < 6))
            or value["transport"] not in (None, "usb", "ble")
            or (value["transport"] is not None and value["sequence"] == 0)):
        raise ValueError("豆包任务导航事件无效")


def validate_press(value):
    if (not isinstance(value, dict) or value.get("source") != "doubao"
            or type(value.get("sequence")) is not int or not 0 <= value["sequence"] <= 0xFFFFFFFF
            or not {"agent", "transport"} <= value.keys()):
        raise ValueError("GET_STATUS doubao_agent_press 无效")
    if value["agent"] is None and value["transport"] is None:
        return
    if (value["sequence"] == 0 or type(value["agent"]) is not int
            or not 0 <= value["agent"] < 6 or value["transport"] not in {"usb", "ble"}):
        raise ValueError("豆包按键槽位或传输无效")
