"""BLE sessions over the real gateway path: gateway.connect_port -> worker thread.

Everything is real except the GATT channel: BleGateway, BleWorker, its QThread,
BootstrapSession, Contract and the request and status timers, in real time.
Packet pacing is the 2026-11-04 measurement: 514 bytes per indication, 127 ms.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import zlib

import pytest
from PySide6.QtBluetooth import QBluetoothDeviceInfo
from PySide6.QtCore import QObject, QThread, QTimer, QUuid, Signal

from controller_config.protocol.framing import HEADER, MAGIC, RESPONSE_FLAG, canonical_json_bytes
from controller_config.transport import ble
from controller_config.transport.qt_serial import SerialWorker

PORT = "ble:11111111-2222-3333-4444-555555555555"
HELLO, CAPABILITIES, GET_CONFIG, GET_STATUS = 0x01, 0x02, 0x10, 0x13
FAST_PACKET_S = 0.001
MEASURED_PACKET_S = 0.127
PACKET_BYTES = 514


def _compact(value) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _frame(contract, request_id: int, payload: dict) -> bytes:
    body = _compact(payload)
    return HEADER.pack(MAGIC, contract.protocol_major, contract.protocol_minor, 0x7E,
                       RESPONSE_FLAG, request_id, len(body), zlib.crc32(body) & 0xFFFFFFFF) + body


def _config_of_size(load_fixture, contract, size: int) -> dict:
    """A schema-valid configuration of exactly `size` compact bytes."""
    config = load_fixture("config-matrix12-power-v2-v1.json")
    template = config["profiles"][0]
    while len(config["profiles"]) < 8:
        extra = copy.deepcopy(template)
        extra["id"] = max(p["id"] for p in config["profiles"]) + 1
        extra["name"] = f"P{extra['id']}"
        grown = dict(config, profiles=[*config["profiles"], extra])
        if len(_compact(grown)) > size:
            break
        config = grown
    config["macros"] = [{"id": 0, "name": "pad", "steps": [{"op": "text", "text": ""}]}]
    steps = config["macros"][0]["steps"]
    while (missing := size - len(_compact(config))) > 0:
        if len(steps[-1]["text"]) == 256:
            steps.append({"op": "text", "text": ""})
            continue
        steps[-1]["text"] += "a" * min(missing, 256 - len(steps[-1]["text"]))
    contract.validate_config(config)
    assert len(_compact(config)) == size
    return config


class Device:
    """Replies to the bootstrap the way the firmware does, one piece at a time."""

    def __init__(self, contract, load_fixture, config_bytes: int = 5443) -> None:
        self.contract = contract
        config = _config_of_size(load_fixture, contract, config_bytes)
        digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
        capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")
        capabilities["result"]["features"]["device_authentication"] = False
        status = load_fixture("status-matrix12-power-v2-v1.json")
        status["result"].update({"active": {"generation": 1, "digest": digest}, "pending": None})
        self.replies = {
            HELLO: {"command": "HELLO", "result": {
                "identity": {"product_id": contract.product_id, "hardware_id": config["hardware_id"],
                             "serial": "CP01-AABBCCDDEEFF", "usb_vid": contract.usb_vid,
                             "usb_pid": contract.usb_pid},
                "versions": {"firmware": "1.0.0", "protocol_major": contract.protocol_major,
                             "protocol_minor": contract.protocol_minor,
                             "schema_version": contract.schema_version},
                "config": {"generation": 1, "digest": digest},
                "compatibility": {"read": True, "write": True, "reason": "compatible"}}},
            CAPABILITIES: capabilities,
            GET_STATUS: status,
            GET_CONFIG: {"command": "GET_CONFIG", "result": {
                "generation": 1, "digest": digest, "config": config}},
        }
        self.packet_seconds = {GET_CONFIG: FAST_PACKET_S}
        self.silent_after_packets: dict[int, int] = {}
        self.writes: list[tuple[float, int]] = []
        self.channel = None
        self.channel_init_thread = None

    def writes_of(self, message_type: int) -> list[float]:
        return [at for at, kind in self.writes if kind == message_type]


class FakeChannel(QObject):
    """The channel interface of MacBleChannel and BleChannel, without Bluetooth."""

    opened = Signal()
    readyRead = Signal()
    writeProgress = Signal()
    writeCompleted = Signal()
    errorOccurred = Signal(object)
    device: Device
    opened_once = True
    retryable_connection_error = False

    def __init__(self, _info, parent=None) -> None:
        self.device.channel_init_thread = QThread.currentThread()
        super().__init__(parent)
        self.device.channel = self
        self._incoming = bytearray()
        self._open = False
        self._due: list[tuple[float, object]] = []
        self._tick = QTimer(self)
        self._tick.setInterval(1)
        self._tick.timeout.connect(self._run_due)

    def _later(self, seconds: float, action) -> None:
        self._due.append((time.monotonic() + seconds, action))

    def _run_due(self) -> None:
        now = time.monotonic()
        ready = [item for item in self._due if item[0] <= now]
        self._due = [item for item in self._due if item[0] > now]
        for _at, action in sorted(ready, key=lambda item: item[0]):
            action()

    def start(self) -> None:
        self._tick.start()
        self._later(0.001, self._do_open)

    def _do_open(self) -> None:
        self._open = True
        self.opened.emit()

    def isOpen(self) -> bool:
        return self._open

    def errorString(self) -> str:
        return ""

    def readAll(self) -> bytes:
        data = bytes(self._incoming)
        self._incoming.clear()
        return data

    def close(self) -> None:
        self._open = False
        self._due = []
        self._tick.stop()

    def write(self, data) -> int:
        data = bytes(data)
        _magic, _major, _minor, kind, _flags, request_id, _length, _crc = HEADER.unpack_from(data)
        device = self.device
        device.writes.append((time.monotonic(), kind))
        self._later(0.001, self._acknowledge)
        reply = device.replies.get(kind)
        if reply is not None:
            frame = _frame(device.contract, request_id, reply)
            packets = [frame[i:i + PACKET_BYTES] for i in range(0, len(frame), PACKET_BYTES)]
            packets = packets[:device.silent_after_packets.get(kind, len(packets))]
            pace = device.packet_seconds.get(kind, FAST_PACKET_S)
            for index, packet in enumerate(packets, 1):
                self._later(0.002 + index * pace, lambda piece=packet: self._deliver(piece))
        return len(data)

    def _acknowledge(self) -> None:
        self.writeProgress.emit()
        self.writeCompleted.emit()

    def _deliver(self, piece: bytes) -> None:
        self._incoming += piece
        self.readyRead.emit()


@pytest.fixture
def connect(qtbot, contract, load_fixture, monkeypatch):
    """Open PORT through the real gateway; gives the device and the observations."""
    gateways = []

    def start(config_bytes=5443, *, packet_seconds=FAST_PACKET_S, silent_after=None):
        device = Device(contract, load_fixture, config_bytes)
        device.packet_seconds[GET_CONFIG] = packet_seconds
        if silent_after is not None:
            device.silent_after_packets[GET_CONFIG] = silent_after
        channel_class = type("Channel", (FakeChannel,), {"device": device})
        monkeypatch.setattr(ble, "MacBleChannel", channel_class, raising=False)
        monkeypatch.setattr(ble, "BleChannel", channel_class)
        gateway = ble.BleGateway(contract, threaded=True)
        gateways.append(gateway)
        gateway._worker._device_info_by_port[PORT] = QBluetoothDeviceInfo(
            QUuid(PORT.removeprefix("ble:")), "Boring Mist", 0)
        ready, failures, statuses = [], [], []
        gateway.snapshot_ready.connect(lambda snapshot: ready.append(time.monotonic()))
        gateway.failure.connect(lambda kind, message, technical: failures.append(
            (time.monotonic(), message, technical)))
        gateway.status_updated.connect(lambda status: statuses.append(status))
        gateway.connect_port(PORT)
        return gateway, device, ready, failures, statuses

    yield start
    for gateway in gateways:
        gateway.shutdown()


def _thread_warnings(qtlog) -> list[str]:
    return [record.message for record in qtlog.records
            if "thread" in record.message.lower() or "Cannot create children" in record.message]


def test_connect_builds_channel_and_timers_on_the_worker_thread(qtbot, connect, qtlog) -> None:
    gateway, device, ready, failures, _statuses = connect()
    qtbot.waitUntil(lambda: bool(ready), timeout=5000)
    worker = gateway._worker
    assert device.channel_init_thread is worker.thread()
    assert device.channel.thread() is worker.thread()
    assert device.channel.parent() is worker
    for timer in (worker._request_timer, worker._status_timer, worker._presence_timer):
        assert timer.thread() is worker.thread()
        assert timer.parent() is worker
    assert worker.thread() is not QThread.currentThread()
    assert not failures
    assert _thread_warnings(qtlog) == []


def test_connected_session_keeps_polling_status(qtbot, connect, qtlog) -> None:
    _gateway, device, ready, failures, statuses = connect()
    qtbot.waitUntil(lambda: bool(ready), timeout=5000)
    qtbot.waitUntil(lambda: len(statuses) >= 2, timeout=3000)
    assert len([at for at in device.writes_of(GET_STATUS) if at > ready[0]]) >= 2
    assert not failures
    assert _thread_warnings(qtlog) == []


def test_lost_reply_fails_after_the_reply_timeout(qtbot, connect, qtlog) -> None:
    _gateway, device, ready, failures, _statuses = connect(silent_after=0)
    qtbot.waitUntil(lambda: bool(failures), timeout=4000)
    elapsed = failures[0][0] - device.writes_of(GET_CONFIG)[0]
    assert 2.3 <= elapsed <= 3.0
    assert "GET_CONFIG" in failures[0][2]
    assert not ready
    assert _thread_warnings(qtlog) == []


def test_reply_that_stops_midway_fails_after_the_reply_timeout(qtbot, connect) -> None:
    packets = 20
    _gateway, device, ready, failures, _statuses = connect(
        13_600, packet_seconds=MEASURED_PACKET_S, silent_after=packets)
    qtbot.waitUntil(lambda: bool(failures), timeout=7000)
    elapsed = failures[0][0] - device.writes_of(GET_CONFIG)[0]
    # 20 pieces arrive for 2.54 s, past the old whole-reply budget, then silence.
    assert 2.5 + packets * MEASURED_PACKET_S - 0.3 <= elapsed <= 2.5 + packets * MEASURED_PACKET_S + 0.5
    assert "GET_CONFIG" in failures[0][2]
    assert not ready


def test_large_config_on_a_slow_link_connects(qtbot, connect, qtlog) -> None:
    _gateway, device, ready, failures, _statuses = connect(13_600, packet_seconds=MEASURED_PACKET_S)
    qtbot.waitUntil(lambda: bool(ready or failures), timeout=8000)
    assert not failures
    # 27 packets at 127 ms: the old whole-reply budget of 2.5 s would have failed.
    assert ready[0] - device.writes_of(GET_CONFIG)[0] > 2.5
    assert _thread_warnings(qtlog) == []


def test_reply_that_keeps_trickling_stops_at_the_total_limit(qtbot, connect, monkeypatch) -> None:
    monkeypatch.setattr(ble.BleWorker, "REPLY_TOTAL_TIMEOUT_MS", 1200)
    _gateway, device, ready, failures, _statuses = connect(13_600, packet_seconds=0.2)
    qtbot.waitUntil(lambda: bool(failures or ready), timeout=7000)
    assert not ready
    elapsed = failures[0][0] - device.writes_of(GET_CONFIG)[0]
    # Data was still arriving; without the limit this reply needs 5.4 s.
    assert 1.0 <= elapsed <= 1.8
    assert "GET_CONFIG" in failures[0][2]


def test_real_limit_covers_the_largest_reply_at_a_very_slow_pace() -> None:
    largest_reply_packets = 33
    assert ble.BleWorker.REPLY_TOTAL_TIMEOUT_MS >= largest_reply_packets * 1800


def _worker_with_reply_timer(contract, *, sending: bool):
    worker = ble.BleWorker(contract)
    worker._request_timer = QTimer(worker)
    worker._request_timer.setSingleShot(True)
    worker._reply_timeout_ms = 2500
    worker._reply_deadline = time.monotonic() + 60
    worker._sending = sending
    worker._request_timer.start(100)
    return worker


def test_incoming_data_renews_the_reply_timer(qtbot, contract) -> None:
    worker = _worker_with_reply_timer(contract, sending=False)
    worker._note_incoming(b"piece")
    assert worker._request_timer.remainingTime() > 2000
    worker._request_timer.stop()


def test_incoming_data_does_not_renew_while_writing(qtbot, contract) -> None:
    worker = _worker_with_reply_timer(contract, sending=True)
    worker._note_incoming(b"piece")
    assert worker._request_timer.remainingTime() < 1000  # Not renewed to 2500; coarse timers overshoot.
    worker._request_timer.stop()


def test_empty_read_does_not_renew_the_reply_timer(qtbot, contract) -> None:
    worker = _worker_with_reply_timer(contract, sending=False)
    worker._note_incoming(b"")
    assert worker._request_timer.remainingTime() < 1000  # Not renewed to 2500; coarse timers overshoot.
    worker._request_timer.stop()


def test_usb_keeps_one_budget_for_the_whole_reply(qtbot, contract, monkeypatch) -> None:
    worker = SerialWorker(contract)
    worker._serial = type("Serial", (), {"readAll": lambda self: b"WMP1"})()
    worker._session = type("Session", (), {"current_command": None, "current_request_id": 1})()
    worker._request_timer = QTimer(worker)
    worker._request_timer.setSingleShot(True)
    worker._request_timer.start(100)
    worker._on_ready_read()
    assert worker._request_timer.remainingTime() < 1000  # Not renewed to 2500; coarse timers overshoot.
    worker._request_timer.stop()
    worker._serial = None
    worker._session = None
