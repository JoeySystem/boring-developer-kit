"""Privacy-minimal Claude hook events to the six CC status-key slots.

The registry does not read transcripts or infer task success.  ``completed``
means only that a Stop explicitly reported a finished response with no known
continuation.  The transport must ACK an idle snapshot before acknowledging a
released slot here; only then may a waiting session take its place.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEY_ORDER


CLAUDE_STATUS_KEYS = MATRIX12_AGENT_STATUS_KEY_ORDER
STALE_AFTER_SECONDS = 120.0
OwnerStarted = str | float | int
OwnerAlive = Callable[[int, OwnerStarted], bool | None]


@dataclass(frozen=True)
class ClaudeSessionView:
    session_id: str
    slot: int | None
    state: str
    reason: str
    last_event_at: float
    owner_pid: int | None = None
    owner_started: OwnerStarted | None = None
    stale: bool = False


@dataclass(frozen=True)
class RegistryUpdate:
    states: tuple[str, ...]
    changed: bool
    cleared_slots: tuple[int, ...]
    owner_changed_slots: tuple[int, ...]


class ClaudeSessionRegistry:
    """Stable first-available assignment, with explicit idle-before-reuse ACKs.

    ``sessions`` contains assigned sessions in slot order; ``overflow`` contains
    unassigned sessions in arrival order.  Empty slots are represented by idle
    in ``states()``.  No owner-liveness callback means PID data is not trusted as
    evidence that a process is still running.
    """

    def __init__(
        self,
        *,
        owner_alive: OwnerAlive | None = None,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self._owner_alive = owner_alive
        self._stale_after = stale_after_seconds
        self._sessions: dict[str, ClaudeSessionView] = {}
        self._slots: list[str | None] = [None] * len(CLAUDE_STATUS_KEYS)
        self._pending_clear: set[int] = set()
        self._subagents: dict[str, set[str]] = {}
        self.last_cleared_reason = ""

    @property
    def sessions(self) -> tuple[ClaudeSessionView, ...]:
        return tuple(self._sessions[sid] for sid in self._slots if sid is not None)

    @property
    def overflow(self) -> tuple[ClaudeSessionView, ...]:
        return tuple(view for view in self._sessions.values() if view.slot is None)

    @property
    def pending_clear_slots(self) -> tuple[int, ...]:
        return tuple(sorted(self._pending_clear))

    def states(self) -> tuple[str, ...]:
        return tuple(
            self._sessions[sid].state if sid is not None else "idle"
            for sid in self._slots
        )

    def consume(self, event: dict, now: float | None = None) -> RegistryUpdate:
        before = self._snapshot()
        now = time.monotonic() if now is None else now
        session_id = event.get("session_id")
        hook = event.get("hook_event_name")
        if not isinstance(session_id, str) or not session_id:
            return self._update(before)
        # Global hooks also run in subagents.  Their Stop/SessionEnd must not
        # finish or release the main session, and their tool events must not
        # replace a main-agent approval/reply state.
        if hook in {"SubagentStart", "SubagentStop"}:
            agent_id = event.get("agent_id")
            if session_id in self._sessions and isinstance(agent_id, str) and agent_id:
                agents = self._subagents.setdefault(session_id, set())
                if hook == "SubagentStart":
                    agents.add(agent_id)
                else:
                    agents.discard(agent_id)
            return self._update(before)
        if event.get("agent_id"):
            return self._update(before)
        if hook == "SessionEnd":
            self._release(session_id, "session_ended")
            return self._update(before)
        if hook == "Stop" and self._subagents.get(session_id):
            event = {**event, "has_background_tasks": True}
        transition = _transition(event)
        if transition is None:
            return self._update(before)
        state, reason = transition
        previous = self._sessions.get(session_id)
        pid = event.get("owner_pid")
        started = event.get("owner_started")
        if not (
            type(pid) is int and pid > 0
            and type(started) in (str, float, int) and started != ""
        ):
            pid = previous.owner_pid if previous else None
            started = previous.owner_started if previous else None
        # A resumed session may have the same session_id but another process.
        # It is still the same light owner, not a newly assigned session slot.
        self._sessions[session_id] = ClaudeSessionView(
            session_id=session_id,
            slot=previous.slot if previous else None,
            state=state,
            reason=reason,
            last_event_at=now,
            owner_pid=pid,
            owner_started=started,
        )
        self._assign_waiting()
        return self._update(before)

    def expire(self, now: float | None = None) -> RegistryUpdate:
        before = self._snapshot()
        now = time.monotonic() if now is None else now
        for session_id, view in tuple(self._sessions.items()):
            alive = self._owner_status(view)
            if alive is False:
                self._release(session_id, "owner_exited")
            elif now - view.last_event_at >= self._stale_after:
                if alive is True:
                    # An idle CLI can remain alive after a user interrupt.
                    # Liveness alone is never evidence of an ongoing task.
                    self._sessions[session_id] = replace(
                        view, state="idle", reason="stale_no_events", stale=True
                    )
                else:
                    # Without reliable process identity, retaining dead slots
                    # forever would eventually prevent all new sessions.
                    self._release(session_id, "stale_owner_unknown")
        return self._update(before)

    def acknowledge_cleared(self, indices: Iterable[int]) -> RegistryUpdate:
        """Call only for idle indices present in a successfully ACKed snapshot."""
        before = self._snapshot()
        self._pending_clear.difference_update(indices)
        self._assign_waiting()
        return self._update(before)

    def _owner_status(self, view: ClaudeSessionView) -> bool | None:
        if (
            self._owner_alive is None
            or view.owner_pid is None
            or view.owner_started is None
        ):
            return None
        try:
            return self._owner_alive(view.owner_pid, view.owner_started)
        except OSError:
            return None

    def _release(self, session_id: str, reason: str) -> None:
        self._subagents.pop(session_id, None)
        view = self._sessions.pop(session_id, None)
        if view is None:
            return
        self.last_cleared_reason = reason
        if view.slot is not None:
            self._slots[view.slot] = None
            self._pending_clear.add(view.slot)

    def _assign_waiting(self) -> None:
        free = [
            i for i, sid in enumerate(self._slots)
            if sid is None and i not in self._pending_clear
        ]
        for slot, view in zip(free, self.overflow):
            self._slots[slot] = view.session_id
            self._sessions[view.session_id] = replace(view, slot=slot)

    def _snapshot(self) -> tuple:
        return self.sessions, self.overflow, self.pending_clear_slots, tuple(self._slots)

    def _update(self, before: tuple) -> RegistryUpdate:
        return RegistryUpdate(
            states=self.states(),
            changed=self._snapshot() != before,
            cleared_slots=self.pending_clear_slots,
            owner_changed_slots=tuple(
                i for i, sid in enumerate(self._slots) if sid != before[3][i]
            ),
        )


def _transition(event: dict) -> tuple[str, str] | None:
    hook = event.get("hook_event_name")
    if hook == "SessionStart":
        return "idle", "session_started"
    if hook == "UserPromptSubmit":
        return "working", "prompt_submitted"
    if hook == "PreToolUse":
        if event.get("tool_name") == "AskUserQuestion":
            return "reply", "question_requested"
        return "working", "tool_started"
    if hook == "PermissionRequest":
        return "approval", "permission_requested"
    if hook == "PostToolUse":
        return "working", "tool_finished"
    if hook == "PostToolUseFailure":
        if event.get("is_interrupt") is True:
            return "idle", "interrupted"
        return "working", "tool_failed_turn_continues"
    if hook == "Stop":
        continuation = (
            event.get("has_background_tasks"),
            event.get("has_session_crons"),
        )
        if any(value is True for value in continuation):
            return "working", "continuation_pending"
        # This flag describes a previous Stop-hook continuation, not a promise
        # that work will continue after this Stop. Do not leave fake working.
        if event.get("stop_hook_active") is True:
            return "idle", "stop_decision_unknown"
        if event.get("stop_hook_active") is False and all(value is False for value in continuation):
            return "completed", "response_finished"
        if event.get("stop_hook_active") is False:
            # Older supported versions omit background metadata. Stop still
            # explicitly reports the end of this response, not project success.
            return "completed", "response_finished_background_unknown"
        return "idle", "stop_continuation_unknown"
    if hook == "StopFailure":
        return "error", "turn_api_error"
    if hook == "Notification":
        notification = event.get("notification_type")
        if notification == "permission_prompt":
            return "approval", "permission_requested"
        if notification in {"elicitation_dialog", "elicitation_url_dialog"}:
            return "reply", "question_requested"
    return None
