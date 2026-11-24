"""USB serial transport (SerialWorker, which BleWorker inherits) for the `desktop` source.

Driven by the shared samples in software/protocol/fixtures/desktop_agent/.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from controller_config import desktop_status_wire as desktop
from controller_config.agent_status import (
    clear_status_command as claude_clear, status_command as claude_status,
)
from controller_config.doubao_status import (
    clear_status_command as doubao_clear, status_command as doubao_status,
)
from controller_config.protocol.framing import RESPONSE_FLAG, Frame, FrameDecoder
from controller_config.transport.qt_serial import SerialWorker


@pytest.fixture(scope="module")
def samples(protocol_dir: Path) -> Path:
    return protocol_dir / "fixtures" / "desktop_agent"


def sample(samples: Path, name: str) -> dict:
    return json.loads((samples / name).read_text(encoding="utf-8"))


def names(samples: Path, folder: str) -> list[str]:
    return sorted(f"{folder}/{path.name}" for path in (samples / folder).glob("*.json"))


def mixed_command(samples: Path):
    payload = sample(samples, "valid/set-mixed.json")
    return desktop.status_command(payload["states"], payload["completion_seq"])


# --- USB serial transport ------------------------------------------------------

def connected_worker(contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True, write=len, errorString=lambda: "")
    sent = []
    real_send = worker._send_command

    def send(command, request_id):
        sent.append((command, request_id))
        real_send(command, request_id)

    monkeypatch.setattr(worker, "_send_command", send)
    return worker, sent


def reply(worker, sent, payload):
    worker._session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, sent[-1][1],
                                 json.dumps(payload).encode()))


def outcome(qtbot, worker, sent, command, payload):
    results = []
    worker.agent_status_completed.connect(lambda cmd, value: results.append(("ok", cmd, value)))
    worker.agent_status_failed.connect(lambda cmd, value: results.append(("failed", cmd, value)))
    worker.execute_command(command)
    reply(worker, sent, payload)
    assert len(results) == 1 and results[0][1] is command
    return results[0][0]


def test_desktop_set_and_clear_acks_complete_on_the_same_command(qtbot, contract, monkeypatch, samples):
    worker, sent = connected_worker(contract, monkeypatch)
    command = mixed_command(samples)
    assert outcome(qtbot, worker, sent, command, sample(samples, "ack-set.json")) == "ok"
    assert worker._desktop_status_sent and not worker._agent_status_sent
    assert not worker._doubao_status_sent
    worker2, sent2 = connected_worker(contract, monkeypatch)
    worker2._desktop_status_sent = True
    clear = desktop.clear_status_command()
    assert outcome(qtbot, worker2, sent2, clear, sample(samples, "ack-clear.json")) == "ok"
    assert not worker2._desktop_status_sent


def bad_acks(samples):
    ack = sample(samples, "ack-set.json")
    def changed(**result):
        value = copy.deepcopy(ack)
        value["result"].update(result)
        return value
    return [
        changed(source="claude_code"),
        changed(source="doubao"),
        changed(active=False),
        changed(active=1),
        changed(lease_ms=4000),
        changed(lease_ms="5000"),
        changed(lease_ms=True),
        {"command": "SET_AGENT_STATUS"},
        {"command": "SET_AGENT_STATUS", "result": []},
        {"command": "CLEAR_AGENT_STATUS", "result": ack["result"]},
    ]


def test_malformed_desktop_acks_fail(qtbot, contract, monkeypatch, samples):
    for payload in bad_acks(samples):
        worker, sent = connected_worker(contract, monkeypatch)
        assert outcome(qtbot, worker, sent, mixed_command(samples), payload) == "failed", payload
    worker, sent = connected_worker(contract, monkeypatch)
    worker._desktop_status_sent = True
    assert outcome(qtbot, worker, sent, desktop.clear_status_command(),
                   sample(samples, "ack-set.json")) == "failed"
    assert worker._desktop_status_sent  # an unconfirmed CLEAR is retried on exit


def test_desktop_nack_fails_with_device_error_name(qtbot, contract, monkeypatch, samples):
    worker, sent = connected_worker(contract, monkeypatch)
    errors = []
    worker.agent_status_failed.connect(lambda cmd, error: errors.append(error))
    command = mixed_command(samples)
    worker.execute_command(command)
    worker._session.accept(Frame(1, 0, 0x7F, RESPONSE_FLAG, sent[-1][1], json.dumps({
        "command": command.name, "error": {"name": "READ_ONLY", "message": "desktop status requires Normal mode"},
    }).encode()))
    assert errors[0].error_name == "READ_ONLY"


def test_claude_code_and_doubao_acks_keep_their_own_validation(qtbot, contract, monkeypatch, samples):
    claude_ack = {"command": "SET_AGENT_STATUS",
                  "result": {"source": "claude_code", "active": True, "lease_ms": 5000}}
    doubao_ack = {"command": "SET_AGENT_STATUS",
                  "result": {"source": "doubao", "active": True, "lease_ms": 5000}}
    desktop_ack = sample(samples, "ack-set.json")
    cases = [
        (claude_status(("working",) + ("idle",) * 5), claude_ack, "ok"),
        (claude_status(("working",) + ("idle",) * 5), desktop_ack, "failed"),
        (doubao_status(("working",) * 6, (0,) * 6), doubao_ack, "ok"),
        (doubao_status(("working",) * 6, (0,) * 6), desktop_ack, "failed"),
        (mixed_command(samples), claude_ack, "failed"),
    ]
    for command, payload, expected in cases:
        worker, sent = connected_worker(contract, monkeypatch)
        assert outcome(qtbot, worker, sent, command, payload) == expected


def decoded(contract, writes):
    decoder = FrameDecoder(max_payload_bytes=contract.max_payload_bytes, protocol_major=1, protocol_minor=0)
    return [(frame.message_type, json.loads(frame.payload_bytes))
            for data in writes for frame in decoder.feed(data)]


@pytest.mark.parametrize(("claude", "doubao", "desk", "expected"), [
    (False, False, True, [{"source": "desktop"}]),
    (True, False, True, [{"source": "claude_code"}, {"source": "desktop"}]),
    (True, True, False, [{"source": "claude_code"}, {"source": "doubao"}]),
    (False, False, False, []),
])
def test_close_sends_best_effort_clear_per_sent_source(contract, monkeypatch, claude, doubao, desk, expected):
    worker = SerialWorker(contract)
    writes, order = [], []
    worker._serial = SimpleNamespace(isOpen=lambda: True,
        write=lambda frame: writes.append(frame) or len(frame),
        waitForBytesWritten=lambda ms: order.append("flushed") or True)
    worker._agent_status_sent, worker._doubao_status_sent, worker._desktop_status_sent = claude, doubao, desk
    monkeypatch.setattr(worker, "_dispose_serial", lambda: order.append("closed"))
    worker.close()
    frames = decoded(contract, writes)
    assert [payload for _, payload in frames] == expected
    assert all(message_type == 0x29 for message_type, _ in frames)
    assert order[-1] == "closed" and order.count("flushed") == len(expected)
    assert not (worker._agent_status_sent or worker._doubao_status_sent or worker._desktop_status_sent)


def test_real_send_marks_desktop_source_for_exit_clear(contract, samples):
    worker = SerialWorker(contract)
    writes = []
    worker._serial = SimpleNamespace(isOpen=lambda: True, write=lambda frame: writes.append(frame) or len(frame))
    worker._send_command(mixed_command(samples), 7)
    assert worker._desktop_status_sent and not worker._agent_status_sent and not worker._doubao_status_sent
    assert decoded(contract, writes) == [(0x28, sample(samples, "valid/set-mixed.json"))]


def test_desktop_clear_drops_only_queued_desktop_status(contract, samples):
    worker = SerialWorker(contract)
    cc, db, desk = claude_status(("idle",) * 6), doubao_status(("working",) * 6, (0,) * 6), mixed_command(samples)
    for command in (cc, desk, db):
        worker._queue_command(command)
    clear = desktop.clear_status_command()
    worker._queue_command(clear)
    assert list(worker._queued_commands) == [cc, db, clear]
    worker._queue_command(claude_clear())
    worker._queue_command(doubao_clear())
    assert [item.payload["source"] for item in worker._queued_commands] == ["desktop", "claude_code", "doubao"]


def test_desktop_lease_polls_at_working_interval_without_changing_doubao(qtbot, contract):
    from PySide6.QtCore import QTimer
    worker = SerialWorker(contract)
    worker._status_timer = QTimer(worker)
    leased = {"desktop_agent_status": {"active": True, "selected": True}}
    for port in ("COM5", "ble:44:1b:f6:ff:e0:82"):
        worker._port_name = port
        worker._status_timer.setInterval(500)
        worker._on_status_complete(leased)
        assert worker._status_timer.interval() == desktop.POLL_INTERVAL_MS == 250
    worker._on_status_complete({"desktop_agent_status": {"active": True, "selected": False}})
    assert worker._status_timer.interval() == 500
    worker._on_status_complete({"desktop_agent_status": {"active": False, "selected": False}})
    assert worker._status_timer.interval() == 500
    worker._on_status_complete({**leased, "doubao_status": {"active": True, "selected": True}})
    assert worker._status_timer.interval() == 10
    worker._status_timer.setInterval(60000)
    worker._on_status_complete(leased)
    assert worker._status_timer.interval() == 60000
