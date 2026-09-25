from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from PySide6.QtBluetooth import QBluetoothAddress, QBluetoothDeviceInfo, QLowEnergyCharacteristic, QLowEnergyService
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, QUuid, Signal

from controller_config.models import PortCandidate
from controller_config.protocol.contract import Contract
from controller_config.transport.ble import (
    LAST_BLE_PORT_KEY,
    BleChannel,
    BleWorker,
    connected_service_uuid_texts,
    split_att_payload,
)
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

    def scan(self, *, preferred_port: str | None = None) -> None:
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

    assert errors == ["无法完成蓝牙配置服务发现，请重新连接或改用 USB。"]
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


@pytest.mark.parametrize("identifiers", (
    ("aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"),
    ("12345678-1234-1234-1234-123456789ab1", "12345678-1234-1234-1234-123456789ab2"),
))
def test_connected_custom_names_keep_same_name_devices_separate(qtbot, contract, identifiers):
    worker = BleWorker(contract)
    for identifier in identifiers:
        worker._add_connected_device(identifier, "办公室键盘")
        worker._add_connected_device(identifier, "办公室键盘")
    candidates = tuple(worker._candidates_by_port.values())
    assert len(candidates) == 2
    assert {item.description for item in candidates} == {"办公室键盘"}
    assert {item.port_name for item in candidates} == {f"ble:{value}" for value in identifiers}
    assert all(not item.serial_number for item in candidates)


def test_native_connected_config_service_accepts_custom_names_but_hid_stays_filtered(qtbot, monkeypatch):
    from controller_config.transport import ble

    core = SimpleNamespace(
        CBManagerStateUnknown=0, CBManagerStateResetting=1, CBManagerStatePoweredOn=5,
        CBUUID=SimpleNamespace(UUIDWithString_=lambda value: value),
    )
    monkeypatch.setattr(ble, "CoreBluetooth", core, raising=False)

    def peripheral(identifier, name):
        return SimpleNamespace(identifier=lambda: SimpleNamespace(UUIDString=lambda: identifier),
                               name=lambda: name)

    first = peripheral("AA", "办公室键盘")
    second = peripheral("BB", "办公室键盘")
    legacy = peripheral("CC", "BORING MIST")
    unrelated = peripheral("DD", "Other keyboard")
    queries = []

    def connected(services):
        queries.append(services[0])
        return [first, second] if services[0] == ble.SERVICE_UUID_TEXT else [first, legacy, unrelated]

    manager = SimpleNamespace(
        state=lambda: 5,
        retrieveConnectedPeripheralsWithServices_=connected,
        scanForPeripheralsWithServices_options_=lambda *_: None,
    )
    finder = ble.MacConnectedDeviceFinder()
    found = []
    finder.device_found.connect(lambda identifier, name: found.append((identifier, name)))
    finder._manager_state_changed(manager)

    assert queries == list(ble.connected_service_uuid_texts())
    assert found == [("aa", "办公室键盘"), ("bb", "办公室键盘"), ("cc", "BORING MIST")]


def test_native_connected_query_never_recalls_history_or_scans_neighbours(qtbot, monkeypatch):
    from controller_config.transport import ble
    identifier = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(ble, "CoreBluetooth", SimpleNamespace(
        CBManagerStateUnknown=0, CBManagerStateResetting=1, CBManagerStatePoweredOn=5,
        CBUUID=SimpleNamespace(UUIDWithString_=lambda value: value)), raising=False)
    def forbidden(*_):
        raise AssertionError("History and nearby advertisements cannot become connected candidates")
    manager = SimpleNamespace(state=lambda: 5,
        retrieveConnectedPeripheralsWithServices_=lambda _: [],
        retrievePeripheralsWithIdentifiers_=forbidden,
        scanForPeripheralsWithServices_options_=forbidden)
    finder = ble.MacConnectedDeviceFinder(remembered_identifier=identifier)
    found, finished = [], []
    finder.device_found.connect(lambda *args: found.append(args))
    finder.finished.connect(lambda: finished.append(True))
    finder._manager_state_changed(manager)
    assert found == [] and finished == [True]


