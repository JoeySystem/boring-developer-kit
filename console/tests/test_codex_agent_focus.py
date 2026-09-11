from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QProcess

from controller_config.codex_agent_focus import (
    CodexAgentFocus, MacChatGPTActivator,
)
from controller_config.protocol.contract import ContractError, validate_agent_press
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.prompt_library import PromptLibraryStore


@pytest.fixture
def snapshot(contract):
    value = copy.deepcopy(_power_v2_snapshot(contract, read_only=False))
    value = replace(value, trust=DeviceTrust(DeviceTrustState.AUTHENTICATED, "verified", ""))
    value.capabilities["features"]["codex_agent_focus"] = True
    value.status["operating_mode"] = "codex"
    value.status["codex_agent_press"] = {"sequence": 0, "agent": None, "transport": None}
    return value


def press(snapshot, sequence=1, agent=0, transport="usb"):
    status = copy.deepcopy(snapshot.status)
    status["codex_agent_press"] = dict(sequence=sequence, agent=agent, transport=transport)
    return status


@pytest.mark.parametrize("agent", range(6))
def test_fresh_agent_press_only_once(qtbot, snapshot, agent):
    listener = CodexAgentFocus()
    received = []
    listener.requested.connect(received.append)
    listener.bind(snapshot)
    status = press(snapshot, agent=agent)
    listener.consume(status)
    listener.consume(status)
    listener.consume(press(snapshot, agent=None, transport=None))
    assert received == [agent]


def test_bootstrap_reconnect_and_readback_never_replay(qtbot, snapshot):
    listener = CodexAgentFocus()
    received = []
    listener.requested.connect(received.append)
    snapshot = replace(snapshot, status=press(snapshot, sequence=8))
    listener.bind(snapshot)
    listener.consume(snapshot.status)
    listener.consume(press(snapshot, sequence=9))
    listener.unbind()
    listener.consume(press(snapshot, sequence=10))
    snapshot = replace(snapshot, status=press(snapshot, sequence=10))
    listener.bind(snapshot)
    listener.consume(snapshot.status)
    listener.consume(press(snapshot, sequence=11))
    assert received == [0, 0]


@pytest.mark.parametrize("kind", ["missing_capability", "untrusted", "read_only", "missing_field"])
def test_ineligible_connections_do_not_activate(qtbot, snapshot, kind):
    if kind == "missing_capability":
        snapshot.capabilities["features"].pop("codex_agent_focus")
    elif kind == "untrusted":
        snapshot = replace(snapshot, trust=DeviceTrust(DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED, "dev", ""))
    elif kind == "read_only":
        snapshot.compatibility.update(read=True, write=False)
    else:
        snapshot.status.pop("codex_agent_press")
    listener = CodexAgentFocus()
    received = []
    listener.requested.connect(received.append)
    if kind == "read_only":
        assert snapshot.is_read_only
    listener.bind(snapshot)
    listener.consume(press(snapshot))
    assert received == []


@pytest.mark.parametrize("reason", ["normal", "claude_code", "local", "capture", "standby", "busy", "expired", "transport"])
def test_suppressed_event_is_consumed_not_deferred(qtbot, snapshot, reason):
    listener = CodexAgentFocus()
    received = []
    listener.requested.connect(received.append)
    listener.bind(snapshot)
    status = press(snapshot)
    if reason in ("normal", "claude_code"):
        status["operating_mode"] = reason
    elif reason == "local":
        status["action_engine"]["local_page"] = "settings"
    elif reason == "capture":
        status["diagnostic_capture"]["active"] = True
    elif reason == "standby":
        status["action_engine"]["usb_standby_active"] = True
    elif reason == "expired":
        status["codex_agent_press"].update(agent=None, transport=None)
    elif reason == "transport":
        status["codex_agent_press"]["transport"] = "ble"
    listener.consume(status, allowed=reason != "busy")
    listener.consume(press(snapshot))
    assert received == []
    listener.consume(press(snapshot, sequence=2))
    assert received == [0]


def test_ble_transport_and_counter_wrap(qtbot, snapshot):
    snapshot = replace(snapshot, port_name="ble:test")
    snapshot.status["codex_agent_press"]["sequence"] = 0xFFFFFFFF
    listener = CodexAgentFocus()
    received = []
    listener.requested.connect(received.append)
    listener.bind(snapshot)
    listener.consume(press(snapshot, transport="ble"))
    assert received == [0]


@pytest.mark.parametrize("event", [None, {}, {"sequence": True, "agent": None, "transport": None},
    {"sequence": 1, "agent": 6, "transport": "usb"},
    {"sequence": 1, "agent": 0, "transport": []},
    {"sequence": 0, "agent": 0, "transport": "usb"},
    {"sequence": 1, "agent": None, "transport": "usb"}])
def test_invalid_event_rejected_by_contract(contract, snapshot, event):
    with pytest.raises(ValueError):
        validate_agent_press(event)
    snapshot.status["codex_agent_press"] = event
    with pytest.raises(ContractError):
        contract.validate_status({"command": "GET_STATUS", "result": snapshot.status})


def test_gateway_viewmodel_signal_chain_does_not_rebuild_ui(qtbot, contract, snapshot, tmp_path):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(gateway, contract, prompt_library_store=PromptLibraryStore(tmp_path / "prompts.json"))
    received, changed = [], []
    vm.codex_agent_focus.requested.connect(received.append)
    vm.changed.connect(changed.append)
    gateway.snapshot_ready.emit(snapshot)
    changed.clear()
    gateway.status_updated.emit(press(snapshot, agent=3))
    gateway.status_updated.emit(press(snapshot, agent=3))
    assert received == [3]
    assert changed == []
    gateway.disconnected.emit("unplugged")
    gateway.status_updated.emit(press(snapshot, sequence=2))
    assert received == [3]
    vm.shutdown()


def test_activator_uses_launchservices_without_background_or_new_instance(qtbot, monkeypatch):
    activator = MacChatGPTActivator()
    calls = []
    monkeypatch.setattr(activator._process, "start", lambda *args: calls.append(args))
    activator.activate(4)
    activator.activate(5)
    assert calls == [("/usr/bin/open", ["-a", "ChatGPT"])]
    activator._finished(0, QProcess.ExitStatus.NormalExit)
    assert not activator._deadline.isActive()
    activator.shutdown()


def test_activator_failure_timeout_and_shutdown_are_bounded(qtbot, monkeypatch):
    activator = MacChatGPTActivator()
    failures = []
    activator.failed.connect(failures.append)
    monkeypatch.setattr(activator._process, "start", lambda *args: None)
    activator.activate()
    activator._finished(1, QProcess.ExitStatus.NormalExit)
    activator._error(QProcess.ProcessError.FailedToStart)
    assert len(failures) == 1
    activator.activate()
    activator._timeout()
    assert len(failures) == 2
    activator.activate()
    activator.shutdown()
    activator._finished(1, QProcess.ExitStatus.CrashExit)
    assert len(failures) == 2
