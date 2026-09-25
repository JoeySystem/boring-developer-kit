"""Exercise queue and deadlines without connecting to or writing a real device."""
import json

import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.protocol.bootstrap import ACK, Command
from controller_config.protocol.framing import Frame, RESPONSE_FLAG
from controller_config.transport.ble import BleWorker


class PendingChannel(QObject):
    readyRead = Signal()
    errorOccurred = Signal(object)
    writeProgress = Signal()
    writeCompleted = Signal()

    def __init__(self):
        super().__init__()
        self.open = True
        self.writes = []

    def isOpen(self):
        return self.open

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def close(self):
        self.open = False


@pytest.fixture
def worker(qtbot, contract):
    worker = BleWorker(contract)
    channel = PendingChannel()
    worker._serial = channel
    worker._closing = False
    worker._request_timer = QTimer(worker)
    worker._request_timer.setSingleShot(True)
    worker._request_timer.timeout.connect(worker._on_request_timeout)
    channel.readyRead.connect(worker._on_ready_read)
    channel.errorOccurred.connect(worker._on_serial_error)
    channel.writeProgress.connect(worker._on_write_progress)
    channel.writeCompleted.connect(worker._on_write_completed)
    yield worker, channel
    worker.close()


def test_slow_send_does_not_consume_response_budget_or_advance_queue(worker, qtbot):
    worker, channel = worker
    failed = []
    worker.command_failed.connect(lambda *args: failed.append(args))
    worker.SEND_TIMEOUT_MS = 200
    worker.execute_command(Command("VALIDATE_CONFIG", 0x11, {}, timeout_ms=100))
    first = worker._session
    worker.execute_command(Command("GET_CONFIG", 0x10, {}))
    for _ in range(3):
        qtbot.wait(60)
        channel.writeProgress.emit()
        assert worker._session is first
        assert len(channel.writes) == 1
    assert not failed
    channel.writeCompleted.emit()
    assert worker._request_timer.interval() == 100
    assert worker._request_timer.remainingTime() > 70
    qtbot.waitUntil(lambda: bool(failed))
    assert failed[0][1].error_name == "TRANSPORT_TIMEOUT"
    assert len(channel.writes) == 2
    assert worker._sending  # Next request has its own send deadline.


def test_stalled_send_closes_without_running_next_command(worker, qtbot):
    worker, channel = worker
    failures = []
    worker.failure.connect(lambda *args: failures.append(args))
    worker.SEND_TIMEOUT_MS = 50
    worker.execute_command(Command("SET_CONFIG", 0x12, {}))
    worker.execute_command(Command("GET_CONFIG", 0x10, {}))
    qtbot.waitUntil(lambda: bool(failures))
    assert "发送超时" in failures[0][1]
    assert len(channel.writes) == 1
    assert not channel.open and worker._session is None
    assert not worker._queued_commands
    worker._on_write_completed()  # Late callbacks cannot revive the closed session.
    assert worker._request_timer is None and not worker._sending


def test_reply_finishes_timer_and_preserves_queue_order(worker):
    worker, channel = worker
    completed = []
    worker.command_completed.connect(lambda *args: completed.append(args))
    worker.execute_command(Command("VALIDATE_CONFIG", 0x11, {}))
    worker.execute_command(Command("GET_CONFIG", 0x10, {}))
    channel.writeCompleted.emit()
    session = worker._session
    payload = {"ok": True, "command": "VALIDATE_CONFIG", "valid": True}
    session.accept(Frame(1, 0, ACK, RESPONSE_FLAG, session.current_request_id,
                         json.dumps(payload).encode()))
    assert completed == [("VALIDATE_CONFIG", payload)]
    assert len(channel.writes) == 2
    assert worker._session.current_command.name == "GET_CONFIG"
    assert worker._request_timer.interval() == worker.SEND_TIMEOUT_MS
