import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
from test_session_recovery import session
from test_firmware_transaction import _package, _status, trust_test_signing_key


def pause_offline(vm, gateway, contract, tmp_path):
    package = vm.load_firmware_package(_package(tmp_path, contract))
    vm.start_firmware_update()
    gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    gateway.disconnected.emit("USB unplugged during BEGIN")
    assert vm.firmware_update.state is FirmwareUpdateState.PAUSED
    return package


def test_paused_offline_can_exit_without_aborting_device(session, contract, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    package = pause_offline(vm, gateway, contract, tmp_path)
    questions = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args[2]) or QMessageBox.Yes)
    before = len(gateway.commands)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert event.isAccepted(), "Offline PAUSED traps the user in the app"
        assert questions and "不会取消设备" in questions[0]
        assert len(gateway.commands) == before
        assert vm.firmware_update.state is not FirmwareUpdateState.COMPLETED
        assert vm.firmware_update.package is package
    finally:
        vm._firmware_update = FirmwareUpdateTransaction()


@pytest.mark.parametrize("reconnect", [False, True])
def test_offline_exit_cancel_or_reconnection_keeps_transaction(session, contract, tmp_path, monkeypatch, reconnect):
    window, vm, gateway, snapshot, _ = session
    pause_offline(vm, gateway, contract, tmp_path)
    def answer(*args):
        if reconnect:
            gateway.snapshot_ready.emit(snapshot)
            return QMessageBox.Yes
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", answer)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    try:
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert vm.firmware_update.state is (FirmwareUpdateState.CHECKING if reconnect else FirmwareUpdateState.PAUSED)
        assert not any(c.name == "FW_ABORT" for c in gateway.commands)
    finally:
        vm._firmware_update = FirmwareUpdateTransaction()


def test_stop_wait_then_reconnect_reads_status_before_resuming(session, contract, tmp_path):
    window, vm, gateway, snapshot, _ = session
    package = pause_offline(vm, gateway, contract, tmp_path)
    try:
        vm.stop_firmware_wait()
        before = len(gateway.commands)
        gateway.snapshot_ready.emit(snapshot)
        assert not any(c.name.startswith("FW_") for c in gateway.commands[before:])
        vm.start_firmware_update()
        assert gateway.commands[-1].name == "FW_STATUS"
        gateway.command_completed.emit("FW_STATUS", _status(package, "RECEIVING", received=100))
        assert gateway.commands[-1].name == "FW_DATA"
        assert vm.firmware_update.received_size == 100
    finally:
        vm._firmware_update = FirmwareUpdateTransaction()
