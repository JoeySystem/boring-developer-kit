from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QMessageBox, QPushButton

from controller_config.ai_setup import (
    AI_APPLICATIONS, AI_SELECTION_KEY, AI_SETUP_COMPLETED_KEY,
    AI_SETUP_DEFERRED_KEY, VOICE_ACTION,
    installed_desktop_app, load_choices, load_device_choices, prepare_ai_profiles, save_device_choices,
)
from controller_config.drafts import LocalDraft
from controller_config.models import AppState, ScreenModel
from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
from controller_config.transactions import ConfigTransactionState
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.views.ai_setup import AISetupDialog
from test_session_recovery import session
from test_write_transaction import _ack, _active_status
from notice_helpers import answer_warnings


def ready(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr("controller_config.views.ai_setup.sys.platform", "darwin")
    settings = QSettings(str(tmp_path / "ai.ini"), QSettings.IniFormat)
    window._onboarding_settings = settings
    snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "normal"})
    gateway.snapshot_ready.emit(snapshot)
    window._show_ai_setup()
    return window, vm, gateway, snapshot, settings, window._ai_setup_dialog


def select_two(dialog, voice="none", second="claude"):
    dialog._choices["codex"].setChecked(True)
    dialog._choices[second].setChecked(True)
    dialog._next.click()
    dialog._voice.setCurrentIndex(dialog._voice.findData(voice))
    dialog._next.click()


def readback(vm, gateway, snapshot, *, fail=False):
    command = gateway.commands[-1]
    assert command.name == "VALIDATE_CONFIG"
    candidate = copy.deepcopy(command.payload["config"])
    digest = command.payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert gateway.commands[-1].name == "SET_CONFIG"
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    assert gateway.commands[-1].name == "GET_CONFIG"
    gateway.command_completed.emit("GET_CONFIG", {"command": "GET_CONFIG", "result": {
        "generation": generation, "digest": digest, "config": candidate,
    }})
    return candidate


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_all_profiles_preserve_existing_and_official_controls(contract, platform):
    draft = LocalDraft.from_snapshot(_power_v2_snapshot(contract, read_only=False), contract)
    original = copy.deepcopy(draft.config)
    keys = [app.key for app in AI_APPLICATIONS]
    config, entries = prepare_ai_profiles(draft, keys, "codex", "none", {}, platform=platform)
    assert draft.config == original
    assert len(entries) == len(AI_APPLICATIONS)
    assert config["profiles"][:len(original["profiles"])] == original["profiles"]
    for p in config["profiles"][len(original["profiles"]):]:
        for m in p["mappings"]:
            if m["control_id"] in MATRIX12_AGENT_STATUS_KEYS:
                assert m["action"] == {"type": "none"}
    draft.config = config
    assert draft.validate(contract) == ()


def test_capacity_failure_is_atomic_and_voice_is_per_app(contract):
    draft = LocalDraft.from_snapshot(_power_v2_snapshot(contract, read_only=False), contract)
    assert "key_gesture" in draft.actions   # Power V2 firmware lists it
    original = copy.deepcopy(draft.config)
    draft.max_profiles = len(draft.profiles) + 1
    with pytest.raises(ValueError):
        prepare_ai_profiles(draft, ["codex", "claude"], "codex", "external", {}, platform="darwin")
    assert draft.config == original
    draft.max_profiles = 8
    config, entries = prepare_ai_profiles(draft, ["codex", "claude"], "codex", "external", {}, platform="darwin")
    draft.config = config
    assert draft.mapping(entries["codex"]["profile_id"], "key.8")["action"] == VOICE_ACTION
    assert draft.mapping(entries["claude"]["profile_id"], "key.8")["action"]["type"] == "key"
    updated, later = prepare_ai_profiles(draft, ["claude"], "claude", "none", entries, platform="darwin")
    assert len(updated["profiles"]) == len(config["profiles"])
    assert later["codex"]["voice"] == "external"
    assert updated["profiles"] == config["profiles"]


