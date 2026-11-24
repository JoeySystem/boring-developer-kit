from __future__ import annotations

import json
from datetime import datetime, timezone

from controller_config.diagnostics import (
    DiagnosticsSession,
    diagnostic_readiness,
    joystick_direction_label,
)
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
    assert report["runtime"]["battery"] == 73
    assert report["runtime"]["is_charging"] is None
    assert report["unavailable_fields"] == ["charging", "device_logs"]


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
        "action_engine": {
            **snapshot.status["action_engine"],
            "host_output_state": "diagnostic_capture",
        },
        "diagnostic_capture": {
            "active": True,
            "timeout_ms": 3000,
            "event_sequence": 1,
            "last_control": "encoder.cw",
            "last_pressed": True,
        },
    }

    session.observe_status(
        snapshot.status,
        active,
        capture_start_confirmed=True,
    )

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
        "action_engine": {
            **snapshot.status["action_engine"],
            "host_output_state": "diagnostic_capture",
        },
        "diagnostic_capture": {
            "active": True,
            "timeout_ms": 3000,
            "event_sequence": 1,
            "last_control": "joystick.up",
            "last_pressed": True,
        },
    }
    session.observe_status(
        snapshot.status,
        active,
        capture_start_confirmed=True,
    )
    assert session.tested_controls == frozenset({"joystick.up"})

    session.record_connection_loss("USB disconnected")
    session.bind_snapshot(snapshot)

    assert session.tested_controls == frozenset()


def _ready_status(contract) -> dict:
    return dict(_power_v2_snapshot(contract, read_only=False).status)


def test_diagnostic_readiness_reports_one_highest_priority_blocker(contract) -> None:
    status = _ready_status(contract)
    status.update(
        inputs_neutral=False,
        active_controls=["key.3", "joystick.up"],
        pending={"generation": 2},
    )
    status["action_engine"] = {
        **status["action_engine"],
        "host_output_state": "wait_neutral",
        "local_page": "menu",
        "quick_config_active": True,
        "idle_standby_active": True,
    }

    readiness = diagnostic_readiness(status, calibration_active=True)

    assert readiness.state == "waiting"
    assert readiness.reason == "active_control"
    assert readiness.control_id == "key.3"


def test_diagnostic_readiness_covers_each_known_blocker(contract) -> None:
    base = _ready_status(contract)
    cases = (
        ({"inputs_neutral": False}, {}, False, "active_control"),
        ({}, {"host_output_state": "wait_neutral"}, False, "wait_neutral"),
        ({}, {"idle_standby_active": True}, False, "standby"),
        ({}, {"usb_standby_active": True}, False, "standby"),
        ({}, {"local_page": "quick_settings"}, False, "local_page"),
        ({}, {"quick_config_active": True}, False, "local_page"),
        ({"pending": {"generation": 2}}, {}, False, "config_pending"),
        ({}, {}, True, "console_calibration"),
    )
    for status_patch, action_patch, calibration_active, reason in cases:
        status = {**base, **status_patch}
        status["action_engine"] = {**base["action_engine"], **action_patch}
        readiness = diagnostic_readiness(
            status, calibration_active=calibration_active
        )
        assert readiness.ready is False
        assert readiness.reason == reason


def test_diagnostic_readiness_treats_missing_or_unknown_state_as_unconfirmed(
    contract,
) -> None:
    base = _ready_status(contract)
    for status in (
        {key: value for key, value in base.items() if key != "inputs_neutral"},
        {**base, "action_engine": {"host_output_state": "enabled"}},
        {
            **base,
            "action_engine": {
                **base["action_engine"],
                "host_output_state": "unexpected_owner",
            },
        },
    ):
        readiness = diagnostic_readiness(status)
        assert readiness.state == "waiting"
        assert readiness.reason == "confirming_status"


def test_diagnostic_readiness_keeps_activation_failure_non_blocking(contract) -> None:
    status = {**_ready_status(contract), "activation_failed": True}

    readiness = diagnostic_readiness(status)

    assert readiness.ready is True
    assert readiness.reason == "ready"
    assert readiness.activation_failed is True
