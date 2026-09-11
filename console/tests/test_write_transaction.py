from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel


class FakeWriteGateway(QObject):
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


def _dirty_view_model(contract):
    gateway = FakeWriteGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    view_model.rename_profile(snapshot.active_profile_id, "Write candidate")
    return view_model, gateway, snapshot


def _ack(command: str, result: dict | None = None) -> dict:
    return {"command": command, "result": result or {}}


def _active_status(snapshot, digest: str, generation: int) -> dict:
    return {
        **snapshot.status,
        "state": "ACTIVE",
        "active": {"generation": generation, "digest": digest},
        "pending": None,
        "activation_failed": False,
    }


def test_write_happy_path_validates_confirms_polls_and_rebases_draft(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)

    view_model.prepare_device_write()

    validate = gateway.commands[-1]
    expected_config = copy.deepcopy(view_model.draft.config)
    expected_digest = hashlib.sha256(canonical_json_bytes(expected_config)).hexdigest()
    assert validate.name == "VALIDATE_CONFIG"
    assert validate.payload == {"config": expected_config, "digest": expected_digest}
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert view_model.write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION

    view_model.confirm_device_write()

    set_command = gateway.commands[-1]
    assert set_command.name == "SET_CONFIG"
    assert set_command.payload == {
        "config": expected_config,
        "digest": expected_digest,
        "base_generation": snapshot.config_result["generation"],
    }
    assert gateway.poll_intervals[-1] == 250
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    gateway.status_updated.emit(
        {
            **snapshot.status,
            "state": "PENDING_ACTIVATION",
            "pending": {
                "generation": snapshot.config_result["generation"] + 1,
                "digest": expected_digest,
            },
        }
    )
    assert view_model.write_transaction.state is ConfigTransactionState.PENDING

    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, expected_digest, generation))
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit(
        "GET_CONFIG",
        _ack(
            "GET_CONFIG",
            {"generation": generation, "digest": expected_digest, "config": expected_config},
        ),
    )

    assert view_model.write_transaction.state is ConfigTransactionState.ACTIVE
    assert view_model.model.snapshot.config_result["generation"] == generation
    assert view_model.draft is not None and not view_model.draft.is_dirty
    assert gateway.poll_intervals[-1] == 500


def test_generation_conflict_is_terminal_and_never_retries_set(contract) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()

    gateway.command_failed.emit(
        "SET_CONFIG",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令执行失败",
            "GENERATION_CONFLICT: stale",
            error_name="GENERATION_CONFLICT",
            details={"current_generation": 8},
        ),
    )

    assert view_model.write_transaction.state is ConfigTransactionState.CONFLICT
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_conflict_can_rebase_full_draft_or_discard_to_latest_device_config(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_failed.emit(
        "SET_CONFIG",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令执行失败",
            "GENERATION_CONFLICT: stale",
            error_name="GENERATION_CONFLICT",
            details={"current_generation": 2},
        ),
    )
    local_name = view_model.draft.profile(snapshot.active_profile_id)["name"]
    latest_config = copy.deepcopy(snapshot.config)
    latest_config["profiles"][0]["name"] = "Device latest"
    latest_digest = hashlib.sha256(canonical_json_bytes(latest_config)).hexdigest()
    latest = replace(
        snapshot,
        hello_config={"generation": 2, "digest": latest_digest},
        status=_active_status(snapshot, latest_digest, 2),
        config_result={"generation": 2, "digest": latest_digest, "config": latest_config},
    )
    gateway.snapshot_ready.emit(latest)

    view_model.rebase_conflicted_draft()

    assert view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert view_model.draft.base_generation == 2
    assert view_model.draft.profile(snapshot.active_profile_id)["name"] == local_name
    assert view_model.draft.is_dirty
    view_model.prepare_device_write()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"

    discard_view_model, discard_gateway, discard_snapshot = _dirty_view_model(contract)
    discard_view_model._set_write_transaction(
        ConfigTransaction(ConfigTransactionState.CONFLICT, "设备配置已变化")
    )
    discard_gateway.snapshot_ready.emit(latest)
    discard_view_model.discard_conflicted_draft()

    assert discard_view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert discard_view_model.draft.base_generation == 2
    assert discard_view_model.draft.profile(discard_snapshot.active_profile_id)["name"] == "Device latest"
    assert not discard_view_model.draft.is_dirty


def test_device_validation_nack_prevents_set_and_keeps_draft(contract) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()

    gateway.command_failed.emit(
        "VALIDATE_CONFIG",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令执行失败",
            "VALIDATION_FAILED: profiles.0.name",
            error_name="VALIDATION_FAILED",
        ),
    )

    assert view_model.write_transaction.state is ConfigTransactionState.FAILED
    assert [command.name for command in gateway.commands] == ["VALIDATE_CONFIG"]
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_draft_change_after_validation_requires_validation_again(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.rename_profile(snapshot.active_profile_id, "Changed after validation")

    assert view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert [command.name for command in gateway.commands] == ["VALIDATE_CONFIG"]


def test_cancelling_confirmation_keeps_dirty_draft_without_sending_set(contract) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))

    view_model.cancel_device_write_confirmation()

    assert view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert [command.name for command in gateway.commands] == ["VALIDATE_CONFIG"]
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_lost_set_ack_reconciles_without_retrying_set(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    digest = view_model.write_transaction.candidate_digest

    gateway.command_failed.emit(
        "SET_CONFIG",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备命令响应超时",
            "SET_CONFIG 未在超时前返回",
            error_name="TRANSPORT_TIMEOUT",
        ),
    )
    assert view_model.write_transaction.state is ConfigTransactionState.PENDING
    gateway.status_updated.emit(
        _active_status(snapshot, digest, snapshot.config_result["generation"] + 1)
    )

    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1
    assert gateway.commands[-1].name == "GET_CONFIG"