@pytest.mark.parametrize("second", ["claude", "deepseek-harness"])
def test_multi_choice_only_writes_once_and_waits_for_readback(session, tmp_path, monkeypatch, qtbot, second):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    original = copy.deepcopy(vm.draft.config)
    select_two(dialog, second=second)
    assert dialog.selected() == ["codex", second]
    assert vm.draft.config == original
    assert load_choices(settings) == ["codex", second]
    assert dialog._next.isEnabled()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 2
    assert not dialog._next.isEnabled()
    candidate = readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert vm.draft.config == candidate
    assert len([c for c in gateway.commands if c.name == "SET_CONFIG"]) == 1
    assert len(load_device_choices(settings, vm.draft.serial)) == 2
    dialog.accept()
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    dialog._practice.input.setPlainText("多选设置测试")
    qtbot.keyClick(dialog._practice.input, Qt.Key_Return)
    assert dialog._practice.completed
    dialog._next.click()
    assert dialog._pages.currentIndex() == 4
    assert not dialog._next.isEnabled()
    dialog._target_check.setChecked(True)
    settings.setValue(AI_SETUP_DEFERRED_KEY, True)
    dialog._next.click()
    assert settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    assert not settings.value(AI_SETUP_DEFERRED_KEY, False, type=bool)
    assert load_device_choices(settings, vm.draft.serial)["codex"]["tried"]


def test_close_and_reopen_keeps_choices_without_marking_complete(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog.reject()
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    window._show_ai_setup()
    assert window._ai_setup_dialog.selected() == ["codex", "claude"]
    assert not vm.draft.is_dirty


def test_draft_blocks_writes_but_runtime_mode_only_blocks_practice(session, tmp_path, monkeypatch, qtbot):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    vm.rename_profile(vm.draft.config["active_profile"], "我的现场")
    select_two(dialog)
    assert not dialog._next.isEnabled()
    assert vm.draft.profile(vm.draft.config["active_profile"])["name"] == "我的现场"
    vm.draft.discard()
    codex_snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "codex"})
    gateway.snapshot_ready.emit(codex_snapshot)
    assert dialog._next.isEnabled()
    assert "实体试用前" in dialog._connection.text()
    dialog._next.click()
    readback(vm, gateway, codex_snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    assert vm.model.snapshot.status['operating_mode'] == 'codex'
    assert not dialog._next.isEnabled()
    assert not dialog._practice.isEnabled() and not dialog._practice.completed
    gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': 'normal'})
    assert dialog._practice.isEnabled()
    dialog._practice.input.setPlainText('切换后练习')
    qtbot.keyClick(dialog._practice.input, Qt.Key_Return)
    assert dialog._next.isEnabled()


def test_refresh_preserves_input_and_other_device_cannot_complete(session, tmp_path, monkeypatch, qtbot):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._practice is not None)
    dialog._practice.input.setPlainText("保留我的文字")
    vm.changed.emit(vm.model)
    assert dialog._practice.input.toPlainText() == "保留我的文字"
    gateway.disconnected.emit("已断开")
    assert not dialog._next.isEnabled()
    assert dialog._practice.input.toPlainText() == "保留我的文字"
    other = replace(snapshot, identity={**snapshot.identity, "serial": "another-device"})
    gateway.snapshot_ready.emit(other)
    assert not dialog._next.isEnabled()
    assert dialog._practice.input.toPlainText() == "保留我的文字"


def test_completed_practice_ignores_unrelated_device_config_changes(
    session, tmp_path, monkeypatch, qtbot,
):
    _, vm, gateway, snapshot, _, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    dialog._practice.input.setPlainText("保留这次成功练习")
    qtbot.keyClick(dialog._practice.input, Qt.Key_Return)
    assert dialog._practice.completed and dialog._next.isEnabled()

    current = vm.model.snapshot
    changed = copy.deepcopy(current.config)
    changed["display"]["brightness"] = max(
        0, changed["display"]["brightness"] - 1,
    )
    active = next(profile for profile in changed["profiles"]
                  if profile["id"] == current.active_profile_id)
    send = next(mapping for mapping in active["mappings"]
                if mapping["control_id"] == "key.8")
    send["action"].setdefault("modifiers", [])
    generation = current.config_result["generation"] + 1
    gateway.snapshot_ready.emit(replace(
        current,
        hello_config={"generation": generation, "digest": "a" * 64},
        config_result={
            **current.config_result,
            "generation": generation,
            "digest": "a" * 64,
            "config": changed,
        },
    ))

    assert dialog._practice.completed
    assert dialog._next.isEnabled()
    assert not dialog._error.isVisible()


