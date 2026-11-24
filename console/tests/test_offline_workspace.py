"""Disconnected work remains editable without representing cached data as live."""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QPlainTextEdit, QWidget

from controller_config.models import AppState
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.views.mode_page import ModeStage
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.main_window import MainWindow
from controller_config.viewmodels.main import MainViewModel
from controller_config.transactions import ConfigTransactionState
from test_session_recovery import session
from test_write_transaction import FakeWriteGateway, _ack


@pytest.mark.parametrize("port,message", [
    ("", "配置读取失败；请重新连接设备。"),
    # The port never opened (no GET_CONFIG yet): the message says the link failed,
    # not that the configuration was unreadable (see connection_controller).
    ("ble:test", "蓝牙连接失败；请重新连接，或使用 USB 连接设备。"),
    ("/dev/tty.test", "USB 连接失败；请重新连接，仍失败时可重新插拔 USB。"),
])
def test_first_failure_keeps_preview_navigation_and_collapsed_details(qtbot, contract, port, message):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        if port:
            vm.connect_candidate(port)
        gateway.failure.emit(BootstrapKind.READ_FAILED, '无法建立蓝牙配置连接', 'Remote device cannot be found')
        qtbot.wait(30)
        assert vm.model.snapshot is None and vm.draft is None
        assert window.findChild(QWidget, 'devicePreviewPage') is not None
        assert len(window.findChildren(QPushButton, 'controlKey')) == 12
        assert all('未映射' not in label.toolTip()
                   for label in window.findChildren(QLabel, 'keycapInscription'))
        assert window.findChild(QLabel, 'deviceBatterySummary') is None
        assert window._connection_message.text() == message
        visible = {
            label.text()
            for label in window.findChildren(QLabel)
            if label.isVisible()
        }
        # A read failure titles the card 配置未能读取 and repeats the message under it.
        assert '配置未能读取' in visible
        assert message in visible
        retry = window.findChild(QPushButton, 'connectDeviceButton')
        assert retry.isVisible() and retry.isEnabled() and retry.text() == '重新连接并读取配置'
        assert not any('按键配置 · 设置按键' in text for text in visible)
        assert not window._connection_terminal.isVisible()
        details = window.findChild(QPlainTextEdit, 'connectionDetails')
        assert not details.isVisible()
        window.findChild(QPushButton, 'connectionDetailsToggle').click()
        assert 'Remote device cannot be found' in details.toPlainText()
        assert details.isVisible()
        assert all(button.isEnabled() for button in window._nav_buttons.values())
        vm.navigate('settings')
        assert window._view_model.page == 'settings'
        assert gateway.commands == []
    finally:
        vm.shutdown()


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def test_mapping_survives_relink_no_device_and_failure_without_device_commands(session):
    window, vm, gateway, snapshot, _ = session
    snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "normal"})
    gateway.snapshot_ready.emit(snapshot)
    window._select_physical_control('key.8')
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('离线编辑')
    draft = vm.draft
    gateway.disconnected.emit('unplugged')
    vm.refresh()
    gateway.candidates_found.emit(())
    vm.connect_candidate('ble:test')
    gateway.progress.emit('正在读取：AUTH_CHALLENGE')
    gateway.failure.emit(BootstrapKind.READ_FAILED, '连接失败', 'remote missing')
    assert vm.model.snapshot is snapshot
    assert vm.draft is draft
    assert window.findChild(QLineEdit, 'mappingShortNameEditor').text() == '离线编辑'
    assert window._selected_control_id == 'key.8'
    bar = _stage(window).bar
    assert not bar.save.isVisible() and not bar.state().can_save
    assert bar.state().right == 'offline' and bar.state().text == '没连上设备，连上以后再保存'
    commands = list(gateway.commands)
    _stage(window).device.choose('key.9')   # moving on keeps the typing in the local draft
    assert vm.draft.mapping(snapshot.active_profile_id, 'key.8')['short_name'] == '离线编辑'
    bar = _stage(window).bar
    assert bar.status_text.text() == '没连上设备，连上以后再保存' and not bar.save.isVisible()
    assert gateway.commands == commands
    with pytest.raises(ValueError, match='不可写入'):
        vm.prepare_device_write()
    gateway.snapshot_ready.emit(snapshot)
    save = _stage(window).bar.save
    assert save.isVisible() and save.isEnabled()
    assert gateway.commands == commands  # Reconnection never applies the draft.


