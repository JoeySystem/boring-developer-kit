from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import (
    BootstrapKind,
    BootstrapSession,
    Command,
    CommandSession,
    StatusSession,
)
from controller_config.protocol.device_auth import (
    DeviceAuthenticationError,
    DeviceAuthenticator,
    DeviceTrust,
    DeviceTrustState,
    TrustBundle,
    TrustPolicy,
)
from controller_config.protocol.framing import ERROR_FLAG, RESPONSE_FLAG, Frame


def test_bootstrap_runs_required_read_sequence_and_builds_snapshot(contract, load_fixture) -> None:
    sent = []
    completed: list[DeviceSnapshot] = []
    failures = []
    session = BootstrapSession(
        contract=contract,
        port_name="cu.usbmodem-demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
    )
    hello = load_fixture("hello-v1.json")["ack"]
    config = load_fixture("config-v1.json")
    digest = hello["result"]["config"]["digest"]
    status = {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": digest},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "codex",
        },
    }
    config_response = {
        "command": "GET_CONFIG",
        "result": {"generation": 1, "digest": digest, "config": config},
    }
    capabilities = load_fixture("capabilities-v1.json")

    session.start()
    assert sent[-1][0].name == "HELLO"
    session.accept(_ack(sent[-1][1], hello))
    assert sent[-1][0].name == "CAPABILITIES"
    session.accept(_ack(sent[-1][1], capabilities))
    assert sent[-1][0].name == "GET_STATUS"
    session.accept(_ack(sent[-1][1], status))
    assert sent[-1][0].name == "GET_CONFIG"
    session.accept(_ack(sent[-1][1], config_response))

    assert not failures
    assert len(completed) == 1
    assert completed[0].profile_name == "Default"
    assert completed[0].config_is_synced
    assert len(completed[0].controls) == 15
    assert completed[0].trust.state is DeviceTrustState.DEVELOPMENT_UNAUTHENTICATED


def test_hello_read_incompatible_stops_before_status(contract, load_fixture) -> None:
    sent = []
    failures = []
    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["compatibility"] = {
        "read": False,
        "write": False,
        "reason": "unsupported_schema",
    }
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
    )
    session.start()
    session.accept(_ack(sent[-1][1], hello))
    assert len(sent) == 1
    assert failures[0].kind is BootstrapKind.INCOMPATIBLE


def test_hello_protocol_nack_is_incompatible(contract) -> None:
    sent = []
    failures = []
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
    )
    session.start()
    nack = {
        "command": "HELLO",
        "error": {"name": "UNSUPPORTED_PROTOCOL", "message": "major mismatch"},
    }
    session.accept(
        Frame(
            protocol_major=1,
            protocol_minor=0,
            message_type=0x7F,
            flags=RESPONSE_FLAG | ERROR_FLAG,
            request_id=sent[-1][1],
            payload_bytes=json.dumps(nack).encode(),
        )
    )

    assert failures[0].kind is BootstrapKind.INCOMPATIBLE
    assert len(sent) == 1


def test_malformed_hello_object_finishes_as_incompatible(contract) -> None:
    sent = []
    failures = []
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
    )
    session.start()
    malformed = {"command": "HELLO", "result": []}

    session.accept(_ack(sent[-1][1], malformed))

    assert session.finished
    assert failures[0].kind is BootstrapKind.INCOMPATIBLE


def test_get_config_timeout_is_read_failure(contract, load_fixture) -> None:
    sent = []
    failures = []
    hello = load_fixture("hello-v1.json")["ack"]
    status = _status(hello["result"]["config"]["digest"])
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
    )
    session.start()
    session.accept(_ack(sent[-1][1], hello))
    session.accept(_ack(sent[-1][1], load_fixture("capabilities-v1.json")))
    session.accept(_ack(sent[-1][1], status))
    assert sent[-1][0].name == "GET_CONFIG"
    session.timeout()
    assert failures[0].kind is BootstrapKind.READ_FAILED
    assert "GET_CONFIG" in failures[0].technical


