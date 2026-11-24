"""SH-005: skip the Bluetooth GET_CONFIG when the device's configuration is
already saved on this computer, and time each connection step."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from controller_config.config_cache import DeviceConfigCache, config_digest
from controller_config.connect_timing import TimingMark, timing_report, timing_summary
from controller_config.diagnostics import DiagnosticsSession
from controller_config.models import AppState, PortCandidate, ScreenModel
from controller_config.protocol.bootstrap import BootstrapKind, BootstrapSession
from controller_config.protocol.device_auth import (
    DeviceAuthenticationError,
    DeviceAuthenticator,
    DeviceTrust,
    DeviceTrustState,
    TrustBundle,
    TrustPolicy,
)
from controller_config.protocol.framing import Frame, canonical_json_bytes
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from test_write_transaction import FakeWriteGateway, _ack as _command_ack, _active_status


# ---- cache file --------------------------------------------------------------


def _result(config: dict, generation: int = 3) -> dict:
    return {"generation": generation, "digest": config_digest(config), "config": config}


def test_cache_round_trip_and_mismatches(tmp_path, load_fixture) -> None:
    cache = DeviceConfigCache(tmp_path / "cache")
    config = load_fixture("config-matrix12-power-v2-v1.json")
    result = _result(config, 17)

    assert cache.lookup("SN1", 17, result["digest"]) is None  # nothing saved yet
    assert cache.store("SN1", result)
    assert cache.lookup("SN1", 17, result["digest"]) == result
    assert cache.lookup("SN1", 18, result["digest"]) is None  # generation differs
    assert cache.lookup("SN1", 17, "0" * 64) is None  # digest differs
    assert cache.lookup("SN2", 17, result["digest"]) is None  # another device


def test_cache_refuses_results_whose_config_does_not_hash_to_the_digest(tmp_path, load_fixture) -> None:
    cache = DeviceConfigCache(tmp_path)
    config = load_fixture("config-matrix12-power-v2-v1.json")
    assert not cache.store("SN1", {"generation": 1, "digest": "a" * 64, "config": config})
    assert not cache.store("", _result(config))
    assert not list(tmp_path.iterdir())


def test_cache_ignores_a_tampered_or_damaged_file(tmp_path, load_fixture) -> None:
    cache = DeviceConfigCache(tmp_path)
    config = load_fixture("config-matrix12-power-v2-v1.json")
    result = _result(config)
    cache.store("SN1", result)
    path = cache.path_for("SN1")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["result"]["config"]["active_profile"] = 99  # digest no longer matches
    path.write_text(json.dumps(document), encoding="utf-8")
    assert cache.lookup("SN1", result["generation"], result["digest"]) is None
    path.write_text("{not json", encoding="utf-8")
    assert cache.lookup("SN1", result["generation"], result["digest"]) is None


def test_console_digest_matches_the_protocol_fixture(load_fixture) -> None:
    # The fixture's digest comes from the firmware's RFC 8785 JCS bytes.
    config = load_fixture("config-matrix12-power-v2-v1.json")
    status = load_fixture("status-matrix12-power-v2-v1.json")
    assert config_digest(config) == status["result"]["active"]["digest"]


# ---- handshake ---------------------------------------------------------------


def _frame(request_id: int, payload: dict) -> Frame:
    return Frame(1, 0, 0x7E, 0x01, request_id, json.dumps(payload).encode())


class _SpyCache(DeviceConfigCache):
    def __init__(self, directory) -> None:
        super().__init__(directory)
        self.lookups = []

    def lookup(self, serial, generation, digest):
        self.lookups.append((serial, generation, digest))
        return super().lookup(serial, generation, digest)


class _Handshake:
    """An authenticated BLE handshake on the shared protocol fixtures."""

    def __init__(self, contract, load_fixture, cache, *, port_name="ble:test", authenticator=None):
        self.fixture = load_fixture("device-auth-v1.json")
        certificate = self.fixture["certificate_response"]["result"]["certificate"]
        self.serial = certificate["serial"]
        root = {**self.fixture["test_root"], "purpose": "test"}
        self.authenticator = authenticator or DeviceAuthenticator(
            TrustBundle.from_document({"version": 1, "roots": [root]}, policy=TrustPolicy.DEVELOPMENT),
            nonce_factory=lambda: bytes(range(32)),
        )
        self.hello = load_fixture("hello-v1.json")["ack"]
        self.hello["result"]["identity"].update(
            {key: certificate[key] for key in ("product_id", "hardware_id", "serial")})
        self.capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")
        self.capabilities["result"]["features"]["device_authentication"] = True
        self.status = load_fixture("status-matrix12-power-v2-v1.json")
        self.config = load_fixture("config-matrix12-power-v2-v1.json")
        active = self.status["result"]["active"]
        self.hello["result"]["config"] = dict(active)
        self.config_result = {**active, "config": copy.deepcopy(self.config)}
        self.sent, self.completed, self.failures, self.steps = [], [], [], []
        self.session = BootstrapSession(
            contract=contract, port_name=port_name, usb_serial=self.serial,
            send=lambda command, request_id: self.sent.append((command, request_id)),
            completed=self.completed.append, failed=self.failures.append,
            authenticator=self.authenticator, config_cache=cache,
            step_completed=lambda step, detail: self.steps.append((step, detail)),
        )

    def run(self) -> list[str]:
        responses = {
            "HELLO": self.hello,
            "CAPABILITIES": self.capabilities,
            "AUTH_GET_CERTIFICATE": self.fixture["certificate_response"],
            "AUTH_CHALLENGE": self.fixture["challenge_response"],
            "GET_STATUS": self.status,
            "GET_CONFIG": {"command": "GET_CONFIG", "result": self.config_result},
        }
        self.session.start()
        while not self.session.finished:
            command, request_id = self.sent[-1]
            self.session.accept(_frame(request_id, responses[command.name]))
        return [command.name for command, _request_id in self.sent]


FULL_SEQUENCE = ["HELLO", "CAPABILITIES", "AUTH_GET_CERTIFICATE", "AUTH_CHALLENGE",
                 "GET_STATUS", "GET_CONFIG"]


def test_cached_config_skips_get_config_when_hello_digest_matches(contract, load_fixture, tmp_path) -> None:
    cache = DeviceConfigCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache)
    assert cache.store(handshake.serial, handshake.config_result)

    assert handshake.run() == FULL_SEQUENCE[:-1]

    assert not handshake.failures
    snapshot = handshake.completed[0]
    assert snapshot.config == handshake.config
    assert snapshot.config_result == handshake.config_result
    assert snapshot.config_is_synced
    assert snapshot.trust.is_authenticated
    assert handshake.session.config_source == "cache"
    assert handshake.steps[-1] == ("GET_CONFIG", "skipped: cached config matches HELLO digest")


def test_digest_mismatch_reads_config_from_device(contract, load_fixture, tmp_path) -> None:
    cache = DeviceConfigCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache)
    stale_config = copy.deepcopy(handshake.config)
    stale_config["profiles"][0]["name"] = "Before the last write"
    stale = _result(stale_config, handshake.config_result["generation"])
    assert stale["digest"] != handshake.config_result["digest"]
    assert cache.store(handshake.serial, stale)

    assert handshake.run() == FULL_SEQUENCE
    assert handshake.completed[0].config == handshake.config
    assert handshake.session.config_source == "device"


def test_without_cache_entry_reads_config_from_device(contract, load_fixture, tmp_path) -> None:
    handshake = _Handshake(contract, load_fixture, DeviceConfigCache(tmp_path))
    assert handshake.run() == FULL_SEQUENCE
    assert handshake.session.config_source == "device"
    assert [step for step, _detail in handshake.steps] == FULL_SEQUENCE
    assert all(detail.endswith(" bytes") for _step, detail in handshake.steps)


def test_status_disagreeing_with_hello_reads_config_from_device(contract, load_fixture, tmp_path) -> None:
    cache = DeviceConfigCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache)
    cache.store(handshake.serial, handshake.config_result)
    handshake.status["result"]["active"] = {"generation": 99, "digest": "b" * 64}
    assert handshake.run()[-1] == "GET_CONFIG"


def test_authentication_failure_never_consults_the_cache(contract, load_fixture, tmp_path) -> None:
    class RejectingAuthenticator:
        def new_nonce(self) -> str:
            return "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

        def verify_certificate_response(self, *_args, **_kwargs):
            raise DeviceAuthenticationError("certificate_signature", "设备证书签名验证失败")

    cache = _SpyCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache, authenticator=RejectingAuthenticator())
    cache.store(handshake.serial, handshake.config_result)

    assert handshake.run() == FULL_SEQUENCE[:3]
    assert handshake.failures[0].kind is BootstrapKind.AUTHENTICITY_FAILED
    assert not handshake.completed
    assert cache.lookups == []


def test_unauthenticated_development_device_does_not_use_the_cache(contract, load_fixture, tmp_path) -> None:
    cache = _SpyCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache)
    handshake.capabilities["result"]["features"]["device_authentication"] = False
    cache.store(handshake.serial, handshake.config_result)

    assert handshake.run() == ["HELLO", "CAPABILITIES", "GET_STATUS", "GET_CONFIG"]
    assert not handshake.completed[0].trust.is_authenticated
    assert cache.lookups == []


def test_cached_config_that_no_longer_fits_capabilities_is_read_again(contract, load_fixture, tmp_path) -> None:
    cache = DeviceConfigCache(tmp_path)
    handshake = _Handshake(contract, load_fixture, cache)
    cache.store(handshake.serial, handshake.config_result)
    # Firmware dropped an action the saved configuration uses.
    used = {mapping["action"]["type"]
            for profile in handshake.config["profiles"] for mapping in profile["mappings"]}
    handshake.capabilities["result"]["actions"] = [
        action for action in handshake.capabilities["result"]["actions"] if action not in used]
    handshake.run()
    assert handshake.sent[-1][0].name == "GET_CONFIG"
    assert handshake.session.config_source != "cache"


# ---- view model: saving confirmed configurations -----------------------------


def _authenticated(snapshot):
    serial = str(snapshot.identity["serial"])
    # Authenticated sessions also read the BLE name and Agent-key setting;
    # those reads would keep the write path busy and are not under test here.
    features = {**snapshot.capabilities.get("features", {}),
                "ble_name": False, "normal_agent_key_behavior": False}
    return replace(
        snapshot,
        capabilities={**snapshot.capabilities, "features": features},
        trust=DeviceTrust(DeviceTrustState.AUTHENTICATED, "BORING 设备已认证", "test",
                          issuer_key_id="test", serial=serial),
    )


@pytest.fixture
def cached_view_model(qtbot, contract, tmp_path):
    gateway = FakeWriteGateway()
    gateway.config_cache = DeviceConfigCache(tmp_path / "config-cache")
    view_model = MainViewModel(gateway, contract)
    yield view_model, gateway
    view_model.shutdown()


def test_authenticated_snapshot_is_saved_and_unauthenticated_is_not(cached_view_model, contract) -> None:
    view_model, gateway = cached_view_model
    cache = gateway.config_cache
    snapshot = _power_v2_snapshot(contract, read_only=False)
    serial = str(snapshot.identity["serial"])
    revision = (snapshot.config_result["generation"], snapshot.config_result["digest"])

    gateway.snapshot_ready.emit(snapshot)  # development trust
    assert cache.lookup(serial, *revision) is None

    gateway.snapshot_ready.emit(_authenticated(snapshot))
    assert cache.lookup(serial, *revision)["config"] == snapshot.config


def test_confirmed_write_updates_the_cache(cached_view_model, contract) -> None:
    view_model, gateway = cached_view_model
    cache = gateway.config_cache
    snapshot = _authenticated(_power_v2_snapshot(contract, read_only=False))
    serial = snapshot.trust.serial
    gateway.snapshot_ready.emit(snapshot)
    view_model.rename_profile(snapshot.active_profile_id, "Cached after write")

    view_model.prepare_device_write()
    gateway.command_completed.emit("VALIDATE_CONFIG", _command_ack("VALIDATE_CONFIG"))
    view_model.confirm_device_write()
    gateway.command_completed.emit("SET_CONFIG", _command_ack("SET_CONFIG"))
    written = copy.deepcopy(view_model.write_transaction.candidate_config)
    digest = hashlib.sha256(canonical_json_bytes(written)).hexdigest()
    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    gateway.command_completed.emit("GET_CONFIG", _command_ack(
        "GET_CONFIG", {"generation": generation, "digest": digest, "config": written}))

    cached = cache.lookup(serial, generation, digest)
    assert cached is not None and cached["config"] == written
    assert cache.lookup(serial, snapshot.config_result["generation"],
                        snapshot.config_result["digest"]) is None


# ---- connection timing -------------------------------------------------------


def test_timing_report_lists_each_step() -> None:
    marks = (TimingMark("scan_start", 10.0), TimingMark("device_found", 10.05, "bluetooth"),
             TimingMark("connect", 10.06, "bluetooth"), TimingMark("channel_open", 10.2),
             TimingMark("GET_CONFIG", 10.2, "skipped: cached"), TimingMark("ui_ready", 10.25))
    report = timing_report(marks)
    assert report["total_ms"] == 250
    assert "T" in report["started_at"]
    assert [step["step"] for step in report["steps"]] == [
        "scan_start", "device_found", "connect", "channel_open", "GET_CONFIG", "ui_ready"]
    assert report["steps"][3] == {"step": "channel_open", "ms": 140, "at_ms": 200}
    assert report["steps"][1]["detail"] == "bluetooth"
    assert timing_summary(marks).startswith("total=250ms device_found=50ms")


def test_view_model_puts_the_connection_timeline_in_diagnostics(
        cached_view_model, contract, monkeypatch, tmp_path) -> None:
    log = tmp_path / "timing.jsonl"
    monkeypatch.setenv("BORING_CONNECT_TIMING_LOG", str(log))
    view_model, gateway = cached_view_model
    snapshot = _authenticated(_power_v2_snapshot(contract, read_only=False))
    view_model.refresh()
    gateway.candidates_found.emit((PortCandidate(snapshot.port_name),))
    worker_marks = (TimingMark("connect", 1.0, "usb"), TimingMark("channel_open", 1.1),
                    TimingMark("HELLO", 1.2, "300 bytes"))
    gateway.snapshot_ready.emit(replace(snapshot, connect_timing=worker_marks))

    report = json.loads(view_model.diagnostic_report_text())
    steps = [step["step"] for step in report["connection_timing"]["steps"]]
    assert steps == ["scan_start", "device_found", "connect", "channel_open", "HELLO", "ui_ready"]
    assert any(event["category"] == "CONNECT_TIMING" for event in report["events"])
    logged = json.loads(log.read_text(encoding="utf-8"))
    assert logged["port"] == snapshot.port_name and logged["steps"][-1]["step"] == "ui_ready"


def test_snapshot_timing_does_not_change_equality(contract) -> None:
    snapshot = _power_v2_snapshot(contract, read_only=False)
    assert replace(snapshot, connect_timing=(TimingMark("connect", 1.0),)) == snapshot


def test_diagnostics_without_a_connection_has_no_timing() -> None:
    report = DiagnosticsSession().report(ScreenModel(AppState.NO_DEVICE))
    assert report["connection_timing"] is None


def test_worker_attaches_its_timeline_and_passes_the_cache_to_the_handshake(qtbot, contract, tmp_path) -> None:
    from controller_config.transport.qt_serial import SerialWorker

    cache = DeviceConfigCache(tmp_path)
    worker = SerialWorker(contract, config_cache=cache)
    sessions = []
    worker._send_command = lambda command, request_id: sessions.append(worker._session)
    worker._serial = object()
    worker._timing = [TimingMark("connect", 1.0, "usb")]
    worker._start_bootstrap("/dev/cu.test")
    assert sessions and sessions[0]._config_cache is cache
    assert [mark.step for mark in worker._timing] == ["connect", "channel_open"]

    ready = []
    worker.snapshot_ready.connect(ready.append)
    worker._on_bootstrap_complete(_power_v2_snapshot(contract, read_only=False))
    assert [mark.step for mark in ready[0].connect_timing] == ["connect", "channel_open"]
    worker._serial = None
    worker.close()


def test_technical_details_show_the_last_connection_timing(contract) -> None:
    from controller_config.views.main_window import _technical_details_text

    snapshot = _power_v2_snapshot(contract, read_only=False)
    assert "连接耗时" not in _technical_details_text(snapshot)
    text = _technical_details_text(snapshot, (TimingMark("connect", 1.0), TimingMark("ui_ready", 1.5)))
    assert "连接耗时       total=500ms ui_ready=500ms" in text
