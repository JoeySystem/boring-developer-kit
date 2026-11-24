"""Provider adaptation uses normal writes and never treats a preset as a trial."""
import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLineEdit, QPushButton

from controller_config.voice_setup import adapted_voice_action
from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.transactions import ConfigTransactionState
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from test_session_recovery import session
from test_voice_input_ui import _enable_voice, PRESET, confirm_shortcut
from test_ai_setup import readback, ready, select_two
from test_write_transaction import _ack, _active_status


@pytest.mark.parametrize("provider,usage", [("doubao", 228), ("qianwen", 230)])
def test_provider_preset_preserves_independent_send(provider, usage):
    original = {**PRESET, "double_modifiers": [227]}
    assert adapted_voice_action(original, provider, "darwin") == {**original, "usage": usage}
    assert original["usage"] == 104
    assert adapted_voice_action(original, provider, "win32") == original
    assert adapted_voice_action(original, "typeless", "darwin") == original
    matching = {**original, "usage": usage}
    assert adapted_voice_action(matching, provider, "darwin") == matching


def applied_guide(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    config = copy.deepcopy(snapshot.config)
    profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
    profile["mappings"] = [m for m in profile["mappings"] if m["control_id"] != "key.8"]
    profile["mappings"].append({"control_id": "key.8", "short_name": "语音输入",
                                "action": {**PRESET, "double_modifiers": [227]}})
    snapshot = replace(snapshot, config_result={**snapshot.config_result, "config": config,
                       "generation": snapshot.config_result["generation"] + 1})
    window._close_control_editor()
    gateway.snapshot_ready.emit(snapshot)
    window._onboarding_settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    window._open_voice_setup("key.8")
    return window, vm, gateway, snapshot, window._voice_setup_dialog


@pytest.mark.parametrize("provider,usage", [("doubao", 228), ("qianwen", 230)])
def test_apply_requires_readback_and_binding_confirmation(session, monkeypatch, tmp_path, provider, usage):
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    commands = list(gateway.commands)
    dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
    assert gateway.commands == commands
    assert not dialog._next.isEnabled()
    dialog._toggle_supported.setChecked(True)
    assert not dialog._next.isEnabled()  # F13 cannot become compatible by ticking a box.
    if provider == "doubao":
        assert dialog._adapt_button.isEnabled()
        assert dialog._adapt_button.text() == "应用豆包推荐快捷键"
    else:
        assert not dialog._adapt_button.isEnabled()
        confirm_shortcut(dialog, usage)
    dialog._adapt_button.click()
    assert dialog._adapting and not dialog.provider.isEnabled()
    assert dialog._context_notice.isHidden()  # Writing is not a disconnection.
    assert dialog.action["usage"] == 104
    assert not dialog._next.isEnabled()
    readback(vm, gateway, snapshot)
    expected = {**PRESET, "usage": usage, "double_modifiers": [227]}
    if provider != "doubao":
        expected["modifiers"] = []
    assert dialog.action == expected
    assert not dialog._adapting and not vm.draft.is_dirty
    assert dialog._context_ready
    if provider == "doubao":
        assert dialog._pages.currentIndex() == 0
        assert dialog._next.isEnabled()
        dialog._next.click()
    else:
        assert dialog._toggle_supported.isChecked()
    assert dialog._pages.currentIndex() == 1 and not dialog._next.isEnabled()
    assert not dialog.practice.completed
    dialog.reject()


def test_failed_adaptation_can_retry_without_overwriting_other_drafts(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 228)
    dialog._adapt_button.click()
    gateway.command_failed.emit("VALIDATE_CONFIG", BootstrapError(BootstrapKind.READ_FAILED, "验证失败", "test"))
    assert vm.write_transaction.state is ConfigTransactionState.FAILED
    assert dialog.action["usage"] == 104 and not dialog._next.isEnabled()
    assert not dialog._adapting
    assert dialog._adapt_error.text()
    dialog._adapt_button.click()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 228
    dialog.provider.setCurrentIndex(dialog.provider.findData("qianwen"))
    confirm_shortcut(dialog, 230)
    vm.rename_profile(vm.draft.config["active_profile"], "保留这个修改")
    commands = list(gateway.commands)
    dialog._adapt_button.click()
    assert gateway.commands == commands
    assert vm.draft.profile(vm.draft.config["active_profile"])["name"] == "保留这个修改"
    assert dialog.action["usage"] == 228 and not dialog._next.isEnabled()
    dialog.reject()


def test_failed_adaptation_cancel_discards_only_the_guide_owned_draft(
    session, monkeypatch, tmp_path,
):
    _, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    original = copy.deepcopy(vm.draft.config)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    dialog._adapt_button.click()
    gateway.command_failed.emit(
        "VALIDATE_CONFIG",
        BootstrapError(BootstrapKind.READ_FAILED, "验证失败", "test"),
    )
    assert vm.draft.is_dirty and vm.draft.config != original
    dialog.reject()
    assert not vm.draft.is_dirty
    assert vm.draft.config == original


def test_legacy_confirmed_doubao_name_change_keeps_its_verified_shortcut(
    session, monkeypatch, tmp_path,
):
    from controller_config.voice_setup import save_voice_trial

    window, vm, gateway, snapshot, first = applied_guide(
        session, monkeypatch, tmp_path,
    )
    action = copy.deepcopy(first.action)
    settings = window._onboarding_settings
    settings.setValue(first._provider_key, "doubao")
    save_voice_trial(
        settings, vm.draft.serial, vm.model.snapshot.active_profile_id,
        first._control_id, action, "doubao", "codex", mode=first._mode,
    )
    first.reject()
    window._select_control("key.8")
    window.findChild(QLineEdit, "mappingShortNameEditor").setText("我的豆包语音")
    window.findChild(QPushButton, "voiceConnectApp").click()
    dialog = window._voice_setup_dialog
    assert dialog._history_panel.isVisible()
    dialog._change_settings_button.click()
    assert dialog._adapt_button.isVisible()
    assert dialog._adapt_button.text() == "保存快捷键"
    dialog._adapt_button.click()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    candidate = gateway.commands[-1].payload["config"]
    profile = next(
        item for item in candidate["profiles"]
        if item["id"] == candidate["active_profile"]
    )
    mapping = next(
        item for item in profile["mappings"] if item["control_id"] == "key.8"
    )
    assert mapping["action"] == action
    readback(vm, gateway, vm.model.snapshot)
    assert vm.model.snapshot.mappings["key.8"]["action"] == action
    assert vm.model.snapshot.mappings["key.8"]["short_name"] == "我的豆包语音"


def test_legacy_doubao_send_recovery_changes_only_the_double_click_key(
    session, monkeypatch, tmp_path,
):
    from controller_config.voice_setup import (
        save_confirmed_voice_choice, save_voice_trial,
    )

    window, vm, gateway, snapshot, first = applied_guide(
        session, monkeypatch, tmp_path,
    )
    action = copy.deepcopy(first.action)
    settings = window._onboarding_settings
    settings.setValue(first._provider_key, "doubao")
    save_confirmed_voice_choice(
        settings, vm.draft.serial, vm.model.snapshot.active_profile_id,
        action, "doubao", mode=first._mode,
    )
    save_voice_trial(
        settings, vm.draft.serial, vm.model.snapshot.active_profile_id,
        first._control_id, action, "doubao", "codex", mode=first._mode,
    )
    first.reject()
    window._open_voice_setup("key.8")
    dialog = window._voice_setup_dialog
    assert dialog._history_panel.isVisible()
    dialog._repair("send")
    dialog._shortcut_editors[1][1]._shortcut_recorder.shortcut_recorded.emit(
        {"type": "key", "usage": 41, "modifiers": []}
    )
    assert dialog._adapt_button.text() == "保存快捷键"
    dialog._adapt_button.click()
    candidate = gateway.commands[-1].payload["config"]
    profile = next(
        item for item in candidate["profiles"]
        if item["id"] == candidate["active_profile"]
    )
    candidate_action = next(
        item["action"] for item in profile["mappings"]
        if item["control_id"] == "key.8"
    )
    assert candidate_action["usage"] == action["usage"]
    assert candidate_action.get("modifiers", []) == action.get("modifiers", [])
    assert candidate_action["double_usage"] == 41
    readback(vm, gateway, vm.model.snapshot)
    applied = vm.model.snapshot.mappings["key.8"]["action"]
    assert applied["usage"] == action["usage"]
    assert applied.get("modifiers", []) == action.get("modifiers", [])
    assert applied["double_usage"] == 41


def test_nested_common_ai_guide_tracks_adapted_readback(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot, settings, outer = ready(session, tmp_path, monkeypatch)
    select_two(outer, voice="external")
    outer._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: outer._pages.currentIndex() == 3)
    outer._open_binding()
    dialog = outer._voice_dialog
    dialog.provider.setCurrentIndex(dialog.provider.findData("qianwen"))
    confirm_shortcut(dialog, 230)
    snapshot = vm.model.snapshot
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 230 and dialog._context_ready
    assert outer._candidate == vm.model.snapshot.config
    assert not outer._error_text
    assert not outer._practice.completed and not outer._next.isEnabled()
    dialog.reject()
    outer.reject()


def test_reconnection_needs_matching_readback_not_just_a_link(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 228)
    dialog._adapt_button.click()
    candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    digest = gateway.commands[-1].payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert gateway.commands[-1].name == "SET_CONFIG"
    gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "test")
    assert vm.write_transaction.state is ConfigTransactionState.UNKNOWN
    assert not dialog._next.isEnabled() and dialog.action["usage"] == 104
    dialog.reject()
    assert window._voice_setup_dialog is dialog and dialog._adapting
    gateway.snapshot_ready.emit(replace(snapshot,
        status=_active_status(snapshot, digest, snapshot.config_result["generation"] + 1),
        config_result={
        **snapshot.config_result, "generation": snapshot.config_result["generation"] + 1,
        "config": candidate, "digest": digest}))
    assert dialog.action["usage"] == 228
    assert dialog._context_ready and dialog._next.isEnabled()
    assert dialog._pages.currentIndex() == 0
    dialog.reject()


