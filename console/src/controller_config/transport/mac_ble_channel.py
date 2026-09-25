"""CoreBluetooth WMP channel for devices already connected by macOS.

Subscribe through CoreBluetooth instead of Qt's descriptor-value discovery,
which can time out on the HID + WMP peripheral despite a valid CCCD.
The existing SerialWorker still owns framing, authentication and readback.
"""
from collections import deque

import CoreBluetooth as CB
import objc
from dispatch import dispatch_queue_create, DISPATCH_QUEUE_SERIAL
from Foundation import NSData, NSObject
from PySide6.QtCore import QByteArray, QEventLoop, QObject, QTimer, QUuid, Signal, Slot, Qt
from PySide6.QtSerialPort import QSerialPort

SERVICE = "7e2f0001-7a91-4a5b-9c2d-6e8f4b524731"
RX = "7e2f0002-7a91-4a5b-9c2d-6e8f4b524731"
TX = "7e2f0003-7a91-4a5b-9c2d-6e8f4b524731"


class _WmpPeripheralDelegate(NSObject):
    def initWithOwner_(self, owner):
        self = objc.super(_WmpPeripheralDelegate, self).init()
        if self is not None:
            self.owner = owner
        return self

    @objc.python_method
    def _dispatch(self, method, *args):
        owner = self.owner
        if owner is not None:
            owner.nativeEvent.emit(method, args)

    def centralManagerDidUpdateState_(self, manager):
        self._dispatch("_manager_ready", manager)

    def centralManager_didConnectPeripheral_(self, manager, peripheral):
        self._dispatch("_connected", peripheral)

    def centralManager_didFailToConnectPeripheral_error_(self, manager, peripheral, error):
        self._dispatch("_fail", "无法建立蓝牙配置连接", error)

    def centralManager_didDisconnectPeripheral_error_(self, manager, peripheral, error):
        self._dispatch("_fail", "蓝牙配置连接已断开", error)

    def peripheral_didDiscoverServices_(self, peripheral, error):
        self._dispatch("_services_ready", peripheral, error)

    def peripheral_didDiscoverCharacteristicsForService_error_(self, peripheral, service, error):
        self._dispatch("_characteristics_ready", peripheral, service, error)

    def peripheral_didUpdateNotificationStateForCharacteristic_error_(self, peripheral, characteristic, error):
        self._dispatch("_subscription_ready", characteristic, error)

    def peripheral_didUpdateValueForCharacteristic_error_(self, peripheral, characteristic, error):
        # CoreBluetooth reuses the characteristic; snapshot each packet before
        # handing it to Qt, otherwise a later notification can replace its value.
        data = bytes(characteristic.value() or b"") if error is None else b""
        self._dispatch("_received", characteristic, error, data)

    def peripheral_didWriteValueForCharacteristic_error_(self, peripheral, characteristic, error):
        self._dispatch("_written", characteristic, error)


