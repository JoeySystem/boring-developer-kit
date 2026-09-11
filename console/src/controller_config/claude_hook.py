"""Short, non-decision-making Claude Code hook adapter (deliberately no Qt)."""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import socket
import sys
from typing import Any

import psutil


MAX_INPUT_BYTES = 1024 * 1024
MAX_MESSAGE_BYTES = 16 * 1024
SOCKET_TIMEOUT_SECONDS = 0.2
HOOK_EVENTS = frozenset({
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PermissionRequest", "PostToolUse", "PostToolUseFailure", "Notification",
    "Stop", "StopFailure", "SubagentStart", "SubagentStop",
})
STOP_ERRORS = frozenset({
    "rate_limit", "overloaded", "authentication_failed", "oauth_org_not_allowed",
    "account_on_hold", "billing_error", "invalid_request", "model_not_found",
    "server_error", "max_output_tokens", "unknown",
})


def extract_event(payload: object) -> dict[str, Any] | None:
    """Copy only lifecycle metadata; never forward input, replies or transcripts."""
    if not isinstance(payload, dict):
        return None
    session_id = payload.get("session_id")
    event_name = payload.get("hook_event_name")
    if (not isinstance(session_id, str) or not 0 < len(session_id) <= 256
            or not isinstance(event_name, str) or event_name not in HOOK_EVENTS):
        return None
    result: dict[str, Any] = {"session_id": session_id, "hook_event_name": event_name}
    for key in ("tool_name", "notification_type", "agent_id"):
        value = payload.get(key)
        if isinstance(value, str) and 0 < len(value) <= 256:
            result[key] = value
    for key in ("stop_hook_active", "is_interrupt"):
        if isinstance(payload.get(key), bool):
            result[key] = payload[key]
    for source, target in (("background_tasks", "has_background_tasks"),
                           ("session_crons", "has_session_crons")):
        value = payload.get(source)
        if isinstance(value, list):
            result[target] = bool(value)
        elif isinstance(payload.get(target), bool):
            result[target] = payload[target]
    error = payload.get("error")
    if event_name == "StopFailure" and isinstance(error, str) and error in STOP_ERRORS:
        result["error"] = error
    return result


def _is_claude_process(process: psutil.Process) -> bool:
    name = process.name().lower()
    executable_path = process.exe().replace("\\", "/")
    executable = executable_path.rsplit("/", 1)[-1].lower()
    if name in {"claude", "claude.exe"} or executable in {"claude", "claude.exe"}:
        return True
    if "/.local/share/claude/versions/" in executable_path and re.fullmatch(r"\d+\.\d+\.\d+", executable):
        return True
    # Older supported npm installs execute the official CLI through node. Do not
    # mistake bash/cmd, the hook Python, or an arbitrary node process for Claude.
    if name in {"node", "node.exe"} or executable in {"node", "node.exe"}:
        args = process.cmdline()
        script = args[1].replace("\\", "/") if len(args) > 1 else ""
        return script.endswith("/@anthropic-ai/claude-code/cli.js")
    return False


def claude_owner() -> tuple[int | None, float | None]:
    """Identify the actual Claude ancestor, not the transient hook shell."""
    try:
        process = psutil.Process().parent()
        for _ in range(32):
            if process is None:
                break
            if _is_claude_process(process):
                return process.pid, process.create_time()
            process = process.parent()
    except (psutil.Error, OSError):
        pass
    return None, None


def run_hook(endpoint_path: str | Path) -> int:
    """Best effort, bounded localhost delivery; always silent and exit zero."""
    try:
        with Path(endpoint_path).open("rb") as handle:
            endpoint_bytes = handle.read(MAX_MESSAGE_BYTES + 1)
        if len(endpoint_bytes) > MAX_MESSAGE_BYTES:
            return 0
        endpoint = json.loads(endpoint_bytes)
        if not isinstance(endpoint, dict) or endpoint.get("host") != "127.0.0.1":
            return 0
        port, token = endpoint.get("port"), endpoint.get("token")
        if (type(port) is not int or not 0 < port < 65536
                or not isinstance(token, str) or not 1 <= len(token) <= 512):
            return 0
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        event = extract_event(json.loads(raw))
        if event is None:
            return 0
        pid, started = claude_owner()
        event["owner_pid"] = pid
        event["owner_started"] = started if started is None or math.isfinite(started) else None
        message = json.dumps({"token": token, "event": event}, ensure_ascii=False).encode("utf-8") + b"\n"
        if len(message) > MAX_MESSAGE_BYTES:
            return 0
        with socket.create_connection(("127.0.0.1", port), timeout=SOCKET_TIMEOUT_SECONDS) as connection:
            connection.settimeout(SOCKET_TIMEOUT_SECONDS)
            connection.sendall(message)
    except Exception:
        # An offline console or malformed hook input must never affect Claude's
        # permission decisions, print transcript text, or produce a hook error.
        pass
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--endpoint":
        return run_hook(args[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
