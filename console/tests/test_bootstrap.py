from __future__ import annotations

import json
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


def test_ble_auth_timeout_records_stage_and_stops_without_retry(contract, load_fixture):
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
    assert '暂停自动重连' in str(failures[0])
