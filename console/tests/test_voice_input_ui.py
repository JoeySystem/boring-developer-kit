from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

from controller_config.actions import action_definitions, describe_action
from controller_config.protocol.bootstrap import BootstrapKind
from controller_config.transactions import ConfigTransactionState
from controller_config.views.action_editor import ActionEditor
from controller_config.views.voice_guide import VoiceDemo, VoiceSetupDialog
from controller_config.views.voice_practice import VoicePractice
from test_session_recovery import session
from test_write_transaction import _ack, _active_status


PRESET = {"type": "key_gesture", "usage": 104, "double_usage": 40}


def confirm_shortcut(dialog, usage=None):
    """Act like a user recording an existing shortcut, then confirming it."""
    if dialog._shortcut_editors and (usage is not None or not dialog._custom_shortcut_chosen):
        editor = dialog._shortcut_editors[0][1]
        editor._shortcut_recorder.shortcut_recorded.emit({
            "type": "key", "usage": usage if usage is not None else dialog._requested_action["usage"],
        })
    dialog._toggle_supported.setChecked(True)


def test_voice_help_is_optional_and_preserves_shortcuts(qtbot, contract):
    editor = ActionEditor(
        action_definitions(contract, ("key", "none", "key_gesture")), PRESET,
        platform="macos", allow_voice_preset=True, show_manual_controls=False,
    )
    qtbot.addWidget(editor)
    editor.show()
    assert editor.findChild(QComboBox, "mappingPurpose").currentText() == "语音输入"
    assert all(
        "Typeless" not in label.text() and "Codex" not in label.text()
        for label in editor.findChildren(QLabel)
    )
    assert not editor.findChild(QWidget, "voiceSetupInstructions").isVisible()
    assert not editor.findChild(VoiceDemo).isVisible()
    assert editor.findChild(QWidget, "voiceShortcutDetails") is None
    assert all("F13" not in label.text() for label in editor.findChildren(QLabel) if label.isVisible())
    button = editor.findChild(QPushButton, "voiceSetupToggle")
    button.click()
    assert editor.action() == PRESET
    assert "统一配置" in editor.findChild(QWidget, "voiceSetupInstructions").findChild(QLabel).text()
    button.click()
    assert editor.action() == PRESET
    assert describe_action(PRESET, platform="macos") == "语音启停 · 双击发送"
    assert describe_action({"type": "key", "usage": 104}, platform="macos") == "F13"
    assert "F13" in describe_action({**PRESET, "double_usage": 4}, platform="macos")


def test_voice_preset_preserves_normal_shortcut_and_routes_setup_to_guide(qtbot, contract):
    original = {"type": "key", "usage": 25, "modifiers": [227]}
    editor = ActionEditor(
        action_definitions(contract, ("key", "none", "key_gesture")), original,
        platform="macos", allow_voice_preset=True, show_manual_controls=False,
    )
    qtbot.addWidget(editor)
    editor.show()
    purpose = editor.findChild(QComboBox, "mappingPurpose")
    purpose.setCurrentIndex(1)
    assert editor.action() == PRESET
    assert editor.findChild(QWidget, "voiceInputSetup").isVisible()
    assert not editor._shortcut_recorder.isVisible()
    assert editor.findChild(QWidget, "voiceShortcutDetails") is None
    requested = []
    editor.voice_setup_requested.connect(lambda: requested.append(True))
    editor.findChild(QPushButton, "voiceConnectApp").click()
    assert requested == [True]
    purpose.setCurrentIndex(0)
    assert editor.action() == original
    assert editor._shortcut_recorder.isVisible()


def test_unsupported_firmware_cannot_select_voice_preset(qtbot, contract):
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")), {"type": "key", "usage": 44},
        allow_voice_preset=True, show_manual_controls=False,
    )
    qtbot.addWidget(editor)
    purpose = editor.findChild(QComboBox, "mappingPurpose")
    assert not purpose.model().item(1).isEnabled()
    assert editor.findChild(QLabel, "voiceFirmwareNotice") is not None
    assert editor.action() == {"type": "key", "usage": 44}


