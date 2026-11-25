"""DesktopAgentHub against a fake `desktop`-source device. No app, UI or I/O."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from controller_config import desktop_agent_hub as hub_module
from controller_config.desktop_agent_hub import (
    UINT32_MAX, Conversation, DesktopAgentHub, FakeNavigator, OpenResult,
    validate_clear_request, validate_set_request,
)
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind

C = Conversation


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeDevice(QObject):
    """The firmware side of protocol.md's `desktop` source, as far as Console sees it."""
    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)

    def __init__(self, clock, *, supported=True, navigation=True):
        super().__init__()
        self.clock = clock
        self.features = {}
        if supported:
            self.features["desktop_agent_status"] = True
        if navigation:
            self.features["desktop_task_navigation"] = True
        self.mode = "normal"
        self.commands = []
        self.script = []
        self.held = []
        self.lease_until = None
        self.lease_transport = None
        self.states = ["unknown"] * 6
        self.seq = [0] * 6
        self.navigation_opt_in = False
        self.pulses = []
        self.press = {"source": "desktop", "sequence": 0, "agent": None, "transport": None}
        self.nav = {"source": "desktop", "sequence": 0, "anchor_sequence": 0, "offset": 0,
                    "agent": None, "transport": None}
        self.input_at = None
        self.input_transport = None
        self.codex_age = None
        self.link = "usb"  # The connection the gateway currently talks over.

    def live(self):
        return self.lease_until is not None and self.clock() < self.lease_until

    def sets(self):
        return [c for c in self.commands if c.name == "SET_AGENT_STATUS"]

    def last(self):
        return self.commands[-1].payload

    def execute_command(self, command):
        self.commands.append(command)
        reply = self.script.pop(0) if self.script else "ack"
        if reply == "hold":
            self.held.append(command)
        else:
            self.reply(command, reply)

    def reply(self, command, reply="ack", transport=None):
        transport = transport or self.link
        if reply == "timeout":
            self.agent_status_failed.emit(command, BootstrapError(
                BootstrapKind.READ_FAILED, "设备命令响应超时", "timeout", error_name="TRANSPORT_TIMEOUT"))
            return
        if reply in {"READ_ONLY", "BUSY"}:  # the device refuses for now (mode)
            self.agent_status_failed.emit(command, BootstrapError(
                BootstrapKind.READ_FAILED, "设备命令执行失败", f"{reply}: mode", error_name=reply))
            return
        if reply == "drop":
            self.agent_status_failed.emit(command, BootstrapError.command_dropped(command.name, "closed"))
            return
        if reply == "bad":
            self.agent_status_completed.emit(command, {"command": command.name, "result": {
                "source": "desktop", "active": "yes", "lease_ms": 5000}})
            return
        try:
            if command.name == "SET_AGENT_STATUS":
                validate_set_request(command.payload)
                if self.mode != "normal":
                    raise LookupError("READ_ONLY")
            else:
                validate_clear_request(command.payload)
        except (ValueError, LookupError) as exc:
            name = "READ_ONLY" if isinstance(exc, LookupError) else "VALIDATION_FAILED"
            self.agent_status_failed.emit(command, BootstrapError(
                BootstrapKind.READ_FAILED, "设备命令执行失败", f"{name}: {exc}", error_name=name))
            return
        if reply == "nack":
            self.agent_status_failed.emit(command, BootstrapError(
                BootstrapKind.READ_FAILED, "设备命令执行失败", "VALIDATION_FAILED: x",
                error_name="VALIDATION_FAILED"))
            return
        if command.name == "SET_AGENT_STATUS":
            states, seq = command.payload["states"], command.payload["completion_seq"]
            if self.live():  # The first snapshot of a lease is a silent baseline.
                for i in range(6):
                    if states[i] == "completed" and seq[i] > self.seq[i]:
                        self.pulses.append(i)
            self.states, self.seq = list(states), list(seq)
            self.navigation_opt_in = command.payload.get("encoder_navigation", False)
            self.lease_until = self.clock() + 5
            self.lease_transport = transport
        else:
            self.lease_until = None
            self.states = ["unknown"] * 6
        self.agent_status_completed.emit(command, {"command": command.name, "result": {
            "source": "desktop", "active": command.name == "SET_AGENT_STATUS", "lease_ms": 5000}})

    def key_down(self, agent, transport="usb"):
        self.press = dict(self.press, sequence=self.press["sequence"] + 1, agent=agent,
                          transport=transport)
        self.nav = dict(self.nav, sequence=self.nav["sequence"] + 1, offset=0, agent=agent,
                        transport=transport)
        self.nav["anchor_sequence"] = self.nav["sequence"]
        self.input_at, self.input_transport = self.clock(), transport

    def turn(self, steps, transport="usb"):
        for _ in range(abs(steps)):
            self.nav = dict(self.nav, sequence=self.nav["sequence"] + 1,
                            offset=(self.nav["offset"] + (1 if steps > 0 else -1)) % 6,
                            transport=transport)
        self.input_at, self.input_transport = self.clock(), transport

    def status(self, transport="usb"):
        live = self.live()
        fresh = (live and self.input_at is not None and self.clock() - self.input_at < 2
                 and transport == self.lease_transport == self.input_transport)
        press, nav = dict(self.press), dict(self.nav)
        if not fresh:
            press.update(agent=None, transport=None)
            nav.update(agent=None, transport=None)
        return {
            "operating_mode": self.mode,
            "desktop_agent_status": {
                "source": "desktop", "active": live, "selected": live and self.mode == "normal",
                "lease_ms": 5000, "states": list(self.states) if live else ["unknown"] * 6,
                "completion_seq": list(self.seq), "encoder_navigation": live and self.navigation_opt_in},
            "desktop_agent_press": press,
            "desktop_navigation": nav,
            "codex_micro": {"status_age_ms": self.codex_age},
        }

    def snapshot(self, transport="usb"):
        self.link = transport
        return SimpleNamespace(port_name=PORTS[transport],
                               capabilities={"features": dict(self.features)},
                               status=self.status(transport),
                               connection_kind="bluetooth" if transport == "ble" else "usb")


