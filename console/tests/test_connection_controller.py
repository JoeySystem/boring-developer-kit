"""ConnectionController on its own: no MainViewModel, no Qt window."""

from __future__ import annotations

from dataclasses import replace

import pytest

from controller_config.models import AppState, PortCandidate, ScreenModel
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.protocol.device_auth import DeviceTrustState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.connection_controller import (
    CONFIG_STORAGE_UNAVAILABLE,
    ConnectionContext,
    ConnectionController,
)


class Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def scan(self, *, usb_only: bool = False) -> None:
        self.calls.append(("scan", usb_only))

    def scan_reconnect(self, port_name: str, *, usb_only: bool = False) -> None:
        self.calls.append(("scan_reconnect", port_name, usb_only))

    def connect_port(self, port_name: str) -> None:
        self.calls.append(("connect", port_name))

    def probe_usb(self, serial: str) -> None:
        self.calls.append(("probe_usb", serial))


class Harness:
    """Plays MainViewModel: applies requested screens, opens requested ports."""

    def __init__(self) -> None:
        self.gateway = Gateway()
        self.model = ScreenModel(AppState.SCANNING)
        self.firmware_busy = False
        self.calibration_unknown = False
        self.blocked = False
        self.clock = [10.0]
        self.controller = ConnectionController(
            self.gateway,
            lambda: ConnectionContext(self.model, self.firmware_busy, self.calibration_unknown),
            lambda: self.blocked,
            clock=lambda: self.clock[0],
        )
        self.connects: list[str] = []
        self.storage: list[bool] = []
        self.controller.screen_requested.connect(self._show)
        self.controller.connect_requested.connect(self.connects.append)
        self.controller.storage_degraded_changed.connect(self.storage.append)

    def _show(self, model: ScreenModel) -> None:
        self.model = model
        self.controller.sync_usb_probe(model)

    @property
    def state(self) -> AppState:
        return self.model.state


@pytest.fixture
def harness(qtbot):
    harness = Harness()
    yield harness
    harness.controller.shutdown()


def test_single_candidate_requests_connect_and_multiple_asks_user(harness):
    harness.controller.handle_candidates((PortCandidate("cu.a"),))
    assert harness.connects == ["cu.a"]
    harness.controller.handle_candidates((PortCandidate("cu.a"), PortCandidate("cu.b")))
    assert harness.state is AppState.MULTIPLE_DEVICES
    assert [c.port_name for c in harness.model.candidates] == ["cu.a", "cu.b"]
    assert not harness.controller.reconnect_timer.isActive()


def test_no_candidates_shows_no_device_and_keeps_polling(harness):
    harness.controller.handle_candidates(())
    assert harness.state is AppState.NO_DEVICE
    assert harness.controller.reconnect_timer.isActive()


def test_read_failure_remembers_port_until_it_is_unplugged(harness):
    harness.controller.prepare_connect("cu.a")
    harness.model = ScreenModel(AppState.CONNECTING)
    harness.controller.handle_progress("GET_CONFIG")
    assert harness.controller.reading_config
    harness.controller.handle_failure(BootstrapKind.READ_FAILED, "bad", "crc", firmware_was_busy=False)
    assert harness.state is AppState.READ_FAILED
    assert harness.model.message.startswith("USB 设备已连接，但配置未能读取")
    assert harness.model.technical_message == "bad\ncrc"
    assert harness.controller.failed_ports == {"cu.a"}
    assert harness.controller.reconnect_timer.isActive()

    harness.controller.handle_candidates((PortCandidate("cu.a"),))
    assert harness.connects == []
    harness.controller.handle_candidates(())
    assert harness.controller.failed_ports == set()
    harness.controller.handle_candidates((PortCandidate("cu.a"),))
    assert harness.connects == ["cu.a"]


def test_ble_failure_wording_and_failed_ble_port_survives_enumeration(harness):
    harness.controller.prepare_connect("ble:desk")
    harness.model = ScreenModel(AppState.CONNECTING)
    harness.controller.handle_failure(BootstrapKind.READ_FAILED, "x", "y", firmware_was_busy=False)
    assert harness.model.message.startswith("蓝牙连接失败")
    harness.controller.forget_vanished_failed_ports(())
    assert harness.controller.failed_ports == {"ble:desk"}


@pytest.mark.parametrize("kind,state,reconnects", [
    (BootstrapKind.FIRMWARE_UPDATE_REQUIRED, AppState.FIRMWARE_UPDATE_REQUIRED, False),
    (BootstrapKind.AUTHENTICITY_FAILED, AppState.AUTHENTICITY_FAILED, False),
    (BootstrapKind.INCOMPATIBLE, AppState.INCOMPATIBLE, False),
    (BootstrapKind.AUTHENTICATION_INTERRUPTED, AppState.DISCONNECTED, True),
])
def test_failure_kinds_map_to_screens_and_reconnect_policy(harness, kind, state, reconnects):
    harness.controller.handle_failure(kind, "m", "t", firmware_was_busy=False)
    assert harness.state is state
    assert harness.controller.reconnect_timer.isActive() is reconnects


