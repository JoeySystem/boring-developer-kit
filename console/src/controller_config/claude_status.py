"""Local Hooks receiver and Claude session tracking for the status lights."""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QTimer, Signal, QSaveFile, QIODevice
from PySide6.QtNetwork import QHostAddress, QTcpServer

from controller_config.claude_sessions import ClaudeSessionRegistry

_LOG = logging.getLogger(__name__)
DISABLED_MESSAGE = "已禁用 Claude Code 状态联动"


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
    """Hooks opt-in, the local receiver and the session registry.

    design decision: Claude's lights go over the generic `desktop` source
    (``DesktopAgentLink`` registers ``ClaudeDesktopAdapter`` with the hub),
    so this bridge no longer sends the old `claude_code` source to the
    device. ``delivery`` is set by the link: it reports how the lights are
    doing on the device (message, fault, retry, which light a session has).
    """
    changed = Signal()
    # Every registry change (event or expiry): the hub re-reads Claude.
    sessions_changed = Signal()

    def __init__(self, gateway=None, *, installation=None, registry=None, parent=None):
        super().__init__(parent)
        from controller_config.claude_hooks import ClaudeHookInstallation
        self.installation = installation or ClaudeHookInstallation()
        self.registry = registry or ClaudeSessionRegistry(owner_alive=_owner_alive)
        self.receiver = ClaudeHookReceiver(self.installation.endpoint_path, self)
        self.receiver.event_received.connect(self.consume)
        self.enabled = False
        self.inspection = None
        self.message = "未启用 Claude Code 状态联动"
        self.delivery = None
        self._visible_state = None
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)

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

    def repair(self) -> None:
        # H20, one click: point only BORING's own recorded entries at this
        # copy of Console; every other part of settings.json is kept.
        self.enable()

    def _enable_receiver(self) -> None:
        self.receiver.start()
        self.enabled = True
        self._timer.start()
        self._expire()
        self._notify()

    def disable(self) -> None:
        # Remove only our installed hook entries; an error leaves them intact
        # and visible so the user can retry removal.
        self.inspection = self.installation.uninstall()
        self.shutdown()
        self.message = DISABLED_MESSAGE
        self._notify()

    def shutdown(self) -> None:
        self._timer.stop()
        self.receiver.stop()
        self.enabled = False

    # Session points kept for MainViewModel; the device side is the hub's.
    def bind(self, _snapshot) -> None:
        self._notify()

    def unbind(self, *, clear=False) -> None:
        self._notify()

    def set_paused(self, _paused: bool) -> None:
        self._notify()

    @property
    def fault(self) -> bool:
        return bool(self.enabled and self.delivery is not None and self.delivery.fault)

    def retry(self) -> None:
        if self.delivery is not None:
            self.delivery.retry()
        self._notify()

    def consume(self, event: dict) -> None:
        if not self.enabled:
            return
        self.registry.consume(event)
        self._registry_changed()
        self._notify()

    def _tick(self) -> None:
        self._expire()
        self._notify()

    def _expire(self) -> None:
        self.registry.expire()
        self._registry_changed()

    def _registry_changed(self) -> None:
        # No device ACK is involved any more: a released registry slot is
        # free at once (the hub decides the lights on its own).
        if self.registry.pending_clear_slots:
            self.registry.acknowledge_cleared(self.registry.pending_clear_slots)
        self.sessions_changed.emit()

    def _update_message(self) -> None:
        if not self.enabled:
            if self.message == DISABLED_MESSAGE or self.message.startswith("Claude 状态灯未启动"):
                return  # the user's removal or a start-up failure stays shown
            self.message = "未启用 Claude Code 状态联动"
        elif self.delivery is None:
            self.message = "状态联动已开启"
        else:
            self.message = self.delivery.message()

    def _notify(self) -> None:
        self._update_message()
        visible = (self.enabled, self.message, self.inspection, self.fault,
                   self.registry.sessions, self.registry.overflow)
        if visible != self._visible_state:
            self._visible_state = visible
            self.changed.emit()
