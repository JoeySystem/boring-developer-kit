from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QObject, Signal

from controller_config.agent_status import (
    SET_AGENT_STATUS, CLEAR_AGENT_STATUS, status_command, validate_agent_response,
)
from controller_config.claude_hooks import ClaudeHookInstallation
from controller_config.claude_status import ClaudeStatusBridge
from controller_config.protocol.contract import ContractError
from controller_config.transport.demo import _power_v2_snapshot, DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.prompt_library import PromptLibraryStore
from controller_config.workflows import WorkflowStore
from controller_config.automation import AutomationStore
from controller_config.models import AppState


class Gateway(QObject):
    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.commands = []

    def execute_command(self, command):
        self.commands.append(command)

    def ack(self, command=None):
        command = command or self.commands[-1]
        self.agent_status_completed.emit(command, {"command": command.name, "result": {
            "source": "claude_code", "active": command.name == SET_AGENT_STATUS, "lease_ms": 5000,
        }})


@pytest.fixture
def setup_bridge(qtbot, contract, tmp_path):
    gateway = Gateway()
    bridge = ClaudeStatusBridge(gateway, installation=ClaudeHookInstallation(
        tmp_path / "settings.json", tmp_path / "endpoint.json"))
    snapshot = _power_v2_snapshot(contract, read_only=False)
    bridge.enabled = True  # no real user settings or Hooks changed by tests
    bridge.bind(snapshot)
    yield bridge, gateway, snapshot
    bridge.shutdown()


def event(sid="s1", name="UserPromptSubmit", **fields):
    return {"session_id": sid, "hook_event_name": name, **fields}


def test_silent_initial_sync_then_latest_snapshot_and_fresh_heartbeat(setup_bridge):
    bridge, gateway, _ = setup_bridge
    assert gateway.commands[-1].name == CLEAR_AGENT_STATUS
    bridge.consume(event())
    gateway.ack()
    first = gateway.commands[-1]
    assert first.payload == {"source": "claude_code", "states": ["working"] + ["idle"] * 5}
    bridge.consume(event(name="PermissionRequest"))
    bridge.consume(event(name="PostToolUse"))
    assert gateway.commands[-1] is first  # one in-flight, no frame backlog
    gateway.ack()
    bridge._tick()
    assert gateway.commands[-1] is not first
    assert gateway.commands[-1].payload == first.payload
    assert first.retries == 0


def test_old_firmware_and_read_only_do_not_receive_status(setup_bridge):
    bridge, gateway, snapshot = setup_bridge
    for readonly in (False, True):
        capabilities = copy.deepcopy(snapshot.capabilities)
        capabilities["features"]["claude_code_status"] = readonly
        old = replace(snapshot, capabilities=capabilities,
                      compatibility={**snapshot.compatibility, "write": not readonly})
        gateway.commands.clear()
        bridge.bind(old)
        bridge.consume(event())
        bridge._tick()
        assert not gateway.commands
        assert "不支持" in bridge.message


def test_old_ack_cannot_update_new_device_binding(setup_bridge):
    bridge, gateway, snapshot = setup_bridge
    gateway.ack()
    old = gateway.commands[-1]
    bridge.unbind()
    bridge.bind(replace(snapshot, identity={**snapshot.identity, "serial": "CP01-112233445566"}))
    new = gateway.commands[-1]
    gateway.ack(old)
    assert bridge._pending is new
    gateway.ack(new)
    assert gateway.commands[-1].name == SET_AGENT_STATUS


def test_ota_pauses_and_clear_precedes_resume(setup_bridge):
    bridge, gateway, _ = setup_bridge
    gateway.ack()
    in_flight = gateway.commands[-1]
    bridge.set_paused(True)
    assert gateway.commands[-1].name == CLEAR_AGENT_STATUS
    count = len(gateway.commands)
    bridge.consume(event())
    bridge._tick()
    gateway.ack(in_flight)
    assert len(gateway.commands) == count
    bridge.set_paused(False)
    assert gateway.commands[-1].name == CLEAR_AGENT_STATUS
    gateway.ack()
    assert gateway.commands[-1].payload["states"][0] == "working"


def test_released_slot_is_acknowledged_idle_before_overflow_reuse(setup_bridge):
    bridge, gateway, _ = setup_bridge
    gateway.ack()
    gateway.ack()
    for index in range(7):
        bridge.consume(event(str(index)))
    gateway.ack()
    gateway.ack()
    assert len(bridge.registry.overflow) == 1
    bridge.consume(event("0", "SessionEnd"))
    assert gateway.commands[-1].payload["states"][0] == "idle"
    assert bridge.registry.pending_clear_slots == (0,)
    gateway.ack()
    assert gateway.commands[-1].payload["states"][0] == "working"
    assert bridge.registry.sessions[0].session_id == "6"


def test_status_failure_does_not_retry_forever_and_can_recover(setup_bridge):
    bridge, gateway, _ = setup_bridge
    gateway.agent_status_failed.emit(gateway.commands[-1], ValueError("READ_ONLY"))
    count = len(gateway.commands)
    bridge._tick()
    assert len(gateway.commands) == count
    assert "失败" in bridge.message
    bridge.retry()
    assert len(gateway.commands) == count + 1
    assert gateway.commands[-1].name == CLEAR_AGENT_STATUS


