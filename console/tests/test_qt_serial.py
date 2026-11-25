from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from controller_config.models import PortCandidate
from controller_config.protocol.bootstrap import BootstrapKind, Command
from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
    clear_lighting_preview_command,
    set_lighting_preview_command,
)
from controller_config.protocol.framing import HEADER, RESPONSE_FLAG, Frame
from controller_config.transport import qt_serial
from controller_config.transport.qt_serial import SerialWorker
from controller_config.agent_status import status_command, clear_status_command


class FakePortInfo:
    def __init__(
        self,
        port_name: str,
        vid: int,
        pid: int,
        serial_number: str = "WMP-001122334455",
    ) -> None:
        self._port_name = port_name
        self._vid = vid
        self._pid = pid
        self._serial_number = serial_number

    def hasVendorIdentifier(self) -> bool:
        return True

    def hasProductIdentifier(self) -> bool:
        return True

    def vendorIdentifier(self) -> int:
        return self._vid

    def productIdentifier(self) -> int:
        return self._pid

    def portName(self) -> str:
        return self._port_name

    def description(self) -> str:
        return "BORING test device"

    def manufacturer(self) -> str:
        return "BORING"

    def serialNumber(self) -> str:
        return self._serial_number


def test_fast_navigation_poll_is_ble_scoped_and_preserves_maintenance(qtbot, contract):
    from PySide6.QtCore import QTimer
    worker = SerialWorker(contract)
    worker._status_timer = QTimer(worker)
    selected = {"doubao_status": {"active": True, "selected": True}}
    worker._port_name = "COM5"
    worker._status_timer.setInterval(500)
    worker._on_status_complete(selected)
    assert worker._status_timer.interval() == 100
    worker._port_name = "ble:44:1b:f6:ff:e0:82"
    worker._on_status_complete(selected)
    assert worker._status_timer.interval() == 10
    worker._on_status_complete({"doubao_status": {"active": True, "selected": False}})
    assert worker._status_timer.interval() == 500
    worker._status_timer.setInterval(60000)
    worker._on_status_complete(selected)
    assert worker._status_timer.interval() == 60000


def test_fast_poll_never_overlaps_an_inflight_request(qtbot, contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    inflight = object()
    worker._session = inflight
    sent = []
    monkeypatch.setattr(worker, "_send_command", lambda *args: sent.append(args))
    for _ in range(20):
        worker._poll_status()
    assert worker._session is inflight and not sent

def test_scan_filters_candidates_by_authoritative_usb_identity(qtbot, contract, monkeypatch) -> None:
    ports = [
        FakePortInfo("accepted", contract.usb_vid, contract.usb_pid),
        FakePortInfo("wrong-pid", contract.usb_vid, 0x9999),
        FakePortInfo("wrong-vid", 0x1234, contract.usb_pid),
    ]
    monkeypatch.setattr(qt_serial.QSerialPortInfo, "availablePorts", lambda: ports)
    worker = SerialWorker(contract)

    with qtbot.waitSignal(worker.candidates_found, timeout=1000) as blocker:
        worker.scan()

    candidates = blocker.args[0]
    assert [candidate.port_name for candidate in candidates] == ["accepted"]


def test_agent_status_ack_keeps_command_identity_and_heartbeat_uses_new_request_id(qtbot, contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    sent = []
    monkeypatch.setattr(worker, "_send_command", lambda command, request_id: sent.append((command, request_id)))
    command = status_command(("working",) + ("idle",) * 5)
    worker.execute_command(command)
    with qtbot.waitSignal(worker.agent_status_completed) as response:
        worker._session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, sent[-1][1], json.dumps({
            "command": command.name, "result": {"source": "claude_code", "active": True, "lease_ms": 5000}
        }).encode()))
    assert response.args[0] is command
    worker.execute_command(status_command(("working",) + ("idle",) * 5))
    assert sent[-1][1] != sent[0][1]


