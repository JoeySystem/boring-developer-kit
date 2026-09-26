"""Opt-in, volatile Codex quota display for capable BORING MIST firmware."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from controller_config.codex_usage import CodexUsageSnapshot, CodexUsageStatus
from controller_config.models import AppState
from controller_config.protocol.bootstrap import Command


SETTING_KEY = "codex/screen_usage_enabled"
SET_CODEX_USAGE = "SET_CODEX_USAGE"
CLEAR_CODEX_USAGE = "CLEAR_CODEX_USAGE"
COMMANDS = {SET_CODEX_USAGE, CLEAR_CODEX_USAGE}
LEASE_MS = 600_000
HEARTBEAT_MS = 120_000
REFRESH_MS = 60_000


def _remaining(snapshot: CodexUsageSnapshot) -> int | None:
    if (snapshot.status is not CodexUsageStatus.AVAILABLE or
            snapshot.source != "app_server" or snapshot.updated_at is None or
            time.time() - snapshot.updated_at > LEASE_MS / 1000):
        return None
    bucket = next((item for item in snapshot.buckets if item.limit_id == "codex"), None)
    if bucket is None:
        return None
    weekly = next((window for window in (bucket.primary, bucket.secondary)
                   if window is not None and window.window_minutes == 10_080), None)
    return round(weekly.remaining_percent) if weekly is not None else None


def _five_hour_remaining(snapshot: CodexUsageSnapshot) -> int | None:
    if _remaining(snapshot) is None:
        return None
    bucket = next((item for item in snapshot.buckets if item.limit_id == "codex"), None)
    if bucket is None:
        return None
    five_hour = next((window for window in (bucket.primary, bucket.secondary)
                      if window is not None and window.window_minutes == 300), None)
    return round(five_hour.remaining_percent) if five_hour is not None else None


class CodexScreenBridge(QObject):
    changed = Signal()

    def __init__(self, view_model, gateway, monitor, *, settings=None, parent=None):
        super().__init__(parent)
        self._view_model = view_model
        self._gateway = gateway
        self._monitor = monitor
        self._settings = settings or QSettings()
        self._enabled = self._settings.value(SETTING_KEY, False, type=bool)
        self._usage = monitor.snapshot
        self._binding = None
        self._last_sent = None
        self._pending = None
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(lambda: self._flush(force=True))
        self._timer.start()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_usage)
        self._refresh_timer.start()
        monitor.changed.connect(self._on_usage)
        view_model.changed.connect(self._on_device)
        gateway.command_completed.connect(self._completed)
        gateway.command_failed.connect(self._failed)
        self._on_device(None)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def supported(self) -> bool:
        snapshot = self._view_model.model.snapshot
        features = snapshot.capabilities.get("features") if snapshot is not None else None
        return (self._view_model.model.state is AppState.READY and
                snapshot is not None and not snapshot.is_read_only and
                isinstance(features, dict) and
                features.get("codex_usage_display") is True)

    @property
    def menu_supported(self) -> bool:
        snapshot = self._view_model.model.snapshot
        features = snapshot.capabilities.get("features") if snapshot is not None else None
        return self.supported and isinstance(features, dict) and (
            features.get("codex_quota_menu") is True)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._settings.setValue(SETTING_KEY, self._enabled)
        if self._enabled:
            self._refresh_usage()
        self._flush(force=True)
        self.changed.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        self._refresh_timer.stop()
        if (self._binding is not None and
                (self._last_sent is not None or
                 (self._pending is not None and self._pending[0] == SET_CODEX_USAGE))):
            self._gateway.execute_command(Command(CLEAR_CODEX_USAGE, 0x2D,
                                                  {"source": "codex"}, timeout_ms=1200))

    def _on_usage(self, snapshot: CodexUsageSnapshot) -> None:
        self._usage = snapshot
        if snapshot.status is not CodexUsageStatus.LOADING:
            self._flush()

    def _on_device(self, _model) -> None:
        snapshot = self._view_model.model.snapshot
        binding = ((snapshot.identity.get("serial"), snapshot.port_name)
                   if self.supported else None)
        if binding != self._binding:
            self._binding = binding
            self._pending = None
            self._last_sent = None
            self.changed.emit()
            if self._binding is not None:
                self._refresh_usage()
            self._flush(force=True)

    def _refresh_usage(self) -> None:
        if (self._enabled or self.menu_supported) and self.supported:
            self._monitor.refresh()

    def _flush(self, *, force: bool = False) -> None:
        if self._binding is None or self._pending is not None or not self.supported:
            return
        if self._view_model.firmware_update.blocks_editing:
            return
        if self._usage.status is CodexUsageStatus.LOADING:
            return
        weekly = _remaining(self._usage)
        values = None
        if weekly is not None and (self._enabled or self.menu_supported):
            values = {"source": "codex", "weekly_remaining": weekly}
            if self.menu_supported:
                values["show_on_home"] = self._enabled
                five_hour = _five_hour_remaining(self._usage)
                if five_hour is not None:
                    values["five_hour_remaining"] = five_hour
        if values is None:
            if self._last_sent is None:
                return
            command = Command(CLEAR_CODEX_USAGE, 0x2D, {"source": "codex"}, timeout_ms=1200)
        elif force or values != self._last_sent:
            command = Command(SET_CODEX_USAGE, 0x2C,
                              values,
                              timeout_ms=1200)
        else:
            return
        self._pending = (command.name, values)
        self._gateway.execute_command(command)

    def _completed(self, name: str, payload: dict) -> None:
        if name not in COMMANDS or self._pending is None or self._pending[0] != name:
            return
        result = payload.get("result") if isinstance(payload, dict) else None
        if (isinstance(payload, dict) and payload.get("command") == name and
                isinstance(result, dict) and
                result.get("active") is (name == SET_CODEX_USAGE) and
                result.get("lease_ms") == LEASE_MS):
            self._last_sent = self._pending[1]
        self._pending = None
        QTimer.singleShot(0, self._flush)

    def _failed(self, name: str, _error) -> None:
        if name in COMMANDS and self._pending is not None and self._pending[0] == name:
            self._pending = None
