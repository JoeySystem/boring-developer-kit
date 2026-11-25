"""Claude desktop Code sessions as one app of the desktop Agent hub.

design decision: the lights of Claude desktop's local Code sessions go over the
generic `desktop` source like Doubao's, so Claude, Doubao and Codex share the
six lights over USB and Bluetooth alike. State comes from the Hooks registry
(``ClaudeSessionRegistry``, fed by ``ClaudeStatusBridge``); this module only
turns it into hub conversations and handles a press on a Claude light.

Two hooks are left open until the open-by-session experiment reports
(adapt1-verify, 11-16):
* ``open_session(session_id) -> bool``: open exactly this session in Claude
  and verify it; True means it is on screen. Not available yet, so a press
  falls back to bringing Claude to the front.
* ``mark_viewed(session_id)``: Claude's own evidence that the user saw the
  latest completion. Until a real signal exists, a press on the light counts
  as seen (same as Doubao's fallback when Console cannot look into the app).

Privacy: only session IDs and states are held, in memory; no prompt, reply,
title or path. ``stats()`` holds counts only.
"""
from __future__ import annotations

import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QObject, Signal

from controller_config.desktop_agent_hub import Conversation, OpenResult

APP_ID = "claude"
# Claude desktop on macOS (LaunchServices bundle identifier).
MAC_BUNDLE_ID = "com.anthropic.claudefordesktop"
# Registry states that take a light; `idle` (and released sessions) do not.
LIGHT_STATES = frozenset({"working", "completed", "approval", "reply", "error"})

# Press results in product language (no IDs). The hub shows them as a notice.
OPEN_FRONT_ONLY = "已切到 Claude，但没能自动打开这个会话，请在 Claude 里手动点开"
OPEN_NOT_ACTIVATED = "没能切到 Claude，请手动打开 Claude 查看这个会话"


def default_activate(platform: str | None = None) -> Callable[[], bool]:
    """Bring Claude desktop to the front; True when the system accepted it.

    macOS: LaunchServices by bundle id (`open -b`), no AppleScript, no key
    injection, the pointer never moves. Elsewhere not available yet.
    """
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        def mac() -> bool:
            try:
                result = subprocess.run(["/usr/bin/open", "-b", MAC_BUNDLE_ID],
                                        capture_output=True, timeout=5, check=False)
            except (OSError, subprocess.SubprocessError):
                return False
            return result.returncode == 0
        return mac
    return lambda: False


class _Inline:
    """Executor running jobs at once on the calling thread (tests, demo)."""

    def submit(self, fn, *args):
        fn(*args)

    def shutdown(self, **_kwargs):
        pass


class ClaudeDesktopAdapter(QObject):
    """ConversationSource + Navigator for the hub (``app_id = "claude"``)."""

    app_id = APP_ID
    changed = Signal()

    def __init__(self, registry, *, activate: Callable[[], bool] | None = None,
                 open_session: Callable[[str], bool] | None = None,
                 executor=None, parent=None):
        super().__init__(parent)
        self.registry = registry
        self._activate = activate if activate is not None else default_activate()
        self.open_session = open_session
        self._worker = executor if executor is not None else ThreadPoolExecutor(
            1, thread_name_prefix="claude-ui")
        # Completion turns: one new value per new completed Stop. A global
        # counter keeps values unique even if a session leaves and comes back.
        self._next_turn = 0
        self._completed_at: dict[str, float] = {}
        self._turn: dict[str, str] = {}
        self._viewed_turn: dict[str, str | None] = {}
        self.counters: Counter = Counter()
        self._closed = False

    # ---- ConversationSource --------------------------------------------------

    def _views(self):
        return (*self.registry.sessions, *self.registry.overflow)

    def conversations(self) -> list[Conversation]:
        views = self._views()
        live = {view.session_id for view in views}
        for table in (self._completed_at, self._turn, self._viewed_turn):
            for session_id in [sid for sid in table if sid not in live]:
                del table[session_id]
        result = []
        for view in views:
            sid = view.session_id
            if view.state == "completed" and self._completed_at.get(sid) != view.last_event_at:
                # Each completed Stop carries its own event time: a new value
                # is one new completion (the light pulses once).
                self._completed_at[sid] = view.last_event_at
                self._next_turn += 1
                self._turn[sid] = str(self._next_turn)
            if view.state not in LIGHT_STATES:
                continue  # idle: no light (design decision "没有就不亮")
            turn = self._turn.get(sid)
            result.append(Conversation(
                sid, view.state, view.last_event_at, turn,
                viewed=turn is not None and self._viewed_turn.get(sid, ()) == turn))
        return result

    def mark_viewed(self, session_id: str) -> None:
        """The user has seen this session's latest completion (light turns white).

        Interface for a real "seen" signal from Claude; today a press calls it.
        """
        self._viewed_turn[session_id] = self._turn.get(session_id)
        self.changed.emit()

    # ---- Navigator -----------------------------------------------------------

    def open(self, conversation_id: str, finished: Callable[[OpenResult], None]) -> None:
        # The press itself is the acknowledgement (fallback until Claude
        # exposes a seen signal): mark first, on the Qt thread.
        self.mark_viewed(conversation_id)
        self._worker.submit(self._open, conversation_id, finished)

    def _open(self, conversation_id: str, finished) -> None:
        if self.open_session is not None:
            try:
                if self.open_session(conversation_id):
                    self.counters["open_session_ok"] += 1
                    finished(OpenResult(active_id=conversation_id))
                    return
                self.counters["open_session_failed"] += 1
            except Exception as exc:  # noqa: BLE001 - fall back to the front
                self.counters["open_session_" + type(exc).__name__] += 1
        try:
            front = bool(self._activate())
        except Exception as exc:  # noqa: BLE001 - reported, never raised into Qt
            self.counters["activate_" + type(exc).__name__] += 1
            front = False
        self.counters["activate_ok" if front else "activate_failed"] += 1
        finished(OpenResult(error=OPEN_FRONT_ONLY if front else OPEN_NOT_ACTIVATED))

    # ---- lifecycle and diagnostics -------------------------------------------

    def close(self) -> None:
        self._closed = True
        self._worker.shutdown(wait=False, cancel_futures=True)

    def stats(self) -> dict:
        """Counts only: no session IDs."""
        states = Counter(view.state for view in self._views())
        return {"sessions": sum(states.values()), "states": dict(sorted(states.items())),
                "counters": dict(sorted(self.counters.items()))}