def test_close_sends_cc_clear_before_disposing_owned_port(qtbot, contract, monkeypatch):
    worker = SerialWorker(contract)
    writes = []
    order = []
    worker._serial = SimpleNamespace(isOpen=lambda: True,
        write=lambda frame: writes.append(frame) or len(frame),
        waitForBytesWritten=lambda ms: order.append("flushed") or True)
    worker._agent_status_sent = True
    monkeypatch.setattr(worker, "_dispose_serial", lambda: order.append("closed"))
    worker.close()
    from controller_config.protocol.framing import FrameDecoder
    frames = FrameDecoder(max_payload_bytes=contract.max_payload_bytes, protocol_major=1, protocol_minor=0).feed(writes[0])
    assert frames[0].message_type == 0x29
    assert json.loads(frames[0].payload_bytes) == {"source": "claude_code"}
    assert order == ["flushed", "closed"]


def test_cc_clear_removes_queued_status_but_not_configuration(qtbot, contract):
    worker = SerialWorker(contract)
    config = Command("SET_CONFIG", 0x12, {})
    worker._queue_command(status_command(("working",) * 6))
    worker._queue_command(config)
    worker._queue_command(clear_status_command())
    assert [item.name for item in worker._queued_commands] == ["SET_CONFIG", "CLEAR_AGENT_STATUS"]


def test_scan_merges_macos_tty_and_cu_ports_for_one_physical_device(
    qtbot, contract, monkeypatch
) -> None:
    ports = [
        FakePortInfo(
            "tty.usbmodem113101",
            contract.usb_vid,
            contract.usb_pid,
            "CP01-E072A1DA05E0",
        ),
        FakePortInfo(
            "cu.usbmodem113101",
            contract.usb_vid,
            contract.usb_pid,
            "CP01-E072A1DA05E0",
        ),
    ]
    monkeypatch.setattr(qt_serial.QSerialPortInfo, "availablePorts", lambda: ports)
    worker = SerialWorker(contract)

    with qtbot.waitSignal(worker.candidates_found, timeout=1000) as blocker:
        worker.scan()

    candidates = blocker.args[0]
    assert [candidate.port_name for candidate in candidates] == ["cu.usbmodem113101"]


def test_scan_keeps_devices_with_different_usb_serial_numbers(
    qtbot, contract, monkeypatch
) -> None:
    ports = [
        FakePortInfo(
            "cu.usbmodem113101",
            contract.usb_vid,
            contract.usb_pid,
            "CP01-E072A1DA05E0",
        ),
        FakePortInfo(
            "cu.usbmodem213101",
            contract.usb_vid,
            contract.usb_pid,
            "CP01-102030405060",
        ),
    ]
    monkeypatch.setattr(qt_serial.QSerialPortInfo, "availablePorts", lambda: ports)
    worker = SerialWorker(contract)

    with qtbot.waitSignal(worker.candidates_found, timeout=1000) as blocker:
        worker.scan()

    candidates = blocker.args[0]
    assert [candidate.port_name for candidate in candidates] == [
        "cu.usbmodem113101",
        "cu.usbmodem213101",
    ]


def test_presence_check_emits_disconnect_when_selected_port_disappears(
    qtbot, contract, monkeypatch
) -> None:
    monkeypatch.setattr(qt_serial.QSerialPortInfo, "availablePorts", lambda: [])
    worker = SerialWorker(contract)
    worker._port_name = "missing"  # Exercise the active-port presence poll.

    with qtbot.waitSignal(worker.disconnected, timeout=1000) as blocker:
        worker._check_presence()

    assert "missing" in blocker.args[0]


def test_rescan_closes_previous_session(contract, monkeypatch) -> None:
    monkeypatch.setattr(qt_serial.QSerialPortInfo, "availablePorts", lambda: [])
    worker = SerialWorker(contract)
    worker._session = object()
    worker._port_name = "previous"

    worker.scan()

    assert worker._session is None
    assert worker._port_name == ""


