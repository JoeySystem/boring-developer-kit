from __future__ import annotations

import copy

import pytest

from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
    LightingPreviewError,
    build_lighting_preview_snapshot,
    clear_lighting_preview_command,
    set_lighting_preview_command,
    validate_lighting_preview_response,
    validate_lighting_preview_snapshot,
)


def test_preview_snapshot_extracts_only_volatile_lighting_fields(load_fixture) -> None:
    config = load_fixture("config-matrix12-power-v2-v1.json")

    snapshot = build_lighting_preview_snapshot(
        config["lighting"],
        under_key_rgb_count=12,
    )

    assert set(snapshot) == {"enabled", "brightness", "under_key"}
    assert "status" not in snapshot
    assert len(snapshot["under_key"]) == 12
    assert snapshot["under_key"] is not config["lighting"]["under_key"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(brightness=101),
        lambda value: value.update(brightness=True),
        lambda value: value["under_key"].pop(),
        lambda value: value["under_key"][0].update(r=256),
        lambda value: value.update(status=[]),
    ],
)
def test_preview_snapshot_rejects_invalid_shape_or_values(
    load_fixture,
    mutate,
) -> None:
    config = load_fixture("config-matrix12-power-v2-v1.json")
    snapshot = {
        "enabled": config["lighting"]["enabled"],
        "brightness": config["lighting"]["brightness"],
        "under_key": copy.deepcopy(config["lighting"]["under_key"]),
    }
    mutate(snapshot)

    with pytest.raises(LightingPreviewError):
        validate_lighting_preview_snapshot(snapshot, under_key_rgb_count=12)


def test_preview_commands_use_reserved_message_types_and_no_config_identity(
    load_fixture,
) -> None:
    config = load_fixture("config-matrix12-power-v2-v1.json")
    snapshot = build_lighting_preview_snapshot(
        config["lighting"],
        under_key_rgb_count=12,
    )

    set_command = set_lighting_preview_command(snapshot)
    clear_command = clear_lighting_preview_command()

    assert (set_command.name, set_command.message_type) == (
        SET_LIGHTING_PREVIEW,
        0x26,
    )
    assert set(set_command.payload) == {"enabled", "brightness", "under_key"}
    assert "generation" not in set_command.payload
    assert "digest" not in set_command.payload
    assert (clear_command.name, clear_command.message_type, clear_command.payload) == (
        CLEAR_LIGHTING_PREVIEW,
        0x27,
        {},
    )


@pytest.mark.parametrize(
    ("command_name", "state"),
    [
        (SET_LIGHTING_PREVIEW, "ACTIVE"),
        (CLEAR_LIGHTING_PREVIEW, "CLEARED"),
    ],
)
def test_preview_response_contract_accepts_only_expected_state(
    command_name: str,
    state: str,
) -> None:
    validate_lighting_preview_response(
        command_name,
        {"command": command_name, "result": {"state": state}},
    )

    with pytest.raises(LightingPreviewError):
        validate_lighting_preview_response(
            command_name,
            {"command": command_name, "result": {"state": "OTHER"}},
        )

    with pytest.raises(LightingPreviewError):
        validate_lighting_preview_response(
            command_name,
            {"command": "OTHER", "result": {"state": state}},
        )
