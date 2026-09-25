"""Provider adaptation uses normal writes and never treats a preset as a trial."""
import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings

from controller_config.voice_setup import adapted_voice_action, save_confirmed_voice_choice
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
    assert dialog._requested_action["usage"] == usage
    assert dialog._shortcut_panel.isHidden()
    assert dialog._adapt_button.isEnabled()
    assert not dialog._toggle_supported.isVisible()
    if provider == "doubao":
        assert not dialog._open_voice_button.isVisible()
    dialog._adapt_button.click()
    assert dialog._adapting and not dialog.provider.isEnabled()
    assert dialog._context_notice.isHidden()  # Writing is not a disconnection.
    assert dialog.action["usage"] == 104
    assert not dialog._next.isEnabled()
    readback(vm, gateway, snapshot)
    assert dialog.action == {**PRESET, "usage": usage, "modifiers": [], "double_modifiers": [227]}
    assert not dialog._adapting and not vm.draft.is_dirty
    assert dialog._context_ready
    assert not dialog._toggle_supported.isChecked()
    assert dialog._toggle_supported.isVisible() and dialog._toggle_supported.isEnabled()
    assert dialog._device_stage_status.text() == "✓ 设备快捷键 · 已保存并读回"
    assert dialog._software_stage_status.text() == "2  输入软件绑定 · 待完成"
    if provider == "doubao":
        assert dialog._open_voice_button.isVisible() and dialog._open_voice_button.isEnabled()
    assert dialog._pages.currentIndex() == 0
    dialog._toggle_supported.click()
    assert dialog._software_stage_status.text() == "✓ 输入软件绑定 · 已确认"
    assert dialog._next.isEnabled()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 1
    assert not dialog._next.isEnabled()
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
    gateway.snapshot_ready.emit(replace(snapshot,
        status=_active_status(snapshot, digest, snapshot.config_result["generation"] + 1),
        config_result={
        **snapshot.config_result, "generation": snapshot.config_result["generation"] + 1,
        "config": candidate, "digest": digest}))
    assert dialog.action["usage"] == 228
    assert dialog._context_ready and not dialog._next.isEnabled()
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
    draft.actions = (*draft.actions, "key_gesture")
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
        hint = (
            "推荐使用右 Control。设备保存完成后，在豆包“免按模式”中设为右 Control，并切换到豆包输入法或开启全局唤起。"
            if provider == "doubao" else
            "推荐使用右 Option。设备保存完成后，在千问“语音输入”中设为右 Option，并打开“短按也唤起语音输入”。"
        )
        for language in ("zh_CN", "en_US", "ja_JP"):
            window._language_manager.set_language(language)
            dialog.resize(480, 620)
            qtbot.wait(30)
            assert dialog.provider.currentText() == translate_ui_text("豆包输入法" if provider == "doubao" else "千问输入法")
            assert dialog._step_number.text() == "1/2 ·"
            assert dialog._heading.text() == translate_ui_text("连接语音输入软件")
            assert dialog._binding_hint.text() == translate_ui_text(hint)
            assert dialog._recommended_button.text() == translate_ui_text("使用推荐快捷键")
            assert dialog._device_stage_status.text() == translate_ui_text("1  设备快捷键 · 待保存")
            assert dialog._software_stage_status.text() == translate_ui_text("2  输入软件绑定 · 保存设备后继续")
            assert dialog._toggle_supported.sizeHint().width() <= dialog._body.viewport().width()
            assert dialog._next.mapTo(dialog, dialog._next.rect().bottomRight()).y() < dialog.height()
            dialog.grab().save(str(output / f"{provider}-{language}.png"))
    window._language_manager.set_language("zh_CN")
    dialog.reject()


def test_qianwen_help_explains_manual_settings_path(session, monkeypatch, tmp_path):
    _, _, _, _, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("qianwen"))
    dialog._more_toggle.click()
    assert dialog._more_panel.isVisible()
    dialog._help_button.click()
    assert dialog._binding_help.isVisible()
    assert "Mac 顶部输入法菜单" in dialog._binding_help.text()
    assert "千问设置 → 语音输入" in dialog._binding_help.text()
    assert "右 Option" in dialog._binding_help.text()
    dialog._more_toggle.click()
    assert not dialog._more_panel.isVisible()
    assert not dialog._binding_help.isVisible()
    dialog.reject()


@pytest.mark.parametrize("language", ["zh_CN", "en_US", "ja_JP"])
def test_binding_confirmation_stays_visible_with_manual_editors_open(session, monkeypatch, tmp_path, qtbot, language):
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
    assert not dialog._toggle_supported.isVisible()
    assert not dialog._open_voice_button.isVisible()
    assert dialog._adapt_button.isEnabled()
    assert dialog._adapt_notice.text() == translate_ui_text("先保存到设备，再到语音软件中完成上方绑定。")
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