@pytest.mark.parametrize("changed_context", ["active_profile", "key_action"])
def test_required_practice_context_change_offers_an_enabled_recovery_action(
    session, tmp_path, monkeypatch, qtbot, changed_context,
):
    _, vm, gateway, snapshot, _, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    dialog._practice.input.setPlainText("这次练习已成功")
    qtbot.keyClick(dialog._practice.input, Qt.Key_Return)

    current = vm.model.snapshot
    changed = copy.deepcopy(current.config)
    if changed_context == "active_profile":
        changed["active_profile"] = next(
            profile["id"] for profile in changed["profiles"]
            if profile["id"] != current.active_profile_id
        )
    else:
        active = next(profile for profile in changed["profiles"]
                      if profile["id"] == current.active_profile_id)
        send = next(mapping for mapping in active["mappings"]
                    if mapping["control_id"] == "key.8")
        send["action"] = {"type": "key", "usage": 41}
    generation = current.config_result["generation"] + 1
    gateway.snapshot_ready.emit(replace(
        current,
        hello_config={"generation": generation, "digest": "b" * 64},
        config_result={
            **current.config_result,
            "generation": generation,
            "digest": "b" * 64,
            "config": changed,
        },
    ))

    assert dialog._next.isEnabled()
    assert dialog._next.text() == "重新确认设置"
    assert "这颗键" in dialog._error.text()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 2
    assert dialog._practice is None


def test_favorites_switch_uses_one_apply_and_keeps_unselected_profiles(session, tmp_path, monkeypatch, qtbot):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._applied)
    dialog.reject()
    entries = load_device_choices(settings, vm.draft.serial)
    old_profiles = copy.deepcopy(vm.draft.profiles)
    previous_writes = len([c for c in gateway.commands if c.name == "SET_CONFIG"])
    with monkeypatch.context() as scope:
        answer_warnings(scope, lambda *_: QMessageBox.Yes)
        window._activate_common_ai(entries["claude"]["profile_id"])
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    readback(vm, gateway, vm.model.snapshot)
    assert vm.model.snapshot.active_profile_id == entries["claude"]["profile_id"]
    assert vm.draft.profiles == old_profiles
    assert len([c for c in gateway.commands if c.name == "SET_CONFIG"]) == previous_writes + 1


def test_failed_write_can_retry_without_duplicate_profiles(session, tmp_path, monkeypatch, qtbot):
    from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    candidate = copy.deepcopy(vm.draft.config)
    gateway.command_failed.emit("VALIDATE_CONFIG", BootstrapError(
        BootstrapKind.READ_FAILED, "设备校验失败", "VALIDATION_FAILED", error_name="VALIDATION_FAILED"))
    assert vm.write_transaction.state is ConfigTransactionState.FAILED
    assert dialog._next.isEnabled()
    assert dialog._next.text() == "重新应用"
    dialog._next.click()
    assert vm.draft.config == candidate
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    assert len(vm.draft.profiles) == len(candidate["profiles"])


def test_unknown_waits_and_reconnect_readback_advances_without_rewrite(session, tmp_path, monkeypatch, qtbot):
    from controller_config.protocol.bootstrap import BootstrapKind
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    digest = gateway.commands[-1].payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    gateway.failure.emit(BootstrapKind.READ_FAILED, "连接中断", "short read")
    assert vm.write_transaction.state is ConfigTransactionState.UNKNOWN
    assert not dialog._next.isEnabled()
    generation = snapshot.config_result["generation"] + 1
    gateway.snapshot_ready.emit(replace(snapshot, status=_active_status(snapshot, digest, generation),
        config_result={"generation": generation, "digest": digest, "config": candidate}))
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    assert [c.name for c in gateway.commands].count("SET_CONFIG") == 1
    assert dialog._bind.isVisibleTo(dialog)
    assert not dialog._next.isEnabled()
    dialog._bind.click()
    guide = dialog._voice_dialog
    guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
    guide._toggle_supported.setChecked(True)
    guide._next.click()
    guide.practice.input.setPlainText("说话练习")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    guide.findChild(QCheckBox, "voicePhysicalConfirmed").setChecked(True)
    guide._next.click()
    assert dialog._next.isEnabled()


