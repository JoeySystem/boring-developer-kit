"""CODEX dictation choice stays separate from normal mappings and real trials."""
import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QPushButton

from controller_config.views.action_editor import ActionEditor
from controller_config.views.mode_page import ModeStage
from controller_config.voice_setup import voice_trial, save_voice_trial, voice_provider_key
from test_session_recovery import session
from test_voice_input_ui import _enable_voice, PRESET, confirm_shortcut
from test_ai_setup import readback
from notice_helpers import answer_warnings


HOLD = {"type": "key", "usage": 104}


def ready(session, monkeypatch, tmp_path, *, supported=True, applied=False):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    config = copy.deepcopy(snapshot.config)
    if applied:
        config['profiles'][0]['codex_voice'] = {**PRESET, 'double_modifiers': [227]}
    snapshot = replace(snapshot,
        capabilities={**snapshot.capabilities, 'features': {**snapshot.capabilities['features'], 'codex_voice': supported}},
        status={**snapshot.status, 'operating_mode': 'codex'},
        config_result={**snapshot.config_result, 'config': config,
                       'generation': snapshot.config_result['generation'] + int(applied)})
    window._close_control_editor()
    gateway.snapshot_ready.emit(snapshot)
    window._select_configuration_mode("codex")
    window._onboarding_settings = QSettings(str(tmp_path / 'voice.ini'), QSettings.IniFormat)
    window._select_control('key.8')
    return window, vm, gateway, snapshot


def test_hold_voice_readback_is_supported_and_preserved(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    config = copy.deepcopy(snapshot.config)
    config["profiles"][0]["codex_voice"] = copy.deepcopy(HOLD)
    snapshot = replace(
        snapshot,
        capabilities={
            **snapshot.capabilities,
            "features": {
                **snapshot.capabilities["features"],
                "codex_voice": True,
                "codex_voice_hold": True,
            },
        },
        config_result={
            **snapshot.config_result,
            "config": config,
            "generation": snapshot.config_result["generation"] + 1,
        },
    )
    window._close_control_editor()
    gateway.snapshot_ready.emit(snapshot)
    window._select_control("key.8")

    editor = window.findChild(ActionEditor)
    purpose = window.findChild(QComboBox, "mappingPurpose")
    assert purpose.currentText() == "第三方语音输入软件"
    assert editor.action() == HOLD
    assert not window._mapping_editor_has_uncommitted_changes()

    purpose.setCurrentIndex(0)
    assert editor.action() == {"type": "none"}
    purpose.setCurrentIndex(1)
    assert editor.action() == HOLD
    assert window._save_mapping_editor_draft()
    assert vm.draft.profile(0)["codex_voice"] == HOLD


def test_old_firmware_shows_official_voice_and_cannot_offer_false_override(session, monkeypatch, tmp_path):
    window, vm, _, _ = ready(session, monkeypatch, tmp_path, supported=False)
    assert window.findChild(QLabel, 'voiceFirmwareNotice') is not None
    assert window.findChild(QComboBox, 'mappingPurpose') is None
    with pytest.raises(ValueError, match='CODEX'):
        vm.set_codex_voice(0, PRESET)


def test_hold_voice_requires_hold_capability(session, monkeypatch, tmp_path):
    _, vm, _, _ = ready(session, monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="按住说话"):
        vm.set_codex_voice(0, HOLD)


def test_choice_applies_only_codex_voice_then_restores_official(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    original = copy.deepcopy(vm.draft.config)
    purpose = window.findChild(QComboBox, 'mappingPurpose')
    assert purpose.currentText() == 'Codex 自带语音'
    assert not window._mapping_editor_has_uncommitted_changes()
    purpose.setCurrentIndex(1)
    assert window._mapping_editor_has_uncommitted_changes()
    window.findChild(QPushButton, 'voiceConnectApp').click()
    guide = window._voice_setup_dialog
    guide.provider.setCurrentIndex(guide.provider.findData('typeless'))
    confirm_shortcut(guide)
    guide._adapt_button.click()
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    candidate = gateway.commands[-1].payload['config']
    assert candidate['profiles'][0]['mappings'] == original['profiles'][0]['mappings']
    assert candidate['profiles'][0]['codex_voice'] == {**PRESET, 'modifiers': []}
    assert not guide._next.isEnabled()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: window._voice_setup_dialog is not None)
    assert window._voice_setup_dialog._context_ready
    window._voice_setup_dialog.reject()
    assert vm.model.snapshot.active_profile['codex_voice'] == {**PRESET, 'modifiers': []}
    window.findChild(QComboBox, 'mappingPurpose').setCurrentIndex(0)
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    assert gateway.commands[-1].payload['config'] == original
    readback(vm, gateway, vm.model.snapshot)
    assert vm.model.snapshot.config == original
    assert window._voice_setup_dialog is None


@pytest.mark.parametrize('provider,usage', [('doubao',228),('qianwen',230)])
def test_codex_provider_adaptation_readback_preserves_normal_mode(session, monkeypatch, tmp_path, provider, usage):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path, applied=True)
    original_mappings = copy.deepcopy(snapshot.active_profile['mappings'])
    window._open_voice_setup('key.8', mode='codex')
    dialog = window._voice_setup_dialog
    assert dialog._context_ready
    dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
    confirm_shortcut(dialog, usage)
    dialog._adapt_button.click()
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    assert dialog.action['usage'] == 104
    readback(vm, gateway, snapshot)
    assert dialog.action == {**PRESET, 'usage': usage, 'modifiers': [], 'double_modifiers': [227]}
    assert vm.model.snapshot.active_profile['mappings'] == original_mappings
    assert vm.model.snapshot.active_profile['codex_voice'] == dialog.action
    assert dialog._context_ready
    gateway.status_updated.emit({**snapshot.status, 'operating_mode': 'normal'})
    assert dialog._context_ready and dialog._device_available()
    assert not dialog._runtime_ready()
    assert not dialog.practice.isEnabled() and not dialog._next.isEnabled()
    dialog.reject()


