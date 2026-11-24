"""A-02 / A-07: automatic recovery after USB replug and reconnect scan backoff."""

from __future__ import annotations

import pytest

from controller_config.models import AppState, PortCandidate
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.connection_controller import ConnectionController, full_scan_delays_ms
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway


@pytest.fixture
def recovery(qtbot, contract):
    gateway = FakeWriteGateway()
    view_model = MainViewModel(gateway, contract)
    scans, connections = [], []
    gateway.scan = lambda **kwargs: scans.append(kwargs)
    gateway.connect_port = connections.append
    yield view_model, gateway, scans, connections
    view_model.shutdown()


def _fail_usb_read(view_model, gateway, port: str) -> None:
    view_model.connect_candidate(port)
    gateway.failure.emit(BootstrapKind.READ_FAILED, "读取失败", "GET_CONFIG timeout")
    assert view_model.model.state is AppState.READ_FAILED
    assert port in view_model._connection.failed_ports


def test_a02_replugged_usb_port_reconnects_automatically(recovery) -> None:
    view_model, gateway, _scans, connections = recovery
    port = "/dev/cu.usbmodem1101"
    _fail_usb_read(view_model, gateway, port)

    gateway.candidates_found.emit(())  # cable unplugged
    assert port not in view_model._connection.failed_ports
    gateway.candidates_found.emit((PortCandidate(port),))  # same name re-inserted
    assert connections == [port, port]


def test_a02_persistent_failed_port_is_not_retried(recovery) -> None:
    view_model, gateway, _scans, connections = recovery
    port = "COM7"
    _fail_usb_read(view_model, gateway, port)
    for _ in range(5):
        gateway.candidates_found.emit((PortCandidate(port),))
    assert connections == [port]
    assert port in view_model._connection.failed_ports


def test_a02_bluetooth_failure_survives_usb_only_enumeration(recovery) -> None:
    view_model, gateway, _scans, _connections = recovery
    view_model.connect_candidate("ble:known")
    gateway.failure.emit(BootstrapKind.READ_FAILED, "蓝牙配置读取失败", "services unavailable")
    gateway.candidates_found.emit(())
    assert "ble:known" in view_model._connection.failed_ports


# --- A-07: reconnect backoff -----------------------------------------------


def _no_device(view_model, gateway, clock):
    view_model._connection.monotonic = lambda: clock[0]
    view_model.refresh()
    gateway.candidates_found.emit(())
    assert view_model.model.state is AppState.NO_DEVICE


def _full_scan_times(view_model, scans, clock, *, until: float, step: float = 0.25):
    times = []
    while clock[0] <= until:
        before = len(scans)
        view_model._connection.scan_for_reconnect()
        if len(scans) > before and scans[-1] == {}:
            times.append(round(clock[0], 2))
        clock[0] += step
    return times


def test_a07_full_scans_back_off_to_five_seconds(recovery, monkeypatch) -> None:
    # Windows keeps the A-07 cap: each scan starts a PowerShell WinRT query.
    monkeypatch.setattr(ConnectionController, "RECONNECT_FULL_SCAN_DELAYS_MS",
                        full_scan_delays_ms("win32"))
    view_model, gateway, scans, _connections = recovery
    clock = [100.0]
    _no_device(view_model, gateway, clock)
    scans.clear()
    times = _full_scan_times(view_model, scans, clock, until=125.0)
    gaps = [round(b - a, 2) for a, b in zip(times, times[1:])]
    assert gaps[:3] == [0.5, 1.0, 2.0]
    assert set(gaps[3:]) == {5.0}
    # USB stays responsive between full scans (≤ 1 s cadence).
    assert view_model._connection.reconnect_timer.interval() <= 1000
    assert {"usb_only": True} in scans


