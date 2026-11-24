"""A-03: protocol minor compatibility and tolerance of unknown response fields."""

from __future__ import annotations

import copy
import json

import pytest

from controller_config.protocol.bootstrap import BootstrapSession
from controller_config.protocol.framing import RESPONSE_FLAG, Frame


def _ack(request_id: int, payload: dict, *, minor: int = 0) -> Frame:
    return Frame(1, minor, 0x7E, RESPONSE_FLAG, request_id, json.dumps(payload).encode())


def _run_bootstrap(contract, load_fixture, *, device_minor: int, extra_fields: bool):
    sent, completed, failures = [], [], []
    session = BootstrapSession(
        contract=contract,
        port_name="cu.usbmodem-demo",
        send=lambda command, request_id: sent.append((command, request_id)),
        completed=completed.append,
        failed=failures.append,
    )
    hello = copy.deepcopy(load_fixture("hello-v1.json")["ack"])
    hello["result"]["versions"]["protocol_minor"] = device_minor
    capabilities = copy.deepcopy(load_fixture("capabilities-v1.json"))
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
    if extra_fields:
        for payload in (hello, capabilities, status, config_response):
            payload["result"]["future_minor_field"] = {"added_in": "1.x"}
        hello["result"]["identity"]["future_identity_hint"] = "x"
        capabilities["result"]["features"]["future_feature"] = True
        status["result"]["future_status"] = [1, 2, 3]
    session.start()
    for response in (hello, capabilities, status, config_response):
        session.accept(_ack(sent[-1][1], response, minor=device_minor))
    return completed, failures


@pytest.mark.parametrize("delta", [0, 1, 5])
def test_handshake_accepts_device_minor_equal_or_higher(contract, load_fixture, delta) -> None:
    completed, failures = _run_bootstrap(
        contract, load_fixture, device_minor=contract.protocol_minor + delta, extra_fields=False
    )
    assert not failures, failures and failures[0].technical
    assert len(completed) == 1


def test_handshake_accepts_device_minor_lower(contract, load_fixture, monkeypatch) -> None:
    # The client is at minor 0 today; emulate a newer client to cover "lower".
    newer_client = copy.copy(contract)
    object.__setattr__(newer_client, "protocol_minor", contract.protocol_minor + 2)
    completed, failures = _run_bootstrap(
        newer_client, load_fixture, device_minor=contract.protocol_minor, extra_fields=False
    )
    assert not failures, failures and failures[0].technical
    assert len(completed) == 1


def test_handshake_ignores_unknown_response_fields(contract, load_fixture) -> None:
    completed, failures = _run_bootstrap(
        contract, load_fixture, device_minor=contract.protocol_minor + 1, extra_fields=True
    )
    assert not failures, failures and failures[0].technical
    assert len(completed) == 1


def test_hello_rejects_non_integer_minor(contract, load_fixture) -> None:
    hello = copy.deepcopy(load_fixture("hello-v1.json")["ack"])
    hello["result"]["versions"]["protocol_minor"] = "1"
    with pytest.raises(Exception, match="protocol_minor"):
        contract.validate_hello(hello)
