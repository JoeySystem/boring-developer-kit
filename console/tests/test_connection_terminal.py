from dataclasses import replace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit

from controller_config.models import AppState, ScreenModel
from controller_config.style import tokens
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.connection_terminal import ConnectionTerminal
from controller_config.views.main_window import MainWindow
from controller_config.views.mode_page import ModeStage


def _device(window):
    """The device drawn on the visible Mode page, once it is on screen."""
    stage = next((stage for stage in window.findChildren(ModeStage) if stage.isVisible()), None)
    return stage.device if stage is not None and stage.device.isVisible() else None


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
        qtbot.waitUntil(lambda: (
            (retry := window.findChild(QPushButton, 'connectDeviceButton')) is not None
            and retry.isVisible()
            and retry.isEnabled()
        ))
        assert not window.findChild(QPlainTextEdit, 'connectionDetails').isVisible()
        window.findChild(QPushButton, 'connectionDetailsToggle').click()
        assert '请检查设备身份' in window.findChild(QPlainTextEdit, 'connectionDetails').toPlainText()
        # A new attempt updates the real view model immediately; the visual hold does not gate it.
        vm.start()
        qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
        assert window._connection_terminal is terminal
        # A first connection lets the found clip's ending play (about 1.6 s) first.
        qtbot.waitUntil(lambda: window._connection_transition_model is None, timeout=3500)
        # READY is a model event; Qt can show newly inserted children on the
        # next event-loop turn. Still require controls before the 1100 ms hold.
        qtbot.waitUntil(lambda: _device(window) is not None, timeout=500)
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
        # The found clip's ending plays first (about 1.6 s), then the fade.
        qtbot.waitUntil(lambda: window._connection_transition_model is None
                        and window._connection_fade_target is None, timeout=3500)
        assert window._connection_terminal.present
        qtbot.waitUntil(
            lambda: (device := _device(window)) is not None
            and window.rect().contains(device.mapTo(window, device.key_rect('key.1').center().toPoint())),
            timeout=500,
        )
        qtbot.wait(200)  # Let initial responsive sizing finish during the success hold.
        assert window._connection_terminal.present
        device = _device(window)
        position = device.mapTo(window, QPoint(0, 0)), device.size()
        qtbot.waitUntil(lambda: not window._connection_terminal.present, timeout=2000)
        assert _device(window) is device
        assert (device.mapTo(window, QPoint(0, 0)), device.size()) == position
    finally:
        vm.shutdown()


def _panel(qtbot, reduce_motion):
    panel = ConnectionTerminal(reduce_motion=reduce_motion)
    qtbot.addWidget(panel)
    panel.resize(280, 360)
    panel.show()
    return panel


def test_reduce_motion_lines_appear_at_once_cursor_holds_and_no_fade(qtbot, contract):
    """Console动效盘点 11-19 item 13."""
    still = _panel(qtbot, True)
    still.observe(ScreenModel(AppState.SCANNING))
    still.observe(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
    assert all(row.graphicsEffect() is None for row in still._rows)
    assert not still._blink.isActive() and still._cursor.text() == '> _'
    still.observe(ready_model(contract))
    still._fade_out()  # the hold has run out
    assert not still._fade.state() == still._fade.State.Running
    assert not still.present and still.isHidden()


def test_without_reduce_motion_lines_fade_in_cursor_blinks_and_panel_fades(qtbot, contract):
    moving = _panel(qtbot, False)
    moving.observe(ScreenModel(AppState.SCANNING))
    moving.observe(ScreenModel(AppState.CONNECTING, '正在读取：HELLO'))
    assert all(row.graphicsEffect() is not None for row in moving._rows)
    assert moving._rows[-1]._line_animation.duration() == tokens.MOTION_INSTANT_MS
    assert moving._blink.isActive() and moving._blink.interval() == 550
    moving.observe(ready_model(contract))
    moving._fade_out()
    assert moving._fade.state() == moving._fade.State.Running and moving._fade.duration() == tokens.MOTION_SHORT_MS
    qtbot.waitUntil(lambda: not moving.present, timeout=2000)
