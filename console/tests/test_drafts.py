from __future__ import annotations

import copy

from controller_config.drafts import LocalDraft
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
    original_mapping = draft.mapping(snapshot.active_profile_id, "key.12")

    draft.set_mapping(snapshot.active_profile_id, "key.12", "Enter", action)

    assert draft.mapping(snapshot.active_profile_id, "key.12") == {
        "control_id": "key.12",
        "short_name": "Enter",
        "action": action,
    }
    assert draft.validate(contract) == ()
    assert draft.is_dirty

    draft.discard()

    assert not draft.is_dirty
    assert draft.mapping(snapshot.active_profile_id, "key.12") == original_mapping


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
    assert all(
        mapping["action"] == {"type": "none"}
        for mapping in draft.profile(new_id)["mappings"]
    )
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
