"""The three connecting steps on the Mode page (design decision, 单 design ticket 答复 11-22).

While Console connects to a device it has seen before, the Mode page stays (grey)
and the right cell of its bottom bar says where the connection is:

    连接设备…            the channel opening (Bluetooth: GATT subscribed)
    确认是你的 BORING…   HELLO, CAPABILITIES, certificate, challenge
    读取设置…            GET_STATUS and GET_CONFIG

with a 36 ring of dots (16, design decision) that runs on estimated times, never goes
back, and stops at 90 % of a step that has not finished. Under the step a hint:
"这次要把设置完整读一遍…" when the configuration will be read over the link
(known right after HELLO, see BootstrapSession.expects_config_read), or
"比平时慢…" once it takes longer than usual. Done: "已连接", then "已和设备同步".
A connection that is done within a second shows no steps at all.

This module only keeps time and words; it has no Qt in it. The worker reports
steps (SerialWorker.connect_step), MainViewModel feeds them in with the clock,
and the bar asks view(now) about 25 times a second while it is shown.
"""
from __future__ import annotations

from dataclasses import dataclass

STEP_TEXTS = ("连接设备…", "确认是你的 BORING…", "读取设置…")
READING_HINT = "这次要把设置完整读一遍，多等几秒"
SLOW_HINT = "比平时慢，把 BORING 靠近电脑试试"
CONNECTED_TEXT = "已连接"
SYNCED_TEXT = "已和设备同步"

# Estimated seconds per part (Agent's values in the D90 定稿, from the SH-005 measurements):
# channel, identity, device status, configuration (read / taken from this computer's copy).
CHANNEL_S = 1.0
IDENTITY_S = 3.0
STATUS_S = 2.5
CONFIG_READ_S = 3.5
CONFIG_CACHED_S = 0.1
HOLD_SHARE = 0.9          # a part that has not finished stops at 90 % of its estimate
QUIET_S = 1.0             # done within a second: no steps
SLOW_S = 11.0             # "比平时慢" from when the device was found ...
SLOW_READING_S = 15.0     # ... or this when the configuration is read
SYNCED_AFTER_S = 0.6      # "已连接" → "已和设备同步"
DONE_HOLD_S = 1.2         # then the bar's own "已和设备同步" cell takes over
FAILED_HOLD_S = 2.0       # failed: the lit dots orange this long, then the bar's own cell
RETRY_WINDOW_S = 3.0      # a connect this soon after the last step is a retry of the same attempt

# Parts of the ring, in order. "send GET_STATUS" ends identity (it is also what
# a device without authentication sends right after CAPABILITIES).
_CHANNEL, _IDENTITY, _STATUS, _CONFIG = range(4)


@dataclass(frozen=True)
class StepsView:
    title: str
    hint: str
    value: float          # 0..100 for the ring
    ring: str             # running / done / failed (ProgressRing states)


class ConnectionSteps:
    """One connection attempt, from "connect" to done or failed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started: float | None = None
        self._part = _CHANNEL
        self._part_started = 0.0
        self.reading = False
        self.done_at: float | None = None
        self.failed_at: float | None = None
        self._shown = 0.0
        self._touched = 0.0

    # -- what happens -------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """Between "connect" and done / failed."""
        return self.started is not None and self.done_at is None and self.failed_at is None

    def begin(self, now: float) -> None:
        """A connect started. A retry inside an attempt (Bluetooth authentication
        interrupted, reconnecting half a second later) carries on: same start, the
        ring does not go back. An attempt left hanging for longer starts over."""
        if self.active and now - self._touched < RETRY_WINDOW_S:
            self._enter(_CHANNEL, now, force=True)
            self._touched = now
            return
        self.reset()
        self.started = self._touched = now
        self._part_started = now

    def step(self, step: str, detail: str, now: float) -> None:
        """A step from the worker (SerialWorker.connect_step)."""
        if step == "connect":
            self.begin(now)
            return
        if not self.active:
            return
        self._touched = now
        if step == "channel_open":
            self._enter(_IDENTITY, now)
        elif step == "send" and detail == "GET_STATUS":
            self._enter(_STATUS, now)
        elif step == "GET_STATUS":
            self._enter(_CONFIG, now)
        elif step == "config_read":
            self.reading = True

    def finish(self, now: float) -> None:
        if self.active:
            self.done_at = now

    def fail(self, now: float) -> None:
        if self.active:
            self.failed_at = now

    def _enter(self, part: int, now: float, *, force: bool = False) -> None:
        if part > self._part or force:
            self._part, self._part_started = part, now

    # -- what shows -----------------------------------------------------------------------

    def estimates(self) -> tuple[float, float, float, float]:
        return (CHANNEL_S, IDENTITY_S, STATUS_S, CONFIG_READ_S if self.reading else CONFIG_CACHED_S)

    def share(self, now: float) -> float:
        """0..1 round the ring: the finished parts' estimates plus the time spent in
        this one (at most 90 % of its estimate), never less than shown before."""
        if self.started is None:
            return 0.0
        if self.done_at is not None:
            return 1.0
        if self.failed_at is not None:
            now = min(now, self.failed_at)   # stops where it failed
        estimates = self.estimates()
        spent = min(max(0.0, now - self._part_started), HOLD_SHARE * estimates[self._part])
        share = (sum(estimates[:self._part]) + spent) / sum(estimates)
        self._shown = max(self._shown, min(share, 1.0))
        return self._shown

    def title(self, now: float) -> str:
        if self.done_at is not None:
            return SYNCED_TEXT if now - self.done_at >= SYNCED_AFTER_S else CONNECTED_TEXT
        return STEP_TEXTS[min(self._part, 2)]

    def hint(self, now: float) -> str:
        if self.started is None or self.done_at is not None:
            return ""
        if now - self.started >= (SLOW_READING_S if self.reading else SLOW_S):
            return SLOW_HINT
        return READING_HINT if self.reading else ""

    def view(self, now: float) -> StepsView | None:
        """What the bar's right cell shows now, or None for its usual cell."""
        if not self._shows(now):
            return None
        if self.done_at is not None and now - self.done_at >= DONE_HOLD_S:
            return None
        if self.failed_at is not None and now - self.failed_at >= FAILED_HOLD_S:
            return None
        ring = "done" if self.done_at is not None else "failed" if self.failed_at is not None else "running"
        return StepsView(self.title(now), self.hint(now), round(self.share(now) * 100, 1), ring)

    def _shows(self, now: float) -> bool:
        """Past the first second of the attempt (done or failed sooner: never)."""
        if self.started is None:
            return False
        ended = self.done_at if self.done_at is not None else self.failed_at
        return (now if ended is None else ended) - self.started >= QUIET_S

    def needs_frames(self, now: float) -> bool:
        """Whether view() may still change by itself (the bar keeps its clock running)."""
        if self.started is None:
            return False
        if self.done_at is not None:
            return self._shows(now) and now - self.done_at < DONE_HOLD_S
        if self.failed_at is not None:
            return self._shows(now) and now - self.failed_at < FAILED_HOLD_S
        return True