def test_preview_prepared_when_device_arrives_later(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    # Disconnection retains an existing draft. Also cover a genuine first connection.
    gateway.disconnected.emit("断开")
    vm._draft = None
    select_two(dialog)
    assert dialog._candidate is None
    assert not dialog._next.isEnabled()
    gateway.snapshot_ready.emit(snapshot)
    assert dialog._candidate is not None
    assert dialog._next.isEnabled()


def test_voice_preference_loads_on_late_connection_and_failed_change_is_not_saved(session, tmp_path, monkeypatch, qtbot):
    from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._applied)
    confirmed = vm.model.snapshot
    saved = load_device_choices(settings, vm.draft.serial)
    saved["codex"]["tried"] = True
    save_device_choices(settings, vm.draft.serial, saved)
    dialog.reject()
    gateway.disconnected.emit("断开")
    vm._draft = None
    window._show_ai_setup()
    guide = window._ai_setup_dialog
    assert guide._voice.currentData() == "none"
    gateway.snapshot_ready.emit(confirmed)
    assert guide._voice.currentData() == "external"
    guide._next.click()
    guide._voice.setCurrentIndex(guide._voice.findData("none"))
    guide._next.click()
    assert guide._entries["codex"]["tried"] is False
    guide._next.click()
    gateway.command_failed.emit("VALIDATE_CONFIG", BootstrapError(
        BootstrapKind.READ_FAILED, "设备校验失败", "VALIDATION_FAILED", error_name="VALIDATION_FAILED"))
    assert load_device_choices(settings, vm.draft.serial)["codex"]["voice"] == "external"
    guide._back.click()
    assert not vm.draft.is_dirty
    assert vm.draft.mapping(saved["codex"]["profile_id"], "key.8")["action"] == VOICE_ACTION


def test_hiding_guide_during_write_still_saves_confirmed_preferences(session, tmp_path, monkeypatch, qtbot):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    dialog.reject()
    assert window._ai_setup_dialog is dialog
    assert load_device_choices(settings, vm.draft.serial)["codex"].get("voice") is None
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    assert load_device_choices(settings, vm.draft.serial)["codex"]["voice"] == "external"
    window._show_ai_setup()
    assert window._ai_setup_dialog is dialog
    assert dialog._pages.currentIndex() == 3


def test_six_choices_fit_real_factory_configuration_without_overwrite(contract):
    import json
    from pathlib import Path
    snapshot = _power_v2_snapshot(contract, read_only=False)
    factory = json.loads((Path(__file__).resolve().parents[2] / 'firmware/components/config_store/default_config_matrix12_power_v2.json').read_text())
    # The factory Key 8 voice default is a key_gesture (firmware 2c764bc / 91c4ec0);
    # Power V2 firmware lists key_gesture and host_action (config_protocol.c).
    draft = LocalDraft.from_snapshot(replace(snapshot, config_result={**snapshot.config_result, 'config': factory}), contract)
    original = copy.deepcopy(draft.config)
    config, _ = prepare_ai_profiles(draft, [app.key for app in AI_APPLICATIONS], "codex", "none", {}, platform="darwin")
    assert draft.config == original
    assert config['profiles'][:2] == factory['profiles']
    draft.config = config
    assert draft.validate(contract) == ()


@pytest.mark.parametrize("previous", [["chatgpt"], ["codex"], ["codex", "chatgpt"]])
def test_combined_choice_keeps_old_selection_once(tmp_path, previous):
    settings = QSettings(str(tmp_path / "choices.ini"), QSettings.IniFormat)
    settings.setValue(AI_SELECTION_KEY, [*previous, "doubao"])
    assert load_choices(settings) == ["codex", "doubao"]
    assert [app.name for app in AI_APPLICATIONS].count("Codex / ChatGPT") == 1
    assert "chatgpt" not in [app.key for app in AI_APPLICATIONS]


def test_combined_choice_reuses_chatgpt_profile_without_deleting_it(contract, tmp_path):
    draft = LocalDraft.from_snapshot(_power_v2_snapshot(contract, read_only=False), contract)
    config, entries = prepare_ai_profiles(draft, ["codex"], "codex", "none", {}, platform="darwin")
    draft.config = config
    old = entries.pop("codex")
    entries["chatgpt"] = old
    settings = QSettings(str(tmp_path / "device.ini"), QSettings.IniFormat)
    save_device_choices(settings, draft.serial, entries)
    saved = load_device_choices(settings, draft.serial)
    updated, entries = prepare_ai_profiles(draft, ["codex"], "codex", "none", saved, platform="darwin")
    assert updated == config
    assert entries["codex"]["profile_id"] == old["profile_id"]
    assert entries["chatgpt"] == old