PORTS = {"usb": "/dev/cu.usbmodem101", "ble": "ble:E072A1D68DE8"}


class ListSource:
    def __init__(self, app_id, *conversations):
        self.app_id = app_id
        self.items = {c.conversation_id: c for c in conversations}
        self.error = None

    def conversations(self):
        if self.error:
            raise RuntimeError(self.error)
        return list(self.items.values())

    def put(self, conversation):
        self.items[conversation.conversation_id] = conversation


@pytest.fixture
def rig(qapp):
    def make(*conversations, supported=True, navigation=True, encoder_navigation=True,
             transport="usb", bind=True):
        clock = Clock()
        device = FakeDevice(clock, supported=supported, navigation=navigation)
        hub = DesktopAgentHub(device, clock=clock, encoder_navigation=encoder_navigation,
                              use_timer=False)
        source, navigator = ListSource("app-a", *conversations), FakeNavigator()
        hub.register(source, navigator)
        hub.enable()
        if bind:
            hub.bind(device.snapshot(transport))
        return SimpleNamespace(clock=clock, device=device, hub=hub, source=source, nav=navigator)
    return make


def busy(cid, at, state="working", **fields):
    return C(cid, state=state, activity_at=at, **fields)


def poll(r, transport="usb"):
    r.hub.consume_status(r.device.status(transport))


# --- capability gating and snapshots ---------------------------------------

def test_missing_capability_sends_no_desktop_command(rig):
    r = rig(busy("a", 1), supported=False)
    r.device.key_down(0)
    poll(r)
    r.clock.advance(10)
    r.hub.tick()
    r.hub.shutdown()
    assert r.device.commands == []
    assert "不支持" in rig(supported=False).hub.message


def test_encoder_navigation_only_with_its_capability(rig):
    assert "encoder_navigation" not in rig(busy("a", 1), navigation=False).device.last()
    assert rig(busy("a", 1)).device.last()["encoder_navigation"] is True
    assert "encoder_navigation" not in rig(busy("a", 1), encoder_navigation=False).device.last()


def test_full_snapshot_first_after_start_and_reconnect(rig):
    r = rig(busy("a", 1), busy("b", 2, "completed", completion_turn="t1"), bind=False)
    assert r.device.commands == []  # Nothing before an authenticated bind.
    r.hub.bind(r.device.snapshot())
    first = r.device.last()
    assert first["states"] == ["completed", "working", "idle", "idle", "idle", "idle"]
    assert first["completion_seq"] == [0] * 6 and r.device.pulses == []
    r.hub.unbind()
    sent = len(r.device.commands)
    r.hub.bind(r.device.snapshot())
    assert len(r.device.commands) == sent + 1 and r.device.last() == first


def test_heartbeat_at_most_every_two_seconds(rig):
    r = rig(busy("a", 1))
    assert len(r.device.sets()) == 1
    r.clock.advance(1.9)
    r.hub.tick()
    poll(r)
    assert len(r.device.sets()) == 1
    r.clock.advance(0.1)
    r.hub.tick()
    sets = r.device.sets()
    assert len(sets) == 2 and sets[1].payload == sets[0].payload and sets[1] is not sets[0]
    for _ in range(10):  # Ticks every 250 ms keep the 5 s lease alive.
        r.clock.advance(0.25)
        r.hub.tick()
        assert r.device.live() and r.hub.lease_active
    assert len(r.device.sets()) == 3


def test_state_change_is_sent_immediately(rig):
    r = rig(busy("a", 1))
    r.source.put(busy("a", 2, "approval"))
    r.hub.refresh()
    assert r.device.last()["states"][0] == "approval" and len(r.device.sets()) == 2


