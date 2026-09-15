from dataclasses import replace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from controller_config.desktop_update import UpdateStatus
from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
from controller_config.update_reminders import UpdateReminders
from test_desktop_update_ui import update_session
from test_session_recovery import session
from test_firmware_reminder_ui import offer, trust_test_signing_key


def test_navigation_update_downloads_then_installs_once(update_session, qtbot, tmp_path):
    window, vm, _, _, _, ui, updater, _ = update_session
    ui.reminders = UpdateReminders(QSettings(str(tmp_path / 'reminders.ini'), QSettings.IniFormat))
    button = window._nav_buttons['settings']
    updater.publish('available', version='0.2.0')
    assert button.text() == '更新'
    assert ui.row.isHidden()
    button.click()
    assert updater.calls == ['download']
    QApplication.processEvents()
    assert button.property('desktopUpdateProgress') == 25
    assert button.text() == '25%'
    assert ui.progress.isHidden()
    assert ui.row.isHidden()
    image = button.grab().toImage()
    scale_x = image.width() / button.width()
    scale_y = image.height() / button.height()
    filled = image.pixelColor(int(button.width() * .12 * scale_x), int(button.height() * .5 * scale_y))
    remaining = image.pixelColor(int(button.width() * .88 * scale_x), int(button.height() * .5 * scale_y))
    assert filled.blue() > remaining.blue() + 80
    button.click()  # Repeated clicks while downloading must not start another request.
    assert updater.calls == ['download']
    updater.publish('downloading', version='0.2.0', received=75, total=100)
    assert button.property('desktopUpdateProgress') == 75
    assert button.text() == '75%'
    updater.publish('ready', version='0.2.0')
    qtbot.waitUntil(lambda: updater.calls == ['download', 'install'])
    ui.show_status(updater.status)
    assert updater.calls == ['download', 'install']


def test_firmware_dot_and_simultaneous_desktop_offer(update_session, tmp_path):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    fw, snap = offer((window, vm, gateway, snapshot, store))
    button = window._nav_buttons['settings']
    assert button.property('firmwareUpdateAvailable') is True
    assert fw.row.isHidden()
    assert button.text() == '设置'
    button.click()
    assert vm.page == 'firmware'
    updater.publish('available', version='0.2.0')
    assert button.text() == '更新'
    assert button.property('firmwareUpdateAvailable') is True
    assert not button.firmware_notice_visible()
    gateway.disconnected.emit('test disconnect')
    assert button.property('firmwareUpdateAvailable') is False
    assert button.text() == '更新'
    updater.publish('current')
    assert button.text() == '设置'


def test_navigation_snooze_and_custom_firmware(update_session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    ui.reminders = UpdateReminders(QSettings(str(tmp_path / 'pause.ini'), QSettings.IniFormat))
    button = window._nav_buttons['settings']
    updater.publish('available', version='0.2.0')
    ui.snooze_update()
    assert button.text() == '设置'
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('checking'))
    ui.check()
    updater.publish('available', version='0.2.0')
    assert button.text() == '更新'
    fw, snap = offer((window, vm, gateway, snapshot, store))
    gateway.snapshot_ready.emit(replace(snap, versions={**snap.versions, 'build_id':'custom-demo-01'}))
    assert button.property('firmwareUpdateAvailable') is False


def test_navigation_cancel_does_not_restart_and_ready_needs_explicit_intent(update_session, qtbot):
    window, _, _, _, _, ui, updater, _ = update_session
    button = window._nav_buttons['settings']
    updater.publish('available', version='0.2.0')
    button.click()
    updater.cancel()
    updater.publish('ready', version='0.2.0')
    qtbot.wait(30)
    assert 'install' not in updater.calls
    button.click()
    assert updater.calls[-1] == 'install'


def test_navigation_ready_respects_existing_restart_guard(update_session, qtbot, monkeypatch):
    window, _, _, _, _, ui, updater, _ = update_session
    monkeypatch.setattr(ui, 'blocked_reason', lambda: '设备写入尚未结束')
    updater.publish('available', version='0.2.0')
    window._nav_buttons['settings'].click()
    updater.publish('ready', version='0.2.0')
    qtbot.waitUntil(lambda: 'install' in updater.calls)
    assert not ui.restart_prepared
    assert window.isEnabled()
    assert ui.status.state == 'ready'
    assert ui.message.text() == '设备写入尚未结束'
    assert not ui.row.isHidden()
    qtbot.wait(30)
    assert updater.calls.count('install') == 1


def test_navigation_keeps_positions_and_settings_shortcut(update_session, qtbot):
    from PySide6.QtCore import Qt
    window, vm, _, _, _, ui, updater, _ = update_session
    window.show()
    qtbot.wait(150)  # Let the initial responsive layout settle before comparing offers.
    for language in ('zh_CN', 'en_US', 'ja_JP'):
        window._language_manager.set_language(language)
        for compact in (False, True):
            button = window._nav_buttons['settings']
            button.setProperty('compactNavigation', compact)
            updater.publish('current')
            button.set_active(False)
            qtbot.wait(100)  # Settle parent layouts after changing locale/compact mode.
            before = [(b.mapTo(window, b.rect().topLeft()), b.size()) for b in window._nav_buttons.values()]
            updater.publish('available', version='0.2.0')
            button.set_active(False)
            qtbot.wait(100)  # Settle parent layouts after changing locale/compact mode.
            assert button.text() == window._language_manager.translate('更新')
            assert [(b.mapTo(window, b.rect().topLeft()), b.size()) for b in window._nav_buttons.values()] == before, (language, compact)
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest
    window.raise_()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    assert window._nav_buttons['settings'].isEnabled()
    QTest.keySequence(window, QKeySequence('Ctrl+5'))
    qtbot.waitUntil(lambda: vm.page == 'settings')
    assert updater.calls == []  # The settings shortcut must never trigger a restart.


def test_navigation_restart_preparation_survives_device_refresh(update_session, qtbot, monkeypatch):
    window, vm, _, _, _, ui, updater, _ = update_session
    monkeypatch.setattr(vm, 'stop_lighting_preview', lambda **_: vm.changed.emit(vm.model))
    updater.publish('available', version='0.2.0')
    window._nav_buttons['settings'].click()
    updater.publish('ready', version='0.2.0')
    qtbot.waitUntil(lambda: updater.status.state == 'installing')
    assert ui.restart_prepared
    assert not window.isEnabled()
