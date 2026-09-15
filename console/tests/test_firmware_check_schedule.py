from test_firmware_release import remember_test_snapshot, trust_test_signing_key
"""Automatic discovery is bounded, quiet, and scoped to the connected device."""
from dataclasses import replace

import pytest

from controller_config import viewmodels
from controller_config.firmware_release import FirmwareReleaseError, RemoteFirmwareCheck, RemoteFirmwareState
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.joystick_calibration import CalibrationState
from controller_config.prompt_device import PromptDeviceState
from controller_config.transactions import ConfigTransactionState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_firmware_device_context import CancelTrackingSource
from test_firmware_release import _bundle, _release
from test_firmware_transaction import FirmwareGateway


@pytest.fixture
def setup(qapp, contract):
    source, gateway = CancelTrackingSource(), FirmwareGateway()
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    snapshot = remember_test_snapshot(vm, _power_v2_snapshot(contract, read_only=False))
    yield vm, gateway, source, snapshot
    vm.shutdown()


def test_custom_firmware_connect_skips_automatic_query_without_error(setup):
    vm, _, source, snapshot = setup
    vm._on_snapshot(replace(snapshot, versions={**snapshot.versions, "build_id": "custom-demo-20260913.01"}))
    assert source.snapshots == []
    assert vm.remote_firmware.state is RemoteFirmwareState.IDLE


def test_disconnect_cancels_inflight_discovery_and_ignores_late_result(setup, contract):
    vm, gateway, source, snapshot = setup
    gateway.snapshot_ready.emit(snapshot)
    gateway.disconnected.emit("unplugged")
    assert source.canceled == 1
    source.release_found.emit(_release(_bundle(contract)))
    source.failed.emit(FirmwareReleaseError("old request failed", kind="network"))
    assert vm.remote_firmware.state is RemoteFirmwareState.IDLE
    assert vm.remote_firmware_device is None


def test_six_hour_check_and_manual_bypass(setup, monkeypatch):
    vm, gateway, source, snapshot = setup
    clock = [100.0]
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: clock[0])
    gateway.snapshot_ready.emit(snapshot)
    assert len(source.snapshots) == 1
    source.failed.emit(FirmwareReleaseError("offline", kind="network"))
    for _ in range(20):
        gateway.status_updated.emit(snapshot.status)
    assert len(source.snapshots) == 1
    clock[0] += 6 * 60 * 60 - 1
    assert vm.auto_check_remote_firmware() is False
    clock[0] += 1
    assert vm.auto_check_remote_firmware() is True
    assert len(source.snapshots) == 2
    assert vm.auto_check_remote_firmware() is False
    source.failed.emit("offline")
    vm.check_remote_firmware()
    assert len(source.snapshots) == 3
    assert vm._remote_firmware_check_timer.isActive()
    vm.shutdown()
    assert not vm._remote_firmware_check_timer.isActive()


@pytest.mark.parametrize("busy", ["checking", "downloading", "downloaded", "ota", "needs_abort", "config", "unknown_config", "calibration", "icon", "glyph", "prompt", "refresh", "pending"])
def test_due_background_check_waits_for_busy_work(setup, monkeypatch, busy):
    vm, gateway, source, snapshot = setup
    clock = [0.0]
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: clock[0])
    gateway.snapshot_ready.emit(snapshot)
    source.failed.emit("offline")
    clock[0] += 6 * 60 * 60
    if busy in {"checking", "downloading", "downloaded"}:
        vm._remote_firmware = RemoteFirmwareCheck(state=RemoteFirmwareState(busy))
    elif busy in {"ota", "needs_abort"}:
        vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.TRANSFERRING if busy == "ota" else FirmwareUpdateState.NEEDS_ABORT)
    elif busy in {"config", "unknown_config"}:
        vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.WRITING if busy == "config" else ConfigTransactionState.UNKNOWN)
    elif busy == "calibration":
        vm._calibration = replace(vm.calibration, state=CalibrationState.CENTERING)
    elif busy == "icon":
        vm.screen_icon.busy = True
    elif busy == "glyph":
        vm.screen_glyphs.busy = True
    elif busy == "prompt":
        vm._prompt_device._status = replace(vm._prompt_device.status, state=PromptDeviceState.WRITING)
    elif busy == "refresh":
        vm._config_refresh = ("serial", "digest")
    elif busy == "pending":
        vm._model = replace(vm.model, snapshot=replace(snapshot, status={**snapshot.status, "pending": {"generation": 99}}))
    assert vm.auto_check_remote_firmware() is False
    assert len(source.snapshots) == 1


