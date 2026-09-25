from dataclasses import replace
from copy import deepcopy
from pathlib import Path
from PySide6.QtGui import QCloseEvent
from controller_config.build_identity import BuildIdentity
from controller_config.update_recovery import UpdateRecoveryStore

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton

from controller_config.codex_agent_focus import AGENT_CLIENT_KEY, MacChatGPTActivator
from controller_config.i18n import LanguageManager
from controller_config.protocol.device_auth import DeviceTrustState
from controller_config.views.desktop_update import DesktopUpdateUi
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


def make_window(qtbot, contract, tmp_path, mode="codex", language="zh_CN", *, supported=False):
    settings = QSettings(str(tmp_path / "ui.ini"), QSettings.IniFormat)
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(gateway, contract)
    vm.normal_agent.test_commands = []
    vm.normal_agent._send = vm.normal_agent.test_commands.append
    window = MainWindow(vm, onboarding_settings=settings)
    window._language_manager.set_language(language)
    qtbot.addWidget(window)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    capabilities = deepcopy(snapshot.capabilities)
    capabilities["features"]["normal_agent_key_behavior"] = supported
    capabilities["features"]["ble_name"] = False
    snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": mode},
                       capabilities=capabilities,
                       trust=replace(snapshot.trust, state=DeviceTrustState.AUTHENTICATED))
    gateway.snapshot_ready.emit(snapshot)
    window._selected_control_id = "key.1"
    card = window._mapping_editor(snapshot)
    card.setParent(window)
    window._language_manager.retranslate_widget_tree(card)
    return window, card, vm, settings


@pytest.mark.parametrize("language", ["zh_CN", "en_US", "ja_JP"])
def test_normal_notice_has_working_update_entry_without_fake_setting(qtbot, contract, tmp_path, language):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", language)
    assert card.findChild(QComboBox) is None
    assert card.findChild(QPushButton, "applyMappingToDevice") is None
    notice = card.findChild(QLabel, "normalAgentFirmwareNotice").text()
    if language != "zh_CN":
        assert "需要支持" not in notice
    card.findChild(QPushButton, "normalAgentFirmwareUpdate").click()
    assert vm.page == "firmware"
    vm.shutdown()


def test_client_choice_reaches_activator_without_writing_device(qtbot, contract, tmp_path, monkeypatch):
    clients = {"Codex": Path("/Applications/Codex.app"), "ChatGPT": Path("/Applications/ChatGPT.app")}
    monkeypatch.setattr("controller_config.views.main_window.installed_agent_clients", lambda: clients)
    monkeypatch.setattr("controller_config.codex_agent_focus.installed_agent_clients", lambda: clients)
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    window, card, vm, settings = make_window(qtbot, contract, tmp_path)
    config = vm.draft.config.copy()
    selector = card.findChild(QComboBox, "agentDesktopClient")
    selector.setCurrentIndex(selector.findData("ChatGPT"))
    assert settings.value(AGENT_CLIENT_KEY) == "ChatGPT"
    activator = MacChatGPTActivator(settings=settings)
    calls = []
    monkeypatch.setattr(activator._process, "start", lambda *args: calls.append(args))
    activator.activate(2)
    assert calls == [("/usr/bin/open", ["-a", str(clients["ChatGPT"])])]
    assert vm.draft.config == config
    activator.shutdown()
    vm.shutdown()


def test_focus_failure_is_visible_and_reuses_one_dialog(qtbot, contract, tmp_path):
    window, _, vm, _ = make_window(qtbot, contract, tmp_path)
    window.show_agent_focus_error("找不到已选择的桌面应用，请在状态灯按键设置中重新选择。")
    window.show_agent_focus_error("检测到多个桌面应用，请在状态灯按键设置中选择接收设备操作的应用。")
    notices = window.findChildren(QMessageBox, "agentFocusError")
    assert len(notices) == 1 and notices[0].isVisible()
    assert "多个" in notices[0].text()
    notices[0].close()
    vm.shutdown()


