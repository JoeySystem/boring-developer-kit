import pytest
from PySide6.QtCore import QSettings
from controller_config.update_reminders import UpdateReminders
from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
from test_desktop_update_ui import update_session, _restart_destination
from test_session_recovery import session
from test_firmware_reminder_ui import offer, trust_test_signing_key


@pytest.mark.parametrize('state', ['available', 'downloading', 'verifying', 'ready', 'installing'])
def test_desktop_priority_hides_dot_but_preserves_firmware_access(update_session, state):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    fw, _ = offer((window, vm, gateway, snapshot, store))
    button = window._nav_buttons['settings']
    updater.publish(state, version='0.2.0')
    fw.refresh()  # Device polls must not bring the dot back over a desktop update.
    assert button.accessibleName() == '固件与系统'
    assert button.property('firmwareUpdateAvailable') is True
    assert not button.firmware_notice_visible()
    assert '设备固件有更新' not in button.toolTip()
    fw.open_update()
    assert vm.page == 'firmware'


def test_failure_and_snooze_release_priority(update_session, tmp_path):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    ui.reminders = UpdateReminders(QSettings(str(tmp_path / 'pause.ini'), QSettings.IniFormat))
    fw, _ = offer((window, vm, gateway, snapshot, store))
    button = window._nav_buttons['settings']
    updater.publish('available', version='0.2.0')
    button.click()
    ui.action.click()
    updater.publish('failed', version='0.2.0', message='network unavailable')
    assert button.text() == '固件与系统'
    assert button.firmware_notice_visible()
    assert not ui.row.isHidden()  # Software retry remains accessible.
    updater.publish('available', version='0.2.0')
    ui.snooze_update()
    assert button.text() == '固件与系统'
    assert button.firmware_notice_visible()
    updater.publish('available', version='0.3.0')
    assert not button.firmware_notice_visible()


def test_disconnect_while_suppressed_cannot_restore_stale_dot(update_session):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    offer((window, vm, gateway, snapshot, store))
    button = window._nav_buttons['settings']
    updater.publish('available', version='0.2.0')
    gateway.disconnected.emit('test disconnect')
    updater.publish('current')
    assert button.text() == '固件与系统'
    assert not button.firmware_notice_visible()


def test_reopened_window_waits_for_fresh_firmware_offer(update_session, contract, qtbot, monkeypatch):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    fw, snap = offer((window, vm, gateway, snapshot, store))
    updater.publish('ready', version='0.2.0')
    reopened, new_vm, new_gateway, new_ui = _restart_destination(
        update_session, contract, qtbot, monkeypatch, snapshot=snap)
    button = reopened._nav_buttons['settings']
    assert button.text() == '固件与系统'
    assert not button.firmware_notice_visible()
    offer((reopened, new_vm, new_gateway, snap, store))
    assert button.firmware_notice_visible()
