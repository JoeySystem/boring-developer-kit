from __future__ import annotations

import json
from datetime import datetime, timezone

from controller_config.diagnostics import DiagnosticsSession, joystick_direction_label
from controller_config.models import AppState, ScreenModel
from controller_config.transport.demo import _power_v2_snapshot


def test_diagnostics_records_only_meaningful_protocol_changes(contract) -> None:
    now = datetime(2026, 8, 27, 10, 30, tzinfo=timezone.utc)
    session = DiagnosticsSession(clock=lambda: now)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    session.bind_snapshot(snapshot)

    updated = {
        **snapshot.status,
        "inputs_neutral": False,
        "active_controls": ["key.3", "joystick.up"],
        "joystick_diagnostics": {
            **snapshot.status["joystick_diagnostics"],
            "directions": 4,
        },
    }
    session.observe_status(snapshot.status, updated)

    assert [event.category for event in session.events] == [
        "CONNECTION",
        "INPUT",
        "STATUS",
    ]
    assert session.events[1].detail == "active_controls=key.3,joystick.up"
    assert session.events[2].detail == "inputs_neutral=false"

    session.observe_status(updated, dict(updated))
    assert len(session.events) == 3


def test_diagnostic_report_exports_protocol_data_without_full_configuration(
    contract, tmp_path
) -> None:
    session = DiagnosticsSession(
        clock=lambda: datetime(2026, 8, 27, 10, 30, tzinfo=timezone.utc)
    )
    snapshot = _power_v2_snapshot(contract, read_only=False)
    session.bind_snapshot(snapshot)
    model = ScreenModel(AppState.READY, snapshot.config_status_label, snapshot=snapshot)
    target = tmp_path / "BORING-diagnostics.json"

    session.export(target, model)
    report = json.loads(target.read_text(encoding="utf-8"))

    assert report["format"] == "boring-diagnostics-v1"
    assert report["device"]["identity"]["serial"] == "CP01-AABBCCDDEEFF"
    assert report["configuration"]["generation"] == 1
    assert "profiles" not in report["configuration"]
    assert report["runtime"]["joystick_diagnostics"]["raw_x"] == 2048
    assert report["unavailable_fields"] == ["battery", "charging", "device_logs"]


def test_direction_mask_uses_authoritative_status_bit_order() -> None:
    assert joystick_direction_label(0) == "CENTER"
    assert joystick_direction_label(4) == "UP"
    assert joystick_direction_label(2 | 8) == "RIGHT + DOWN"
    assert joystick_direction_label("4") == "—"


def test_diagnostic_capture_records_instantaneous_control_events(contract) -> None:
    session = DiagnosticsSession()
    snapshot = _power_v2_snapshot(contract, read_only=False)
    session.bind_snapshot(snapshot)
    active = {
        **snapshot.status,
        "diagnostic_capture": {
            "active": True,
            "timeout_ms": 3000,
            "event_sequence": 1,
            "last_control": "encoder.cw",
            "last_pressed": True,
        },
    }

    session.observe_status(snapshot.status, active)

    assert session.tested_controls == frozenset({"encoder.cw"})
    assert [event.category for event in session.events[-2:]] == [
        "CAPTURE",
        "CAPTURE",
    ]
    assert session.events[-1].detail == "control=encoder.cw pressed=true"

    session.observe_status(active, dict(active))
    assert session.tested_controls == frozenset({"encoder.cw"})


def test_same_device_reconnect_starts_a_fresh_control_check(contract) -> None:
    session = DiagnosticsSession()
    snapshot = _power_v2_snapshot(contract, read_only=False)
    session.bind_snapshot(snapshot)
    active = {
        **snapshot.status,
        "diagnostic_capture": {
            "active": True,
            "timeout_ms": 3000,
            "event_sequence": 1,
            "last_control": "joystick.up",
            "last_pressed": True,
        },
    }
    session.observe_status(snapshot.status, active)
    assert session.tested_controls == frozenset({"joystick.up"})

    session.record_connection_loss("USB disconnected")
    session.bind_snapshot(snapshot)

    assert session.tested_controls == frozenset()
