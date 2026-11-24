"""`desktop` source wire format, as the hub uses it, against every shared
fixture in software/protocol/fixtures/desktop_agent/ (PR #49 on main).
"""
import copy
import json
from pathlib import Path

import pytest

from controller_config.desktop_agent_hub import (
    clear_status_command, codex_status_age_ms, status_command,
    validate_clear_request, validate_navigation, validate_press,
    validate_readback, validate_response, validate_set_request,
)
from controller_config.protocol import contract as contract_module
from controller_config.protocol.bootstrap import ACK, CommandSession
from controller_config.protocol.framing import (
    HEADER, RESPONSE_FLAG, FrameDecoder, encode_request,
)

FIXTURES = Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "desktop_agent"
VALID = sorted((FIXTURES / "valid").glob("*.json"))
INVALID = sorted((FIXTURES / "invalid").glob("*.json"))
COVERED = {"ack-set.json", "ack-clear.json", "clear.json", "get-status-excerpt.json"}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_every_fixture_is_covered():
    files = {str(p.relative_to(FIXTURES)) for p in FIXTURES.rglob("*.json")}
    expected = COVERED | {f"valid/{p.name}" for p in VALID} | {f"invalid/{p.name}" for p in INVALID}
    assert files == expected
    assert len(VALID) == 3 and len(INVALID) == 9


def test_contract_constants_match_protocol():
    excerpt = load(FIXTURES / "get-status-excerpt.json")
    assert contract_module.SET_AGENT_STATUS_TYPE == 0x28
    assert contract_module.CLEAR_AGENT_STATUS_TYPE == 0x29
    assert contract_module.DESKTOP_AGENT_SOURCE == "desktop"
    assert set(contract_module.DESKTOP_AGENT_STATES) == {
        "unknown", "idle", "standby", "working", "completed", "approval", "reply", "error", "codex"}
    assert contract_module.DESKTOP_AGENT_SLOT_KEYS == (1, 2, 4, 5, 6, 7)
    assert {contract_module.STATUS_DESKTOP_AGENT_STATUS, contract_module.STATUS_DESKTOP_AGENT_PRESS,
            contract_module.STATUS_DESKTOP_NAVIGATION, "codex_micro"} == set(excerpt)
    assert excerpt["desktop_agent_status"]["lease_ms"] == contract_module.DESKTOP_AGENT_LEASE_MS
    assert contract_module.STATUS_CODEX_STATUS_AGE_MS in excerpt["codex_micro"]


def _round_trip(contract, command):
    raw = encode_request(protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor,
                         message_type=command.message_type, request_id=7, payload=command.payload,
                         max_payload_bytes=contract.max_payload_bytes)
    decoder = FrameDecoder(max_payload_bytes=contract.max_payload_bytes,
                           protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor)
    (frame,) = decoder.feed(raw)
    assert frame.message_type == command.message_type and frame.request_id == 7
    return frame.json_payload()


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_set_requests_encode_exactly(contract, path):
    payload = load(path)
    validate_set_request(payload)
    command = status_command(payload["states"], payload["completion_seq"],
                             encoder_navigation=payload.get("encoder_navigation", False))
    assert command.name == "SET_AGENT_STATUS" and command.message_type == 0x28
    assert command.payload == payload
    assert _round_trip(contract, command) == payload


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_set_requests_are_rejected(path):
    payload = load(path)
    with pytest.raises(ValueError):
        validate_set_request(payload)
    if set(payload) <= {"source", "states", "completion_seq"} and payload["source"] == "desktop":
        # The hub can never build these either.
        with pytest.raises(ValueError):
            status_command(payload["states"], payload["completion_seq"])


def test_clear_request_matches_fixture(contract):
    command = clear_status_command()
    assert command.message_type == 0x29 and command.payload == load(FIXTURES / "clear.json")
    validate_clear_request(command.payload)
    assert _round_trip(contract, command) == command.payload
    for bad in ({"source": "doubao"}, {"source": "desktop", "states": []}, {}):
        with pytest.raises(ValueError):
            validate_clear_request(bad)


def _ack_frame(contract, request_id, payload):
    raw = encode_request(protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor,
                         message_type=ACK, request_id=request_id, payload=payload,
                         max_payload_bytes=contract.max_payload_bytes)
    header = list(HEADER.unpack_from(raw))
    header[4] = RESPONSE_FLAG
    decoder = FrameDecoder(max_payload_bytes=contract.max_payload_bytes,
                           protocol_major=contract.protocol_major, protocol_minor=contract.protocol_minor)
    (frame,) = decoder.feed(HEADER.pack(*header) + raw[HEADER.size:])
    return frame