@pytest.mark.parametrize("installed", ["Codex", "ChatGPT"])
def test_combined_label_opens_real_app_name(monkeypatch, installed):
    from pathlib import Path
    monkeypatch.setattr("controller_config.ai_setup.sys.platform", "darwin")
    expected = Path("/Applications") / f"{installed}.app"
    monkeypatch.setattr(Path, "is_dir", lambda path: path == expected)
    assert installed_desktop_app("Codex / ChatGPT") == expected


def test_customized_send_is_not_silently_relabelled_or_overwritten(contract):
    draft = LocalDraft.from_snapshot(_power_v2_snapshot(contract, read_only=False), contract)
    config, entries = prepare_ai_profiles(draft, ['codex'], 'codex', 'none', {}, platform='darwin')
    draft.config = config
    draft.set_mapping(entries['codex']['profile_id'], 'key.8', '粘贴', {'type': 'key', 'usage': 25, 'modifiers': [227]})
    before = copy.deepcopy(draft.config)
    with pytest.raises(ValueError, match='自定义'):
        prepare_ai_profiles(draft, ['codex'], 'codex', 'none', entries, platform='darwin')
    assert draft.config == before


def test_favorites_menu_opens_from_home_button(session, tmp_path, monkeypatch, qtbot):
    from PySide6.QtWidgets import QPushButton
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._applied)
    dialog.reject()
    window.show()
    qtbot.wait(20)
    # The set's name chip opens 全部方案 (Mode页 定稿 v1, 10); 切换常用 AI now sits under
    # 新建一屏 › AI 模板.
    from PySide6.QtWidgets import QMenu
    from controller_config.views.mode_page import ModeStage
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    qtbot.mouseClick(stage.bar.profile, Qt.LeftButton)
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    assert stage.overview_shown()
    new = stage.overview.cards[-1]
    assert new.screen is None, 'no 新建一屏 card: 切换常用 AI cannot be reached'
    opened = []
    import controller_config.views.main_window as main_window_module

    class RecordingMenu(QMenu):   # PySide6 ignores QMenu.exec patched on the class itself
        def exec(self, *_args, **_kwargs):  # noqa: A003 - Qt's name
            opened.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", RecordingMenu)
    stage.overview.new_requested.emit(new)
    assert len(opened) == 1 and opened[0].objectName() == 'modeNewScreenMenu'
    templates = opened[0].findChild(QMenu, 'modeAiTemplateMenu')
    assert templates is not None
    names = [action.objectName() for sub in templates.findChildren(QMenu) for action in sub.actions()]
    assert 'commonAI_codex' in names and 'commonAI_claude' in names


def test_voice_software_choice_is_remembered_and_does_not_skip_practice(qtbot, tmp_path):
    from PySide6.QtWidgets import QComboBox, QLabel
    from controller_config.views.voice_guide import VoiceSetupDialog
    settings = QSettings(str(tmp_path/'voice-software.ini'), QSettings.IniFormat)
    guide = VoiceSetupDialog(settings=settings)
    qtbot.addWidget(guide)
    guide.show()
    provider = guide.findChild(QComboBox, 'voiceSoftware')
    assert provider.currentData() == ''
    provider.setCurrentIndex(provider.findData('typeless'))
    from controller_config.i18n import translate_ui_text
    assert guide._binding_hint.text() == translate_ui_text("Typeless 设置 → 快捷键 → 语音输入：查看正在使用的启停快捷键。")
    assert not guide._next.isEnabled()
    guide._toggle_supported.setChecked(True)
    guide._next.click()
    assert not guide._next.isEnabled()
    guide.reject()
    restored = VoiceSetupDialog(settings=settings)
    qtbot.addWidget(restored)
    assert restored.findChild(QComboBox, 'voiceSoftware').currentData() == 'typeless'