def test_connect_passes_scanned_usb_serial_and_authenticator_to_bootstrap(
    qtbot, contract, monkeypatch
) -> None:
    captured = {}
    authenticator = object()

    class FakeSignal:
        def connect(self, _callback) -> None:
            pass

        def disconnect(self, _callback) -> None:
            pass

    class FakeSerial:
        ReadWrite = object()

        def __init__(self, _parent) -> None:
            self.readyRead = FakeSignal()
            self.errorOccurred = FakeSignal()
            self._open = False

        def setPortName(self, _port_name: str) -> None:  # noqa: N802
            pass

        def open(self, _mode) -> bool:
            self._open = True
            return True

        def isOpen(self) -> bool:  # noqa: N802
            return self._open

        def close(self) -> None:
            self._open = False

        def deleteLater(self) -> None:  # noqa: N802
            pass

    class FakeSession:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def start(self) -> None:
            captured["started"] = True

    monkeypatch.setattr(qt_serial, "QSerialPort", FakeSerial)
    monkeypatch.setattr(qt_serial, "BootstrapSession", FakeSession)
    worker = SerialWorker(contract, authenticator=authenticator)
    worker._candidates_by_port["accepted"] = PortCandidate(
        "accepted",
        serial_number="CP01-102030405060",
    )

    worker.connect_port("accepted")

    assert captured["started"] is True
    assert captured["authenticator"] is authenticator
    assert captured["usb_serial"] == "CP01-102030405060"


def test_connected_worker_polls_runtime_status(qtbot, contract, monkeypatch) -> None:
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    sent = []
    monkeypatch.setattr(
        worker,
        "_send_command",
        lambda command, request_id: sent.append((command, request_id)),
    )

    worker._poll_status()

    command, request_id = sent[0]
    assert command.name == "GET_STATUS"
    payload = {
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
    frame = Frame(
        protocol_major=1,
        protocol_minor=0,
        message_type=0x7E,
        flags=RESPONSE_FLAG,
        request_id=request_id,
        payload_bytes=json.dumps(payload).encode(),
    )
    with qtbot.waitSignal(worker.status_updated, timeout=1000) as blocker:
        assert worker._session is not None
        worker._session.accept(frame)

    assert blocker.args[0]["operating_mode"] == "codex"
    assert worker._session is None


def test_connected_worker_executes_validated_config_command(qtbot, contract, monkeypatch) -> None:
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    sent = []
    monkeypatch.setattr(
        worker,
        "_send_command",
        lambda command, request_id: sent.append((command, request_id)),
    )
    command = Command("VALIDATE_CONFIG", 0x11, {"config": {}, "digest": "a" * 64})

    worker.execute_command(command)

    _, request_id = sent[0]
    with qtbot.waitSignal(worker.command_completed, timeout=1000) as blocker:
        assert worker._session is not None
        worker._session.accept(
            Frame(
                protocol_major=1,
                protocol_minor=0,
                message_type=0x7E,
                flags=RESPONSE_FLAG,
                request_id=request_id,
                payload_bytes=b'{"command":"VALIDATE_CONFIG","result":{}}',
            )
        )

    assert blocker.args[0] == "VALIDATE_CONFIG"
    assert worker._session is None


def test_command_waits_for_inflight_status_poll_and_runs_once(
    qtbot, contract, monkeypatch
) -> None:
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    timer_events = []
    worker._status_timer = SimpleNamespace(
        interval=lambda: 500,
        setInterval=lambda value: None,
        stop=lambda: timer_events.append("stop"),
        start=lambda: timer_events.append("start"),
        isActive=lambda: False,
    )
    sent = []
    monkeypatch.setattr(
        worker,
        "_send_command",
        lambda command, request_id: sent.append((command, request_id)),
    )
    worker._poll_status()
    _, status_request_id = sent[0]
    command = Command("VALIDATE_CONFIG", 0x11, {"config": {}, "digest": "a" * 64})

    worker.execute_command(command)

    assert [item[0].name for item in sent] == ["GET_STATUS"]
    assert timer_events == ["stop"]
    status_payload = {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": "a" * 64},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "normal",
        },
    }
    assert worker._session is not None
    worker._session.accept(
        Frame(
            1,
            0,
            0x7E,
            RESPONSE_FLAG,
            status_request_id,
            json.dumps(status_payload).encode(),
        )
    )
    assert [item[0].name for item in sent] == ["GET_STATUS", "VALIDATE_CONFIG"]
    _, command_request_id = sent[1]

    with qtbot.waitSignal(worker.command_completed, timeout=1000) as blocker:
        assert worker._session is not None
        worker._session.accept(
            Frame(
                1,
                0,
                0x7E,
                RESPONSE_FLAG,
                command_request_id,
                b'{"command":"VALIDATE_CONFIG","result":{}}',
            )
        )

    assert blocker.args[0] == "VALIDATE_CONFIG"
    assert timer_events == ["stop", "start"]


