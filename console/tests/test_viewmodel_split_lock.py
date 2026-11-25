"""A-15 round 2 behaviour lock for the MainViewModel split.

Each scenario drives MainViewModel through a recording gateway (no Qt
window) and records, after every step, what the user would see and what was
sent to the device. The golden traces were captured on main before the
connection and firmware update controllers were extracted; the split must
reproduce them exactly. Regenerate only for an intended behaviour change:
BORING_UPDATE_SPLIT_LOCK=1 pytest tests/test_viewmodel_split_lock.py
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

from controller_config.models import PortCandidate
from controller_config.protocol.bootstrap import COMMAND_DROPPED, BootstrapError, BootstrapKind
from controller_config.protocol.device_auth import DeviceTrustState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_firmware_release import trust_test_signing_key  # noqa: F401  (autouse signing key)
from test_firmware_transaction import _booted_status, _package, _status


GOLDEN = Path(__file__).parent / "fixtures" / "viewmodel_split_lock.json"


class RecordingGateway(QObject):
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)
    usb_candidate_found = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.log: list[list] = []

    def drain(self) -> list[list]:
        log, self.log = self.log, []
        return log

    def scan(self, *, usb_only: bool = False) -> None:
        self.log.append(["scan", usb_only])

    def scan_reconnect(self, port_name: str, *, usb_only: bool = False) -> None:
        self.log.append(["scan_reconnect", port_name, usb_only])

    def connect_port(self, port_name: str) -> None:
        self.log.append(["connect", port_name])

    def probe_usb(self, serial: str) -> None:
        self.log.append(["probe_usb", serial])

    def execute_command(self, command) -> None:
        self.log.append(["command", command.name])

    def set_status_poll_interval(self, interval_ms: int) -> None:
        self.log.append(["poll", interval_ms])

    def shutdown(self) -> None:
        self.log.append(["shutdown"])


def _observe(vm: MainViewModel, gateway: RecordingGateway, step: str) -> dict:
    fw = vm.firmware_update
    write = vm.write_transaction
    return {
        "step": step,
        "app": [vm.model.state.name, vm.model.message, vm.model.technical_message],
        "candidates": [c.port_name for c in vm.model.candidates],
        "page": vm.page,
        "firmware": [fw.state.name, fw.message, fw.technical, fw.received_size,
                     fw.in_flight_size, fw.target_partition, fw.abort_requested],
        "write": [write.state.name, write.message, write.technical],
        "remote": vm.remote_firmware.state.name,
        "reconnect_timer": [vm._connection.reconnect_timer.isActive(), vm._connection.reconnect_timer.interval()],
        "usb_probe_timer": vm._connection.usb_probe_timer.isActive(),
        "firmware_timers": [vm._firmware.status_timer.isActive(), vm._firmware.deadline.isActive()],
        "failed_ports": sorted(vm._connection.failed_ports),
        "gateway": gateway.drain(),
    }


class Run:
    def __init__(self, contract) -> None:
        self.gateway = RecordingGateway()
        self.vm = MainViewModel(self.gateway, contract)
        self.clock = [100.0]
        self.vm._connection.monotonic = lambda: self.clock[0]
        self.trace: list[dict] = []
        self.step("init")

    def step(self, name: str) -> None:
        self.trace.append(_observe(self.vm, self.gateway, name))

    def ready(self, snapshot) -> None:
        self.gateway.snapshot_ready.emit(snapshot)
        # Optional background readers have no fake replies.
        self.vm.screen_icon.attach(None)
        self.vm.screen_glyphs.attach(None)
        self.vm.ble_name.detach()
        self.vm.normal_agent.detach()
        self.step(f"snapshot {snapshot.port_name}")

    def finish(self) -> list[dict]:
        self.vm.shutdown()
        self.step("shutdown")
        return self.trace


def _error(name: str = "", kind=BootstrapKind.READ_FAILED) -> BootstrapError:
    return BootstrapError(kind, "device error", f"technical {name or 'nack'}", error_name=name)


def _usb(contract, port="cu.usbmodem-lock"):
    return replace(_power_v2_snapshot(contract, read_only=False), port_name=port)


def _authenticated(snapshot):
    return replace(snapshot, trust=replace(snapshot.trust, state=DeviceTrustState.AUTHENTICATED))


# ---- connection ---------------------------------------------------------


def scenario_usb_read_failure_and_replug(contract, _tmp_path):
    run = Run(contract)
    run.vm.start()
    run.step("start")
    port = PortCandidate("cu.usbmodem-lock")
    run.gateway.candidates_found.emit((port,))
    run.step("one usb candidate")
    run.gateway.progress.emit("正在读取 GET_CONFIG")
    run.step("progress GET_CONFIG")
    run.gateway.failure.emit(BootstrapKind.READ_FAILED, "配置损坏", "GET_CONFIG payload invalid")
    run.step("read failed while reading config")
    run.vm._connection.scan_for_reconnect()
    run.step("reconnect tick after read failure")
    run.gateway.candidates_found.emit((port, PortCandidate("ble:other", transport="bluetooth")))
    run.step("same failed port still present")
    run.gateway.candidates_found.emit(())
    run.step("port vanished")
    run.gateway.candidates_found.emit((port,))
    run.step("port re-inserted")
    return run.finish()


def scenario_ble_failure_messages(contract, _tmp_path):
    run = Run(contract)
    run.vm.connect_candidate("ble:desk")
    run.step("connect ble")
    run.gateway.failure.emit(BootstrapKind.READ_FAILED, "无法打开", "port busy")
    run.step("ble failed before config")
    run.vm.connect_candidate("ble:desk")
    run.gateway.progress.emit("GET_CONFIG")
    run.gateway.failure.emit(BootstrapKind.READ_FAILED, "配置损坏", "bad config")
    run.step("ble failed while reading config")
    run.vm._connection.port = ""
    run.vm._set(replace(run.vm.model, state=run.vm.model.state.__class__.CONNECTING))
    run.gateway.failure.emit(BootstrapKind.READ_FAILED, "未知", "no port")
    run.step("failure without port")
    return run.finish()


def scenario_failure_kinds(contract, _tmp_path, kind):
    run = Run(contract)
    run.ready(_usb(contract))
    run.gateway.failure.emit(kind, f"{kind.name} message", f"{kind.name} technical")
    run.step(f"failure {kind.name}")
    run.vm._connection.scan_for_reconnect()
    run.step("reconnect tick")
    return run.finish()


def scenario_candidates_lists(contract, _tmp_path):
    run = Run(contract)
    run.vm.refresh()
    run.step("refresh")
    run.gateway.candidates_found.emit(())
    run.step("no candidates")
    run.gateway.candidates_found.emit((PortCandidate("cu.a"), PortCandidate("cu.b")))
    run.step("two candidates")
    run.vm.connect_candidate("cu.b")
    run.step("user picks one")
    return run.finish()


def scenario_disconnect_backoff(contract, _tmp_path):
    from controller_config.viewmodels.connection_controller import full_scan_delays_ms

    run = Run(contract)
    # The golden trace was captured with the A-07 backoff; macOS now scans
    # Bluetooth every second (SH-005, test_reconnect_recovery covers it).
    run.vm._connection.RECONNECT_FULL_SCAN_DELAYS_MS = full_scan_delays_ms("win32")
    run.ready(_usb(contract))
    run.gateway.disconnected.emit("cable removed")
    run.step("disconnected")
    for tick in range(8):
        run.clock[0] += 0.6
        run.vm._connection.scan_for_reconnect()
        run.step(f"tick {tick}")
    run.gateway.candidates_found.emit(())
    run.step("empty scan while disconnected")
    run.gateway.candidates_found.emit((PortCandidate("cu.usbmodem-lock"),))
    run.step("device back")
    return run.finish()


def scenario_firmware_maintenance_entry(contract, _tmp_path):
    run = Run(contract)
    snapshot = replace(_usb(contract), maintenance_reason="schema incompatible")
    run.gateway.snapshot_ready.emit(snapshot)
    run.step("maintenance snapshot")
    run.gateway.disconnected.emit("unplugged")
    run.step("maintenance device unplugged")
    return run.finish()


def scenario_usb_handoff(contract, _tmp_path):
    run = Run(contract)
    snapshot = _authenticated(replace(_power_v2_snapshot(contract, read_only=False), port_name="ble:current"))
    run.ready(snapshot)
    run.vm._connection.usb_probe_timer.timeout.emit()
    run.step("probe tick")
    serial = snapshot.identity["serial"]
    run.gateway.usb_candidate_found.emit(PortCandidate("cu.other", serial_number="OTHER"))
    run.step("other usb device")
    run.gateway.usb_candidate_found.emit(PortCandidate("cu.same", serial_number=serial))
    run.step("same usb device")
    run.gateway.snapshot_ready.emit(replace(snapshot, port_name="cu.same"))
    run.step("usb session")
    return run.finish()


# ---- firmware update ----------------------------------------------------


def _firmware_run(contract, tmp_path):
    run = Run(contract)
    snapshot = _usb(contract)
    run.ready(snapshot)
    package = run.vm.load_firmware_package(_package(tmp_path, contract))
    run.step("package loaded")
    run.vm.start_firmware_update()
    run.step("start")
    return run, snapshot, package


def _begin(run, package, offset=0):
    run.gateway.command_completed.emit("FW_BEGIN", {"command": "FW_BEGIN", "result": {
        "state": "RECEIVING", "offset": offset, "chunk_bytes": 256, "target_partition": "ota_1"}})
    run.step("FW_BEGIN ack")


def _data(run, received):
    run.gateway.command_completed.emit("FW_DATA", {"command": "FW_DATA", "result": {
        "state": "RECEIVING", "received_size": received}})
    run.step(f"FW_DATA ack {received}")


def scenario_firmware_happy_path(contract, tmp_path):
    run, snapshot, package = _firmware_run(contract, tmp_path)
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    run.step("FW_STATUS idle")
    _begin(run, package)
    for received in range(256, package.size + 1, 256):
        _data(run, received)
    run.gateway.command_completed.emit("FW_END", {"command": "FW_END", "result": {
        "state": "FINALIZING", "version": package.version}})
    run.step("FW_END finalizing")
    run.vm._firmware.poll_status()
    run.step("status poll")
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "READY_TO_REBOOT", package.size))
    run.step("ready to reboot")
    run.gateway.disconnected.emit("expected reboot")
    run.step("rebooting")
    updated = replace(snapshot, versions={**snapshot.versions, "firmware": package.version,
                                          "build_id": package.build_id})
    run.gateway.snapshot_ready.emit(updated)
    run.step("reconnected")
    run.gateway.command_completed.emit("FW_STATUS", _booted_status(package, rollback_pending=True))
    run.step("rollback pending")
    run.gateway.command_completed.emit("FW_STATUS", _booted_status(package))
    run.step("verified")
    run.vm.dismiss_firmware_update()
    run.step("dismissed")
    return run.finish()


def scenario_firmware_resume_and_timeouts(contract, tmp_path):
    run, snapshot, package = _firmware_run(contract, tmp_path)
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "RECEIVING", 256))
    run.step("resume from device offset")
    run.gateway.command_failed.emit("FW_DATA", _error("TRANSPORT_TIMEOUT"))
    run.step("FW_DATA timeout")
    run.gateway.command_failed.emit("FW_STATUS", _error(COMMAND_DROPPED))
    run.step("dropped FW_STATUS ignored")
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "RECEIVING", package.size))
    run.step("status after timeout")
    run.gateway.command_failed.emit("FW_END", _error("TRANSPORT_TIMEOUT"))
    run.step("FW_END timeout")
    run.gateway.candidates_found.emit(())
    run.step("usb scan empty")
    run.vm._firmware.on_deadline()
    run.step("deadline while verifying")
    return run.finish()


def scenario_firmware_deadlines_and_pause(contract, tmp_path):
    run, snapshot, package = _firmware_run(contract, tmp_path)
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    _begin(run, package)
    run.gateway.disconnected.emit("cable pulled mid transfer")
    run.step("paused by disconnect")
    run.vm._connection.scan_for_reconnect()
    run.step("urgent usb reconnect")
    run.gateway.candidates_found.emit(())
    run.step("waiting for usb")
    run.gateway.snapshot_ready.emit(snapshot)
    run.step("same device back")
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "FINALIZING", package.size))
    run.step("device finalizing")
    run.vm._firmware.on_deadline()
    run.step("finalize deadline")
    return run.finish()


def scenario_firmware_abort_and_reject(contract, tmp_path):
    run, snapshot, package = _firmware_run(contract, tmp_path)
    run.vm.abort_firmware_update()
    run.step("abort while checking")
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "RECEIVING", 256))
    run.step("status during abort")
    run.gateway.command_completed.emit("FW_ABORT", {"command": "FW_ABORT", "result": {"state": "IDLE"}})
    run.step("aborted")
    run.vm.start_firmware_update()
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    run.gateway.command_failed.emit("FW_BEGIN", _error("VALIDATION_FAILED"))
    run.step("FW_BEGIN rejected")
    run.vm.start_firmware_update()
    other = dict(_status(package, "RECEIVING", 0))
    other["result"] = {"firmware_update": {**other["result"]["firmware_update"], "expected_sha256": "0" * 64}}
    run.gateway.command_completed.emit("FW_STATUS", other)
    run.step("foreign receive needs abort")
    run.gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, "serial": "OTHER"}))
    run.step("another device reconnects")
    return run.finish()


def scenario_firmware_failure_wait_and_stop(contract, tmp_path):
    run, snapshot, package = _firmware_run(contract, tmp_path)
    run.gateway.command_completed.emit("FW_STATUS", _status(package, "IDLE"))
    _begin(run, package)
    run.gateway.failure.emit(BootstrapKind.READ_FAILED, "通信失败", "link lost")
    run.step("failure during transfer")
    run.vm._connection.scan_for_reconnect()
    run.step("reconnect tick")
    run.vm.stop_firmware_wait()
    run.step("user stops waiting")
    return run.finish()


# ---- M-12: write command failures --------------------------------------


def scenario_write_failures(contract, _tmp_path, command, error_name):
    run = Run(contract)
    snapshot = _usb(contract)
    run.ready(snapshot)
    run.vm.rename_profile(snapshot.active_profile_id, "Lock candidate")
    run.vm.prepare_device_write()
    run.step("validating")
    if command == "SET_CONFIG":
        run.gateway.command_completed.emit("VALIDATE_CONFIG", {"command": "VALIDATE_CONFIG", "result": {}})
        run.vm.confirm_device_write()
        run.step("writing")
    error = _error(error_name)
    if error_name == "GENERATION_CONFLICT":
        error = BootstrapError(BootstrapKind.READ_FAILED, "conflict", "conflict",
                               error_name=error_name, details={"current_generation": 9})
    run.gateway.command_failed.emit(command, error)
    run.step(f"{command} {error_name or 'no name'}")
    return run.finish()


def scenario_write_readback_failure(contract, _tmp_path):
    run = Run(contract)
    snapshot = _usb(contract)
    run.ready(snapshot)
    run.vm.rename_profile(snapshot.active_profile_id, "Lock candidate")
    run.vm.prepare_device_write()
    run.gateway.command_completed.emit("VALIDATE_CONFIG", {"command": "VALIDATE_CONFIG", "result": {}})
    run.vm.confirm_device_write()
    run.gateway.command_completed.emit("SET_CONFIG", {"command": "SET_CONFIG", "result": {}})
    run.vm._request_config_readback("lock readback")
    run.step("verifying")
    run.gateway.command_failed.emit("GET_CONFIG", _error("TRANSPORT_TIMEOUT"))
    run.step("readback failed")
    return run.finish()


SCENARIOS = {
    "usb_read_failure_and_replug": scenario_usb_read_failure_and_replug,
    "ble_failure_messages": scenario_ble_failure_messages,
    "candidates_lists": scenario_candidates_lists,
    "disconnect_backoff": scenario_disconnect_backoff,
    "firmware_maintenance_entry": scenario_firmware_maintenance_entry,
    "usb_handoff": scenario_usb_handoff,
    "firmware_happy_path": scenario_firmware_happy_path,
    "firmware_resume_and_timeouts": scenario_firmware_resume_and_timeouts,
    "firmware_deadlines_and_pause": scenario_firmware_deadlines_and_pause,
    "firmware_abort_and_reject": scenario_firmware_abort_and_reject,
    "firmware_failure_wait_and_stop": scenario_firmware_failure_wait_and_stop,
    "write_readback_failure": scenario_write_readback_failure,
}
for _kind in (BootstrapKind.FIRMWARE_UPDATE_REQUIRED, BootstrapKind.INCOMPATIBLE,
              BootstrapKind.AUTHENTICITY_FAILED, BootstrapKind.AUTHENTICATION_INTERRUPTED,
              BootstrapKind.DISCOVERY_INTERRUPTED):
    SCENARIOS[f"failure_kind_{_kind.name}"] = (
        lambda contract, tmp_path, kind=_kind: scenario_failure_kinds(contract, tmp_path, kind))
for _command, _error_name in (("VALIDATE_CONFIG", "VALIDATION_FAILED"), ("VALIDATE_CONFIG", "TRANSPORT_TIMEOUT"),
                              ("VALIDATE_CONFIG", ""), ("SET_CONFIG", "STORAGE_FAILURE"),
                              ("SET_CONFIG", "TRANSPORT_TIMEOUT"), ("SET_CONFIG", "GENERATION_CONFLICT"),
                              ("VALIDATE_CONFIG", "GENERATION_CONFLICT")):
    SCENARIOS[f"write_failure_{_command}_{_error_name or 'none'}"] = (
        lambda contract, tmp_path, c=_command, e=_error_name: scenario_write_failures(contract, tmp_path, c, e))


def _normalise(trace: list[dict], tmp_path: Path) -> list[dict]:
    return json.loads(json.dumps(trace, ensure_ascii=False).replace(str(tmp_path), "<tmp>"))


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_viewmodel_split_keeps_behaviour(qtbot, contract, tmp_path, name):
    trace = _normalise(SCENARIOS[name](contract, tmp_path), tmp_path)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    if os.environ.get("BORING_UPDATE_SPLIT_LOCK") == "1":
        golden[name] = trace
        GOLDEN.write_text(json.dumps(golden, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")
        return
    assert name in golden, "golden trace missing; capture it on the pre-split code"
    expected = golden[name]
    for index, (actual_step, expected_step) in enumerate(zip(trace, expected)):
        assert actual_step == expected_step, f"{name} step {index} ({expected_step['step']})"
    assert len(trace) == len(expected)


# M-12: the write transaction claims only VALIDATE_CONFIG / SET_CONFIG /
# GET_CONFIG read-back. The write_failure_* golden traces above prove those
# are unchanged; before the split any other unclaimed failure (a stale
# GET_STATUS, a future command nobody routed) set the write state to
# FAILED "设备写入失败" even with no write in progress.
@pytest.mark.parametrize("command", ["GET_STATUS", "SOME_NEW_COMMAND"])
@pytest.mark.parametrize("writing", [False, True])
def test_unclaimed_command_failure_is_recorded_not_a_write_failure(qtbot, contract, command, writing):
    run = Run(contract)
    snapshot = _usb(contract)
    run.ready(snapshot)
    if writing:
        run.vm.rename_profile(snapshot.active_profile_id, "Lock candidate")
        run.vm.prepare_device_write()
    before = run.vm.write_transaction
    run.gateway.command_failed.emit(command, _error("UNSUPPORTED_COMMAND"))
    assert run.vm.write_transaction == before
    event = run.vm.diagnostics.events[-1]
    assert event.category == "UNCLAIMED_COMMAND_FAILURE"
    assert event.detail.startswith(f"{command}: ")
    run.finish()
