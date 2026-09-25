from dataclasses import replace
import copy

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QInputDialog, QPushButton, QMessageBox

from controller_config.models import AppState, ScreenModel
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.ble_connections import BleConnectionsDialog
from controller_config.views.device_silhouette import DeviceModelCanvas
from test_viewmodel import FakeGateway


@pytest.fixture
def panel(qtbot, contract, tmp_path):
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    settings = QSettings(str(tmp_path / 'computers.ini'), QSettings.IniFormat)
    dialog = BleConnectionsDialog(vm, snapshot, AppState.READY, settings=settings)
    qtbot.addWidget(dialog)
    dialog.show()
    yield dialog, gateway, vm, snapshot, settings
    dialog.reject()
    vm.shutdown()


def update(gateway, snapshot, *, active=2, paired=False, connected=False):
    status = copy.deepcopy(snapshot.status)
    micro = status['codex_micro']
    micro['active_slot'] = active
    micro['slots'][1] = {'slot': 2, 'paired': paired, 'connected': connected}
    gateway.status_updated.emit(status)
    return status


def test_add_requires_status_and_connection_not_ack(panel, qtbot):
    dialog, gateway, vm, snapshot, _ = panel
    assert dialog._rows[1][0].isVisible()
    assert not dialog._rows[2][0].isVisible()
    qtbot.mouseClick(dialog._add, Qt.LeftButton)
    assert gateway.commands[-1].name == 'BLE_SLOT_SELECT'
    assert gateway.commands[-1].payload == {'slot': 2}
    gateway.command_completed.emit('BLE_SLOT_SELECT', {'result': {'slot': 2}})
    assert not dialog._pairing_ready
    update(gateway, snapshot)
    assert dialog._pairing_ready
    assert '蓝牙设置' in dialog._message.text()
    assert '已连接' not in dialog._message.text()
    update(gateway, snapshot, paired=True, connected=True)
    assert dialog._pending is None
    assert dialog._rows[2][2].text() == '已连接'
    assert '可以开始使用' in dialog._message.text()
    assert dialog._add.isEnabled()
    update(gateway, snapshot, paired=True, connected=False)
    assert '可以开始使用' not in dialog._message.text()


def test_disconnect_does_not_report_old_connection_or_success(panel):
    dialog, gateway, vm, snapshot, _ = panel
    dialog._start(2, 'add')
    vm.changed.emit(ScreenModel(AppState.DISCONNECTED, snapshot=snapshot))
    assert not dialog._add.isEnabled()
    assert '无法确认' in dialog._message.text()
    assert dialog._rows[1][2].text() == '连接状态待确认'
    update(gateway, snapshot, paired=True, connected=True)
    assert dialog._rows[2][2].text() == '已连接'


def test_failed_ble_command_does_not_change_config_transaction(panel):
    dialog, gateway, vm, snapshot, _ = panel
    before = vm.write_transaction
    dialog._start(2, 'add')
    gateway.command_failed.emit('BLE_SLOT_SELECT', BootstrapError(
        BootstrapKind.READ_FAILED, '蓝牙暂不可用', 'radio unavailable'))
    assert '蓝牙暂不可用' in dialog._message.text()
    assert dialog._pending is None
    assert dialog._add.isEnabled()
    assert vm.write_transaction == before


def test_timeout_remains_honest_until_later_status_confirms(panel):
    dialog, gateway, vm, snapshot, _ = panel
    dialog._start(2, 'add')
    update(gateway, snapshot)
    dialog._timed_out()
    update(gateway, snapshot)
    assert '暂未确认' in dialog._message.text()
    assert dialog._add.isEnabled()
    update(gateway, snapshot, paired=True, connected=True)
    assert '已连接' in dialog._message.text()


def test_alias_persists_and_forget_requires_readback(panel, monkeypatch, qtbot):
    dialog, gateway, vm, snapshot, settings = panel
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **k: ('办公室电脑', True))
    dialog._rename(1)
    assert dialog._rows[1][1].text() == '办公室电脑'
    reopened = BleConnectionsDialog(vm, snapshot, AppState.READY, settings=settings)
    qtbot.addWidget(reopened)
    assert reopened._name(1) == '办公室电脑'
    reopened.reject()
    dialog._start(1, 'forget')
    gateway.command_completed.emit('BLE_SLOT_CLEAR', {'result': {'slot': 1}})
    assert settings.value(dialog._alias_key(1)) == '办公室电脑'
    status = copy.deepcopy(snapshot.status)
    status['codex_micro']['slots'][0]['paired'] = False
    gateway.status_updated.emit(status)
    assert not settings.contains(dialog._alias_key(1))
    assert '已忘记' in dialog._message.text()


