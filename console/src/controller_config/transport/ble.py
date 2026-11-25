from __future__ import annotations

import sys
import os
import time
from collections import deque

from PySide6.QtBluetooth import (
    QBluetoothAddress,
    QBluetoothDeviceInfo,
    QBluetoothUuid,
    QLowEnergyCharacteristic,
    QLowEnergyController,
    QLowEnergyDescriptor,
    QLowEnergyService,
)
from PySide6.QtCore import (
    QByteArray,
    QMetaObject,
    QObject,
    QSettings,
    QThread,
    QTimer,
    Qt,
    QUuid,
    Signal,
    Slot,
)
from PySide6.QtSerialPort import QSerialPort

from controller_config.connect_timing import TimingMark
from controller_config.models import DEVICE_DISPLAY_NAME, PortCandidate
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind, BootstrapSession, Command
from controller_config.protocol.contract import Contract
from controller_config.protocol.device_auth import DeviceAuthenticator
from controller_config.transport.qt_serial import SerialWorker
from controller_config.transport.windows_connected import WindowsConnectedDeviceFinder


SERVICE_UUID_TEXT = "7e2f0001-7a91-4a5b-9c2d-6e8f4b524731"
RX_UUID_TEXT = "7e2f0002-7a91-4a5b-9c2d-6e8f4b524731"
TX_UUID_TEXT = "7e2f0003-7a91-4a5b-9c2d-6e8f4b524731"

SERVICE_UUID = QBluetoothUuid(QUuid(SERVICE_UUID_TEXT))
RX_UUID = QBluetoothUuid(QUuid(RX_UUID_TEXT))
TX_UUID = QBluetoothUuid(QUuid(TX_UUID_TEXT))
HID_SERVICE_UUID_TEXT = "1812"
LAST_BLE_PORT_KEY = "devices/lastBluetoothPort"


def connected_service_uuid_texts() -> tuple[str, ...]:
    return SERVICE_UUID_TEXT, HID_SERVICE_UUID_TEXT


if sys.platform == "darwin":
    from controller_config.transport.mac_ble_channel import MacBleChannel
    import CoreBluetooth
    import objc
    from Foundation import NSObject
    from dispatch import dispatch_queue_create, DISPATCH_QUEUE_SERIAL

    class _MacCentralDelegate(NSObject):
        def initWithOwner_(self, owner: object):
            self = objc.super(_MacCentralDelegate, self).init()
            if self is not None:
                self._owner = owner
            return self

        def centralManagerDidUpdateState_(self, manager: object) -> None:
            owner = self._owner
            if owner is not None:
                owner.nativeStateChanged.emit(manager)

    def _create_mac_central() -> tuple[object, object]:
        delegate = _MacCentralDelegate.alloc().initWithOwner_(None)
        # Quick3D can stall native main-queue callbacks after initial startup.
        # Receive on a serial queue, and keep enumeration/Qt signals on Qt's thread.
        queue = dispatch_queue_create(b"com.boring.console.connected", DISPATCH_QUEUE_SERIAL)
        manager = CoreBluetooth.CBCentralManager.alloc().initWithDelegate_queue_options_(
            delegate, queue, None
        )
        # The queue must outlive the manager; keep it reachable from the delegate.
        delegate._queue = queue
        return manager, delegate

    def _mac_central_is_settled(manager: object) -> bool:
        return manager.state() not in (
            CoreBluetooth.CBManagerStateUnknown, CoreBluetooth.CBManagerStateResetting,
        )


class SharedMacCentral:
    """A-07: one CBCentralManager for the process lifetime.

    Creating a manager per reconnect scan re-initialises CoreBluetooth every
    few hundred milliseconds. The shared delegate forwards state callbacks to
    whichever finder currently owns it.
    """

    factory = None  # () -> (manager, delegate); tests may replace it
    settled = None  # (manager) -> bool
    _manager = None
    _delegate = None
    creations = 0

    @classmethod
    def acquire(cls, owner: object) -> tuple[object, bool]:
        if cls._manager is None:
            factory = cls.factory or _create_mac_central
            cls._manager, cls._delegate = factory()
            cls.creations += 1
        cls._delegate._owner = owner
        is_settled = cls.settled or _mac_central_is_settled
        return cls._manager, bool(is_settled(cls._manager))

    @classmethod
    def release(cls, owner: object) -> None:
        if cls._delegate is not None and getattr(cls._delegate, "_owner", None) is owner:
            cls._delegate._owner = None

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._manager = None
        cls._delegate = None
        cls.creations = 0



