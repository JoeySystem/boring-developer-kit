from __future__ import annotations

import copy
import json
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from controller_config.connect_timing import TimingMark, timing_report, timing_summary
from controller_config.models import DeviceSnapshot, ScreenModel


Clock = Callable[[], datetime]


class DiagnosticCapturePhase(str, Enum):
    IDLE = ""
    STARTING = "starting"
    WAITING_READY = "waiting_ready"
    AWAITING_ACTIVE = "awaiting_active"
    STOPPING = "stopping"


@dataclass(frozen=True)
class DiagnosticReadiness:
    state: str
    reason: str
    control_id: str = ""
    activation_failed: bool = False

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True)
class DiagnosticEvent:
    sequence: int
    timestamp: str
    category: str
    detail: str

    @property
    def line(self) -> str:
        return f"[{self.timestamp}] {self.category}  {self.detail}"


class DiagnosticsSession:
    """Keeps a bounded host-side record of protocol-visible device changes."""

    def __init__(self, *, clock: Clock | None = None, max_events: int = 200) -> None:
        if max_events < 1:
            raise ValueError("max_events 必须大于 0")
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._events: deque[DiagnosticEvent] = deque(maxlen=max_events)
        self._sequence = 0
        self._serial = ""
        self._connected = False
        self._tested_controls: set[str] = set()
        self._capture_event_sequence = 0
        self._connect_timing: tuple[TimingMark, ...] = ()

    @property
    def events(self) -> tuple[DiagnosticEvent, ...]:
        return tuple(self._events)

    @property
    def tested_controls(self) -> frozenset[str]:
        return frozenset(self._tested_controls)

    def reset_control_check(self) -> None:
        self._tested_controls.clear()
        self._capture_event_sequence = 0

    def bind_snapshot(self, snapshot: DeviceSnapshot) -> None:
        serial = str(snapshot.identity.get("serial", ""))
        if serial != self._serial:
            self._events.clear()
            self.reset_control_check()
            self._serial = serial
            self._sequence = 0
        elif not self._connected:
            self.reset_control_check()
        state = "connected" if not self._connected else "refreshed"
        self._connected = True
        self._append(
            "CONNECTION",
            f"{state} serial={serial or 'unknown'} port={snapshot.port_name or 'unknown'}",
        )

    def observe_status(
        self,
        previous: dict,
        current: dict,
        *,
        capture_start_confirmed: bool = False,
    ) -> None:
        previous_controls = _active_controls(previous)
        current_controls = _active_controls(current)
        if previous_controls != current_controls:
            value = ",".join(current_controls) if current_controls else "none"
            self._append("INPUT", f"active_controls={value}")

        for field in ("state", "operating_mode", "platform", "inputs_neutral"):
            if previous.get(field) != current.get(field):
                self._append("STATUS", f"{field}={_technical_value(current.get(field))}")

        previous_action = previous.get("action_engine")
        current_action = current.get("action_engine")
        if isinstance(current_action, dict) and current_action != previous_action:
            detail = " ".join(
                f"{key}={_technical_value(current_action.get(key))}"
                for key in ("host_output_state", "local_page", "quick_config_active")
                if key in current_action
            )
            if detail:
                self._append("ACTION", detail)

        previous_ble = _ble_summary(previous.get("codex_micro"))
        current_ble = _ble_summary(current.get("codex_micro"))
        if previous_ble != current_ble and current_ble:
            self._append("TRANSPORT", current_ble)

        previous_capture = _diagnostic_capture(previous)
        current_capture = _diagnostic_capture(current)
        previous_owned = diagnostic_capture_owned(previous)
        current_owned = diagnostic_capture_owned(current)
        if capture_start_confirmed:
            previous_sequence = self._capture_event_sequence
            self.reset_control_check()
            confirmed_sequence = current_capture.get("event_sequence")
            if (
                isinstance(confirmed_sequence, int)
                and not isinstance(confirmed_sequence, bool)
                and confirmed_sequence == previous_sequence
            ):
                # The firmware keeps the capture sequence across lease renewals.
                # When the confirming snapshot repeats the last observed event,
                # use it as the new baseline instead of re-counting old input.
                self._capture_event_sequence = confirmed_sequence
        if current_owned and not previous_owned:
            self._append("CAPTURE", "diagnostic input capture started")
        event_sequence = current_capture.get("event_sequence")
        control_id = current_capture.get("last_control")
        if (
            current_owned
            and isinstance(event_sequence, int)
            and not isinstance(event_sequence, bool)
            and event_sequence != self._capture_event_sequence
            and isinstance(control_id, str)
            and control_id
        ):
            self._capture_event_sequence = event_sequence
            self._tested_controls.add(control_id)
            pressed = current_capture.get("last_pressed")
            self._append(
                "CAPTURE",
                f"control={control_id} pressed={_technical_value(pressed)}",
            )
        if previous_owned and not current_owned:
            self._append("CAPTURE", "diagnostic input capture stopped")

    def record_connection_loss(self, detail: str) -> None:
        if not self._connected:
            return
        self._connected = False
        normalized = " ".join(detail.split()) or "unknown"
        self._append("CONNECTION", f"disconnected reason={normalized}")

    def clear(self) -> None:
        self._events.clear()

    def record_link_warning(self, category: str, detail: str) -> None:
        """Recoverable transport events: skipped frames, failed status polls."""
        normalized = " ".join(str(detail).split()) or "unknown"
        self._append(str(category) or "LINK", normalized)

    def record_connect_timing(self, marks: tuple[TimingMark, ...]) -> None:
        """SH-005: how long the last successful connection took, per step."""
        self._connect_timing = tuple(marks)
        summary = timing_summary(self._connect_timing)
        if summary:
            self._append("CONNECT_TIMING", summary)

    @property
    def connect_timing(self) -> tuple[TimingMark, ...]:
        return self._connect_timing

    def record_host_focus_error(self, message: str) -> None:
        self._append("CHATGPT_FOCUS", message)

    def report(self, model: ScreenModel) -> dict[str, object]:
        snapshot = model.snapshot
        report: dict[str, object] = {
            "format": "boring-diagnostics-v1",
            "generated_at": self._timestamp(),
            "application": {
                "state": model.state.value,
                "message": model.message,
                "technical_message": model.technical_message,
            },
            "device": None,
            "configuration": None,
            "runtime": None,
            "capabilities": None,
            "events": [asdict(event) for event in self._events],
            "connection_timing": timing_report(self._connect_timing) or None,
            "unavailable_fields": ["battery", "charging", "device_logs"],
        }
        if snapshot is None:
            return report

        if snapshot.battery_percent is not None:
            report["unavailable_fields"].remove("battery")
        if type(snapshot.status.get("is_charging")) is bool:
            report["unavailable_fields"].remove("charging")

        report["device"] = {
            "identity": copy.deepcopy(snapshot.identity),
            "versions": copy.deepcopy(snapshot.versions),
            "compatibility": copy.deepcopy(snapshot.compatibility),
            "port_name": snapshot.port_name,
        }
        report["configuration"] = {
            "status": snapshot.config_status_label,
            "generation": snapshot.config_result.get("generation"),
            "digest": snapshot.config_result.get("digest"),
            "active_profile": snapshot.active_profile_id,
            "active_profile_name": snapshot.profile_name,
        }
        report["runtime"] = {
            key: copy.deepcopy(snapshot.status.get(key))
            for key in (
                "build_id",
                "state",
                "activation_failed",
                "active",
                "pending",
                "inputs_neutral",
                "platform",
                "operating_mode",
                "battery",
                "battery_valid",
                "is_charging",
                "active_controls",
                "action_engine",
                "diagnostic_capture",
                "joystick_diagnostics",
                "codex_micro",
                "firmware_update",
            )
            if key in snapshot.status
        }
        features = snapshot.capabilities.get("features")
        limits = snapshot.capabilities.get("limits")
        report["capabilities"] = {
            "features": copy.deepcopy(features) if isinstance(features, dict) else None,
            "limits": copy.deepcopy(limits) if isinstance(limits, dict) else None,
            "controls": list(snapshot.controls),
        }
        return report

    def report_text(self, model: ScreenModel) -> str:
        return json.dumps(
            self.report(model),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    def export(self, path: Path, model: ScreenModel) -> None:
        path.write_text(self.report_text(model), encoding="utf-8")

    def _append(self, category: str, detail: str) -> None:
        self._sequence += 1
        self._events.append(
            DiagnosticEvent(
                sequence=self._sequence,
                timestamp=self._timestamp(),
                category=category,
                detail=detail,
            )
        )

    def _timestamp(self) -> str:
        return self._clock().astimezone().isoformat(timespec="seconds")


def active_controls(status: dict) -> tuple[str, ...]:
    return _active_controls(status)


def joystick_diagnostics(status: dict) -> dict[str, object] | None:
    value = status.get("joystick_diagnostics")
    return dict(value) if isinstance(value, dict) else None


def diagnostic_capture(status: dict) -> dict[str, object]:
    return _diagnostic_capture(status)


def diagnostic_capture_owned(status: dict) -> bool:
    action_engine = status.get("action_engine")
    return (
        _diagnostic_capture(status).get("active") is True
        and isinstance(action_engine, dict)
        and action_engine.get("host_output_state") == "diagnostic_capture"
    )


def diagnostic_readiness(
    status: dict, *, calibration_active: bool = False
) -> DiagnosticReadiness:
    """Describe whether diagnostic input capture can start from the current status."""

    activation_failed = status.get("activation_failed") is True
    controls_value = status.get("active_controls")
    inputs_neutral = status.get("inputs_neutral")
    if isinstance(controls_value, list):
        controls = _active_controls(status)
        if controls:
            return DiagnosticReadiness(
                "waiting",
                "active_control",
                controls[0],
                activation_failed,
            )
    if inputs_neutral is False:
        return DiagnosticReadiness(
            "waiting", "active_control", activation_failed=activation_failed
        )

    action_engine = status.get("action_engine")
    if not isinstance(action_engine, dict):
        return DiagnosticReadiness(
            "waiting", "confirming_status", activation_failed=activation_failed
        )

    host_output_state = action_engine.get("host_output_state")
    if host_output_state == "wait_neutral":
        return DiagnosticReadiness(
            "waiting", "wait_neutral", activation_failed=activation_failed
        )
    if (
        action_engine.get("idle_standby_active") is True
        or action_engine.get("usb_standby_active") is True
    ):
        return DiagnosticReadiness(
            "waiting", "standby", activation_failed=activation_failed
        )
    local_page = action_engine.get("local_page")
    if (
        isinstance(local_page, str)
        and local_page != "none"
        or action_engine.get("quick_config_active") is True
    ):
        return DiagnosticReadiness(
            "action_required", "local_page", activation_failed=activation_failed
        )
    if "pending" in status and status.get("pending") is not None:
        return DiagnosticReadiness(
            "waiting", "config_pending", activation_failed=activation_failed
        )
    if calibration_active:
        return DiagnosticReadiness(
            "action_required",
            "console_calibration",
            activation_failed=activation_failed,
        )
    if _diagnostic_capture(status).get("active") is True and not diagnostic_capture_owned(
        status
    ):
        return DiagnosticReadiness(
            "waiting", "confirming_status", activation_failed=activation_failed
        )

    complete = (
        isinstance(controls_value, list)
        and type(inputs_neutral) is bool
        and isinstance(host_output_state, str)
        and isinstance(local_page, str)
        and type(action_engine.get("quick_config_active")) is bool
        and type(action_engine.get("idle_standby_active")) is bool
        and type(action_engine.get("usb_standby_active")) is bool
        and "pending" in status
    )
    if not complete or host_output_state != "enabled":
        return DiagnosticReadiness(
            "waiting", "confirming_status", activation_failed=activation_failed
        )
    return DiagnosticReadiness(
        "ready", "ready", activation_failed=activation_failed
    )


def joystick_direction_label(mask: object) -> str:
    if not isinstance(mask, int) or isinstance(mask, bool):
        return "—"
    directions = tuple(
        label
        for bit, label in ((4, "UP"), (2, "RIGHT"), (8, "DOWN"), (1, "LEFT"))
        if mask & bit
    )
    return " + ".join(directions) if directions else "CENTER"


def _active_controls(status: dict) -> tuple[str, ...]:
    value = status.get("active_controls")
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _diagnostic_capture(status: dict) -> dict[str, object]:
    value = status.get("diagnostic_capture")
    return dict(value) if isinstance(value, dict) else {}


def _ble_summary(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for key in ("usb_connected", "ble_connected", "active_slot"):
        if key in value:
            parts.append(f"{key}={_technical_value(value.get(key))}")
    return " ".join(parts)


def _technical_value(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    return str(value)