def test_connected_worker_reports_command_nack_without_closing_port(
    qtbot, contract, monkeypatch
) -> None:
    worker = SerialWorker(contract)
    serial = SimpleNamespace(isOpen=lambda: True)
    worker._serial = serial
    sent = []
    monkeypatch.setattr(
        worker,
        "_send_command",
        lambda command, request_id: sent.append((command, request_id)),
    )
    worker.execute_command(Command("SET_CONFIG", 0x12, {}))
    _, request_id = sent[0]
    nack = {
        "command": "SET_CONFIG",
        "error": {
            "code": 10,
            "name": "GENERATION_CONFLICT",
            "message": "stale",
            "details": {"current_generation": 8},
        },
    }

    with qtbot.waitSignal(worker.command_failed, timeout=1000) as blocker:
        assert worker._session is not None
        worker._session.accept(
            Frame(1, 0, 0x7F, RESPONSE_FLAG | 0x02, request_id, json.dumps(nack).encode())
        )

    assert blocker.args[0] == "SET_CONFIG"
    assert blocker.args[1].error_name == "GENERATION_CONFLICT"
    assert worker._serial is serial


def test_preview_queue_keeps_only_latest_drag_snapshot(contract) -> None:
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    worker._session = object()
    first = set_lighting_preview_command(
        {"enabled": True, "brightness": 20, "under_key": []}
    )
    latest = set_lighting_preview_command(
        {"enabled": True, "brightness": 80, "under_key": []}
    )

    worker.execute_command(first)
    worker.execute_command(latest)

    assert list(worker._queued_commands) == [latest]


def test_preview_clear_drops_stale_frames_but_preserves_newer_preview(contract) -> None:
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    worker._session = object()
    stale = set_lighting_preview_command(
        {"enabled": True, "brightness": 20, "under_key": []}
    )
    clear = clear_lighting_preview_command()
    latest = set_lighting_preview_command(
        {"enabled": True, "brightness": 60, "under_key": []}
    )

    worker.execute_command(stale)
    worker.execute_command(clear)
    worker.execute_command(clear_lighting_preview_command())
    worker.execute_command(latest)

    queued = list(worker._queued_commands)
    assert [command.name for command in queued] == [
        CLEAR_LIGHTING_PREVIEW,
        SET_LIGHTING_PREVIEW,
    ]
    assert queued[1] is latest


def test_hello_header_version_mismatch_is_incompatible(qtbot, contract, monkeypatch) -> None:
    worker = SerialWorker(contract)
    frame = HEADER.pack(b"WMP1", 2, 0, 0x7F, 0x03, 1, 0, 0)
    worker._serial = SimpleNamespace(readAll=lambda: frame)
    worker._session = SimpleNamespace(current_command=SimpleNamespace(name="HELLO"))
    monkeypatch.setattr(worker, "close", lambda: None)

    with qtbot.waitSignal(worker.failure, timeout=1000) as blocker:
        worker._on_ready_read()

    assert blocker.args[0] is BootstrapKind.INCOMPATIBLE


