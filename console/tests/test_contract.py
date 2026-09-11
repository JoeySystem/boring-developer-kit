import pytest

from controller_config.protocol.contract import Contract
from controller_config.protocol.contract import ContractError


def test_contract_is_loaded_from_shared_assets(contract: Contract) -> None:
    assert contract.product_id == "wired-macro-pad-v1"
    assert contract.protocol_major == 1
    assert contract.schema_version == 1
    assert contract.usb_vid == 0x303A
    assert contract.usb_pid == 0x8360
    assert "WMP-S3-MATRIX12-POWER-V2" in contract.hardware_ids
    assert contract.editor_rules.max_macros == contract.config_schema["properties"]["macros"]["maxItems"]
    assert contract.editor_rules.macro_name_max_length == 24
    assert contract.editor_rules.macro_steps_max_items == 128
    assert contract.editor_rules.macro_delay_ms.minimum == 10
    assert contract.editor_rules.macro_delay_ms.maximum == 5000
    assert contract.editor_rules.display_rotations == (0, 90, 180, 270)


def test_hello_fixture_matches_contract(contract: Contract, load_fixture) -> None:
    hello = load_fixture("hello-v1.json")["ack"]
    contract.validate_hello(hello)


def test_non_contract_pid_is_rejected(contract: Contract, load_fixture) -> None:
    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["identity"]["usb_pid"] = 0x9999
    with pytest.raises(ContractError, match="USB ID"):
        contract.validate_hello(hello)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("product_id", "another-product", "product_id"),
        ("hardware_id", "WMP-S3-UNKNOWN", "hardware_id"),
        ("serial", "temporary-port-name", "序列号"),
    ],
)
def test_hello_rejects_wrong_product_hardware_or_serial(
    contract: Contract,
    load_fixture,
    field: str,
    invalid_value: str,
    message: str,
) -> None:
    hello = load_fixture("hello-v1.json")["ack"]
    hello["result"]["identity"][field] = invalid_value

    with pytest.raises(ContractError, match=message):
        contract.validate_hello(hello)


def test_config_fixture_matches_authoritative_schema(contract: Contract, load_fixture) -> None:
    config = load_fixture("config-matrix12-power-v2-v1.json")
    contract.validate_config(config)


def test_status_accepts_three_consistent_ble_host_slots(contract: Contract) -> None:
    status = {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": "0" * 64},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "normal",
            "codex_micro": {
                "active_slot": 2,
                "slots": [
                    {"slot": 1, "paired": True, "connected": False},
                    {"slot": 2, "paired": True, "connected": True},
                    {"slot": 3, "paired": False, "connected": False},
                ],
            },
        },
    }
    contract.validate_status(status)


def test_power_v2_status_fixture_covers_live_diagnostics(
    contract: Contract, load_fixture
) -> None:
    status = load_fixture("status-matrix12-power-v2-v1.json")
    contract.validate_status(status)
    result = status["result"]
    assert result["active_controls"] == []
    assert result["joystick_diagnostics"]["directions"] == 0


def test_status_accepts_claude_code_operating_mode(
    contract: Contract, load_fixture
) -> None:
    status = load_fixture("status-matrix12-power-v2-v1.json")
    status["result"]["operating_mode"] = "claude_code"
    status["result"]["codex_micro"]["mode"] = "claude_code"
    contract.validate_status(status)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("active_controls", ["key.1", "key.1"], "active_controls"),
        ("action_engine", {"host_output_state": "released"}, "local_page"),
        ("joystick_diagnostics", {"raw_x": 2048}, "raw_y"),
    ],
)
def test_status_rejects_malformed_live_diagnostics(
    contract: Contract, load_fixture, field: str, value: object, message: str
) -> None:
    status = load_fixture("status-matrix12-power-v2-v1.json")
    status["result"][field] = value
    with pytest.raises(ContractError, match=message):
        contract.validate_status(status)


def test_status_rejects_connected_ble_slot_that_is_not_active(contract: Contract) -> None:
    status = {
        "command": "GET_STATUS",
        "result": {
            "state": "ACTIVE",
            "active": {"generation": 1, "digest": "0" * 64},
            "pending": None,
            "inputs_neutral": True,
            "activation_failed": False,
            "platform": "macos",
            "operating_mode": "normal",
            "codex_micro": {
                "active_slot": 1,
                "slots": [
                    {"slot": 1, "paired": True, "connected": False},
                    {"slot": 2, "paired": True, "connected": True},
                    {"slot": 3, "paired": False, "connected": False},
                ],
            },
        },
    }
    with pytest.raises(ContractError, match="当前已配对槽位"):
        contract.validate_status(status)


def test_invalid_config_reports_schema_path(contract: Contract, load_fixture) -> None:
    config = load_fixture("config-matrix12-power-v2-v1.json")
    config["active_profile"] = 99
    with pytest.raises(ContractError, match="active_profile"):
        contract.validate_config(config)


def test_capabilities_require_pc_u3_capacity_limits(contract: Contract, load_fixture) -> None:
    capabilities = {
        "result": load_fixture("capabilities-matrix12-power-v2-v1.json")["result"]
    }
    del capabilities["result"]["limits"]["macro_bytes"]

    with pytest.raises(ContractError, match="macro_bytes"):
        contract.validate_capabilities(capabilities)


def test_capabilities_validate_device_authentication_descriptor(
    contract: Contract, load_fixture
) -> None:
    capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")
    contract.validate_capabilities(capabilities)

    capabilities["result"]["features"]["device_authentication"] = True
    capabilities["result"]["device_authentication"]["signature_algorithm"] = "rsa-v1_5"
    with pytest.raises(ContractError, match="signature_algorithm"):
        contract.validate_capabilities(capabilities)


def test_device_authentication_true_requires_descriptor(
    contract: Contract, load_fixture
) -> None:
    capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")
    capabilities["result"]["features"]["device_authentication"] = True
    del capabilities["result"]["device_authentication"]

    with pytest.raises(ContractError, match="device_authentication"):
        contract.validate_capabilities(capabilities)