def test_connected_query_callback_returns_to_qt_thread(qtbot, monkeypatch):
    import sys
    import threading
    if sys.platform != "darwin":
        pytest.skip("macOS native delegate")
    from controller_config.transport import ble
    calls = []
    owner_thread = threading.get_ident()
    monkeypatch.setattr(ble.MacConnectedDeviceFinder, "_manager_state_changed",
                        lambda self, manager: calls.append(threading.get_ident()))
    finder = ble.MacConnectedDeviceFinder()
    delegate = ble._MacCentralDelegate.alloc().initWithOwner_(finder)
    thread = threading.Thread(target=lambda: delegate.centralManagerDidUpdateState_(object()))
    thread.start()
    thread.join()
    assert calls == []
    qtbot.waitUntil(lambda: bool(calls))
    assert calls == [owner_thread]


def test_canceled_connected_query_ignores_queued_native_callback(qtbot):
    import sys
    if sys.platform != "darwin":
        pytest.skip("macOS native delegate")
    from controller_config.transport import ble
    finder = ble.MacConnectedDeviceFinder()
    delegate = ble._MacCentralDelegate.alloc().initWithOwner_(finder)
    finder._delegate = delegate
    finished = []
    finder.finished.connect(lambda: finished.append(True))
    # An already queued callback must not enumerate or emit after cancellation.
    delegate.centralManagerDidUpdateState_(object())
    finder.cancel()
    QCoreApplication.processEvents()
    assert finished == [] and delegate._owner is None


def test_connected_query_timeout_is_retryable_discovery_failure(qtbot, contract, monkeypatch):
    from controller_config.transport import ble
    from controller_config.protocol.bootstrap import BootstrapKind
    monkeypatch.setattr(ble.MacConnectedDeviceFinder, "start", lambda self: None)
    monkeypatch.setattr(ble.WindowsConnectedDeviceFinder, "start", lambda self: None)
    if ble.sys.platform not in {"darwin", "win32"}:
        pytest.skip("Connected Bluetooth queries only supported on macOS/Windows")
    worker = BleWorker(contract)
    failures = []
    worker.failure.connect(lambda *args: failures.append(args))
    worker.scan()
    worker._scan_timer.timeout.emit()
    assert failures[0][0] is BootstrapKind.DISCOVERY_INTERRUPTED

    # A timed-out query may be followed by shutdown before deferred Qt deletion.
    # The old per-query timer/lambda cycle crashed on the next event loop turn.
    import gc
    import weakref
    reference = weakref.ref(worker)
    del worker
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert reference() is None


def test_remembered_renamed_hid_is_only_listed_when_system_connected(qtbot, monkeypatch):
    from controller_config.transport import ble
    identifier = "12345678-1234-1234-1234-123456789abc"
    monkeypatch.setattr(ble, "CoreBluetooth", SimpleNamespace(
        CBManagerStateUnknown=0, CBManagerStateResetting=1, CBManagerStatePoweredOn=5,
        CBUUID=SimpleNamespace(UUIDWithString_=lambda value: value)), raising=False)
    peripheral = SimpleNamespace(identifier=lambda: SimpleNamespace(UUIDString=lambda: identifier),
                                 name=lambda: "办公室键盘")
    for connected in (False, True):
        manager = SimpleNamespace(state=lambda: 5,
            retrieveConnectedPeripheralsWithServices_=lambda services: (
                [peripheral] if connected and services == [ble.HID_SERVICE_UUID_TEXT] else []))
        finder = ble.MacConnectedDeviceFinder(remembered_identifier=identifier)
        found = []
        finder.device_found.connect(lambda *args: found.append(args))
        finder._manager_state_changed(manager)
        assert found == ([(identifier, "办公室键盘")] if connected else [])


