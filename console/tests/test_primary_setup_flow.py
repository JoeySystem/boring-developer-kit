"""Mode-page editing helpers shared by other tests, and two pending-state checks.

The voice task dialog these tests were named after (VoiceTaskDialog, opened from the
old home card) was removed with its code (design decision); see test_voice_input_ui and
test_global_voice for voice setup through the key card.
"""
from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLineEdit

from controller_config.views.action_editor import ActionEditor
from controller_config.views.mode_page import ModeStage
from test_session_recovery import session


def ready(session, monkeypatch, tmp_path, *, mode="normal", codex_action=None):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    config = deepcopy(snapshot.config)
    if codex_action is not None:
        profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
        profile["codex_voice"] = deepcopy(codex_action)
    actions = list(snapshot.capabilities["actions"])
    assert "key_gesture" in actions
    snapshot = replace(
        snapshot,
        capabilities={
            **snapshot.capabilities, "actions": actions,
            "features": {**snapshot.capabilities["features"], "codex_voice": True, "codex_voice_hold": True},
        },
        status={**snapshot.status, "operating_mode": mode},
        config_result={
            **snapshot.config_result, "config": config,
            "generation": snapshot.config_result["generation"] + int(codex_action is not None),
        },
    )
    gateway.snapshot_ready.emit(snapshot)
    window._select_configuration_mode(mode)
    window._onboarding_settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    return window, vm, gateway, snapshot


def device_writes(gateway):
    return [command for command in gateway.commands if command.name in {"VALIDATE_CONFIG", "SET_CONFIG"}]


def key_changed(window):
    """The bar's 这颗键有改动 (it replaced the sync card, Mode页 定稿 v1)."""
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    return stage.bar.state().editing_key


def current_editor(window):
    return window._current_mapping_card().findChild(ActionEditor)


@pytest.mark.parametrize("codex_action", [
    {"type": "none"},
    {"type": "key", "usage": 104},
    {"type": "key_gesture", "usage": 104, "double_usage": 40},
])
def test_saved_codex_voice_uses_the_same_pending_state_in_editor_and_sidebar(
    session, monkeypatch, tmp_path, qtbot, codex_action,
):
    window, vm, gateway, _ = ready(
        session, monkeypatch, tmp_path, mode="codex", codex_action=codex_action,
    )
    window._select_physical_control("key.8")
    card = window._current_mapping_card()
    qtbot.waitUntil(card.isVisible)
    editor = card.findChild(ActionEditor)
    original = deepcopy(editor.action())
    config = deepcopy(vm.draft.config)
    assert not vm.draft.is_dirty
    assert not key_changed(window), "Saved CODEX voice must not claim a pending ordinary-key edit"
    editor.set_action({"type": "key", "usage": 10})
    assert key_changed(window)
    editor.set_action(original)
    assert not key_changed(window)
    assert vm.draft.config == config and not device_writes(gateway)


@pytest.mark.parametrize("control,name,wheel,display", [
    ("encoder.cw", "Scroll up", 1, "顺时针"),
    ("encoder.ccw", "Scroll down", -1, "逆时针"),
])
def test_saved_scroll_alias_does_not_claim_pending_name_edit(
    session, monkeypatch, tmp_path, qtbot, control, name, wheel, display,
):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    config = deepcopy(snapshot.config)
    profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
    mapping = next(m for m in profile["mappings"] if m["control_id"] == control)
    mapping.update(short_name=name, action={"type": "mouse", "x": 0, "y": 0, "wheel": wheel, "pan": 0, "button": 0})
    gateway.snapshot_ready.emit(replace(snapshot, config_result={
        **snapshot.config_result, "generation": snapshot.config_result["generation"] + 1,
        "config": config,
    }))
    window._select_physical_control(control)
    card = window._current_mapping_card()
    qtbot.waitUntil(card.isVisible)
    assert card.findChild(QLineEdit, "mappingShortNameEditor").text() == display
    assert not vm.draft.is_dirty
    assert not key_changed(window)
    assert not device_writes(gateway)
