"""Claude desktop Code sessions on the `desktop` lights (design decision,
task): the Hooks registry feeds the hub next to Doubao, over USB
and Bluetooth; the old `claude_code` source is no longer sent; a press brings
Claude to the front and marks the light seen; Console hides the Claude Code mode.

Synthetic session IDs only; no real Claude settings, Hooks or sessions.
"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QObject, QSettings, Signal

from controller_config.claude_desktop_adapter import (
    OPEN_FRONT_ONLY, OPEN_NOT_ACTIVATED, ClaudeDesktopAdapter, _Inline,
)
from controller_config.claude_hooks import ClaudeHookInstallation
from controller_config.claude_status import ClaudeStatusBridge
from controller_config.desktop_agent_hub import Conversation, OpenResult
from controller_config.viewmodels.desktop_agents import DesktopAgentLink
from test_desktop_agent_hub import Clock, FakeDevice
from test_session_recovery import session  # noqa: F401 - window fixture


def event(sid, name="UserPromptSubmit", **fields):
    return {"session_id": sid, "hook_event_name": name, **fields}


def stop(sid):
    return event(sid, "Stop", stop_hook_active=False, has_background_tasks=False,
                 has_session_crons=False)


class FakeDoubao(QObject):
    """Just enough of DoubaoAdapter for DesktopAgentLink: a fixed list."""
    app_id = "doubao"
    changed = Signal()
    account_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.running = True
        self.account = ""
        self.items: dict[str, Conversation] = {}
        self.opened: list[str] = []

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def problems(self):
        return []

    def stats(self):
        return {}

    def conversations(self):
        return list(self.items.values())

    def open(self, conversation_id, finished):
        self.opened.append(conversation_id)
        finished(OpenResult(active_id=conversation_id))

    def put(self, cid, state="working"):
        self.items[cid] = Conversation(cid, state, time.monotonic())
        self.changed.emit()


class Rig:
    def __init__(self, tmp_path, *, transport="usb", doubao_on=True, activate=True,
                 open_session=None):
        self.clock = Clock()
        self.device = FakeDevice(self.clock)
        self.doubao = FakeDoubao()
        settings = QSettings(str(tmp_path / "link.ini"), QSettings.IniFormat)
        settings.setValue("desktop_agents/doubao_enabled", doubao_on)
        self.link = DesktopAgentLink(self.device, adapter_factory=lambda _p: self.doubao,
                                     settings=settings, clock=self.clock, use_timer=False)
        self.hub = self.link.hub
        self.bridge = ClaudeStatusBridge(installation=ClaudeHookInstallation(
            tmp_path / "settings.json", tmp_path / "endpoint.json"))
        self.activations = []

        def activate_claude():
            self.activations.append(True)
            return activate

        self.claude = ClaudeDesktopAdapter(self.bridge.registry, activate=activate_claude,
                                           open_session=open_session, executor=_Inline())
        self.link.attach_claude(self.bridge, adapter=self.claude)
        self.transport = transport
        snapshot = self.device.snapshot(transport)
        snapshot.identity = {"serial": "CP01-TEST"}
        self.link.bind(snapshot)

    def claude_on(self):
        # The Hooks opt-in without touching any settings file or port.
        self.bridge.enabled = True
        self.bridge._notify()

    def claude_off(self):
        self.bridge.shutdown()
        self.bridge._notify()

    def send(self, *events):
        for e in events:
            self.bridge.consume(e)

    def states(self):
        return self.hub.wire()[0]

    def slot(self, app_id, cid):
        return self.hub.allocator.slots.index((app_id, cid))

    def press(self, slot):
        self.device.key_down(slot, transport=self.transport)
        self.hub.consume_status(self.device.status(self.transport))

    def sources(self):
        return [c.payload.get("source") for c in self.device.commands]


@pytest.fixture
def rig(qapp, tmp_path_factory):
    made = []

    def make(**kwargs):
        r = Rig(tmp_path_factory.mktemp("claude-route"), **kwargs)
        made.append(r)
        return r

    yield make
    for r in made:
        r.bridge.shutdown()


# --- R3: state mapping and completion count ----------------------------------

def test_claude_sessions_map_to_desktop_states_and_each_completion_pulses_once(rig):
    r = rig(doubao_on=False)
    r.claude_on()
    r.send(event("s1"))
    assert r.device.last()["states"][0] == "working"
    assert r.sources() and set(r.sources()) == {"desktop"}
    r.send(stop("s1"))
    assert r.device.last()["states"][0] == "completed"
    assert r.device.last()["completion_seq"][0] == 1 and r.device.pulses == [0]
    r.hub.tick()
    assert r.device.last()["completion_seq"][0] == 1  # the same Stop never counts twice
    r.send(event("s1"), stop("s1"))
    assert r.device.last()["completion_seq"][0] == 2 and r.device.pulses == [0, 0]
    r.send(event("s1", "PermissionRequest"))
    assert r.device.last()["states"][0] == "approval"
    r.send(event("s1", "PreToolUse", tool_name="AskUserQuestion"))
    assert r.device.last()["states"][0] == "reply"
    r.send(event("s1", "StopFailure"))
    assert r.device.last()["states"][0] == "error"
    # An idle session takes no light; a released session gives its light back.
    r.send(event("s2", "SessionStart"))
    assert r.states().count("idle") == 5
    r.send(event("s1", "SessionEnd"))
    assert r.device.commands[-1].name == "CLEAR_AGENT_STATUS"  # no conversation: no lease


def test_subagent_stop_does_not_finish_the_claude_light(rig):
    r = rig(doubao_on=False)
    r.claude_on()
    r.send(event("s1"), event("s1", "SubagentStart", agent_id="a1"),
           {**stop("s1"), "agent_id": "a1"})
    assert r.device.last()["states"][0] == "working"


# --- R4: Claude, Doubao and Codex side by side -------------------------------

def test_claude_and_doubao_share_lights_and_release_separately(rig):
    r = rig()
    r.claude_on()
    r.doubao.put("d1")
    r.send(event("s1"))
    claude_slot, doubao_slot = r.slot("claude", "s1"), r.slot("doubao", "d1")
    assert claude_slot != doubao_slot
    states = r.device.last()["states"]
    assert states[claude_slot] == states[doubao_slot] == "working"
    r.send(event("s1", "SessionEnd"))
    states = r.device.last()["states"]
    assert states[claude_slot] == "idle" and states[doubao_slot] == "working"
    r.send(event("s2"))
    del r.doubao.items["d1"]
    r.doubao.changed.emit()
    assert ("doubao", "d1") not in r.hub.allocator.slots
    assert r.device.last()["states"][r.slot("claude", "s2")] == "working"


def test_codex_lights_stay_codex_while_claude_and_doubao_queue(rig):
    r = rig()
    r.claude_on()
    r.hub.set_codex_slots({4, 5})
    for n in range(3):
        r.doubao.put(f"d{n}")
        r.send(event(f"s{n}"))
    states = r.device.last()["states"]
    assert states[4:] == ["codex", "codex"]
    assert states[:4] == ["working"] * 4
    assert len(r.hub.allocator.queue) == 2  # six conversations, four lights


def test_switching_doubao_off_keeps_claude_and_back(rig):
    r = rig()
    r.claude_on()
    r.doubao.put("d1")
    r.send(event("s1"))
    r.link.set_enabled(False)
    assert r.hub.enabled
    assert ("doubao", "d1") not in r.hub.allocator.slots
    assert r.device.commands[-1].name == "SET_AGENT_STATUS"
    assert "working" in r.device.last()["states"]
    r.link.set_enabled(True)
    assert ("doubao", "d1") in r.hub.allocator.slots
    # Claude off: only Claude's light goes; Doubao keeps the lease.
    r.claude_off()
    assert r.hub.enabled and ("claude", "s1") not in r.hub.allocator.slots
    assert r.device.last()["states"].count("working") == 1


def test_claude_alone_runs_the_hub_and_off_clears_once(rig):
    r = rig(doubao_on=False)
    assert not r.hub.enabled
    r.claude_on()
    assert r.hub.enabled
    r.send(event("s1"))
    r.claude_off()
    assert not r.hub.enabled
    assert r.device.commands[-1].name == "CLEAR_AGENT_STATUS"
    assert not r.device.live()


# --- R5: USB and Bluetooth ----------------------------------------------------

@pytest.mark.parametrize("transport", ["usb", "ble"])
def test_claude_light_is_sent_over_usb_and_bluetooth(rig, transport):
    r = rig(transport=transport, doubao_on=False)
    r.claude_on()
    r.send(event("s1"))
    assert r.hub.transport == transport
    assert r.device.lease_transport == transport
    assert r.device.last()["states"][0] == "working"
    assert r.link.claude_lights_in_use == 1
    if transport == "ble":
        assert r.hub.status_poll_ms > 0  # key presses are read by polling over BLE


# --- R6/R7: a press on a Claude light -----------------------------------------

@pytest.mark.parametrize("transport", ["usb", "ble"])
def test_press_brings_claude_to_front_and_turns_the_light_white(rig, transport):
    r = rig(transport=transport, doubao_on=False)
    failures = []
    r.link.open_failed.connect(failures.append)
    r.claude_on()
    r.send(event("s1"), stop("s1"))
    slot = r.slot("claude", "s1")
    assert r.device.last()["states"][slot] == "completed"
    r.press(slot)
    assert r.activations == [True]
    # the designer 11-16: Claude reached the front, so no notice pops up; the
    # hub still records that the session itself was not opened.
    assert failures == []
    assert r.hub.focus_message == OPEN_FRONT_ONLY
    assert r.device.last()["states"][slot] == "standby"  # seen: white
    assert r.link.last_open_problem == ""  # Doubao's settings line stays Doubao's
    # The next completion is green again.
    r.send(event("s1"), stop("s1"))
    assert r.device.last()["states"][slot] == "completed"


def test_press_when_claude_cannot_be_brought_front_still_counts_as_seen(rig):
    r = rig(doubao_on=False, activate=False)
    failures = []
    r.link.open_failed.connect(failures.append)
    r.claude_on()
    r.send(event("s1"), stop("s1"))
    r.press(0)
    assert failures == [OPEN_NOT_ACTIVATED]
    assert r.device.last()["states"][0] == "standby"


def test_open_session_hook_opens_the_session_without_fallback(rig):
    opened = []
    r = rig(doubao_on=False, open_session=lambda sid: opened.append(sid) or True)
    r.claude_on()
    r.send(event("s1"), stop("s1"))
    r.press(0)
    assert opened == ["s1"] and r.activations == []
    assert r.hub.focus_message == "已打开并核对对话"
    assert r.device.last()["states"][0] == "standby"


def test_adapter_stats_hold_no_session_ids():
    from controller_config.claude_sessions import ClaudeSessionRegistry
    registry = ClaudeSessionRegistry()
    registry.consume(event("secret-session"))
    adapter = ClaudeDesktopAdapter(registry, activate=lambda: True, executor=_Inline())
    assert "secret-session" not in repr(adapter.stats())
    assert [c.conversation_id for c in adapter.conversations()] == ["secret-session"]


# --- the old `claude_code` source is no longer sent ---------------------------

def test_bridge_sends_no_claude_code_source_commands(qapp, contract, tmp_path):
    from test_claude_status import Gateway
    from controller_config.transport.demo import _power_v2_snapshot
    gateway = Gateway()
    bridge = ClaudeStatusBridge(gateway, installation=ClaudeHookInstallation(
        tmp_path / "settings.json", tmp_path / "endpoint.json"))
    bridge.enabled = True
    bridge.bind(_power_v2_snapshot(contract, read_only=False))
    bridge.consume(event("s1"))
    bridge._tick()
    bridge.set_paused(True)
    bridge.set_paused(False)
    bridge.unbind(clear=True)
    bridge.shutdown()
    assert gateway.commands == []
    assert bridge.registry.states()[0] == "working"


def test_console_sends_claude_over_desktop_source_only(qtbot, contract, tmp_path):
    from controller_config.transport.demo import DemoGateway
    from controller_config.viewmodels.main import MainViewModel
    gateway = DemoGateway(contract, "ready", desktop_agent=True)
    sent = []
    execute = gateway.execute_command
    gateway.execute_command = lambda command: (sent.append(command), execute(command))[1]
    vm = MainViewModel(gateway, contract)
    vm.desktop_agents.set_enabled(False)  # no Doubao on this machine
    vm.start()
    try:
        qtbot.waitUntil(lambda: vm.desktop_agents.hub.bound, timeout=3000)
        vm.claude_status.enabled = True  # Hooks opt-in without touching settings
        vm.claude_status._notify()
        vm.claude_status.consume(event("s1"))
        qtbot.waitUntil(lambda: gateway.desktop_status()["desktop_agent_status"]["states"][0]
                        == "working", timeout=3000)
        assert not [c for c in sent if c.payload.get("source") == "claude_code"]
        assert vm.claude_status.message == "状态联动已开启"
    finally:
        vm.claude_status.enabled = False
        vm.shutdown()


# --- the Claude settings page shows the hub's lights -------------------------

def test_settings_page_lists_the_real_light_and_hub_fault(qtbot, rig):
    from controller_config.views.claude_status_settings import ClaudeStatusSettings, claude_diagnostics
    from controller_config.views.settings_wall import ClaudeStatusTile
    r = rig()
    r.claude_on()
    r.doubao.put("d1")
    r.send(event("s1"), event("s2", "SessionStart"))
    widget = ClaudeStatusSettings(r.bridge)
    qtbot.addWidget(widget)
    # The session list is a debugging aid now: it is in 「复制诊断信息」 (design decision).
    text = claude_diagnostics(r.bridge)
    key = f"Key{(1, 2, 4, 5, 6, 7)[r.slot('claude', 's1')]}"
    assert f"{key} · s1" in text and "不占灯 · s2" in text
    from PySide6.QtWidgets import QLabel
    note = "\n".join(label.text() for label in widget.findChildren(QLabel, "muted"))
    assert "蓝牙连接都可以" in note and "只经 USB" not in note
    r.device.script = ["nack"]
    r.send(event("s1", "PermissionRequest"))
    assert r.hub.fault and r.bridge.fault
    # Retry is the tile's one fix action (the details have no second set of buttons).
    tile = ClaudeStatusTile(r.bridge)
    qtbot.addWidget(tile)
    assert not tile.fix.isHidden() and tile.fix.text() == "重试"
    tile.fix.click()
    assert not r.hub.fault


# --- R10: Console hides the Claude Code mode ---------------------------------

def _claude_code_device(session):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot,
        identity={**snapshot.identity, "serial": "CP01-cc-hidden"},
        status={**snapshot.status, "operating_mode": "claude_code"}))
    window._navigate("overview")
    return window


def test_mode_page_has_no_claude_code_tab(session):
    window = _claude_code_device(session)
    # One screen per set, then Codex: there is no Claude Code screen to flip to.
    modes = {mode for mode, _profile in window._mode_screens()}
    assert modes and "claude_code" not in modes
    assert window._configuration_mode == "normal"
    window._select_configuration_mode("claude_code")
    assert window._configuration_mode == "normal"


def test_mode_page_explains_a_device_in_claude_code_mode(session):
    from controller_config.views.mode_page import ModeStage
    window = _claude_code_device(session)
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    words = " ".join([*stage.words(), stage.bar.status_text.text()])
    # Console never names Claude Code mode (boring-P13); it only says how to leave it.
    assert "Claude Code" not in words
    assert stage.bar.status_text.text() == "双击设备上的旋钮，切回普通模式"


def test_mode_page_shows_no_note_outside_claude_code(session):
    from PySide6.QtWidgets import QLabel
    window, vm, gateway, snapshot, _ = session
    window._navigate("overview")
    assert not window.findChildren(QLabel, "modeClaudeCodeHidden")

