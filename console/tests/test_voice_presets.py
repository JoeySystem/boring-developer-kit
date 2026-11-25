"""B-01 / B-05 / B-06 / A-16: the shared voice preset source."""

from __future__ import annotations

from pathlib import Path

import pytest

from controller_config import voice_presets as vp


@pytest.mark.parametrize("value,expected", [
    ("darwin", "macos"), ("macos", "macos"), ("win32", "windows"), ("windows", "windows"), ("linux", "windows"),
])
def test_host_platform(value, expected):
    assert vp.host_platform(value) == expected


def test_platform_lists_hide_unsupported_software():
    assert [k for k, _ in vp.software_choices("darwin")] == ["wechat", "typeless", "doubao", "qianwen", "other"]
    assert [k for k, _ in vp.software_choices("win32")] == ["typeless", "doubao", "other"]
    assert vp.preset("wechat", "win32") is None


def test_windows_doubao_uses_right_alt_only():
    tap = vp.preset_action("doubao", "win32")
    assert tap == {"type": "key_gesture", "usage": vp.SPACE, "modifiers": [vp.RIGHT_ALT], "double_usage": vp.ENTER}
    assert vp.DOUBAO_WINDOWS_HOLD == {"type": "key", "usage": vp.RIGHT_ALT}
    assert vp.preset_matches("doubao", tap, "win32")
    assert not vp.preset_matches("doubao", {**tap, "modifiers": [226]}, "win32")


def test_macos_doubao_accepts_right_control_or_command():
    for usage in (vp.RIGHT_CTRL, vp.RIGHT_GUI):
        assert vp.preset_matches("doubao", {"type": "key_gesture", "usage": usage, "double_usage": 40}, "darwin")
    assert not vp.preset_matches("doubao", {"type": "key_gesture", "usage": 104, "double_usage": 40}, "darwin")


def test_default_start_stop_key_is_defined_once():
    from controller_config.ai_setup import VOICE_ACTION
    assert VOICE_ACTION is vp.DEFAULT_VOICE_ACTION
    assert vp.DEFAULT_VOICE_ACTION["usage"] == vp.DEFAULT_VOICE_USAGE
    root = Path(vp.__file__).parent
    offenders = [str(path.relative_to(root)) for path in root.rglob("*.py")
                 if path.name != "voice_presets.py"
                 and '{"type": "key_gesture", "usage": 104, "double_usage": 40}' in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_settings_app_found_by_bundle_lookup_and_nested_settings(tmp_path):
    bundle = tmp_path / "WeType.app"
    nested = bundle / "Contents" / "MacOS" / "WeTypeSettings.app"
    nested.mkdir(parents=True)
    assert vp.settings_app("wechat", "darwin", lookup=lambda _bid: bundle) == nested
    direct = tmp_path / "DoubaoImeSettings.app"
    direct.mkdir()
    assert vp.settings_app("doubao", "darwin", lookup=lambda _bid: direct) == direct
    assert vp.settings_app("doubao", "darwin", lookup=lambda _bid: None) is None
    assert vp.settings_app("doubao", "darwin", lookup=lambda _bid: tmp_path / "missing.app") is None
    assert vp.settings_app("doubao", "win32", lookup=lambda _bid: direct) is None


def test_no_hard_coded_settings_paths():
    root = Path(vp.__file__).parent
    for path in root.rglob("*.py"):
        assert "/Library/Input Methods/" not in path.read_text(encoding="utf-8") or path.name == "voice_presets.py", path