def test_compatible_doubao_chord_is_not_replaced():
    action = {**PRESET, "usage": 227, "modifiers": [226]}
    assert adapted_voice_action(action, "doubao", "darwin") == action


@pytest.mark.parametrize("provider,usage", [("doubao", 228), ("qianwen", 230)])
def test_adapted_preset_can_restore_previous_send_when_voice_is_disabled(contract, provider, usage):
    from controller_config.ai_setup import prepare_ai_profiles
    from controller_config.drafts import LocalDraft
    from controller_config.transport.demo import _power_v2_snapshot
    draft = LocalDraft.from_snapshot(_power_v2_snapshot(contract, read_only=False), contract)
    assert "key_gesture" in draft.actions   # Power V2 firmware lists it
    config, entries = prepare_ai_profiles(draft, ["codex"], "codex", "external", {}, platform="darwin")
    draft.config = config
    profile = entries["codex"]["profile_id"]
    action = adapted_voice_action(PRESET, provider, "darwin")
    draft.set_mapping(profile, "key.8", "语音输入", action)
    entries["codex"]["voice_action"] = action
    before = entries["codex"]["before_voice"]
    config, _ = prepare_ai_profiles(draft, ["codex"], "codex", "none", entries, platform="darwin")
    draft.config = config
    assert draft.mapping(profile, "key.8") == before


