from __future__ import annotations

import copy
from controller_config.drafts import LocalDraft
from controller_config.profile_templates import (
    agent_profile_mappings,
    agent_profile_templates_for_platform,
)
from controller_config.transport.demo import DemoGateway


def _snapshot(qtbot, contract):
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    return blocker.args[0]


def test_local_draft_binds_to_snapshot_without_mutating_it(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)

    assert draft.serial == snapshot.identity["serial"]
    assert draft.hardware_id == snapshot.identity["hardware_id"]
    assert draft.schema_version == snapshot.versions["schema_version"]
    assert draft.base_generation == snapshot.config_result["generation"]
    assert draft.base_digest == snapshot.config_result["digest"]
    assert not draft.is_dirty

    original_name = snapshot.profile_name
    draft.rename_profile(snapshot.active_profile_id, "Editing draft")

    assert draft.is_dirty
    assert snapshot.profile_name == original_name
    assert draft.profile(snapshot.active_profile_id)["name"] == "Editing draft"
    assert any(change.path.endswith(".name") for change in draft.changes)


def test_mapping_edit_reuses_schema_valid_action_and_can_be_discarded(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    action = {"type": "key", "usage": 40, "modifiers": []}
    original_mapping = draft.mapping(snapshot.active_profile_id, "key.1")

    draft.set_mapping(snapshot.active_profile_id, "key.1", "Enter", action)

    assert draft.mapping(snapshot.active_profile_id, "key.1") == {
        "control_id": "key.1",
        "short_name": "Enter",
        "action": action,
    }
    assert draft.validate(contract) == ()
    assert draft.is_dirty

    draft.discard()

    assert not draft.is_dirty
    assert draft.mapping(snapshot.active_profile_id, "key.1") == original_mapping


def test_matrix12_function_key_mapping_is_customizable_for_normal_mode(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    action = {"type": "key", "usage": 40, "modifiers": []}

    draft.set_mapping(snapshot.active_profile_id, "key.8", "Enter", action)

    assert draft.mapping(snapshot.active_profile_id, "key.8") == {
        "control_id": "key.8",
        "short_name": "Enter",
        "action": action,
    }
    assert draft.validate(contract) == ()
    assert draft.is_dirty


def test_matrix12_function_key_color_is_saved_when_preferences_change(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    original = copy.deepcopy(draft.config["lighting"])
    edited = copy.deepcopy(original)
    edited["brightness"] = 80
    edited["under_key"][2] = {"r": 255, "g": 0, "b": 0}

    draft.set_preferences(
        lighting=edited,
        haptic=draft.config["haptic"],
        display=draft.config["display"],
    )

    assert draft.config["lighting"]["brightness"] == 80
    assert draft.config["lighting"]["under_key"][2] == {"r": 255, "g": 0, "b": 0}


def test_matrix12_candidate_can_replace_function_key_normal_mapping(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    candidate = copy.deepcopy(draft.config)
    candidate["profiles"][0]["mappings"].append(
        {
            "control_id": "key.3",
            "short_name": "Custom",
            "action": {"type": "key", "usage": 40, "modifiers": []},
        }
    )

    assert draft.validate_candidate(candidate, contract) == ()


def test_discard_restores_device_function_mapping_and_light(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    mappings = snapshot.config_result["config"]["profiles"][0]["mappings"]
    custom_mapping = {
        "control_id": "key.3",
        "short_name": "Custom",
        "action": {"type": "key", "usage": 40, "modifiers": []},
    }
    mappings.append(custom_mapping)
    snapshot.config_result["config"]["lighting"]["under_key"][2] = {
        "r": 255,
        "g": 0,
        "b": 0,
    }
    draft = LocalDraft.from_snapshot(snapshot, contract)

    assert not draft.is_dirty
    draft.discard()

    assert not draft.is_dirty
    assert draft.mapping(snapshot.active_profile_id, "key.3") == custom_mapping
    assert draft.config["lighting"]["under_key"][2] == {"r": 255, "g": 0, "b": 0}


def test_draft_rejects_control_outside_capabilities(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)

    try:
        draft.set_mapping(snapshot.active_profile_id, "key.99", "Nope", {"type": "none"})
    except ValueError as exc:
        assert "CAPABILITIES" in str(exc)
    else:
        raise AssertionError("unsupported control must be rejected")


def test_profile_create_copy_and_delete_stay_schema_valid(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)

    new_id = draft.create_profile()
    copied_id = draft.copy_profile(snapshot.active_profile_id)

    assert new_id == 1
    assert copied_id == 2
    assert draft.profile(new_id)["name"] == "新配置方案"
    assert draft.mapping(new_id, "key.1")["action"] == {"type": "none"}
    assert draft.mapping(new_id, "key.3")["action"] == {"type": "none"}
    assert draft.mapping(new_id, "key.9")["action"] == {"type": "none"}
    assert draft.profile(copied_id)["name"].endswith("副本")
    assert draft.validate(contract) == ()

    draft.set_active_profile(copied_id)
    draft.delete_profile(copied_id)

    assert draft.config["active_profile"] == snapshot.active_profile_id
    assert draft.validate(contract) == ()


def test_profile_save_as_copies_names_and_activates_in_one_draft_change(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    source_profile_id = snapshot.active_profile_id
    source_mappings = copy.deepcopy(draft.profile(source_profile_id)["mappings"])

    saved_profile_id = draft.save_profile_as(source_profile_id, "Writing")

    assert saved_profile_id != source_profile_id
    assert draft.config["active_profile"] == saved_profile_id
    assert draft.profile(saved_profile_id)["name"] == "Writing"
    assert draft.profile(saved_profile_id)["mappings"] == source_mappings
    assert draft.validate(contract) == ()


def test_profile_save_as_rejects_duplicate_name_without_partial_copy(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    source_profile_id = snapshot.active_profile_id
    original_profiles = copy.deepcopy(draft.config["profiles"])

    try:
        draft.save_profile_as(source_profile_id, snapshot.profile_name)
    except ValueError as exc:
        assert "同名" in str(exc)
    else:
        raise AssertionError("duplicate profile name must be rejected")

    assert draft.config["profiles"] == original_profiles
    assert draft.config["active_profile"] == source_profile_id


def test_agent_templates_create_valid_local_profiles(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    profiles = {}
    for template in agent_profile_templates_for_platform("darwin"):
        draft = LocalDraft.from_snapshot(snapshot, contract)
        existing_profiles = copy.deepcopy(draft.profiles)
        profile_id = draft.create_profile_from_template(
            template.profile_name,
            agent_profile_mappings(
                template.key, platform="darwin", hardware_id=draft.hardware_id
            ),
        )
        assert draft.config["active_profile"] == profile_id
        assert draft.profile(profile_id)["name"] == template.profile_name
        assert draft.validate(contract) == ()
        assert draft.profiles[:-1] == existing_profiles
        profiles[template.key] = draft

    qwen = profiles["qwen-work"]
    qwen_profile_id = qwen.config["active_profile"]
    assert qwen.mapping(qwen_profile_id, "joystick.up")["action"] == {
        "type": "key",
        "usage": 17,
        "modifiers": [227],
    }
    assert qwen.mapping(qwen_profile_id, "joystick.press")["short_name"] == "取消"
    workbuddy = profiles["workbuddy"]
    workbuddy_profile_id = workbuddy.config["active_profile"]
    assert workbuddy.mapping(workbuddy_profile_id, "key.9")["action"] == {
        "type": "key",
        "usage": 40,
        "modifiers": [227],
    }
    assert snapshot.profile_name == "Default"


def test_windows_agent_templates_use_windows_names_and_shortcuts(
    qtbot, contract
) -> None:
    snapshot = _snapshot(qtbot, contract)
    templates = agent_profile_templates_for_platform("win32")
    assert [template.profile_name for template in templates] == [
        "WorkBuddy Windows",
        "千问办公 Windows",
        "豆包 Windows",
    ]
    draft = LocalDraft.from_snapshot(snapshot, contract)
    template = next(item for item in templates if item.key == "workbuddy")
    profile_id = draft.create_profile_from_template(
        template.profile_name,
        agent_profile_mappings(
            template.key, platform="win32", hardware_id=draft.hardware_id
        ),
    )
    assert draft.mapping(profile_id, "key.9")["action"] == {
        "type": "key",
        "usage": 40,
        "modifiers": [224],
    }
    assert draft.mapping(profile_id, "key.11")["action"]["modifiers"] == [224]
    assert draft.mapping(profile_id, "joystick.left")["action"] == {
        "type": "key",
        "usage": 28,
        "modifiers": [224],
    }


def test_profile_delete_rejects_last_profile(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)

    try:
        draft.delete_profile(snapshot.active_profile_id)
    except ValueError as exc:
        assert "至少保留一个" in str(exc)
    else:
        raise AssertionError("last profile must not be deleted")


def test_macro_crud_and_capacity_validation_follow_capabilities(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)

    macro_id = draft.create_macro()
    draft.update_macro(
        macro_id,
        "Build",
        [
            {"op": "press", "usage": 224},
            {"op": "tap", "usage": 5},
            {"op": "release", "usage": 224},
            {"op": "delay", "duration_ms": 120},
            {"op": "text", "text": "done"},
        ],
    )

    assert draft.macro(macro_id)["name"] == "Build"
    assert draft.macro_encoded_size(macro_id) == 16
    assert draft.validate(contract) == ()

    draft.delete_macro(macro_id)
    assert all(item["id"] != macro_id for item in draft.macros)


def test_macro_validation_rejects_unreleased_key_and_capacity_overflow(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    macro_id = draft.create_macro()

    draft.update_macro(macro_id, "Held", [{"op": "press", "usage": 4}])
    assert any("未释放" in error for error in draft.validate(contract))

    draft.update_macro(macro_id, "Too large", [{"op": "text", "text": "x" * 256}])
    assert any("256 字节" in error for error in draft.validate(contract))

    draft.update_macro(macro_id, "Not ASCII", [{"op": "text", "text": "中文"}])
    assert any("不符合当前 Schema" in error for error in draft.validate(contract))


def test_preferences_and_import_replace_only_change_local_draft(qtbot, contract) -> None:
    snapshot = _snapshot(qtbot, contract)
    draft = LocalDraft.from_snapshot(snapshot, contract)
    imported = copy.deepcopy(draft.config)
    imported["display"]["brightness"] = 42

    draft.replace_config(imported, contract)
    draft.set_preferences(
        lighting={**draft.config["lighting"], "brightness": 24},
        haptic={**draft.config["haptic"], "strength": 55},
        display={**draft.config["display"], "rotation": 180},
    )

    assert draft.config["lighting"]["brightness"] == 24
    assert draft.config["haptic"]["strength"] == 55
    assert draft.config["display"]["rotation"] == 180
    assert draft.is_dirty
    assert draft.validate(contract) == ()
    assert snapshot.config["display"]["brightness"] != 42
