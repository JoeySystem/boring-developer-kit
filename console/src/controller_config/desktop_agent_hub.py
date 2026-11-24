"""Generic `desktop` Agent status source: six Agent lights for desktop apps.

Console side of protocol.md "Desktop Agent status source" and "Desktop encoder
task navigation". Apps report their conversations through a small
ConversationSource; the hub assigns them to the six slots (keys 1, 2, 4, 5, 6,
7), leases the slots with SET/CLEAR_AGENT_STATUS and hands key and encoder
input to the app's Navigator, which opens and verifies the conversation. The
firmware never learns which app owns a slot, and this module imports no app.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config import desktop_status_wire as wire
from controller_config.desktop_status_wire import (  # noqa: F401 - re-exported
    CLEAR_AGENT_STATUS, SET_AGENT_STATUS, UINT32_MAX, clear_status_command,
    status_command, validate_clear_request, validate_navigation, validate_press,
    validate_readback, validate_response, validate_set_request, validate_snapshot,
)
from controller_config.protocol.bootstrap import COMMAND_DROPPED
from controller_config.protocol.contract import (
    DESKTOP_AGENT_BLE_POLL_MS, DESKTOP_AGENT_EVENT_LIFETIME_MS, DESKTOP_AGENT_SLOT_KEYS,
    DESKTOP_ENCODER_NAVIGATION, FEATURE_DESKTOP_AGENT_STATUS, FEATURE_DESKTOP_TASK_NAVIGATION,
    STATUS_CODEX_STATUS_AGE_MS, STATUS_DESKTOP_AGENT_PRESS, STATUS_DESKTOP_AGENT_STATUS,
    STATUS_DESKTOP_NAVIGATION,
)

SLOTS = wire.SLOTS
HEARTBEAT_S = 2.0
TICK_MS = 250
# States an app may report for a conversation. `idle` (empty slot) and
# `codex` (slot yielded to Codex) are decided by the hub, never by an app.
APP_STATES = frozenset({"unknown", "standby", "working", "completed",
                        "approval", "reply", "error"})
# Timeouts and lost links back off and resend; anything the device or the
# transport rejected by name (NACK) is a fault that needs a manual retry.
TRANSIENT_ERRORS = frozenset({"", "TRANSPORT_TIMEOUT"})
# The device refuses the source only for now (read-only or busy mode). Not a
# fault (the firmware owner 11-06): back off with the same delays, capped at the longest,
# and keep resending until it accepts again; the lights then come back alone.
DEVICE_WAIT_ERRORS = frozenset({"READ_ONLY", "BUSY"})

# Why the hub stopped (stable codes for the settings page; fault_detail keeps
# the technical text).
FAULT_REJECTED = "rejected"          # the device refused by name (NACK)
FAULT_NO_REPLY = "no_reply"          # timeouts outlasted the back-off
FAULT_INVALID_REPLY = "invalid_reply"  # an answer that does not validate
FAULT_INVALID_STATUS = "invalid_status"  # GET_STATUS readback does not validate


# --- wire format -------------------------------------------------------------
# One implementation for Console: desktop_status_wire (also used by the serial
# transport and the demo device). The hub keeps only what is its own.

def _validate_poll_ms(value) -> None:
    # Key events live 2 s; polling must stay well inside that to see them.
    if type(value) is not int or not 50 <= value <= DESKTOP_AGENT_EVENT_LIFETIME_MS // 2:
        raise ValueError("蓝牙轮询间隔必须是 50–1000 毫秒")


def codex_status_age_ms(status) -> int | None:
    """Milliseconds since Codex last sent task status, or None if never/unknown."""
    codex = status.get("codex_micro") if isinstance(status, dict) else None
    wire.validate_codex_status_age(codex)
    return codex.get(STATUS_CODEX_STATUS_AGE_MS) if isinstance(codex, dict) else None


# --- app side ----------------------------------------------------------------

@dataclass(frozen=True)
class Conversation:
    """One conversation as its app reports it. IDs stay on the host.

    `activity_at` is the time of the latest task activity (any monotonic or
    wall clock, compared only within the hub); None means the conversation
    never had a task and does not take a light by itself. `completion_turn`
    identifies the latest verified real completion (for example a turn or
    session ID); a new value is one new completion. `viewed` is the app's own
    evidence that the user has seen that completion.
    """
    conversation_id: str
    state: str = "standby"
    activity_at: float | None = None
    completion_turn: str | None = None
    viewed: bool = False


class ConversationSource(Protocol):
    app_id: str

    def conversations(self) -> Iterable[Conversation]: ...


@dataclass(frozen=True)
class OpenResult:
    """What the app shows after an open attempt. Success needs a matching ID."""
    active_id: str = ""
    error: str = ""


class Navigator(Protocol):
    def open(self, conversation_id: str, finished: Callable[[OpenResult], None]) -> None:
        """Open the conversation, verify it and call `finished` exactly once.

        `finished` may be called from any thread, synchronously or later.
        """


class FakeNavigator:
    """Navigator stand-in for tests and the demo device: no app is touched."""

    def __init__(self, *, auto=True):
        self.auto = auto
        self.requests: list[str] = []
        self.results: dict[str, OpenResult] = {}
        self._waiting: list[tuple[str, Callable[[OpenResult], None]]] = []

    def open(self, conversation_id, finished):
        self.requests.append(conversation_id)
        if self.auto:
            finished(self.results.get(conversation_id, OpenResult(active_id=conversation_id)))
        else:
            self._waiting.append((conversation_id, finished))

    def finish(self, active_id=None, error=""):
        conversation_id, finished = self._waiting.pop(0)
        finished(OpenResult(conversation_id if active_id is None else active_id, error))


# --- slot assignment ---------------------------------------------------------

SlotKey = tuple  # (app_id, conversation_id)


class SlotAllocator:
    """Most recently active conversations hold the lights (the designer 11-03 §11-1).

    Occupants keep their slot; a newcomer takes an empty slot or replaces the
    least recently active occupant. Pinned slots keep their conversation and
    `codex` slots hold none. Conversations without a slot wait in `queue`.
    `retained` are assignments restored from an earlier Console run: they keep
    their slot while their app reports them, as the oldest occupants, until
    something with real activity needs the light.
    """

    def __init__(self):
        self.slots: list[SlotKey | None] = [None] * SLOTS
        self.pins: dict[int, SlotKey] = {}
        self.codex: frozenset[int] = frozenset()
        self.queue: tuple[SlotKey, ...] = ()
        self.retained: dict[SlotKey, int] = {}  # key -> its saved slot

    def assign(self, conversations: dict[SlotKey, Conversation]) -> None:
        pins = {i: key for i, key in self.pins.items() if i not in self.codex}
        free = [i for i in range(SLOTS) if i not in self.codex and i not in pins]
        for i in range(SLOTS):
            if i in self.codex:
                self.slots[i] = None
            elif i in pins:
                self.slots[i] = pins[i]
        pinned = set(pins.values())
        eligible = {key: (c.activity_at if c.activity_at is not None else float("-inf"))
                    for key, c in conversations.items()
                    if (c.activity_at is not None or key in self.retained) and key not in pinned}
        for i in free:
            if self.slots[i] not in eligible:
                self.slots[i] = None
        for key, home in self.retained.items():
            # A restored conversation the app reports again returns to its own light.
            if key in eligible and home in free and self.slots[home] is None and key not in self.slots:
                self.slots[home] = key
        placed = {self.slots[i] for i in free}

        def recency(key):
            return (-eligible[key], key)

        for key in sorted((k for k in eligible if k not in placed), key=recency):
            target = next((i for i in free if self.slots[i] is None), None)
            if target is None and free:
                oldest = min(free, key=lambda i: (eligible[self.slots[i]], i))
                if eligible[self.slots[oldest]] < eligible[key]:
                    target = oldest
            if target is None:
                break  # Later candidates are older still.
            self.slots[target] = key
        self.queue = tuple(k for k in sorted(eligible, key=recency) if k not in self.slots)


# --- hub ---------------------------------------------------------------------

class DesktopAgentHub(QObject):
    """Owns the `desktop` source on Console's device session.

    The caller binds it to each authenticated snapshot, forwards every
    GET_STATUS result to `consume_status` and gives the split with Codex via
    `set_codex_slots`. Commands go through the gateway's `execute_command`;
    replies arrive on its `agent_status_completed/failed` signals.
    """
    FORWARD_RETRY_DELAYS_S = (0.5, 1.0, 2.0)

    changed = Signal()
    opened = Signal(str, str)  # app_id, conversation_id, after verification
    # M23: a key press or knob step did not end on its conversation:
    # app_id and the reason in product language (no IDs).
    open_failed = Signal(str, str)
    # GET_STATUS interval the bound connection needs for key events, or 0 when
    # the hub needs no extra polling (USB, or nothing bound). BLE has no push.
    status_poll_requested = Signal(int)
    _open_finished = Signal(int, int, object, object)

    def __init__(self, gateway, *, clock=time.monotonic, encoder_navigation=True,
                 ble_poll_ms=DESKTOP_AGENT_BLE_POLL_MS, use_timer=True,
                 release_when_empty=False, parent=None):
        super().__init__(parent)
        # True: send nothing (and CLEAR an own lease) while no slot holds a
        # conversation, so a Console without desktop conversations never
        # takes the lights from Codex (design decision). False keeps a lease of
        # six `idle` slots as soon as the hub is enabled.
        self.release_when_empty = release_when_empty
        _validate_poll_ms(ble_poll_ms)
        self.ble_poll_ms = ble_poll_ms
        self.connection = ""  # port_name of the one connection holding the lease
        self._gateway = gateway
        self._clock = clock
        self._sources: dict[str, tuple[ConversationSource, Navigator]] = {}
        self.allocator = SlotAllocator()
        self.conversations: dict[SlotKey, Conversation] = {}
        self._counted_turn: dict[SlotKey, str | None] = {}
        self._seen_turn: dict[SlotKey, str | None] = {}
        self._seq = [0] * SLOTS
        self.enabled = False
        self.bound = False
        self.supported = False
        self.navigation_supported = False
        self.encoder_navigation = encoder_navigation
        self.transport = ""
        self.codex_status_age_ms = None
        self.message = "未启用桌面 App 状态灯"
        self.focus_message = ""
        self.source_errors: dict[str, str] = {}
        self._normal = False
        self._paused = False
        self._fault = False
        self.fault_detail = ""
        self.fault_reason = ""
        # READ_ONLY / BUSY name while the device is waited out ("" otherwise).
        self.device_wait = ""
        self._wait_failures = 0
        self._pending = None
        self._link_down = False
        self._last_acked = None
        self._last_sent = float("-inf")
        self._lease_until = None
        self._needs_clear = False
        self._failures = 0
        self._retry_at = None
        self._press_sequence = None
        self._navigation_previous = None
        self._epoch = 0
        self._busy = False
        self._opening_slot = None
        self._pending_slot = None
        self._current_key = None
        self._open_finished.connect(self._opened)
        self._timer = QTimer(self) if use_timer else None
        if self._timer is not None:
            self._timer.setInterval(TICK_MS)
            self._timer.timeout.connect(self.tick)
        if hasattr(gateway, "agent_status_completed"):
            gateway.agent_status_completed.connect(self._completed)
            gateway.agent_status_failed.connect(self._failed)

    # app registration and the slot plan

    def register(self, source: ConversationSource, navigator: Navigator) -> None:
        self._sources[source.app_id] = (source, navigator)
        self.refresh()

    def unregister(self, app_id: str) -> None:
        self._sources.pop(app_id, None)
        self.source_errors.pop(app_id, None)
        self.refresh()

    def set_codex_slots(self, slots: Iterable[int]) -> None:
        """Yield these zero-based slots to Codex; the split is the caller's."""
        slots = frozenset(slots)
        if not slots <= set(range(SLOTS)):
            raise ValueError("Codex 槽位必须是 0–5")
        self.allocator.codex = slots
        self.refresh()

    def pin(self, slot: int, app_id: str, conversation_id: str) -> None:
        if type(slot) is not int or not 0 <= slot < SLOTS:
            raise ValueError("槽位必须是 0–5")
        key = (app_id, conversation_id)
        self.allocator.pins = {i: k for i, k in self.allocator.pins.items() if k != key}
        self.allocator.pins[slot] = key
        self.refresh()

    def unpin(self, slot: int) -> None:
        self.allocator.pins.pop(slot, None)
        self.refresh()

    def assignment(self, app_id: str) -> tuple[tuple[str | None, ...], dict[int, str]]:
        """This app's lights: conversation per slot (None: not this app's) and pins."""
        slots = tuple(key[1] if key is not None and key[0] == app_id else None
                      for key in self.allocator.slots)
        pins = {i: key[1] for i, key in self.allocator.pins.items() if key[0] == app_id}
        return slots, pins

    def restore(self, app_id: str, slots, pins) -> None:
        """Replace this app's saved lights (earlier run, same device and account).

        Pins are restored as pins; the other saved slots are kept for their
        conversations while the app reports them (`SlotAllocator.retained`).
        Another app's or a pin's slot is never taken over.
        """
        slots = tuple(slots)
        if len(slots) != SLOTS:
            raise ValueError("保存的槽位必须是六个")
        allocator = self.allocator
        allocator.pins = {i: k for i, k in allocator.pins.items() if k[0] != app_id}
        for slot, conversation_id in pins.items():
            if type(slot) is int and 0 <= slot < SLOTS and slot not in allocator.pins:
                allocator.pins[slot] = (app_id, conversation_id)
        retained = {k: i for k, i in allocator.retained.items() if k[0] != app_id}
        pinned = set(allocator.pins.values())
        for i, conversation_id in enumerate(slots):
            current = allocator.slots[i]
            if current is not None and current[0] == app_id:
                allocator.slots[i] = None
            key = (app_id, conversation_id) if conversation_id else None
            if (key is not None and key not in pinned and key not in retained
                    and allocator.slots[i] is None and i not in allocator.pins):
                retained[key] = i
        allocator.retained = retained
        self.refresh()

    # lifecycle

    def enable(self) -> None:
        self.enabled = True
        self._fault = False
        self._last_acked = None
        if self._timer is not None:
            self._timer.start()
        self._request_poll()
        self.refresh()

    def disable(self) -> None:
        self._send_clear_now()
        self.enabled = False
        if self._timer is not None:
            self._timer.stop()
        self._reset_link()
        self._request_poll()
        self.message = "未启用桌面 App 状态灯"
        self.changed.emit()

    def shutdown(self) -> None:
        """Console exits normally: release the lights at once."""
        self.disable()

    def bind(self, snapshot) -> None:
        """Called after each authenticated handshake (USB or BLE).

        The hub serves exactly this connection: SET, heartbeats and key reads
        stay on it. Any other connection is a new one and needs its own bind.
        """
        self._reset_link()
        features = snapshot.capabilities.get("features", {})
        status = snapshot.status
        self.bound = True
        self.connection = snapshot.port_name
        self.supported = features.get(FEATURE_DESKTOP_AGENT_STATUS) is True
        self.navigation_supported = (self.supported
                                     and features.get(FEATURE_DESKTOP_TASK_NAVIGATION) is True)
        self.transport = "ble" if snapshot.connection_kind == "bluetooth" else "usb"
        self._normal = status.get("operating_mode") == "normal"
        if self.supported:
            try:
                readback = status.get(STATUS_DESKTOP_AGENT_STATUS)
                if readback is not None:
                    validate_readback(readback)
                    if readback["active"]:
                        # A lease from before this session is still live: keep
                        # counting from its counters so completions still pulse.
                        self._seq = [max(a, b) for a, b in zip(self._seq, readback["completion_seq"])]
                press = status.get(STATUS_DESKTOP_AGENT_PRESS)
                if press is not None:
                    validate_press(press)
                    self._press_sequence = press["sequence"]
                navigation = status.get(STATUS_DESKTOP_NAVIGATION)
                if self.navigation_supported and navigation is not None:
                    validate_navigation(navigation)
                    self._navigation_previous = dict(navigation)
                self.codex_status_age_ms = codex_status_age_ms(status)
            except ValueError as exc:
                self._enter_fault(str(exc), FAULT_INVALID_STATUS)
                return
        self._request_poll()
        self.refresh()

    def unbind(self) -> None:
        """The link is gone; the device lease runs out on its own."""
        self._reset_link()
        self.bound = False
        self.supported = False
        self.navigation_supported = False
        self.connection = ""
        self.transport = ""
        self._request_poll()
        self._update_message()
        self.changed.emit()

    def connection_changed(self, port_name: str) -> None:
        """The gateway now talks over `port_name` (USB plugged in suspends BLE;
        unplugging returns to BLE). Anything but the bound connection is new:
        stop, forget its key sequences and wait for the next bind after HELLO."""
        if self.bound and port_name != self.connection:
            self.unbind()
            self.message = "连接已切换，等待新连接通过身份检查"
            self.changed.emit()

    def set_ble_poll_ms(self, value: int) -> None:
        _validate_poll_ms(value)
        self.ble_poll_ms = value
        self._request_poll()

    @property
    def status_poll_ms(self) -> int:
        """Polling the bound connection needs for key events (0: none extra)."""
        return (self.ble_poll_ms if self.enabled and self.bound and self.supported
                and self.transport == "ble" else 0)

    def _request_poll(self) -> None:
        self.status_poll_requested.emit(self.status_poll_ms)

    def set_paused(self, paused: bool) -> None:
        """Firmware maintenance owns the device while paused."""
        if paused == self._paused:
            return
        if paused:
            self._send_clear_now()
            self._reset_link()
        self._paused = paused
        self.refresh()

    def retry(self) -> None:
        self._fault = False
        self.fault_detail = ""
        self.fault_reason = ""
        self.device_wait = ""
        self._wait_failures = 0
        self._failures = 0
        self._retry_at = None
        self._last_acked = None
        self.refresh()

    # observation

    def tick(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        """Poll the apps, reassign the lights and send what changed."""
        previous = self.conversations
        current: dict[SlotKey, Conversation] = {}
        for app_id, (source, _navigator) in self._sources.items():
            try:
                reported = {}
                for conversation in source.conversations():
                    if (not isinstance(conversation.conversation_id, str)
                            or not conversation.conversation_id
                            or conversation.state not in APP_STATES):
                        raise ValueError(f"{app_id} 报告的对话无效")
                    reported[(app_id, conversation.conversation_id)] = conversation
                self.source_errors.pop(app_id, None)
            except Exception as exc:
                # Unobservable is `unknown`, never a guess.
                self.source_errors[app_id] = str(exc)
                reported = {key: replace(c, state="unknown") for key, c in previous.items()
                            if key[0] == app_id}
            current.update(reported)
        self.conversations = current
        self.allocator.assign(current)
        self._count_completions()
        self._flush()
        self._update_message()
        self.changed.emit()

    def _count_completions(self) -> None:
        for key, conversation in self.conversations.items():
            turn = conversation.completion_turn
            if key not in self._counted_turn:
                self._counted_turn[key] = turn  # First sighting is a baseline.
                continue
            if turn is None or turn == self._counted_turn[key]:
                continue
            self._counted_turn[key] = turn
            if key in self.allocator.slots:
                self._bump(self.allocator.slots.index(key))

    def _bump(self, slot: int) -> None:
        if self._seq[slot] == UINT32_MAX:
            # protocol.md: CLEAR and start a new silent baseline before reuse.
            self._seq = [0] * SLOTS
            self._needs_clear = True
        else:
            self._seq[slot] += 1

    def mark_viewed(self, app_id: str, conversation_id: str) -> None:
        """The user has seen this conversation's latest completion."""
        key = (app_id, conversation_id)
        conversation = self.conversations.get(key)
        self._seen_turn[key] = conversation.completion_turn if conversation else None
        self.refresh()

    # wire snapshot

    def wire(self) -> tuple[tuple[str, ...], tuple[int, ...]]:
        states = []
        for i, key in enumerate(self.allocator.slots):
            if i in self.allocator.codex:
                states.append("codex")
            elif key is None:
                states.append("idle")
            elif key not in self.conversations:
                states.append("unknown")  # Pinned, but its app is not reporting it.
            else:
                conversation = self.conversations[key]
                state = conversation.state
                if state == "completed" and (
                        conversation.viewed
                        or self._seen_turn.get(key, ()) == conversation.completion_turn):
                    state = "standby"
                states.append(state)
        return tuple(states), tuple(self._seq)

    def slot_view(self) -> list[dict]:
        """Per-slot table for settings pages and diagnostics."""
        states, seq = self.wire()
        return [{"slot": i, "key": DESKTOP_AGENT_SLOT_KEYS[i], "state": states[i],
                 "completion_seq": seq[i], "pinned": i in self.allocator.pins,
                 "app_id": key[0] if key else "", "conversation_id": key[1] if key else ""}
                for i, key in enumerate(self.allocator.slots)]

    @property
    def lease_active(self) -> bool:
        return self._lease_until is not None and self._clock() < self._lease_until

    @property
    def fault(self) -> bool:
        return self._fault

    def _navigation_opt_in(self) -> bool:
        return self.navigation_supported and self.encoder_navigation

    def _can_send(self) -> bool:
        return bool(self.enabled and self.bound and self.supported and self._normal
                    and not self._paused and not self._fault and not self._link_down)

    # sending

    def _flush(self) -> None:
        if not self._can_send() or self._pending is not None:
            return
        now = self._clock()
        if self._retry_at is not None:
            if now < self._retry_at:
                return
            self._retry_at = None
        wire = self.wire() + (self._navigation_opt_in(),)
        if self._needs_clear:
            command = clear_status_command()
        elif self.release_when_empty and not any(s not in ("idle", "codex") for s in wire[0]):
            # design decision: no conversation on any light -> hold no lease and
            # leave all six lights to Codex. A lease this hub holds is cleared.
            if self._last_acked is None and self._lease_until is None:
                return
            command = clear_status_command()
        else:
            if wire == self._last_acked and now - self._last_sent < HEARTBEAT_S:
                return
            command = status_command(*wire[:2], encoder_navigation=wire[2])
        self._pending = command
        self._last_sent = now
        self._gateway.execute_command(command)

    def _completed(self, command, payload) -> None:
        if command is not self._pending:
            return
        try:
            lease_ms = validate_response(command, payload)
        except ValueError as exc:
            self._failed(command, exc)
            return
        self._pending = None
        self._failures = 0
        self._wait_failures = 0
        self.device_wait = ""
        if command.name == CLEAR_AGENT_STATUS:
            self._needs_clear = False
            self._last_acked = None
            self._lease_until = None
        else:
            payload = command.payload
            self._last_acked = (tuple(payload["states"]), tuple(payload["completion_seq"]),
                                payload.get(DESKTOP_ENCODER_NAVIGATION, False))
            self._lease_until = self._clock() + lease_ms / 1000
        self._flush()
        self._update_message()
        self.changed.emit()

    def _failed(self, command, error) -> None:
        if command is not self._pending:
            return
        self._pending = None
        name = getattr(error, "error_name", "")
        if name == COMMAND_DROPPED:
            # Sent nowhere: the link closed and the next bind starts afresh.
            self._last_acked = None
            self._link_down = True
            return
        detail = str(getattr(error, "technical", "") or error)
        if not isinstance(error, ValueError) and name in DEVICE_WAIT_ERRORS:
            delays = self.FORWARD_RETRY_DELAYS_S
            self._retry_at = self._clock() + delays[min(self._wait_failures, len(delays) - 1)]
            self._wait_failures += 1
            self._last_acked = None  # Not applied: resend the whole snapshot.
            self.device_wait = name
            self.message = "设备暂时不接受桌面 App 状态（只读或忙），正在自动重试"
            self.changed.emit()
            return
        if isinstance(error, ValueError):
            self._enter_fault(detail, FAULT_INVALID_REPLY)
            return
        if name not in TRANSIENT_ERRORS:
            self._enter_fault(detail, FAULT_REJECTED)
            return
        if self._failures >= len(self.FORWARD_RETRY_DELAYS_S):
            self._enter_fault(detail, FAULT_NO_REPLY)
            return
        self._retry_at = self._clock() + self.FORWARD_RETRY_DELAYS_S[self._failures]
        self._failures += 1
        self._last_acked = None  # The lost command's effect is unknown: resend.
        self.message = (f"桌面 App 状态发送失败，正在自动重试"
                        f"（{self._failures}/{len(self.FORWARD_RETRY_DELAYS_S)}）")
        self.changed.emit()

    def _enter_fault(self, detail: str, reason: str = FAULT_REJECTED) -> None:
        self._fault = True
        self.fault_detail = detail
        self.fault_reason = reason
        self.device_wait = ""
        self._wait_failures = 0
        self._retry_at = None
        self._failures = 0
        self._cancel_navigation()
        self.message = ("桌面 App 状态灯已停止：设备拒绝或无法确认状态。"
                        "租约到期后灯会恢复 Codex；请检查连接后点击“重试”。")
        self.changed.emit()

    def _send_clear_now(self) -> None:
        if self.enabled and self.bound and self.supported:
            self._gateway.execute_command(clear_status_command())

    def _reset_link(self) -> None:
        self._cancel_navigation()
        self._pending = None
        self._link_down = False
        self._last_acked = None
        self._last_sent = float("-inf")
        self._lease_until = None
        self._retry_at = None
        self._failures = 0
        self._press_sequence = None
        self._navigation_previous = None
        self._fault = False
        self.fault_detail = ""
        self.fault_reason = ""
        self.device_wait = ""
        self._wait_failures = 0

    def _update_message(self) -> None:
        if self._fault:
            return
        if not self.enabled:
            self.message = "未启用桌面 App 状态灯"
        elif not self.bound:
            self.message = "等待设备连接并通过身份检查"
        elif not self.supported:
            self.message = "当前固件不支持桌面 App 状态灯，请升级固件"
        elif not self._normal:
            self.message = "设备不在普通模式，桌面 App 状态灯暂停"
        elif self._paused:
            self.message = "设备维护期间暂停桌面 App 状态灯"
        elif self._retry_at is not None:
            return
        elif not self._sources:
            self.message = "桌面 App 状态灯已启用，等待 App 接入"
        else:
            used = sum(1 for key in self.allocator.slots if key is not None)
            self.message = f"桌面 App 状态灯已启用 · 占用 {used} 颗灯 · 排队 {len(self.allocator.queue)}"

    # device status: mode, lease readback, keys and encoder

    def consume_status(self, status: dict, *, port_name: str | None = None) -> None:
        """Feed every GET_STATUS result; `port_name` names the connection it
        was read on. A read from another connection is a connection change."""
        if port_name is not None and port_name != self.connection:
            self.connection_changed(port_name)
            return
        if not self.bound or not self.supported:
            return
        normal = status.get("operating_mode") == "normal"
        if normal != self._normal:
            self._normal = normal
            self._last_acked = None  # Re-entering NORMAL starts with a full snapshot.
            if not normal:
                self._cancel_navigation()
        try:
            readback = status.get(STATUS_DESKTOP_AGENT_STATUS)
            if readback is not None:
                validate_readback(readback)
            press = status.get(STATUS_DESKTOP_AGENT_PRESS)
            if press is not None:
                validate_press(press)
            navigation = status.get(STATUS_DESKTOP_NAVIGATION) if self.navigation_supported else None
            if navigation is not None:
                validate_navigation(navigation)
            self.codex_status_age_ms = codex_status_age_ms(status)
        except ValueError as exc:
            self._enter_fault(str(exc), FAULT_INVALID_STATUS)
            return
        if (readback is not None and not readback["active"] and self._pending is None
                and self._last_acked is not None):
            self._last_acked = None  # The device lost the lease (reboot, expiry).
        live = bool(readback and readback["active"] and readback["selected"]) and self._can_send()
        by_encoder = bool(readback and readback.get(DESKTOP_ENCODER_NAVIGATION) is True
                          and self.navigation_supported)
        if press is not None:
            previous, self._press_sequence = self._press_sequence, press["sequence"]
            if previous is not None and press["sequence"] != previous and not by_encoder:
                self._on_press(press, live)
        if navigation is not None:
            previous, self._navigation_previous = self._navigation_previous, dict(navigation)
            if (previous is not None and navigation["sequence"] != previous["sequence"]
                    and by_encoder):
                self._on_navigation(previous, navigation, live)
        self._flush()
        self.changed.emit()

    def _fresh(self, event, live) -> bool:
        if event["agent"] is None and event["transport"] is None:
            self.focus_message = "按键通知已过期或来自另一条连接，已忽略"
            return False
        if event["transport"] != self.transport:
            self.focus_message = "按键来自另一条连接，已忽略"
            return False
        return live

    def _on_press(self, event, live) -> None:
        if self._fresh(event, live):
            self._request_slot(event["agent"])

    def _on_navigation(self, previous, event, live) -> None:
        if not self._fresh(event, live):
            return
        if event["anchor_sequence"] != previous["anchor_sequence"] and event["agent"] is not None:
            # protocol.md: a new anchor selects (agent + offset) modulo 6.
            base, steps = event["agent"], event["offset"]
            if steps == 0:
                self._request_slot(base)
                return
        else:
            base = self._target_slot()
            steps = (event["offset"] - previous["offset"]) % SLOTS
            if steps == 0:
                return
            if base is None:
                self.focus_message = "请先按一颗灯打开对话，再用旋钮切换"
                return
        target = self._step(base, steps)
        if target is not None:
            self._request_slot(target)

    def _openable(self, slot: int) -> bool:
        key = self.allocator.slots[slot]
        return (slot not in self.allocator.codex and key is not None
                and key in self.conversations and key[0] in self._sources)

    def _step(self, base: int, steps: int) -> int | None:
        """Move `steps` (mod 6; 4 and 5 turn back) over slots with a conversation."""
        delta = steps if steps <= SLOTS // 2 else steps - SLOTS
        if not any(self._openable(i) for i in range(SLOTS)):
            self.focus_message = "没有可切换的对话"
            return None
        direction = 1 if delta > 0 else -1
        slot = base
        for _ in range(abs(delta)):
            for _ in range(SLOTS):
                slot = (slot + direction) % SLOTS
                if self._openable(slot):
                    break
        return slot

    def _target_slot(self) -> int | None:
        """Where relative encoder steps start: the latest requested slot."""
        for slot in (self._pending_slot, self._opening_slot):
            if slot is not None:
                return slot
        if self._current_key is not None and self._current_key in self.allocator.slots:
            return self.allocator.slots.index(self._current_key)
        return None

    def _request_slot(self, slot: int) -> None:
        if not self._openable(slot):
            self.focus_message = "这颗灯上没有可打开的对话"
            return
        if self._busy:
            self._pending_slot = slot  # The latest input wins.
            self.focus_message = "正在切换对话"
            return
        self._open(slot)

    def _open(self, slot: int) -> None:
        key = self.allocator.slots[slot]
        _source, navigator = self._sources[key[0]]
        self._busy = True
        self._opening_slot = slot
        epoch = self._epoch
        self.focus_message = "正在打开对话"

        def finished(result, epoch=epoch, slot=slot, key=key):
            self._open_finished.emit(epoch, slot, key, result)

        try:
            navigator.open(key[1], finished)
        except Exception as exc:
            finished(OpenResult(error=str(exc)))

    def _opened(self, epoch, slot, key, result) -> None:
        if epoch != self._epoch:
            return
        self._busy = False
        self._opening_slot = None
        if not isinstance(result, OpenResult):
            result = OpenResult(error="打开对话的结果无效")
        if result.error:
            self.focus_message = result.error
            self.open_failed.emit(key[0], result.error)
        elif result.active_id != key[1]:
            self.focus_message = "打开后核对不一致：当前显示的不是这颗灯的对话"
            self.open_failed.emit(key[0], self.focus_message)
        else:
            self._current_key = key
            conversation = self.conversations.get(key)
            self._seen_turn[key] = conversation.completion_turn if conversation else None
            self.focus_message = "已打开并核对对话"
            self.opened.emit(key[0], key[1])
        pending, self._pending_slot = self._pending_slot, None
        if pending is not None and self._can_send():
            self._request_slot(pending)
        self.refresh()

    def _cancel_navigation(self) -> None:
        self._epoch += 1
        self._busy = False
        self._opening_slot = None
        self._pending_slot = None