def test_provider_guidance_three_language_layout(session, monkeypatch, tmp_path, qtbot):
    import os
    from pathlib import Path
    from controller_config.i18n import translate_ui_text
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    output = Path(os.environ.get("VOICE_PREVIEW_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    for provider in ("doubao", "qianwen"):
        dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
        confirm_shortcut(dialog, 228 if provider == "doubao" else 230)
        for language in ("zh_CN", "en_US", "ja_JP"):
            window._language_manager.set_language(language)
            dialog.resize(480, 620)
            qtbot.wait(30)
            assert dialog.provider.currentText() == translate_ui_text("豆包输入法" if provider == "doubao" else "千问输入法")
            assert dialog._toggle_supported.sizeHint().width() <= dialog._body.viewport().width()
            assert dialog._next.mapTo(dialog, dialog._next.rect().bottomRight()).y() < dialog.height()
            dialog.grab().save(str(output / f"{provider}-{language}.png"))
    window._language_manager.set_language("zh_CN")
    dialog.reject()


@pytest.mark.parametrize("language", ["zh_CN", "en_US", "ja_JP"])
def test_binding_confirmation_stays_visible_with_manual_editors_open(session, monkeypatch, tmp_path, qtbot, language):
    from PySide6.QtCore import QPoint
    from controller_config.i18n import translate_ui_text
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    window._language_manager.set_language(language)
    dialog.provider.setCurrentIndex(dialog.provider.findData("qianwen"))
    confirm_shortcut(dialog, 230)
    dialog._toggle_supported.setChecked(False)
    for _, editor in dialog._shortcut_editors:
        editor._manual_toggle.setChecked(True)
    dialog._custom_toggle.setChecked(True)
    dialog.resize(480, 620)
    qtbot.wait(30)
    import os
    from pathlib import Path
    if os.environ.get("VOICE_PREVIEW_DIR"):
        output = Path(os.environ["VOICE_PREVIEW_DIR"])
        output.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(output / f"binding-footer-expanded-{language}.png"))
    assert not dialog._body.isAncestorOf(dialog._toggle_supported)
    assert dialog._toggle_supported.isVisible()
    assert dialog._toggle_supported.mapTo(dialog, QPoint(0, 0)).y() >= dialog._body.geometry().bottom()
    assert dialog._toggle_supported.mapTo(dialog, dialog._toggle_supported.rect().bottomRight()).y() < dialog._adapt_button.y()
    assert not dialog._adapt_button.isEnabled()
    assert dialog._adapt_notice.text() == translate_ui_text("请确认上方的快捷键和启停方式，再继续。")
    dialog._toggle_supported.setChecked(True)
    assert dialog._adapt_button.isEnabled()
    assert dialog._adapt_notice.text() == translate_ui_text("保存成功后，自动进入实体练习。")
    dialog._step(1)
    assert not dialog._toggle_supported.isVisible()
    window._language_manager.set_language("zh_CN")
    dialog.reject()


def test_changed_device_mapping_is_not_overwritten_from_an_old_guide(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 228)
    config = copy.deepcopy(snapshot.config)
    profile = next(p for p in config["profiles"] if p["id"] == snapshot.active_profile_id)
    mapping = next(m for m in profile["mappings"] if m["control_id"] == "key.8")
    mapping["action"] = {**PRESET, "double_usage": 41}
    gateway.snapshot_ready.emit(replace(snapshot, config_result={
        **snapshot.config_result, "config": config, "generation": snapshot.config_result["generation"] + 1}))
    assert not dialog._context_ready and not dialog._adapt_button.isEnabled()
    commands = list(gateway.commands)
    dialog._apply_adaptation()
    assert gateway.commands == commands
    assert vm.model.snapshot.mappings["key.8"]["action"]["double_usage"] == 41
    dialog.reject()


def test_open_inspector_follows_applied_shortcut_without_an_extra_apply(session, monkeypatch, tmp_path, qtbot):
    from controller_config.views.action_editor import ActionEditor
    from PySide6.QtWidgets import QPushButton
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    window.show()
    window._confirm_leave_mapping_editor = lambda: True
    window._select_control("key.8")
    for provider, usage in (("doubao", 228), ("qianwen", 230)):
        dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
        confirm_shortcut(dialog, 228 if provider == "doubao" else 230)
        dialog._adapt_button.click()
        readback(vm, gateway, vm.model.snapshot)
        card = window._current_mapping_card()
        assert card.findChild(ActionEditor).action()["usage"] == usage
        assert not window._mapping_editor_has_uncommitted_changes()
        assert not card.findChild(QPushButton, "applyMappingToDevice").isEnabled()
        assert card.findChild(QPushButton, "voiceConnectApp").isEnabled()
    dialog.reject()


@pytest.mark.parametrize("provider", ["doubao", "qianwen", "typeless", "other"])
def test_provider_selection_does_not_guess_existing_shortcut(session, monkeypatch, tmp_path, provider):
    _, _, gateway, _, dialog = applied_guide(session, monkeypatch, tmp_path)
    before = copy.deepcopy(dialog._requested_action)
    commands = list(gateway.commands)
    dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
    assert dialog._requested_action == before
    assert gateway.commands == commands
    assert not dialog._toggle_supported.isChecked()
    assert not dialog._next.isEnabled()
    dialog.reject()


def test_doubao_rejects_f13_and_respects_recorded_right_command(session, monkeypatch, tmp_path):
    from controller_config.actions import describe_action
    _, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 104)
    assert dialog._shortcut_problem()
    assert dialog._adapt_button.isEnabled()
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 228
    assert dialog._pages.currentIndex() == 0 and dialog._next.isEnabled()
    dialog._shortcut_toggle.setChecked(True)
    confirm_shortcut(dialog, 231)
    assert not dialog._shortcut_problem()
    assert describe_action({"type": "key", "usage": 231}, platform="macos") in dialog._shortcut_summary.text()
    assert dialog._adapt_button.isEnabled()
    snapshot = vm.model.snapshot
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 231
    assert dialog.action["double_usage"] == 40
    assert dialog.action["double_modifiers"] == [227]
    assert dialog._pages.currentIndex() == 0
    assert not dialog.practice.completed and dialog._next.isEnabled()
    dialog.reject()


def test_send_only_change_preserves_confirmed_voice_shortcut(session, monkeypatch, tmp_path):
    _, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 231)
    dialog._shortcut_editors[1][1]._shortcut_recorder.shortcut_recorded.emit(
        {"type": "key", "usage": 40, "modifiers": [225]})
    assert dialog._toggle_supported.isChecked()
    assert dialog._requested_action["usage"] == 231
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 231
    assert dialog.action["double_modifiers"] == [225]
    dialog.reject()