def test_get_config_schema_violation_stops_before_capabilities(contract, load_fixture) -> None:
    sent = []
    failures = []
    hello = load_fixture("hello-v1.json")["ack"]
    digest = hello["result"]["config"]["digest"]
    status = _status(digest)
    config = load_fixture("config-v1.json")
    config["active_profile"] = 99
    config_response = {
        "command": "GET_CONFIG",
        "result": {"generation": 1, "digest": digest, "config": config},
    }
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
    )

    session.start()
    session.accept(_ack(sent[-1][1], hello))
    session.accept(_ack(sent[-1][1], load_fixture("capabilities-v1.json")))
    session.accept(_ack(sent[-1][1], status))
    session.accept(_ack(sent[-1][1], config_response))

    assert sent[-1][0].name == "GET_CONFIG"
    assert failures[0].kind is BootstrapKind.READ_FAILED
    assert "active_profile" in failures[0].technical


def test_status_session_reads_and_validates_runtime_mode(contract) -> None:
    sent = []
    completed = []
    failures = []
    session = StatusSession(
        contract=contract,
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
        request_id=42,
    )

    session.start()
    assert sent == [(session.current_command, 42)]
    session.accept(_ack(42, _status("a" * 64)))

    assert not failures
    assert completed[0]["operating_mode"] == "codex"


def test_command_session_returns_validated_ack_payload() -> None:
    sent = []
    completed = []
    failures = []
    command = Command("VALIDATE_CONFIG", 0x11, {"config": {}, "digest": "a" * 64})
    session = CommandSession(
        command=command,
        send=lambda item, request_id: sent.append((item, request_id)),
        completed=completed.append,
        failed=failures.append,
        request_id=91,
    )

    session.start()
    session.accept(_ack(91, {"command": "VALIDATE_CONFIG", "result": {}}))

    assert not failures
    assert completed == [{"command": "VALIDATE_CONFIG", "result": {}}]


def test_command_session_preserves_generation_conflict_details() -> None:
    failures = []
    command = Command("SET_CONFIG", 0x12, {})
    session = CommandSession(
        command=command,
        send=lambda _item, _request_id: None,
        completed=lambda _payload: None,
        failed=failures.append,
        request_id=92,
    )
    nack = {
        "command": "SET_CONFIG",
        "error": {
            "code": 10,
            "name": "GENERATION_CONFLICT",
            "message": "base_generation does not match",
            "details": {"current_generation": 8},
        },
    }

    session.start()
    session.accept(
        Frame(
            protocol_major=1,
            protocol_minor=0,
            message_type=0x7F,
            flags=RESPONSE_FLAG | ERROR_FLAG,
            request_id=92,
            payload_bytes=json.dumps(nack).encode(),
        )
    )

    assert failures[0].error_name == "GENERATION_CONFLICT"
    assert failures[0].details == {"current_generation": 8}


def test_command_session_retries_with_same_request_id_before_timeout() -> None:
    sent = []
    failures = []
    session = CommandSession(
        command=Command("FW_DATA", 0x41, {"offset": 0, "data": "AA=="}, retries=2),
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _payload: None,
        failed=failures.append,
        request_id=91,
    )

    session.start()
    session.timeout()
    session.timeout()

    assert [request_id for _command, request_id in sent] == [91, 91, 91]
    assert not failures

    session.timeout()
    assert failures[0].error_name == "TRANSPORT_TIMEOUT"


def test_command_session_timeout_is_distinct_from_device_nack() -> None:
    failures = []
    session = CommandSession(
        command=Command("SET_CONFIG", 0x12, {}),
        send=lambda _item, _request_id: None,
        completed=lambda _payload: None,
        failed=failures.append,
        request_id=93,
    )

    session.start()
    session.timeout()

    assert failures[0].error_name == "TRANSPORT_TIMEOUT"