def test_active_readback_is_not_duplicated_when_write_deadline_fires(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    digest = view_model.write_transaction.candidate_digest
    generation = snapshot.config_result["generation"] + 1

    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    view_model._write_deadline.timeout.emit()

    assert [command.name for command in gateway.commands].count("GET_CONFIG") == 1
    gateway.command_completed.emit(
        "GET_CONFIG",
        _ack(
            "GET_CONFIG",
            {
                "generation": generation,
                "digest": digest,
                "config": copy.deepcopy(view_model.write_transaction.candidate_config),
            },
        ),
    )
    assert view_model.write_transaction.state is ConfigTransactionState.ACTIVE


def test_inputs_neutral_change_still_updates_pending_write_guidance(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    digest = view_model.write_transaction.candidate_digest
    pending = {
        "generation": snapshot.config_result["generation"] + 1,
        "digest": digest,
    }

    gateway.status_updated.emit(
        {
            **snapshot.status,
            "state": "PENDING_ACTIVATION",
            "pending": pending,
            "inputs_neutral": False,
        }
    )
    assert "松开所有实体控件" in view_model.write_transaction.message

    gateway.status_updated.emit(
        {
            **view_model.model.snapshot.status,
            "inputs_neutral": True,
        }
    )
    assert view_model.write_transaction.message == "候选配置等待激活"


def test_activation_failure_and_confirmed_not_applied_keep_dirty_draft(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    gateway.status_updated.emit({**snapshot.status, "activation_failed": True})

    assert view_model.write_transaction.state is ConfigTransactionState.FAILED
    assert view_model.draft is not None and view_model.draft.is_dirty

    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    view_model._write_deadline.timeout.emit()
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", snapshot.config_result))

    assert view_model.write_transaction.state is ConfigTransactionState.FAILED
    assert "未落盘" in view_model.write_transaction.message
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_disconnect_reconciles_from_bootstrap_readback(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    candidate = copy.deepcopy(view_model.write_transaction.candidate_config)
    digest = view_model.write_transaction.candidate_digest
    generation = snapshot.config_result["generation"] + 1

    gateway.disconnected.emit("gone")
    reconnected = replace(
        snapshot,
        status=_active_status(snapshot, digest, generation),
        config_result={"generation": generation, "digest": digest, "config": candidate},
    )
    gateway.snapshot_ready.emit(reconnected)

    assert view_model.write_transaction.state is ConfigTransactionState.ACTIVE
    assert view_model.draft is not None and not view_model.draft.is_dirty
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


def test_transport_failure_after_set_enters_unknown_without_retry(contract) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()

    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short write")

    assert view_model.write_transaction.state is ConfigTransactionState.UNKNOWN
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_unknown_must_reconcile_before_editing_or_starting_another_write(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short write")

    try:
        view_model.rename_profile(snapshot.active_profile_id, "Must reconcile")
        raise AssertionError("UNKNOWN transaction allowed a draft edit")
    except ValueError as exc:
        assert "不能修改本地草稿" in str(exc)
    try:
        view_model.prepare_device_write()
        raise AssertionError("UNKNOWN transaction allowed another write")
    except ValueError as exc:
        assert "写入事务尚未结束" in str(exc)

    assert view_model.write_transaction.state is ConfigTransactionState.UNKNOWN
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


def test_unknown_manual_reconcile_reads_back_without_retrying_set(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    candidate = copy.deepcopy(view_model.write_transaction.candidate_config)
    digest = view_model.write_transaction.candidate_digest
    generation = snapshot.config_result["generation"] + 1
    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short write")

    view_model.reconcile_device_write()

    assert gateway.scan_calls == 1
    assert gateway.commands[-1].name == "SET_CONFIG"
    gateway.snapshot_ready.emit(replace(
        snapshot,
        status=_active_status(snapshot, digest, generation),
        config_result={"generation": generation, "digest": digest, "config": candidate},
    ))
    assert view_model.write_transaction.state is ConfigTransactionState.ACTIVE
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


def test_unknown_reconcile_while_disconnected_scans_without_sending_command(contract) -> None:
    view_model, gateway, _snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short write")
    gateway.disconnected.emit("gone")
    command_count = len(gateway.commands)

    view_model.reconcile_device_write()

    assert gateway.scan_calls == 1
    assert len(gateway.commands) == command_count
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1


def test_reconnect_to_old_config_finishes_failed_instead_of_staying_pending(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))

    gateway.disconnected.emit("gone")
    gateway.snapshot_ready.emit(snapshot)

    assert view_model.write_transaction.state is ConfigTransactionState.FAILED
    assert "未落盘" in view_model.write_transaction.message
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_unknown_reconcile_to_unchanged_base_allows_edit_without_retry(contract) -> None:
    view_model, gateway, snapshot = _dirty_view_model(contract)
    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short write")

    view_model.reconcile_device_write()
    assert gateway.scan_calls == 1
    gateway.snapshot_ready.emit(snapshot)

    assert view_model.write_transaction.state is ConfigTransactionState.FAILED
    assert "未落盘" in view_model.write_transaction.message
    view_model.rename_profile(snapshot.active_profile_id, "Retry after reconciliation")
    assert view_model.write_transaction.state is ConfigTransactionState.IDLE
    assert [command.name for command in gateway.commands].count("SET_CONFIG") == 1