def test_device_switch_checks_new_device_but_reconnect_is_not_network_poll(setup, monkeypatch, contract):
    vm, gateway, source, snapshot = setup
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: 100.0)
    gateway.snapshot_ready.emit(snapshot)
    source.release_found.emit(_release(_bundle(contract)))
    gateway.disconnected.emit("unplugged")
    assert vm.remote_firmware_device is None
    gateway.snapshot_ready.emit(snapshot)
    assert len(source.snapshots) == 1
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": "DEVICE-B"}))
    assert len(source.snapshots) == 2
    assert vm.remote_firmware_device[0] == "DEVICE-B"
    assert vm.remote_firmware.release is None


def test_reconnect_with_target_already_installed_removes_old_available_result(setup, contract):
    vm, gateway, source, snapshot = setup
    release = _release(_bundle(contract))
    gateway.snapshot_ready.emit(snapshot)
    source.release_found.emit(release)
    assert vm.remote_firmware.state is RemoteFirmwareState.AVAILABLE
    gateway.disconnected.emit("unplugged")
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions, "firmware": release.version, "build_id": release.build_id}))
    assert vm.remote_firmware.state is RemoteFirmwareState.CURRENT
    assert len(source.snapshots) == 1


def test_switch_back_to_previous_device_fetches_its_own_result(setup, contract, monkeypatch):
    vm, gateway, source, snapshot = setup
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: 100.0)
    release = _release(_bundle(contract))
    gateway.snapshot_ready.emit(snapshot)
    source.release_found.emit(release)
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": "DEVICE-B"}))
    source.release_found.emit(release)
    gateway.snapshot_ready.emit(snapshot)
    assert len(source.snapshots) == 3
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
    assert vm.remote_firmware.release is None


def test_disconnect_cancels_download_before_image_can_be_attached(setup, contract):
    vm, gateway, source, snapshot = setup
    bundle = _bundle(contract)
    gateway.snapshot_ready.emit(snapshot)
    source.release_found.emit(_release(bundle))
    vm.download_remote_firmware()
    gateway.disconnected.emit("unplugged")
    source.download_completed.emit(bundle)
    assert source.canceled == 1
    assert vm.firmware_update.package is None
    assert vm.remote_firmware.state is RemoteFirmwareState.IDLE


def test_due_check_deferred_by_transfer_runs_on_later_timer_tick(setup, monkeypatch):
    vm, gateway, source, snapshot = setup
    clock = [10.0]
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: clock[0])
    gateway.snapshot_ready.emit(snapshot)
    source.failed.emit("offline")
    clock[0] += 6 * 60 * 60
    vm.screen_icon.busy = True
    vm._remote_firmware_check_timer.timeout.emit()
    assert len(source.snapshots) == 1
    vm.screen_icon.busy = False
    vm._remote_firmware_check_timer.timeout.emit()
    assert len(source.snapshots) == 2


def test_offline_timer_is_quiet_then_rechecks_canceled_request_on_connect(setup, monkeypatch):
    vm, gateway, source, snapshot = setup
    monkeypatch.setattr(viewmodels.main, "monotonic", lambda: 100.0)
    assert vm.auto_check_remote_firmware() is False
    gateway.snapshot_ready.emit(snapshot)
    gateway.disconnected.emit("unplugged")
    assert vm.auto_check_remote_firmware() is False
    gateway.snapshot_ready.emit(snapshot)
    assert len(source.snapshots) == 2
    assert vm.remote_firmware.state is RemoteFirmwareState.CHECKING