def test_lease_follows_acks_and_device_readback(rig):
    r = rig(busy("a", 1))
    assert r.hub.lease_active
    r.device.script = ["hold"]
    r.clock.advance(2)
    r.hub.tick()
    r.clock.advance(3.1)  # Unanswered: the device's 5 s lease runs out.
    assert not r.hub.lease_active and not r.device.live()
    r.device.reply(r.device.held.pop())
    assert r.hub.lease_active
    r.device.lease_until = None  # Device reboot or expiry seen through GET_STATUS.
    sent = len(r.device.sets())
    poll(r)
    assert len(r.device.sets()) == sent + 1 and r.device.live()


def test_timeouts_back_off_then_fault(rig):
    r = rig(busy("a", 1))
    r.device.script = ["timeout"] * 4
    r.clock.advance(2)
    r.hub.tick()
    for delay in (0.5, 1.0, 2.0):
        sent = len(r.device.commands)
        r.clock.advance(delay - 0.01)
        r.hub.tick()
        assert len(r.device.commands) == sent and not r.hub.fault
        assert "自动重试" in r.hub.message
        r.clock.advance(0.01)
        r.hub.tick()
        assert len(r.device.commands) == sent + 1
    assert r.hub.fault and "重试" in r.hub.message
    sent = len(r.device.commands)
    r.clock.advance(10)
    r.hub.tick()
    assert len(r.device.commands) == sent
    r.hub.retry()
    assert len(r.device.commands) == sent + 1 and not r.hub.fault and r.hub.lease_active


def test_one_timeout_recovers_and_resets_backoff(rig):
    r = rig(busy("a", 1))
    r.device.script = ["timeout"]
    r.source.put(busy("a", 2, "reply"))
    r.hub.refresh()
    r.clock.advance(0.5)
    r.hub.tick()
    assert r.device.last()["states"][0] == "reply" and r.hub.lease_active
    assert r.hub._failures == 0 and "已启用" in r.hub.message


@pytest.mark.parametrize("name", ["READ_ONLY", "BUSY"])
def test_read_only_or_busy_backs_off_without_fault(rig, name):
    """the firmware owner 11-06: a device that refuses for now is waited out, never a fault."""
    r = rig(busy("a", 1))
    r.device.script = [name] * 6
    r.clock.advance(2)
    r.hub.tick()  # heartbeat refused
    assert not r.hub.fault and r.hub.device_wait == name and "自动重试" in r.hub.message
    for delay in (0.5, 1.0, 2.0, 2.0, 2.0):  # same delays, capped at the longest
        sent = len(r.device.commands)
        r.clock.advance(delay - 0.01)
        r.hub.tick()
        assert len(r.device.commands) == sent
        r.clock.advance(0.01)
        r.hub.tick()
        assert len(r.device.commands) == sent + 1
        assert not r.hub.fault and r.hub.fault_reason == ""
    r.clock.advance(2.0)
    r.hub.tick()  # the device accepts again
    assert r.device.live() and r.hub.lease_active and r.hub.device_wait == ""
    assert not r.hub.fault and "已启用" in r.hub.message


def test_lights_come_back_after_read_only_and_busy_spells(rig):
    """Switching modes (into read-only and out, busy and done) needs no manual retry."""
    r = rig(busy("a", 1))
    for spell in ("READ_ONLY", "BUSY"):
        r.device.script = [spell] * 3
        r.source.put(busy("a", r.clock.now, "completed", completion_turn=spell))
        r.hub.refresh()
        assert r.hub.device_wait == spell and not r.hub.fault
        for _ in range(40):  # 10 s of 250 ms ticks
            r.clock.advance(0.25)
            r.hub.tick()
        assert r.hub.device_wait == "" and not r.hub.fault
        assert r.device.live() and r.device.states[0] == "completed"
    # A timeout right after a long wait still gets its own full back-off.
    r.device.script = ["BUSY"] * 4 + ["timeout"]
    r.source.put(busy("a", r.clock.now, "working"))
    r.hub.refresh()
    for _ in range(60):
        r.clock.advance(0.25)
        r.hub.tick()
    assert not r.hub.fault and r.device.live() and r.device.states[0] == "working"


def test_fault_reason_codes(rig):
    r = rig(busy("a", 1))
    r.device.script = ["nack"]
    r.source.put(busy("a", 2, "error"))
    r.hub.refresh()
    assert r.hub.fault_reason == hub_module.FAULT_REJECTED and "VALIDATION_FAILED" in r.hub.fault_detail
    r.hub.retry()
    assert r.hub.fault_reason == "" and r.hub.fault_detail == "" and not r.hub.fault
    r.device.script = ["bad"]
    r.source.put(busy("a", 3, "working"))
    r.hub.refresh()
    assert r.hub.fault_reason == hub_module.FAULT_INVALID_REPLY
    r.hub.retry()
    r.device.script = ["timeout"] * 4
    r.clock.advance(2)
    r.hub.tick()
    for delay in (0.5, 1.0, 2.0):
        r.clock.advance(delay)
        r.hub.tick()
    assert r.hub.fault and r.hub.fault_reason == hub_module.FAULT_NO_REPLY