class MacConnectedDeviceFinder(QObject):
    """Enumerate existing macOS links without discovering or connecting neighbours."""

    device_found = Signal(str, str)
    finished = Signal()
    nativeStateChanged = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        remembered_identifier: str = "",
    ) -> None:
        super().__init__(parent)
        self._manager = None
        self.nativeStateChanged.connect(self._manager_state_changed, Qt.QueuedConnection)
        self._remembered_identifier = remembered_identifier
        self._finished = False
        self._found: dict[str, str] = {}

    def start(self) -> None:
        if sys.platform != "darwin":
            self._finish()
            return
        self._manager, settled = SharedMacCentral.acquire(self)
        if settled:
            # A reused manager will not call centralManagerDidUpdateState_
            # again; enumerate from its current state (queued, like a callback).
            self.nativeStateChanged.emit(self._manager)

    @Slot(object)
    def _manager_state_changed(self, manager: object) -> None:
        if self._finished:
            return
        state = manager.state()
        if state in (CoreBluetooth.CBManagerStateUnknown, CoreBluetooth.CBManagerStateResetting):
            return  # Wait for a usable state; BleWorker's timer still bounds this.
        if state != CoreBluetooth.CBManagerStatePoweredOn:
            self._finish()
            return
        for service_uuid in connected_service_uuid_texts():
            service = CoreBluetooth.CBUUID.UUIDWithString_(service_uuid)
            for peripheral in manager.retrieveConnectedPeripheralsWithServices_([service]):
                identifier = str(peripheral.identifier().UUIDString()).lower()
                if identifier in self._found:
                    continue
                name = str(peripheral.name() or "").strip()
                # The configuration service identifies a candidate even after
                # renaming. HID needs a BORING name or an authenticated historical
                # identity, but is always sourced from the current connected list.
                if (service_uuid != SERVICE_UUID_TEXT
                        and identifier != self._remembered_identifier
                        and not name.casefold().startswith(("boring ", "mist ", "codex micro "))):
                    continue
                self._found[identifier] = name
                self.device_found.emit(identifier, name)
        self._finish()

    def cancel(self) -> None:
        self._finished = True
        # The manager is shared (A-07); only detach this finder from it.
        SharedMacCentral.release(self)

    def _finish(self) -> None:
        if self._finished:
            return
        self.cancel()
        self.finished.emit()