def test_focus_error_refreshes_client_choices_in_open_inspector(qtbot, contract, tmp_path, monkeypatch):
    clients = {"Codex": Path("/Applications/Codex.app")}
    monkeypatch.setattr("controller_config.views.main_window.installed_agent_clients", lambda: clients)
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    window, _, vm, _ = make_window(qtbot, contract, tmp_path)
    window.show()
    qtbot.waitUntil(lambda: window._current_mapping_card() is not None, timeout=2000)
    assert window._current_mapping_card().findChild(QComboBox, "agentDesktopClient") is None
    clients["ChatGPT"] = Path("/Applications/ChatGPT.app")
    window.show_agent_focus_error("检测到多个桌面应用，请在状态灯按键设置中选择接收设备操作的应用。")
    assert window._current_mapping_card().findChild(QComboBox, "agentDesktopClient") is not None
    window.findChild(QMessageBox, "agentFocusError").close()
    vm.shutdown()


@pytest.mark.parametrize("language, expected", [("en_US", "Choose a desktop app"), ("ja_JP", "デスクトップアプリを選択")])
def test_client_selector_placeholder_is_translated(qtbot, contract, tmp_path, monkeypatch, language, expected):
    monkeypatch.setattr("controller_config.views.main_window.installed_agent_clients", lambda: {
        "Codex": Path("/Applications/Codex.app"), "ChatGPT": Path("/Applications/ChatGPT.app")})
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, language=language)
    assert card.findChild(QComboBox, "agentDesktopClient").itemText(0) == expected
    vm.shutdown()


def finish_read(vm, value):
    vm.normal_agent.completed("NORMAL_AGENT_BEHAVIOR_GET", {"result": {"behavior": value}})
    if vm.normal_agent.preparing:
        vm.normal_agent.finish_preparing()


def test_normal_choice_applies_then_waits_for_readback_without_rebuilding(qtbot, contract, tmp_path):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    selector = card.findChild(QComboBox, "normalAgentBehavior")
    apply = card.findChild(QPushButton, "normalAgentApply")
    assert not selector.isEnabled() and not apply.isEnabled()
    finish_read(vm, None)
    assert selector.currentData() is None and not apply.isEnabled()
    selector.setCurrentIndex(selector.findData("open_conversation"))
    assert apply.isEnabled()
    apply.click()
    assert vm.normal_agent.test_commands[-1].name == "NORMAL_AGENT_BEHAVIOR_SET"
    assert vm.normal_agent.test_commands[-1].payload == {"behavior": "open_conversation"}
    assert not selector.isEnabled() and not apply.isEnabled()
    vm.normal_agent.completed("NORMAL_AGENT_BEHAVIOR_SET", {"result": {"behavior": "open_conversation"}})
    assert vm.normal_agent.test_commands[-1].name == "NORMAL_AGENT_BEHAVIOR_GET"
    assert apply.text() != "已应用"
    finish_read(vm, "open_conversation")
    assert card.findChild(QComboBox, "normalAgentBehavior") is selector
    assert apply.text() == "已应用" and not apply.isEnabled()
    selector.setCurrentIndex(selector.findData("status_only"))
    assert card.findChild(QLabel, "normalAgentStatus").text() == "尚未应用到设备"
    vm.normal_agent.edit("open_conversation")
    vm.shutdown()


def test_normal_choice_is_shared_when_switching_status_keys(qtbot, contract, tmp_path):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    finish_read(vm, "open_conversation")
    selector = card.findChild(QComboBox, "normalAgentBehavior")
    selector.setCurrentIndex(selector.findData("status_only"))
    window._selected_control_id = "key.2"
    second = window._mapping_editor(vm.model.snapshot)
    second.setParent(window)
    assert second.findChild(QComboBox, "normalAgentBehavior").currentData() == "status_only"
    assert len(vm.normal_agent.test_commands) == 1
    # Discard is confirmed on actual quit, not while navigating between keys.
    vm.normal_agent.edit("open_conversation")
    vm.shutdown()


