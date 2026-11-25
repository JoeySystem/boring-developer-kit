"""FirmwareUpdateController on its own: no MainViewModel, no Qt window."""

from __future__ import annotations

from dataclasses import replace

import pytest

from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.protocol.bootstrap import COMMAND_DROPPED, BootstrapError, BootstrapKind
from controller_config.firmware_release import load_local_firmware_package
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.firmware_update_controller import FirmwareUpdateController
from test_firmware_release import trust_test_signing_key  # noqa: F401  (autouse signing key)
from test_firmware_transaction import _booted_status, _package, _status


class Harness:
    def __init__(self, contract, tmp_path) -> None:
        self.commands: list[str] = []
        self.poll_intervals: list[int] = []
        self.snapshot = _power_v2_snapshot(contract, read_only=False)
        self.controller = FirmwareUpdateController(
            lambda command: self.commands.append(command.name),
            self.poll_intervals.append,
            lambda: self.snapshot,
        )
        self.events: list[tuple] = []
        self.controller.transaction_changed.connect(lambda t: self.events.append(("changed", t.state)))
        self.controller.finished.connect(lambda state: self.events.append(("finished", state)))
        self.controller.rescan_requested.connect(lambda: self.events.append(("rescan",)))
        self.package = load_local_firmware_package(_package(tmp_path, contract), contract)

    @property
    def state(self) -> FirmwareUpdateState:
        return self.controller.transaction.state

    def started(self):
        self.controller.prepare(self.package)
        self.controller.start(self.snapshot)
        return self


@pytest.fixture
def harness(qtbot, contract, tmp_path):
    harness = Harness(contract, tmp_path)
    yield harness
    harness.controller.shutdown()


def test_start_binds_serial_slows_status_poll_and_reads_fw_status(harness):
    harness.started()
    assert harness.state is FirmwareUpdateState.CHECKING
    assert harness.controller.transaction.serial == harness.snapshot.identity["serial"]
    assert harness.poll_intervals == [60_000]
    assert harness.commands == ["FW_STATUS"]
    assert ("changed", FirmwareUpdateState.PACKAGE_READY) in harness.events


def test_only_fw_commands_are_claimed(harness):
    harness.started()
    error = BootstrapError(BootstrapKind.READ_FAILED, "x", "x", error_name="VALIDATION_FAILED")
    assert harness.controller.handle_completed("SET_CONFIG", {}) is False
    assert harness.controller.handle_failed("SET_CONFIG", error) is False
    assert harness.controller.handle_failed("CALIBRATION_START", error) is False
    assert harness.state is FirmwareUpdateState.CHECKING


def test_dropped_fw_command_is_claimed_but_changes_nothing(harness):
    harness.started()
    before = harness.controller.transaction
    dropped = BootstrapError(BootstrapKind.READ_FAILED, "dropped", "", error_name=COMMAND_DROPPED)
    assert harness.controller.handle_failed("FW_STATUS", dropped) is True
    assert harness.controller.transaction == before


def test_full_transfer_finalize_and_verified_reboot(harness):
    harness.started()
    package = harness.package
    harness.controller.handle_completed("FW_STATUS", _status(package, "IDLE"))
    assert harness.commands[-1] == "FW_BEGIN"
    harness.controller.handle_completed("FW_BEGIN", {"result": {
        "state": "RECEIVING", "offset": 0, "chunk_bytes": 4096, "target_partition": "ota_1"}})
    assert harness.commands[-1] == "FW_DATA"
    harness.controller.handle_completed("FW_DATA", {"result": {
        "state": "RECEIVING", "received_size": package.size}})
    assert harness.commands[-1] == "FW_END"
    harness.controller.handle_completed("FW_END", {"result": {
        "state": "READY_TO_REBOOT", "version": package.version}})
    assert harness.state is FirmwareUpdateState.WAITING_RECONNECT
    assert harness.controller.deadline.isActive()

    harness.controller.on_disconnected("reboot")
    harness.snapshot = replace(harness.snapshot, versions={
        **harness.snapshot.versions, "firmware": package.version, "build_id": package.build_id})
    harness.controller.reconcile_snapshot(harness.snapshot)
    assert harness.state is FirmwareUpdateState.VERIFYING
    harness.controller.handle_completed("FW_STATUS", _booted_status(package))
    assert harness.state is FirmwareUpdateState.COMPLETED
    assert harness.events[-1] == ("finished", FirmwareUpdateState.COMPLETED)
    assert harness.poll_intervals[-1] == 500
    assert not harness.controller.deadline.isActive()


