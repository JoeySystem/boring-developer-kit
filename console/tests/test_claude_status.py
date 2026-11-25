from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QObject, Signal

from controller_config.agent_status import SET_AGENT_STATUS, validate_agent_response
from controller_config.claude_hooks import ClaudeHookInstallation
from controller_config.claude_status import ClaudeStatusBridge
from controller_config.protocol.contract import ContractError
from controller_config.transport.demo import _power_v2_snapshot, DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.prompt_library import PromptLibraryStore
from controller_config.workflows import WorkflowStore
from controller_config.automation import AutomationStore
from controller_config.models import AppState
from notice_helpers import answer_questions


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


@pytest.mark.parametrize("supported,readonly", [(True, False), (False, False), (True, True)])
def test_disabled_status_survives_disconnect_and_fresh_binding(qtbot, contract, tmp_path, supported, readonly):
    gateway = Gateway()
    bridge = ClaudeStatusBridge(gateway, installation=ClaudeHookInstallation(
        tmp_path / "settings.json", tmp_path / "endpoint.json"))
    snapshot = _power_v2_snapshot(contract, read_only=readonly)
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities["features"]["claude_code_status"] = supported
    snapshot = replace(snapshot, capabilities=capabilities)

    bridge.unbind(clear=True)
    bridge.bind(snapshot)
    assert bridge.message == "未启用 Claude Code 状态联动"
    assert not bridge.enabled
    assert not gateway.commands
    assert not bridge.installation.record_path.exists()
    assert not bridge.receiver.endpoint_path.exists()

    bridge.unbind(clear=True)
    assert bridge.message == "未启用 Claude Code 状态联动"
    assert not gateway.commands


def test_viewmodel_connect_and_refresh_keep_disabled_status(qtbot, contract, tmp_path):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(gateway, contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        automation_store=AutomationStore(tmp_path / "automations"))
    vm.claude_status.installation = ClaudeHookInstallation(
        tmp_path / "settings.json", tmp_path / "endpoint.json")
    try:
        vm.start()
        qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
        assert vm.claude_status.message == "未启用 Claude Code 状态联动"

        vm.refresh()
        qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
        assert vm.claude_status.message == "未启用 Claude Code 状态联动"
        assert not vm.claude_status.enabled
    finally:
        vm.shutdown()


# design decision (task): the lights go over the `desktop`
# source through DesktopAgentLink; delivery, retries, Bluetooth and OTA pause
# are the hub's and are tested in test_desktop_agent_hub.py and
# test_claude_desktop_route.py. The bridge itself never sends `claude_code`.

def test_bridge_never_sends_the_old_source_on_any_path(setup_bridge):
    bridge, gateway, snapshot = setup_bridge
    bridge.consume(event())
    bridge._tick()
    bridge.bind(replace(snapshot, port_name="ble:BORING-MIST"))
    bridge.set_paused(True)
    bridge.set_paused(False)
    bridge.retry()
    bridge.unbind(clear=True)
    assert gateway.commands == []
    assert bridge.message == "状态联动已开启"


def test_released_slot_is_free_at_once_for_a_waiting_session(setup_bridge):
    bridge, gateway, _ = setup_bridge
    for index in range(7):
        bridge.consume(event(str(index)))
    assert len(bridge.registry.overflow) == 1
    bridge.consume(event("0", "SessionEnd"))
    # No device ACK is involved any more: the waiting session moves up at once.
    assert bridge.registry.pending_clear_slots == ()
    assert bridge.registry.sessions[0].session_id == "6"
    assert gateway.commands == []


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
    assert gateway.commands == []  # the hub, not the bridge, lights the key
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
    vm.claude_status._notify()
    vm.claude_status.consume(event())
    assert vm.desktop_agents.hub.conversations  # Claude's session reaches the hub
    assert vm.draft.config == initial and not vm.draft.is_dirty
    from controller_config.protocol.bootstrap import BootstrapKind
    vm._on_failure(BootstrapKind.AUTHENTICITY_FAILED, "bad certificate", "test")
    assert not vm.desktop_agents.hub.bound
    vm.claude_status.enabled = False
    vm.shutdown()


