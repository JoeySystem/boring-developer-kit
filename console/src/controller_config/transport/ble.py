from __future__ import annotations

import sys
from collections import deque

from PySide6.QtBluetooth import (
    QBluetoothDeviceDiscoveryAgent,
    QBluetoothDeviceInfo,
    QBluetoothUuid,
    QLowEnergyCharacteristic,
    QLowEnergyController,
    QLowEnergyDescriptor,
    QLowEnergyService,
)
from PySide6.QtCore import QByteArray, QEventLoop, QObject, QTimer, QUuid, Signal, Slot
from PySide6.QtSerialPort import QSerialPort

from controller_config.models import DEVICE_DISPLAY_NAME, PortCandidate
from controller_config.protocol.bootstrap import BootstrapKind, BootstrapSession
from controller_config.protocol.contract import Contract
from controller_config.protocol.device_auth import DeviceAuthenticator
from controller_config.transport.qt_serial import SerialWorker


SERVICE_UUID_TEXT = "7e2f0001-7a91-4a5b-9c2d-6e8f4b524731"
RX_UUID_TEXT = "7e2f0002-7a91-4a5b-9c2d-6e8f4b524731"
TX_UUID_TEXT = "7e2f0003-7a91-4a5b-9c2d-6e8f4b524731"

SERVICE_UUID = QBluetoothUuid(QUuid(SERVICE_UUID_TEXT))
RX_UUID = QBluetoothUuid(QUuid(RX_UUID_TEXT))
TX_UUID = QBluetoothUuid(QUuid(TX_UUID_TEXT))
HID_SERVICE_UUID_TEXT = "1812"


def connected_service_uuid_texts() -> tuple[str, ...]:
    return SERVICE_UUID_TEXT, HID_SERVICE_UUID_TEXT


def _looks_like_boring_device(info: QBluetoothDeviceInfo) -> bool:
    if any(uuid == SERVICE_UUID for uuid in info.serviceUuids()):
        return True
    name = info.name().strip().casefold()
    return name.startswith(("boring ", "mist ", "codex micro "))


def _ble_identifier(info: QBluetoothDeviceInfo) -> str:
    device_uuid = info.deviceUuid()
    if not device_uuid.isNull():
        return device_uuid.toString(QUuid.WithoutBraces).lower()
    address = info.address()
    return "" if address.isNull() else address.toString().lower()


if sys.platform == "darwin":
    import CoreBluetooth
    import objc
    from Foundation import NSObject

    class _MacCentralDelegate(NSObject):
        def initWithOwner_(self, owner: object):
            self = objc.super(_MacCentralDelegate, self).init()
            if self is not None:
                self._owner = owner
            return self

        def centralManagerDidUpdateState_(self, manager: object) -> None:
            if self._owner is not None:
                self._owner._manager_state_changed(manager)

        def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(
            self, manager: object, peripheral: object, advertisement: object, rssi: object
        ) -> None:
            if self._owner is not None:
                self._owner._peripheral_discovered(peripheral, advertisement)