def test_mapping_action_missing_from_capabilities_is_read_failure(contract, load_fixture) -> None:
    sent = []
    completed = []
    failures = []
    hello = load_fixture("hello-v1.json")["ack"]
    digest = hello["result"]["config"]["digest"]
    config = load_fixture("config-v1.json")
    config_response = {
        "command": "GET_CONFIG",
        "result": {"generation": 1, "digest": digest, "config": config},
    }
    capabilities = load_fixture("capabilities-v1.json")
    capabilities["result"]["actions"] = ["none"]
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
    )

    session.start()
    session.accept(_ack(sent[-1][1], hello))
    session.accept(_ack(sent[-1][1], capabilities))
    session.accept(_ack(sent[-1][1], _status(digest)))
    session.accept(_ack(sent[-1][1], config_response))

    assert not completed
    assert failures[0].kind is BootstrapKind.READ_FAILED
    assert "CAPABILITIES actions" in failures[0].technical


def test_production_policy_rejects_device_without_authentication_capability(
    contract, load_fixture
) -> None:
    sent = []
    failures = []
    authenticator = DeviceAuthenticator(
        TrustBundle.from_document(
            {"version": 1, "roots": []},
            policy=TrustPolicy.PRODUCTION,
        )
    )
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
        authenticator=authenticator,
    )

    session.start()
    session.accept(_ack(sent[-1][1], load_fixture("hello-v1.json")["ack"]))
    session.accept(_ack(sent[-1][1], load_fixture("capabilities-v1.json")))

    assert session.finished
    assert failures[0].kind is BootstrapKind.AUTHENTICITY_FAILED
    assert "设备认证能力" in failures[0].technical
    assert "恢复官方固件" in str(failures[0])
    assert [command.name for command, _request_id in sent] == ["HELLO", "CAPABILITIES"]


def test_authenticated_bootstrap_uses_contract_order_and_only_reads_device(
    contract, load_fixture
) -> None:
    sent = []
    completed = []
    failures = []
    nonce = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
    certificate = SimpleNamespace(serial="CP01-AABBCCDDEEFF", issuer_key_id="root")
    trust = DeviceTrust(
        DeviceTrustState.AUTHENTICATED,
        "BORING 设备已认证",
        "issuer=root",
        issuer_key_id="root",
        serial=certificate.serial,
    )

    class FakeAuthenticator:
        def new_nonce(self) -> str:
            return nonce

        def trust_without_device_identity(self, _technical: str) -> DeviceTrust:
            raise AssertionError("authenticated path must not use development fallback")

        def verify_certificate_response(
            self, payload, *, hello_identity, usb_serial
        ):
            assert payload["command"] == "AUTH_GET_CERTIFICATE"
            assert hello_identity["serial"] == certificate.serial
            assert usb_serial == certificate.serial
            return certificate

        def verify_challenge_response(self, payload, *, certificate: object, nonce: str):
            assert payload["command"] == "AUTH_CHALLENGE"
            assert certificate is not None
            assert nonce == payload["result"]["nonce"]
            return trust

    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["identity"]["serial"] = certificate.serial
    capabilities = load_fixture("capabilities-v1.json")
    capabilities["result"]["features"]["device_authentication"] = True
    digest = hello["result"]["config"]["digest"]
    config_response = {
        "command": "GET_CONFIG",
        "result": {
            "generation": 1,
            "digest": digest,
            "config": load_fixture("config-v1.json"),
        },
    }
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        usb_serial=certificate.serial,
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
        authenticator=FakeAuthenticator(),
    )

    responses = (
        hello,
        capabilities,
        {"command": "AUTH_GET_CERTIFICATE", "result": {}},
        {
            "command": "AUTH_CHALLENGE",
            "result": {"nonce": nonce},
        },
        _status(digest),
        config_response,
    )
    session.start()
    for response in responses:
        session.accept(_ack(sent[-1][1], response))

    assert not failures
    assert [command.name for command, _request_id in sent] == [
        "HELLO",
        "CAPABILITIES",
        "AUTH_GET_CERTIFICATE",
        "AUTH_CHALLENGE",
        "GET_STATUS",
        "GET_CONFIG",
    ]
    assert [command.message_type for command, _request_id in sent] == [
        0x01,
        0x02,
        0x04,
        0x05,
        0x13,
        0x10,
    ]
    assert sent[3][0].payload == {"nonce": nonce}
    assert completed[0].trust is trust