@pytest.mark.parametrize("reply", ["nack", "bad"])
def test_nack_or_malformed_ack_faults_at_once(rig, reply):
    r = rig(busy("a", 1))
    r.device.script = [reply]
    r.source.put(busy("a", 2, "error"))
    r.hub.refresh()
    assert r.hub.fault and r.hub.fault_detail
    sent = len(r.device.commands)
    r.clock.advance(5)
    r.hub.tick()
    assert len(r.device.commands) == sent
    r.hub.retry()
    assert r.device.last()["states"][0] == "error" and not r.hub.fault


def test_dropped_command_waits_for_rebind(rig):
    r = rig(busy("a", 1))
    r.device.script = ["drop"]
    r.source.put(busy("a", 2, "reply"))
    r.hub.refresh()
    assert not r.hub.fault
    sent = len(r.device.commands)
    r.clock.advance(5)
    r.hub.tick()
    r.source.put(busy("a", 3, "error"))
    r.hub.refresh()
    assert len(r.device.commands) == sent  # No retries into a closed link.
    r.hub.unbind()
    r.hub.bind(r.device.snapshot())
    assert r.device.last()["states"][0] == "error"


def test_leaving_normal_stops_and_return_resends(rig):
    r = rig(busy("a", 1))
    r.device.mode = "codex"
    poll(r)
    sent = len(r.device.commands)
    r.clock.advance(3)
    r.hub.tick()
    assert len(r.device.commands) == sent and not r.hub.fault and "普通模式" in r.hub.message
    r.device.mode = "normal"
    poll(r)
    assert len(r.device.commands) == sent + 1 and r.device.live()


def test_shutdown_and_disable_send_clear(rig):
    r = rig(busy("a", 1))
    r.hub.shutdown()
    assert r.device.commands[-1].name == "CLEAR_AGENT_STATUS" and not r.device.live()
    r.hub.enable()
    r.hub.bind(r.device.snapshot())
    r.hub.disable()
    assert r.device.commands[-1].payload == {"source": "desktop"}
    sent = len(r.device.commands)
    r.hub.refresh()
    assert len(r.device.commands) == sent


def test_live_lease_counters_are_adopted_on_reconnect(rig):
    r = rig(busy("a", 1, "completed", completion_turn="t1"))
    r.device.seq = [5, 0, 0, 0, 0, 0]  # A lease left by an earlier Console.
    r.hub.unbind()
    r.hub.bind(r.device.snapshot())
    assert r.device.last()["completion_seq"][0] == 5
    r.source.put(busy("a", 2, "completed", completion_turn="t2"))
    r.hub.refresh()
    assert r.device.last()["completion_seq"][0] == 6 and r.device.pulses == [0]


def test_codex_status_age_is_reported_for_the_caller(rig):
    r = rig(busy("a", 1))
    r.device.codex_age = 1200
    poll(r)
    assert r.hub.codex_status_age_ms == 1200


# --- slot assignment ----------------------------------------------------------

def test_recent_conversations_take_lights_and_the_rest_queue(rig):
    r = rig(*(busy(f"c{i}", i) for i in range(8)), C("quiet"))
    view = r.hub.slot_view()
    assert [v["conversation_id"] for v in view] == ["c7", "c6", "c5", "c4", "c3", "c2"]
    assert [v["key"] for v in view] == [1, 2, 4, 5, 6, 7]
    assert r.hub.allocator.queue == (("app-a", "c1"), ("app-a", "c0"))  # "quiet" never had a task.


def test_new_activity_replaces_the_least_recent_and_others_stay(rig):
    r = rig(*(busy(f"c{i}", i) for i in range(1, 7)))
    before = list(r.hub.allocator.slots)
    r.source.put(busy("new", 10))
    r.hub.refresh()
    after = r.hub.allocator.slots
    oldest = before.index(("app-a", "c1"))
    assert after[oldest] == ("app-a", "new")
    assert [k for i, k in enumerate(after) if i != oldest] == [k for i, k in enumerate(before) if i != oldest]
    assert r.hub.allocator.queue == (("app-a", "c1"),)
    r.source.put(busy("c1", 11))  # Queued conversation gets busy again.
    r.hub.refresh()
    assert ("app-a", "c1") in after and ("app-a", "c2") in r.hub.allocator.queue


def test_vanished_conversation_frees_its_light_for_the_queue(rig):
    r = rig(*(busy(f"c{i}", i) for i in range(1, 8)))
    assert r.hub.allocator.queue == (("app-a", "c1"),)
    slot = r.hub.allocator.slots.index(("app-a", "c5"))
    del r.source.items["c5"]
    r.hub.refresh()
    assert r.hub.allocator.slots[slot] == ("app-a", "c1") and r.hub.allocator.queue == ()


