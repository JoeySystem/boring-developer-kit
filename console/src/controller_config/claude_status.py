"""Local Hooks receiver and authenticated, single-port CC status bridge."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QTimer, Signal, QSaveFile, QIODevice
from PySide6.QtNetwork import QHostAddress, QTcpServer

from controller_config.agent_status import (
    CLEAR_AGENT_STATUS, status_command, clear_status_command,
    validate_agent_response,
)
from controller_config.claude_sessions import ClaudeSessionRegistry


def _owner_alive(pid: int, started: float) -> bool:
    try:
        process = psutil.Process(pid)
        return process.create_time() == started and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, ValueError):
        return False


class ClaudeHookReceiver(QObject):
    event_received = Signal(object)

    def __init__(self, endpoint_path: Path, parent=None):
        super().__init__(parent)
        self.endpoint_path = endpoint_path
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        self._clients = {}
        self._token = ""

    def start(self) -> None:
        if self._server.isListening():
            return
        if not self._server.listen(QHostAddress.SpecialAddress.LocalHost, 0):
            raise RuntimeError(self._server.errorString())
        self._token = secrets.token_urlsafe(24)
        self.endpoint_path.parent.mkdir(parents=True, exist_ok=True)
        output = QSaveFile(str(self.endpoint_path))
        data = json.dumps({"host": "127.0.0.1", "port": self._server.serverPort(), "token": self._token}).encode()
        if not output.open(QIODevice.OpenModeFlag.WriteOnly) or output.write(data) != len(data) or not output.commit():
            self._server.close()
            raise OSError("无法保存 Claude Code 本机接收端信息")

    def stop(self) -> None:
        self._server.close()
        for client in list(self._clients):
            client.abort()
        # A stale descriptor is harmless: the hook has a bounded connection
        # timeout, and each running receiver gets a new local token.

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            client = self._server.nextPendingConnection()
            self._clients[client] = bytearray()
            client.readyRead.connect(lambda c=client: self._read(c))
            client.disconnected.connect(lambda c=client: self._forget(c))
            QTimer.singleShot(1000, client, client.abort)

    def _forget(self, client) -> None:
        self._clients.pop(client, None)
        client.deleteLater()

    def _read(self, client) -> None:
        data = self._clients.get(client)
        if data is None:
            return
        data.extend(bytes(client.readAll()))
        if len(data) > 16384:
            client.abort()
            return
        if b"\n" not in data:
            return
        try:
            message = json.loads(bytes(data).split(b"\n", 1)[0])
            if isinstance(message, dict) and message.get("token") == self._token and isinstance(message.get("event"), dict):
                self.event_received.emit(message["event"])
        except (ValueError, UnicodeError):
            pass
        client.disconnectFromHost()


class ClaudeStatusBridge(QObject):
    changed = Signal()

    def __init__(self, gateway, *, installation=None, registry=None, parent=None):
        super().__init__(parent)
        from controller_config.claude_hooks import ClaudeHookInstallation
        self.installation = installation or ClaudeHookInstallation()
        self.registry = registry or ClaudeSessionRegistry(owner_alive=_owner_alive)
        self._gateway = gateway
        self.receiver = ClaudeHookReceiver(self.installation.endpoint_path, self)
        self.receiver.event_received.connect(self.consume)
        self.enabled = False
        self.inspection = None
        self.message = "未启用 Claude Code 状态联动"
        self._binding = None
        self._supported = False
        self._paused = False
        self._pending = None
        self._last_states = None
        self._needs_clear = True
        self._fault = False
        self._visible_state = None
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)
        if hasattr(gateway, "agent_status_completed"):
            gateway.agent_status_completed.connect(self._completed)
            gateway.agent_status_failed.connect(self._failed)

    def start(self) -> None:
        if not self.installation.record_path.exists():
            return
        self.inspect()
        if self.inspection.installed and not self.inspection.problem:
            self._enable_receiver()

    def inspect(self) -> None:
        self.inspection = self.installation.inspect()
        self._notify()

    def enable(self) -> None:
        self.inspection = self.installation.install()
        self._enable_receiver()

    def _enable_receiver(self) -> None:
        self.receiver.start()
        self.enabled = True
        self._fault = False
        self._timer.start()
        self.registry.expire()
        self._flush(force=True)
        self._notify()

    def disable(self) -> None:
        # Remove only our installed hook entries; an error leaves them intact
        # and visible so the user can retry removal.
        self.inspection = self.installation.uninstall()
        self.shutdown()
        self.message = "已禁用 Claude Code 状态联动"
        self._notify()

    def shutdown(self) -> None:
        self._timer.stop()
        self.receiver.stop()
        if self.enabled and self._binding and self._supported:
            self._gateway.execute_command(clear_status_command())
        self.enabled = False
        self._pending = None
        self._last_states = None
        self._needs_clear = True

    def bind(self, snapshot) -> None:
        # Called only after a fresh successful authenticated/development-policy
        # handshake, never from draft changes or GET_STATUS polling.
        self._binding = (snapshot.identity.get("serial"), snapshot.identity.get("hardware_id"), snapshot.port_name)
        self._supported = snapshot.capabilities.get("features", {}).get("claude_code_status") is True and not snapshot.is_read_only
        self._pending = None
        self._last_states = None
        self._needs_clear = True
        self._fault = False
        self.registry.expire()
        self._flush(force=True)
        self._notify()

    def unbind(self, *, clear=False) -> None:
        if clear and self.enabled and self._binding and self._supported:
            self._gateway.execute_command(clear_status_command())
        self._binding = None
        self._pending = None
        self._needs_clear = True
        self._last_states = None
        self.message = "等待设备连接并通过身份检查"
        self._notify()

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        if paused:
            if self.enabled and self._binding and self._supported:
                self._gateway.execute_command(clear_status_command())
            self._pending = None
            self._needs_clear = True
            self.message = "固件维护期间暂停状态转发"
        else:
            self._flush(force=True)
        self._notify()

    def consume(self, event: dict) -> None:
        if not self.enabled:
            return
        self.registry.consume(event)
        self._flush()
        self._notify()

    def retry(self) -> None:
        self._fault = False
        self._needs_clear = True
        self._flush(force=True)

    def _tick(self) -> None:
        self.registry.expire()
        self._flush(force=True)
        self._notify()

    def _flush(self, *, force=False) -> None:
        if not self.enabled:
            return
        if not self._binding:
            self.message = "等待设备连接并通过身份检查"
            return
        if not self._supported:
            self.message = "当前固件不支持 CC 状态接口或设备只读，请升级固件"
            return
        if self._paused or self._pending is not None or self._fault:
            return
        states = self.registry.states()
        if self._needs_clear:
            command = clear_status_command()
        elif force or states != self._last_states or self.registry.pending_clear_slots:
            command = status_command(states)
        else:
            return
        self._pending = command
        self._gateway.execute_command(command)

    def _completed(self, command, payload) -> None:
        if command is not self._pending:
            return
        try:
            validate_agent_response(command.name, payload)
        except ValueError as exc:
            self._failed(command, exc)
            return
        self._pending = None
        if command.name == CLEAR_AGENT_STATUS:
            self._needs_clear = False
            self._last_states = None
        else:
            self._last_states = tuple(command.payload["states"])
            cleared = [index for index in self.registry.pending_clear_slots if self._last_states[index] == "idle"]
            self.registry.acknowledge_cleared(cleared)
            self.message = "USB 状态快照已接收；灯光与震动待实机验收"
        self._flush()
        self._notify()

    def _failed(self, command, error) -> None:
        if command is not self._pending:
            return
        self._pending = None
        self._fault = True
        self.message = "状态转发失败，可重试；设备将在租约结束后清除状态"
        self._notify()

    def _notify(self) -> None:
        visible = (self.enabled, self.message, self.inspection,
                   self.registry.sessions, self.registry.overflow)
        if visible != self._visible_state:
            self._visible_state = visible
            self.changed.emit()