def test_discovery_interrupted_waits_at_longest_backoff(harness):
    harness.controller.handle_failure(BootstrapKind.DISCOVERY_INTERRUPTED, "m", "t", firmware_was_busy=False)
    assert harness.state is AppState.DISCONNECTED
    assert harness.model.message == "蓝牙连接检查暂未完成，正在自动重试。"
    assert harness.controller.reconnect_timer.interval() == 5000
    assert harness.controller.full_scans == len(ConnectionController.RECONNECT_FULL_SCAN_DELAYS_MS) - 1


def test_firmware_failure_keeps_reconnecting_even_when_incompatible(harness):
    harness.controller.handle_failure(BootstrapKind.INCOMPATIBLE, "m", "t", firmware_was_busy=True)
    assert harness.controller.reconnect_timer.isActive()


def test_disconnect_backoff_spaces_full_scans_and_probes_usb_between(harness):
    harness.model = ScreenModel(AppState.DISCONNECTED)
    harness.controller.handle_disconnected()
    assert harness.gateway.calls == [("scan", False)]
    calls = []
    for _ in range(4):
        harness.gateway.calls.clear()
        harness.clock[0] += 0.6
        harness.controller.scan_for_reconnect()
        calls.append(harness.gateway.calls[0])
    assert calls == [("scan", False), ("scan", True), ("scan", False), ("scan", True)]
    assert harness.controller.reconnect_timer.interval() == 1000


def test_read_failed_reconnect_only_probes_usb_unless_recovering(harness):
    harness.model = ScreenModel(AppState.READ_FAILED)
    harness.controller.scan_for_reconnect()
    assert harness.gateway.calls == [("scan", True)]
    harness.gateway.calls.clear()
    harness.firmware_busy = True
    harness.controller.scan_for_reconnect()
    assert harness.gateway.calls == [("scan", True)]


def test_empty_scan_during_firmware_update_waits_for_usb(harness):
    harness.firmware_busy = True
    harness.controller.handle_candidates(())
    assert harness.state is AppState.DISCONNECTED
    assert "固件维护设备" in harness.model.message
    assert harness.controller.reconnect_timer.isActive()


def test_usb_handoff_only_for_same_serial_when_not_blocked(qtbot, contract):
    harness = Harness()
    snapshot = replace(_power_v2_snapshot(contract, read_only=False), port_name="ble:current")
    snapshot = replace(snapshot, trust=replace(snapshot.trust, state=DeviceTrustState.AUTHENTICATED))
    harness._show(ScreenModel(AppState.READY, snapshot=snapshot))
    assert harness.controller.usb_probe_timer.isActive()
    harness.controller.probe_usb_handoff()
    serial = snapshot.identity["serial"]
    assert harness.gateway.calls == [("probe_usb", serial)]
    harness.controller.handle_usb_candidate(PortCandidate("cu.other", serial_number="OTHER"))
    harness.blocked = True
    harness.controller.handle_usb_candidate(PortCandidate("cu.same", serial_number=serial))
    assert harness.connects == []
    harness.blocked = False
    harness.controller.handle_usb_candidate(PortCandidate("cu.same", serial_number=serial))
    assert harness.connects == ["cu.same"]
    harness.controller.shutdown()


def test_adopt_records_session_and_reserves_storage_degraded_flag(qtbot, contract):
    harness = Harness()
    snapshot = _power_v2_snapshot(contract, read_only=False)
    harness.controller.failed_ports.add(snapshot.port_name)
    harness.controller.reconnect_timer.start()
    harness.controller.adopt(snapshot, remember_name=True)
    assert harness.controller.port == snapshot.port_name
    assert harness.controller.failed_ports == set()
    assert not harness.controller.reconnect_timer.isActive()
    assert harness.controller.storage_degraded is False
    degraded = replace(snapshot, compatibility={**snapshot.compatibility, "reason": CONFIG_STORAGE_UNAVAILABLE})
    harness.controller.adopt(degraded, remember_name=True)
    assert harness.controller.storage_degraded is True
    assert harness.storage == [True]
    harness.controller.shutdown()


def test_known_ble_name_supplies_serial_for_candidates(harness):
    harness.controller.remember_device_name("ble:desk", "SERIAL-1", "Desk")
    harness.controller.handle_candidates((PortCandidate("ble:desk", transport="bluetooth"),
                                          PortCandidate("cu.a")))
    assert harness.model.candidates[0].serial_number == "SERIAL-1"


def test_connection_changed_only_when_the_port_changes(harness):
    seen: list[str] = []
    harness.controller.connection_changed.connect(seen.append)
    harness.controller.prepare_connect("ble:E072A1D68DE8")
    harness.controller.prepare_connect("ble:E072A1D68DE8")
    harness.controller.prepare_connect("/dev/cu.usbmodem101")  # USB plugged in.
    assert seen == ["ble:E072A1D68DE8", "/dev/cu.usbmodem101"]