def test_conversations_from_several_apps_share_the_lights(rig):
    r = rig(busy("a1", 1), busy("a2", 3))
    other = ListSource("app-b", busy("b1", 2, "approval"))
    r.hub.register(other, FakeNavigator())
    assert r.device.last()["states"][:3] == ["working", "working", "approval"]
    assert r.hub.allocator.slots[2] == ("app-b", "b1")


def test_pinned_slot_keeps_its_conversation(rig):
    r = rig(*(busy(f"c{i}", i) for i in range(1, 7)))
    r.hub.pin(3, "app-a", "c1")  # The least recent one.
    for i in range(10, 13):
        r.source.put(busy(f"n{i}", i))
    r.hub.refresh()
    assert r.hub.allocator.slots[3] == ("app-a", "c1")
    del r.source.items["c1"]
    r.hub.refresh()
    assert r.device.last()["states"][3] == "unknown"  # Pinned but not observable.
    r.hub.unpin(3)
    assert r.hub.allocator.slots[3] != ("app-a", "c1")


def test_codex_slots_are_yielded_and_occupants_move(rig):
    r = rig(*(busy(f"c{i}", i) for i in range(1, 5)))
    r.hub.set_codex_slots({3, 4, 5})
    states = r.device.last()["states"]
    assert states[3:] == ["codex"] * 3 and states[:3] == ["working"] * 3
    assert r.hub.allocator.queue == (("app-a", "c1"),)
    r.hub.set_codex_slots(range(6))
    assert r.device.last()["states"] == ["codex"] * 6
    with pytest.raises(ValueError):
        r.hub.set_codex_slots({6})


def test_press_on_codex_or_empty_slot_opens_nothing(rig):
    r = rig(busy("a", 1), encoder_navigation=False)
    r.hub.set_codex_slots({1})
    for slot in (1, 4):
        r.device.key_down(slot)
        poll(r)
    assert r.nav.requests == [] and "没有可打开" in r.hub.focus_message


# --- completion_seq and standby -------------------------------------------------

def test_completion_counts_once_per_turn(rig):
    r = rig(busy("a", 1))
    r.source.put(busy("a", 2, "completed", completion_turn="t1"))
    r.hub.refresh()
    assert r.device.last()["completion_seq"][0] == 1 and r.device.pulses == [0]
    for state in ("completed", "unknown", "completed"):  # Repeats and gaps never replay.
        r.source.put(busy("a", 2, state, completion_turn="t1"))
        r.hub.refresh()
        r.clock.advance(2)
        r.hub.tick()
    assert r.device.last()["completion_seq"][0] == 1 and r.device.pulses == [0]
    r.source.put(busy("a", 3, "working", completion_turn="t1"))
    r.hub.refresh()
    r.source.put(busy("a", 4, "completed", completion_turn="t2"))
    r.hub.refresh()
    assert r.device.last()["completion_seq"][0] == 2 and r.device.pulses == [0, 0]


def test_completions_seen_at_startup_are_a_silent_baseline(rig):
    r = rig(busy("a", 1, "completed", completion_turn="old"))
    r.hub.refresh()
    assert r.device.last()["completion_seq"] == [0] * 6 and r.device.pulses == []


def test_counter_overflow_clears_and_restarts_baseline(rig):
    r = rig(busy("a", 1, "completed", completion_turn="t1"))
    r.hub._seq[0] = UINT32_MAX
    r.source.put(busy("a", 2, "completed", completion_turn="t2"))
    r.hub.refresh()
    names = [c.name for c in r.device.commands[-2:]]
    assert names == ["CLEAR_AGENT_STATUS", "SET_AGENT_STATUS"]
    assert r.device.last()["completion_seq"] == [0] * 6 and r.device.pulses == []


def test_verified_open_turns_completed_into_standby(rig):
    r = rig(busy("a", 1), encoder_navigation=False)
    r.source.put(busy("a", 2, "completed", completion_turn="t1"))
    r.hub.refresh()
    r.device.key_down(0)
    poll(r)
    assert r.nav.requests == ["a"] and r.device.last()["states"][0] == "standby"
    assert r.device.last()["completion_seq"][0] == 1
    r.source.put(busy("a", 3, "completed", completion_turn="t2"))
    r.hub.refresh()
    assert r.device.last()["states"][0] == "completed"


def test_app_reported_view_and_mark_viewed_give_standby(rig):
    r = rig(busy("a", 1, "completed", completion_turn="t1", viewed=True),
            busy("b", 2, "completed", completion_turn="t1"))
    assert r.device.last()["states"][:2] == ["completed", "standby"]
    r.hub.mark_viewed("app-a", "b")
    assert r.device.last()["states"][:2] == ["standby", "standby"]