@pytest.mark.parametrize("name, command", [
    ("ack-set.json", status_command(["idle"] * 6, [0] * 6)),
    ("ack-clear.json", clear_status_command()),
])
def test_acks_parse_through_a_command_session(contract, name, command):
    ack = load(FIXTURES / name)
    assert validate_response(command, ack) == 5000
    done, failed = [], []
    session = CommandSession(command=command, send=lambda *_: None, completed=done.append,
                             failed=failed.append, request_id=9,
                             validator=lambda payload: validate_response(command, payload))
    session.start()
    session.accept(_ack_frame(contract, 9, ack))
    assert done == [ack] and failed == []


def test_mismatched_or_malformed_acks_are_rejected():
    set_ack, clear_ack = load(FIXTURES / "ack-set.json"), load(FIXTURES / "ack-clear.json")
    set_command, clear_command = status_command(["idle"] * 6, [0] * 6), clear_status_command()
    with pytest.raises(ValueError):
        validate_response(clear_command, set_ack)
    with pytest.raises(ValueError):
        validate_response(set_command, clear_ack)
    for path, value in ((("result", "source"), "doubao"), (("result", "active"), 1),
                        (("result", "lease_ms"), 4000), (("result", "lease_ms"), 5000.0),
                        (("command",), "CLEAR_AGENT_STATUS")):
        broken = copy.deepcopy(set_ack)
        target = broken
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        with pytest.raises(ValueError):
            validate_response(set_command, broken)
    with pytest.raises(ValueError):
        validate_response(set_command, {"command": "SET_AGENT_STATUS"})


def test_get_status_excerpt_parses():
    excerpt = load(FIXTURES / "get-status-excerpt.json")
    validate_readback(excerpt["desktop_agent_status"])
    validate_press(excerpt["desktop_agent_press"])
    validate_navigation(excerpt["desktop_navigation"])
    assert codex_status_age_ms(excerpt) == 1200
    assert codex_status_age_ms({"codex_micro": {"status_age_ms": None}}) is None
    assert codex_status_age_ms({}) is None


@pytest.mark.parametrize("field, value", [
    ("source", "doubao"), ("active", "yes"), ("selected", None), ("lease_ms", 4999),
    ("states", ["working"] * 5), ("states", ["busy"] + ["idle"] * 5),
    ("completion_seq", [-1, 0, 0, 0, 0, 0]), ("encoder_navigation", 0),
])
def test_bad_readback_is_rejected(field, value):
    readback = copy.deepcopy(load(FIXTURES / "get-status-excerpt.json")["desktop_agent_status"])
    readback[field] = value
    with pytest.raises(ValueError):
        validate_readback(readback)


@pytest.mark.parametrize("event, ok", [
    ({"source": "desktop", "sequence": 7, "agent": 2, "transport": "ble"}, True),
    ({"source": "desktop", "sequence": 7, "agent": None, "transport": None}, True),  # expired
    ({"source": "desktop", "sequence": 0, "agent": None, "transport": None}, True),  # boot
    ({"source": "desktop", "sequence": 0, "agent": 1, "transport": "usb"}, False),
    ({"source": "desktop", "sequence": 7, "agent": 6, "transport": "usb"}, False),
    ({"source": "desktop", "sequence": 7, "agent": 1, "transport": None}, False),
    ({"source": "desktop", "sequence": 7, "agent": 1, "transport": "wifi"}, False),
    ({"source": "doubao", "sequence": 7, "agent": 1, "transport": "usb"}, False),
    ({"source": "desktop", "sequence": 7, "agent": 1}, False),
])
def test_press_validation(event, ok):
    if ok:
        validate_press(event)
    else:
        with pytest.raises(ValueError):
            validate_press(event)


@pytest.mark.parametrize("change, ok", [
    ({}, True),
    ({"agent": 4, "transport": "usb", "offset": 2}, True),
    ({"offset": 6}, False),
    ({"agent": 6}, False),
    ({"transport": "usb", "sequence": 0}, False),
    # the firmware owner 11-04: agent and transport go null together; before any Agent key
    # only the encoder moved, so a null agent with a live transport is valid.
    ({"agent": 4, "transport": None}, False),
    ({"agent": None, "transport": "usb"}, True),
    ({"anchor_sequence": None}, False),
    ({"source": "doubao"}, False),
])
def test_navigation_validation(change, ok):
    event = dict(load(FIXTURES / "get-status-excerpt.json")["desktop_navigation"], **change)
    if ok:
        validate_navigation(event)
    else:
        with pytest.raises(ValueError):
            validate_navigation(event)
