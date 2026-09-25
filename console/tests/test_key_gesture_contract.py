from __future__ import annotations

import copy

import pytest

from controller_config.actions import (
    action_definitions,
    action_field_choices,
    action_field_label,
    describe_action,
)
from controller_config.drafts import LocalDraft
from controller_config.protocol.contract import ContractError


GESTURE = {
    "type": "key_gesture",
    "usage": 104,
    "modifiers": [],
    "double_usage": 40,
    "double_modifiers": [],
}


def test_gesture_schema_accepts_optional_modifiers_and_both_keyboard_chords(contract):
    contract.validate_action(GESTURE)
    contract.validate_action({"type": "key_gesture", "usage": 231, "double_usage": 4})
    contract.validate_action({**GESTURE, "double_modifiers": list(range(224, 232))})


@pytest.mark.parametrize("field", ["usage", "double_usage"])
@pytest.mark.parametrize("value", [None, True, 3, 232, 104.5, "104"])
def test_gesture_rejects_invalid_required_usage(contract, field, value):
    action = {**GESTURE, field: value}
    if value is None:
        del action[field]
    with pytest.raises(ContractError):
        contract.validate_action(action)


@pytest.mark.parametrize("field", ["modifiers", "double_modifiers"])
@pytest.mark.parametrize("value", [[223], [232], [224, 224], [True], "Command"])
def test_gesture_rejects_invalid_modifier_arrays(contract, field, value):
    with pytest.raises(ContractError):
        contract.validate_action({**GESTURE, field: value})


@pytest.mark.parametrize("hardware", ["WMP-S3-MATRIX12-V1", "WMP-S3-MATRIX12-POWER-V2"])
@pytest.mark.parametrize("control", ["key.8", "key.9", "key.10", "key.11", "key.12"])
def test_gesture_config_accepts_matrix12_ordinary_keys(contract, load_fixture, hardware, control):
    config = load_fixture("config-matrix12-power-v2-v1.json")
    config["hardware_id"] = hardware
    config["profiles"][0]["mappings"] = [
        {"control_id": control, "short_name": "语音", "action": GESTURE}
    ]
    contract.validate_config(config)


@pytest.mark.parametrize("control", ["key.1", "key.7", "encoder.press", "joystick.press"])
def test_gesture_config_rejects_reserved_and_non_key_controls(contract, load_fixture, control):
    config = load_fixture("config-matrix12-power-v2-v1.json")
    config["profiles"][0]["mappings"] = [
        {"control_id": control, "short_name": "语音", "action": GESTURE}
    ]
    with pytest.raises(ContractError):
        contract.validate_config(config)


def test_gesture_config_rejects_rev_a_even_with_ordinary_key_id(contract, load_fixture):
    config = load_fixture("config-matrix12-power-v2-v1.json")
    config["hardware_id"] = "WMP-S3-REV-A"
    config["lighting"]["under_key"] = config["lighting"]["under_key"][:7]
    config["profiles"][0]["mappings"] = [
        {"control_id": "key.8", "short_name": "语音", "action": GESTURE}
    ]
    with pytest.raises(ContractError):
        contract.validate_config(config)


def test_gesture_fields_and_description_preserve_each_chord(contract):
    definition, = action_definitions(contract, ("key_gesture",))
    fields = {field.name: field for field in definition.fields}
    contract.validate_action(definition.default_action())
    assert set(fields) == {"usage", "modifiers", "double_usage", "double_modifiers"}
    for name in ("usage", "double_usage"):
        assert fields[name].required
        choices = action_field_choices("key_gesture", fields[name], 104, platform="macos")
        assert ("F13", 104) in choices
        assert ("Enter", 40) in choices
    for name in ("modifiers", "double_modifiers"):
        assert not fields[name].required
        assert (fields[name].minimum, fields[name].maximum) == (224, 231)
        assert fields[name].max_items == 8 and fields[name].unique
    assert action_field_label("key_gesture", "usage") == "单击按键"
    assert action_field_label("key_gesture", "double_modifiers") == "双击组合键"
    assert describe_action(GESTURE, platform="macos") == "语音启停 · 双击发送"
    assert describe_action(
        {**GESTURE, "modifiers": [227], "double_modifiers": [225]},
        platform="macos", compact=True,
    ) == "单击 ⌘F13 / 双击 ⇧Enter"


def test_older_capabilities_hide_and_reject_gesture_without_mutating_draft(contract, load_fixture):
    config = load_fixture("config-matrix12-power-v2-v1.json")
    capabilities = load_fixture("capabilities-matrix12-power-v2-v1.json")["result"]
    assert "key_gesture" not in capabilities["actions"]
    assert all(
        item.action_type != "key_gesture"
        for item in action_definitions(contract, tuple(capabilities["actions"]))
    )
    draft = LocalDraft(
        serial="test", hardware_id=config["hardware_id"], schema_version=1,
        base_generation=1, base_digest="unused", max_profiles=8,
        controls=tuple(capabilities["controls"]), actions=tuple(capabilities["actions"]),
        limits=capabilities["limits"], features=capabilities["features"],
        editor_rules=contract.editor_rules, platform="macos", config=config,
    )
    original = copy.deepcopy(draft.config)
    with pytest.raises(ValueError, match="不支持 key_gesture"):
        draft.set_mapping(config["active_profile"], "key.8", "语音", GESTURE)
    assert draft.config == original