def test_shared_fixture_completes_authenticated_bootstrap_with_real_verifier(
    contract, load_fixture
) -> None:
    sent = []
    completed = []
    failures = []
    auth_fixture = load_fixture("device-auth-v1.json")
    certificate = auth_fixture["certificate_response"]["result"]["certificate"]
    root = {**auth_fixture["test_root"], "purpose": "test"}
    authenticator = DeviceAuthenticator(
        TrustBundle.from_document(
            {"version": 1, "roots": [root]},
            policy=TrustPolicy.DEVELOPMENT,
        ),
        nonce_factory=lambda: bytes(range(32)),
    )

    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["identity"].update(
        {
            "product_id": certificate["product_id"],
            "hardware_id": certificate["hardware_id"],
            "serial": certificate["serial"],
        }
    )
    capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")
    capabilities["result"]["features"]["device_authentication"] = True
    status = load_fixture("status-matrix12-power-v2-v1.json")
    active = status["result"]["active"]
    hello["result"]["config"] = dict(active)
    config_response = {
        "command": "GET_CONFIG",
        "result": {
            **active,
            "config": load_fixture("config-matrix12-power-v2-v1.json"),
        },
    }
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        usb_serial=certificate["serial"],
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
        authenticator=authenticator,
    )

    responses = (
        hello,
        capabilities,
        auth_fixture["certificate_response"],
        auth_fixture["challenge_response"],
        status,
        config_response,
    )
    session.start()
    for response in responses:
        session.accept(_ack(sent[-1][1], response))

    assert not failures
    assert [command.name for command, _request_id in sent] == [
        "HELLO",
        "CAPABILITIES",
        "AUTH_GET_CERTIFICATE",
        "AUTH_CHALLENGE",
        "GET_STATUS",
        "GET_CONFIG",
    ]
    assert sent[3][0].payload == auth_fixture["challenge_request"]
    assert len(completed) == 1
    assert completed[0].trust.state is DeviceTrustState.AUTHENTICATED
    assert completed[0].trust.serial == certificate["serial"]
    assert completed[0].config_is_synced


def test_authentication_failure_stops_before_status_and_config(contract, load_fixture) -> None:
    sent = []
    failures = []

    class RejectingAuthenticator:
        def new_nonce(self) -> str:
            return "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

        def verify_certificate_response(self, *_args, **_kwargs):
            raise DeviceAuthenticationError("certificate_signature", "设备证书签名验证失败")

    capabilities = load_fixture("capabilities-v1.json")
    capabilities["result"]["features"]["device_authentication"] = True
    session = BootstrapSession(
        contract=contract,
        port_name="demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _snapshot: None,
        failed=failures.append,
        authenticator=RejectingAuthenticator(),
    )

    session.start()
    session.accept(_ack(sent[-1][1], load_fixture("hello-v1.json")["ack"]))
    session.accept(_ack(sent[-1][1], capabilities))
    session.accept(
        _ack(
            sent[-1][1],
            {"command": "AUTH_GET_CERTIFICATE", "result": {}},
        )
    )

    assert failures[0].kind is BootstrapKind.AUTHENTICITY_FAILED
    assert failures[0].technical == "certificate_signature: 设备证书签名验证失败"
    assert [command.name for command, _request_id in sent] == [
        "HELLO",
        "CAPABILITIES",
        "AUTH_GET_CERTIFICATE",
    ]


def _ack(request_id: int, payload: dict) -> Frame:
    return Frame(
        protocol_major=1,
        protocol_minor=0,
        message_type=0x7E,
        flags=0x01,
        request_id=request_id,
        payload_bytes=json.dumps(payload).encode(),
    )


def _status(digest: str) -> dict:
    return {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": digest},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "codex",
        },
    }