class BleChannel(QObject):
    """QSerialPort-shaped WMP byte channel backed by encrypted BLE GATT."""

    opened = Signal()
    readyRead = Signal()
    writeProgress = Signal()
    writeCompleted = Signal()
    errorOccurred = Signal(object)

    def __init__(self, info: QBluetoothDeviceInfo, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = QLowEnergyController.createCentral(info, self)
        self._service: QLowEnergyService | None = None
        self._rx = QLowEnergyCharacteristic()
        self._tx = QLowEnergyCharacteristic()
        self._ccc = QLowEnergyDescriptor()
        self._incoming = bytearray()
        self._write_chunks: deque[bytes] = deque()
        self._write_inflight = False
        self._open = False
        self._opened_once = False
        self._retryable_connection_error = False
        self._closing = False
        self._error = ""
        self._response_batch = QTimer(self)
        self._response_batch.setSingleShot(True)
        self._response_batch.setInterval(25)
        self._response_batch.timeout.connect(self._flush_received)
        self._controller.connected.connect(self._on_connected)
        self._controller.disconnected.connect(self._on_disconnected)
        self._controller.discoveryFinished.connect(self._on_services_discovered)
        self._controller.errorOccurred.connect(self._on_controller_error)

    def start(self) -> None:
        self._controller.connectToDevice()

    def isOpen(self) -> bool:
        return self._open

    @property
    def opened_once(self) -> bool:
        return self._opened_once

    @property
    def retryable_connection_error(self) -> bool:
        return self._retryable_connection_error

    def errorString(self) -> str:
        return self._error or self._controller.errorString()

    def readAll(self) -> QByteArray:
        value = bytes(self._incoming)
        self._incoming.clear()
        return QByteArray(value)

    def write(self, data: bytes | bytearray | QByteArray) -> int:
        value = bytes(data)
        if not self._open or self._service is None or not self._rx.isValid():
            self._error = "蓝牙配置通道尚未就绪"
            return -1
        if self._write_chunks or self._write_inflight:
            self._error = "上一条蓝牙请求仍在发送"
            return -1
        self._write_chunks.extend(split_att_payload(value, self._controller.mtu()))
        self._send_next_chunk()
        return len(value)

    def close(self) -> None:
        self._closing = True
        self._open = False
        self._response_batch.stop()
        self._write_chunks.clear()
        self._write_inflight = False
        if self._controller.state() != QLowEnergyController.UnconnectedState:
            self._controller.disconnectFromDevice()

    @Slot()
    def _on_connected(self) -> None:
        self._retryable_connection_error = False
        self._controller.discoverServices()

    @Slot()
    def _on_services_discovered(self) -> None:
        if SERVICE_UUID not in self._controller.services():
            self._fail("设备固件尚未提供蓝牙配置服务，请先通过 USB 更新固件")
            return
        service = self._controller.createServiceObject(SERVICE_UUID, self)
        if service is None:
            self._fail("无法打开设备的蓝牙配置服务")
            return
        self._service = service
        service.stateChanged.connect(self._on_service_state_changed)
        service.characteristicChanged.connect(self._on_characteristic_changed)
        service.characteristicWritten.connect(self._on_characteristic_written)
        service.descriptorWritten.connect(self._on_descriptor_written)
        service.errorOccurred.connect(self._on_service_error)
        service.discoverDetails()

    @Slot(object)
    def _on_service_state_changed(self, state: object) -> None:
        if state != QLowEnergyService.RemoteServiceDiscovered or self._service is None:
            return
        self._rx = self._service.characteristic(RX_UUID)
        self._tx = self._service.characteristic(TX_UUID)
        self._ccc = self._tx.descriptor(
            QBluetoothUuid(QBluetoothUuid.ClientCharacteristicConfiguration)
        )
        if not self._rx.isValid() or not self._tx.isValid() or not self._ccc.isValid():
            self._fail("无法完成蓝牙配置服务发现，请重新连接或改用 USB。")
            return
        self._service.writeDescriptor(self._ccc, QByteArray(b"\x02\x00"))

    @Slot(object, object)
    def _on_descriptor_written(self, descriptor: object, value: object) -> None:
        if descriptor != self._ccc or bytes(value) != b"\x02\x00":
            return
        self._open = True
        self._opened_once = True
        self.opened.emit()

    @Slot(object, object)
    def _on_characteristic_changed(self, characteristic: object, value: object) -> None:
        if self._closing or characteristic != self._tx:
            return
        self._incoming.extend(bytes(value))
        # Feed the incremental frame decoder as soon as an indication arrives.
        # Keep the existing write-ACK gate: replies arriving during a request
        # are drained by _on_characteristic_written after its last chunk.
        self._flush_received()

    @Slot(object, object)
    def _on_characteristic_written(self, characteristic: object, _value: object) -> None:
        if self._closing or characteristic != self._rx or not self._write_inflight:
            return
        self._write_inflight = False
        self.writeProgress.emit()
        self._send_next_chunk()
        if not self._write_inflight and not self._write_chunks:
            self.writeCompleted.emit()
            if self._incoming:
                self._flush_received()

    def _flush_received(self) -> None:
        if not self._closing and not self._write_inflight and not self._write_chunks and self._incoming:
            self.readyRead.emit()

    def _send_next_chunk(self) -> None:
        if self._write_inflight or not self._write_chunks or self._service is None:
            return
        self._write_inflight = True
        self._service.writeCharacteristic(
            self._rx,
            QByteArray(self._write_chunks.popleft()),
            QLowEnergyService.WriteWithResponse,
        )

    @Slot()
    def _on_disconnected(self) -> None:
        self._open = False
        if not self._closing:
            self._retryable_connection_error = not self._opened_once
            if not self._error:
                self._error = "蓝牙设备已断开"
            self.errorOccurred.emit(QSerialPort.ResourceError)

    @Slot(object)
    def _on_controller_error(self, error: object) -> None:
        if not self._closing:
            self._retryable_connection_error = error in {
                QLowEnergyController.ConnectionError,
                QLowEnergyController.RemoteHostClosedError,
                QLowEnergyController.UnknownRemoteDeviceError,
            }
            self._fail(self._controller.errorString() or "蓝牙连接失败")

    @Slot(object)
    def _on_service_error(self, _error: object) -> None:
        if self._service is not None and not self._closing:
            self._fail(f"蓝牙配置通信失败：{self._service.error().name}")

    def _fail(self, message: str) -> None:
        self._error = message
        self.errorOccurred.emit(QSerialPort.ResourceError)


class BleWorker(SerialWorker):
    def __init__(
        self,
        contract: Contract,
        authenticator: DeviceAuthenticator | None = None,
        parent: QObject | None = None,
        *,
        settings: QSettings | None = None,
        config_cache: object | None = None,
    ) -> None:
        super().__init__(contract, authenticator, config_cache=config_cache)
        self.setParent(parent)
        self._settings = settings if settings is not None else QSettings()
        self._device_info_by_port: dict[str, QBluetoothDeviceInfo] = {}
        self._connected_finder: MacConnectedDeviceFinder | WindowsConnectedDeviceFinder | None = None
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self._connected_query_timeout)
        self._scan_sources_pending = 0
        self._preferred_port = ""
        self._sending = False
        self._reply_timeout_ms = 2500
        self._reply_deadline = 0.0
        self.snapshot_ready.connect(self._remember_completed_bluetooth)

    @Slot()
    def scan(self, *, preferred_port: str | None = None) -> None:
        self.close()
        self.stop_scan()
        self._preferred_port = preferred_port or ""
        self.progress.emit("正在读取本机已连接的 BORING 蓝牙设备…")
        self._candidates_by_port.clear()
        self._device_info_by_port.clear()
        self._scan_sources_pending = 1
        self._scan_timer.setInterval(6000)
        self._scan_timer.start()

        if sys.platform == "darwin":
            finder = MacConnectedDeviceFinder(
                self, remembered_identifier=self._remembered_identifier(),
            )
        elif sys.platform == "win32":
            finder = WindowsConnectedDeviceFinder(self)
            finder.failed.connect(self._connected_query_failed)
            finder.warning.connect(self.progress)
            self._scan_timer.setInterval(12000)
        else:
            self._connected_query_failed("当前系统暂不支持读取已连接的蓝牙设备，请使用 USB。")
            return
        finder.device_found.connect(self._add_connected_device)
        finder.finished.connect(self._scan_source_finished)
        self._connected_finder = finder
        finder.start()

    @Slot()
    def _connected_query_timeout(self) -> None:
        self._connected_query_failed("读取系统蓝牙连接状态超时。", retryable=True)

    def _connected_query_failed(self, detail: str, *, retryable: bool = False) -> None:
        self.progress.emit(detail)
        self._scan_sources_pending = 0
        self.stop_scan()
        kind = BootstrapKind.DISCOVERY_INTERRUPTED if retryable else BootstrapKind.READ_FAILED
        self.failure.emit(kind, "无法读取系统蓝牙连接状态", detail)

    def _remembered_identifier(self) -> str:
        port_name = str(self._settings.value(LAST_BLE_PORT_KEY, "") or "")
        if not port_name.startswith("ble:"):
            return ""
        identifier = port_name.removeprefix("ble:").strip().lower()
        return identifier if identifier and not QUuid(identifier).isNull() else ""

    @Slot(object)
    def _remember_completed_bluetooth(self, snapshot: object) -> None:
        port_name = str(getattr(snapshot, "port_name", "") or "")
        if not port_name.startswith("ble:"):
            return
        identifier = port_name.removeprefix("ble:").strip().lower()
        if not identifier or QUuid(identifier).isNull():
            return
        self._settings.setValue(LAST_BLE_PORT_KEY, f"ble:{identifier}")
        self._settings.sync()

    @Slot()
    def stop_scan(self) -> None:
        if self._connected_finder is not None:
            self._connected_finder.cancel()
            self._connected_finder.deleteLater()
            self._connected_finder = None
        self._scan_timer.stop()
        if self._scan_sources_pending:
            self._scan_sources_pending = 0
            self.candidates_found.emit(tuple(self._candidates_by_port.values()))
        self._preferred_port = ""

    @Slot(str, str)
    def _add_connected_device(self, identifier: str, name: str) -> None:
        device_id = QBluetoothAddress(identifier) if ":" in identifier else QUuid(identifier)
        info = QBluetoothDeviceInfo(device_id, name, 0)
        info.setCoreConfigurations(QBluetoothDeviceInfo.CoreConfiguration.LowEnergyCoreConfiguration)
        self._store_device(identifier, info)

    def _store_device(self, identifier: str, info: QBluetoothDeviceInfo) -> None:
        port_name = f"ble:{identifier}"
        self._device_info_by_port[port_name] = info
        self._candidates_by_port[port_name] = PortCandidate(
            port_name=port_name,
            description=info.name() or DEVICE_DISPLAY_NAME,
            transport="bluetooth",
        )
        if port_name == self._preferred_port:
            # Avoid nesting the candidates signal inside the discovery callback.
            QTimer.singleShot(0, self.stop_scan)

    @Slot()
    def _scan_source_finished(self, *_args: object) -> None:
        if self._scan_sources_pending <= 0:
            return
        if self._scan_sources_pending == 1:
            self.stop_scan()
        else:
            self._scan_sources_pending -= 1

    @Slot(str)
    def connect_port(self, port_name: str) -> None:
        self.close()
        self.stop_scan()
        info = self._device_info_by_port.get(port_name)
        if info is None:
            self.failure.emit(
                BootstrapKind.READ_FAILED,
                "找不到这台蓝牙设备",
                "请确认设备已在系统蓝牙设置中配对并重新扫描。",
            )
            return
        self._closing = False
        self._port_name = port_name
        self._decoder.reset()
        self._timing = [TimingMark("connect", time.monotonic(), "bluetooth")]
        self.connect_step.emit("connect", "bluetooth")
        channel = MacBleChannel(info, self) if sys.platform == "darwin" else BleChannel(info, self)
        channel.readyRead.connect(self._on_ready_read)
        channel.writeProgress.connect(self._on_write_progress)
        channel.writeCompleted.connect(self._on_write_completed)
        channel.errorOccurred.connect(self._on_serial_error)
        channel.opened.connect(lambda: self._start_bootstrap(port_name))
        self._serial = channel
        self.progress.emit(
            f"正在通过蓝牙连接 {info.name() or DEVICE_DISPLAY_NAME}…"
        )
        channel.start()

    # This is a per-chunk inactivity deadline, separate from the command's
    # response budget. A large configuration may take several seconds to send.
    SEND_TIMEOUT_MS = 2500

    # A reply arrives one indication at a time and each one waits for its
    # confirmation, so the command's timeout budgets the silence between
    # pieces, like SEND_TIMEOUT_MS for writes. A link that keeps trickling is
    # stopped here: 60 s covers the largest reply (16 KB config, 33 packets at
    # MTU 517) at 1.8 s per packet, 14 times the measured 127 ms. Beyond that
    # the link cannot serve a configuration session anyway.
    REPLY_TOTAL_TIMEOUT_MS = 60_000

    def _send_command(self, command: Command, request_id: int) -> None:
        self._sending = True
        self._reply_timeout_ms = max(100, command.timeout_ms)
        super()._send_command(command, request_id)
        self._on_write_progress()

    @Slot()
    def _on_write_progress(self) -> None:
        if self._sending and not self._closing and self._request_timer is not None:
            self._request_timer.start(self.SEND_TIMEOUT_MS)

    @Slot()
    def _on_write_completed(self) -> None:
        if not self._sending or self._closing:
            return
        self._sending = False
        self._reply_deadline = time.monotonic() + self.REPLY_TOTAL_TIMEOUT_MS / 1000
        if self._request_timer is not None:
            self._request_timer.start(self._reply_timeout_ms)

    def _note_incoming(self, data: bytes) -> None:
        timer = self._request_timer
        if not data or self._sending or self._closing or timer is None or not timer.isActive():
            return
        remaining_ms = int((self._reply_deadline - time.monotonic()) * 1000)
        timer.start(max(0, min(self._reply_timeout_ms, remaining_ms)))

    @Slot()
    def _on_request_timeout(self) -> None:
        if self._sending:
            command = self._session.current_command if self._session is not None else None
            detail = f"{command.name if command else 'WMP'}: Bluetooth write acknowledgement timed out"
            if isinstance(self._session, BootstrapSession) and self._session.authenticating_over_ble:
                self._session.authentication_interrupted(detail)
            else:
                # Do not finish the session and advance its queue while the
                # transport still owns an unfinished frame. Never replay writes.
                self._on_bootstrap_failed(BootstrapError(
                    BootstrapKind.READ_FAILED,
                    "蓝牙请求发送超时，请重新连接后重试。",
                    detail,
                    error_name="TRANSPORT_TIMEOUT",
                ))
            return
        super()._on_request_timeout()

    @Slot(object)
    def _on_serial_error(self, error: object) -> None:
        channel = self._serial
        if (not self._closing and error == QSerialPort.ResourceError
                and isinstance(self._session, BootstrapSession)
                and self._session.authenticating_over_ble):
            self._session.authentication_interrupted(
                channel.errorString() if channel is not None else "蓝牙连接中断")
            return
        if (
            not self._closing
            and error == QSerialPort.ResourceError
            and channel is not None
            and not channel.opened_once
        ):
            detail = channel.errorString().strip() or "系统未提供蓝牙错误详情"
            retryable = channel.retryable_connection_error
            self.close()
            if retryable:
                self.disconnected.emit(detail)
            else:
                self.failure.emit(
                    BootstrapKind.READ_FAILED,
                    "无法建立蓝牙配置连接",
                    detail,
                )
            return
        super()._on_serial_error(error)

    @Slot()
    def _check_presence(self) -> None:
        # CoreBluetooth/Qt emits a disconnect event; rescanning can otherwise
        # miss an already-connected HID peripheral between advertisements.
        return

    def _flush_before_close(self, serial: object) -> None:
        # A-05: never spin a nested event loop while closing a GATT link. The
        # best-effort CLEAR is queued on the channel; the device lease expires
        # if it does not complete before disconnect.
        return

    @Slot(str)
    def scan_preferred(self, preferred_port: str) -> None:
        self.scan(preferred_port=preferred_port or None)

    @Slot(str)
    def connect_requested(self, port_name: str) -> None:
        # The gateway's signal must connect to this, not to connect_port.
        # PySide 6.11 calls a @Slot that overrides a base class @Slot directly
        # on the emitting thread, even with Qt.QueuedConnection. That put the
        # channel and the session timers on the GUI thread, where the worker
        # could not start them: no request timeouts and no GET_STATUS polling.
        self.connect_port(port_name)

    link_closed = Signal(int)

    @Slot(int)
    def close_for_handoff(self, token: int) -> None:
        self.close()
        self.link_closed.emit(token)

    @Slot()
    def close(self) -> None:
        self._sending = False
        self.stop_scan()
        super().close()

    def _dispose_serial(self) -> None:
        channel = self._serial
        super()._dispose_serial()
        # A GATT channel can own native callbacks while still discovering or
        # after a failed subscription; isOpen() is false in both cases.
        if channel is not None:
            channel.close()


