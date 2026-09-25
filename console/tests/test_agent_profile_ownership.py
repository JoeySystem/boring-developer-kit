from __future__ import annotations

from pathlib import Path
import re

import pytest

from controller_config.official_controls import (
    MATRIX12_AGENT_STATUS_KEYS,
    MATRIX12_CLAUDE_CODE_KEY_NAMES,
    MATRIX12_HARDWARE_IDS,
)
from controller_config.profile_templates import agent_profile_mappings


@pytest.mark.parametrize("hardware_id", sorted(MATRIX12_HARDWARE_IDS))
@pytest.mark.parametrize("platform", ["darwin", "win32"])
@pytest.mark.parametrize("template", ["workbuddy", "qwen-work", "doubao"])
def test_matrix12_presets_put_send_and_newline_on_profile_driven_keys(
    hardware_id, platform, template
):
    mappings = {
        item["control_id"]: item
        for item in agent_profile_mappings(
            template, platform=platform, hardware_id=hardware_id
        )
    }

    # These slots are required by the config format, but NORMAL mode consumes
    # their events as official Agent actions before reaching profile dispatch.
    for control_id in MATRIX12_AGENT_STATUS_KEYS:
        assert mappings[control_id]["action"] == {"type": "none"}
        assert mappings[control_id]["short_name"] == ""

    assert mappings["key.8"]["action"] == {
        "type": "key", "usage": 40, "modifiers": []
    }
    newline_modifier = (
        (227 if platform == "darwin" else 224)
        if template == "workbuddy" else 225
    )
    assert mappings["key.9"]["action"] == {
        "type": "key", "usage": 40, "modifiers": [newline_modifier]
    }


def test_matrix12_remapping_does_not_change_other_hardware_or_later_templates():
    original = agent_profile_mappings("qwen-work", platform="darwin")
    matrix12 = agent_profile_mappings(
        "qwen-work", platform="darwin", hardware_id="WMP-S3-MATRIX12-POWER-V2"
    )
    matrix12[0]["action"]["type"] = "modified"

    assert agent_profile_mappings("qwen-work", platform="darwin") == original
    assert agent_profile_mappings(
        "qwen-work", platform="darwin", hardware_id="WMP-S3-REV-A"
    ) == original
    fresh = agent_profile_mappings(
        "qwen-work", platform="darwin", hardware_id="WMP-S3-MATRIX12-POWER-V2"
    )
    assert fresh[0]["action"] == {"type": "none"}


def test_cc_readonly_names_match_the_firmware_actions():
    firmware_source = (
        Path(__file__).resolve().parents[2]
        / "firmware/components/action_engine/claude_code_shortcuts.c"
    ).read_text()
    key_shortcuts = firmware_source.split("COMMON_KEY_SHORTCUTS[] = {", 1)[1].split(
        "};", 1
    )[0]
    labels = re.findall(r'"([A-Z ]+)"', key_shortcuts)

    assert set(MATRIX12_CLAUDE_CODE_KEY_NAMES) == MATRIX12_AGENT_STATUS_KEYS
    for control_id, label in MATRIX12_CLAUDE_CODE_KEY_NAMES.items():
        key_index = int(control_id.removeprefix("key.")) - 1
        assert label.upper() == labels[key_index]
