"""Device connection state machine, split out of MainViewModel (A-15 round 2).

The controller owns scanning, connecting and reconnecting: the A-02 failed
port memory, the A-07 reconnect backoff, the BLE -> USB handoff probe, and
the mapping of bootstrap failures (READ_FAILED, maintenance, authenticity...)
to what the console shows. It calls only the gateway's connection API
(scan, scan_reconnect, connect_port, probe_usb). What it decides leaves as
signals; MainViewModel applies the screen and tears down the other device
sessions before opening a port.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
import time
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.connect_timing import TimingMark
from controller_config.models import AppState, DeviceSnapshot, PortCandidate, ScreenModel
from controller_config.protocol.bootstrap import BootstrapKind


# J-03 / design decision: HELLO compatibility.reason when config storage is degraded.
CONFIG_STORAGE_UNAVAILABLE = "config_storage_unavailable"


def full_scan_delays_ms(platform: str) -> tuple[int, ...]:
    """Idle backoff between full (USB + Bluetooth) reconnect scans.

    A-07 capped it at 5 s because every scan created a new CBCentralManager.
    The macOS finder now reuses one shared manager and only reads the system's
    connected list, so idle Macs look for a Bluetooth link on every 1 s
    reconnect tick (SH-005); a cap between ticks would round up to 2 s.
    Windows starts a PowerShell WinRT query per scan, so it keeps 5 s.
    """
    if platform == "darwin":
        return (500, 1000)
    return (500, 1000, 2000, 5000)


@dataclass(frozen=True)
class ConnectionContext:
    """What the connection controller reads from the rest of the console."""

    model: ScreenModel
    # An OTA restart keeps a fast, USB-only reconnect.
    firmware_busy: bool
    # A calibration result still to be read back keeps a fast reconnect.
    calibration_unknown: bool


class ConnectionController(QObject):
    # ScreenModel MainViewModel should show (it stays the only writer).
    screen_requested = Signal(object)
    # Open this port: MainViewModel resets the per-device sessions first.
    connect_requested = Signal(str)
    # J-03 seam: the adopted session reported degraded config storage.
    # Nothing consumes it yet, so the console still follows compatibility.write.
    storage_degraded_changed = Signal(bool)
    # The port the console talks over changed (plugging USB suspends BLE,
    # unplugging returns to BLE, another device was picked). Sessions bound
    # to one connection (the desktop Agent hub) must drop it and wait for the
    # next authenticated snapshot. Emitted only by prepare_connect and adopt,
    # the two places the port changes.
    connection_changed = Signal(str)

    RECONNECT_FULL_SCAN_DELAYS_MS = full_scan_delays_ms(sys.platform)
    RECONNECT_USB_PROBE_MS = 1000
    FULL_SCAN_SLACK_S = 0.1

    def __init__(
        self,
        gateway,
        context: Callable[[], ConnectionContext],
        handoff_blocked: Callable[[], bool],
        parent: QObject | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self._context = context
        self._handoff_blocked = handoff_blocked
        self.port = ""
        self.reading_config = False
        self.failed_ports: set[str] = set()
        self._known_device_names: dict[str, tuple[str, str]] = {}
        self._storage_degraded = False
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setInterval(500)
        self.reconnect_timer.timeout.connect(self.scan_for_reconnect)
        # A-07: idle reconnect backs off full (Bluetooth-inclusive) scans while
        # cheap USB enumeration keeps cable insertion responsive.
        self.monotonic = clock
        self.full_scans = 0
        self._next_full_scan = 0.0
        self._usb_only_scan_supported: bool | None = None
        self.usb_probe_timer = QTimer(self)
        self.usb_probe_timer.setInterval(2000)
        self.usb_probe_timer.timeout.connect(self.probe_usb_handoff)
        # SH-005 connection timing: when the scan that is still waiting for
        # results began, and the scan + pick marks of the port being opened.
        self._scan_mark: TimingMark | None = None
        self._discovery_marks: tuple[str, tuple[TimingMark, ...]] = ("", ())

    @property
    def storage_degraded(self) -> bool:
        return self._storage_degraded

    # ---- commands from MainViewModel -------------------------------------

    def reset(self) -> None:
        """Manual rescan: forget failed ports and restart the backoff."""
        self.failed_ports.clear()
        self.reconnect_timer.stop()
        self.reset_backoff()

    def _mark_scan_start(self) -> None:
        # A scan request while another is still in flight is dropped by the
        # gateway, so the first unanswered request marks when scanning began.
        if self._scan_mark is None:
            self._scan_mark = TimingMark("scan_start", time.monotonic())

    def take_discovery_timing(self, port_name: str) -> tuple[TimingMark, ...]:
        """The scan and pick marks for ``port_name``, once."""
        port, marks = self._discovery_marks
        self._discovery_marks = ("", ())
        return marks if port == port_name else ()

    def scan(self, *, usb_only: bool) -> None:
        self._mark_scan_start()
        if usb_only:
            self._gateway.scan(usb_only=True)
        else:
            self._gateway.scan()

    def _set_port(self, port_name: str) -> None:
        changed = port_name != self.port
        self.port = port_name
        if changed:
            self.connection_changed.emit(port_name)

    def prepare_connect(self, port_name: str) -> None:
        self._set_port(port_name)
        self.reading_config = False
        self.reconnect_timer.stop()
        self.reset_backoff()

    def open(self, port_name: str) -> None:
        self._gateway.connect_port(port_name)

    def adopt(self, snapshot: DeviceSnapshot, *, remember_name: bool) -> None:
        """A session (normal or maintenance-only) is established on this port."""
        self.reading_config = False
        self._set_port(snapshot.port_name)
        self.failed_ports.clear()
        if remember_name:
            self._known_device_names[snapshot.port_name] = (str(snapshot.identity["serial"]), "")
        self.reconnect_timer.stop()
        degraded = snapshot.compatibility.get("reason") == CONFIG_STORAGE_UNAVAILABLE
        if degraded != self._storage_degraded:
            self._storage_degraded = degraded
            self.storage_degraded_changed.emit(degraded)

    def remember_device_name(self, port_name: str, serial: str, name: str) -> None:
        self._known_device_names[port_name] = (serial, name)

    def stop_reconnect(self) -> None:
        self.reconnect_timer.stop()

    def sync_usb_probe(self, model: ScreenModel) -> None:
        if (model.state in {AppState.READY, AppState.READ_ONLY}
                and model.snapshot is not None
                and model.snapshot.connection_kind == "bluetooth"):
            if not self.usb_probe_timer.isActive():
                self.usb_probe_timer.start()
        else:
            self.usb_probe_timer.stop()

    def shutdown(self) -> None:
        self.usb_probe_timer.stop()
        self.reconnect_timer.stop()

    # ---- gateway events --------------------------------------------------

    def forget_vanished_failed_ports(self, candidates: tuple[PortCandidate, ...]) -> None:
        """A-02: a USB port that disappeared from enumeration was unplugged.
        Its read failure no longer applies, so a re-inserted device (which
        usually gets the same cu.usbmodem…/COM name) connects automatically.
        A port that stays present keeps its failure and is not retried."""

        present = {candidate.port_name for candidate in candidates}
        self.failed_ports = {
            port for port in self.failed_ports
            if port.startswith("ble:") or port in present
        }

    def handle_candidates(self, candidates: tuple[PortCandidate, ...]) -> None:
        scan_mark, self._scan_mark = self._scan_mark, None
        self.forget_vanished_failed_ports(candidates)
        context = self._context()
        model = context.model
        if model.state is AppState.READ_FAILED and not (
            context.firmware_busy or context.calibration_unknown
        ):
            # A failed BLE link must not prevent a newly inserted USB cable from
            # working, or silently retry the same failed connection every tick.
            candidates = tuple(candidate for candidate in candidates
                               if candidate.transport == "usb"
                               and candidate.port_name not in self.failed_ports)
            if not candidates:
                return
        candidates = tuple(
            replace(candidate, serial_number=self._known_device_names[candidate.port_name][0])
            if candidate.transport == "bluetooth" and candidate.port_name in self._known_device_names else candidate
            for candidate in candidates
        )
        if not candidates and context.firmware_busy:
            self.screen_requested.emit(ScreenModel(
                AppState.DISCONNECTED,
                "等待固件维护设备重新连接 USB…",
                snapshot=model.snapshot,
            ))
            self.reconnect_timer.start()
            return
        if model.state is AppState.DISCONNECTED and not candidates:
            return
        if not candidates:
            self.screen_requested.emit(
                ScreenModel(
                    AppState.NO_DEVICE,
                    "尚未连接 BORING 设备",
                    "请插入 USB，或先在系统蓝牙设置中连接设备。",
                    snapshot=model.snapshot,
                )
            )
            if not self.reconnect_timer.isActive():
                self.reconnect_timer.start()
            return
        self.reset_backoff()
        if len(candidates) > 1:
            self.reconnect_timer.stop()
            self.screen_requested.emit(
                ScreenModel(
                    AppState.MULTIPLE_DEVICES,
                    "此电脑连接了多台 BORING 设备",
                    "请选择要管理的设备。",
                    candidates=candidates,
                    snapshot=model.snapshot,
                )
            )
            return
        port_name = candidates[0].port_name
        found = TimingMark("device_found", time.monotonic(), candidates[0].transport)
        self._discovery_marks = (
            port_name, (scan_mark, found) if scan_mark is not None else (found,))
        self.connect_requested.emit(port_name)

    def handle_progress(self, message: str) -> None:
        model = self._context().model
        if model.state in {AppState.SCANNING, AppState.CONNECTING}:
            if "GET_CONFIG" in message:
                self.reading_config = True
            self.screen_requested.emit(
                ScreenModel(
                    model.state,
                    message,
                    candidates=model.candidates,
                    snapshot=model.snapshot,
                )
            )

    def handle_failure(
        self,
        kind: BootstrapKind,
        message: str,
        technical: str,
        *,
        firmware_was_busy: bool,
    ) -> None:
        """Show the failure and choose the reconnect policy. MainViewModel has
        already stopped the reconnect timer and wound down the transactions."""
        model = self._context().model
        if kind is BootstrapKind.FIRMWARE_UPDATE_REQUIRED:
            state = AppState.FIRMWARE_UPDATE_REQUIRED
        elif kind in {BootstrapKind.AUTHENTICATION_INTERRUPTED, BootstrapKind.DISCOVERY_INTERRUPTED}:
            state = AppState.DISCONNECTED
            if kind is BootstrapKind.DISCOVERY_INTERRUPTED:
                message = "蓝牙连接检查暂未完成，正在自动重试。"
        elif kind is BootstrapKind.INCOMPATIBLE:
            state = AppState.INCOMPATIBLE
        elif kind is BootstrapKind.AUTHENTICITY_FAILED:
            state = AppState.AUTHENTICITY_FAILED
        else:
            state = AppState.READ_FAILED
            if self.port and model.state in {
                AppState.CONNECTING, AppState.READY, AppState.READ_ONLY,
            }:
                self.failed_ports.add(self.port)
            technical = "\n".join(part for part in (message, technical) if part)
            if self.port.startswith("ble:"):
                message = ("蓝牙设备已连接，但配置未能读取；请重新连接，或使用 USB 连接设备。"
                           if self.reading_config else "蓝牙连接失败；请重新连接，或使用 USB 连接设备。")
            elif self.port:
                message = ("USB 设备已连接，但配置未能读取；请重新连接，仍失败时可重新插拔 USB。"
                           if self.reading_config else "USB 连接失败；请重新连接，仍失败时可重新插拔 USB。")
            else:
                message = "配置读取失败；请重新连接设备。"
        self.reading_config = False
        self._scan_mark = None
        self.screen_requested.emit(ScreenModel(state, message, technical, snapshot=model.snapshot))
        if (kind in {BootstrapKind.AUTHENTICATION_INTERRUPTED, BootstrapKind.DISCOVERY_INTERRUPTED}
                and not self.reconnect_timer.isActive()):
            # The failed worker closes after publishing this signal. Let the
            # timer start a fresh scan after that cleanup instead of re-entering
            # the same worker and canceling the replacement scan.
            if kind is BootstrapKind.DISCOVERY_INTERRUPTED:
                # Bluetooth discovery needs time to settle: stay at the
                # longest backoff step instead of restarting at 0.5 s.
                # The 5 s first tick below is the delay itself.
                self.full_scans = len(self.RECONNECT_FULL_SCAN_DELAYS_MS) - 1
                self._next_full_scan = 0.0
            self.reconnect_timer.start(5000 if kind is BootstrapKind.DISCOVERY_INTERRUPTED else 500)
        elif (kind not in {BootstrapKind.FIRMWARE_UPDATE_REQUIRED, BootstrapKind.AUTHENTICITY_FAILED}
                and (state is AppState.READ_FAILED or firmware_was_busy
                     or self._context().calibration_unknown)
                and not self.reconnect_timer.isActive()):
            self.reconnect_timer.start()

    def handle_disconnected(self) -> None:
        """Called after the DISCONNECTED screen is shown."""
        self.reset_backoff()
        if not self.reconnect_timer.isActive():
            self.reconnect_timer.start()
        self.scan_for_reconnect()

    # ---- reconnect -------------------------------------------------------

    def reset_backoff(self) -> None:
        self.full_scans = 0
        self._next_full_scan = 0.0
        self.reconnect_timer.setInterval(500)

    def _gateway_scans_usb_only(self) -> bool:
        """Whether the gateway offers a cheap USB-only enumeration. Gateways
        without it simply wait for the next (backed-off) full scan."""
        if self._usb_only_scan_supported is None:
            try:
                parameters = inspect.signature(self._gateway.scan).parameters
            except (TypeError, ValueError):
                parameters = {}
            self._usb_only_scan_supported = "usb_only" in parameters or any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        return self._usb_only_scan_supported

    def scan_for_reconnect(self) -> None:
        context = self._context()
        state = context.model.state
        # Firmware restart and calibration recovery keep the fast cadence.
        urgent = context.firmware_busy or context.calibration_unknown
        if state is AppState.READ_FAILED and not urgent:
            self.reconnect_timer.setInterval(self.RECONNECT_USB_PROBE_MS)
            self._mark_scan_start()
            self._gateway.scan(usb_only=True)
            return
        if state in {AppState.NO_DEVICE, AppState.DISCONNECTED} and not urgent:
            now = self.monotonic()
            # Qt's coarse timers may fire up to 5 % early; without the slack a
            # 1 s cap on a 1 s tick would skip every other full scan.
            if now < self._next_full_scan - self.FULL_SCAN_SLACK_S:
                if self._gateway_scans_usb_only():
                    self._mark_scan_start()
                    self._gateway.scan(usb_only=True)
                return
            delays = self.RECONNECT_FULL_SCAN_DELAYS_MS
            delay = delays[min(self.full_scans, len(delays) - 1)]
            self.full_scans += 1
            self._next_full_scan = now + delay / 1000
            self.reconnect_timer.setInterval(min(delay, self.RECONNECT_USB_PROBE_MS))
        reconnectable = state in {AppState.NO_DEVICE, AppState.DISCONNECTED} or (
            state is AppState.READ_FAILED and urgent
        )
        if reconnectable:
            self._mark_scan_start()
            usb_only = context.firmware_busy
            preferred_port = str(getattr(context.model.snapshot, "port_name", "") or "")
            targeted_scan = getattr(self._gateway, "scan_reconnect", None)
            if preferred_port and callable(targeted_scan):
                targeted_scan(preferred_port, usb_only=usb_only)
            elif usb_only:
                self._gateway.scan(usb_only=True)
            else:
                self._gateway.scan()

    # ---- BLE -> USB handoff ----------------------------------------------

    def can_handoff_to_usb(self) -> bool:
        model = self._context().model
        snapshot = model.snapshot
        return (
            model.state in {AppState.READY, AppState.READ_ONLY}
            and snapshot is not None and snapshot.connection_kind == "bluetooth"
            and snapshot.trust.is_authenticated
            and not self._handoff_blocked()
        )

    def probe_usb_handoff(self) -> None:
        probe = getattr(self._gateway, "probe_usb", None)
        if callable(probe) and self.can_handoff_to_usb():
            probe(str(self._context().model.snapshot.identity["serial"]))

    def handle_usb_candidate(self, candidate: PortCandidate) -> None:
        # A write may have started while enumeration was in flight. Retry later.
        if (self.can_handoff_to_usb() and candidate.transport == "usb"
                and candidate.serial_number == self._context().model.snapshot.identity["serial"]):
            self.connect_requested.emit(candidate.port_name)
