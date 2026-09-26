from __future__ import annotations

import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, QSettings, Signal

from controller_config.codex_screen import CodexScreenBridge, _remaining
from controller_config.codex_usage import (
    CodexQuotaBucket,
    CodexQuotaWindow,
    CodexUsageSnapshot,
    CodexUsageStatus,
)
from controller_config.models import AppState


def usage(*, source="app_server", updated_at=None):
    return CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        source=source,
        updated_at=time.time() if updated_at is None else updated_at,
        buckets=(CodexQuotaBucket("codex", None, None,
                                  CodexQuotaWindow(6, 10_080, None),
                                  CodexQuotaWindow(25, 300, None)),),
    )


class Monitor(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.snapshot = usage()
        self.refresh_count = 0

    def refresh(self):
        self.refresh_count += 1


class ViewModel(QObject):
    changed = Signal(object)

    def __init__(self, supported=True):
        super().__init__()
        self.model = SimpleNamespace(
            state=AppState.READY,
            snapshot=SimpleNamespace(
                identity={"serial": "MIST-1"}, port_name="ble:MIST-1",
                is_read_only=False,
                capabilities={"features": {"codex_usage_display": supported}},
            ),
        )
        self.firmware_update = SimpleNamespace(blocks_editing=False)


class Gateway(QObject):
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.commands = []

    def execute_command(self, command):
        self.commands.append(command)


def ack(gateway, command):
    gateway.command_completed.emit(command.name, {
        "command": command.name,
        "result": {"active": command.name == "SET_CODEX_USAGE", "lease_ms": 600_000},
    })


def test_bridge_sends_only_to_capable_device_and_clears_on_disable(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monitor, gateway, model = Monitor(), Gateway(), ViewModel(supported=False)
    bridge = CodexScreenBridge(model, gateway, monitor, settings=settings)

    bridge.set_enabled(True)
    assert gateway.commands == []

    model.model.snapshot.capabilities["features"]["codex_usage_display"] = True
    model.changed.emit(model.model)
    assert monitor.refresh_count == 1
    assert len(gateway.commands) == 1
    command = gateway.commands[-1]
    assert command.message_type == 0x2C
    assert command.payload == {"source": "codex", "weekly_remaining": 94,
                               "five_hour_remaining": 75}
    ack(gateway, command)

    bridge.set_enabled(False)
    assert gateway.commands[-1].message_type == 0x2D
    assert gateway.commands[-1].payload == {"source": "codex"}
    bridge.shutdown()


def test_bridge_rejects_stale_or_fallback_usage(qapp, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("codex/screen_usage_enabled", True)
    monitor, gateway, model = Monitor(), Gateway(), ViewModel()
    bridge = CodexScreenBridge(model, gateway, monitor, settings=settings)
    assert gateway.commands[-1].name == "SET_CODEX_USAGE"
    ack(gateway, gateway.commands[-1])

    monitor.snapshot = usage(source="session_log")
    monitor.changed.emit(monitor.snapshot)
    assert gateway.commands[-1].name == "CLEAR_CODEX_USAGE"
    ack(gateway, gateway.commands[-1])

    monitor.snapshot = usage(updated_at=time.time() - 601)
    monitor.changed.emit(monitor.snapshot)
    assert len(gateway.commands) == 2
    bridge.shutdown()


def test_remaining_handles_missing_window_and_old_snapshot():
    assert _remaining(usage()) == (94, 75)
    assert _remaining(usage(source="session_log")) is None
    assert _remaining(usage(updated_at=time.time() - 601)) is None
