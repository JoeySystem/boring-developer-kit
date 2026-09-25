"""Navigation integration with production backends; no real OS replacement or flash."""
import json

from test_session_recovery import session
from test_desktop_update_ui import update_session
from test_desktop_update_macos import native_driver
from test_desktop_update_windows import update
from controller_config.desktop_update_macos import MacDesktopUpdater


def test_capsule_reaches_native_sparkle_and_restart_guard(update_session, native_driver, qtbot, monkeypatch):
    window, vm, _, _, _, ui, fake, _ = update_session
    fake.changed.disconnect(ui.show_status)
    driver, callbacks, _ = native_driver
    backend = MacDesktopUpdater({}, ui.prepare_restart)
    driver.owner = backend
    ui.attach(backend)
    button = window._nav_buttons['settings']
    callbacks.found_stage_(driver, 0)
    button.click()
    assert callbacks.choice() == -1
    assert vm.page == 'settings'
    ui.action.click()
    assert callbacks.choice() == 1  # Actual escaping Objective-C block accepted download.
    callbacks.progress_(driver)
    assert ui.progress.value() == 37
    monkeypatch.setattr(ui, 'blocked_reason', lambda: '设备写入尚未结束')
    callbacks.ready_(driver)
    assert callbacks.choice() == -1
    ui.action.click()
    qtbot.waitUntil(lambda: ui.message.text() == '设备写入尚未结束')
    assert callbacks.choice() == -1  # OS install choice has NOT been sent.
    assert window.isEnabled()
    monkeypatch.setattr(ui, 'blocked_reason', lambda: '')
    button.click()
    assert callbacks.choice() == -1
    ui.action.click()
    assert callbacks.choice() == 1
    assert backend.status.state == 'installing'
    assert ui.store.load()['workspace']['page'] == vm.page
    assert not window.isEnabled()


def test_capsule_cancel_prevents_native_install(update_session, native_driver, qtbot):
    window, _, _, _, _, ui, fake, _ = update_session
    fake.changed.disconnect(ui.show_status)
    driver, callbacks, _ = native_driver
    backend = MacDesktopUpdater({}, ui.prepare_restart)
    driver.owner = backend
    ui.attach(backend)
    callbacks.found_stage_(driver, 0)
    window._nav_buttons['settings'].click()
    ui.action.click()
    callbacks.progress_(driver)
    ui.cancel_button.click()
    assert callbacks.acknowledged()
    callbacks.lateProgress_(driver)
    assert backend.status.state == 'idle'
    callbacks.ready_(driver)
    qtbot.wait(30)
    assert callbacks.choice() == -1
    assert window.isEnabled()


def test_capsule_windows_verifies_bytes_before_install_handoff(update_session, update, qtbot, monkeypatch):
    from controller_config import desktop_update_windows
    window, vm, _, _, _, ui, fake, _ = update_session
    fake.changed.disconnect(ui.show_status)
    backend, release, payload = update
    backend.prepare_restart = ui.prepare_restart
    ui.attach(backend)
    events = []
    monkeypatch.setattr(desktop_update_windows, '_launch_installer',
                        lambda path, directory: events.append(('launch', path.read_bytes(), ui.restart_prepared)))
    backend.quit_requested.connect(lambda: events.append(('quit',)))
    backend.check()
    backend._network.reply.deliver(json.dumps(release).encode())
    window._nav_buttons['settings'].click()
    assert backend.status.state == 'available'
    ui.action.click()
    assert backend._network.request.url().toString() == release['url']
    assert events == []
    backend._network.reply.deliver(payload)
    qtbot.waitUntil(lambda: backend.status.state == 'ready')
    assert events == []
    window._nav_buttons['settings'].click()
    assert events == []
    ui.action.click()
    assert backend.status.state == 'installing'
    assert events == [('launch', payload, True), ('quit',)]
    assert ui.store.load()['workspace']['page'] == vm.page
    backend._cleanup()


def test_capsule_windows_rejects_tampered_download(update_session, update, qtbot, monkeypatch):
    from controller_config import desktop_update_windows
    window, _, _, _, _, ui, fake, _ = update_session
    fake.changed.disconnect(ui.show_status)
    backend, release, payload = update
    backend.prepare_restart = ui.prepare_restart
    ui.attach(backend)
    events = []
    monkeypatch.setattr(desktop_update_windows, '_launch_installer', lambda *_: events.append('launch'))
    backend.check()
    backend._network.reply.deliver(json.dumps(release).encode())
    window._nav_buttons['settings'].click()
    ui.action.click()
    backend._network.reply.deliver(b'X' + payload[1:])
    qtbot.waitUntil(lambda: backend.status.state == 'failed')
    assert events == []
    assert backend._installer is None
    assert window.isEnabled() and not ui.row.isHidden()
    assert window._nav_buttons['settings'].text() == '固件与系统'