def test_crc_failure_is_never_a_successful_read(qtbot, contract, monkeypatch):
    """A-03: a corrupt response is discarded and logged, never accepted. The
    in-flight request is left to its own timeout/retry rule instead of
    closing the whole link on the first bad frame."""
    worker = SerialWorker(contract)
    frame = HEADER.pack(b"WMP1", 1, 0, 0x7E, 0x01, 5, 2, 0) + b"{}"
    worker._serial = SimpleNamespace(readAll=lambda: frame)
    worker._port_name = "cu.usbmodem-test"
    accepted = []
    worker._session = SimpleNamespace(
        current_command=SimpleNamespace(name="CAPABILITIES"),
        current_request_id=5,
        accept=accepted.append,
    )
    closed, failures, warnings = [], [], []
    monkeypatch.setattr(worker, "close", lambda: closed.append(True))
    worker.failure.connect(lambda *args: failures.append(args))
    worker.link_warning.connect(lambda *args: warnings.append(args))
    worker._on_ready_read()
    assert accepted == [] and failures == [] and closed == []
    assert warnings and warnings[0][0] == "FRAME_DISCARDED"
    assert "request_id=5" in warnings[0][1] and "CRC32" in warnings[0][1]


def _maintenance_worker(contract, monkeypatch):
    worker = SerialWorker(contract)
    worker._serial = SimpleNamespace(isOpen=lambda: True)
    timer = SimpleNamespace(active=False, interval=500)
    worker._status_timer = SimpleNamespace(
        stop=lambda: setattr(timer, "active", False),
        start=lambda: setattr(timer, "active", True),
        setInterval=lambda value: setattr(timer, "interval", value),
        isActive=lambda: timer.active,
    )
    sent = []
    monkeypatch.setattr(worker, "_send_command", lambda command, request_id: sent.append((command, request_id)))
    worker._on_bootstrap_complete(SimpleNamespace(maintenance_only=True))
    return worker, timer, sent


@pytest.mark.parametrize("command", [
    Command("GET_CONFIG", 0x10, {}), Command("SET_CONFIG", 0x12, {}),
    Command("GET_STATUS", 0x13, {}), Command("CALIBRATE_JOYSTICK", 0x20, {}),
    status_command(("working",) * 6),
    set_lighting_preview_command({"enabled": True, "brightness": 20, "under_key": []}),
    Command("FW_STATUS", 0x12, {}),
])
def test_maintenance_worker_rejects_non_firmware_commands(qtbot, contract, monkeypatch, command):
    worker, timer, sent = _maintenance_worker(contract, monkeypatch)
    with qtbot.waitSignal(worker.command_failed) as failure:
        worker.execute_command(command)
    assert failure.args[0] == command.name
    assert "仅允许固件维护" in str(failure.args[1])
    assert not sent and not worker._queued_commands and not timer.active


@pytest.mark.parametrize("name, message_type", [
    ("FW_STATUS", 0x42), ("FW_BEGIN", 0x40), ("FW_DATA", 0x41),
    ("FW_END", 0x43), ("FW_ABORT", 0x44),
])
def test_maintenance_firmware_commands_never_restart_status_poll(
    qtbot, contract, monkeypatch, name, message_type,
):
    worker, timer, sent = _maintenance_worker(contract, monkeypatch)
    worker.set_status_poll_interval(200)
    worker._poll_status()
    assert not sent and not timer.active
    worker.execute_command(Command(name, message_type, {}))
    assert sent[-1][0].name == name
    worker._session.accept(Frame(1, 0, 0x7E, RESPONSE_FLAG, sent[-1][1], json.dumps({
        "command": name, "result": {}
    }).encode()))
    assert not timer.active
    worker._on_command_failed(name, Exception("transport"))
    assert not timer.active


def test_close_resets_maintenance_state(contract, monkeypatch):
    worker, timer, sent = _maintenance_worker(contract, monkeypatch)
    worker._status_timer = None
    monkeypatch.setattr(worker, "_dispose_serial", lambda: None)
    worker.close()
    assert not worker._maintenance_only