def _enable_voice(session, monkeypatch):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr("controller_config.views.main_window.sys.platform", "darwin")
    snapshot = replace(
        snapshot,
        capabilities={**snapshot.capabilities, "actions": [*snapshot.capabilities["actions"], "key_gesture"]},
        status={**snapshot.status, "operating_mode": "normal"},
    )
    gateway.snapshot_ready.emit(snapshot)
    window._select_physical_control("key.8")
    return window, vm, gateway, snapshot


@pytest.mark.parametrize("name,display", [("Typeless 语音", "语音输入"), ("我的听写", "我的听写")])
def test_legacy_voice_name_is_display_only(session, monkeypatch, name, display):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    config = copy.deepcopy(snapshot.config)
    profile = next(p for p in config["profiles"] if p["id"] == config["active_profile"])
    profile["mappings"].append({"control_id": "key.8", "short_name": name, "action": PRESET})
    window._close_control_editor()
    gateway.snapshot_ready.emit(replace(snapshot, config_result={
        **snapshot.config_result, "config": config,
        "generation": snapshot.config_result["generation"] + 1,
    }))
    window._select_control("key.8")
    card = window._current_mapping_card()
    assert card.findChild(QLineEdit, "mappingShortNameEditor").text() == display
    assert not card.findChild(QPushButton, "applyMappingToDevice").isEnabled()
    assert not window._mapping_editor_has_uncommitted_changes()
    assert window._save_mapping_editor_draft()
    assert vm.draft.config == config
    assert not vm.draft.is_dirty
    assert not any(c.name == "SET_CONFIG" for c in gateway.commands)


@pytest.mark.parametrize("recover_unknown", [False, True])
@pytest.mark.parametrize("send_modifiers", [[], [227]])
def test_voice_selection_one_apply_runs_existing_write_readback(session, qtbot, monkeypatch, tmp_path, recover_unknown, send_modifiers):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    window._onboarding_settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    original = copy.deepcopy(vm.draft.config)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    expected = {**PRESET, "modifiers": []}
    if send_modifiers:
        expected = {**PRESET, "modifiers": [], "double_modifiers": send_modifiers}
    assert vm.draft.config == original  # Selecting a purpose does not write or save.
    assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "语音输入"
    window.findChild(QPushButton, "voiceConnectApp").click()
    guide = window._voice_setup_dialog
    guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
    if send_modifiers:
        guide._shortcut_editors[1][1]._shortcut_recorder.shortcut_recorded.emit(
            {"type": "key", "usage": 40, "modifiers": send_modifiers})
    confirm_shortcut(guide)
    assert guide._context_ready, (guide.action, snapshot.mappings.get("key.8"), vm.model.state)
    assert guide._adapt_button.isEnabled(), (guide._device_available(), guide._adaptation_needed())
    guide._adapt_button.click()
    assert gateway.commands[-1].name == "VALIDATE_CONFIG", guide._adapt_error.text()
    assert guide._adapting
    assert not guide._next.isEnabled()
    candidate = copy.deepcopy(gateway.commands[-1].payload["config"])
    digest = gateway.commands[-1].payload["digest"]
    gateway.command_completed.emit("VALIDATE_CONFIG", _ack("VALIDATE_CONFIG"))
    assert gateway.commands[-1].name == "SET_CONFIG"
    gateway.command_completed.emit("SET_CONFIG", _ack("SET_CONFIG"))
    generation = snapshot.config_result["generation"] + 1
    gateway.status_updated.emit(_active_status(snapshot, digest, generation))
    assert gateway.commands[-1].name == "GET_CONFIG"
    assert not guide._next.isEnabled()
    if recover_unknown:
        gateway.failure.emit(BootstrapKind.READ_FAILED, "设备连接已中断", "short read")
        assert vm.write_transaction.state is ConfigTransactionState.UNKNOWN
        assert guide._adapting
        assert not guide._next.isEnabled()
        gateway.snapshot_ready.emit(replace(
            snapshot, status=_active_status(snapshot, digest, generation),
            config_result={"generation": generation, "digest": digest, "config": candidate},
        ))
    else:
        gateway.command_completed.emit("GET_CONFIG", _ack("GET_CONFIG", {"generation": generation, "digest": digest, "config": candidate}))
    assert vm.write_transaction.state is ConfigTransactionState.ACTIVE
    assert not vm.draft.is_dirty
    assert vm.model.snapshot.mappings["key.8"]["action"] == expected
    actual = window.findChild(QLabel, "actualActionValue")
    assert actual.text() == describe_action(expected, platform="macos")
    assert actual.isHidden() == (not send_modifiers)
    assert window.findChild(QPushButton, "applyMappingToDevice").text() == "设备配置已应用"
    for profile in original["profiles"]:
        if profile["id"] != original["active_profile"]:
            assert profile in candidate["profiles"]
    assert [c.name for c in gateway.commands].count("SET_CONFIG") == 1
    qtbot.waitUntil(lambda: window._voice_setup_dialog is not None)
    guide = window._voice_setup_dialog
    assert guide._pages.currentIndex() == 1
    assert not guide.findChild(QPushButton, "voiceSetupNext").isEnabled()
    guide.practice.input.setPlainText("这是一条练习消息")
    qtbot.keyClick(guide.practice.input, Qt.Key_Return, Qt.ControlModifier if send_modifiers else Qt.NoModifier)
    assert guide.practice.completed
    assert guide._next.isEnabled()
    guide.findChild(QPushButton, "voiceSetupNext").click()
    assert window._voice_setup_dialog is None
    from controller_config.voice_setup import voice_trial
    assert voice_trial(window._onboarding_settings, vm.draft.serial, vm.model.snapshot.active_profile_id, "key.8", expected)
    assert window.findChild(QPushButton, "voiceConnectApp").text() == "更换语音输入软件"
    assert window.findChild(QLabel, "voiceSetupStatus").text() == "设置已完成 · 当前输入软件：Typeless"
    assert [c.name for c in gateway.commands].count("SET_CONFIG") == 1


