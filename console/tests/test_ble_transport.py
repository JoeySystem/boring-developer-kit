from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtBluetooth import QLowEnergyCharacteristic, QLowEnergyService
from PySide6.QtCore import QObject, Signal

from controller_config.models import PortCandidate
from controller_config.protocol.contract import Contract
from controller_config.transport.ble import BleChannel, BleWorker, connected_service_uuid_texts, split_att_payload
from controller_config.transport.device_gateway import DeviceGateway
from controller_config.transport.demo import _power_v2_snapshot


class FakeGateway(QObject):
    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)

    def __init__(self, candidates: tuple[PortCandidate, ...] = ()) -> None:
        super().__init__()
        self.candidates = candidates
        self.scans = 0
        self.stops = 0
        self.connected = ""
        self.commands: list[object] = []
        self.interval = 0
        self.shutdowns = 0

    def scan(self) -> None:
        self.scans += 1
        self.candidates_found.emit(self.candidates)

    def stop_scan(self) -> None:
        self.stops += 1

    def connect_port(self, port_name: str) -> None:
        self.connected = port_name

    def execute_command(self, command: object) -> None:
        self.commands.append(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self.interval = interval_ms

    def shutdown(self) -> None:
        self.shutdowns += 1


def test_att_payload_uses_negotiated_mtu() -> None:
    payload = bytes(range(100))
    assert tuple(map(len, split_att_payload(payload, 23))) == (20, 20, 20, 20, 20)
    assert tuple(map(len, split_att_payload(payload, 64))) == (61, 39)
    assert b"".join(split_att_payload(payload, 64)) == payload


def test_channel_rejects_invalid_real_qt_characteristics() -> None:
    errors = []
    service = SimpleNamespace(characteristic=lambda _uuid: QLowEnergyCharacteristic())
    channel = SimpleNamespace(_service=service, _fail=errors.append, _open=True)

    BleChannel._on_service_state_changed(channel, QLowEnergyService.RemoteServiceDiscovered)

    assert errors == ["设备的蓝牙配置特征不完整，请先通过 USB 更新固件"]
    assert BleChannel.write(channel, b"WMP1") == -1


def test_channel_reports_qt_service_error_without_error_string_method() -> None:
    errors = []
    service = SimpleNamespace(error=lambda: QLowEnergyService.ServiceError.CharacteristicWriteError)
    channel = SimpleNamespace(_service=service, _closing=False, _fail=errors.append)

    BleChannel._on_service_error(channel, service.error())

    assert errors == ["蓝牙配置通信失败：CharacteristicWriteError"]


def test_connected_ble_scan_includes_hid_devices() -> None:
    assert connected_service_uuid_texts() == (
        "7e2f0001-7a91-4a5b-9c2d-6e8f4b524731",
        "1812",
    )


def test_bluetooth_candidate_hides_internal_identifier() -> None:
    candidate = PortCandidate(
        "ble:12345678-1234-1234-1234-123456789abc",
        description="BORING MIST",
        transport="bluetooth",
    )
    assert candidate.display_name == "BORING MIST · 蓝牙"


def test_snapshot_reports_bluetooth_connection(contract: Contract) -> None:
    snapshot = _power_v2_snapshot(contract, read_only=False)

    assert snapshot.connection_kind == "usb"
    assert replace(snapshot, port_name="ble:device-id").connection_kind == "bluetooth"


def test_device_gateway_prefers_usb_candidates(qtbot, contract: Contract) -> None:
    usb = FakeGateway((PortCandidate("cu.device"),))
    ble = FakeGateway((PortCandidate("ble:one", transport="bluetooth"),))
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    received: list[tuple[PortCandidate, ...]] = []
    gateway.candidates_found.connect(received.append)

    gateway.scan()

    assert received == [usb.candidates]
    assert ble.scans == 0
    assert ble.stops == 1


def test_device_gateway_falls_back_to_bluetooth(qtbot, contract: Contract) -> None:
    usb = FakeGateway()
    ble = FakeGateway((PortCandidate("ble:one", transport="bluetooth"),))
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    received: list[tuple[PortCandidate, ...]] = []
    gateway.candidates_found.connect(received.append)

    gateway.scan()

    assert received == [ble.candidates]


def test_usb_update_scan_does_not_fall_back_to_ble_while_usb_reboots(qtbot, contract):
    usb = FakeGateway()
    ble = FakeGateway((PortCandidate("ble:one", transport="bluetooth"),))
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    found = []
    gateway.candidates_found.connect(found.append)

    gateway.scan(usb_only=True)
    assert found == [()]
    assert ble.scans == 0
    usb.candidates = (PortCandidate("cu.new-port"),)
    gateway.scan(usb_only=True)
    assert found[-1] == usb.candidates


def test_device_gateway_routes_only_active_transport(qtbot, contract: Contract) -> None:
    usb = FakeGateway()
    ble = FakeGateway()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    received: list[object] = []
    gateway.status_updated.connect(received.append)

    gateway.connect_port("ble:one")
    usb.status_updated.emit({"source": "usb"})
    ble.status_updated.emit({"source": "ble"})

    assert ble.connected == "ble:one"
    assert received == [{"source": "ble"}]


def test_reconnect_tick_does_not_restart_pending_bluetooth_scan(qtbot, contract: Contract) -> None:
    class PendingBle(FakeGateway):
        def scan(self):
            self.scans += 1

    usb = FakeGateway()
    ble = PendingBle()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    found = []
    gateway.candidates_found.connect(found.append)

    gateway.scan()
    gateway.scan()  # MainViewModel retries every second while BLE is discovering.

    assert ble.scans == 1
    assert usb.scans == 1
    ble.candidates_found.emit(())
    assert found == [()]
    gateway.scan()  # A completed empty scan must allow the next retry.
    assert ble.scans == 2


def test_ble_natural_scan_completion_publishes_results_once(qtbot, contract: Contract) -> None:
    worker = BleWorker(contract)
    worker._scan_sources_pending = 2
    worker._add_connected_device("12345678-1234-1234-1234-123456789abc", "Codex Micro TEST")
    found = []
    worker.candidates_found.connect(found.append)

    worker._scan_source_finished()
    assert found == []
    worker._scan_source_finished()

    assert len(found) == 1
    assert found[0][0].description == "Codex Micro TEST"
    worker.stop_scan()
    assert len(found) == 1


def test_authentication_disconnect_emits_failure_instead_of_reconnect_signal(contract):
    from PySide6.QtSerialPort import QSerialPort
    from controller_config.protocol.bootstrap import BootstrapSession, BootstrapKind, Command
    failures = []
    session = BootstrapSession(contract=contract, port_name='ble:test',
        send=lambda *_: None, completed=lambda _: None, failed=failures.append)
    for command in ('AUTH_GET_CERTIFICATE', 'AUTH_CHALLENGE'):
        session.finished = False
        session._commands = [Command(command, 4, {})]
        session._index = 0
        worker = SimpleNamespace(_closing=False, _session=session,
            _serial=SimpleNamespace(errorString=lambda: '蓝牙设备已断开'))
        # Any fall-through to the ordinary disconnected path fails on this
        # deliberately minimal worker, which has no disconnected signal.
        BleWorker._on_serial_error(worker, QSerialPort.ResourceError)
        assert failures[-1].kind is BootstrapKind.AUTHENTICATION_INTERRUPTED
        assert command in failures[-1].technical
