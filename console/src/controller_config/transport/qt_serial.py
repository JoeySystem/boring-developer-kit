from __future__ import annotations

import secrets
from collections import deque

from PySide6.QtCore import QObject, QMetaObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo

from controller_config.models import PortCandidate
from controller_config.agent_status import (
    AGENT_COMMANDS, SET_AGENT_STATUS, CLEAR_AGENT_STATUS,
    clear_status_command, validate_agent_response,
)
from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
    validate_lighting_preview_response,
)
from controller_config.protocol.bootstrap import (
    BootstrapError,
    BootstrapKind,
    BootstrapSession,
    Command,
    CommandSession,
    StatusSession,
)
from controller_config.protocol.contract import Contract
from controller_config.protocol.device_auth import DeviceAuthenticator
from controller_config.protocol.framing import (
    FrameDecoder,
    FrameError,
    ProtocolVersionError,
    encode_request,
)


class SerialWorker(QObject):
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
    ) -> None:
        super().__init__()
        self._contract = contract
        self._authenticator = authenticator
        self._candidates_by_port: dict[str, PortCandidate] = {}
        self._serial: QSerialPort | None = None
        self._session: BootstrapSession | StatusSession | CommandSession | None = None
        self._queued_commands: deque[Command] = deque()
        self._decoder = FrameDecoder(
            max_payload_bytes=contract.max_payload_bytes,
            protocol_major=contract.protocol_major,
            protocol_minor=contract.protocol_minor,
        )
        self._request_timer: QTimer | None = None
        self._presence_timer: QTimer | None = None
        self._status_timer: QTimer | None = None
        self._closing = False
        self._port_name = ""
        self._request_id = secrets.randbits(32) or 1
        self._agent_status_sent = False

    @Slot()
    def scan(self) -> None:
        self.close()
        self.progress.emit("正在查找 BORING 设备…")
        candidates_by_device: dict[str, PortCandidate] = {}
        for info in QSerialPortInfo.availablePorts():
            if not info.hasVendorIdentifier() or not info.hasProductIdentifier():
                continue
            if info.vendorIdentifier() != self._contract.usb_vid:
                continue
            if info.productIdentifier() != self._contract.usb_pid:
                continue
            candidate = PortCandidate(
                port_name=info.portName(),
                description=info.description(),
                manufacturer=info.manufacturer(),
                serial_number=info.serialNumber(),
            )
            device_key = candidate.serial_number or candidate.port_name
            current = candidates_by_device.get(device_key)
            if current is None or (
                current.port_name.startswith("tty.")
                and candidate.port_name.startswith("cu.")
            ):
                candidates_by_device[device_key] = candidate
        candidates = tuple(candidates_by_device.values())
        self._candidates_by_port = {
            candidate.port_name: candidate for candidate in candidates
        }
        self.candidates_found.emit(candidates)

    @Slot(str)
    def connect_port(self, port_name: str) -> None:
        self.close()
        self._closing = False
        self._port_name = port_name
        self._decoder.reset()
        serial = QSerialPort(self)
        serial.setPortName(port_name)
        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_serial_error)
        self._serial = serial
        self.progress.emit(f"正在连接 {port_name}…")
        if not serial.open(QSerialPort.ReadWrite):
            detail = serial.errorString().strip() or "系统未提供串口错误详情"
            self.failure.emit(
                BootstrapKind.READ_FAILED,
                "无法打开设备通信端口",
                f"{detail}。请关闭其他 BORING 控制台或串口工具后重新扫描。",
            )
            self._dispose_serial()
            return

        self._start_bootstrap(
            port_name,
            (
                self._candidates_by_port[port_name].serial_number
                if port_name in self._candidates_by_port
                else ""
            ),
        )

    def _start_bootstrap(self, port_name: str, advertised_serial: str = "") -> None:
        """Start the transport-independent WMP bootstrap on an open channel."""

        self._request_timer = QTimer(self)
        self._request_timer.setSingleShot(True)
        self._request_timer.setInterval(2500)
        self._request_timer.timeout.connect(self._on_request_timeout)

        self._presence_timer = QTimer(self)
        self._presence_timer.setInterval(1000)
        self._presence_timer.timeout.connect(self._check_presence)
        self._presence_timer.start()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._poll_status)

        self._session = BootstrapSession(
            contract=self._contract,
            port_name=port_name,
            usb_serial=advertised_serial,
            send=self._send_command,
            completed=self._on_bootstrap_complete,
            failed=self._on_bootstrap_failed,
            authenticator=self._authenticator,
            next_request_id=self._allocate_request_id,
        )
        self._session.start()

    @Slot()
    def close(self) -> None:
        self._closing = True
        # Closing cannot wait for the normal request queue. Send a best-effort
        # CLEAR on the still-owned port; lease expiry is the crash fallback.
        if self._agent_status_sent and self._serial is not None and self._serial.isOpen():
            command = clear_status_command()
            frame = encode_request(
                protocol_major=self._contract.protocol_major,
                protocol_minor=self._contract.protocol_minor,
                message_type=command.message_type, request_id=self._allocate_request_id(),
                payload=command.payload, max_payload_bytes=self._contract.max_payload_bytes,
            )
            self._serial.write(frame)
            self._serial.waitForBytesWritten(150)
        self._agent_status_sent = False
        if self._request_timer is not None:
            self._request_timer.stop()
            self._request_timer.deleteLater()
            self._request_timer = None
        if self._presence_timer is not None:
            self._presence_timer.stop()
            self._presence_timer.deleteLater()
            self._presence_timer = None
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer.deleteLater()
            self._status_timer = None
        self._session = None
        self._queued_commands.clear()
        self._dispose_serial()

    def _send_command(self, command: Command, request_id: int) -> None:
        serial = self._serial
        if serial is None or not serial.isOpen():
            self._on_bootstrap_failed(
                BootstrapError.from_connection("设备通信端口已经关闭")
            )
            return
        if not isinstance(self._session, StatusSession):
            self.progress.emit(f"正在读取：{command.name}")
        frame = encode_request(
            protocol_major=self._contract.protocol_major,
            protocol_minor=self._contract.protocol_minor,
            message_type=command.message_type,
            request_id=request_id,
            payload=command.payload,
            max_payload_bytes=self._contract.max_payload_bytes,
        )
        written = serial.write(frame)
        if command.name == SET_AGENT_STATUS:
            self._agent_status_sent = True
        if written != len(frame):
            self.failure.emit(BootstrapKind.READ_FAILED, "无法向设备发送读取请求", serial.errorString())
            self.close()
            return
        if self._request_timer is not None:
            self._request_timer.setInterval(max(100, command.timeout_ms))
            self._request_timer.start()

    @Slot()
    def _on_ready_read(self) -> None:
        serial = self._serial
        session = self._session
        if serial is None or session is None:
            return
        try:
            frames = self._decoder.feed(bytes(serial.readAll()))
            for frame in frames:
                if frame.request_id == session.current_request_id and self._request_timer is not None:
                    self._request_timer.stop()
                session.accept(frame)
        except FrameError as exc:
            incompatible = (
                isinstance(exc, ProtocolVersionError)
                and session.current_command is not None
                and session.current_command.name == "HELLO"
            )
            kind = BootstrapKind.INCOMPATIBLE if incompatible else BootstrapKind.READ_FAILED
            message = "这台设备与当前 BORING 控制台不兼容" if incompatible else "设备返回了无法解析的数据"
            self.failure.emit(kind, message, str(exc))
            self.close()

    @Slot(QSerialPort.SerialPortError)
    def _on_serial_error(self, error: QSerialPort.SerialPortError) -> None:
        if self._closing or error == QSerialPort.NoError:
            return
        if error == QSerialPort.ResourceError:
            detail = self._serial.errorString() if self._serial is not None else "设备已移除"
            self.disconnected.emit(detail)
            self.close()

    @Slot()
    def _on_request_timeout(self) -> None:
        if self._session is not None:
            self._session.timeout()

    @Slot()
    def _check_presence(self) -> None:
        if not self._port_name:
            return
        names = {info.portName() for info in QSerialPortInfo.availablePorts()}
        if self._port_name not in names:
            self.disconnected.emit(f"端口 {self._port_name} 已消失")
            self.close()

    @Slot()
    def _poll_status(self) -> None:
        serial = self._serial
        if serial is None or not serial.isOpen() or self._session is not None:
            return
        self._session = StatusSession(
            contract=self._contract,
            send=self._send_command,
            completed=self._on_status_complete,
            failed=self._on_bootstrap_failed,
            request_id=self._allocate_request_id(),
        )
        self._session.start()

    @Slot(object)
    def execute_command(self, command: object) -> None:
        if not isinstance(command, Command):
            return
        serial = self._serial
        if serial is None or not serial.isOpen():
            if command.name in AGENT_COMMANDS:
                self.agent_status_failed.emit(command, BootstrapError.from_connection("设备通信端口已经关闭"))
            self.command_failed.emit(
                command.name,
                BootstrapError.from_connection("设备通信端口已经关闭"),
            )
            return
        if self._session is not None:
            self._queue_command(command)
            if self._status_timer is not None:
                self._status_timer.stop()
            return
        if command.name == "GET_CONFIG":
            validator = self._contract.validate_config_response
        elif command.name in AGENT_COMMANDS:
            validator = lambda payload: validate_agent_response(command.name, payload)
        elif command.name in {SET_LIGHTING_PREVIEW, CLEAR_LIGHTING_PREVIEW}:
            validator = lambda payload: validate_lighting_preview_response(
                command.name, payload
            )
        else:
            validator = None
        self._session = CommandSession(
            command=command,
            send=self._send_command,
            completed=lambda payload: self._finish_command(command, payload, failed=False),
            failed=lambda error: self._finish_command(command, error, failed=True),
            request_id=self._allocate_request_id(),
            validator=validator,
        )
        self._session.start()

    def _finish_command(self, command: Command, result: object, *, failed: bool) -> None:
        if command.name in AGENT_COMMANDS:
            signal = self.agent_status_failed if failed else self.agent_status_completed
            # Preserve the exact submitted object so a late ACK from device A
            # cannot complete device B's pending snapshot.
            signal.emit(command, result)
        if not failed and command.name == CLEAR_AGENT_STATUS:
            self._agent_status_sent = False
        if failed:
            self._on_command_failed(command.name, result)
        else:
            self._on_command_complete(command.name, result)

    @Slot(int)
    def set_status_poll_interval(self, interval_ms: int) -> None:
        if self._status_timer is not None:
            self._status_timer.setInterval(max(100, interval_ms))
            if self._session is None and not self._status_timer.isActive():
                self._status_timer.start()

    def _on_bootstrap_complete(self, snapshot: object) -> None:
        if self._request_timer is not None:
            self._request_timer.stop()
        self._session = None
        self.snapshot_ready.emit(snapshot)
        if self._status_timer is not None:
            self._status_timer.start()

    def _on_status_complete(self, status: dict) -> None:
        if self._request_timer is not None:
            self._request_timer.stop()
        self._session = None
        self.status_updated.emit(status)
        self._run_queued_command()

    def _on_command_complete(self, command_name: str, payload: dict) -> None:
        if self._request_timer is not None:
            self._request_timer.stop()
        self._session = None
        self.command_completed.emit(command_name, payload)
        self._run_queued_command()
        if (
            self._session is None
            and self._status_timer is not None
            and not self._status_timer.isActive()
        ):
            self._status_timer.start()

    def _on_command_failed(self, command_name: str, error: BootstrapError) -> None:
        if self._request_timer is not None:
            self._request_timer.stop()
        self._session = None
        self.command_failed.emit(command_name, error)
        self._run_queued_command()
        if (
            self._session is None
            and self._status_timer is not None
            and not self._status_timer.isActive()
        ):
            self._status_timer.start()

    def _run_queued_command(self) -> None:
        if self._queued_commands:
            self.execute_command(self._queued_commands.popleft())

    def _queue_command(self, command: Command) -> None:
        if command.name == CLEAR_AGENT_STATUS:
            self._queued_commands = deque(
                queued for queued in self._queued_commands if queued.name not in AGENT_COMMANDS
            )
        if command.name == SET_LIGHTING_PREVIEW:
            for index in range(len(self._queued_commands) - 1, -1, -1):
                queued = self._queued_commands[index]
                if queued.name == CLEAR_LIGHTING_PREVIEW:
                    break
                if queued.name == SET_LIGHTING_PREVIEW:
                    self._queued_commands[index] = command
                    return
            self._queued_commands.append(command)
            return
        if command.name == CLEAR_LIGHTING_PREVIEW:
            self._queued_commands = deque(
                queued
                for queued in self._queued_commands
                if queued.name != SET_LIGHTING_PREVIEW
            )
            if (
                self._queued_commands
                and self._queued_commands[-1].name == CLEAR_LIGHTING_PREVIEW
            ):
                return
        self._queued_commands.append(command)

    def _on_bootstrap_failed(self, error: BootstrapError) -> None:
        if self._request_timer is not None:
            self._request_timer.stop()
        self.failure.emit(error.kind, str(error), error.technical)
        self.close()

    def _allocate_request_id(self) -> int:
        self._request_id = (self._request_id % 0xFFFFFFFF) + 1
        return self._request_id

    def _dispose_serial(self) -> None:
        serial = self._serial
        self._serial = None
        self._port_name = ""
        if serial is not None:
            serial.readyRead.disconnect(self._on_ready_read)
            serial.errorOccurred.disconnect(self._on_serial_error)
            if serial.isOpen():
                serial.close()
            serial.deleteLater()


