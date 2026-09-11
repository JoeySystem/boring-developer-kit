from __future__ import annotations

import pytest

from controller_config.claude_sessions import (
    CLAUDE_STATUS_KEYS,
    ClaudeSessionRegistry,
)


def event(hook: str, sid: str = "main", **fields) -> dict:
    return {"session_id": sid, "hook_event_name": hook, **fields}


def test_first_session_starts_idle_and_keeps_its_physical_slot() -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("SessionStart"), now=0)
    assert registry.states() == ("idle",) * 6
    assert registry.sessions[0].slot == 0
    assert CLAUDE_STATUS_KEYS == ("key.1", "key.2", "key.4", "key.5", "key.6", "key.7")
    registry.consume(event("SessionStart", "second"), now=1)
    registry.consume(event("UserPromptSubmit", "second"), now=2)
    registry.consume(event("PreToolUse", tool_name="Read"), now=3)
    assert [(view.session_id, view.slot) for view in registry.sessions] == [
        ("main", 0), ("second", 1)
    ]
    assert registry.states() == ("working", "working", "idle", "idle", "idle", "idle")


@pytest.mark.parametrize(
    ("hook", "fields", "expected"),
    [
        ("UserPromptSubmit", {}, "working"),
        ("PreToolUse", {"tool_name": "Bash"}, "working"),
        ("PreToolUse", {"tool_name": "AskUserQuestion"}, "reply"),
        ("PermissionRequest", {}, "approval"),
        ("PostToolUse", {}, "working"),
        ("PostToolUseFailure", {"is_interrupt": False}, "working"),
        ("PostToolUseFailure", {"is_interrupt": True}, "idle"),
        ("StopFailure", {"error": "rate_limit"}, "error"),
        ("Notification", {"notification_type": "permission_prompt"}, "approval"),
        ("Notification", {"notification_type": "elicitation_dialog"}, "reply"),
        ("Notification", {"notification_type": "elicitation_url_dialog"}, "reply"),
    ],
)
def test_hook_state_mapping(hook: str, fields: dict, expected: str) -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("UserPromptSubmit"), now=0)
    registry.consume(event(hook, **fields), now=1)
    assert registry.states()[0] == expected


def test_approval_or_question_returns_to_working_after_successful_tool() -> None:
    registry = ClaudeSessionRegistry()
    for hook, fields in (
        ("PermissionRequest", {}),
        ("PreToolUse", {"tool_name": "AskUserQuestion"}),
    ):
        registry.consume(event(hook, **fields), now=0)
        registry.consume(event("PostToolUse"), now=1)
        assert registry.states()[0] == "working"


@pytest.mark.parametrize(
    ("fields", "expected", "reason"),
    [
        ({}, "idle", "stop_continuation_unknown"),
        ({"stop_hook_active": False}, "completed", "response_finished_background_unknown"),
        ({"stop_hook_active": True}, "idle", "stop_decision_unknown"),
        ({"has_background_tasks": True}, "working", "continuation_pending"),
        ({"has_session_crons": True}, "working", "continuation_pending"),
        ({"stop_hook_active": False, "has_background_tasks": False,
          "has_session_crons": False}, "completed", "response_finished"),
    ],
)
def test_stop_reports_response_end_and_respects_known_background_or_unknown_decisions(
    fields: dict, expected: str, reason: str
) -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("UserPromptSubmit"), now=0)
    registry.consume(event("Stop", **fields), now=1)
    assert registry.states()[0] == expected
    assert registry.sessions[0].reason == reason


def test_subagent_end_cannot_complete_main_but_known_subagent_keeps_stop_working():
    registry = ClaudeSessionRegistry()
    registry.consume(event("UserPromptSubmit"), now=0)
    registry.consume(event("SubagentStart", agent_id="child"), now=1)
    registry.consume(event("Stop", stop_hook_active=False), now=2)
    assert registry.states()[0] == "working"
    registry.consume(event("SubagentStop", agent_id="child"), now=3)
    assert registry.states()[0] == "working"
    registry.consume(event("Stop", stop_hook_active=False), now=4)
    assert registry.states()[0] == "completed"


def test_repeated_hooks_do_not_change_snapshot_or_slot_owner() -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("PermissionRequest"), now=0)
    duplicate = registry.consume(event("PermissionRequest"), now=1)
    assert duplicate.states == ("approval", "idle", "idle", "idle", "idle", "idle")
    assert duplicate.owner_changed_slots == ()
    assert duplicate.cleared_slots == ()
    assert registry.sessions[0].last_event_at == 1


@pytest.mark.parametrize("notification", ["idle_prompt", "auth_success", "agent_completed", "other"])
def test_unknown_notifications_neither_complete_nor_renew_session(notification: str) -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("UserPromptSubmit"), now=0)
    update = registry.consume(event("Notification", notification_type=notification), now=119)
    assert not update.changed
    assert registry.sessions[0].last_event_at == 0
    registry.expire(now=120)
    assert registry.states() == ("idle",) * 6


