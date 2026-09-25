from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLineEdit, QPushButton, QSizePolicy

from controller_config.diagnostics import DiagnosticsSession
from controller_config.models import AppState, ScreenModel
from controller_config.transport.demo import _power_v2_snapshot
from test_session_recovery import session


@pytest.mark.parametrize('status, expected', [
    ({'battery': 0, 'battery_valid': True}, 0),
    ({'battery': 100, 'battery_valid': True}, 100),
    ({'battery': 73, 'battery_valid': True}, 73),
    ({'battery': None, 'battery_valid': False}, None),
    ({'battery': 73, 'battery_valid': False}, None),
    ({}, None),
    ({'battery': True, 'battery_valid': True}, None),
    ({'battery': 101, 'battery_valid': True}, None),
])
def test_battery_requires_valid_percentage(contract, status, expected):
    snapshot = replace(_power_v2_snapshot(contract, read_only=False), status=status)
    assert snapshot.battery_percent == expected


@pytest.mark.parametrize('port', ['/dev/cu.test', 'ble:test-device'])
def test_battery_updates_in_place_and_disconnect_hides_stale_value(session, port):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, port_name=port))
    window._select_physical_control('key.9')
    editor = window.findChild(QLineEdit, 'mappingShortNameEditor')
    editor.setText('Unsaved')
    label = window.findChild(QLabel, 'deviceBatterySummary')
    gateway.status_updated.emit({**vm.model.snapshot.status,
                                 'battery': 73, 'battery_valid': True, 'is_charging': None})
    assert window.findChild(QLineEdit, 'mappingShortNameEditor') is editor
    assert editor.text() == 'Unsaved'
    assert window.findChild(QLabel, 'deviceBatterySummary') is label
    assert label.text() == '电量 73%'
    assert '正在充电' not in label.text()
    gateway.status_updated.emit({**vm.model.snapshot.status,
                                 'battery': None, 'battery_valid': False})
    assert label.text() == '电量 —'
    assert '暂未' in label.toolTip()
    gateway.status_updated.emit({**vm.model.snapshot.status,
                                 'battery': 100, 'battery_valid': True})
    assert label.text() == '电量 100%'
    gateway.disconnected.emit('test disconnect')
    assert window.findChild(QLabel, 'deviceBatterySummary').text() == '电量 — · 已断开'


def test_older_firmware_and_english_label(session):
    window, vm, gateway, snapshot, _ = session
    status = {k: v for k, v in snapshot.status.items()
              if k not in {'battery', 'battery_valid', 'is_charging'}}
    gateway.status_updated.emit(status)
    label = window.findChild(QLabel, 'deviceBatterySummary')
    assert label.text() == '电量 —'
    assert '固件未提供' in label.toolTip()
    try:
        window._language_manager.set_language('en_US')
        gateway.status_updated.emit({**status, 'battery': 0, 'battery_valid': True})
        assert window.findChild(QLabel, 'deviceBatterySummary').text() == 'Battery 0%'
    finally:
        window._language_manager.set_language('zh_CN')


def test_device_card_prioritizes_active_connection_over_bluetooth_management(session):
    window, _vm, gateway, snapshot, _ = session
    window.resize(1280, 720)
    window.show()
    QApplication.processEvents()

    card = window.findChild(QFrame, 'deviceContextCard')
    assert card is not None
    assert card.findChild(QLabel, 'devicePanelLive').text() == '已连接'
    assert card.findChild(QLabel, 'devicePanelLive').property('connectionState') == 'connected'
    assert card.findChild(QLabel, 'deviceConnectionSummary').text() == 'USB-C · 已连接'
    subtitle = card.findChild(QLabel, 'devicePanelSubtitle')
    assert subtitle.text() == '开发设备 · 未认证'
    assert subtitle.isVisible()
    assert [label.text() for label in card.findChildren(QLabel, 'devicePanelKey')] == ['当前模式']

    bluetooth = card.findChild(QPushButton, 'bleSlotsDisclosure')
    assert bluetooth is not None
    assert bluetooth.text() == '蓝牙与电脑…'
    assert bluetooth.accessibleName() == '打开蓝牙与电脑管理'
    assert bluetooth.property('buttonRole') == 'ghost'
    assert bluetooth.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Maximum

    gateway.snapshot_ready.emit(replace(snapshot, port_name='ble:test-device'))
    card = window.findChild(QFrame, 'deviceContextCard')
    assert card.findChild(QLabel, 'deviceConnectionSummary').text() == '蓝牙 · 已连接'

    gateway.disconnected.emit('test disconnect')
    card = window.findChild(QFrame, 'deviceContextCard')
    assert card.findChild(QLabel, 'devicePanelLive').text() == '已断开'
    assert card.findChild(QLabel, 'devicePanelLive').property('connectionState') == 'disconnected'
    assert card.findChild(QLabel, 'deviceConnectionSummary').text() == '未检测到连接'
    subtitle = card.findChild(QLabel, 'devicePanelSubtitle')
    assert subtitle.text() == '插入 USB，或先在系统蓝牙设置中连接设备。'
    assert subtitle.isVisible()
    assert [label.text() for label in card.findChildren(QLabel, 'devicePanelKey')] == ['上次模式']


def test_diagnostics_does_not_infer_charging_from_usb_or_full_battery(contract):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    for status in ({}, {'battery': 100, 'battery_valid': True, 'is_charging': None}):
        current = replace(snapshot, status=status)
        report = DiagnosticsSession().report(ScreenModel(AppState.READY, snapshot=current))
        assert 'charging' in report['unavailable_fields']
        assert ('battery' in report['unavailable_fields']) == (not status)