class SerialGateway(QObject):
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

    _scan_requested = Signal()
    _connect_requested = Signal(str)
    _command_requested = Signal(object)
    _status_interval_requested = Signal(int)

    def __init__(
        self,
        contract: Contract,
        authenticator: DeviceAuthenticator | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._worker = SerialWorker(contract, authenticator)
        self._worker.moveToThread(self._thread)
        self._scan_requested.connect(self._worker.scan)
        self._connect_requested.connect(self._worker.connect_port)
        self._command_requested.connect(self._worker.execute_command)
        self._status_interval_requested.connect(self._worker.set_status_poll_interval)
        self._worker.candidates_found.connect(self.candidates_found)
        self._worker.progress.connect(self.progress)
        self._worker.snapshot_ready.connect(self.snapshot_ready)
        self._worker.status_updated.connect(self.status_updated)
        self._worker.command_completed.connect(self.command_completed)
        self._worker.command_failed.connect(self.command_failed)
        self._worker.agent_status_completed.connect(self.agent_status_completed)
        self._worker.agent_status_failed.connect(self.agent_status_failed)
        self._worker.failure.connect(self.failure)
        self._worker.disconnected.connect(self.disconnected)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    def scan(self, *, usb_only: bool = False) -> None:
        self._scan_requested.emit()

    def connect_port(self, port_name: str) -> None:
        self._connect_requested.emit(port_name)

    def execute_command(self, command: Command) -> None:
        self._command_requested.emit(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self._status_interval_requested.emit(interval_ms)

    def shutdown(self) -> None:
        if not self._thread.isRunning():
            return
        QMetaObject.invokeMethod(self._worker, "close", Qt.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait(3000)
