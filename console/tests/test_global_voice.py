"""Power V2 voice selection, explicit save, and device readback."""

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QSettings

from controller_config import voice_presets
from controller_config.ai_setup import VOICE_ACTION
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.transactions import ConfigTransactionState
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.action_editor import ActionEditor
from controller_config.views.global_voice import GlobalVoiceDialog, legacy_voice_choices
from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.voice_setup import voice_mapping, voice_provider_key
from test_write_transaction import FakeWriteGateway, _ack, _active_status
from test_session_recovery import session
from test_primary_setup_flow import ready
from PySide6.QtWidgets import QPushButton


def _ready_vm(contract):
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot = replace(snapshot, capabilities={
        **snapshot.capabilities,
        "features": {**snapshot.capabilities["features"], "global_voice": True},
    })
    gateway.snapshot_ready.emit(snapshot)
    return vm, gateway, snapshot


def test_legacy_modes_are_shown_without_choosing_one(qtbot, contract):
    vm, _, snapshot = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm)
    qtbot.addWidget(dialog)
    assert dialog.legacy.currentIndex() == 0
    assert dialog.candidate() is None
    assert not dialog.save.isEnabled()
    existing = {"profiles": [
        {"id": 0, "name": "工作", "mappings": [
            {"control_id": "key.8", "action": {"type": "key", "usage": 104}}],
         "codex_voice": {"type": "key", "usage": 105}},
        {"id": 1, "name": "写作", "mappings": [
            {"control_id": "key.8", "action": {"type": "key_gesture", "usage": 106,
                                                 "double_usage": 40}}]},
    ]}
    choices = legacy_voice_choices(existing)
    assert len(choices) == 4
    assert any("Normal" in label for label, _ in choices)
    assert any("Codex" in label for label, _ in choices)
    assert len({str(action) for _, action in choices}) == 4
    dialog.legacy.setCurrentIndex(1)
    assert dialog.candidate() is None
    assert "不能直接用作" in dialog.status.text()
    dialog.provider.setCurrentIndex(dialog.provider.findData("other"))
    dialog.recorder.shortcut_recorded.emit({"type": "key", "usage": 104})
    assert dialog.candidate() is not None
    # Without other pending edits, an explicit "apply" is the confirmation.
    assert dialog.save.isEnabled()
    dialog.close()
    vm.shutdown()


def test_new_shortcut_defaults_to_separate_send_and_is_shared_after_readback(qtbot, contract):
    vm, gateway, snapshot = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm)
    qtbot.addWidget(dialog)
    dialog.provider.setCurrentIndex(dialog.provider.findData("typeless"))
    dialog.advanced.setChecked(True)
    assert dialog.quick_summary.isHidden()
    dialog.trigger.setCurrentIndex(0)
    dialog.recorder.shortcut_recorded.emit({"type": "key", "usage": 104})
    assert dialog.candidate() == {"type": "key", "usage": 104}
    dialog.save.click()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    assert candidate["voice_action"] == dialog.candidate()
    assert vm.model.snapshot.config.get("voice_action") is None
    digest = gateway.commands[-1].payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert vm.write_transaction.state is ConfigTransactionState.WRITING
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", {
        "config": candidate, "generation": generation, "digest": digest,
    }))
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert "已写入并读回" in dialog.status.text()
    assert "未验证" in dialog.status.text()
    assert "请让语音软件的启停快捷键与设备一致" in dialog.setup_hint.text()
    actual = vm.model.snapshot
    for mode in ("normal", "codex"):
        assert voice_mapping(actual, "key.8", mode)["action"] == candidate["voice_action"]
    vm.rename_profile(actual.active_profile_id, "待修改名称")
    assert not dialog.save.isEnabled()  # Other edits need explicit inclusion.
    dialog.confirmed.setChecked(True)
    dialog.save.click()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG"
    assert "正在写入" in dialog.status.text()
    assert "已写入并读回" not in dialog.status.text()
    from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
    gateway.command_failed.emit("VALIDATE_CONFIG", BootstrapError(
        BootstrapKind.READ_FAILED, "测试中止", "VALIDATION_FAILED", error_name="VALIDATION_FAILED"))
    dialog.close()
    vm.shutdown()


def test_double_send_requires_explicit_selection(qtbot, contract):
    vm, _, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm)
    qtbot.addWidget(dialog)
    dialog.provider.setCurrentIndex(dialog.provider.findData("other"))
    dialog.recorder.shortcut_recorded.emit({"type": "key", "usage": 104})
    assert dialog.candidate()["type"] == "key"
    dialog.trigger.setCurrentIndex(1)
    assert dialog.candidate() == {"type": "key_gesture", "usage": 104,
                                  "modifiers": [], "double_usage": 40}
    dialog.close()
    vm.shutdown()