@pytest.mark.parametrize("provider,usage", [
    ("doubao", 228),
    ("qianwen", 230),
    ("typeless", 104),
])
def test_known_provider_selection_prepares_tested_shortcut_without_writing(
        session, monkeypatch, tmp_path, provider, usage):
    _, _, gateway, _, dialog = applied_guide(session, monkeypatch, tmp_path)
    commands = list(gateway.commands)
    dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
    assert dialog._requested_action["usage"] == usage
    assert gateway.commands == commands
    assert dialog._custom_shortcut_chosen
    assert not dialog._toggle_supported.isChecked()
    assert not dialog._next.isEnabled()
    assert dialog._shortcut_panel.isHidden()
    assert dialog._more_toggle.isVisible()
    assert not dialog._shortcut_toggle.isVisible()
    dialog._more_toggle.setChecked(True)
    assert dialog._shortcut_toggle.isVisible()
    assert dialog._adapt_button.isVisible() is (usage != 104)
    dialog.reject()


def test_unlisted_provider_still_requires_its_real_shortcut(session, monkeypatch, tmp_path):
    _, _, gateway, _, dialog = applied_guide(session, monkeypatch, tmp_path)
    before = copy.deepcopy(dialog._requested_action)
    commands = list(gateway.commands)
    dialog.provider.setCurrentIndex(dialog.provider.findData("other"))
    assert dialog._requested_action == before
    assert gateway.commands == commands
    assert not dialog._custom_shortcut_chosen
    assert not dialog._adapt_button.isEnabled()
    assert dialog._shortcut_panel.isVisible()
    assert dialog._shortcut_toggle.isHidden()
    dialog.reject()


def test_switching_provider_restores_its_confirmed_voice_key_but_keeps_send(
        session, monkeypatch, tmp_path):
    window, _, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    serial = snapshot.identity["serial"]
    typeless = {**PRESET, "double_modifiers": [225]}
    qianwen = {**PRESET, "usage": 230, "double_modifiers": [225]}
    save_confirmed_voice_choice(window._onboarding_settings, serial,
                                snapshot.active_profile_id, typeless, "typeless", mode="normal")
    save_confirmed_voice_choice(window._onboarding_settings, serial,
                                snapshot.active_profile_id, qianwen, "qianwen", mode="normal")
    selection_key = "ui/voice_input_software"
    commands = list(gateway.commands)

    dialog.provider.setCurrentIndex(dialog.provider.findData("typeless"))
    assert dialog._requested_action["usage"] == 104
    assert dialog._requested_action["double_usage"] == PRESET["double_usage"]
    assert dialog._requested_action.get("double_modifiers") == [227]
    assert dialog._custom_shortcut_chosen
    # A dropdown choice alone is not an applied configuration.
    assert window._onboarding_settings.value(selection_key) == "qianwen"

    dialog.provider.setCurrentIndex(dialog.provider.findData("qianwen"))
    assert dialog._requested_action["usage"] == 230
    assert dialog._requested_action.get("double_modifiers") == [227]
    assert dialog._custom_shortcut_chosen
    assert gateway.commands == commands
    dialog.reject()


def test_doubao_rejects_f13_and_respects_recorded_right_command(session, monkeypatch, tmp_path):
    from controller_config.actions import describe_action
    _, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    confirm_shortcut(dialog, 104)
    assert dialog._shortcut_problem()
    assert not dialog._adapt_button.isEnabled()
    commands = list(gateway.commands)
    dialog._adapt_button.click()
    assert gateway.commands == commands
    confirm_shortcut(dialog, 231)
    assert not dialog._shortcut_problem()
    assert describe_action({"type": "key", "usage": 231}, platform="macos") in dialog._shortcut_summary.text()
    assert dialog._adapt_button.isEnabled()
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action["usage"] == 231
    assert dialog.action["double_usage"] == 40
    assert dialog.action["double_modifiers"] == [227]
    assert dialog._pages.currentIndex() == 1
    assert not dialog.practice.completed and not dialog._next.isEnabled()
    dialog.reject()


def test_common_macos_editing_shortcut_is_rejected_and_can_reset_to_recommended(
        session, monkeypatch, tmp_path):
    _, _, gateway, _, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData("typeless"))
    dialog._shortcut_editors[0][1]._shortcut_recorder.shortcut_recorded.emit(
        {"type": "key", "usage": 25, "modifiers": [227]}
    )

    assert "macOS" in dialog._shortcut_problem()
    assert not dialog._adapt_button.isEnabled()
    assert not any(command.name == "VALIDATE_CONFIG" for command in gateway.commands)

    dialog._recommended_button.click()
    assert dialog._requested_action["usage"] == 104
    assert not dialog._shortcut_problem()
    assert not dialog._adaptation_needed()
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
