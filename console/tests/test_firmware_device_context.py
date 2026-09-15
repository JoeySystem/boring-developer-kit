from test_firmware_release import remember_test_snapshot, trust_test_signing_key
"""Online discovery and downloaded packages belong to the selected device."""
from dataclasses import replace

import pytest

from controller_config.firmware_release import RemoteFirmwareState
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_firmware_release import FakeReleaseSource, _bundle, _release, trust_test_signing_key
from test_firmware_transaction import FirmwareGateway, _booted_status


class CancelTrackingSource(FakeReleaseSource):
    def __init__(self):
        super().__init__()
        self.canceled = 0

    def cancel(self):
        self.canceled += 1


@pytest.fixture
def context(qapp, contract):
    source = CancelTrackingSource()
    gateway = FirmwareGateway()
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    snapshot = remember_test_snapshot(vm, _power_v2_snapshot(contract, read_only=False))
    gateway.snapshot_ready.emit(snapshot)
    yield vm, gateway, source, snapshot
    vm.shutdown()


def _other(snapshot, **values):
    return replace(snapshot, identity={**snapshot.identity, "serial": "SECOND-DEVICE", **values})


def _download(vm, source, contract):
    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))
    vm.download_remote_firmware()
    source.download_completed.emit(bundle)
    assert vm.remote_firmware.state is RemoteFirmwareState.DOWNLOADED
    return bundle


@pytest.mark.parametrize("identity_field", ["serial", "product_id", "hardware_id"])
def test_current_result_is_rechecked_for_changed_device_identity(context, contract, identity_field):
    vm, gateway, source, snapshot = context
    release = _release(_bundle(contract))
    current_a = replace(snapshot, versions={
        **snapshot.versions, "firmware": release.version, "build_id": release.build_id,
    })
    gateway.snapshot_ready.emit(current_a)
    source.release_found.emit(release)
    assert vm.remote_firmware.state is RemoteFirmwareState.CURRENT
    device_b = replace(snapshot, identity={**snapshot.identity, identity_field: "DIFFERENT"})
    device_b = remember_test_snapshot(vm, device_b)
    gateway.snapshot_ready.emit(device_b)
    assert source.canceled == 1
    assert len(source.snapshots) == 2
    assert source.snapshots[-1] is device_b
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert vm.remote_firmware.release is None
    assert vm.firmware_update.package is None


def test_device_change_cancels_download_and_does_not_accept_old_image(context, contract):
    vm, gateway, source, snapshot = context
    bundle = _bundle(contract)
    source.release_found.emit(_release(bundle))
    vm.download_remote_firmware()
    device_b = _other(snapshot)
    gateway.snapshot_ready.emit(device_b)
    assert source.canceled == 1
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    source.download_completed.emit(bundle)
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert vm.remote_firmware.release is None
    assert vm.firmware_update.package is None
    assert not any(command.name == "FW_BEGIN" for command in gateway.commands)


@pytest.mark.parametrize("already_completed", [False, True])
def test_downloaded_or_completed_package_is_not_assigned_to_next_device(context, contract, already_completed):
    vm, gateway, source, snapshot = context
    _download(vm, source, contract)
    if already_completed:
        vm._finish_firmware_update(FirmwareUpdateState.COMPLETED, "Device A verified")
    gateway.snapshot_ready.emit(_other(snapshot))
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert vm.remote_firmware.release is None
    assert vm.firmware_update.package is None
    assert vm.firmware_update.state is FirmwareUpdateState.IDLE
    assert source.canceled == 1
    assert source.snapshots[-1].identity["serial"] == "SECOND-DEVICE"


def test_same_device_reboot_retains_package_and_completes_confirmation(context, contract):
    vm, gateway, source, snapshot = context
    _download(vm, source, contract)
    package = vm.firmware_update.package
    vm.start_firmware_update()
    vm._set_firmware_update(replace(
        vm.firmware_update, state=FirmwareUpdateState.WAITING_RECONNECT,
        target_partition="ota_1",
    ))
    gateway.snapshot_ready.emit(replace(snapshot, versions={
        **snapshot.versions, "firmware": package.version, "build_id": package.build_id,
    }))
    assert vm.firmware_update.state is FirmwareUpdateState.VERIFYING
    assert vm.firmware_update.package is package
    assert source.canceled == 0
    assert len(source.snapshots) == 1
    gateway.command_completed.emit("FW_STATUS", _booted_status(package))
    assert vm.firmware_update.state is FirmwareUpdateState.COMPLETED
    assert vm.remote_firmware.state is RemoteFirmwareState.CURRENT


def test_different_device_during_reboot_stops_transfer_then_rechecks(context, contract):
    vm, gateway, source, snapshot = context
    _download(vm, source, contract)
    vm.start_firmware_update()
    vm._set_firmware_update(replace(
        vm.firmware_update, state=FirmwareUpdateState.WAITING_RECONNECT,
        target_partition="ota_1",
    ))
    before = len(gateway.commands)
    gateway.snapshot_ready.emit(_other(snapshot))
    assert vm.firmware_update.state is FirmwareUpdateState.FAILED
    assert "另一台设备" in vm.firmware_update.message
    assert vm.firmware_update.package is None
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert not any(command.name.startswith("FW_") for command in gateway.commands[before:])
