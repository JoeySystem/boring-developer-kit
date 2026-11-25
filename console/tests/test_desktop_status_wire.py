"""Wire format and GET_STATUS contract of the generic `desktop` Agent status source.

Driven by the shared samples in software/protocol/fixtures/desktop_agent/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from controller_config import desktop_status_wire as desktop
from controller_config.protocol.contract import ContractError


@pytest.fixture(scope="module")
def samples(protocol_dir: Path) -> Path:
    return protocol_dir / "fixtures" / "desktop_agent"


def sample(samples: Path, name: str) -> dict:
    return json.loads((samples / name).read_text(encoding="utf-8"))


def names(samples: Path, folder: str) -> list[str]:
    return sorted(f"{folder}/{path.name}" for path in (samples / folder).glob("*.json"))


def mixed_command(samples: Path):
    payload = sample(samples, "valid/set-mixed.json")
    return desktop.status_command(payload["states"], payload["completion_seq"])


# --- wire format -------------------------------------------------------------

def test_valid_samples_are_exactly_what_status_command_builds(samples):
    for name in names(samples, "valid"):
        payload = sample(samples, name)
        desktop.validate_set_request(payload)
        command = desktop.status_command(payload["states"], payload["completion_seq"],
                                         encoder_navigation=payload.get("encoder_navigation", False))
        assert (command.name, command.message_type, command.payload) == ("SET_AGENT_STATUS", 0x28, payload)
    assert desktop.clear_status_command().payload == sample(samples, "clear.json")
    assert desktop.clear_status_command().message_type == 0x29


def test_invalid_samples_are_refused_before_sending(samples):
    invalid = names(samples, "invalid")
    assert len(invalid) == 9
    for name in invalid:
        payload = sample(samples, name)
        with pytest.raises(ValueError):
            desktop.validate_set_request(payload)
        if payload.keys() == {"source", "states", "completion_seq"} and payload["source"] == "desktop":
            with pytest.raises(ValueError):
                desktop.status_command(payload["states"], payload["completion_seq"])
    with pytest.raises(ValueError):
        desktop.validate_clear_request({"source": "desktop", "app": "doubao"})


def test_ack_samples_match_only_their_own_request(samples):
    set_ack, clear_ack = sample(samples, "ack-set.json"), sample(samples, "ack-clear.json")
    set_command, clear_command = mixed_command(samples), desktop.clear_status_command()
    assert desktop.validate_response(set_command, set_ack) == 5000
    assert desktop.validate_response(clear_command, clear_ack) == 5000
    for command, ack in ((set_command, clear_ack), (clear_command, set_ack)):
        with pytest.raises(ValueError):
            desktop.validate_response(command, ack)


# --- contract.validate_status --------------------------------------------------

def status_with_desktop(load_fixture, samples) -> dict:
    status = load_fixture("status-matrix12-power-v2-v1.json")
    excerpt = sample(samples, "get-status-excerpt.json")
    result = status["result"]
    result["operating_mode"] = "normal"
    for field in ("desktop_agent_status", "desktop_agent_press", "desktop_navigation"):
        result[field] = excerpt[field]
    result["codex_micro"] = {**result["codex_micro"], **excerpt["codex_micro"]}
    return status


def test_status_accepts_desktop_readback_and_old_firmware_without_it(contract, load_fixture, samples):
    contract.validate_status(status_with_desktop(load_fixture, samples))
    contract.validate_status(load_fixture("status-matrix12-power-v2-v1.json"))
    status = status_with_desktop(load_fixture, samples)
    result = status["result"]
    result["codex_micro"]["status_age_ms"] = None
    result["desktop_agent_press"].update(agent=None, transport=None)
    result["desktop_navigation"].update(agent=None, transport="ble")  # rotation without an anchor
    del result["desktop_agent_status"]["encoder_navigation"]
    result["desktop_agent_status"]["unknown_later_field"] = 1
    contract.validate_status(status)


MALFORMED = [
    ("desktop_agent_status", None),
    ("desktop_agent_status", {"source": "doubao"}),
    ("desktop_agent_status.source", "doubao"),
    ("desktop_agent_status.active", 1),
    ("desktop_agent_status.selected", None),
    ("desktop_agent_status.lease_ms", 4000),
    ("desktop_agent_status.lease_ms", 5000.0),
    ("desktop_agent_status.states", ["working"] * 5),
    ("desktop_agent_status.states", ["busy"] + ["idle"] * 5),
    ("desktop_agent_status.completion_seq", [-1, 0, 0, 0, 0, 0]),
    ("desktop_agent_status.completion_seq", [True, 0, 0, 0, 0, 0]),
    ("desktop_agent_status.completion_seq", [2 ** 32, 0, 0, 0, 0, 0]),
    ("desktop_agent_status.encoder_navigation", "false"),
    ("desktop_agent_press", []),
    ("desktop_agent_press.source", "doubao"),
    ("desktop_agent_press.sequence", -1),
    ("desktop_agent_press.sequence", 0),
    ("desktop_agent_press.agent", 6),
    ("desktop_agent_press.agent", None),
    ("desktop_agent_press.transport", "wifi"),
    ("desktop_navigation", "x"),
    ("desktop_navigation.source", "claude_code"),
    ("desktop_navigation.anchor_sequence", None),
    ("desktop_navigation.offset", 6),
    ("desktop_navigation.agent", 6),
    ("desktop_navigation.agent", 1),  # agent without a transport
    ("desktop_navigation.transport", "wifi"),
    ("codex_micro.status_age_ms", -1),
    ("codex_micro.status_age_ms", False),
    ("codex_micro.status_age_ms", "1200"),
]


@pytest.mark.parametrize(("path", "value"), MALFORMED)
def test_status_rejects_unrecognized_desktop_shapes(contract, load_fixture, samples, path, value):
    status = status_with_desktop(load_fixture, samples)
    *parents, leaf = path.split(".")
    target = status["result"]
    for part in parents:
        target = target[part]
    target[leaf] = value
    with pytest.raises(ContractError):
        contract.validate_status(status)


def test_status_rejects_missing_navigation_keys(contract, load_fixture, samples):
    for field, key in (("desktop_agent_press", "transport"), ("desktop_navigation", "agent"),
                       ("desktop_agent_status", "states")):
        status = status_with_desktop(load_fixture, samples)
        del status["result"][field][key]
        with pytest.raises(ContractError):
            contract.validate_status(status)



def test_every_desktop_sample_has_a_console_check(samples):
    every = sorted(str(path.relative_to(samples)) for path in samples.rglob("*.json"))
    assert len(every) == 16
    excerpt = sample(samples, "get-status-excerpt.json")
    checks = {
        "ack-set.json": lambda value: desktop.validate_response(mixed_command(samples), value),
        "ack-clear.json": lambda value: desktop.validate_response(desktop.clear_status_command(), value),
        "clear.json": desktop.validate_clear_request,
        "get-status-excerpt.json": lambda value: (
            desktop.validate_readback(value["desktop_agent_status"]),
            desktop.validate_press(value["desktop_agent_press"]),
            desktop.validate_navigation(value["desktop_navigation"]),
            desktop.validate_codex_status_age(value["codex_micro"])),
    }
    for name in every:
        value = sample(samples, name)
        if name.startswith("invalid/"):
            with pytest.raises(ValueError):
                desktop.validate_set_request(value)
        else:
            checks.get(name, desktop.validate_set_request)(value)
    assert excerpt["codex_micro"]["status_age_ms"] == 1200
