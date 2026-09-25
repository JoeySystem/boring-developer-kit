from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from controller_config.ai_setup import (
    AI_APPLICATIONS, AI_SELECTION_KEY, AI_SETUP_COMPLETED_KEY, VOICE_ACTION,
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


def ready(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr("controller_config.views.ai_setup.sys.platform", "darwin")
    settings = QSettings(str(tmp_path / "ai.ini"), QSettings.IniFormat)
    window._onboarding_settings = settings
    snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "normal"},
                       capabilities={**snapshot.capabilities, "actions": [*snapshot.capabilities["actions"], "key_gesture"]})
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
    draft.actions = (*draft.actions, "key_gesture")
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
    dialog._next.click()
    assert settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    assert load_device_choices(settings, vm.draft.serial)["codex"]["tried"]


def test_setup_explains_profile_switch_and_can_restore_previous_profile(
    session, tmp_path, monkeypatch, qtbot
):
    window, vm, gateway, snapshot, settings, dialog = ready(
        session, tmp_path, monkeypatch
    )
    previous_id = snapshot.active_profile_id
    previous_name = snapshot.profile_name
    select_two(dialog)
    assert previous_name in dialog._changes.text()
    assert "保留" in dialog._changes.text()
    dialog._next.click()
    readback(vm, gateway, snapshot)
    qtbot.waitUntil(lambda: dialog._pages.currentIndex() == 3)
    dialog._practice.input.setPlainText("配置方案恢复测试")
    qtbot.keyClick(dialog._practice.input, Qt.Key_Return)
    dialog._next.click()
    dialog._target_check.setChecked(True)
    assert dialog._restore.isVisibleTo(dialog)
    assert previous_name in dialog._restore.text()
    profiles_after_setup = copy.deepcopy(vm.draft.profiles)

    dialog._restore.click()

    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    readback(vm, gateway, vm.model.snapshot)
    assert vm.model.snapshot.active_profile_id == previous_id
    assert vm.draft.profiles == profiles_after_setup
    assert settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)


def test_close_and_reopen_keeps_choices_without_marking_complete(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    select_two(dialog)
    dialog.reject()
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    window._show_ai_setup()
    assert window._ai_setup_dialog.selected() == ["codex", "claude"]
    assert not vm.draft.is_dirty


def test_draft_and_mode_block_writes_not_selection(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, settings, dialog = ready(session, tmp_path, monkeypatch)
    vm.rename_profile(vm.draft.config["active_profile"], "我的现场")
    select_two(dialog)
    assert not dialog._next.isEnabled()
    assert vm.draft.profile(vm.draft.config["active_profile"])["name"] == "我的现场"
    vm.draft.discard()
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, "operating_mode": "codex"}))
    assert not dialog._next.isEnabled()
    assert "NORMAL" in dialog._connection.text()
    assert not any(c.name == "SET_CONFIG" for c in gateway.commands)


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
    from test_voice_input_ui import confirm_shortcut
    confirm_shortcut(guide)
    guide._next.click()
    guide.practice.input.setPlainText("说话练习")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    assert guide._next.isEnabled()
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
    window._onboarding_prompt_scheduled = True
    window.show()
    qtbot.wait(20)
    selector = window.findChild(QPushButton, 'profilePill')
    assert selector.menu() is not None
    names = [a.objectName() for a in selector.menu().actions()]
    assert 'commonAI_codex' in names and 'commonAI_claude' in names
    # Use a timed close because native QPushButton menus run a nested event loop.
    from PySide6.QtCore import QTimer
    seen = []
    def inspect():
        seen.append(selector.menu().isVisible())
        selector.menu().close()
    QTimer.singleShot(50, inspect)
    qtbot.mouseClick(selector, Qt.LeftButton)
    qtbot.waitUntil(lambda: bool(seen))
    assert seen == [True]


def test_unfinished_voice_software_choice_is_not_saved_as_confirmed(qtbot, tmp_path):
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
    assert guide._binding_hint.text() == translate_ui_text(
        "设备语音键在 Typeless 中会显示为 F13，不用在电脑键盘上寻找。"
        "设备保存完成后，打开 Typeless，在“语音输入”快捷键框中按一下设备语音键；"
        "显示 F13 即绑定成功。"
    )
    assert not guide._next.isEnabled()
    guide._toggle_supported.setChecked(True)
    guide._next.click()
    assert not guide._next.isEnabled()
    guide.hide()
    restored = VoiceSetupDialog(settings=settings)
    qtbot.addWidget(restored)
    assert restored.findChild(QComboBox, 'voiceSoftware').currentData() == ''


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
        from test_voice_input_ui import confirm_shortcut
        confirm_shortcut(guide)
        guide._next.click()
        guide.practice.input.setPlainText("设备练习")
        qtbot.keyClick(guide.practice.input, Qt.Key_Return)
        assert guide._next.isEnabled()
        guide._next.click()
        assert dialog._voice_dialog is None
        assert settings.value("ui/voice_input_software") == "typeless"

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