def test_unreadable_app_reports_unknown(rig):
    r = rig(busy("a", 1), busy("b", 2, "approval"))
    r.source.error = "store locked"
    r.hub.refresh()
    assert r.device.last()["states"][:2] == ["unknown", "unknown"]
    assert r.hub.source_errors == {"app-a": "store locked"}
    r.source.error = None
    r.source.put(busy("c", 3, "idle"))  # `idle` belongs to the hub, not an app.
    r.hub.refresh()
    assert "app-a" in r.hub.source_errors


# --- keys -----------------------------------------------------------------------

def test_key_press_opens_verifies_and_deduplicates(rig):
    r = rig(busy("a", 1), busy("b", 2), encoder_navigation=False)
    poll(r)  # Baseline only.
    assert r.nav.requests == []
    r.device.key_down(1)  # Slot 1 holds the older conversation "a".
    poll(r)
    poll(r)  # Same sequence again: nothing new.
    assert r.nav.requests == ["a"] and "核对" in r.hub.focus_message
    opened = []
    r.hub.opened.connect(lambda app, cid: opened.append((app, cid)))
    r.nav.results["b"] = OpenResult(active_id="a")  # The app showed the wrong one.
    r.device.key_down(0)
    poll(r)
    assert r.nav.requests == ["a", "b"] and opened == [] and "不一致" in r.hub.focus_message


def test_first_status_after_bind_is_only_a_baseline(rig):
    r = rig(busy("a", 1), encoder_navigation=False, bind=False)
    r.device.lease_until, r.device.lease_transport = r.clock() + 5, "usb"
    r.device.key_down(0)  # Pressed before this Console session.
    r.hub.bind(r.device.snapshot())
    poll(r)
    assert r.nav.requests == []


def test_expired_or_other_transport_presses_are_ignored(rig):
    r = rig(busy("a", 1), encoder_navigation=False)
    r.device.key_down(0)
    r.clock.advance(2.1)  # Read after the 2 s event lifetime.
    poll(r)
    assert r.nav.requests == [] and "过期" in r.hub.focus_message
    r.device.key_down(0)
    poll(r, transport="ble")  # Lease is on USB; the firmware nulls the event.
    assert r.nav.requests == []
    r.device.key_down(0, transport="ble")  # Defensive: event names another transport.
    r.device.input_transport = r.device.lease_transport = "usb"
    status = r.device.status()
    status["desktop_agent_press"]["transport"] = "ble"
    r.hub.consume_status(status)
    assert r.nav.requests == [] and "另一条连接" in r.hub.focus_message


def test_presses_work_over_ble(rig):
    r = rig(busy("a", 1), encoder_navigation=False, transport="ble")
    r.device.lease_transport = "ble"
    r.device.key_down(0, transport="ble")
    poll(r, transport="ble")
    assert r.hub.transport == "ble" and r.nav.requests == ["a"]


def test_latest_press_wins_while_opening(rig):
    r = rig(busy("a", 1), busy("b", 2), busy("c", 3), encoder_navigation=False)
    r.nav.auto = False
    for slot in (0, 1, 2):
        r.device.key_down(slot)
        poll(r)
    assert r.nav.requests == ["c"]
    r.nav.finish()
    assert r.nav.requests == ["c", "a"]  # "b" was replaced by the later press.
    r.nav.finish()
    assert r.hub._current_key == ("app-a", "a")


def test_navigator_failure_is_reported_and_not_busy(rig):
    class Broken:
        def open(self, conversation_id, finished):
            raise RuntimeError("窗口不可用")
    r = rig(encoder_navigation=False)
    r.hub.register(ListSource("app-b", busy("x", 1)), Broken())
    r.device.key_down(0)
    poll(r)
    assert r.hub.focus_message == "窗口不可用" and not r.hub._busy


def test_stale_open_result_after_unbind_is_dropped(rig):
    r = rig(busy("a", 1), encoder_navigation=False)
    r.nav.auto = False
    r.device.key_down(0)
    poll(r)
    r.hub.unbind()
    r.nav.finish()
    assert r.hub._current_key is None


# --- encoder ---------------------------------------------------------------------

def test_encoder_moves_from_the_key_anchor_and_skips_empty_slots(rig):
    r = rig(busy("a", 1), busy("b", 2), busy("c", 3))  # Slots 0..2 hold c, b, a.
    assert r.device.last()["encoder_navigation"] is True
    poll(r)
    r.device.key_down(0)
    poll(r)
    assert r.nav.requests == ["c"]
    r.device.turn(1)
    poll(r)
    assert r.nav.requests[-1] == "b"
    r.device.turn(2)  # Past "a" (slot 2) to slot 0 again, skipping empty 3..5.
    poll(r)
    assert r.nav.requests[-1] == "c"
    r.device.turn(-1)
    poll(r)
    assert r.nav.requests[-1] == "a"


def test_key_then_turn_between_polls_applies_the_offset(rig):
    r = rig(busy("a", 1), busy("b", 2), busy("c", 3))
    poll(r)
    r.device.key_down(1)
    r.device.turn(1)
    poll(r)
    assert r.nav.requests == ["a"]