def test_power_v2_model_key_opens_shared_editor_in_both_modes(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    config = copy.deepcopy(snapshot.config)
    config["profiles"][0]["mappings"].append({
        "control_id": "key.8", "short_name": "语音输入", "action": copy.deepcopy(VOICE_ACTION),
    })
    config["profiles"][0]["codex_voice"] = copy.deepcopy(VOICE_ACTION)
    capable = replace(snapshot, capabilities={
        **snapshot.capabilities,
        "features": {**snapshot.capabilities["features"], "global_voice": True},
    }, config_result={**snapshot.config_result, "config": config})
    gateway.snapshot_ready.emit(capable)
    for mode in ("normal", "codex"):
        window._select_configuration_mode(mode)
        window._select_physical_control("key.8")
        card = window._current_mapping_card()
        assert card.findChild(ActionEditor) is None
        # B-02: without a device-wide action, the Profile's own voice key is editable.
        scoped = card.findChild(QPushButton, "configureProfileVoice")
        assert scoped is not None
        scoped.click()
        assert isinstance(window._voice_setup_dialog, VoiceSetupDialog)
        window._voice_setup_dialog.close()
        button = card.findChild(QPushButton, "configureGlobalVoice")
        assert button is not None
        button.click()
        assert isinstance(window._global_voice_dialog, GlobalVoiceDialog)
        window._global_voice_dialog.close()
    assert not any(command.name in {"VALIDATE_CONFIG", "SET_CONFIG"}
                   for command in gateway.commands)


# --- B-01 ～ B-06 (ported from the uncommitted 0.2.5 work, made platform-aware) ---


def _complete_write(vm, gateway, snapshot):
    candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    digest = gateway.commands[-1].payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", {
        "config": candidate, "generation": generation, "digest": digest,
    }))
    return candidate


@pytest.mark.parametrize("platform,expected", [
    ("macos", ["", "wechat", "typeless", "doubao", "qianwen", "other"]),
    ("windows", ["", "typeless", "doubao", "other"]),
])
def test_b01_software_list_follows_platform(qtbot, contract, platform, expected):
    vm, _, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform=platform)
    qtbot.addWidget(dialog)
    assert [dialog.provider.itemData(i) for i in range(dialog.provider.count())] == expected
    assert dialog.recorder is not None and dialog.platform == platform
    dialog.close()
    vm.shutdown()


def test_b01_windows_doubao_preset_is_right_alt_space(qtbot, contract):
    vm, gateway, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform="windows")
    qtbot.addWidget(dialog)
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    action = dialog.candidate()
    assert action == {"type": "key_gesture", "usage": 44, "modifiers": [230], "double_usage": 40}
    assert 226 not in action["modifiers"]  # never the left Alt
    assert "右 Alt" in dialog.quick_summary.text()
    assert "Mac" not in dialog.quick_summary.text()
    assert not gateway.commands
    dialog.close()
    vm.shutdown()


def test_b01_macos_doubao_preset_and_wechat_f13(qtbot, contract):
    vm, gateway, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform="macos")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.provider.setCurrentIndex(dialog.provider.findData("wechat"))
    assert dialog.candidate() == VOICE_ACTION
    assert not dialog.advanced_panel.isVisible() and not dialog.setup_hint.isVisible()
    assert dialog.save.isEnabled() and not gateway.commands
    dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
    assert dialog.candidate() == {"type": "key_gesture", "usage": 228, "double_usage": 40}
    assert "右 Control" in dialog.quick_summary.text()
    dialog.close()
    vm.shutdown()


@pytest.mark.parametrize("platform,provider,shortcut", [
    ("macos", "wechat", "F13"),
    ("macos", "doubao", "右 Control"),
    ("windows", "doubao", "右 Alt+空格"),
])
def test_b01_setup_guide_appears_only_after_device_readback(qtbot, contract, platform, provider, shortcut):
    vm, gateway, snapshot = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform=platform, settings_lookup=lambda _bundle: None)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
    dialog.save.click()
    assert "正在写入" in dialog.status.text()
    assert not dialog.setup_hint.isVisible()
    _complete_write(vm, gateway, snapshot)
    assert "已写入并读回" in dialog.status.text()
    assert dialog.setup_hint.isVisible()
    assert shortcut in dialog.setup_hint.text()
    assert dialog.practice.isVisible() and not dialog.practice.completed
    dialog.close()
    vm.shutdown()