def test_receiver_adapter_to_registry_and_device_integration(qtbot, tmp_path, setup_bridge):
    bridge, gateway, _ = setup_bridge
    bridge.receiver.start()
    path = bridge.receiver.endpoint_path
    data = json.loads(path.read_text())
    assert data["host"] == "127.0.0.1"
    adapter = Path(__file__).parents[1] / "src/controller_config/claude_hook.py"
    result = subprocess.run([sys.executable, str(adapter), "--endpoint", str(path)],
                            input=json.dumps(event(prompt="DO NOT FORWARD", transcript_path="private")),
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0 and not result.stdout and not result.stderr
    qtbot.waitUntil(lambda: bridge.registry.states()[0] == "working")
    gateway.ack()
    assert gateway.commands[-1].payload["states"][0] == "working"
    assert "DO NOT FORWARD" not in repr(bridge.registry.sessions)


def test_status_readback_validation_accepts_new_and_old_firmware(contract, load_fixture):
    payload = load_fixture("status-matrix12-power-v2-v1.json")
    contract.validate_status(payload)
    payload["result"].pop("claude_code_status", None)
    contract.validate_status(payload)
    payload["result"]["claude_code_status"] = {"active": True, "selected": True,
        "lease_ms": 5000, "states": ["working"] * 5}
    with pytest.raises(ContractError):
        contract.validate_status(payload)


def test_bad_ack_is_not_treated_as_active():
    with pytest.raises(ValueError):
        validate_agent_response(SET_AGENT_STATUS, {"command": SET_AGENT_STATUS,
            "result": {"source": "codex", "active": True, "lease_ms": 5000}})


def test_viewmodel_demo_path_auth_failure_unbinds_and_keeps_config(qtbot, contract, tmp_path):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(gateway, contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"))
    vm.start()
    qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
    initial = copy.deepcopy(vm.draft.config)
    vm.claude_status.enabled = True
    vm.claude_status.bind(vm.model.snapshot)
    vm.claude_status.consume(event())
    qtbot.waitUntil(lambda: vm.claude_status._last_states == ("working",) + ("idle",) * 5)
    assert vm.draft.config == initial and not vm.draft.is_dirty
    from controller_config.protocol.bootstrap import BootstrapKind
    vm._on_failure(BootstrapKind.AUTHENTICITY_FAILED, "bad certificate", "test")
    assert vm.claude_status._binding is None
    vm.shutdown()


def test_hook_application_fast_path_does_not_import_qt(tmp_path):
    result = subprocess.run([sys.executable, "-m", "controller_config.app", "--claude-status-hook", str(tmp_path / "missing")],
                            input="{}", text=True, capture_output=True, timeout=3)
    assert result.returncode == 0 and not result.stdout and not result.stderr


def test_noop_heartbeat_does_not_refresh_settings_and_start_without_hooks_is_read_only(setup_bridge, monkeypatch):
    bridge, gateway, _ = setup_bridge
    gateway.ack()
    gateway.ack()
    changes = []
    bridge.changed.connect(lambda: changes.append(True))
    bridge._tick()
    gateway.ack()
    assert changes == []
    bridge.shutdown()
    monkeypatch.setattr(bridge.installation, "inspect", lambda: pytest.fail("uninstalled startup should not probe CLI"))
    bridge.start()
    assert not bridge.enabled


def test_settings_exposes_opt_in_and_overflow_without_new_top_navigation(qtbot, setup_bridge):
    from controller_config.views.claude_status_settings import ClaudeStatusSettings
    bridge, gateway, _ = setup_bridge
    widget = ClaudeStatusSettings(bridge)
    qtbot.addWidget(widget)
    widget.resize(1000, 460)
    widget.show()
    for index in range(7):
        bridge.consume(event(str(index)))
    assert "未映射" in widget.sessions.text()
    assert "Key7" in widget.sessions.text()
    assert widget.disable_button.isEnabled()
    assert not widget.enable_button.isEnabled()
    assert widget.manage_button.isVisible()
    assert not widget.retry_button.isVisible()
    assert not widget.management.isVisible()
    widget.manage_button.click()
    assert widget.management.isVisible()
    assert widget.disable_button.isVisible()

    bridge._fault = True
    widget.refresh()
    assert widget.retry_button.isVisible()
    assert not widget.manage_button.isVisible()


def test_status_settings_english_labels_fit_and_live_state_is_translated(qapp, qtbot, setup_bridge, tmp_path):
    from PySide6.QtCore import QSettings
    from controller_config.i18n import LanguageManager
    from controller_config.views.claude_status_settings import ClaudeStatusSettings
    manager = LanguageManager(qapp, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat),
                              initial_language="en_US", persist=False)
    bridge, gateway, _ = setup_bridge
    widget = ClaudeStatusSettings(bridge)
    qtbot.addWidget(widget)
    widget.resize(1000, 480)
    widget.show()
    try:
        qtbot.waitUntil(lambda: widget.enable_button.text() == "Enable Status…")
        bridge.consume(event(name="PermissionRequest"))
        assert "Awaiting approval" in widget.sessions.text()
        bridge.consume(event("idle-session", name="SessionStart"))
        assert "Idle" in widget.sessions.text()
        for button in (widget.detect, widget.enable_button, widget.disable_button, widget.retry_button):
            assert widget.rect().contains(button.geometry())
    finally:
        manager.set_language("zh_CN")