def test_known_unsafe_ble_builds_stop_at_hello_but_usb_and_fixed_build_continue(contract, load_fixture):
    for build, port, blocked in (
        ('20260907.16-g7989ccbf-dirty', 'ble:test', True),
        ('20260909.08-g7989ccbf-dirty', 'ble:test', True),
        ('20260907.16-g7989ccbf-dirty', 'cu.usbmodem-test', False),
        ('20260910.05-g7989ccbf-dirty', 'ble:test', False),
    ):
        sent, failures, completed = [], [], []
        session = BootstrapSession(contract=contract, port_name=port,
            send=lambda command, request_id: sent.append((command, request_id)),
            completed=completed.append, failed=failures.append)
        hello = load_fixture('hello-v1.json')['ack']
        hello['result']['identity']['hardware_id'] = 'WMP-S3-MATRIX12-POWER-V2'
        hello['result']['versions']['build_id'] = build
        session.start()
        session.accept(_ack(sent[-1][1], hello))
        assert not completed
        if blocked:
            assert [c.name for c, _ in sent] == ['HELLO']
            assert failures[0].kind is BootstrapKind.FIRMWARE_UPDATE_REQUIRED
            assert build in failures[0].technical
            assert 'USB' in str(failures[0])
        else:
            assert not failures
            assert sent[-1][0].name == 'CAPABILITIES'


def test_ble_auth_timeout_records_stage_for_fresh_connection_retry(contract, load_fixture):
    sent, failures = [], []
    session = BootstrapSession(contract=contract, port_name='ble:test',
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=lambda _: None, failed=failures.append)
    session.start()
    hello = load_fixture('hello-v1.json')['ack']
    hello['result']['versions']['build_id'] = '20260910.05-g7989ccbf-dirty'
    session.accept(_ack(sent[-1][1], hello))
    caps = load_fixture('capabilities-v1.json')
    caps['result']['features']['device_authentication'] = True
    session.accept(_ack(sent[-1][1], caps))
    assert session.authenticating_over_ble
    assert sent[-1][0].name == 'AUTH_GET_CERTIFICATE'
    count = len(sent)
    session.timeout()
    session.timeout()
    assert len(sent) == count
    assert len(failures) == 1
    assert failures[0].kind is BootstrapKind.AUTHENTICATION_INTERRUPTED
    assert 'AUTH_GET_CERTIFICATE' in failures[0].technical
    assert '20260910.05' in failures[0].technical
    assert '重新连接' in str(failures[0])


def _production_maintenance_session(contract, load_fixture, *, hello_change=None,
                                    caps_change=None, port_name="usb:test"):
    sent, completed, failures = [], [], []
    fixture = load_fixture("device-auth-v1.json")
    certificate = fixture["certificate_response"]["result"]["certificate"]
    authenticator = DeviceAuthenticator(
        TrustBundle.from_document(
            {"version": 1, "roots": [{**fixture["test_root"], "purpose": "production"}]},
            policy=TrustPolicy.PRODUCTION,
        ), nonce_factory=lambda: bytes(range(32)),
    )
    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["identity"].update({key: certificate[key]
                                         for key in ("product_id", "hardware_id", "serial")})
    if hello_change:
        hello_change(hello["result"])
    caps = load_fixture("capabilities-matrix12-power-v2-v1.json")
    caps["result"]["features"]["device_authentication"] = True
    if caps_change:
        caps_change(caps["result"])
    session = BootstrapSession(
        contract=contract, port_name=port_name, usb_serial=certificate["serial"],
        authenticator=authenticator,
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append, failed=failures.append,
    )
    session.start()
    for response in (hello, caps, fixture["certificate_response"], fixture["challenge_response"]):
        if session.finished:
            break
        session.accept(_ack(sent[-1][1], response))
    return session, sent, completed, failures, hello


def _maintenance_ready(session, sent):
    assert sent[-1][0].name == "FW_STATUS"
    session.accept(_ack(sent[-1][1], {
        "command": "FW_STATUS", "result": {"firmware_update": {
            "state": "IDLE", "running_partition": "ota_0", "rollback_pending": False,
            "running_version": session._responses["HELLO"]["result"]["versions"]["firmware"],
        }}
    }))