def test_b01_doubao_reopens_with_saved_right_command_without_replacing_it(qtbot, contract, tmp_path):
    vm, gateway, snapshot = _ready_vm(contract)
    action = {"type": "key_gesture", "usage": 231, "double_usage": 40}
    config = {**snapshot.config, "voice_action": action}
    gateway.snapshot_ready.emit(replace(snapshot, config_result={**snapshot.config_result, "config": config}))
    settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    settings.setValue(voice_provider_key(snapshot.identity["serial"], mode="global"), "doubao")
    dialog = GlobalVoiceDialog(vm, settings=settings, platform="macos", settings_lookup=lambda _bundle: None)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.candidate() == action
    assert "已读回" in dialog.status.text()
    assert "右 Command" in dialog.setup_hint.text()
    assert not gateway.commands
    dialog.close()
    vm.shutdown()


def test_b01_voice_modules_have_no_hard_coded_platform() -> None:
    from pathlib import Path
    import controller_config.views.global_voice as gv
    import controller_config.views.voice_guide as vg
    for module in (gv, vg):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert '"darwin"' not in text and '"macos"' not in text, module.__name__


@pytest.mark.parametrize("value,expected,offers_profile", [
    ({"type": "key_gesture", "usage": 104, "double_usage": 40}, "Normal／Codex 共用", False),
    (None, "当前 Profile", True),
    ("broken", "无法识别", False),
])
def test_b02_voice_card_text_for_dict_none_and_invalid(session, monkeypatch, tmp_path, value, expected, offers_profile):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    config = copy.deepcopy(snapshot.config)
    config["profiles"][0]["mappings"].append({
        "control_id": "key.8", "short_name": "语音输入", "action": copy.deepcopy(VOICE_ACTION),
    })
    draft = copy.deepcopy(vm.draft)
    draft_config = copy.deepcopy(config)
    if value is None:
        draft_config.pop("voice_action", None)
    else:
        draft_config["voice_action"] = value
    fake_draft = type("Draft", (), {"config": draft_config})()
    current = replace(snapshot, config_result={**snapshot.config_result, "config": config})
    window._configuration_mode = "normal"
    text, offer = window._global_voice_card_text(fake_draft, current)
    assert expected in text
    assert offer is offers_profile


@pytest.mark.parametrize("trigger,shown,hidden", [
    ("double", "双击语音键发送", "11 号键发送"),
    ("direct", "11 号键发送", "双击语音键发送"),
])
def test_b03_only_the_active_send_method_is_described(qtbot, contract, trigger, shown, hidden):
    vm, _, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform="macos")
    qtbot.addWidget(dialog)
    dialog.provider.setCurrentIndex(dialog.provider.findData("other"))
    dialog.trigger.setCurrentIndex(dialog.trigger.findData(trigger))
    text = dialog.send_method.text()
    assert shown in text and hidden not in text
    if trigger == "double":
        assert "延迟" in text
    else:
        assert "原映射不再触发" in text
    dialog.close()
    vm.shutdown()


def test_b05_all_entries_share_one_software_list(qtbot, contract):
    vm, _, _ = _ready_vm(contract)
    for platform in ("macos", "windows"):
        dialog = GlobalVoiceDialog(vm, platform=platform)
        guide = VoiceSetupDialog(platform=platform, include_target=False)
        qtbot.addWidget(dialog)
        qtbot.addWidget(guide)
        global_items = [dialog.provider.itemData(i) for i in range(1, dialog.provider.count())]
        guide_items = [guide.provider.itemData(i) for i in range(1, guide.provider.count())]
        assert global_items == guide_items == [key for key, _ in voice_presets.software_choices(platform)]
        dialog.close()
        guide.close()
    vm.shutdown()


def test_b06_dialog_size_does_not_jump_after_apply(qtbot, contract):
    vm, gateway, snapshot = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform="macos", settings_lookup=lambda _bundle: None)
    qtbot.addWidget(dialog)
    dialog.show()
    before = dialog.size()
    dialog.provider.setCurrentIndex(dialog.provider.findData("wechat"))
    dialog.save.click()
    _complete_write(vm, gateway, snapshot)
    assert dialog.setup_hint.isVisible()
    assert dialog.size() == before
    dialog.close()
    vm.shutdown()


def test_b06_settings_button_follows_bundle_lookup(qtbot, contract, tmp_path):
    app = tmp_path / "DoubaoIme.app" / "Contents" / "Applications" / "DoubaoImeSettings.app"
    app.mkdir(parents=True)
    for found, visible in ((tmp_path / "DoubaoIme.app", True), (None, False)):
        vm, gateway, snapshot = _ready_vm(contract)
        dialog = GlobalVoiceDialog(vm, platform="macos", settings_lookup=lambda _bundle, f=found: f)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.provider.setCurrentIndex(dialog.provider.findData("doubao"))
        dialog.save.click()
        _complete_write(vm, gateway, snapshot)
        assert dialog.open_settings.isVisible() is visible
        if visible:
            assert dialog.settings_app() == app
        else:
            assert "输入法菜单" in dialog.setup_hint.text()
        dialog.close()
        vm.shutdown()
