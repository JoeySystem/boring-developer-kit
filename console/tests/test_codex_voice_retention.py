"""A confirmed CODEX shortcut survives official mode; unfinished edits do not."""
import copy
from dataclasses import replace

import pytest

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QComboBox, QPushButton

from controller_config.voice_setup import (confirmed_voice_choice,
    confirmed_voice_choice_for_provider, save_confirmed_voice_choice,
    voice_provider_key)
from controller_config.views.action_editor import ActionEditor
from controller_config.views.device_silhouette import create_device_silhouette
from controller_config.views.device_silhouette import DeviceModelCanvas
from controller_config.actions import describe_action
from test_codex_voice_choice import ready
from test_session_recovery import session
from test_ai_setup import readback


CUSTOM = {"type": "key_gesture", "usage": 231, "double_usage": 40, "double_modifiers": [227]}


def test_confirmed_choice_is_scoped_and_survives_settings_reload(tmp_path):
    path = str(tmp_path / "remembered.ini")
    settings = QSettings(path, QSettings.IniFormat)
    save_confirmed_voice_choice(settings, "one", 0, CUSTOM, "doubao")
    settings = QSettings(path, QSettings.IniFormat)
    assert confirmed_voice_choice(settings, "one", 0) == {"action": CUSTOM, "provider": "doubao"}
    for serial, profile, mode in [("two", 0, "codex"), ("one", 1, "codex"), ("one", 0, "normal")]:
        assert confirmed_voice_choice(settings, serial, profile, mode=mode) is None
    save_confirmed_voice_choice(settings, "one", 0, {"type": "none"}, "")
    assert confirmed_voice_choice(settings, "one", 0)["action"] == CUSTOM


def test_confirmed_shortcuts_are_remembered_per_provider(tmp_path):
    settings = QSettings(str(tmp_path / "providers.ini"), QSettings.IniFormat)
    qianwen = {**CUSTOM, "usage": 230}
    typeless = {**CUSTOM, "usage": 104}
    save_confirmed_voice_choice(settings, "one", 0, qianwen, "qianwen")
    save_confirmed_voice_choice(settings, "one", 0, typeless, "typeless")
    assert confirmed_voice_choice(settings, "one", 0) == {
        "action": typeless, "provider": "typeless"}
    assert confirmed_voice_choice_for_provider(settings, "one", 0, "qianwen") == {
        "action": qianwen, "provider": "qianwen"}
    assert confirmed_voice_choice_for_provider(settings, "one", 0, "typeless") == {
        "action": typeless, "provider": "typeless"}
    assert settings.value(voice_provider_key("one", 0, "codex")) == "typeless"


def test_readback_retained_after_applying_official_and_recreating_editor(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    serial = snapshot.identity["serial"]
    original_normal = copy.deepcopy(snapshot.active_profile["mappings"])
    vm.set_codex_voice(0, CUSTOM)
    vm.prepare_device_write(confirm_after_validation=True)
    assert confirmed_voice_choice(window._onboarding_settings, serial, 0) is None
    readback(vm, gateway, snapshot)
    saved = confirmed_voice_choice(window._onboarding_settings, serial, 0)
    assert saved == {"action": CUSTOM, "provider": ""}
    # Device readback identifies the key, not which input software owns it.
    # Simulate the separately confirmed provider persisted by the guide.
    save_confirmed_voice_choice(window._onboarding_settings, serial, 0, CUSTOM, "doubao")
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(0)
    window.findChild(QPushButton, "applyMappingToDevice").click()
    readback(vm, gateway, vm.model.snapshot)
    assert "codex_voice" not in vm.model.snapshot.active_profile
    assert vm.model.snapshot.active_profile["mappings"] == original_normal
    window._close_control_editor()
    window._onboarding_settings = QSettings(window._onboarding_settings.fileName(), QSettings.IniFormat)
    window._select_control("key.8")
    assert window.findChild(ActionEditor).action() == {"type": "none"}
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    assert window.findChild(ActionEditor).action() == CUSTOM
    # An unfinished provider choice must not replace the saved mapping's app.
    window._onboarding_settings.setValue(voice_provider_key(serial, 0, "codex"), "qianwen")
    window.findChild(QPushButton, "voiceConnectApp").click()
    assert window._voice_setup_dialog.provider.currentData() == "doubao"
    assert window._voice_setup_dialog._custom_shortcut_chosen
    assert window._voice_setup_dialog._requested_action == CUSTOM
    window._voice_setup_dialog.reject()


def test_unapplied_external_draft_does_not_overwrite_remembered_choice(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    serial = snapshot.identity["serial"]
    save_confirmed_voice_choice(window._onboarding_settings, serial, 0, CUSTOM, "doubao")
    vm.set_codex_voice(0, {**CUSTOM, "usage": 230})
    window.render(vm.model)
    assert confirmed_voice_choice(window._onboarding_settings, serial, 0) == {"action": CUSTOM, "provider": "doubao"}


def test_remembered_shortcut_survives_empty_modifier_normalization(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    serial = snapshot.identity["serial"]
    save_confirmed_voice_choice(window._onboarding_settings, serial, 0, CUSTOM, "doubao")
    candidate = {**CUSTOM, "modifiers": []}
    window._open_voice_setup("key.8", mode="codex", candidate_action=candidate)
    dialog = window._voice_setup_dialog
    assert dialog.provider.currentData() == "doubao"
    assert dialog._custom_shortcut_chosen
    assert dialog._requested_action == candidate
    assert not dialog._toggle_supported.isChecked()
    assert dialog._adapt_button.isEnabled()
    assert not dialog._toggle_supported.isEnabled()
    dialog.reject()


@pytest.mark.parametrize("custom,supported", [(None, True), (CUSTOM, True), (CUSTOM, False)])
def test_codex_model_shows_codex_voice_instead_of_normal_mapping(session, monkeypatch, tmp_path, qtbot, custom, supported):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path, supported=supported)
    config = copy.deepcopy(snapshot.config)
    if custom is not None:
        config["profiles"][0]["codex_voice"] = custom
    snapshot = replace(snapshot, config_result={**snapshot.config_result, "config": config})
    normal = {**snapshot.mappings, "key.8": {"action": {"type": "key", "usage": 105}, "short_name": "F14 normal"}}
    shell = create_device_silhouette(snapshot, mappings=normal)
    qtbot.addWidget(shell)
    button = next(w for w in shell.findChildren(QPushButton) if w.property("controlId") == "key.8")
    expected = (describe_action(custom, platform=str(snapshot.status.get("platform", "")), compact=True, control_id="key.8")
                if custom and supported else "Codex 自带语音")
    assert expected in button.toolTip()
    assert "F14" not in button.toolTip()
    assert expected in shell.findChild(DeviceModelCanvas)._control_labels["key.8"]
    assert normal["key.8"]["action"]["usage"] == 105