def test_encoder_without_an_open_conversation_asks_for_a_key(rig):
    r = rig(busy("a", 1))
    poll(r)
    r.device.lease_transport = "usb"
    r.device.turn(1)
    r.device.nav["agent"] = None
    poll(r)
    assert r.nav.requests == [] and "先按一颗灯" in r.hub.focus_message


def test_turns_while_opening_coalesce(rig):
    r = rig(busy("a", 1), busy("b", 2), busy("c", 3))
    r.nav.auto = False
    poll(r)
    r.device.key_down(0)
    poll(r)
    r.device.turn(1)
    poll(r)
    r.device.turn(1)
    poll(r)
    r.nav.finish()
    assert r.nav.requests == ["c", "a"]


def test_press_stream_is_ignored_while_the_encoder_owns_input(rig):
    r = rig(busy("a", 1), busy("b", 2))
    poll(r)
    r.device.key_down(1)
    poll(r)
    assert r.nav.requests == ["a"]  # Opened once, not once per stream.


# --- decoupling ------------------------------------------------------------------

def test_hub_imports_no_app_module():
    tree = ast.parse(Path(hub_module.__file__).read_text(encoding="utf-8"))
    names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
             for alias in node.names]
    names += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert names and not [n for n in names if "doubao" in n or "claude" in n or "codex" in n]


# --- one connection (the firmware owner 11-04: USB suspends BLE, unplugging returns to BLE) ---

def test_ble_poll_interval_is_configurable(rig):
    r = rig(busy("a", 1), transport="ble")
    assert r.hub.ble_poll_ms == 250 and r.hub.status_poll_ms == 250
    requested = []
    r.hub.status_poll_requested.connect(requested.append)
    r.hub.set_ble_poll_ms(100)
    assert requested == [100] and r.hub.status_poll_ms == 100
    for bad in (0, 49, 1001, 250.0, "250"):
        with pytest.raises(ValueError):
            r.hub.set_ble_poll_ms(bad)
    assert r.hub.ble_poll_ms == 100
    r.hub.bind(r.device.snapshot("usb"))  # USB pushes nothing extra either way.
    assert requested[-1] == 0 and r.hub.status_poll_ms == 0
    r.hub.disable()
    assert r.hub.status_poll_ms == 0
    hub = DesktopAgentHub(r.device, clock=r.clock, ble_poll_ms=400, use_timer=False)
    assert hub.ble_poll_ms == 400
    with pytest.raises(ValueError):
        DesktopAgentHub(r.device, ble_poll_ms=5000, use_timer=False)


def test_plugging_usb_is_a_new_connection(rig):
    r = rig(busy("a", 1), busy("b", 2), encoder_navigation=False, transport="ble")
    poll(r, "ble")
    r.hub.consume_status(r.device.status("ble"), port_name=PORTS["ble"])  # Same connection.
    assert r.hub.bound and r.hub.connection == PORTS["ble"]
    r.device.key_down(0, transport="ble")  # Pressed on BLE, never read there.
    r.device.link = "usb"  # USB plugged in: BLE suspended.
    r.hub.connection_changed(PORTS["usb"])
    assert not r.hub.bound and "连接已切换" in r.hub.message and r.hub.status_poll_ms == 0
    sent = len(r.device.commands)
    r.clock.advance(3)
    r.hub.tick()
    r.source.put(busy("a", 3, "approval"))
    r.hub.refresh()
    assert len(r.device.commands) == sent  # Nothing until HELLO on the new link.
    r.hub.bind(r.device.snapshot("usb"))
    assert len(r.device.commands) == sent + 1 and r.device.last()["states"][1] == "approval"
    assert r.device.lease_transport == "usb" and r.hub.transport == "usb"
    r.device.input_transport = "usb"  # Even if the old press were still readable here,
    poll(r)  # the first read on the new connection is only a baseline.
    assert r.nav.requests == []
    r.device.key_down(0)
    poll(r)
    assert r.nav.requests == ["b"]


def test_unplugging_returns_to_ble_as_a_new_connection(rig):
    r = rig(busy("a", 1), encoder_navigation=False)
    r.device.link = "ble"
    r.hub.consume_status(r.device.status("ble"), port_name=PORTS["ble"])  # First BLE read.
    assert not r.hub.bound and r.nav.requests == []
    r.hub.bind(r.device.snapshot("ble"))
    first = r.device.commands[-1]
    assert first.name == "SET_AGENT_STATUS" and r.device.lease_transport == "ble"
    assert r.hub.status_poll_ms == 250
    r.device.key_down(0, transport="ble")
    r.hub.consume_status(r.device.status("ble"), port_name=PORTS["ble"])
    assert r.nav.requests == ["a"]


