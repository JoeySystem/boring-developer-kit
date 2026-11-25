"""Link resilience regressions from the 2026-10-26 review (A-01, A-03, A-04)."""

from __future__ import annotations

import json
import zlib
from types import SimpleNamespace

from controller_config.protocol.bootstrap import Command
from controller_config.protocol.framing import HEADER
from controller_config.transport.qt_serial import SerialWorker


def _response(request_id: int, payload: dict, *, minor: int = 0) -> bytes:
    data = json.dumps(payload).encode()
    return HEADER.pack(b"WMP1", 1, minor, 0x7E, 0x01, request_id, len(data), zlib.crc32(data)) + data


class _RecordingSession:
    def __init__(self, request_id: int = 1) -> None:
        self.current_request_id = request_id
        self.current_command = Command("GET_STATUS", 0x13, {})
        self.accepted: list[int] = []

    def accept(self, frame) -> None:
        self.accepted.append(frame.request_id)


def _worker_with_stream(contract, chunks: list[bytes]) -> SerialWorker:
    worker = SerialWorker(contract)
    pending = list(chunks)
    worker._serial = SimpleNamespace(
        isOpen=lambda: True,
        readAll=lambda: pending.pop(0) if pending else b"",
        write=lambda frame: len(frame),
    )
    worker._port_name = "cu.test"
    return worker


def test_a03_corrupt_frame_is_skipped_without_closing(qtbot, contract) -> None:
    bad = bytearray(_response(2, {"command": "GET_STATUS"}))
    bad[-1] ^= 0x01
    worker = _worker_with_stream(contract, [_response(1, {"command": "GET_STATUS"}) + bytes(bad) + _response(3, {"command": "X"})])
    session = _RecordingSession()
    worker._session = session
    failures, warnings = [], []
    worker.failure.connect(lambda *args: failures.append(args))
    worker.link_warning.connect(lambda *args: warnings.append(args))

    worker._on_ready_read()

    assert session.accepted == [1, 3]
    assert failures == []
    assert worker._serial is not None
    assert warnings and warnings[0][0] == "FRAME_DISCARDED"


def test_a03_clustered_corrupt_frames_close_the_link(qtbot, contract) -> None:
    bad = bytearray(_response(2, {"command": "GET_STATUS"}))
    bad[-1] ^= 0x01
    worker = _worker_with_stream(contract, [bytes(bad) * 5])
    worker._session = _RecordingSession()
    failures = []
    worker.failure.connect(lambda *args: failures.append(args))
    closed = []
    worker._dispose_serial = lambda: closed.append(True)

    worker._on_ready_read()

    assert failures and "损坏的数据帧" in failures[0][2]
    assert closed


def test_a03_newer_device_minor_is_reported_once(qtbot, contract) -> None:
    worker = _worker_with_stream(
        contract,
        [_response(1, {"command": "A"}, minor=contract.protocol_minor + 1),
         _response(2, {"command": "B"}, minor=contract.protocol_minor + 1)],
    )
    session = _RecordingSession()
    worker._session = session
    warnings = []
    worker.link_warning.connect(lambda *args: warnings.append(args))
    worker._on_ready_read()
    worker._on_ready_read()
    assert session.accepted == [1, 2]
    assert [w[0] for w in warnings] == ["PROTOCOL"]
    assert "建议更新控制台" in warnings[0][1]


# --- A-01: status poll failures -------------------------------------------

from controller_config.protocol.framing import ERROR_FLAG, RESPONSE_FLAG, Frame  # noqa: E402


def _status_payload() -> dict:
    return {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": "a" * 64},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "codex",
        },
    }


def _busy_nack(request_id: int) -> Frame:
    payload = {"command": "GET_STATUS", "error": {"name": "BUSY", "message": "busy"}}
    return Frame(1, 0, 0x7F, RESPONSE_FLAG | ERROR_FLAG, request_id, json.dumps(payload).encode())


def _ack(request_id: int, payload: dict) -> Frame:
    return Frame(1, 0, 0x7E, RESPONSE_FLAG, request_id, json.dumps(payload).encode())


