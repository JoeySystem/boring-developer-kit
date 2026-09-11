from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import struct
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QObject, Signal

from controller_config.firmware_update import (
    APP_DESC_OFFSET,
    CUSTOM_DESC_OFFSET,
    ESP_APP_DESC_MAGIC,
    FirmwareUpdateState,
    WMP_DESCRIPTOR,
    WMP_DESCRIPTOR_MAGIC,
    WMP_DESCRIPTOR_VERSION,
)
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel


class FirmwareGateway(QObject):
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.commands = []
        self.poll_intervals = []
        self.scans = []

    def scan(self, *, usb_only=False) -> None:
        self.scans.append(usb_only)

    def connect_port(self, _port_name: str) -> None:
        pass

    def execute_command(self, command) -> None:
        self.commands.append(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self.poll_intervals.append(interval_ms)

    def shutdown(self) -> None:
        pass


def _package(root: Path, contract, *, build_id: str = "next-build") -> Path:
    image = bytearray(1024)
    image[0] = 0xE9
    struct.pack_into("<I", image, APP_DESC_OFFSET, ESP_APP_DESC_MAGIC)
    image[APP_DESC_OFFSET + 16 : APP_DESC_OFFSET + 48] = _fixed("0.3.0-alpha.2", 32)
    image[APP_DESC_OFFSET + 48 : APP_DESC_OFFSET + 80] = _fixed("wired_macro_pad", 32)
    WMP_DESCRIPTOR.pack_into(
        image,
        CUSTOM_DESC_OFFSET,
        WMP_DESCRIPTOR_MAGIC,
        WMP_DESCRIPTOR_VERSION,
        WMP_DESCRIPTOR.size,
        _fixed(contract.product_id, 32),
        _fixed("WMP-S3-MATRIX12-POWER-V2", 32),
    )
    image_path = root / "wired_macro_pad.bin"
    image_path.write_bytes(image)
    manifest_path = root / "firmware-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "format": "wmp-firmware-update-v1",
                "product_id": contract.product_id,
                "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
                "version": "0.3.0-alpha.2",
                "image": image_path.name,
                "size": len(image),
                "sha256": hashlib.sha256(image).hexdigest(),
                "build_id": build_id,
                "validation_state": "built",
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _fixed(value: str, size: int) -> bytes:
    encoded = value.encode("ascii")
    return encoded + b"\0" + bytes(size - len(encoded) - 1)


def test_viewmodel_accepts_downloaded_zip_as_a_ready_package(
    qtbot, contract, tmp_path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest_path = _package(source, contract)
    archive_path = tmp_path / "boring-firmware.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.write(manifest_path, "firmware-manifest.json")
        archive.write(source / "wired_macro_pad.bin", "wired_macro_pad.bin")

    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))

    package = view_model.load_firmware_package(archive_path)

    assert package.source_path == archive_path.resolve()
    assert view_model.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert view_model.firmware_update.package is package


def _status(package, state: str, received: int = 0) -> dict:
    return {
        "command": "FW_STATUS",
        "result": {
            "firmware_update": {
                "state": state,
                "expected_size": package.size if state != "IDLE" else 0,
                "received_size": received,
                "expected_sha256": package.sha256 if state != "IDLE" else "",
                "expected_version": package.version if state != "IDLE" else "",
                "running_version": "0.3.0-alpha.1",
                "running_partition": "ota_0",
                "target_partition": "ota_1" if state != "IDLE" else "",
                "rollback_pending": False,
                "reboot_pending": state == "READY_TO_REBOOT",
            }
        },
    }


def test_complete_firmware_flow_transfers_finalizes_and_verifies_reconnect(
    qtbot, contract, tmp_path
) -> None:
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    package = view_model.load_firmware_package(_package(tmp_path, contract))

    view_model.start_firmware_update()
    assert gateway.commands[-1].name == "FW_STATUS"
    gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    assert gateway.commands[-1].name == "FW_BEGIN"

    gateway.command_completed.emit(
        "FW_BEGIN",
        {
            "command": "FW_BEGIN",
            "result": {
                "state": "RECEIVING",
                "offset": 0,
                "chunk_bytes": 4096,
                "target_partition": "ota_1",
            },
        },
    )
    assert gateway.commands[-1].name == "FW_DATA"
    gateway.command_completed.emit(
        "FW_DATA",
        {
            "command": "FW_DATA",
            "result": {
                "state": "RECEIVING",
                "received_size": package.size,
                "expected_size": package.size,
            },
        },
    )
    assert gateway.commands[-1].name == "FW_END"

    gateway.command_completed.emit(
        "FW_END",
        {
            "command": "FW_END",
            "result": {
                "state": "FINALIZING",
                "version": package.version,
                "reboot_scheduled": False,
            },
        },
    )
    gateway.command_completed.emit(
        "FW_STATUS", _status(package, "READY_TO_REBOOT", package.size)
    )
    assert view_model.firmware_update.state is FirmwareUpdateState.WAITING_RECONNECT

    gateway.disconnected.emit("expected reboot")
    updated = replace(
        snapshot,
        versions={
            **snapshot.versions,
            "firmware": package.version,
            "build_id": package.build_id,
        },
    )
    gateway.snapshot_ready.emit(updated)
    assert view_model.firmware_update.state is FirmwareUpdateState.VERIFYING
    assert gateway.commands[-1].name == "FW_STATUS"
    gateway.command_completed.emit("FW_STATUS", _booted_status(package))
    assert view_model.firmware_update.state is FirmwareUpdateState.COMPLETED
    assert gateway.poll_intervals[-1] == 500