def test_sh005_mac_looks_for_bluetooth_every_second(recovery, monkeypatch) -> None:
    # SH-005: the shared CBCentralManager makes a full scan cheap on macOS.
    monkeypatch.setattr(ConnectionController, "RECONNECT_FULL_SCAN_DELAYS_MS",
                        full_scan_delays_ms("darwin"))
    view_model, gateway, scans, _connections = recovery
    clock = [100.0]
    _no_device(view_model, gateway, clock)
    scans.clear()
    times = _full_scan_times(view_model, scans, clock, until=110.0)
    gaps = [round(b - a, 2) for a, b in zip(times, times[1:])]
    assert gaps[:2] == [0.5, 1.0]
    assert set(gaps[2:]) == {1.0}
    assert view_model._connection.reconnect_timer.interval() == 1000


def test_sh005_mac_full_scan_survives_an_early_timer_tick(recovery, monkeypatch) -> None:
    # Qt's coarse timer may fire a 1 s tick up to 5 % early; that tick must
    # still scan Bluetooth, or the effective cadence doubles to 2 s.
    monkeypatch.setattr(ConnectionController, "RECONNECT_FULL_SCAN_DELAYS_MS",
                        full_scan_delays_ms("darwin"))
    view_model, gateway, scans, _connections = recovery
    clock = [100.0]
    _no_device(view_model, gateway, clock)
    view_model._connection.full_scans = 5
    view_model._connection.scan_for_reconnect()
    scans.clear()
    for _ in range(5):
        clock[0] += 0.95
        view_model._connection.scan_for_reconnect()
    assert scans == [{}] * 5


def test_sh005_windows_keeps_the_five_second_cap() -> None:
    assert full_scan_delays_ms("win32")[-1] == 5000
    assert full_scan_delays_ms("darwin")[-1] <= 2000


def test_a07_manual_refresh_resets_backoff(recovery) -> None:
    view_model, gateway, scans, _connections = recovery
    clock = [100.0]
    _no_device(view_model, gateway, clock)
    _full_scan_times(view_model, scans, clock, until=120.0)
    assert view_model._connection.full_scans >= 4
    view_model.refresh()
    assert view_model._connection.full_scans == 0
    assert view_model._connection.reconnect_timer.interval() == 500


def test_a07_found_device_resets_backoff(recovery) -> None:
    view_model, gateway, scans, connections = recovery
    clock = [100.0]
    _no_device(view_model, gateway, clock)
    _full_scan_times(view_model, scans, clock, until=120.0)
    gateway.candidates_found.emit((PortCandidate("/dev/cu.usbmodem1"),))
    assert view_model._connection.full_scans == 0
    assert connections[-1] == "/dev/cu.usbmodem1"


def test_a07_shared_mac_central_is_created_once() -> None:
    from types import SimpleNamespace
    from controller_config.transport.ble import SharedMacCentral

    created = []
    SharedMacCentral.reset_for_tests()
    SharedMacCentral.factory = lambda: (created.append(1) or object(), SimpleNamespace(_owner=None))
    SharedMacCentral.settled = lambda _manager: True
    try:
        owners = [object() for _ in range(5)]
        managers = {id(SharedMacCentral.acquire(owner)[0]) for owner in owners}
        for owner in owners:
            SharedMacCentral.release(owner)
        assert len(created) == 1 and len(managers) == 1
        assert SharedMacCentral.creations == 1
    finally:
        SharedMacCentral.factory = None
        SharedMacCentral.settled = None
        SharedMacCentral.reset_for_tests()


# --- A-04: dropped commands must not rewrite a write transaction ------------

from dataclasses import replace as _replace  # noqa: E402

from controller_config.protocol.bootstrap import BootstrapError  # noqa: E402
from controller_config.transactions import ConfigTransactionState  # noqa: E402
from test_write_transaction import _dirty_view_model  # noqa: E402


@pytest.mark.parametrize("state", [
    ConfigTransactionState.UNKNOWN,
    ConfigTransactionState.PENDING,
    ConfigTransactionState.VERIFYING,
])
@pytest.mark.parametrize("command", ["SET_CONFIG", "GET_CONFIG", "ACTIVATE_CONFIG"])
def test_a04_dropped_command_keeps_unreconciled_write_state(qtbot, contract, state, command) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model._write_transaction = _replace(view_model._write_transaction, state=state)
    gateway.command_failed.emit(command, BootstrapError.command_dropped(command, "连接已关闭"))
    assert view_model._write_transaction.state is state
    view_model.shutdown()
