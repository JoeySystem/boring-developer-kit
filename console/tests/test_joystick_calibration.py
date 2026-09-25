from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from controller_config.joystick_calibration import (
    CalibrationError,
    CalibrationSample,
    CalibrationState,
)
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transport.demo import DemoGateway, _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel


class CalibrationGateway(QObject):
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.commands = []
        self.poll_intervals = []
        self.scan_calls = 0

    def scan(self) -> None:
        self.scan_calls += 1

    def connect_port(self, _port_name: str) -> None:
        pass

    def execute_command(self, command) -> None:
        self.commands.append(command)

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self.poll_intervals.append(interval_ms)

    def shutdown(self) -> None:
        pass


def _start_result(session_id: int = 7) -> dict:
    return {
        "command": "CALIBRATION_START",
        "result": {
            "session_id": session_id,
            "state": "CENTERING",
            "raw": {"x": 2048, "y": 2048},
            "center": None,
            "minimum": None,
            "maximum": None,
            "travel_complete": False,
            "center_window_ms": 500,
            "timeout_ms": 30_000,
        },
    }


def _sample_result(*, complete: bool, session_id: int = 7) -> dict:
    return {
        "command": "CALIBRATION_SAMPLE",
        "result": {
            "session_id": session_id,
            "state": "CAPTURING",
            "raw": {"x": 2048 if complete else 260, "y": 2048 if complete else 340},
            "center": {"x": 2048, "y": 2048},
            "minimum": {"x": 260, "y": 340},
            "maximum": {"x": 3820, "y": 3750},
            "travel_complete": complete,
        },
    }


def _ready_transaction(view_model: MainViewModel, gateway: CalibrationGateway) -> None:
    view_model.start_joystick_calibration()
    gateway.command_completed.emit("CALIBRATION_START", _start_result())
    view_model._poll_calibration_sample()
    gateway.command_completed.emit(
        "CALIBRATION_SAMPLE", _sample_result(complete=True)
    )