def _booted_status(package, **overrides) -> dict:
    payload = _status(package, "IDLE")
    payload["result"]["firmware_update"].update(
        running_version=package.version,
        running_partition="ota_1",
        target_partition="ota_0",
        **overrides,
    )
    return payload


def _reconnected_update(contract, tmp_path):
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    package = view_model.load_firmware_package(_package(tmp_path, contract))
    view_model.start_firmware_update()
    view_model._set_firmware_update(
        replace(
            view_model.firmware_update,
            state=FirmwareUpdateState.WAITING_RECONNECT,
            target_partition="ota_1",
        )
    )
    gateway.snapshot_ready.emit(
        replace(snapshot, versions={**snapshot.versions, "firmware": package.version, "build_id": package.build_id})
    )
    return gateway, view_model, package


@pytest.mark.parametrize("rollback_pending", [True, None])
def test_reconnect_waits_for_explicit_rollback_confirmation(
    qtbot, contract, tmp_path, rollback_pending
) -> None:
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    gateway.command_completed.emit("FW_STATUS", _booted_status(package, rollback_pending=rollback_pending))

    assert view_model.firmware_update.state is FirmwareUpdateState.VERIFYING
    assert view_model._firmware_deadline.isActive()
    assert view_model._firmware_status_timer.isActive()
    view_model._poll_firmware_status()
    assert gateway.commands[-1].name == "FW_STATUS"

    gateway.command_completed.emit("FW_STATUS", _booted_status(package))
    assert view_model.firmware_update.state is FirmwareUpdateState.COMPLETED
    assert not view_model._firmware_deadline.isActive()
    assert not view_model._firmware_status_timer.isActive()


def test_same_version_rollback_to_previous_partition_is_not_success(
    qtbot, contract, tmp_path
) -> None:
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    payload = _booted_status(package)
    payload["result"]["firmware_update"].update(running_partition="ota_0", target_partition="ota_1")
    gateway.command_completed.emit("FW_STATUS", payload)

    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED
    assert "回滚" in view_model.firmware_update.message


@pytest.mark.parametrize("field", ["firmware", "build_id"])
def test_confirmed_partition_still_requires_matching_version_and_build(
    qtbot, contract, tmp_path, field
) -> None:
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    snapshot = view_model.model.snapshot
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions, field: "old"}))
    gateway.command_completed.emit("FW_STATUS", _booted_status(package))

    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED


def test_rollback_confirmation_times_out_and_late_status_cannot_restart_update(
    qtbot, contract, tmp_path
) -> None:
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    gateway.command_completed.emit("FW_STATUS", _booted_status(package, rollback_pending=True))
    view_model._on_firmware_deadline()

    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED
    assert "期限" in view_model.firmware_update.message
    assert gateway.poll_intervals[-1] == 500
    commands_before_late_response = len(gateway.commands)
    gateway.command_completed.emit("FW_STATUS", _booted_status(package))
    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED
    assert len(gateway.commands) == commands_before_late_response


@pytest.mark.parametrize("connection_loss", ["disconnect", "failure"])
def test_connection_loss_during_confirmation_keeps_deadline(
    qtbot, contract, tmp_path, connection_loss
) -> None:
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    gateway.command_completed.emit("FW_STATUS", _booted_status(package, rollback_pending=True))
    if connection_loss == "disconnect":
        gateway.disconnected.emit("reboot before confirmation")
    else:
        gateway.failure.emit(BootstrapKind.READ_FAILED, "read failed", "lost connection")

    assert view_model.firmware_update.state is FirmwareUpdateState.VERIFYING
    assert view_model._firmware_deadline.isActive()
    view_model._on_firmware_deadline()
    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED


@pytest.mark.parametrize("trigger", ["reconnect_deadline", "end_ack_timeout"])
def test_reconnect_recovery_starts_bounded_verification(
    qtbot, contract, tmp_path, trigger
) -> None:
    gateway, view_model, _package = _reconnected_update(contract, tmp_path)
    view_model._firmware_deadline.stop()
    if trigger == "reconnect_deadline":
        view_model._set_firmware_update(
            replace(view_model.firmware_update, state=FirmwareUpdateState.WAITING_RECONNECT)
        )
        view_model._on_firmware_deadline()
    else:
        gateway.command_failed.emit(
            "FW_END",
            BootstrapError(BootstrapKind.READ_FAILED, "timeout", "FW_END timeout", error_name="TRANSPORT_TIMEOUT"),
        )

    assert view_model.firmware_update.state is FirmwareUpdateState.VERIFYING
    assert view_model._firmware_deadline.isActive()
    view_model._on_firmware_deadline()
    assert view_model.firmware_update.state is FirmwareUpdateState.FAILED