class MacConnectedDeviceFinder(QObject):
    """Discover BLE using CoreBluetooth, including existing macOS HID links.

    Qt discovery first calls legacy IOBluetoothHostController on the main thread;
    on affected macOS installations that call waits forever. CoreBluetooth uses
    asynchronous state/discovery callbacks without initializing that controller.
    """

    device_found = Signal(str, str)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = None
        self._delegate = None
        self._finished = False
        self._scanning = False
        self._found: set[str] = set()

    def start(self) -> None:
        if sys.platform != "darwin":
            self._finish()
            return
        self._delegate = _MacCentralDelegate.alloc().initWithOwner_(self)
        self._manager = CoreBluetooth.CBCentralManager.alloc().initWithDelegate_queue_options_(
            self._delegate, None, None
        )

    def _manager_state_changed(self, manager: object) -> None:
        if self._finished:
            return
        state = manager.state()
        if state in (CoreBluetooth.CBManagerStateUnknown, CoreBluetooth.CBManagerStateResetting):
            return  # Wait for a usable state; BleWorker's timer still bounds this.
        if state != CoreBluetooth.CBManagerStatePoweredOn:
            self._finish()
            return
        if self._scanning:
            return
        for service_uuid in connected_service_uuid_texts():
            service = CoreBluetooth.CBUUID.UUIDWithString_(service_uuid)
            for peripheral in manager.retrieveConnectedPeripheralsWithServices_([service]):
                identifier = str(peripheral.identifier().UUIDString()).lower()
                if identifier in self._found:
                    continue
                name = str(peripheral.name() or "").strip()
                # The configuration service identifies a candidate even after
                # renaming. The generic HID fallback still requires a BORING
                # legacy name; all candidates authenticate during bootstrap.
                if (service_uuid != SERVICE_UUID_TEXT
                        and not name.casefold().startswith(("boring ", "mist ", "codex micro "))):
                    continue
                self._found.add(identifier)
                self.device_found.emit(identifier, name)
        # An unfiltered BLE scan preserves the existing name-prefix fallback for
        # firmware that does not advertise the configuration service UUID.
        self._scanning = True
        manager.scanForPeripheralsWithServices_options_(None, None)

    def _peripheral_discovered(self, peripheral: object, advertisement: object) -> None:
        if self._finished:
            return
        name = str(advertisement.get(CoreBluetooth.CBAdvertisementDataLocalNameKey)
                   or peripheral.name() or "").strip()
        services = advertisement.get(CoreBluetooth.CBAdvertisementDataServiceUUIDsKey, ())
        has_service = any(str(service.UUIDString()).lower() == SERVICE_UUID_TEXT for service in services)
        if not has_service and not name.casefold().startswith(("boring ", "mist ", "codex micro ")):
            return
        identifier = str(peripheral.identifier().UUIDString()).lower()
        if identifier in self._found:
            return
        self._found.add(identifier)
        self.device_found.emit(identifier, name)

    def cancel(self) -> None:
        self._finished = True
        if self._manager is not None:
            if self._scanning:
                self._manager.stopScan()
                self._scanning = False
            self._manager.setDelegate_(None)
        if self._delegate is not None:
            self._delegate._owner = None

    def _finish(self) -> None:
        if self._finished:
            return
        self.cancel()
        self.finished.emit()