def test_unapplied_voice_selection_survives_refresh_and_reconnect(session, monkeypatch):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    window.findChild(QLineEdit, "mappingShortNameEditor").setText("我的语音")
    gateway.status_updated.emit({**snapshot.status, "battery": {"percent": 75}})
    gateway.disconnected.emit("test disconnect")
    gateway.snapshot_ready.emit(snapshot)
    assert window.findChild(ActionEditor).action() == PRESET
    assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "我的语音"
    assert not any(c.name == "SET_CONFIG" for c in gateway.commands)


def test_voice_edit_survives_firmware_downgrade_without_allowing_write(session, monkeypatch):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    gateway.disconnected.emit("firmware changed")
    old = replace(snapshot, capabilities={**snapshot.capabilities, "actions": [a for a in snapshot.capabilities["actions"] if a != "key_gesture"]})
    gateway.snapshot_ready.emit(old)
    editor = window.findChild(ActionEditor)
    assert editor.action() == PRESET
    assert not window.findChild(QPushButton, "applyMappingToDevice").isEnabled()
    assert window.findChild(QLabel, "voiceFirmwareNotice") is not None
    editor._shortcut_was_recorded({"type": "key", "usage": 44})
    assert editor.action() == {"type": "key", "usage": 44}


@pytest.mark.parametrize("control", ["key.1", "key.3", "encoder.press", "joystick.up"])
def test_voice_preset_excludes_official_and_system_controls(session, monkeypatch, control):
    window, *_ = _enable_voice(session, monkeypatch)
    window._select_control(control)
    assert window.findChild(QComboBox, "mappingPurpose") is None


@pytest.mark.parametrize("mode", ["claude_code"])
def test_voice_edit_remains_normal_only_in_official_modes(session, monkeypatch, mode):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    gateway.status_updated.emit({**snapshot.status, "operating_mode": mode})
    window._select_physical_control("key.8")
    notice = window.findChild(QLabel, "mappingExecutionNotice")
    assert notice is not None and "普通模式" in notice.text()


@pytest.mark.parametrize("language", ["zh_CN", "en_US"])
def test_voice_editor_layout_keeps_apply_visible(session, qtbot, monkeypatch, tmp_path, language):
    window, *_ = _enable_voice(session, monkeypatch)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    window._language_manager.set_language(language)
    window.show()
    for width, height in ((1100, 700), (1600, 900)):
        window.resize(width, height)
        qtbot.wait(70)
        body = window.findChild(QScrollArea, "mappingEditorBody")
        assert body.widget().width() <= body.viewport().width()
        entry = window.findChild(QPushButton, "voiceConnectApp")
        assert entry.isVisible() and entry.isEnabled()
        path = tmp_path / f"voice-input-{width}.png"
        window.grab().save(str(path))
        print(f"UI preview: {path}")
    window._language_manager.set_language("zh_CN")