def test_connected_name_refresh_replaces_same_identifier(qtbot, contract):
    worker = BleWorker(contract)
    identifier = "12345678-1234-1234-1234-123456789abc"
    worker._add_connected_device(identifier, "BORING MIST")
    worker._add_connected_device(identifier, "Home keyboard")
    assert len(worker._candidates_by_port) == 1
    assert worker._candidates_by_port[f"ble:{identifier}"].description == "Home keyboard"


def test_completed_bluetooth_bootstrap_is_remembered_for_next_launch(
    qtbot, contract, tmp_path
):
    settings = QSettings(str(tmp_path / "ble.ini"), QSettings.IniFormat)
    worker = BleWorker(contract, settings=settings)
    identifier = "12345678-1234-1234-1234-123456789abc"
    snapshot = replace(
        _power_v2_snapshot(contract, read_only=False),
        port_name=f"ble:{identifier}",
    )

    worker._remember_completed_bluetooth(snapshot)

    assert settings.value(LAST_BLE_PORT_KEY) == f"ble:{identifier}"
    assert worker._remembered_identifier() == identifier


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


def test_connected_query_failure_reaches_ui_and_allows_usb_recovery(qtbot, contract):
    from controller_config.protocol.bootstrap import BootstrapKind

    class FailingBle(FakeGateway):
        def scan(self, *, preferred_port=None):
            self.scans += 1
            self.progress.emit("读取系统连接状态失败")
            self.failure.emit(BootstrapKind.READ_FAILED, "无法读取系统蓝牙连接状态", "权限不可用")

    usb, ble = FakeGateway(), FailingBle()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    failures, progress, found = [], [], []
    gateway.failure.connect(lambda *args: failures.append(args))
    gateway.progress.connect(progress.append)
    gateway.candidates_found.connect(found.append)
    gateway.scan()

    assert failures == [(BootstrapKind.READ_FAILED, "无法读取系统蓝牙连接状态", "权限不可用")]
    assert "读取系统连接状态失败" in progress
    assert found == []
    assert not gateway._ble_scan_active
    usb.candidates = (PortCandidate("cu.returned"),)
    gateway.scan(usb_only=True)
    assert found == [usb.candidates]
    assert ble.scans == 1


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
        def scan(self, *, preferred_port: str | None = None):
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


def test_reconnect_usb_probe_is_not_blocked_by_pending_bluetooth_scan(qtbot, contract: Contract) -> None:
    class PendingGateway(FakeGateway):
        def scan(self, *, preferred_port: str | None = None) -> None:
            self.scans += 1

    usb = PendingGateway()
    ble = PendingGateway()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble)
    found: list[tuple[PortCandidate, ...]] = []
    gateway.candidates_found.connect(found.append)

    gateway.scan_reconnect("ble:known-device")
    assert usb.scans == 1
    usb.candidates_found.emit(())
    assert ble.scans == 1

    # USB hot-plug must still be probed while the targeted BLE scan is pending.
    gateway.scan_reconnect("ble:known-device")
    assert usb.scans == 2
    returned = (PortCandidate("cu.returned"),)
    usb.candidates_found.emit(returned)

    assert found == [returned]
    assert ble.stops == 1


def test_preferred_bluetooth_candidate_finishes_reconnect_without_scan_timeout(
    qtbot, contract: Contract
) -> None:
    worker = BleWorker(contract)
    worker._scan_sources_pending = 1
    worker._preferred_port = "ble:12345678-1234-1234-1234-123456789abc"
    found: list[tuple[PortCandidate, ...]] = []
    worker.candidates_found.connect(found.append)

    worker._add_connected_device(
        "12345678-1234-1234-1234-123456789abc", "BORING MIST"
    )
    qtbot.waitUntil(lambda: bool(found), timeout=250)

    assert found[0][0].port_name == "ble:12345678-1234-1234-1234-123456789abc"
    assert worker._scan_sources_pending == 0


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