def test_child_hooks_do_not_complete_release_or_replace_main_session_state() -> None:
    registry = ClaudeSessionRegistry()
    registry.consume(event("PermissionRequest"), now=0)
    for hook in ("Stop", "StopFailure", "SessionEnd", "PostToolUse", "PreToolUse"):
        update = registry.consume(event(hook, agent_id="child"), now=1)
        assert not update.changed
    for hook in ("SubagentStart", "SubagentStop"):
        registry.consume(event(hook), now=2)
    assert registry.states()[0] == "approval"
    assert registry.sessions[0].last_event_at == 0


def test_seventh_session_waits_and_reuse_requires_an_idle_ack() -> None:
    registry = ClaudeSessionRegistry()
    for number in range(7):
        registry.consume(event("PermissionRequest", str(number)), now=number)
    assert [view.session_id for view in registry.sessions] == list("012345")
    assert [view.session_id for view in registry.overflow] == ["6"]
    freed = registry.consume(event("SessionEnd", "2"), now=8)
    assert freed.states == ("approval", "approval", "idle", "approval", "approval", "approval")
    assert registry.pending_clear_slots == (2,)
    registry.consume(event("PermissionRequest", "6"), now=9)
    assert registry.states()[2] == "idle"
    reused = registry.acknowledge_cleared([2])
    assert reused.states == ("approval",) * 6
    assert reused.owner_changed_slots == (2,)
    assert registry.sessions[2].session_id == "6"
    assert not registry.overflow
    assert not registry.pending_clear_slots


def test_acknowledging_only_some_cleared_slots_does_not_reuse_the_others() -> None:
    registry = ClaudeSessionRegistry()
    for number in range(8):
        registry.consume(event("UserPromptSubmit", str(number)), now=number)
    registry.consume(event("SessionEnd", "0"), now=9)
    registry.consume(event("SessionEnd", "1"), now=10)
    registry.acknowledge_cleared([0])
    assert registry.states()[:2] == ("working", "idle")
    assert registry.pending_clear_slots == (1,)
    assert [view.session_id for view in registry.overflow] == ["7"]


def test_session_end_can_remove_unmapped_session_without_touching_device() -> None:
    registry = ClaudeSessionRegistry()
    for number in range(7):
        registry.consume(event("UserPromptSubmit", str(number)), now=number)
    update = registry.consume(event("SessionEnd", "6"), now=7)
    assert update.states == ("working",) * 6
    assert not update.cleared_slots
    assert not registry.overflow


def test_dead_process_releases_slot_without_waiting_for_session_end() -> None:
    owners = {(41, "start-one"): True}
    registry = ClaudeSessionRegistry(owner_alive=lambda pid, started: owners.get((pid, started), False))
    registry.consume(event("UserPromptSubmit", owner_pid=41, owner_started="start-one"), now=0)
    owners[(41, "start-one")] = False
    registry.expire(now=1)
    assert registry.states() == ("idle",) * 6
    assert registry.pending_clear_slots == (0,)
    assert not registry.sessions
    assert registry.last_cleared_reason == "owner_exited"


def test_live_process_does_not_keep_stale_working_after_unreported_interrupt() -> None:
    registry = ClaudeSessionRegistry(owner_alive=lambda _pid, _started: True)
    registry.consume(event("UserPromptSubmit", owner_pid=41, owner_started=1.25), now=0)
    registry.expire(now=119)
    assert registry.states()[0] == "working"
    registry.expire(now=120)
    assert registry.states() == ("idle",) * 6
    assert registry.sessions[0].stale
    assert registry.sessions[0].reason == "stale_no_events"
    assert not registry.pending_clear_slots
    registry.consume(event("UserPromptSubmit"), now=121)
    assert registry.states()[0] == "working"
    assert not registry.sessions[0].stale
    assert registry.sessions[0].owner_pid == 41


def test_unreliable_owner_expires_and_does_not_permanently_occupy_six_slots() -> None:
    registry = ClaudeSessionRegistry()
    for number in range(6):
        registry.consume(event("UserPromptSubmit", str(number)), now=0)
    registry.consume(event("UserPromptSubmit", "new"), now=119)
    registry.expire(now=120)
    assert registry.states() == ("idle",) * 6
    assert not registry.sessions
    assert registry.pending_clear_slots == tuple(range(6))
    assert registry.last_cleared_reason == "stale_owner_unknown"
    registry.acknowledge_cleared(range(6))
    assert registry.sessions[0].session_id == "new"
    assert registry.states()[0] == "working"


def test_pid_reuse_with_different_start_time_is_not_the_original_owner() -> None:
    registry = ClaudeSessionRegistry(owner_alive=lambda pid, start: (pid, start) == (41, 2.0))
    registry.consume(event("UserPromptSubmit", owner_pid=41, owner_started=1.0), now=0)
    registry.expire(now=1)
    assert not registry.sessions


def test_unsupported_event_does_not_create_session_or_consume_slot() -> None:
    registry = ClaudeSessionRegistry()
    for payload in ({}, event("Unknown"), event("Notification", notification_type="idle_prompt")):
        assert not registry.consume(payload, now=0).changed
    assert not registry.sessions
    assert registry.states() == ("idle",) * 6