def test_voice_demo_only_animates_the_selected_model_key(session, qtbot, monkeypatch):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    pressed = []
    monkeypatch.setattr(window, "_preview_voice_key", pressed.append)
    demo = window.findChild(VoiceDemo)
    commands = list(gateway.commands)
    for index in range(len(demo._frames)):
        demo._index = index
        demo._show_frame()
    assert pressed == ["key.8"] * 4  # start, stop, two send presses
    assert gateway.commands == commands
    demo.show()
    demo._timer.start(1000)
    demo.hide()
    assert not demo._timer.isActive()


def test_failed_voice_write_does_not_open_setup(session, monkeypatch):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    window.findChild(QComboBox, "mappingPurpose").setCurrentIndex(1)
    window.findChild(QPushButton, "applyMappingToDevice").click()
    assert window._voice_setup_pending is not None
    vm._write_transaction = replace(vm.write_transaction, state=ConfigTransactionState.FAILED)
    vm.changed.emit(vm.model)
    assert window._voice_setup_pending is None
    assert window._voice_setup_dialog is None


def test_voice_demo_respects_reduced_motion(qtbot, monkeypatch):
    monkeypatch.setattr("controller_config.views.voice_guide.system_reduces_motion", lambda: True)
    demo = VoiceDemo()
    qtbot.addWidget(demo)
    demo.show()
    assert not demo._timer.isActive()
    demo.findChild(QPushButton, "voiceDemoReplay").click()
    assert demo._index == 1 and not demo._timer.isActive()


def test_practice_requires_real_text_then_send_and_can_retry(qtbot):
    guide = VoiceSetupDialog(action=PRESET)
    qtbot.addWidget(guide)
    guide.show()
    guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
    confirm_shortcut(guide)
    guide._next.click()
    practice = guide.practice
    assert not guide.demo.isVisible()
    repair_panel = guide.findChild(QWidget, "voiceRepairPanel_practice")
    repair_toggle = guide.findChild(QPushButton, "voiceRepairToggle_practice")
    assert repair_toggle.isVisible() and not repair_panel.isVisible()
    repair_toggle.click()
    assert repair_panel.isVisible()
    repair_toggle.click()
    qtbot.keyClick(practice.input, Qt.Key_Return)
    guide.accept()
    assert guide.isVisible() and not practice.completed
    assert not guide._next.isEnabled()
    practice.input.setPlainText("  ")
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert not practice.completed
    practice.input.setPlainText("<b>实际文字</b>\n第二行")
    assert practice._send_hint.isVisible()
    assert practice._received.text() == "✓" and practice._sent.text() == "2"
    assert not guide._next.isEnabled()
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert practice.completed and guide._next.isEnabled()
    assert practice._hint.text() == "练习完成，内容未发送到 AI。"
    assert practice._message.toPlainText() == "<b>实际文字</b>\n第二行"
    assert practice._sent.text() == "✓" and not practice.input.isVisible()
    practice._retry.click()
    assert not practice.completed and not guide._next.isEnabled()
    assert practice.input.toPlainText() == "" and practice.input.isVisible()


@pytest.mark.parametrize("usage,modifiers,key,qt_modifiers", [
    (40, [227], Qt.Key_Return, Qt.ControlModifier),
    (40, [225], Qt.Key_Return, Qt.ShiftModifier),
    (104, [], Qt.Key_F13, Qt.NoModifier),
])
def test_practice_honors_configured_send(qtbot, usage, modifiers, key, qt_modifiers):
    practice = VoicePractice({**PRESET, "double_usage": usage, "double_modifiers": modifiers})
    practice.input._platform = "macos"
    qtbot.addWidget(practice)
    practice.show()
    practice.input.setPlainText("Send this")
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert not practice.completed
    qtbot.keyClick(practice.input, key, qt_modifiers)
    assert practice.completed and practice._message.toPlainText() == "Send this"