def test_preopen_bluetooth_link_drop_retries_instead_of_becoming_read_failure(contract, monkeypatch):
    from PySide6.QtSerialPort import QSerialPort
    from controller_config.transport import ble

    class CapturedSignal:
        def __init__(self) -> None:
            self.values = []

        def emit(self, *values: object) -> None:
            self.values.append(values)

    disconnected = CapturedSignal()
    failures = CapturedSignal()
    closed = []
    class FakeChannel:
        opened_once = False
        retryable_connection_error = True

        @staticmethod
        def errorString() -> str:
            return "蓝牙设备已断开"

    monkeypatch.setattr(ble, "BleChannel", FakeChannel)
    worker = SimpleNamespace(
        _closing=False,
        _session=None,
        _serial=FakeChannel(),
        disconnected=disconnected,
        failure=failures,
        close=lambda: closed.append(True),
    )

    ble.BleWorker._on_serial_error(worker, QSerialPort.ResourceError)

    assert disconnected.values == [("蓝牙设备已断开",)]
    assert failures.values == []
    assert closed == [True]


def test_channel_marks_only_preopen_disconnect_as_retryable() -> None:
    class CapturedSignal:
        def __init__(self) -> None:
            self.values = []

        def emit(self, *values: object) -> None:
            self.values.append(values)

    signal = CapturedSignal()
    channel = SimpleNamespace(
        _open=True,
        _closing=False,
        _opened_once=False,
        _retryable_connection_error=False,
        _error="",
        errorOccurred=signal,
    )

    BleChannel._on_disconnected(channel)

    assert channel._retryable_connection_error is True
    assert channel._error == "蓝牙设备已断开"
    assert signal.values


def test_preopen_bluetooth_service_error_remains_visible_failure(contract, monkeypatch):
    from PySide6.QtSerialPort import QSerialPort
    from controller_config.protocol.bootstrap import BootstrapKind
    from controller_config.transport import ble

    class CapturedSignal:
        def __init__(self) -> None:
            self.values = []

        def emit(self, *values: object) -> None:
            self.values.append(values)

    disconnected = CapturedSignal()
    failures = CapturedSignal()
    closed = []
    class FakeChannel:
        opened_once = False
        retryable_connection_error = False

        @staticmethod
        def errorString() -> str:
            return "设备的蓝牙配置特征不完整"

    monkeypatch.setattr(ble, "BleChannel", FakeChannel)
    worker = SimpleNamespace(
        _closing=False,
        _session=None,
        _serial=FakeChannel(),
        disconnected=disconnected,
        failure=failures,
        close=lambda: closed.append(True),
    )

    ble.BleWorker._on_serial_error(worker, QSerialPort.ResourceError)

    assert disconnected.values == []
    assert failures.values == [(
        BootstrapKind.READ_FAILED,
        "无法建立蓝牙配置连接",
        "设备的蓝牙配置特征不完整",
    )]
    assert closed == [True]