def test_reads_and_replies_from_the_old_connection_are_dropped(rig):
    r = rig(busy("a", 1), busy("b", 2), encoder_navigation=False)
    r.nav.auto = False
    r.device.key_down(0)
    poll(r)
    assert r.nav.requests == ["b"]
    r.device.script = ["hold"]
    r.source.put(busy("a", 3, "reply"))
    r.hub.refresh()
    old = r.device.held.pop()
    r.hub.connection_changed(PORTS["ble"])
    r.nav.finish()  # The open finished after the switch: not ours any more.
    r.device.reply(old)  # Neither is the old link's ACK.
    assert r.hub._current_key is None and not r.hub.lease_active
    r.hub.connection_changed(PORTS["ble"])  # Repeated notice is harmless.
    r.hub.bind(r.device.snapshot("ble"))
    r.device.lease_transport = "ble"
    r.device.key_down(1, transport="ble")
    r.hub.consume_status(r.device.status("usb"), port_name=PORTS["usb"])  # Stale USB read.
    assert not r.hub.bound and r.nav.requests == ["b"]


# --- wiring additions (接线单 task) ---------------------------------

def test_release_when_empty_holds_no_lease_until_a_conversation_shows(rig):
    r = rig(C("quiet"), bind=False)
    r.hub.release_when_empty = True
    r.hub.bind(r.device.snapshot())
    r.clock.advance(3)
    r.hub.refresh()
    assert r.device.commands == []  # design decision: nothing to show, Codex keeps the lights.
    r.source.put(busy("a", 1))
    r.hub.refresh()
    assert r.device.last()["states"][0] == "working"
    del r.source.items["a"]
    r.hub.refresh()
    assert r.device.commands[-1].name == "CLEAR_AGENT_STATUS" and not r.device.live()
    count = len(r.device.commands)
    r.clock.advance(3)
    r.hub.refresh()
    assert len(r.device.commands) == count  # No heartbeat, no repeated CLEAR.


def test_all_codex_slots_hold_no_lease_with_release_when_empty(rig):
    r = rig(busy("a", 1), bind=False)
    r.hub.release_when_empty = True
    r.hub.set_codex_slots(range(6))
    r.hub.bind(r.device.snapshot())
    assert r.device.commands == []


def test_restore_puts_saved_conversations_back_on_their_lights(rig):
    r = rig(C("a"), C("b"), C("c"))  # Reported, but no task this run.
    assert r.hub.assignment("app-a") == ((None,) * 6, {})
    r.hub.restore("app-a", (None, "b", None, "a", None, None), {5: "c"})
    assert r.hub.assignment("app-a") == ((None, "b", None, "a", None, "c"), {5: "c"})
    states = r.device.last()["states"]
    assert states[1] == states[3] == states[5] == "standby" and states[0] == "idle"
    # Real activity takes an empty light first, then the oldest restored one.
    for i, cid in enumerate(["n1", "n2", "n3", "n4"], start=10):
        r.source.put(busy(cid, i))
    r.hub.refresh()
    slots = r.hub.assignment("app-a")[0]
    assert slots[5] == "c" and slots.count(None) == 0
    assert {"n1", "n2", "n3", "n4"} <= set(slots) and len({"a", "b"} & set(slots)) == 1


def test_restored_conversation_returns_to_its_light_when_reported_later(rig):
    r = rig()
    r.hub.restore("app-a", ("x", None, None, None, None, "y"), {})
    assert r.hub.assignment("app-a")[0] == (None,) * 6  # Not reported yet.
    r.source.put(C("y"))
    r.source.put(C("x"))
    r.hub.refresh()
    assert r.hub.assignment("app-a")[0] == ("x", None, None, None, None, "y")


def test_restore_for_another_account_clears_this_apps_lights_only(rig):
    r = rig(busy("a", 1))
    other = ListSource("app-b", busy("b1", 2))
    r.hub.register(other, FakeNavigator())
    r.hub.pin(4, "app-a", "a")
    r.hub.restore("app-a", (None,) * 6, {})
    slots, pins = r.hub.assignment("app-a")
    assert pins == {} and "a" in slots  # Pin gone; "a" keeps a light by its own activity.
    assert ("app-b", "b1") in r.hub.allocator.slots
    with pytest.raises(ValueError):
        r.hub.restore("app-a", (None,) * 5, {})


def test_hub_uses_the_one_desktop_wire_module():
    from controller_config import desktop_status_wire
    assert hub_module.validate_navigation is desktop_status_wire.validate_navigation
    assert hub_module.status_command is desktop_status_wire.status_command
    with pytest.raises(ValueError):  # the firmware owner 11-04: agent without transport is invalid.
        hub_module.validate_navigation({"source": "desktop", "sequence": 3, "anchor_sequence": 3,
                                        "offset": 0, "agent": 1, "transport": None})
    hub_module.validate_navigation({"source": "desktop", "sequence": 3, "anchor_sequence": 0,
                                    "offset": 1, "agent": None, "transport": "usb"})
