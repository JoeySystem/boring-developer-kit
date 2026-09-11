"""Disconnected work remains editable without representing cached data as live."""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QPlainTextEdit, QWidget

from controller_config.models import AppState
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.main_window import MainWindow
from controller_config.viewmodels.main import MainViewModel
from controller_config.transactions import ConfigTransactionState
from test_session_recovery import session
from test_write_transaction import FakeWriteGateway, _ack


def test_first_failure_keeps_preview_navigation_and_collapsed_details(qtbot, contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        gateway.failure.emit(BootstrapKind.READ_FAILED, '无法建立蓝牙配置连接', 'Remote device cannot be found')
        assert vm.model.snapshot is None and vm.draft is None
        assert window.findChild(QWidget, 'devicePreviewPage') is not None
        assert len(window.findChildren(QPushButton, 'controlKey')) == 12
        assert all('未映射' not in label.toolTip()
                   for label in window.findChildren(QLabel, 'keycapInscription'))
        assert window.findChild(QLabel, 'deviceBatterySummary') is None
        assert '预览' in window._connection_message.text()
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


def test_mapping_survives_relink_no_device_and_failure_without_device_commands(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.1')
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
    assert window._selected_control_id == 'key.1'
    assert not window.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
    assert '已同步' not in window.findChild(QLabel, 'syncChangeCount').text()
    assert '离线草稿' in window.findChild(QLabel, 'syncDraftState').text()
    commands = list(gateway.commands)
    window.findChild(QPushButton, 'saveMappingDraft').click()
    assert vm.draft.mapping(snapshot.active_profile_id, 'key.1')['short_name'] == '离线编辑'
    assert gateway.commands == commands
    with pytest.raises(ValueError, match='不可写入'):
        vm.prepare_device_write()
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
    assert gateway.commands == commands  # Reconnection never applies the draft.


def test_unsaved_mapping_is_not_restored_into_another_device(session):
    window, vm, gateway, snapshot, _ = session
    window._select_physical_control('key.1')
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('Only A')
    vm.refresh()
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, 'serial': 'CP01-001122334455'}))
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
        assert not window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
        assert not window.findChild(QWidget, 'lightingPreviewEnabled').isEnabled()
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(PreferencesEditor).values() == values
    assert window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    assert not vm.draft.is_dirty
    # Leave through a local save, so teardown does not ask about pending inputs.
    window.findChild(QPushButton, 'savePreferencesDraft').click()


def test_relink_cannot_confirm_a_previously_validated_write(session):
    window, vm, gateway, snapshot, _ = session
    vm.rename_profile(snapshot.active_profile_id, 'candidate')
    vm.prepare_device_write()
    gateway.command_completed.emit('VALIDATE_CONFIG', _ack('VALIDATE_CONFIG'))
    assert vm.write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION
    vm.refresh()
    assert window.findChild(QPushButton, 'confirmConfigurationWrite') is None
    assert vm.write_transaction.state is ConfigTransactionState.FAILED
    commands = list(gateway.commands)
    with pytest.raises(ValueError, match='没有等待确认'):
        vm.confirm_device_write()
    assert gateway.commands == commands