def test_macos_discovery_queries_only_native_connected_devices(qtbot, contract, monkeypatch):
    from controller_config.transport import ble
    monkeypatch.setattr(ble, "sys", SimpleNamespace(platform="darwin"))
    class NativeFinder(QObject):
        device_found = Signal(str, str)
        finished = Signal()
        def __init__(self, parent=None, *, remembered_identifier=""):
            super().__init__(parent)
        def start(self):
            self.device_found.emit("12345678-1234-1234-1234-123456789abc", "BORING MIST")
            self.finished.emit()
        def cancel(self):
            pass
    monkeypatch.setattr(ble, "MacConnectedDeviceFinder", NativeFinder)
    worker = BleWorker(contract)
    found = []
    worker.candidates_found.connect(found.append)
    worker.scan()
    assert len(found) == 1
    assert found[0][0].port_name == "ble:12345678-1234-1234-1234-123456789abc"
    assert not worker._scan_timer.isActive()
    worker.stop_scan()
    assert len(found) == 1
    # The fake finder finishes synchronously, before scan() returns.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_windows_discovery_uses_connected_system_query(qtbot, contract, monkeypatch):
    from controller_config.transport import ble
    monkeypatch.setattr(ble, "sys", SimpleNamespace(platform="win32"))
    class Finder(QObject):
        device_found = Signal(str, str)
        finished = Signal()
        failed = Signal(str)
        warning = Signal(str)
        def start(self):
            self.device_found.emit("aa:bb:cc:dd:ee:01", "Office keyboard")
            self.finished.emit()
        def cancel(self):
            pass
    monkeypatch.setattr(ble, "WindowsConnectedDeviceFinder", Finder)
    worker = BleWorker(contract)
    found = []
    worker.candidates_found.connect(found.append)
    worker.scan()
    assert found[0][0].port_name == "ble:aa:bb:cc:dd:ee:01"
    assert worker._device_info_by_port[found[0][0].port_name].address().toString() == "AA:BB:CC:DD:EE:01"
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_native_query_waits_for_power_then_finishes_once(qtbot, monkeypatch):
    from controller_config.transport import ble
    monkeypatch.setattr(ble, "CoreBluetooth", SimpleNamespace(
        CBManagerStateUnknown=0, CBManagerStateResetting=1, CBManagerStatePoweredOn=5,
        CBUUID=SimpleNamespace(UUIDWithString_=lambda value: value)), raising=False)
    class Manager:
        current_state = 0
        queries = 0
        def state(self):
            return self.current_state
        def retrieveConnectedPeripheralsWithServices_(self, services):
            self.queries += 1
            return []
        def setDelegate_(self, delegate):
            self.delegate = delegate
    finder = ble.MacConnectedDeviceFinder()
    manager = Manager()
    finder._manager = manager
    finished = []
    finder.finished.connect(lambda: finished.append(True))
    finder._manager_state_changed(manager)
    assert not finished and manager.queries == 0
    manager.current_state = 1
    finder._manager_state_changed(manager)
    assert not finished
    manager.current_state = 5
    finder._manager_state_changed(manager)
    assert finished == [True] and manager.queries == 2 and manager.delegate is None
    finder._manager_state_changed(manager)
    assert finished == [True] and manager.queries == 2


def test_connected_query_timeout_is_not_reported_as_just_no_devices(qtbot, contract):
    worker = BleWorker(contract)
    errors, candidates = [], []
    worker.failure.connect(lambda *args: errors.append(args))
    worker.candidates_found.connect(candidates.append)
    worker._scan_sources_pending = 1
    worker._connected_query_failed("系统查询超时")
    assert errors[0][1:] == ("无法读取系统蓝牙连接状态", "系统查询超时")
    assert candidates == [] and worker._scan_sources_pending == 0


def test_qt_channel_holds_early_reply_until_last_write_ack(qtbot):
    from collections import deque
    from PySide6.QtCore import QTimer

    # Use the actual channel methods with a simulated GATT service; no radio IO.
    class Channel(BleChannel):
        def __init__(self):
            QObject.__init__(self)
            self._rx, self._tx = object(), object()
            self._closing = False
            self._write_inflight = True
            self._write_chunks = deque([b"last"])
            self._incoming = bytearray()
            self._service = SimpleNamespace(writeCharacteristic=lambda *_: None)
            self._response_batch = QTimer(self)
            self._response_batch.setSingleShot(True)
            self._response_batch.setInterval(1)
            self._response_batch.timeout.connect(self._flush_received)

    channel = Channel()
    events = []
    channel.writeProgress.connect(lambda: events.append("progress"))
    channel.writeCompleted.connect(lambda: events.append("sent"))
    channel.readyRead.connect(lambda: events.append(bytes(channel.readAll())))
    channel._on_characteristic_changed(channel._tx, b"reply")
    qtbot.wait(10)
    assert events == []
    channel._on_characteristic_written(channel._rx, b"first")
    assert events == ["progress"]
    channel._on_characteristic_written(channel._rx, b"last")
    qtbot.waitUntil(lambda: b"reply" in events)
    assert events == ["progress", "progress", "sent", b"reply"]