def test_practice_does_not_send_ime_preedit_or_key_repeat(qtbot):
    practice = VoicePractice(PRESET)
    qtbot.addWidget(practice)
    practice.show()
    practice.input.setPlainText("已有文字")
    QApplication.sendEvent(practice.input, QInputMethodEvent("拼音", []))
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert not practice.completed
    commit = QInputMethodEvent()
    commit.setCommitString("输入")
    QApplication.sendEvent(practice.input, commit)
    QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "", True))
    assert not practice.completed
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert practice.completed


def test_practice_matches_a_modifier_only_send_on_release(qtbot):
    practice = VoicePractice({**PRESET, "double_usage": 231})
    qtbot.addWidget(practice)
    practice.input._platform = "macos"
    practice.show()
    practice.input.setPlainText("Right Command")
    QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyPress, Qt.Key_Control, Qt.ControlModifier, 0, 0x36, 0, ""))
    assert not practice.completed
    QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyRelease, Qt.Key_Control, Qt.NoModifier, 0, 0x36, 0, ""))
    assert practice.completed


@pytest.mark.parametrize("shift_first", [False, True])
@pytest.mark.parametrize("other_key", [False, True])
def test_modifier_send_does_not_depend_on_release_order(qtbot, shift_first, other_key):
    practice = VoicePractice({**PRESET, "double_usage": 231, "double_modifiers": [225]})
    qtbot.addWidget(practice)
    practice.input._platform = "macos"
    practice.show()
    practice.input.setPlainText("Modifier chord")
    command = (Qt.Key_Control, 0x36, Qt.ControlModifier)
    shift = (Qt.Key_Shift, 0x38, Qt.ShiftModifier)
    for key, native, _ in (command, shift):
        QApplication.sendEvent(practice.input, QKeyEvent(
            QEvent.KeyPress, key, Qt.ControlModifier | Qt.ShiftModifier, 0, native, 0, ""))
    if other_key:
        QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.ControlModifier | Qt.ShiftModifier))
    first, last = (shift, command) if shift_first else (command, shift)
    QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyRelease, first[0], last[2], 0, first[1], 0, ""))
    assert not practice.completed
    QApplication.sendEvent(practice.input, QKeyEvent(QEvent.KeyRelease, last[0], Qt.NoModifier, 0, last[1], 0, ""))
    assert practice.completed is (not other_key)


def test_practice_text_survives_device_refresh_and_does_not_write(session, monkeypatch):
    window, vm, gateway, snapshot = _enable_voice(session, monkeypatch)
    guide = VoiceSetupDialog(window, action=PRESET)
    guide.show()
    guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
    confirm_shortcut(guide)
    guide._next.click()
    guide.practice.input.setPlainText("还没发送的练习")
    commands = list(gateway.commands)
    gateway.status_updated.emit({**snapshot.status, "battery": {"percent": 70}})
    assert guide.practice.input.toPlainText() == "还没发送的练习"
    assert gateway.commands == commands
    guide.reject()


def test_practice_reduced_motion_keeps_feedback_immediate(qtbot, monkeypatch):
    monkeypatch.setattr("controller_config.views.voice_practice.system_reduces_motion", lambda: True)
    practice = VoicePractice(PRESET)
    qtbot.addWidget(practice)
    practice.show()
    practice.input.setPlainText("Test")
    assert practice._animation is None and practice._send_hint.isVisible()
    qtbot.keyClick(practice.input, Qt.Key_Return)
    assert practice.completed and practice._message.isVisible()
    assert practice._animation is None


def test_practice_language_and_long_message_keep_user_text(session, qtbot):
    window, *_ = session
    guide = VoiceSetupDialog(window, action=PRESET)
    guide.show()
    guide.provider.setCurrentIndex(guide.provider.findData("typeless"))
    confirm_shortcut(guide)
    guide._next.click()
    text = "首页图片\n" * 30
    guide.practice.input.setPlainText(text)
    window._language_manager.set_language("en_US")
    assert guide.practice.input.placeholderText() == "Your words will appear here"
    assert guide.practice.input.toPlainText() == text
    qtbot.keyClick(guide.practice.input, Qt.Key_Return)
    assert guide.practice._message.toPlainText() == text.strip()
    assert guide.practice._message.height() <= 160
    window._language_manager.set_language("zh_CN")
    assert guide.practice._message.toPlainText() == text.strip()
    guide.reject()
