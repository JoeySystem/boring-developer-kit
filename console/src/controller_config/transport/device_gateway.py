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
        self._pending_ble_candidates: tuple[object, ...] | None = None

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
        # The reconnect timer ticks faster than BLE discovery can finish.
        # Keep its candidate/device-info handoff intact until both sources finish.
        if self._scan_waiting_for_usb or (self._scan_waiting_for_ble and not usb_only):
            return
        self._active = ""
        self._scan_waiting_for_ble = not usb_only
        self._scan_waiting_for_usb = True
        self._pending_ble_candidates = None
        if usb_only:
            self._ble.stop_scan()
        # USB recovery must not depend on macOS Bluetooth initialization.
        # Discover BLE only after USB has reported no candidates.
        self._usb.scan()

    def connect_port(self, port_name: str) -> None:
        self._active = "ble" if port_name.startswith("ble:") else "usb"
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
        values = tuple(candidates)
        self._scan_waiting_for_usb = False
        if values:
            self._scan_waiting_for_ble = False
            self._ble.stop_scan()
            self.candidates_found.emit(values)
            return
        if self._pending_ble_candidates is not None:
            self._scan_waiting_for_ble = False
            self.candidates_found.emit(self._pending_ble_candidates)
            return
        if self._scan_waiting_for_ble:
            self.progress.emit("未发现 USB，正在查找已配对的 BORING 蓝牙设备…")
            self._ble.scan()
        else:
            self.candidates_found.emit(())

    def _on_ble_candidates(self, candidates: object) -> None:
        if not self._scan_waiting_for_ble:
            return
        values = tuple(candidates)
        if self._scan_waiting_for_usb:
            self._pending_ble_candidates = values
            return
        self._scan_waiting_for_ble = False
        self.candidates_found.emit(values)

    def _forward_progress(self, source: str, message: str) -> None:
        if self._active == source or (not self._active and source == "usb"):
            self.progress.emit(message)

    def _forward(self, signal_name: str, source: str, *args: object) -> None:
        if source == self._active:
            getattr(self, signal_name).emit(*args)