def test_complete_calibration_samples_confirms_and_reads_back(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    _ready_transaction(view_model, gateway)

    assert view_model.calibration.state is CalibrationState.READY_TO_CONFIRM
    assert view_model.calibration.travel_complete is True
    view_model.confirm_joystick_calibration()
    assert gateway.commands[-1].name == "CALIBRATION_CONFIRM"
    assert gateway.commands[-1].payload == {
        "session_id": 7,
        "base_generation": snapshot.config_result["generation"],
    }

    config = copy.deepcopy(snapshot.config)
    config["joystick"]["calibrated"] = True
    config["joystick"]["x"].update(
        minimum=260, center=2048, maximum=3820, deadzone=80
    )
    config["joystick"]["y"].update(
        minimum=340, center=2048, maximum=3750, deadzone=90
    )
    digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
    generation = snapshot.config_result["generation"] + 1
    gateway.command_completed.emit(
        "CALIBRATION_CONFIRM",
        {
            "command": "CALIBRATION_CONFIRM",
            "result": {
                "state": "PENDING_ACTIVATION",
                "generation": generation,
                "digest": digest,
            },
        },
    )
    assert view_model.calibration.state is CalibrationState.PENDING
    assert gateway.poll_intervals[-1] == 250

    gateway.status_updated.emit(
        {
            **snapshot.status,
            "state": "ACTIVE",
            "active": {"generation": generation, "digest": digest},
            "pending": None,
        }
    )
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit(
        "GET_CONFIG",
        {
            "command": "GET_CONFIG",
            "result": {
                "generation": generation,
                "digest": digest,
                "config": config,
            },
        },
    )

    assert view_model.calibration.state is CalibrationState.COMPLETED
    assert view_model.draft is not None and not view_model.draft.is_dirty
    assert view_model.draft.config["joystick"]["filter"] == snapshot.config["joystick"]["filter"]
    assert view_model.draft.config["joystick"]["x"]["invert"] is False
    assert gateway.poll_intervals[-1] == 500


def test_cancel_preserves_original_configuration(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    view_model.start_joystick_calibration()
    gateway.command_completed.emit("CALIBRATION_START", _start_result())

    view_model.cancel_joystick_calibration()
    assert gateway.commands[-1].name == "CALIBRATION_CANCEL"
    gateway.command_completed.emit(
        "CALIBRATION_CANCEL",
        {
            "command": "CALIBRATION_CANCEL",
            "result": {"state": "CANCELLED", "session_id": 7},
        },
    )

    assert view_model.calibration.state is CalibrationState.CANCELLED
    assert view_model.model.snapshot.config == snapshot.config
    assert not any(command.name == "CALIBRATION_CONFIRM" for command in gateway.commands)


def test_failed_center_check_keeps_session_available_for_retry(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    _ready_transaction(view_model, gateway)
    view_model.confirm_joystick_calibration()

    gateway.command_failed.emit(
        "CALIBRATION_CONFIRM",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令执行失败",
            "return the joystick to center before confirming",
            error_name="VALIDATION_FAILED",
        ),
    )

    assert view_model.calibration.state is CalibrationState.READY_TO_CONFIRM
    assert view_model.calibration.can_cancel is True
    assert view_model.calibration.can_confirm is True


def test_disconnect_before_confirmation_cancels_session_locally(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    view_model.start_joystick_calibration()
    gateway.command_completed.emit("CALIBRATION_START", _start_result())

    gateway.disconnected.emit("USB removed")

    assert view_model.calibration.state is CalibrationState.FAILED
    assert view_model.calibration.session_id == 0
    assert view_model.calibration.blocks_editing is False


def test_cancel_waits_for_in_flight_sample(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    view_model.start_joystick_calibration()
    gateway.command_completed.emit("CALIBRATION_START", _start_result())
    view_model._poll_calibration_sample()
    assert gateway.commands[-1].name == "CALIBRATION_SAMPLE"

    view_model.cancel_joystick_calibration()
    assert view_model.calibration.state is CalibrationState.CANCELLING
    assert gateway.commands[-1].name == "CALIBRATION_SAMPLE"
    gateway.command_completed.emit(
        "CALIBRATION_SAMPLE", _sample_result(complete=False)
    )

    assert gateway.commands[-1].name == "CALIBRATION_CANCEL"


def test_unknown_confirm_result_reconciles_after_reconnect(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    _ready_transaction(view_model, gateway)
    view_model.confirm_joystick_calibration()

    gateway.command_failed.emit(
        "CALIBRATION_CONFIRM",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令响应超时",
            "CALIBRATION_CONFIRM timeout",
            error_name="TRANSPORT_TIMEOUT",
        ),
    )
    assert view_model.calibration.state is CalibrationState.UNKNOWN
    assert gateway.scan_calls == 1

    config = copy.deepcopy(snapshot.config)
    config["joystick"]["calibrated"] = True
    config["joystick"]["x"].update(
        minimum=260, center=2048, maximum=3820, deadzone=80
    )
    config["joystick"]["y"].update(
        minimum=340, center=2048, maximum=3750, deadzone=90
    )
    digest = hashlib.sha256(canonical_json_bytes(config)).hexdigest()
    generation = snapshot.config_result["generation"] + 1
    active_status = {
        **snapshot.status,
        "active": {"generation": generation, "digest": digest},
        "pending": None,
    }
    gateway.snapshot_ready.emit(
        replace(
            snapshot,
            hello_config={"generation": generation, "digest": digest},
            status=active_status,
            config_result={
                "generation": generation,
                "digest": digest,
                "config": config,
            },
        )
    )

    assert view_model.calibration.state is CalibrationState.COMPLETED


def test_malformed_confirm_response_is_reconciled_without_resending(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    _ready_transaction(view_model, gateway)
    view_model.confirm_joystick_calibration()

    gateway.command_completed.emit(
        "CALIBRATION_CONFIRM",
        {
            "command": "CALIBRATION_CONFIRM",
            "result": {"state": "PENDING_ACTIVATION", "generation": 2},
        },
    )

    assert view_model.calibration.state is CalibrationState.UNKNOWN
    assert view_model.calibration.session_id == 0
    assert gateway.scan_calls == 1
    assert sum(
        command.name == "CALIBRATION_CONFIRM" for command in gateway.commands
    ) == 1


def test_start_is_blocked_while_local_draft_is_dirty(qtbot, contract) -> None:
    gateway = CalibrationGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    view_model.rename_profile(snapshot.active_profile_id, "Local draft")

    try:
        view_model.start_joystick_calibration()
    except ValueError as exc:
        assert "本地草稿" in str(exc)
    else:
        raise AssertionError("dirty draft must block calibration")


def test_calibration_sample_rejects_wrong_session_and_adc_range() -> None:
    payload = _sample_result(complete=False)
    payload["result"]["raw"]["x"] = 5000

    try:
        CalibrationSample.from_payload(payload, expected_session_id=9)
    except CalibrationError as exc:
        assert "不属于当前会话" in str(exc)
    else:
        raise AssertionError("wrong session must be rejected")

    try:
        CalibrationSample.from_payload(payload, expected_session_id=7)
    except CalibrationError as exc:
        assert "0..4095" in str(exc)
    else:
        raise AssertionError("out-of-range ADC sample must be rejected")


def test_demo_gateway_runs_the_full_calibration_transaction(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    view_model.start_joystick_calibration()
    qtbot.waitUntil(
        lambda: view_model.calibration.state is CalibrationState.READY_TO_CONFIRM,
        timeout=2000,
    )
    view_model.confirm_joystick_calibration()
    qtbot.waitUntil(
        lambda: view_model.calibration.state is CalibrationState.COMPLETED,
        timeout=2000,
    )

    assert view_model.draft is not None
    assert view_model.draft.config["joystick"]["calibrated"] is True