def test_rollback_pending_keeps_polling_within_deadline(harness):
    harness.started()
    package = harness.package
    harness.controller.set_transaction(replace(
        harness.controller.transaction, state=FirmwareUpdateState.VERIFYING, target_partition="ota_1"))
    harness.snapshot = replace(harness.snapshot, versions={
        **harness.snapshot.versions, "firmware": package.version, "build_id": package.build_id})
    harness.controller.handle_completed("FW_STATUS", _booted_status(package, rollback_pending=True))
    assert harness.state is FirmwareUpdateState.VERIFYING
    assert "回滚" in harness.controller.transaction.message
    assert harness.controller.status_timer.isActive()
    harness.controller.poll_status()
    assert harness.commands[-1] == "FW_STATUS"


def test_deadline_waiting_reconnect_requests_rescan_then_fails(harness):
    harness.started()
    harness.controller.set_transaction(replace(
        harness.controller.transaction, state=FirmwareUpdateState.WAITING_RECONNECT))
    harness.controller.on_deadline()
    assert harness.state is FirmwareUpdateState.VERIFYING
    assert harness.events[-1] == ("rescan",)
    assert harness.controller.deadline.isActive()
    harness.controller.on_deadline()
    assert harness.state is FirmwareUpdateState.FAILED


def test_fw_end_timeout_verifies_instead_of_restarting(harness):
    harness.started()
    timeout = BootstrapError(BootstrapKind.READ_FAILED, "t", "FW_END timeout", error_name="TRANSPORT_TIMEOUT")
    assert harness.controller.handle_failed("FW_END", timeout)
    assert harness.state is FirmwareUpdateState.VERIFYING
    assert harness.events[-1] == ("rescan",)


def test_data_timeout_reconciles_offset_with_fw_status(harness):
    harness.started()
    timeout = BootstrapError(BootstrapKind.READ_FAILED, "t", "FW_DATA timeout", error_name="TRANSPORT_TIMEOUT")
    harness.controller.handle_failed("FW_DATA", timeout)
    assert harness.state is FirmwareUpdateState.CHECKING
    assert harness.commands[-1] == "FW_STATUS"


def test_disconnect_mid_transfer_pauses_without_deadline(harness):
    harness.started()
    harness.controller.set_transaction(replace(
        harness.controller.transaction, state=FirmwareUpdateState.TRANSFERRING, in_flight_size=256))
    harness.controller.on_disconnected("cable")
    assert harness.state is FirmwareUpdateState.PAUSED
    assert harness.controller.transaction.in_flight_size == 0
    assert not harness.controller.deadline.isActive()


def test_reconnect_of_another_device_stops_update(harness):
    harness.started()
    other = replace(harness.snapshot, identity={**harness.snapshot.identity, "serial": "OTHER"})
    harness.controller.reconcile_snapshot(other)
    assert harness.state is FirmwareUpdateState.FAILED
    assert "另一台设备" in harness.controller.transaction.message


def test_abort_guards_and_waits_for_in_flight_command(harness):
    harness.started()
    with pytest.raises(ValueError, match="重新连接"):
        harness.controller.abort(harness.snapshot, link_usable=False)
    harness.controller.abort(harness.snapshot, link_usable=True)
    assert harness.state is FirmwareUpdateState.ABORTING
    assert harness.commands[-1] == "FW_STATUS"  # abort waits for the in-flight request
    harness.controller.handle_completed("FW_STATUS", _status(harness.package, "IDLE"))
    assert harness.state is FirmwareUpdateState.PACKAGE_READY


def test_dismiss_only_from_resting_states(harness):
    harness.started()
    with pytest.raises(ValueError):
        harness.controller.dismiss()
    harness.controller.finish(FirmwareUpdateState.FAILED, "x")
    harness.controller.dismiss()
    assert harness.controller.transaction == FirmwareUpdateTransaction()
    assert harness.poll_intervals[-1] == 500
