from types import SimpleNamespace as NS
import sys
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="CoreBluetooth is macOS only")
if sys.platform == "darwin":
    from controller_config.transport.mac_ble_channel import MacBleChannel, SERVICE, RX, TX, CB
from PySide6.QtCore import QUuid, QTimer
from PySide6.QtBluetooth import QBluetoothDeviceInfo

IDENT = "12345678-1234-1234-1234-123456789abc"


def test_close_releases_config_subscription_before_canceling_connection(channel):
    events = []
    channel._tx = characteristic(TX, CB.CBCharacteristicPropertyIndicate)
    channel._subscription_ready(channel._tx, None)

    def unsubscribe(enabled, tx):
        assert enabled is False
        events.append('unsubscribe')
        def confirm():
            tx.isNotifying = lambda: False
            events.append('confirmed')
            channel.nativeEvent.emit('_subscription_ready', (tx, None))
        QTimer.singleShot(0, confirm)

    channel._peripheral = NS(setNotifyValue_forCharacteristic_=unsubscribe,
                             setDelegate_=lambda _: None)
    channel._manager = NS(cancelPeripheralConnection_=lambda _: events.append('cancel'),
                         setDelegate_=lambda _: None)
    channel.close()
    assert events == ['unsubscribe', 'confirmed', 'cancel']

@pytest.fixture
def channel(qtbot):
    value = MacBleChannel(QBluetoothDeviceInfo(QUuid(IDENT), "BORING", 0))
    yield value
    value.close()


def characteristic(uuid, properties, notifying=True):
    return NS(UUID=lambda: uuid, properties=lambda: properties, isNotifying=lambda: notifying)


def test_only_currently_connected_device_can_open(channel):
    errors = []
    channel.errorOccurred.connect(errors.append)
    channel._manager_ready(NS(state=lambda: CB.CBManagerStatePoweredOn,
        retrieveConnectedPeripheralsWithServices_=lambda _: [],
        retrievePeripheralsWithIdentifiers_=lambda _: pytest.fail("must not reconnect a cached device")))
    assert errors and not channel.isOpen()
    assert "系统蓝牙设置" in channel.errorString()


def test_subscribe_directly_and_wait_for_confirmed_notifications(channel):
    rx = characteristic(RX, CB.CBCharacteristicPropertyWrite)
    tx = characteristic(TX, CB.CBCharacteristicPropertyIndicate)
    subscriptions, opened = [], []
    channel.opened.connect(lambda: opened.append(True))
    peripheral = NS(setNotifyValue_forCharacteristic_=lambda *args: subscriptions.append(args))
    channel._characteristics_ready(peripheral, NS(characteristics=lambda: [rx, tx]), None)
    assert subscriptions == [(True, tx)]
    assert not opened and not channel.isOpen()
    channel._subscription_ready(tx, None)
    assert opened == [True] and channel.isOpen()
    channel._subscription_ready(tx, None)
    assert opened == [True]


@pytest.mark.parametrize("missing", ["rx", "tx", "write", "indicate"])
def test_invalid_gatt_never_starts_bootstrap(channel, missing):
    rx = characteristic(RX, 0 if missing == "write" else CB.CBCharacteristicPropertyWrite)
    tx = characteristic(TX, 0 if missing == "indicate" else CB.CBCharacteristicPropertyIndicate)
    chars = [rx, tx]
    if missing == "rx": chars.remove(rx)
    if missing == "tx": chars.remove(tx)
    errors = []
    channel.errorOccurred.connect(errors.append)
    channel._characteristics_ready(NS(setNotifyValue_forCharacteristic_=lambda *_: pytest.fail("invalid service")),
                                   NS(characteristics=lambda: chars), None)
    assert errors and not channel.isOpen()
    assert "更新固件" not in channel.errorString()


def test_subscription_failure_is_not_ready(channel):
    channel._tx = characteristic(TX, CB.CBCharacteristicPropertyIndicate, notifying=False)
    errors = []
    channel.errorOccurred.connect(errors.append)
    channel._subscription_ready(channel._tx, None)
    assert errors and not channel.isOpen()