def test_unsaved_mapping_is_not_restored_into_another_device(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "normal"})
    gateway.snapshot_ready.emit(snapshot)
    window._select_physical_control('key.8')
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('Only A')
    vm.refresh()
    serial_b = 'CP01-001122334455'
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, 'serial': serial_b}))
    qtbot.waitUntil(lambda: vm.draft.serial == serial_b, timeout=1000)
    # The found clip's ending (about 1.6 s) plays before the Mode page comes back.
    qtbot.waitUntil(lambda: window._connection_transition_model is None, timeout=3500)
    qtbot.waitUntil(
        lambda: (
            (editor := window.findChild(QLineEdit, 'mappingShortNameEditor'))
            is not None
            and editor.text() != 'Only A'
        ),
        timeout=1000,
    )
    assert window.findChild(QLineEdit, 'mappingShortNameEditor').text() != 'Only A'
    assert not vm.draft.is_dirty


def test_unsaved_preferences_survive_disconnect_retry_and_reconnect(session):
    window, vm, gateway, snapshot, _ = session
    vm.navigate('lighting')
    editor = window.findChild(PreferencesEditor)
    editor._haptic_enabled.setChecked(not editor._haptic_enabled.isChecked())
    values = editor.values()
    for action in (lambda: gateway.disconnected.emit('unplugged'), vm.refresh,
                   lambda: gateway.failure.emit(BootstrapKind.READ_FAILED, '连接失败', 'remote missing')):
        action()
        assert window.findChild(PreferencesEditor).values() == values
        assert window.findChild(QPushButton, 'savePreferencesToDevice') is None  # ⑥ 存为本地草稿 only
        assert not window.findChild(QWidget, 'lightingPreviewEnabled').isEnabled()
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(PreferencesEditor).values() == values
    assert window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    assert not vm.draft.is_dirty
    # Leave through a local save, so teardown does not ask about pending inputs.
    window._save_banner.save_draft_requested.emit()


def test_relink_cannot_confirm_a_previously_validated_write(session):
    window, vm, gateway, snapshot, _ = session
    vm.rename_profile(snapshot.active_profile_id, 'candidate')
    vm.prepare_device_write()
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    assert vm.write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION
    vm.refresh()
    confirm = window.findChild(QPushButton, 'confirmConfigurationWrite')
    assert confirm is None or not confirm.isVisibleTo(window) or not confirm.isEnabled()
    assert vm.write_transaction.state is ConfigTransactionState.FAILED
    commands = list(gateway.commands)
    with pytest.raises(ValueError, match='没有等待确认'):
        vm.confirm_device_write()
    assert gateway.commands == commands


@pytest.mark.parametrize("kind,technical,shows_recovery", [
    # Plain read failures are fixed by reconnecting: no official-recovery note.
    (BootstrapKind.READ_FAILED, "GET_CONFIG timeout", False),
    (BootstrapKind.READ_FAILED, "响应 payload CRC32 不匹配", False),
    # Firmware that cannot authenticate or cannot be maintained keeps the note (design decision).
    (BootstrapKind.AUTHENTICITY_FAILED, "invalid signature", True),
    (BootstrapKind.INCOMPATIBLE, "device_read_not_supported", True),
])
def test_recovery_note_only_for_authentication_or_maintenance_problems(
    qtbot, contract, kind, technical, shows_recovery,
):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        vm.connect_candidate("/dev/tty.test")
        gateway.failure.emit(kind, "连接失败", technical)
        qtbot.wait(30)
        assert window.findChild(QWidget, "devicePreviewPage") is not None
        notes = [label for label in window.findChildren(QLabel, "firmwareRecoveryGuidance")
                 if label.isVisible()]
        assert bool(notes) is shows_recovery, (kind, technical)
        if shows_recovery:
            assert "官方 USB 恢复工具" in notes[0].text()
        assert gateway.commands == []
    finally:
        vm.shutdown()