class BleChannel(QObject):
    """QSerialPort-shaped WMP byte channel backed by encrypted BLE GATT."""

    opened = Signal()
    readyRead = Signal()
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
        self._closing = False
        self._error = ""
        self._response_batch = QTimer(self)
        self._response_batch.setSingleShot(True)
        self._response_batch.setInterval(25)
        self._response_batch.timeout.connect(self.readyRead)
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

    def waitForBytesWritten(self, timeout_ms: int) -> bool:
        if not self._write_chunks and not self._write_inflight:
            return True
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)

        def poll() -> None:
            if not self._write_chunks and not self._write_inflight:
                loop.quit()

        poll_timer = QTimer()
        poll_timer.setInterval(5)
        poll_timer.timeout.connect(poll)
        poll_timer.start()
        timer.start(max(0, timeout_ms))
        loop.exec()
        return not self._write_chunks and not self._write_inflight

    def close(self) -> None:
        self._closing = True
        self._open = False
        self._write_chunks.clear()
        self._write_inflight = False
        if self._controller.state() != QLowEnergyController.UnconnectedState:
            self._controller.disconnectFromDevice()

    @Slot()
    def _on_connected(self) -> None:
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
            self._fail("设备的蓝牙配置特征不完整，请先通过 USB 更新固件")
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
        if characteristic != self._tx:
            return
        self._incoming.extend(bytes(value))
        self._response_batch.start()

    @Slot(object, object)
    def _on_characteristic_written(self, characteristic: object, _value: object) -> None:
        if characteristic != self._rx:
            return
        self._write_inflight = False
        self._send_next_chunk()

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
            if not self._error:
                self._error = "蓝牙设备已断开"
            self.errorOccurred.emit(QSerialPort.ResourceError)

    @Slot(object)
    def _on_controller_error(self, _error: object) -> None:
        if not self._closing:
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
    ) -> None:
        super().__init__(contract, authenticator)
        self.setParent(parent)
        self._device_info_by_port: dict[str, QBluetoothDeviceInfo] = {}
        self._discovery: QBluetoothDeviceDiscoveryAgent | None = None
        self._connected_finder: MacConnectedDeviceFinder | None = None
        self._scan_timer: QTimer | None = None
        self._scan_sources_pending = 0

    @Slot()
    def scan(self) -> None:
        self.close()
        self.stop_scan()
        self.progress.emit("正在查找已配对的 BORING 蓝牙设备…")
        self._candidates_by_port.clear()
        self._device_info_by_port.clear()
        self._scan_sources_pending = 1
        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.setInterval(6000)
        self._scan_timer.timeout.connect(self.stop_scan)
        self._scan_timer.start()

        if sys.platform == "darwin":
            finder = MacConnectedDeviceFinder(self)
            finder.device_found.connect(self._add_connected_device)
            finder.finished.connect(self._scan_source_finished)
            self._connected_finder = finder
            finder.start()
        else:
            discovery = QBluetoothDeviceDiscoveryAgent(self)
            discovery.deviceDiscovered.connect(self._add_discovered_device)
            discovery.finished.connect(self._scan_source_finished)
            discovery.errorOccurred.connect(self._scan_source_finished)
            self._discovery = discovery
            discovery.start(QBluetoothDeviceDiscoveryAgent.LowEnergyMethod)

    @Slot()
    def stop_scan(self) -> None:
        if self._discovery is not None:
            for signal, slot in (
                (self._discovery.deviceDiscovered, self._add_discovered_device),
                (self._discovery.finished, self._scan_source_finished),
                (self._discovery.errorOccurred, self._scan_source_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            if self._discovery.isActive():
                self._discovery.stop()
            self._discovery.deleteLater()
            self._discovery = None
        if self._connected_finder is not None:
            self._connected_finder.cancel()
            self._connected_finder.deleteLater()
            self._connected_finder = None
        if self._scan_timer is not None:
            self._scan_timer.stop()
            self._scan_timer.deleteLater()
            self._scan_timer = None
        if self._scan_sources_pending:
            self._scan_sources_pending = 0
            self.candidates_found.emit(tuple(self._candidates_by_port.values()))

    @Slot(object)
    def _add_discovered_device(self, info: QBluetoothDeviceInfo) -> None:
        if not _looks_like_boring_device(info):
            return
        identifier = _ble_identifier(info)
        if not identifier:
            return
        self._store_device(identifier, info)

    @Slot(str, str)
    def _add_connected_device(self, identifier: str, name: str) -> None:
        info = QBluetoothDeviceInfo(QUuid(identifier), name, 0)
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
        channel = BleChannel(info, self)
        channel.readyRead.connect(self._on_ready_read)
        channel.errorOccurred.connect(self._on_serial_error)
        channel.opened.connect(lambda: self._start_bootstrap(port_name))
        self._serial = channel
        self.progress.emit(
            f"正在通过蓝牙连接 {info.name() or DEVICE_DISPLAY_NAME}…"
        )
        channel.start()

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
            and isinstance(channel, BleChannel)
            and not channel.opened_once
        ):
            detail = channel.errorString().strip() or "系统未提供蓝牙错误详情"
            self.failure.emit(
                BootstrapKind.READ_FAILED,
                "无法建立蓝牙配置连接",
                detail,
            )
            self.close()
            return
        super()._on_serial_error(error)

    @Slot()
    def _check_presence(self) -> None:
        # CoreBluetooth/Qt emits a disconnect event; rescanning can otherwise
        # miss an already-connected HID peripheral between advertisements.
        return

    @Slot()
    def close(self) -> None:
        self.stop_scan()
        super().close()


class BleGateway(QObject):
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

    def __init__(
        self,
        contract: Contract,
        authenticator: DeviceAuthenticator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = BleWorker(contract, authenticator, self)
        for name in (
            "agent_status_completed", "agent_status_failed", "candidates_found",
            "progress", "snapshot_ready", "status_updated", "command_completed",
            "command_failed", "failure", "disconnected",
        ):
            getattr(self._worker, name).connect(getattr(self, name))

    def scan(self) -> None:
        self._worker.scan()

    def stop_scan(self) -> None:
        self._worker.stop_scan()

    def connect_port(self, port_name: str) -> None:
        self._worker.connect_port(port_name)

    def execute_command(self, command: object) -> None:
        self._worker.execute_command(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self._worker.set_status_poll_interval(interval_ms)

    def shutdown(self) -> None:
        self._worker.close()


def split_att_payload(data: bytes, mtu: int) -> tuple[bytes, ...]:
    capacity = max(20, int(mtu) - 3)
    return tuple(
        data[offset : offset + capacity]
        for offset in range(0, len(data), capacity)
    )
