"""USB-only, volatile Claude Code status commands (no config writes)."""
from __future__ import annotations

SET_AGENT_STATUS = "SET_AGENT_STATUS"
CLEAR_AGENT_STATUS = "CLEAR_AGENT_STATUS"
AGENT_COMMANDS = {SET_AGENT_STATUS, CLEAR_AGENT_STATUS}
AGENT_STATES = {"idle", "working", "completed", "approval", "reply", "error"}


def validate_states(states: object) -> None:
    if (not isinstance(states, (list, tuple)) or len(states) != 6
            or any(not isinstance(state, str) or state not in AGENT_STATES for state in states)):
        raise ValueError("Claude Code 状态必须是六个有效状态")


def status_command(states: tuple[str, ...]):
    from controller_config.protocol.bootstrap import Command
    validate_states(states)
    return Command(SET_AGENT_STATUS, 0x28,
                   {"source": "claude_code", "states": list(states)}, timeout_ms=1200)


def clear_status_command():
    from controller_config.protocol.bootstrap import Command
    return Command(CLEAR_AGENT_STATUS, 0x29, {"source": "claude_code"}, timeout_ms=1200)


def validate_agent_response(name: str, payload: dict) -> None:
    result = payload.get("result")
    if (name not in AGENT_COMMANDS or payload.get("command") != name
            or not isinstance(result, dict) or result.get("source") != "claude_code"
            or result.get("active") is not (name == SET_AGENT_STATUS)
            or type(result.get("lease_ms")) is not int or result["lease_ms"] != 5000):
        raise ValueError("Claude Code 状态响应与请求不一致")


def validate_agent_readback(value: object) -> None:
    if (not isinstance(value, dict) or type(value.get("active")) is not bool
            or type(value.get("selected")) is not bool
            or type(value.get("lease_ms")) is not int or value["lease_ms"] != 5000):
        raise ValueError("GET_STATUS claude_code_status 字段无效")
    validate_states(value.get("states"))