def ble_worker_thread_enabled() -> bool:
    """A-05: BLE runs on its own QThread. ``BORING_BLE_WORKER_THREAD=0`` keeps
    the previous GUI-thread worker as an acceptance-time fallback."""

    return os.environ.get("BORING_BLE_WORKER_THREAD", "1").strip() not in {"0", "false", "no"}


class BleGateway(QObject):
    """GUI-thread facade; the BleWorker, its GATT channel, timers and protocol
    sessions live on a dedicated thread and are reached only through queued
    signals (A-05)."""

    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)
    candidates_found = Signal(object)
    progress = Signal(str)
    connect_step = Signal(str, str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)
    link_warning = Signal(str, str)

    _scan_requested = Signal(str)
    _stop_scan_requested = Signal()
    _connect_requested = Signal(str)
    _command_requested = Signal(object)
    _status_interval_requested = Signal(int)
    _close_requested = Signal(int)

    def __init__(
        self,
        contract: Contract,
        authenticator: DeviceAuthenticator | None = None,
        parent: QObject | None = None,
        *,
        threaded: bool | None = None,
        config_cache: object | None = None,
    ) -> None:
        super().__init__(parent)
        use_thread = ble_worker_thread_enabled() if threaded is None else threaded
        self._worker = BleWorker(contract, authenticator, config_cache=config_cache)
        self._thread: QThread | None = None
        if use_thread:
            self._thread = QThread(self)
            self._thread.setObjectName("boring-ble-worker")
            self._worker.moveToThread(self._thread)
            self._thread.finished.connect(self._worker.deleteLater)
        else:
            self._worker.setParent(self)
        self._scan_requested.connect(self._worker.scan_preferred)
        self._stop_scan_requested.connect(self._worker.stop_scan)
        self._connect_requested.connect(self._worker.connect_requested)
        self._command_requested.connect(self._worker.execute_command)
        self._status_interval_requested.connect(self._worker.set_status_poll_interval)
        self._close_requested.connect(self._worker.close_for_handoff)
        for name in (
            "agent_status_completed", "agent_status_failed", "candidates_found",
            "progress", "connect_step", "snapshot_ready", "status_updated", "command_completed",
            "command_failed", "failure", "disconnected", "link_warning",
        ):
            getattr(self._worker, name).connect(getattr(self, name))
        self._worker.link_closed.connect(self._on_link_closed)
        self._close_token = 0
        self._after_close: dict[int, object] = {}
        if self._thread is not None:
            self._thread.start()

    @property
    def threaded(self) -> bool:
        return self._thread is not None

    def scan(self, *, preferred_port: str | None = None) -> None:
        self._scan_requested.emit(preferred_port or "")

    def stop_scan(self) -> None:
        self._stop_scan_requested.emit()

    def connect_port(self, port_name: str) -> None:
        self._connect_requested.emit(port_name)

    def execute_command(self, command: object) -> None:
        self._command_requested.emit(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self._status_interval_requested.emit(interval_ms)

    def close_link(self, then: object | None = None) -> None:
        """Release the BLE link without blocking the GUI thread; ``then`` runs
        on the GUI thread once the worker has closed (e.g. a USB handoff)."""

        self._close_token += 1
        if callable(then):
            self._after_close[self._close_token] = then
        self._close_requested.emit(self._close_token)

    @Slot(int)
    def _on_link_closed(self, token: int) -> None:
        callback = self._after_close.pop(token, None)
        if callable(callback):
            callback()

    def shutdown(self) -> None:
        self._after_close.clear()
        if self._thread is None:
            self._worker.close()
            return
        if not self._thread.isRunning():
            return
        QMetaObject.invokeMethod(self._worker, "close", Qt.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait(3000)


def split_att_payload(data: bytes, mtu: int) -> tuple[bytes, ...]:
    capacity = max(20, int(mtu) - 3)
    return tuple(
        data[offset : offset + capacity]
        for offset in range(0, len(data), capacity)
    )