def test_voice_completion_waits_for_target_and_retry_requires_new_practice(session, tmp_path, monkeypatch, qtbot):
    from controller_config.voice_setup import voice_trial
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)

    def practise():
        dialog._bind.click()
        guide = dialog._voice_dialog
        guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
        guide._toggle_supported.setChecked(True)
        guide._next.click()
        guide.practice.input.setPlainText("设备练习")
        qtbot.keyClick(guide.practice.input, Qt.Key_Return)
        guide.findChild(QCheckBox, "voicePhysicalConfirmed").setChecked(True)
        guide._next.click()
        assert dialog._voice_dialog is None

    practise()
    assert dialog._next.isEnabled()
    current = vm.model.snapshot
    action = current.mappings["key.8"]["action"]
    assert voice_trial(settings, vm.draft.serial, current.active_profile_id, "key.8", action) is None
    dialog._bind.click()
    dialog._voice_dialog.reject()
    assert not dialog._next.isEnabled()
    practise()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 4 and not dialog._next.isEnabled()
    dialog._target_check.setChecked(True)
    dialog._next.click()
    record = voice_trial(settings, vm.draft.serial, current.active_profile_id, "key.8", action)
    assert record["target"] == "codex" and record["provider"] == "typeless"


def test_doubao_nested_flow_commits_only_from_outer_target_success(
    session, tmp_path, monkeypatch, qtbot,
):
    from controller_config.voice_setup import voice_provider_key, voice_trial

    _, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)

    dialog._bind.click()
    guide = dialog._voice_dialog
    guide.provider.setCurrentIndex(guide.provider.findData("doubao"))
    current = vm.model.snapshot
    guide._adapt_button.click()
    readback(vm, gateway, current)
    assert guide._pages.currentIndex() == 0 and guide._next.isEnabled()
    assert not settings.contains(voice_provider_key(vm.draft.serial, vm.model.snapshot.active_profile_id))
    guide._next.click()
    guide.practice.input.setPlainText("豆包嵌套练习")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    guide._next.click()
    assert dialog._voice_dialog is None and dialog._practice.completed
    assert not settings.contains(voice_provider_key(vm.draft.serial, vm.model.snapshot.active_profile_id))

    dialog._next.click()
    assert dialog._pages.currentIndex() == 4
    assert dialog._target_check.isHidden() and dialog._next.isEnabled()
    dialog.accept()
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    dialog._next.click()

    active = vm.model.snapshot
    provider_key = voice_provider_key(vm.draft.serial, active.active_profile_id)
    assert settings.value(provider_key) == "doubao"
    record = voice_trial(settings, vm.draft.serial, active.active_profile_id,
                         "key.8", active.mappings["key.8"]["action"])
    assert record and record["target"] == "codex"


