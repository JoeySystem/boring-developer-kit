from dataclasses import replace
from PySide6.QtCore import QSettings
from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareRelease, RemoteFirmwareState
from controller_config.update_reminders import UpdateReminders
from test_session_recovery import session
from test_firmware_release import trust_test_signing_key, _TEST_SIGNING_KEY
from controller_config.firmware_signature import sign_manifest


def offer(session, state=RemoteFirmwareState.AVAILABLE, build='20260913.02-gabcdef12', version='0.3.0-alpha.1'):
    window, vm, gateway, original, _ = session
    snap = replace(original, versions={**original.versions,'firmware':'0.3.0-alpha.1','build_id':'20260913.01-gabcdef12'})
    vm._firmware_release_history.remember(sign_manifest({"version":snap.versions["firmware"], "build_id":snap.versions["build_id"], **{k:snap.identity[k] for k in ("product_id", "hardware_id")}}, _TEST_SIGNING_KEY))
    gateway.snapshot_ready.emit(snap)
    vm._remote_firmware_device = tuple(str(snap.identity[k]) for k in ('serial','product_id','hardware_id'))
    release = RemoteFirmwareRelease({'version':version, 'build_id':build,'size':123, **{k:snap.identity[k] for k in ('product_id','hardware_id')}},'https://example.test/manifest.json','https://example.test/a.bin')
    vm._set_remote_firmware(RemoteFirmwareCheck(state=state, release=release))
    return window._firmware_update_ui, snap


def test_available_snooze_manual_expiry_and_downloaded(session,tmp_path):
    ui, snap = offer(session)
    now=[1000.]
    ui.reminders=UpdateReminders(QSettings(str(tmp_path/'pause.ini'),QSettings.IniFormat),clock=lambda:now[0])
    ui.refresh()
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True
    ui.snooze.click()
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is False
    ui.reveal()
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True
    ui.snooze.click()
    now[0]+=86400
    ui.refresh()
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True
    offer(session, state=RemoteFirmwareState.DOWNLOADED)
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True
    assert '安装' in ui.action.text()


def test_current_older_custom_disconnected_do_not_offer(session):
    window,vm,gateway,original,_=session
    for build in ('20260913.01-gabcdef12','20260912.09-gabcdef12'):
        ui,snap=offer(session,build=build)
        assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is False
    ui,snap=offer(session)
    gateway.disconnected.emit('test disconnect')
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is False
    gateway.snapshot_ready.emit(replace(snap,versions={**snap.versions,'build_id':'custom-dev-001'}))
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is False


def test_device_b_not_suppressed_by_a(session,tmp_path):
    window,vm,gateway,original,_=session
    ui,snap=offer(session)
    ui.reminders=UpdateReminders(QSettings(str(tmp_path/'pause.ini'),QSettings.IniFormat))
    ui.snooze.click()
    b=replace(snap,identity={**snap.identity,'serial':'DEVICE-B'})
    gateway.snapshot_ready.emit(b)
    vm._remote_firmware_device=tuple(str(b.identity[k]) for k in ('serial','product_id','hardware_id'))
    vm._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.AVAILABLE,release=RemoteFirmwareRelease({'version':'0.3.0-alpha.1','build_id':'20260913.02-gabcdef12','size':123},'https://example.test/manifest.json','https://example.test/a.bin')))
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True


def test_manual_check_bypasses_pause_after_async_response(session,tmp_path):
    window,vm,_,_,_=session
    ui,_=offer(session)
    ui.reminders=UpdateReminders(QSettings(str(tmp_path/'pause.ini'),QSettings.IniFormat))
    ui.snooze.click()
    release=vm.remote_firmware.release
    ui.reveal()
    vm._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.CHECKING))
    vm._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.AVAILABLE,release=release))
    assert ui.window._nav_buttons['settings'].property('firmwareUpdateAvailable') is True


def test_opening_offer_acknowledges_only_that_device_and_target_for_session(session, qtbot):
    window, vm, gateway, _, _ = session
    window.show()
    ui, snap = offer(session)
    button = window._nav_buttons['settings']

    assert button.firmware_notice_visible()
    button.click()
    assert vm.page == 'settings'
    assert button.property('firmwareUpdateAvailable') is True
    qtbot.waitUntil(lambda: ui.action.isVisible())
    ui.action.click()
    assert vm.page == 'firmware'
    assert button.property('firmwareUpdateAvailable') is False
    ui.refresh()
    assert button.property('firmwareUpdateAvailable') is False

    # Downloading the release does not make the same reminder return.
    offer(session, state=RemoteFirmwareState.DOWNLOADED)
    assert button.property('firmwareUpdateAvailable') is False

    # A newer target is a new reminder even during the same app session.
    offer(session, build='20260913.03-gabcdef12')
    assert button.property('firmwareUpdateAvailable') is True

    # Acknowledging device A must not hide the same release for device B.
    ui.open_update()
    device_b = replace(snap, identity={**snap.identity, 'serial': 'DEVICE-B'})
    gateway.snapshot_ready.emit(device_b)
    vm._remote_firmware_device = tuple(
        str(device_b.identity[key]) for key in ('serial', 'product_id', 'hardware_id')
    )
    vm._set_remote_firmware(RemoteFirmwareCheck(
        state=RemoteFirmwareState.AVAILABLE,
        release=RemoteFirmwareRelease(
            {
                'version': '0.3.0-alpha.1',
                'build_id': '20260913.03-gabcdef12',
                'size': 123,
                'product_id': device_b.identity['product_id'],
                'hardware_id': device_b.identity['hardware_id'],
            },
            'https://example.test/manifest.json',
            'https://example.test/a.bin',
        ),
    ))
    assert button.property('firmwareUpdateAvailable') is True
