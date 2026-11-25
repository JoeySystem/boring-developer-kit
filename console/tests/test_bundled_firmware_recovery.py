import pytest
from dataclasses import replace

from PySide6.QtWidgets import QPushButton

from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
from controller_config.firmware_update import FirmwareUpdateState
from test_session_recovery import session


@pytest.mark.skip(reason="社区版不内置官方恢复固件 ZIP")
def test_offline_recovery_prepares_signed_package_without_flashing(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, versions={
        **snapshot.versions, 'build_id': 'custom-assembly-20260918.02',
    }))
    vm._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.UNPUBLISHED))
    vm.navigate('firmware')
    qtbot.waitUntil(lambda: window.findChild(QPushButton, 'prepareOfficialRecovery').isVisible())
    button = window.findChild(QPushButton, 'prepareOfficialRecovery')
    assert button is not None and button.isVisible() and button.isEnabled()
    before = len(gateway.commands)
    button.click()
    qtbot.waitUntil(lambda: vm.firmware_update.package is not None)
    package = vm.firmware_update.package
    assert package.source_kind == 'official'
    assert package.build_id == '20261007.01-gae3b06fb-dirty'
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert not any(c.name.startswith('FW_') for c in gateway.commands[before:])
    assert window.findChild(QPushButton, 'startFirmwareUpdate') is not None


def test_recovery_not_offered_for_another_hardware(session):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, identity={
        **snapshot.identity, 'hardware_id': 'different-hardware',
    }))
    vm.navigate('firmware')
    assert window.findChild(QPushButton, 'prepareOfficialRecovery') is None
