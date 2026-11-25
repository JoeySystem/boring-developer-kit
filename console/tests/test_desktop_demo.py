"""Demo device simulating the `desktop` Agent status source without hardware.

Driven by the shared samples in software/protocol/fixtures/desktop_agent/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from controller_config import desktop_status_wire as desktop
from controller_config.protocol.bootstrap import Command
from controller_config.transport.demo import DemoGateway


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


# --- demo device ---------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms / 1000


def demo(qtbot, contract, *, navigation=True, state="ready", **kwargs):
    clock = Clock()
    gateway = DemoGateway(contract, state, desktop_navigation=navigation, clock=clock,
                          **({"desktop_agent": True} | kwargs))
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as ready:
        gateway.connect_port("demo://power-v2")
    gateway.clock = clock
    gateway.snapshot = ready.args[0]
    return gateway


def run(qtbot, gateway, command):
    results = []
    gateway.agent_status_completed.connect(lambda cmd, value: results.append(("ok", cmd, value)))
    gateway.agent_status_failed.connect(lambda cmd, value: results.append(("failed", cmd, value)))
    gateway.execute_command(command)
    qtbot.waitUntil(lambda: bool(results), timeout=1000)
    kind, cmd, value = results[0]
    assert cmd is command
    if kind == "ok":
        desktop.validate_response(command, value)
        return value
    return value.error_name


def read(contract, gateway, transport="usb"):
    status = gateway.desktop_status(transport=transport)
    contract.validate_status({"command": "GET_STATUS", "result": status})
    return status


def test_demo_declares_desktop_source_in_normal_mode(qtbot, contract):
    gateway = demo(qtbot, contract)
    features = gateway.snapshot.capabilities["features"]
    assert features["desktop_agent_status"] is True and features["desktop_task_navigation"] is True
    status = gateway.snapshot.status
    assert status["operating_mode"] == "normal" and status["codex_micro"]["status_age_ms"] is None
    contract.validate_status({"command": "GET_STATUS", "result": status})
    readback = status["desktop_agent_status"]
    assert (readback["active"], readback["states"]) == (False, ["unknown"] * 6)
    assert status["desktop_agent_press"] == {"source": "desktop", "sequence": 0, "agent": None, "transport": None}


def test_default_demo_keeps_fixture_and_refuses_desktop(qtbot, contract, monkeypatch, samples):
    monkeypatch.delenv("BORING_DEMO_DESKTOP_AGENT", raising=False)
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as ready:
        gateway.connect_port("demo://power-v2")
    snapshot = ready.args[0]
    assert "desktop_agent_status" not in snapshot.capabilities["features"]
    assert "desktop_agent_status" not in snapshot.status
    assert run(qtbot, gateway, mixed_command(samples)) == "READ_ONLY"
    monkeypatch.setenv("BORING_DEMO_DESKTOP_AGENT", "1")
    assert DemoGateway(contract, "ready")._desktop_agent is True


def test_demo_set_readback_heartbeat_and_lease_expiry(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    command = mixed_command(samples)
    assert run(qtbot, gateway, command) == sample(samples, "ack-set.json")
    readback = read(contract, gateway)["desktop_agent_status"]
    assert readback == {**sample(samples, "get-status-excerpt.json")["desktop_agent_status"],
                        "states": command.payload["states"]}
    gateway.clock.advance(4900)
    assert read(contract, gateway)["desktop_agent_status"]["active"] is True
    run(qtbot, gateway, mixed_command(samples))  # heartbeat renews
    gateway.clock.advance(4900)
    assert read(contract, gateway)["desktop_agent_status"]["active"] is True
    gateway.clock.advance(100)
    expired = read(contract, gateway)["desktop_agent_status"]
    assert (expired["active"], expired["selected"], expired["states"]) == (False, False, ["unknown"] * 6)


def test_demo_counters_never_go_back_within_a_lease(qtbot, contract):
    gateway = demo(qtbot, contract)
    run(qtbot, gateway, desktop.status_command(["completed"] + ["idle"] * 5, [4, 0, 0, 0, 0, 0]))
    run(qtbot, gateway, desktop.status_command(["completed"] + ["idle"] * 5, [2, 0, 0, 0, 0, 0]))
    assert read(contract, gateway)["desktop_agent_status"]["completion_seq"][0] == 4
    run(qtbot, gateway, desktop.clear_status_command())
    run(qtbot, gateway, desktop.status_command(["completed"] + ["idle"] * 5, [2, 0, 0, 0, 0, 0]))
    assert read(contract, gateway)["desktop_agent_status"]["completion_seq"][0] == 2


def test_demo_clear_is_idempotent_and_allowed_outside_normal(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    run(qtbot, gateway, mixed_command(samples))
    assert run(qtbot, gateway, desktop.clear_status_command()) == sample(samples, "ack-clear.json")
    assert run(qtbot, gateway, desktop.clear_status_command()) == sample(samples, "ack-clear.json")
    readback = read(contract, gateway)["desktop_agent_status"]
    assert (readback["active"], readback["states"]) == (False, ["unknown"] * 6)
    gateway.simulate_operating_mode("codex")
    assert run(qtbot, gateway, desktop.clear_status_command())["result"]["active"] is False


def test_demo_set_outside_normal_is_read_only_and_lease_is_not_selected(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    run(qtbot, gateway, mixed_command(samples))
    gateway.simulate_operating_mode("claude_code")
    readback = read(contract, gateway)["desktop_agent_status"]
    assert (readback["active"], readback["selected"]) == (True, False)
    assert run(qtbot, gateway, mixed_command(samples)) == "READ_ONLY"
    assert gateway.simulate_desktop_press(0) is False
    read_only = demo(qtbot, contract, state="read-only")
    assert run(qtbot, read_only, mixed_command(samples)) == "READ_ONLY"


def test_demo_rejects_invalid_samples(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    for name in names(samples, "invalid"):
        command = Command("SET_AGENT_STATUS", 0x28, sample(samples, name), timeout_ms=1200)
        if command.payload["source"] != "desktop":
            continue  # the old doubao source is not the desktop source at all
        assert run(qtbot, gateway, command) == "VALIDATION_FAILED", name
    bad_clear = Command("CLEAR_AGENT_STATUS", 0x29, {"source": "desktop", "app": "doubao"})
    assert run(qtbot, gateway, bad_clear) == "VALIDATION_FAILED"
    assert read(contract, gateway)["desktop_agent_status"]["active"] is False


def test_demo_press_event_expires_and_hides_from_other_transport(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    assert gateway.simulate_desktop_press(1) is False  # no lease yet
    run(qtbot, gateway, mixed_command(samples))  # slot 5 is codex
    assert gateway.simulate_desktop_press(5) is False
    assert gateway.simulate_desktop_press(2) is True
    press = read(contract, gateway)["desktop_agent_press"]
    assert press == {"source": "desktop", "sequence": 1, "agent": 2, "transport": "usb"}
    assert read(contract, gateway, "ble")["desktop_agent_press"] == {
        "source": "desktop", "sequence": 1, "agent": None, "transport": None}
    gateway.clock.advance(1999)
    assert read(contract, gateway)["desktop_agent_press"]["agent"] == 2  # reads do not consume
    gateway.clock.advance(1)
    assert read(contract, gateway)["desktop_agent_press"] == {
        "source": "desktop", "sequence": 1, "agent": None, "transport": None}
    gateway.simulate_desktop_press(0)
    run(qtbot, gateway, desktop.clear_status_command())
    assert read(contract, gateway)["desktop_agent_press"] == {
        "source": "desktop", "sequence": 2, "agent": None, "transport": None}


def test_demo_encoder_navigation_snapshot(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    payload = sample(samples, "valid/set-navigation.json")
    navigated = desktop.status_command(payload["states"], payload["completion_seq"], encoder_navigation=True)
    run(qtbot, gateway, navigated)
    assert read(contract, gateway)["desktop_agent_status"]["encoder_navigation"] is True
    assert gateway.simulate_desktop_rotation(1) is True  # no anchor yet
    nav = read(contract, gateway)["desktop_navigation"]
    assert nav == {"source": "desktop", "sequence": 1, "anchor_sequence": 0, "offset": 1,
                   "agent": None, "transport": "usb"}
    gateway.simulate_desktop_press(0)
    gateway.simulate_desktop_rotation(1)
    gateway.simulate_desktop_rotation(1)
    gateway.simulate_desktop_rotation(-1)
    assert gateway.simulate_desktop_rotation(2) is False
    nav = read(contract, gateway)["desktop_navigation"]
    assert nav == {"source": "desktop", "sequence": 5, "anchor_sequence": 2, "offset": 1,
                   "agent": 0, "transport": "usb"}
    assert read(contract, gateway, "ble")["desktop_navigation"]["transport"] is None
    gateway.clock.advance(2000)
    nav = read(contract, gateway)["desktop_navigation"]
    assert (nav["sequence"], nav["offset"], nav["agent"], nav["transport"]) == (5, 1, None, None)
    run(qtbot, gateway, mixed_command(samples))  # opt-in withdrawn
    assert gateway.simulate_desktop_rotation(1) is False
    nav = read(contract, gateway)["desktop_navigation"]
    assert (nav["sequence"], nav["anchor_sequence"], nav["offset"]) == (5, 5, 0)


def test_demo_without_navigation_capability_refuses_opt_in(qtbot, contract, samples):
    gateway = demo(qtbot, contract, navigation=False)
    assert gateway.snapshot.capabilities["features"]["desktop_task_navigation"] is False
    payload = sample(samples, "valid/set-navigation.json")
    navigated = desktop.status_command(payload["states"], payload["completion_seq"], encoder_navigation=True)
    assert run(qtbot, gateway, navigated) == "VALIDATION_FAILED"
    run(qtbot, gateway, mixed_command(samples))
    assert gateway.simulate_desktop_rotation(1) is False


def test_demo_polls_validated_status_and_reconnect_releases_lease(qtbot, contract, samples):
    gateway = demo(qtbot, contract)
    gateway.set_status_poll_interval(100)
    run(qtbot, gateway, mixed_command(samples))
    with qtbot.waitSignal(gateway.status_updated, timeout=1000) as polled:
        pass
    contract.validate_status({"command": "GET_STATUS", "result": polled.args[0]})
    assert polled.args[0]["desktop_agent_status"]["active"] is True
    gateway.simulate_desktop_press(1)
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as ready:
        gateway.connect_port("demo://power-v2")
    assert ready.args[0].status["desktop_agent_status"]["active"] is False
    assert ready.args[0].status["desktop_agent_press"]["sequence"] == 1  # boot-scoped
    gateway.shutdown()
    assert not gateway._desktop_poll.isActive()