def test_forget_cancel_does_not_send_command(panel, monkeypatch):
    dialog, gateway, vm, snapshot, _ = panel
    monkeypatch.setattr(QMessageBox, 'exec', lambda _: 0)
    before = len(gateway.commands)
    dialog._forget(1)
    assert len(gateway.commands) == before


def test_other_device_cannot_receive_actions_from_old_dialog(panel):
    dialog, gateway, vm, snapshot, _ = panel
    other = replace(snapshot, identity={**snapshot.identity, 'serial': 'other-device'})
    gateway.snapshot_ready.emit(other)
    dialog._add_computer()
    assert gateway.commands == []
    assert not dialog._add.isEnabled()


def test_physical_help_highlights_combination_without_device_commands(panel, monkeypatch):
    dialog, gateway, vm, snapshot, _ = panel
    before = len(gateway.commands)
    def inspect(help_dialog):
        canvas = help_dialog.findChild(DeviceModelCanvas)
        assert {k for k,v in canvas.key_visual_states().items() if v['selected']} == {'key.3', 'key.8'}
        buttons = help_dialog.findChildren(QPushButton, 'bleHelpTarget')
        buttons[2].click()
        assert {k for k,v in canvas.key_visual_states().items() if v['selected']} == {'key.3', 'key.10'}
        return 0
    monkeypatch.setattr(QDialog, 'exec', inspect)
    dialog._show_physical_help()
    assert len(gateway.commands) == before


def test_read_only_and_full_list_offer_no_unsafe_add(panel):
    dialog, gateway, vm, snapshot, _ = panel
    status = copy.deepcopy(snapshot.status)
    for item in status['codex_micro']['slots']:
        item['paired'] = True
    gateway.status_updated.emit(status)
    assert not dialog._add.isVisible()
    assert '已记住 3 台' in dialog._message.text()
    dialog._update_model(ScreenModel(AppState.READ_ONLY, snapshot=replace(snapshot, status=status)))
    assert all(not row[3].isEnabled() for row in dialog._rows.values())
    assert '只读' in dialog._message.text()


def test_pairing_uses_active_broadcast_name_even_after_disconnect(panel):
    dialog, gateway, vm, snapshot, _ = panel
    vm.ble_name._current.result = {'active_name': 'BORING 工作设备', 'saved_name': '下次重启的新名字'}
    dialog._start(2, 'add')
    update(gateway, snapshot)
    assert 'BORING 工作设备' in dialog._message.text()
    assert '下次重启的新名字' not in dialog._message.text()
    dialog._update_model(ScreenModel(AppState.DISCONNECTED))
    assert 'BORING 工作设备' in dialog._message.text()
    assert '无法确认' in dialog._message.text()


def test_readback_clears_alias_only_for_this_device(panel, qtbot):
    dialog, gateway, vm, snapshot, settings = panel
    settings.setValue(dialog._alias_key(1), '办公室电脑')
    other = replace(snapshot, identity={**snapshot.identity, 'serial': 'second-device'})
    other_dialog = BleConnectionsDialog(vm, other, AppState.READY, settings=settings)
    qtbot.addWidget(other_dialog)
    assert other_dialog._name(1) == '电脑 1'
    other_dialog.reject()


def test_model_bridge_exposes_both_highlights(panel, qtbot):
    canvas = DeviceModelCanvas(None, {}, None)
    qtbot.addWidget(canvas)
    from controller_config.views.boring_mist_3d import _DeviceModelBridge
    bridge = _DeviceModelBridge(canvas)
    canvas.set_highlighted_controls(('key.3', 'key.9'))
    assert bridge.keyVisualStates['key.3']['selected']
    assert bridge.keyVisualStates['key.9']['selected']
    assert not bridge.keyVisualStates['key.8']['selected']
    bridge.set_owner(None)


def test_name_read_temporarily_disables_connection_actions(panel):
    dialog, gateway, vm, snapshot, _ = panel
    vm.ble_name._command = 'BLE_NAME_GET'
    vm.ble_name.changed.emit()
    assert not dialog._add.isEnabled()
    assert not dialog._rows[1][3].isEnabled()
    vm.ble_name._command = ''
    vm.ble_name.changed.emit()
    assert dialog._add.isEnabled()