def test_hook_application_fast_path_does_not_import_qt(tmp_path):
    result = subprocess.run([sys.executable, "-m", "controller_config.app", "--claude-status-hook", str(tmp_path / "missing")],
                            input="{}", text=True, capture_output=True, timeout=3)
    assert result.returncode == 0 and not result.stdout and not result.stderr


def test_noop_heartbeat_does_not_refresh_settings_and_start_without_hooks_is_read_only(setup_bridge, monkeypatch):
    bridge, gateway, _ = setup_bridge
    bridge._tick()
    changes = []
    bridge.changed.connect(lambda: changes.append(True))
    bridge._tick()
    assert changes == []
    bridge.shutdown()
    monkeypatch.setattr(bridge.installation, "inspect", lambda: pytest.fail("uninstalled startup should not probe CLI"))
    bridge.start()
    assert not bridge.enabled


def test_details_have_no_second_switch_and_copy_the_session_list(qtbot, setup_bridge):
    # 设置 1、3：开关和那一个修复动作在方块上；「了解更多」里没有“启用状态灯…”“管理状态灯”“禁用并移除”。
    # 会话列表（短 ID、占哪颗灯）是调试信息，收进“复制诊断信息”（design decision）。
    from PySide6.QtWidgets import QPushButton
    from controller_config.views.claude_status_settings import ClaudeStatusSettings, claude_diagnostics
    from controller_config.views.settings_wall import ClaudeStatusTile
    bridge, gateway, _ = setup_bridge
    widget = ClaudeStatusSettings(bridge)
    qtbot.addWidget(widget)
    widget.resize(1000, 460)
    widget.show()
    for index in range(7):
        bridge.consume(event(str(index)))
    for name in ("enableClaudeStatus", "manageClaudeStatus", "disableClaudeStatus",
                 "retryClaudeStatus", "repairClaudeStatus", "claudeStatusDetailsToggle"):
        assert widget.findChild(QPushButton, name) is None, name
    assert widget.detect.isVisible() and widget.copy_diagnostics.isVisible()
    copied = claude_diagnostics(bridge)
    assert "未映射" in copied and "Key7" in copied and "enabled: True" in copied

    # A delivery fault: the one fix action is the tile's.
    tile = ClaudeStatusTile(bridge)
    qtbot.addWidget(tile)
    bridge.delivery = type("Delivery", (), {
        "fault": True, "message": lambda self: "x", "retry": lambda self: None,
        "slot_of": lambda self, sid: None})()
    tile.refresh()
    assert not tile.fix.isHidden() and tile.fix.text() == "重试"


def test_status_settings_english_labels_fit(qapp, qtbot, setup_bridge, tmp_path):
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
        qtbot.waitUntil(lambda: widget.detect.text() == "Check Again")
        assert widget.copy_diagnostics.text() == "Copy diagnostics"
        for button in (widget.detect, widget.copy_diagnostics):
            assert widget.rect().contains(button.geometry())
    finally:
        manager.set_language("zh_CN")


@pytest.mark.parametrize("language,coverage", [
    ("zh_CN", "云端会话和普通对话暂不支持"),
    ("en_US", "cloud sessions and regular chats are not supported yet"),
    ("ja_JP", "通常のチャットにはまだ対応していません"),
])
def test_status_light_settings_coverage_in_three_languages(
        qapp, qtbot, setup_bridge, tmp_path, language, coverage):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLabel, QPushButton
    from controller_config.i18n import LanguageManager
    from controller_config.views.claude_status_settings import ClaudeStatusSettings
    manager = LanguageManager(qapp, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat),
                              initial_language=language, persist=False)
    bridge, _, _ = setup_bridge
    widget = ClaudeStatusSettings(bridge)
    qtbot.addWidget(widget)
    try:
        texts = [label.text() for label in widget.findChildren(QLabel)]
        assert any(coverage in text for text in texts)
        rendered = "\n".join(texts + [button.text() for button in widget.findChildren(QPushButton)])
        assert "Claude Code 状态联动" not in rendered and "联动" not in rendered
    finally:
        manager.set_language("zh_CN")