@pytest.mark.parametrize(
    "reason,inner_page,expanded",
    [
        ("no_voice", 0, "use_mode"),
        ("no_text", 1, None),
        ("cant_stop", 0, "shortcut"),
        ("conflict", 0, None),
        ("send", 0, "send"),
    ],
)
def test_doubao_nested_failure_removes_success_and_resumes_matching_recovery(
    session, tmp_path, monkeypatch, qtbot, reason, inner_page, expanded,
):
    from controller_config.voice_setup import (
        confirmed_voice_choice, save_voice_trial, voice_provider_key, voice_trial,
    )

    _, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)

    dialog._bind.click()
    guide = dialog._voice_dialog
    guide.provider.setCurrentIndex(guide.provider.findData("doubao"))
    current = vm.model.snapshot
    guide._adapt_button.click()
    readback(vm, gateway, current)
    if reason == "no_voice":
        guide._other_use_toggle.click()
        guide._current_input_only.setChecked(True)
    guide._next.click()
    guide.practice.input.setPlainText("豆包恢复练习")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    guide._next.click()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 4

    active = vm.model.snapshot
    provider_key = voice_provider_key(vm.draft.serial, active.active_profile_id)
    settings.setValue(provider_key, "doubao")
    save_voice_trial(
        settings, vm.draft.serial, active.active_profile_id, "key.8",
        active.mappings["key.8"]["action"], "doubao", "codex",
    )
    dialog._entries["codex"]["tried"] = True
    save_device_choices(settings, vm.draft.serial, dialog._entries)
    other_trial = None
    if reason == "conflict":
        changed = copy.deepcopy(active.config)
        other_profile = next(
            profile for profile in changed["profiles"]
            if profile["id"] != active.active_profile_id
            and any(mapping["control_id"] == "key.8" for mapping in profile["mappings"])
        )
        other_mapping = next(
            mapping for mapping in other_profile["mappings"]
            if mapping["control_id"] == "key.8"
        )
        save_voice_trial(
            settings, vm.draft.serial, other_profile["id"], "key.8",
            other_mapping["action"], "doubao", "claude",
        )
        changed["active_profile"] = other_profile["id"]
        gateway.snapshot_ready.emit(replace(
            active,
            config_result={**active.config_result, "config": changed},
        ))
        other_trial = (other_profile["id"], other_mapping["action"])
    assert voice_trial(
        settings, vm.draft.serial, active.active_profile_id, "key.8",
        active.mappings["key.8"]["action"],
    )
    assert confirmed_voice_choice(
        settings, vm.draft.serial, active.active_profile_id, mode="normal",
    ) == {"action": active.mappings["key.8"]["action"], "provider": "doubao"}

    dialog._target_recovery_toggle.click()
    assert dialog._target_recovery_toggle.property("expanded") is True
    assert dialog._target_recovery_toggle.property("controls") == "aiTargetRecovery"
    dialog.findChild(QPushButton, "aiTargetRepair_" + reason).click()
    assert dialog._pages.currentIndex() == 3
    assert not load_device_choices(settings, vm.draft.serial)["codex"]["tried"]
    assert voice_trial(
        settings, vm.draft.serial, active.active_profile_id, "key.8",
        active.mappings["key.8"]["action"],
    ) is None
    if other_trial is not None:
        assert voice_trial(
            settings, vm.draft.serial, other_trial[0], "key.8", other_trial[1],
        )
        gateway.snapshot_ready.emit(active)
    assert dialog._practice.completed is (reason == "conflict")

    dialog._bind.click()
    retry = dialog._voice_dialog
    assert retry._pages.currentIndex() == inner_page
    assert retry.provider.currentData() == "doubao"
    if expanded == "use_mode":
        assert retry._other_use_toggle.isChecked()
        assert retry.doubao_use_mode == "current_input"
    elif expanded == "shortcut":
        assert retry._shortcut_toggle.isChecked()
    elif expanded == "send":
        assert retry._send_panel.isVisible()
        assert retry._shortcut_panel.isHidden()
    if reason == "no_text":
        retry.practice.input.setPlainText("恢复后的练习")
        qtbot.keyClick(retry.practice.input, Qt.Key_Return)
        assert retry.practice.completed and retry._next.isEnabled()
    retry.reject()


def test_doubao_nested_outer_cancel_keeps_provider_and_trial_uncommitted(
    session, tmp_path, monkeypatch, qtbot,
):
    from controller_config.voice_setup import voice_provider_key, voice_trial

    _, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    dialog._bind.click()
    guide = dialog._voice_dialog
    guide.provider.setCurrentIndex(guide.provider.findData("doubao"))
    current = vm.model.snapshot
    guide._adapt_button.click()
    readback(vm, gateway, current)
    guide._next.click()
    guide.practice.input.setPlainText("取消前练习")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    guide._next.click()
    active = vm.model.snapshot
    provider_key = voice_provider_key(vm.draft.serial, active.active_profile_id)
    assert not settings.contains(provider_key)
    dialog.reject()
    assert not settings.contains(provider_key)
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    assert voice_trial(
        settings, vm.draft.serial, active.active_profile_id, "key.8",
        active.mappings["key.8"]["action"],
    ) is None


def test_voice_binding_cannot_start_from_another_device_profile(session, tmp_path, monkeypatch, qtbot):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog, "external")
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    original = vm.model.snapshot
    changed = copy.deepcopy(original.config)
    changed['active_profile'] = next(p['id'] for p in changed['profiles'] if p['id'] != original.active_profile_id)
    gateway.snapshot_ready.emit(replace(original, config_result={**original.config_result, 'config': changed}))
    assert not dialog._bind.isEnabled()
    dialog._open_binding()
    assert dialog._voice_dialog is None
    gateway.snapshot_ready.emit(original)
    assert dialog._bind.isEnabled()
    dialog._bind.click()
    assert dialog._voice_dialog is not None
    assert dialog._voice_dialog.practice.input._usage == original.mappings['key.8']['action']['double_usage']
    dialog._voice_dialog.reject()