def test_firmware_recovery_keeps_scanning_after_empty_usb_scan(qtbot, contract, tmp_path):
    gateway, view_model, package = _reconnected_update(contract, tmp_path)
    view_model.refresh()
    gateway.candidates_found.emit(())
    assert view_model._reconnect_timer.isActive()
    view_model._scan_for_reconnect()
    assert gateway.scans == [True, True]
    assert view_model._firmware_deadline.isActive()
    view_model.shutdown()


def test_demo_updates_alternate_partitions_and_pass_real_confirmation_rules(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    gateway.connect_port("demo://power-v2")
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None)
    manifest_path = _package(tmp_path, contract, build_id="0.3.0-alpha.2")

    for expected_partition in ("ota_1", "ota_0"):
        view_model.load_firmware_package(manifest_path)
        view_model.start_firmware_update()
        qtbot.waitUntil(
            lambda: view_model.firmware_update.state in {FirmwareUpdateState.COMPLETED, FirmwareUpdateState.FAILED},
            timeout=10_000,
        )
        assert view_model.firmware_update.state is FirmwareUpdateState.COMPLETED
        assert view_model.firmware_update.target_partition == expected_partition
        assert gateway._firmware_update["running_partition"] == expected_partition
        assert gateway._firmware_update["rollback_pending"] is False
        assert gateway._firmware_update["target_partition"] != expected_partition


def test_firmware_install_requires_usb_connection(qtbot, contract, tmp_path) -> None:
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = replace(
        _power_v2_snapshot(contract, read_only=False),
        port_name="ble:12345678-1234-1234-1234-123456789abc",
    )
    gateway.snapshot_ready.emit(snapshot)
    view_model.load_firmware_package(_package(tmp_path, contract))

    with pytest.raises(ValueError, match="请连接 USB"):
        view_model.start_firmware_update()

    assert gateway.commands == []


def test_different_interrupted_update_requires_explicit_abort(qtbot, contract, tmp_path) -> None:
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    package = view_model.load_firmware_package(_package(tmp_path, contract))
    view_model.start_firmware_update()
    other = _status(package, "RECEIVING", 128)
    other["result"]["firmware_update"]["expected_sha256"] = "0" * 64

    gateway.command_completed.emit("FW_STATUS", other)
    assert view_model.firmware_update.state is FirmwareUpdateState.NEEDS_ABORT
    view_model.abort_firmware_update()
    assert gateway.commands[-1].name == "FW_ABORT"
    gateway.command_completed.emit(
        "FW_ABORT", {"command": "FW_ABORT", "result": {"state": "IDLE"}}
    )
    assert view_model.firmware_update.state is FirmwareUpdateState.PACKAGE_READY


def test_lost_chunk_ack_reconciles_offset_instead_of_restarting(
    qtbot, contract, tmp_path
) -> None:
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    package = view_model.load_firmware_package(_package(tmp_path, contract))
    view_model.start_firmware_update()
    gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    gateway.command_completed.emit(
        "FW_BEGIN",
        {
            "command": "FW_BEGIN",
            "result": {
                "state": "RECEIVING",
                "offset": 0,
                "chunk_bytes": 512,
                "target_partition": "ota_1",
            },
        },
    )
    first_data = gateway.commands[-1]
    gateway.command_failed.emit(
        "FW_DATA",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "timeout",
            "FW_DATA timeout",
            error_name="TRANSPORT_TIMEOUT",
        ),
    )
    assert gateway.commands[-1].name == "FW_STATUS"
    gateway.command_completed.emit("FW_STATUS", _status(package, "RECEIVING", 512))
    resumed = gateway.commands[-1]
    assert resumed.name == "FW_DATA"
    assert first_data.payload["offset"] == 0
    assert resumed.payload["offset"] == 512


def test_abort_waits_for_in_flight_chunk_then_sends_fw_abort(
    qtbot, contract, tmp_path
) -> None:
    gateway = FirmwareGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    package = view_model.load_firmware_package(_package(tmp_path, contract))
    view_model.start_firmware_update()
    gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    gateway.command_completed.emit(
        "FW_BEGIN",
        {
            "command": "FW_BEGIN",
            "result": {
                "state": "RECEIVING",
                "offset": 0,
                "chunk_bytes": 512,
                "target_partition": "ota_1",
            },
        },
    )
    assert gateway.commands[-1].name == "FW_DATA"

    view_model.abort_firmware_update()
    assert view_model.firmware_update.state is FirmwareUpdateState.ABORTING
    assert gateway.commands[-1].name == "FW_DATA"

    gateway.command_completed.emit(
        "FW_DATA",
        {
            "command": "FW_DATA",
            "result": {
                "state": "RECEIVING",
                "received_size": 512,
                "expected_size": package.size,
            },
        },
    )
    assert gateway.commands[-1].name == "FW_ABORT"
    gateway.command_completed.emit(
        "FW_ABORT", {"command": "FW_ABORT", "result": {"state": "IDLE"}}
    )
    assert view_model.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