@pytest.mark.parametrize("failed_command, failure", [
    ("GET_STATUS", "timeout"), ("GET_STATUS", "nack"),
    ("GET_STATUS", "schema"), ("GET_CONFIG", "timeout"),
    ("GET_CONFIG", "nack"), ("GET_CONFIG", "schema"),
    ("GET_CONFIG", "mapping"),
])
def test_authenticated_usb_read_failure_can_establish_ota_only_session(
    contract, load_fixture, failed_command, failure,
):
    session, sent, completed, failures, hello = _production_maintenance_session(contract, load_fixture)
    if failed_command == "GET_CONFIG":
        session.accept(_ack(sent[-1][1], _status(hello["result"]["config"]["digest"])))
    assert sent[-1][0].name == failed_command
    if failure == "timeout":
        session.timeout()
    elif failure == "nack":
        session.accept(Frame(1, 0, 0x7F, RESPONSE_FLAG | ERROR_FLAG, sent[-1][1], json.dumps({
            "command": failed_command, "error": {"name": "INTERNAL", "message": "old config unreadable"}
        }).encode()))
    elif failure == "mapping":
        config = load_fixture("config-matrix12-power-v2-v1.json")
        # Supported JSON schema, but not an action this particular firmware declares.
        session._responses["CAPABILITIES"]["result"]["actions"] = ["none"]
        session.accept(_ack(sent[-1][1], {"command": "GET_CONFIG", "result": {
            **hello["result"]["config"], "config": config,
        }}))
    else:
        session.accept(_ack(sent[-1][1], {"command": failed_command, "result": {}}))
    assert not failures and not completed
    _maintenance_ready(session, sent)
    assert not failures
    snapshot = completed[0]
    assert snapshot.maintenance_only and snapshot.maintenance_reason
    assert snapshot.trust.is_authenticated
    assert snapshot.identity == hello["result"]["identity"]
    assert snapshot.versions == hello["result"]["versions"]
    assert snapshot.config_result == {} and snapshot.config == {}
    assert not snapshot.config_is_synced


@pytest.mark.parametrize("change", [
    lambda result: result["versions"].update(schema_version=2),
    lambda result: result["compatibility"].update(read=False, write=False, reason="old_schema"),
    lambda result: result["compatibility"].update(write=False, reason="read_only"),
])
def test_authenticated_usb_config_compatibility_can_enter_maintenance(contract, load_fixture, change):
    session, sent, completed, failures, _ = _production_maintenance_session(
        contract, load_fixture, hello_change=change,
    )
    _maintenance_ready(session, sent)
    assert not failures and completed[0].maintenance_only
    assert "GET_CONFIG" not in [command.name for command, _ in sent]


@pytest.mark.parametrize("failure", ["nack", "invalid", "timeout"])
def test_maintenance_probe_failure_keeps_connection_failed(contract, load_fixture, failure):
    session, sent, completed, failures, _ = _production_maintenance_session(contract, load_fixture)
    session.timeout()  # Authenticated GET_STATUS failure initiates FW_STATUS probe.
    assert sent[-1][0].name == "FW_STATUS"
    if failure == "timeout":
        session.timeout()
    elif failure == "invalid":
        session.accept(_ack(sent[-1][1], {"command": "FW_STATUS", "result": {}}))
    else:
        session.accept(Frame(1, 0, 0x7F, RESPONSE_FLAG | ERROR_FLAG, sent[-1][1], json.dumps({
            "command": "FW_STATUS", "error": {"name": "UNKNOWN_COMMAND", "message": "unsupported"}
        }).encode()))
    assert not completed and len(failures) == 1
    assert "GET_STATUS" in failures[0].technical
    assert "FW_STATUS" in failures[0].technical