def test_config_import_rejects_codex_voice_on_old_firmware(session, monkeypatch, tmp_path):
    _, vm, _, _ = ready(session, monkeypatch, tmp_path, supported=False)
    candidate=copy.deepcopy(vm.draft.config)
    candidate['profiles'][0]['codex_voice']=PRESET
    assert any('CODEX' in e for e in vm.draft.validate_candidate(candidate, vm._contract))


def test_trial_does_not_leak_between_modes(tmp_path):
    settings=QSettings(str(tmp_path/'trial.ini'), QSettings.IniFormat)
    settings.setValue('ui/voice_input_software','typeless')
    settings.setValue(voice_provider_key('device',0,'codex'),'typeless')
    save_voice_trial(settings,'device',0,'key.8',PRESET,'typeless','codex',mode='codex')
    assert voice_trial(settings,'device',0,'key.8',PRESET,mode='codex')
    assert voice_trial(settings,'device',0,'key.8',PRESET) is None


def test_unapplied_codex_choice_survives_hardware_mode_switch(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    original=copy.deepcopy(vm.draft.profile(0)['mappings'])
    window.findChild(QComboBox, 'mappingPurpose').setCurrentIndex(1)
    gateway.status_updated.emit({**snapshot.status,'operating_mode':'normal'})
    assert window._configuration_mode == 'codex'
    assert window.findChild(ActionEditor).action() == PRESET
    assert 'codex_voice' not in vm.draft.profile(0)
    assert vm.draft.profile(0)['mappings'] == original
    assert not any(c.name in {'VALIDATE_CONFIG', 'SET_CONFIG'} for c in gateway.commands)
    gateway.status_updated.emit(snapshot.status)
    assert window.findChild(ActionEditor).action() == PRESET
    assert window.findChild(QPushButton,'applyMappingToDevice').isEnabled()


def test_cancelled_codex_doubao_choice_keeps_normal_provider_and_does_not_commit(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path, applied=True)
    window._onboarding_settings.setValue('ui/voice_input_software','typeless')
    window._open_voice_setup('key.8', mode='codex')
    dialog=window._voice_setup_dialog
    dialog.provider.setCurrentIndex(dialog.provider.findData('doubao'))
    assert window._onboarding_settings.value('ui/voice_input_software') == 'typeless'
    dialog.reject()
    window._open_voice_setup('key.8', mode='codex')
    assert window._voice_setup_dialog.provider.currentData() == ''
    assert window._onboarding_settings.value('ui/voice_input_software') == 'typeless'
    window._voice_setup_dialog.reject()


def test_rollback_and_mode_change_retains_unsupported_edit_without_render_error(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window.findChild(QComboBox,'mappingPurpose').setCurrentIndex(1)
    older=replace(snapshot,
        capabilities={**snapshot.capabilities,'features':{**snapshot.capabilities['features'],'codex_voice':False}},
        status={**snapshot.status,'operating_mode':'normal'})
    gateway.snapshot_ready.emit(older)
    assert window._configuration_mode == 'codex'
    assert window._pending_mapping_editing_state.action == PRESET
    assert 'codex_voice' not in vm.draft.profile(0)
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(ActionEditor).action() == PRESET
    assert not any(c.name in {'VALIDATE_CONFIG', 'SET_CONFIG'} for c in gateway.commands)


def test_unsupported_pending_voice_blocks_tab_switch_without_losing_edit(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window.findChild(QComboBox, 'mappingPurpose').setCurrentIndex(1)
    older = replace(snapshot, capabilities={**snapshot.capabilities,
        'features': {**snapshot.capabilities['features'], 'codex_voice': False}})
    gateway.snapshot_ready.emit(older)
    warnings = []
    answer_warnings(monkeypatch, lambda *args: warnings.append(args[2]))
    window._show_mode_screen(0)   # flip to the first NORMAL screen
    assert warnings
    assert window._configuration_mode == 'codex'
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    assert stage.state().word == 'CODEX'
    assert window._pending_mapping_editing_state.action == PRESET
    assert 'codex_voice' not in vm.draft.profile(0)
    assert not any(c.name in {'VALIDATE_CONFIG', 'SET_CONFIG'} for c in gateway.commands)
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(ActionEditor).action() == PRESET


@pytest.mark.parametrize('language', ['zh_CN','en_US','ja_JP'])
def test_codex_choice_language_layout(session, monkeypatch, tmp_path, qtbot, language):
    from pathlib import Path
    from controller_config.i18n import translate_ui_text
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path, applied=True)
    window._language_manager.set_language(language)
    window.resize(1100,700)
    window.show()
    qtbot.wait(60)
    card = window._current_mapping_card()
    assert card is not None
    purpose = card.findChild(QComboBox,'mappingPurpose')
    assert purpose.itemText(0) == translate_ui_text('Codex 自带语音')
    assert purpose.itemText(1) == translate_ui_text('第三方语音输入软件')
    # The page says which mode is being configured: the CODEX screen and its bar.
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    assert stage.state().word == 'CODEX'
    assert stage.bar.state().profile_name == 'Codex'
    editor=card.findChild(ActionEditor)
    original=editor.action()
    purpose.setCurrentIndex(0)
    assert editor.action() == {'type':'none'}
    purpose.setCurrentIndex(1)
    assert editor.action() == original
    apply=card.findChild(QPushButton,'applyMappingToDevice')
    assert not apply.isVisible()
    assert card.findChild(QPushButton, 'voiceConnectApp').isVisible()
    # The list is shown as segments (用途三格); whether they fit: see the test below.
    segments = card.findChild(QFrame, 'modeCardPurposes')
    assert segments.isVisible() and not purpose.isVisible()
    output=tmp_path/'codex-voice-choice'
    output.mkdir(parents=True,exist_ok=True)
    window.grab().save(str(output/f'{language}.png'))
    window._language_manager.set_language('zh_CN')


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_codex_choice_segments_fit_the_card(session, monkeypatch, tmp_path, qtbot, language):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path, applied=True)
    window._language_manager.set_language(language)
    try:
        window.resize(1100, 700)
        window.show()
        qtbot.wait(60)
        card = window._current_mapping_card()
        segments = card.findChild(QFrame, 'modeCardPurposes')
        assert segments.isVisible()
        assert segments.mapTo(card, QPoint()).x() + segments.width() <= card.width()
    finally:
        window._language_manager.set_language('zh_CN')