def test_writes_wait_for_ack_and_reassemble_notifications(channel, qtbot):
    writes = []
    rx, tx = object(), NS(value=lambda: b"WMP1")
    channel._rx, channel._tx, channel._open = rx, tx, True
    channel._peripheral = NS(maximumWriteValueLengthForType_=lambda _: 20,
        writeValue_forCharacteristic_type_=lambda data, *_: writes.append(bytes(data)),
        setDelegate_=lambda _: None)
    channel._manager = NS(cancelPeripheralConnection_=lambda _: None, setDelegate_=lambda _: None)
    payload = bytes(range(49))
    assert channel.write(payload) == 49
    assert [len(x) for x in writes] == [20]
    channel._written(rx, None)
    channel._written(rx, None)
    channel._written(rx, None)
    assert b"".join(writes) == payload
    assert channel.waitForBytesWritten(5)
    received = []
    channel.readyRead.connect(lambda: received.append(bytes(channel.readAll())))
    channel._received(tx, None)
    tx.value = lambda: b"payload"
    channel._received(tx, None)
    qtbot.waitUntil(lambda: bool(received))
    assert received == [b"WMP1payload"]


def test_close_releases_callbacks_even_before_open(channel):
    calls = []
    channel._delegate = NS(owner=channel)
    channel._peripheral = NS(setDelegate_=lambda value: calls.append(("delegate", value)))
    channel._manager = NS(cancelPeripheralConnection_=lambda _: calls.append(("cancel", True)),
                          setDelegate_=lambda value: calls.append(("manager", value)))
    channel._deadline.start()
    channel.close()
    channel.close()
    assert channel._delegate.owner is None and not channel._deadline.isActive()
    assert calls == [("delegate", None), ("cancel", True), ("manager", None)]


def test_worker_disposes_unopened_channel(qtbot, contract):
    from controller_config.transport.ble import BleWorker
    worker = BleWorker(contract)
    calls = []
    ch = NS(readyRead=NS(disconnect=lambda _: None), errorOccurred=NS(disconnect=lambda _: None),
            isOpen=lambda: False, close=lambda: calls.append("closed"), deleteLater=lambda: None)
    worker._serial = ch
    worker._dispose_serial()
    assert calls == ["closed"] and worker._serial is None


def test_reply_waits_for_final_write_callback(channel, qtbot):
    rx, tx = object(), NS(value=lambda: b"reply")
    channel._rx, channel._tx, channel._open = rx, tx, True
    channel._peripheral = NS(maximumWriteValueLengthForType_=lambda _: 20,
        writeValue_forCharacteristic_type_=lambda *_: None, setDelegate_=lambda _: None)
    channel._manager = NS(cancelPeripheralConnection_=lambda _: None, setDelegate_=lambda _: None)
    received = []
    channel.readyRead.connect(lambda: received.append(bytes(channel.readAll())))
    channel.write(b"request")
    channel._received(tx, None)
    qtbot.wait(40)
    assert received == []
    channel._written(rx, None)
    qtbot.waitUntil(lambda: bool(received))
    assert received == [b"reply"]


def test_native_callbacks_copy_packets_and_run_on_qt_thread(channel, qtbot):
    import threading
    from controller_config.transport.mac_ble_channel import _WmpPeripheralDelegate
    delegate = _WmpPeripheralDelegate.alloc().initWithOwner_(channel)
    channel._delegate = delegate
    tx = NS(value=lambda: b"first")
    channel._tx = tx
    received = []
    owner_thread = threading.get_ident()
    channel.readyRead.connect(lambda: received.append((threading.get_ident(), bytes(channel.readAll()))))

    def native_callbacks():
        delegate.peripheral_didUpdateValueForCharacteristic_error_(None, tx, None)
        tx.value = lambda: b"second"
        delegate.peripheral_didUpdateValueForCharacteristic_error_(None, tx, None)
        tx.value = lambda: b"replaced after callback"

    native_thread = threading.Thread(target=native_callbacks)
    native_thread.start()
    native_thread.join()
    assert not channel._incoming
    qtbot.waitUntil(lambda: bool(received))
    assert received == [(owner_thread, b"firstsecond")]


def test_queued_native_event_is_ignored_after_close(channel, qtbot):
    from controller_config.transport.mac_ble_channel import _WmpPeripheralDelegate
    delegate = _WmpPeripheralDelegate.alloc().initWithOwner_(channel)
    channel._delegate = delegate
    tx = NS(value=lambda: b"late")
    channel._tx = tx
    delegate.peripheral_didUpdateValueForCharacteristic_error_(None, tx, None)
    channel.close()
    qtbot.wait(40)
    assert not channel._incoming and not channel._response_batch.isActive()