@pytest.mark.parametrize("change", [
    lambda caps: caps["features"].update(firmware_update=False),
    lambda caps: caps["limits"].pop("firmware_chunk_bytes"),
    lambda caps: caps["limits"].update(firmware_image_bytes=0),
])
def test_authenticated_without_ota_capability_or_limits_cannot_enter_maintenance(
    contract, load_fixture, change,
):
    session, sent, completed, failures, _ = _production_maintenance_session(
        contract, load_fixture, caps_change=change,
    )
    session.timeout()
    assert not completed and len(failures) == 1
    assert "FW_STATUS" not in [command.name for command, _ in sent]


def test_authenticated_ble_read_failure_keeps_original_failure(contract, load_fixture):
    session, sent, completed, failures, _ = _production_maintenance_session(
        contract, load_fixture, port_name="ble:test",
    )
    session.timeout()
    assert not completed and len(failures) == 1
    assert sent[-1][0].name == "GET_STATUS"


@pytest.mark.parametrize("change", [
    lambda result: result["versions"].update(protocol_major=2),
    lambda result: result["identity"].update(hardware_id="UNKNOWN"),
    lambda result: result["versions"].update(schema_version="2"),
])
def test_config_maintenance_does_not_relax_hello_identity_or_protocol(
    contract, load_fixture, change,
):
    session, sent, completed, failures, _ = _production_maintenance_session(
        contract, load_fixture, hello_change=change,
    )
    assert session.finished and not completed
    assert failures[0].kind is BootstrapKind.INCOMPATIBLE
    assert [command.name for command, _ in sent] == ["HELLO"]


def test_config_maintenance_never_accepts_a_different_authenticated_device(contract, load_fixture):
    session, sent, completed, failures, _ = _production_maintenance_session(
        contract, load_fixture,
        hello_change=lambda result: result["identity"].update(serial="CP01-112233445566"),
    )
    assert session.finished and not completed
    assert failures[0].kind is BootstrapKind.AUTHENTICITY_FAILED
    assert "FW_STATUS" not in [command.name for command, _ in sent]


def test_ble_read_only_configuration_still_connects_as_before(contract, load_fixture):
    session, sent, completed, failures, hello = _production_maintenance_session(
        contract, load_fixture, port_name="ble:test",
        hello_change=lambda result: result["compatibility"].update(write=False),
    )
    session.accept(_ack(sent[-1][1], _status(hello["result"]["config"]["digest"])))
    session.accept(_ack(sent[-1][1], {"command": "GET_CONFIG", "result": {
        **hello["result"]["config"], "config": load_fixture("config-matrix12-power-v2-v1.json"),
    }}))
    assert not failures
    assert completed[0].is_read_only and not completed[0].maintenance_only


def test_hello_config_relaxation_is_explicit_and_only_relaxes_schema(contract, load_fixture):
    from controller_config.protocol.contract import ContractError

    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["versions"]["schema_version"] = 2
    with pytest.raises(ContractError, match="schema"):
        contract.validate_hello(hello)
    contract.validate_hello(hello, allow_config_incompatible=True)
    hello["result"]["config"] = {}
    with pytest.raises(ContractError):
        contract.validate_hello(hello, allow_config_incompatible=True)


@pytest.mark.parametrize("update", [
    {"state": "IDLE"},
    {"state": "IDLE", "running_partition": "factory", "running_version": "0.1.0-alpha.1", "rollback_pending": False},
    {"state": "IDLE", "running_partition": "ota_0", "running_version": "different-version", "rollback_pending": False},
    {"state": "IDLE", "running_partition": "ota_0", "running_version": "0.1.0-alpha.1", "rollback_pending": "false"},
])
def test_maintenance_probe_requires_running_image_and_rollback_fields(contract, load_fixture, update):
    session, sent, completed, failures, _ = _production_maintenance_session(contract, load_fixture)
    session.timeout()
    assert sent[-1][0].name == "FW_STATUS"
    session.accept(_ack(sent[-1][1], {"command": "FW_STATUS", "result": {"firmware_update": update}}))
    assert session.finished and not completed and len(failures) == 1
    assert "FW_STATUS 验证失败" in failures[0].technical