class MacBleChannel(QObject):
    nativeEvent = Signal(str, object)
    opened = Signal()
    readyRead = Signal()
    writeProgress = Signal()
    writeCompleted = Signal()
    errorOccurred = Signal(object)
    subscriptionStopped = Signal()

    def __init__(self, info, parent=None):
        super().__init__(parent)
        self._identifier = info.deviceUuid().toString(QUuid.WithoutBraces).lower()
        self._manager = self._delegate = self._peripheral = None
        self._callback_queue = None
        self.nativeEvent.connect(self._dispatch_native_event, Qt.QueuedConnection)
        self._rx = self._tx = None
        self._incoming = bytearray()
        self._chunks = deque()
        self._inflight = False
        self._open = self._opened_once = self._closing = False
        self._subscribed = False
        self._error = ""
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(20000)
        self._deadline.timeout.connect(lambda: self._fail("蓝牙配置连接超时，请重新连接或改用 USB。"))
        self._response_batch = QTimer(self)
        self._response_batch.setSingleShot(True)
        self._response_batch.setInterval(25)
        self._response_batch.timeout.connect(self._flush_received)

    def start(self):
        self._deadline.start()
        self._delegate = _WmpPeripheralDelegate.alloc().initWithOwner_(self)
        # Quick3D can keep the native main dispatch queue from servicing BLE.
        # Receive on a serial native queue, then handle all state/timers in Qt.
        self._callback_queue = dispatch_queue_create(b"com.boring.console.wmp", DISPATCH_QUEUE_SERIAL)
        self._manager = CB.CBCentralManager.alloc().initWithDelegate_queue_options_(
            self._delegate, self._callback_queue, None)

    @Slot(str, object)
    def _dispatch_native_event(self, method, args):
        if not self._closing or method == "_subscription_ready":
            getattr(self, method)(*args)

    def _connected(self, peripheral):
        peripheral.discoverServices_([CB.CBUUID.UUIDWithString_(SERVICE)])

    def _manager_ready(self, manager):
        if self._closing:
            return
        if manager.state() in (CB.CBManagerStateUnknown, CB.CBManagerStateResetting):
            if self._opened_once:
                self._fail("蓝牙配置连接已断开")
            return
        if manager.state() != CB.CBManagerStatePoweredOn:
            self._fail("蓝牙不可用，请检查系统蓝牙设置。")
            return
        if self._peripheral is not None:
            return
        # Re-check connection at opening time; a cached scan row is not proof.
        for service in (SERVICE, "1812"):
            for peripheral in manager.retrieveConnectedPeripheralsWithServices_([CB.CBUUID.UUIDWithString_(service)]):
                if str(peripheral.identifier().UUIDString()).lower() == self._identifier:
                    self._peripheral = peripheral
                    peripheral.setDelegate_(self._delegate)
                    manager.connectPeripheral_options_(peripheral, None)
                    return
        self._fail("设备未连接到此电脑，请先在系统蓝牙设置中连接。")

    def _services_ready(self, peripheral, error):
        if self._closing:
            return
        if error is not None:
            self._fail("无法完成蓝牙配置服务发现，请重新连接或改用 USB。", error)
            return
        service = next((s for s in peripheral.services() or () if str(s.UUID()).lower() == SERVICE), None)
        if service is None:
            self._fail("未发现蓝牙配置服务，请使用 USB 连接后检查固件支持。")
            return
        peripheral.discoverCharacteristics_forService_([CB.CBUUID.UUIDWithString_(RX), CB.CBUUID.UUIDWithString_(TX)], service)

    def _characteristics_ready(self, peripheral, service, error):
        if self._closing:
            return
        if error is not None:
            self._fail("无法完成蓝牙配置服务发现，请重新连接或改用 USB。", error)
            return
        characteristics = {str(c.UUID()).lower(): c for c in service.characteristics() or ()}
        self._rx, self._tx = characteristics.get(RX), characteristics.get(TX)
        if (self._rx is None or self._tx is None
                or not self._rx.properties() & CB.CBCharacteristicPropertyWrite
                or not self._tx.properties() & CB.CBCharacteristicPropertyIndicate):
            self._fail("无法完成蓝牙配置服务发现，请重新连接或改用 USB。")
            return
        peripheral.setNotifyValue_forCharacteristic_(True, self._tx)

    def _subscription_ready(self, characteristic, error):
        if characteristic != self._tx:
            return
        if self._closing:
            self._subscribed = error is not None or characteristic.isNotifying()
            self.subscriptionStopped.emit()
            return
        if error is not None or not characteristic.isNotifying():
            self._fail("无法订阅设备配置回复，请重新连接或改用 USB。", error)
            return
        if not self._open:
            self._subscribed = True
            self._deadline.stop()
            self._open = self._opened_once = True
            self.opened.emit()

    def isOpen(self):
        return self._open

    @property
    def opened_once(self):
        return self._opened_once

    @property
    def retryable_connection_error(self):
        # Failed setup needs an explicit retry; do not repeatedly open a bad link.
        return False

    def errorString(self):
        return self._error

    def readAll(self):
        data = QByteArray(bytes(self._incoming))
        self._incoming.clear()
        return data

    def write(self, data):
        if not self._open:
            self._error = "蓝牙配置通道尚未就绪"
            return -1
        if self._inflight or self._chunks:
            self._error = "上一条蓝牙请求仍在发送"
            return -1
        value = bytes(data)
        capacity = int(self._peripheral.maximumWriteValueLengthForType_(CB.CBCharacteristicWriteWithResponse))
        if capacity <= 0:
            self._fail("蓝牙配置通信失败")
            return -1
        self._chunks.extend(value[i:i + capacity] for i in range(0, len(value), capacity))
        self._send_next()
        return len(value)

    def _send_next(self):
        if not self._open or self._inflight or not self._chunks:
            return
        data = self._chunks.popleft()
        self._inflight = True
        self._peripheral.writeValue_forCharacteristic_type_(NSData.dataWithBytes_length_(data, len(data)), self._rx, CB.CBCharacteristicWriteWithResponse)

    def _written(self, characteristic, error):
        if self._closing or characteristic != self._rx or not self._inflight:
            return
        if error is not None:
            self._fail("蓝牙配置通信失败", error)
            return
        self._inflight = False
        self.writeProgress.emit()
        self._send_next()
        if not self._inflight and not self._chunks:
            self.writeCompleted.emit()
            if self._incoming:
                self._response_batch.start()

    def _flush_received(self):
        # A reply can precede CoreBluetooth's final write callback. Keep the
        # protocol session alive until the outgoing frame has drained.
        if not self._closing and not self._inflight and not self._chunks and self._incoming:
            self.readyRead.emit()

    def _received(self, characteristic, error, data=None):
        if self._closing or characteristic != self._tx:
            return
        if error is not None:
            self._fail("蓝牙配置通信失败", error)
            return
        self._incoming.extend(bytes(characteristic.value() or b"") if data is None else data)
        self._response_batch.start()

    def waitForBytesWritten(self, timeout_ms):
        if not self._chunks and not self._inflight:
            return True
        loop = QEventLoop()
        deadline, poll = QTimer(), QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.timeout.connect(lambda: loop.quit() if self._closing or (not self._chunks and not self._inflight) else None)
        poll.start(5)
        deadline.start(max(0, timeout_ms))
        loop.exec()
        return not self._chunks and not self._inflight and not self._error

    def _fail(self, message, error=None):
        if self._closing:
            return
        self._open = False
        self._deadline.stop()
        self._error = message + (f" ({error.localizedDescription()})" if error is not None else "")
        self.errorOccurred.emit(QSerialPort.ResourceError)

    def close(self):
        if self._closing:
            return
        self._closing = True
        self._open = False
        self._deadline.stop()
        self._response_batch.stop()
        self._chunks.clear()
        self._inflight = False
        if self._subscribed and self._peripheral is not None and self._tx is not None:
            # macOS may keep the physical HID connection alive after cancel.
            # Firmware releases its BLE configuration owner on CCCD unsubscribe;
            # wait for that acknowledgement before a caller opens USB.
            loop, deadline = QEventLoop(), QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            self.subscriptionStopped.connect(loop.quit)
            deadline.start(1500)
            self._peripheral.setNotifyValue_forCharacteristic_(False, self._tx)
            loop.exec()
            deadline.stop()
            self.subscriptionStopped.disconnect(loop.quit)
        if self._delegate is not None:
            self._delegate.owner = None
        if self._peripheral is not None:
            self._peripheral.setDelegate_(None)
            self._manager.cancelPeripheralConnection_(self._peripheral)
        if self._manager is not None:
            self._manager.setDelegate_(None)
