from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from controller_config.protocol.contract import Contract
from controller_config.protocol.device_auth import DeviceAuthenticator
from controller_config.protocol.bootstrap import Command
from controller_config.transport.ble import BleGateway
from controller_config.transport.qt_serial import SerialGateway


class DeviceGateway(QObject):
    """Prefer USB CDC and fall back to authenticated BLE configuration."""

    agent_status_completed = Signal(object, object)
    agent_status_failed = Signal(object, object)
    candidates_found = Signal(object)
    usb_candidate_found = Signal(object)
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
        parent: QObject | None = None,
        *,
        usb_gateway: QObject | None = None,
        ble_gateway: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._usb = usb_gateway or SerialGateway(contract, authenticator, self)
        self._ble = ble_gateway or BleGateway(contract, authenticator, self)
        self._active = ""
        self._scan_waiting_for_ble = False
        self._scan_waiting_for_usb = False
        self._ble_scan_active = False
        self._pending_ble_candidates: tuple[object, ...] | None = None
        self._reconnect_preferred_port = ""
        self._usb_probe_serial = ""

        self._usb.candidates_found.connect(self._on_usb_candidates)
        self._ble.candidates_found.connect(self._on_ble_candidates)
        self._usb.progress.connect(lambda text: self._forward_progress("usb", text))
        self._ble.progress.connect(lambda text: self._forward_progress("ble", text))
        for source_name, source in (("usb", self._usb), ("ble", self._ble)):
            source.snapshot_ready.connect(
                lambda value, name=source_name: self._forward("snapshot_ready", name, value)
            )
            source.status_updated.connect(
                lambda value, name=source_name: self._forward("status_updated", name, value)
            )
            source.command_completed.connect(
                lambda command, value, name=source_name: self._forward(
                    "command_completed", name, command, value
                )
            )
            source.command_failed.connect(
                lambda command, value, name=source_name: self._forward(
                    "command_failed", name, command, value
                )
            )
            source.agent_status_completed.connect(
                lambda command, value, name=source_name: self._forward(
                    "agent_status_completed", name, command, value
                )
            )
            source.agent_status_failed.connect(
                lambda command, value, name=source_name: self._forward(
                    "agent_status_failed", name, command, value
                )
            )
            source.failure.connect(
                lambda kind, message, detail, name=source_name: self._forward(
                    "failure", name, kind, message, detail
                )
            )
            source.disconnected.connect(
                lambda detail, name=source_name: self._forward(
                    "disconnected", name, detail
                )
            )

    def scan(self, *, usb_only: bool = False) -> None:
        if self._usb_probe_serial:
            # Reuse an in-flight enumeration for an explicit refresh instead of
            # dropping its result as a background probe while the UI is scanning.
            self._usb_probe_serial = ""
            self._active = ""
            self._scan_waiting_for_ble = not usb_only
            self._reconnect_preferred_port = ""
            return
        # The reconnect timer ticks faster than BLE discovery can finish.
        # Keep its candidate/device-info handoff intact until both sources finish.
        if self._scan_waiting_for_usb or self._ble_scan_active:
            return
        self._active = ""
        self._scan_waiting_for_ble = not usb_only
        self._scan_waiting_for_usb = True
        self._ble_scan_active = False
        self._pending_ble_candidates = None
        self._reconnect_preferred_port = ""
        if usb_only:
            self._ble.stop_scan()
        # USB recovery must not depend on macOS Bluetooth initialization.
        # Discover BLE only after USB has reported no candidates.
        self._usb.scan()

    def scan_reconnect(self, preferred_port: str, *, usb_only: bool = False) -> None:
        """Probe USB promptly while retaining a targeted BLE reconnect scan."""
        if self._usb_probe_serial:
            self.scan(usb_only=usb_only)
            self._reconnect_preferred_port = preferred_port
            return
        if self._scan_waiting_for_usb:
            return
        self._reconnect_preferred_port = preferred_port
        if usb_only:
            self._scan_waiting_for_ble = False
            self._ble_scan_active = False
            self._pending_ble_candidates = None
            self._ble.stop_scan()
        elif not self._scan_waiting_for_ble:
            self._active = ""
            self._scan_waiting_for_ble = True
            self._pending_ble_candidates = None
        self._scan_waiting_for_usb = True
        self._usb.scan()

    def probe_usb(self, serial: str) -> None:
        """Enumerate USB without disturbing the working BLE configuration link."""
        if (not serial or self._active != "ble" or self._scan_waiting_for_usb
                or self._ble_scan_active):
            return
        self._usb_probe_serial = serial
        self._scan_waiting_for_usb = True
        self._usb.scan()

    def connect_port(self, port_name: str) -> None:
        self._active = "ble" if port_name.startswith("ble:") else "usb"
        if self._active == "usb":
            # stop_scan only cancels discovery; it does not close a live BLE link.
            self._ble.shutdown()
        inactive = self._usb if self._active == "ble" else self._ble
        if hasattr(inactive, "stop_scan"):
            inactive.stop_scan()
        gateway = self._ble if self._active == "ble" else self._usb
        gateway.connect_port(port_name)

    def execute_command(self, command: Command) -> None:
        self._active_gateway().execute_command(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self._usb.set_status_poll_interval(interval_ms)
        self._ble.set_status_poll_interval(interval_ms)

    def shutdown(self) -> None:
        self._ble.shutdown()
        self._usb.shutdown()

    def _active_gateway(self) -> QObject:
        return self._ble if self._active == "ble" else self._usb

    def _on_usb_candidates(self, candidates: object) -> None:
        if self._usb_probe_serial:
            serial, self._usb_probe_serial = self._usb_probe_serial, ""
            self._scan_waiting_for_usb = False
            if self._active == "ble":
                matches = tuple(c for c in candidates if c.serial_number == serial)
                if len(matches) == 1:
                    self.usb_candidate_found.emit(matches[0])
            return
        values = self._preferred_candidates(tuple(candidates))
        self._scan_waiting_for_usb = False
        if values:
            self._scan_waiting_for_ble = False
            self._ble_scan_active = False
            self._ble.stop_scan()
            self.candidates_found.emit(values)
            return
        if self._pending_ble_candidates is not None:
            self._scan_waiting_for_ble = False
            self._ble_scan_active = False
            pending = self._preferred_candidates(self._pending_ble_candidates)
            self._pending_ble_candidates = None
            self.candidates_found.emit(pending)
            return
        if self._scan_waiting_for_ble:
            if not self._ble_scan_active:
                self.progress.emit("未发现 USB，正在读取本机已连接的蓝牙设备…")
                self._ble_scan_active = True
                self._ble.scan(preferred_port=self._reconnect_preferred_port or None)
        else:
            self.candidates_found.emit(())

    def _on_ble_candidates(self, candidates: object) -> None:
        if not self._scan_waiting_for_ble:
            return
        values = self._preferred_candidates(tuple(candidates))
        self._ble_scan_active = False
        if self._scan_waiting_for_usb:
            self._pending_ble_candidates = values
            return
        self._scan_waiting_for_ble = False
        self.candidates_found.emit(values)

    def _preferred_candidates(self, candidates: tuple[object, ...]) -> tuple[object, ...]:
        preferred_port = self._reconnect_preferred_port
        if not preferred_port:
            return candidates
        preferred = tuple(
            candidate for candidate in candidates
            if getattr(candidate, "port_name", "") == preferred_port
        )
        return preferred or candidates

    def _forward_progress(self, source: str, message: str) -> None:
        if (self._active == source or (not self._active and source == "usb")
                or (source == "ble" and self._scan_waiting_for_ble)):
            self.progress.emit(message)

    def _forward(self, signal_name: str, source: str, *args: object) -> None:
        if signal_name == "failure" and source == "ble" and self._scan_waiting_for_ble:
            self._scan_waiting_for_ble = False
            self._ble_scan_active = False
            self._pending_ble_candidates = None
            self.failure.emit(*args)
            return
        if source == self._active:
            getattr(self, signal_name).emit(*args)