def _polling_worker(contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    sent, failures, closed, statuses, warnings = [], [], [], [], []
    worker.failure.connect(lambda *args: failures.append(args))
    worker.status_updated.connect(statuses.append)
    worker.link_warning.connect(lambda *args: warnings.append(args))
    monkeypatch.setattr(worker, "close", lambda: closed.append(True))
    monkeypatch.setattr(worker, "_send_command", lambda cmd, rid: sent.append((cmd, rid)))
    return worker, SimpleNamespace(sent=sent, failures=failures, closed=closed,
                                   statuses=statuses, warnings=warnings)


def test_a01_single_busy_nack_keeps_connection(qtbot, contract, monkeypatch) -> None:
    worker, log = _polling_worker(contract, monkeypatch)
    worker._poll_status()
    worker._session.accept(_busy_nack(log.sent[-1][1]))
    assert not log.failures and not log.closed
    assert worker._session is None
    assert log.warnings and log.warnings[0][0] == "STATUS_POLL"

    worker._poll_status()
    worker._session.accept(_ack(log.sent[-1][1], _status_payload()))
    assert len(log.statuses) == 1
    assert worker._status_failures == 0


def test_a01_two_timeouts_then_success_keeps_connection(qtbot, contract, monkeypatch) -> None:
    worker, log = _polling_worker(contract, monkeypatch)
    for _ in range(2):
        worker._poll_status()
        worker._on_request_timeout()  # in-session retry
        worker._on_request_timeout()  # poll fails
        assert worker._session is None
    worker._poll_status()
    worker._session.accept(_ack(log.sent[-1][1], _status_payload()))
    assert not log.failures and not log.closed
    assert len(log.statuses) == 1


def test_a01_three_consecutive_failures_close_connection(qtbot, contract, monkeypatch) -> None:
    worker, log = _polling_worker(contract, monkeypatch)
    for attempt in range(3):
        worker._poll_status()
        worker._session.accept(_busy_nack(log.sent[-1][1]))
        if attempt < 2:
            assert not log.failures
    assert log.failures and log.closed
    assert "连续 3 次" in log.failures[0][2]


def test_a01_closed_channel_fails_immediately(qtbot, contract, monkeypatch) -> None:
    worker, log = _polling_worker(contract, monkeypatch)
    worker._poll_status()
    session = worker._session
    worker._serial = SimpleNamespace(isOpen=lambda: False)
    session.accept(_busy_nack(log.sent[-1][1]))
    assert log.failures and log.closed


def test_a01_queued_command_runs_after_failed_poll(qtbot, contract, monkeypatch) -> None:
    worker, log = _polling_worker(contract, monkeypatch)
    worker._poll_status()
    worker.execute_command(Command("SET_CONFIG", 0x12, {}))
    worker._session.accept(_busy_nack(log.sent[0][1]))
    assert log.sent[-1][0].name == "SET_CONFIG"


# --- A-04: receipts for dropped commands ------------------------------------

from controller_config.agent_status import status_command  # noqa: E402
from controller_config.protocol.bootstrap import COMMAND_DROPPED, CommandSession  # noqa: E402


def _busy_worker(contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True, write=lambda frame: len(frame))
    sent, failed, agent_failed = [], [], []
    worker.command_failed.connect(lambda name, error: failed.append((name, error)))
    worker.agent_status_failed.connect(lambda command, error: agent_failed.append(command))
    monkeypatch.setattr(worker, "_dispose_serial", lambda: setattr(worker, "_serial", None))
    return worker, sent, failed, agent_failed


def test_a04_close_fails_every_queued_command(qtbot, contract, monkeypatch) -> None:
    worker, _sent, failed, agent_failed = _busy_worker(contract, monkeypatch)
    worker._session = _RecordingSession()  # something is in flight
    agent = status_command(("working",) + ("idle",) * 5)
    worker.execute_command(Command("SET_BLE_NAME", 0x30, {"name": "x"}))
    worker.execute_command(agent)
    assert len(worker._queued_commands) == 2

    worker.close()

    assert [name for name, _ in failed] == ["SET_BLE_NAME", agent.name]
    assert all(error.error_name == COMMAND_DROPPED for _, error in failed)
    assert agent_failed == [agent]
    assert not worker._queued_commands


def test_a04_maintenance_only_fails_cleared_commands(qtbot, contract, monkeypatch) -> None:
    worker, _sent, failed, _agent = _busy_worker(contract, monkeypatch)
    worker._session = _RecordingSession()
    worker.execute_command(Command("GET_CONFIG", 0x10, {}))
    worker._on_bootstrap_complete(SimpleNamespace(maintenance_only=True))
    assert [name for name, _ in failed] == ["GET_CONFIG"]
    assert failed[0][1].error_name == COMMAND_DROPPED


def test_a04_oversized_payload_fails_command_and_queue_continues(qtbot, contract, monkeypatch) -> None:
    worker, _sent, failed, _agent = _busy_worker(contract, monkeypatch)
    huge = Command("SET_CONFIG", 0x12, {"blob": "x" * (contract.max_payload_bytes + 10)})
    worker.execute_command(huge)
    assert [name for name, _ in failed] == ["SET_CONFIG"]
    assert failed[0][1].error_name == "ENCODE_FAILED"
    assert worker._session is None
    writes = []
    worker._serial = SimpleNamespace(isOpen=lambda: True, write=lambda frame: writes.append(frame) or len(frame))
    worker.execute_command(Command("PING", 0x03, {"nonce": 1}))
    assert writes and isinstance(worker._session, CommandSession)


def test_a04_validator_exception_fails_command_without_raising(qtbot, contract, monkeypatch) -> None:
    worker, _sent, failed, _agent = _busy_worker(contract, monkeypatch)
    worker.execute_command(Command("PING", 0x03, {"nonce": 1}))
    session = worker._session
    request_id = session.current_request_id

    def explode(_frame):
        raise RuntimeError("validator bug")

    monkeypatch.setattr(session, "accept", explode)
    worker._serial = SimpleNamespace(
        isOpen=lambda: True, write=lambda frame: len(frame),
        readAll=lambda: _response(request_id, {"command": "PING", "result": {}}),
    )
    worker._on_ready_read()  # must not raise
    assert [name for name, _ in failed] == ["PING"]
    assert failed[0][1].error_name == "INTERNAL"
    assert worker._session is None


# --- A-05: BLE worker thread and re-entrant close ---------------------------

import inspect  # noqa: E402

from PySide6.QtCore import QThread  # noqa: E402

from pathlib import Path  # noqa: E402

from controller_config.transport import ble  # noqa: E402


def test_a05_ble_close_paths_have_no_nested_event_loop() -> None:
    transport = Path(ble.__file__).parent
    for name in ("ble.py", "mac_ble_channel.py"):
        assert "QEventLoop" not in (transport / name).read_text(encoding="utf-8"), name


def test_a05_close_is_reentrant_safe(qtbot, contract, monkeypatch) -> None:
    worker = SerialWorker(contract)
    disposals = []

    def dispose():
        disposals.append(True)
        worker.close()  # an error signal re-entering close() while disposing
        worker._serial = None

    worker._serial = SimpleNamespace(isOpen=lambda: True, write=lambda frame: len(frame),
                                     waitForBytesWritten=lambda ms: True)
    monkeypatch.setattr(worker, "_dispose_serial", dispose)
    worker.close()
    assert disposals == [True]
    worker.close()  # a later close still works
    assert disposals == [True, True]


def test_a05_ble_worker_close_does_not_flush_synchronously(qtbot, contract) -> None:
    worker = ble.BleWorker(contract)
    flushed = []
    worker._serial = SimpleNamespace(
        isOpen=lambda: True,
        write=lambda frame: len(frame),
        waitForBytesWritten=lambda ms: flushed.append(ms) or True,
        readyRead=SimpleNamespace(disconnect=lambda *_: None),
        errorOccurred=SimpleNamespace(disconnect=lambda *_: None),
        writeProgress=SimpleNamespace(disconnect=lambda *_: None),
        writeCompleted=SimpleNamespace(disconnect=lambda *_: None),
        close=lambda: None, deleteLater=lambda: None,
    )
    worker._agent_status_sent = True
    worker._dispose_serial = lambda: setattr(worker, "_serial", None)
    worker.close()
    assert flushed == []


def test_a05_ble_gateway_runs_worker_on_its_own_thread(qtbot, contract) -> None:
    gateway = ble.BleGateway(contract, threaded=True)
    try:
        assert gateway.threaded
        assert gateway._worker.thread() is not QThread.currentThread()
        received = []
        gateway.command_failed.connect(lambda name, error: received.append(
            (name, QThread.currentThread())))
        # Not connected: the worker answers on its thread, the facade re-emits
        # on the GUI thread.
        gateway.execute_command(Command("PING", 0x03, {"nonce": 1}))
        qtbot.waitUntil(lambda: bool(received), timeout=2000)
        assert received[0][0] == "PING"
        assert received[0][1] is QThread.currentThread()

        closed = []
        gateway.close_link(then=lambda: closed.append(QThread.currentThread()))
        qtbot.waitUntil(lambda: bool(closed), timeout=2000)
        assert closed[0] is QThread.currentThread()
    finally:
        gateway.shutdown()
    assert not gateway._thread.isRunning()


def test_a05_fallback_keeps_worker_on_gui_thread(qtbot, contract, monkeypatch) -> None:
    monkeypatch.setenv("BORING_BLE_WORKER_THREAD", "0")
    gateway = ble.BleGateway(contract)
    try:
        assert not gateway.threaded
        assert gateway._worker.thread() is QThread.currentThread()
    finally:
        gateway.shutdown()


def test_a05_usb_handoff_waits_for_ble_close(qtbot, contract) -> None:
    from controller_config.transport.device_gateway import DeviceGateway
    from test_ble_transport import FakeGateway

    class AsyncBle(FakeGateway):
        def __init__(self):
            super().__init__()
            self.pending = []

        def close_link(self, then=None):
            self.pending.append(then)

    usb, ble_side = FakeGateway(), AsyncBle()
    gateway = DeviceGateway(contract, usb_gateway=usb, ble_gateway=ble_side)
    gateway.connect_port("ble:current")
    gateway.connect_port("cu.usbmodem1")
    assert usb.connected != "cu.usbmodem1"  # not before BLE released
    ble_side.pending.pop()()
    assert usb.connected == "cu.usbmodem1"

    # A superseded handoff does not connect a stale port.
    gateway.connect_port("cu.usbmodem2")
    stale = ble_side.pending.pop()
    gateway.scan()
    stale()
    assert usb.connected == "cu.usbmodem1"