@pytest.mark.parametrize("language,warning", [
    ("zh_CN", "状态灯失效"), ("en_US", "Status lights broken"), ("ja_JP", "ステータスライトが動作していません"),
])
def test_moved_console_shows_broken_hooks_and_one_click_repairs(
        qapp, qtbot, contract, tmp_path, monkeypatch, language, warning):
    # H20 end to end: hooks written by a copy that was later moved; the next
    # launch tells the user and one click points them at this copy.
    from PySide6.QtCore import QSettings
    from controller_config import claude_hooks
    from controller_config.i18n import LanguageManager
    from controller_config.i18n import translate_ui_text as tr
    from controller_config.views.claude_status_settings import ClaudeStatusSettings
    from controller_config.views.settings_wall import ClaudeStatusTile
    monkeypatch.setattr(claude_hooks, "_detect_claude", lambda: (None, None, claude_hooks.NO_CLI_NOTE))
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")

    def place(app):
        app.parent.mkdir(parents=True, exist_ok=True)
        app.write_text("", encoding="utf-8")
        monkeypatch.setattr(claude_hooks, "_launcher_args", lambda endpoint, frozen=None: [
            str(app), "--claude-status-hook", str(endpoint)])

    settings = tmp_path / "claude/settings.json"
    settings.parent.mkdir(parents=True)
    user = {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}
    settings.write_text(json.dumps(user, indent=2) + "\n", encoding="utf-8")
    old_app = tmp_path / "Downloads/BORING Console.app/Contents/MacOS/BORING Console"
    place(old_app)
    ClaudeHookInstallation(settings, tmp_path / "boring/endpoint.json").install()
    old_app.unlink()
    place(tmp_path / "Applications/BORING Console.app/Contents/MacOS/BORING Console")

    manager = LanguageManager(qapp, settings=QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat),
                              initial_language=language, persist=False)
    gateway = Gateway()
    bridge = ClaudeStatusBridge(gateway, installation=ClaudeHookInstallation(
        settings, tmp_path / "boring/endpoint.json"))
    try:
        bridge.start()
        widget = ClaudeStatusSettings(bridge)
        qtbot.addWidget(widget)
        widget.resize(1000, 480)
        widget.show()
        assert warning in widget.status.text()
        # The one-click repair is the tile's single action (the details have no second set).
        tile = ClaudeStatusTile(bridge)
        qtbot.addWidget(tile)
        assert not tile.fix.isHidden() and tile.fix.text() == tr("修复")

        tile.fix.click()
        assert bridge.enabled and not bridge.inspection.needs_repair
        assert warning not in widget.status.text()
        assert tile.fix.isHidden()
        config = json.loads(settings.read_text(encoding="utf-8"))
        assert {k: v for k, v in config.items() if k != "hooks"} == {"model": "opus"}
        assert config["hooks"]["Stop"][0] == user["hooks"]["Stop"][0]
        assert str(old_app) not in settings.read_text(encoding="utf-8")
    finally:
        bridge.shutdown()
        manager.set_language("zh_CN")


def test_desktop_only_machine_can_enable_status_lights_from_settings(
        qtbot, tmp_path, monkeypatch):
    # Only the Claude desktop app, no CLI: the settings page still enables.
    from PySide6.QtWidgets import QMessageBox
    from controller_config import claude_hooks
    from controller_config.views.settings_wall import ClaudeStatusTile
    monkeypatch.setattr(claude_hooks.shutil, "which", lambda _name: None)
    monkeypatch.setattr(claude_hooks, "_cli_candidates", lambda: [])
    monkeypatch.setattr(claude_hooks, "_host_platform", lambda: "macos")
    answer_questions(monkeypatch, lambda *_args, **_kwargs: QMessageBox.Yes)
    bridge = ClaudeStatusBridge(Gateway(), installation=ClaudeHookInstallation(
        tmp_path / "claude/settings.json", tmp_path / "boring/endpoint.json"))
    try:
        bridge.inspect()
        tile = ClaudeStatusTile(bridge)
        qtbot.addWidget(tile)
        assert not bridge.inspection.problem
        assert tile.enabled.isEnabled() and not tile.enabled.isChecked()
        tile.enabled.click()
        assert bridge.enabled and bridge.inspection.installed
        assert claude_hooks.NO_CLI_NOTE in bridge.inspection.note
    finally:
        bridge.shutdown()
