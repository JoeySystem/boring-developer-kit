from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit

from controller_config.models import AppState, ScreenModel
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.connection_terminal import ConnectionTerminal
from controller_config.views.main_window import MainWindow


@pytest.fixture
def terminal(qtbot):
    panel = ConnectionTerminal()
    qtbot.addWidget(panel)
    panel.resize(280, 360)
    panel.show()
    return panel


def ready_model(contract, authenticated=True):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    if authenticated:
        snapshot = replace(snapshot, port_name='/dev/test-usb', trust=DeviceTrust(
            DeviceTrustState.AUTHENTICATED, 'test verified', 'test fixture'))
    return ScreenModel(AppState.READY, snapshot=snapshot)


def test_requested_commands_cannot_claim_authentication_success(terminal):
    terminal.observe(ScreenModel(AppState.SCANNING))
    for command in ['HELLO', 'AUTH_GET_CERTIFICATE', 'AUTH_CHALLENGE', 'GET_CONFIG']:
        terminal.observe(ScreenModel(AppState.CONNECTING, '正在读取：' + command))
    assert '> verifying identity...' in terminal.transcript
    assert '> loading configuration...' in terminal.transcript
    assert not any('verified' in line or 'ready' in line for line in terminal.transcript)
    terminal.observe(ScreenModel(AppState.AUTHENTICITY_FAILED, '证书验证失败'))
    assert terminal.transcript[-1] == '> 证书验证失败'
    assert not terminal._blink.isActive()
    assert not terminal._hold.isActive()
    assert terminal.present


def test_ready_needs_authenticated_snapshot_and_does_not_replay_on_poll(terminal, contract, qtbot):
    terminal.observe(ScreenModel(AppState.CONNECTING, '正在读取：AUTH_CHALLENGE'))
    model = ready_model(contract)
    terminal.observe(model)
    assert '> identity verified' in terminal.transcript
    original = terminal.transcript
    qtbot.wait(120)
    remaining = terminal._hold.remainingTime()
    terminal.observe(model)
    assert terminal.transcript == original
    assert terminal._hold.remainingTime() <= remaining
    qtbot.waitUntil(lambda: not terminal.present, timeout=2000)
    assert not terminal._blink.isActive()


def test_development_session_is_not_reported_as_verified(terminal, contract):
    terminal.observe(ScreenModel(AppState.CONNECTING))
    terminal.observe(ready_model(contract, authenticated=False))
    assert '> development identity' in terminal.transcript
    assert not any('verified' in line for line in terminal.transcript)


def test_failure_interrupts_success_and_retry_drops_old_result(terminal, contract, qtbot):
    terminal.observe(ScreenModel(AppState.CONNECTING))
    terminal.observe(ready_model(contract))
    terminal.observe(ScreenModel(AppState.DISCONNECTED, '蓝牙认证中断'))
    qtbot.wait(1350)
    assert terminal.present
    assert not any('ready' in line or 'verified' in line for line in terminal.transcript)
    assert '> 蓝牙认证中断' in terminal.transcript
    terminal.observe(ScreenModel(AppState.SCANNING))
    assert terminal.transcript == ('> scanning device...',)
    assert not terminal._blink.isActive()  # Rapid reconnects use the quiet presentation.
    terminal.observe(ready_model(contract))
    assert not terminal.present


def test_main_window_reuses_terminal_and_keeps_retry_and_editor_available(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    terminal = window._connection_terminal
    try:
        window.render(ScreenModel(AppState.CONNECTING, '正在读取：AUTH_CHALLENGE'))
        window.render(ScreenModel(AppState.AUTHENTICITY_FAILED, '认证失败', '请检查设备身份'))
        assert window._connection_terminal is terminal
        qtbot.waitUntil(lambda: any(b.isVisible() and b.text() == '重新扫描'
                                   for b in window.findChildren(QPushButton)))
        assert not window.findChild(QPlainTextEdit, 'connectionDetails').isVisible()
        window.findChild(QPushButton, 'connectionDetailsToggle').click()
        assert '请检查设备身份' in window.findChild(QPlainTextEdit, 'connectionDetails').toPlainText()
        # A new attempt updates the real view model immediately; the visual hold does not gate it.
        vm.start()
        qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
        assert window._connection_terminal is terminal
        # READY is a model event; Qt can show newly inserted children on the
        # next event-loop turn. Still require controls before the 1100 ms hold.
        qtbot.waitUntil(lambda: window.findChild(QPushButton, 'controlKey').isVisible(), timeout=500)
        assert terminal.focusPolicy() == Qt.NoFocus
        qtbot.waitUntil(lambda: not terminal.present, timeout=2000)
    finally:
        vm.shutdown()


def test_success_animation_does_not_push_device_controls_out_of_view(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        vm._on_snapshot(ready_model(contract).snapshot)
        qtbot.wait(150)
        assert window._connection_terminal.present
        key = window.findChild(QPushButton, 'controlKey')
        qtbot.waitUntil(lambda: window.rect().contains(key.mapTo(window, key.rect().center())), timeout=500)
        assert window._connection_terminal.present
        position = key.mapTo(window, QPoint(0, 0))
        qtbot.waitUntil(lambda: not window._connection_terminal.present, timeout=2000)
        assert key.mapTo(window, QPoint(0, 0)) == position
    finally:
        vm.shutdown()