def test_failed_read_exposes_retry_and_does_not_claim_saved(qtbot, contract, tmp_path):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    vm.normal_agent.completed("NORMAL_AGENT_BEHAVIOR_GET", {"result": {}})
    retry = card.findChild(QPushButton, "normalAgentRead")
    assert not retry.isHidden() and retry.isEnabled()
    assert not card.findChild(QPushButton, "normalAgentApply").isEnabled()
    retry.click()
    assert vm.normal_agent.test_commands[-1].name == "NORMAL_AGENT_BEHAVIOR_GET"
    finish_read(vm, "status_only")
    assert retry.isHidden()
    vm.shutdown()


@pytest.mark.parametrize("language, expected", [("zh_CN", "打开对应对话"),
    ("en_US", "Open the corresponding conversation"), ("ja_JP", "対応する会話を開く")])
def test_normal_behavior_translations_and_shared_client_selector(qtbot, contract, tmp_path, monkeypatch, language, expected):
    monkeypatch.setattr("controller_config.views.main_window.installed_agent_clients", lambda: {
        "Codex": Path("/Applications/Codex.app"), "ChatGPT": Path("/Applications/ChatGPT.app")})
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", language, supported=True)
    finish_read(vm, "open_conversation")
    selector = card.findChild(QComboBox, "normalAgentBehavior")
    assert selector.itemText(selector.findData("open_conversation")) == expected
    assert card.findChild(QComboBox, "agentDesktopClient") is not None
    assert not card.findChild(QComboBox, "agentDesktopClient").parentWidget().isHidden()
    selector.setCurrentIndex(selector.findData("status_only"))
    assert card.findChild(QComboBox, "agentDesktopClient").parentWidget().isHidden()
    vm.normal_agent.edit("open_conversation")
    vm.shutdown()


def test_restart_blocks_pending_behavior_but_accepts_confirmed_status_card(qtbot, contract, tmp_path, monkeypatch):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    ui = DesktopUpdateUi(window, build_identity=BuildIdentity("official"),
                         store=UpdateRecoveryStore(tmp_path / "update.json"))
    monkeypatch.setattr(window, "_current_mapping_card", lambda: card)
    assert "正在保存或读回" in ui.blocked_reason()
    finish_read(vm, "open_conversation")
    vm.normal_agent.edit("status_only")
    assert "请先应用状态灯按键设置" in ui.blocked_reason()
    vm.normal_agent.edit("open_conversation")
    assert "状态灯" not in ui.blocked_reason()
    # A confirmed official key has no shortcut editor; it must not block recovery capture.
    assert "mapping" not in ui.capture_workspace()
    vm.shutdown()


def test_close_waits_for_readback_and_cancel_preserves_pending_choice(qtbot, contract, tmp_path, monkeypatch):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and warnings
    finish_read(vm, "open_conversation")
    vm.normal_agent.edit("status_only")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    assert not window._confirm_discard_normal_agent_choices()
    assert vm.normal_agent.draft == "status_only"
    vm.normal_agent.edit("open_conversation")
    vm.shutdown()


def test_normal_open_behavior_waits_for_preparation_before_showing_applied(qtbot, contract, tmp_path):
    window, card, vm, _ = make_window(qtbot, contract, tmp_path, "normal", supported=True)
    vm.normal_agent.completed("NORMAL_AGENT_BEHAVIOR_GET", {"result": {"behavior": "open_conversation"}})
    apply = card.findChild(QPushButton, "normalAgentApply")
    assert vm.normal_agent.preparing
    assert apply.text() != "已应用" and not apply.isEnabled()
    assert not card.findChild(QComboBox, "normalAgentBehavior").isEnabled()
    assert "正在准备" in card.findChild(QLabel, "normalAgentStatus").text()
    vm.normal_agent.finish_preparing()
    assert apply.text() == "已应用"
    vm.shutdown()
