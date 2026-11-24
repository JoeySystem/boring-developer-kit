"""The guide must distinguish observed input, user-confirmed hardware, and app use."""
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QDialog, QLabel
import copy
import pytest

from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.i18n import translate_ui_text
from controller_config.voice_setup import _gesture
from test_voice_input_ui import confirm_shortcut

PRESET = {"type": "key_gesture", "usage": 104, "double_usage": 40}


def guide(qtbot, **kwargs):
    action = kwargs.pop("action", PRESET)
    widget = VoiceSetupDialog(action=action, **kwargs)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def local_practice(qtbot, widget):
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    confirm_shortcut(widget)
    widget._next.click()
    assert widget._pages.currentIndex() == 1
    widget.practice.input.setPlainText("练习文字")
    qtbot.keyClick(widget.practice.input, Qt.Key_Return)
    assert widget.practice.completed


def test_provider_hint_stays_on_binding_page(qtbot):
    widget = guide(qtbot)
    trial_hint = widget.findChild(QLabel, "voicePracticeHint")
    original = trial_hint.text()
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    confirm_shortcut(widget)
    assert widget.findChild(QLabel, "voiceBindingHint").text() == translate_ui_text("Typeless 设置 → 快捷键 → 语音输入：查看正在使用的启停快捷键。")
    assert trial_hint.text() == original
    widget.provider.setCurrentIndex(widget.provider.findData("other"))
    assert widget.findChild(QLabel, "voiceBindingHint").text() == translate_ui_text("打开语音输入软件，确认这里的快捷键与它的启停快捷键一致。")
    assert trial_hint.text() == original
    assert not widget._next.isEnabled()
    widget._next_step()
    assert widget._pages.currentIndex() == 0
    widget._toggle_supported.setChecked(True)
    assert widget._next.isEnabled()


def test_standalone_requires_local_physical_and_target_confirmation(qtbot):
    widget = guide(qtbot, target_key="codex")
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    local_practice(qtbot, widget)
    # Typed text and a computer-keyboard Return are real input, but do not prove
    # the microphone or physical device worked.
    assert not widget._next.isEnabled()
    widget.accept()
    assert not accepted
    widget._physical_confirmed.setChecked(True)
    assert widget._next.isEnabled()
    widget._next.click()
    assert widget._pages.currentIndex() == 2 and widget.target_key == "codex"
    widget.accept()
    assert not accepted and not widget._next.isEnabled()
    widget._target_confirmed.setChecked(True)
    assert widget._next.isEnabled()
    widget._target.setCurrentIndex(widget._target.findData("claude"))
    assert not widget._target_confirmed.isChecked() and not widget._next.isEnabled()
    widget._target_confirmed.setChecked(True)
    widget.accept()
    assert accepted == [True]


def test_nested_guide_finishes_local_practice_without_duplicate_target(qtbot):
    widget = guide(qtbot, include_target=False)
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    assert widget._pages.count() == 2
    local_practice(qtbot, widget)
    widget._physical_confirmed.setChecked(True)
    widget._next.click()
    assert accepted == [True]


def test_retry_and_provider_change_clear_previous_result(qtbot):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    widget._physical_confirmed.setChecked(True)
    widget.practice.reset()
    assert not widget._physical_confirmed.isChecked() and not widget._next.isEnabled()
    widget.practice.input.setPlainText("再试一次")
    qtbot.keyClick(widget.practice.input, Qt.Key_Return)
    widget._physical_confirmed.setChecked(True)
    widget._next.click()
    widget._target_confirmed.setChecked(True)
    widget.provider.setCurrentIndex(widget.provider.findData("other"))
    assert widget._pages.currentIndex() == 0
    assert not widget.practice.completed
    assert not widget._physical_confirmed.isChecked()
    assert not widget._target_confirmed.isChecked()
    assert not widget._toggle_supported.isChecked()


def test_context_loss_blocks_completion_and_preserves_practice(qtbot):
    widget = guide(qtbot)
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    local_practice(qtbot, widget)
    widget._physical_confirmed.setChecked(True)
    widget._next.click()
    widget._target_confirmed.setChecked(True)
    widget.set_context_ready(False)
    assert not widget._next.isEnabled()
    assert widget.practice.completed and widget._context_notice.isVisible()
    assert widget.practice._message.toPlainText() == "练习文字"
    widget.accept()
    assert not accepted
    widget.set_context_ready(True)
    assert widget._next.isEnabled()
    assert widget._pages.currentIndex() == 2


def test_opening_guide_does_not_launch_external_software(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr("controller_config.views.voice_guide.QDesktopServices.openUrl", lambda url: calls.append(url.toString()) or True)
    monkeypatch.setattr("controller_config.views.voice_guide.installed_desktop_app", lambda name: None)
    widget = guide(qtbot, target_key="claude")
    assert calls == []
    widget._open_target()
    assert calls == ["https://claude.ai"]


def test_qianwen_uses_menu_instructions_not_background_service(qtbot, monkeypatch):
    monkeypatch.setattr("controller_config.views.voice_guide.sys.platform", "darwin")
    monkeypatch.setattr("controller_config.views.voice_guide.Path.exists", lambda path: True)
    widget = guide(qtbot)
    widget.provider.setCurrentIndex(widget.provider.findData("qianwen"))
    assert widget._voice_app_path() is None
    assert widget._open_voice_button.isHidden()
    assert widget._binding_hint.text() == translate_ui_text(
        "从 Mac 顶部的输入法菜单打开千问设置 → 语音输入。查看现有快捷键，并打开“短按也唤起语音输入”。")
    widget.provider.setCurrentIndex(widget.provider.findData("doubao"))
    assert not widget._open_voice_button.isHidden()
    assert widget._voice_app_path().name == "DoubaoImeSettings.app"


def test_reopen_same_shortcut_without_empty_modifier_fields_needs_no_write(qtbot):
    # Device readback retains modifiers=[], while ActionEditor omits it.
    widget = VoiceSetupDialog(
        action={"type": "key_gesture", "usage": 230, "modifiers": [], "double_usage": 40},
        candidate_action={"type": "key_gesture", "usage": 230, "double_usage": 40},
    )
    qtbot.addWidget(widget)
    widget.show()
    widget.provider.setCurrentIndex(widget.provider.findData("qianwen"))
    widget._toggle_supported.setChecked(True)
    assert not widget._adaptation_needed()
    assert widget._adapt_button.isHidden()
    assert widget._next.isEnabled()


def test_context_loss_preserves_unsent_input(qtbot):
    widget = guide(qtbot)
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    confirm_shortcut(widget)
    widget._next.click()
    widget.practice.input.setPlainText("还没说完的内容")
    widget.set_context_ready(False)
    assert widget.practice.input.toPlainText() == "还没说完的内容"
    widget.set_context_ready(True)
    assert widget.practice.input.toPlainText() == "还没说完的内容"
    assert widget._pages.currentIndex() == 1


def test_expanded_demo_keeps_main_action_visible(qtbot):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    widget._demo_toggle.setChecked(True)          # the demo takes the practice box's place (10)
    widget.resize(480, 620)
    qtbot.wait(30)
    # Mode页 两个弹窗 v0 (08): all three steps are 585 high; what opens scrolls inside.
    assert widget.height() == widget.FIXED_HEIGHT == 585
    assert widget.demo.isVisible() and not widget.practice.isVisible()
    assert widget._next.isVisible()
    assert widget._next.mapTo(widget, widget._next.rect().bottomRight()).y() < widget.height()


@pytest.mark.parametrize("reason", ["no_voice", "conflict", "send"])
def test_repair_preserves_choice_and_shortcuts_but_requires_fresh_practice(qtbot, reason):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    widget._physical_confirmed.setChecked(True)
    original = copy.deepcopy(widget._requested_action)
    widget._repair(reason)
    assert widget._pages.currentIndex() == 0
    assert widget.provider.currentData() == "typeless"
    assert widget._requested_action == original
    assert not widget.practice.completed
    assert not widget._physical_confirmed.isChecked()
    assert not widget._target_confirmed.isChecked()
    assert widget._toggle_supported.isChecked() is (reason == "send")
    assert widget._issue_notice.isVisible()


def test_macos_doubao_uses_three_result_confirmations_without_checkbox_friction(
    qtbot, monkeypatch, tmp_path,
):
    monkeypatch.setattr("controller_config.views.voice_guide.sys.platform", "darwin")
    settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    action = {**PRESET, "usage": 228}
    widget = guide(qtbot, action=action, settings=settings, target_key="codex")
    widget.provider.setCurrentIndex(widget.provider.findData("doubao"))
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))

    assert widget._next.text() == translate_ui_text("我已完成，开始按键练习")
    assert widget._shortcut_panel.isHidden()
    assert widget._toggle_supported.isHidden()
    assert all(options.isHidden() for _, options, _ in widget._repair_groups)
    widget.accept()
    assert not accepted and widget._pages.currentIndex() == 0

    widget._next.click()
    assert widget._pages.currentIndex() == 1
    widget.practice.input.setPlainText("豆包练习")
    qtbot.keyClick(widget.practice.input, Qt.Key_Return)
    assert widget.practice.completed and widget._physical_confirmed.isHidden()
    assert widget._next.text() == translate_ui_text("我已用 BORING 键完成练习")
    widget._next.click()
    assert widget._pages.currentIndex() == 2 and widget._target_confirmed.isHidden()
    widget.accept()
    assert not accepted
    widget._next.click()
    assert accepted == [True]


@pytest.mark.parametrize("reason", ["send", "cant_stop"])
def test_doubao_recovery_exposes_only_the_relevant_shortcut_editor(
    qtbot, monkeypatch, reason,
):
    monkeypatch.setattr("controller_config.views.voice_guide.sys.platform", "darwin")
    widget = guide(qtbot, action={**PRESET, "usage": 228})
    widget.provider.setCurrentIndex(widget.provider.findData("doubao"))
    widget._repair(reason)
    if reason == "send":
        assert widget._send_panel.isVisible()
        assert widget._shortcut_panel.isHidden()
        assert widget._shortcut_toggle.isHidden()
        assert widget._custom_toggle.isHidden()
    else:
        assert widget._shortcut_panel.isVisible()
        assert widget._send_panel.isHidden()


def test_macos_doubao_cancel_does_not_commit_provider_and_history_is_not_cleared(
    qtbot, monkeypatch, tmp_path,
):
    monkeypatch.setattr("controller_config.views.voice_guide.sys.platform", "darwin")
    settings = QSettings(str(tmp_path / "voice.ini"), QSettings.IniFormat)
    settings.setValue("ui/voice_input_software", "typeless")
    action = {**PRESET, "usage": 228}
    widget = guide(qtbot, action=action, settings=settings)
    widget.provider.setCurrentIndex(widget.provider.findData("doubao"))
    widget.reject()
    assert settings.value("ui/voice_input_software") == "typeless"

    settings.setValue("ui/voice_input_software", "doubao")
    trial = {"action": _gesture(action), "provider": "doubao", "target": "codex"}
    reopened = guide(qtbot, action=action, settings=settings, trial_record=trial)
    assert reopened._history_panel.isVisible()
    reopened._reverify_button.click()
    assert reopened._pages.currentIndex() == 1

    pending = {**action, "usage": 227, "modifiers": [226]}
    with_pending_edit = guide(
        qtbot, action=action, candidate_action=pending,
        settings=settings, trial_record=trial,
    )
    assert with_pending_edit._adaptation_needed()
    with_pending_edit._reverify_button.click()
    assert with_pending_edit._pages.currentIndex() == 1
    assert not with_pending_edit._adaptation_needed()
    assert with_pending_edit._requested_action == action
    with_pending_edit.practice.input.setPlainText("重新验证")
    qtbot.keyClick(with_pending_edit.practice.input, Qt.Key_Return)
    assert with_pending_edit._next.isEnabled()

    legacy_action = PRESET  # F13 was valid in an older confirmed Doubao trial.
    legacy_trial = {"action": _gesture(legacy_action), "provider": "doubao", "target": "codex"}
    legacy = guide(
        qtbot, action=legacy_action, settings=settings, trial_record=legacy_trial,
    )
    assert legacy._history_panel.isVisible()
    legacy._reverify_button.click()
    assert legacy._pages.currentIndex() == 1
    assert not legacy._adaptation_needed()
    legacy.practice.input.setPlainText("旧版记录重新验证")
    qtbot.keyClick(legacy.practice.input, Qt.Key_Return)
    assert legacy._next.isEnabled()

    legacy_settings = guide(
        qtbot, action=legacy_action, settings=settings, trial_record=legacy_trial,
    )
    legacy_settings._change_settings_button.click()
    assert legacy_settings._pages.currentIndex() == 0
    assert legacy_settings._next.isEnabled()
    assert legacy_settings._adapt_button.isHidden()
    assert legacy_settings._compatibility_notice.isHidden()


# -- Mode页 两个弹窗 v0：演示、帮助、状态并进三步 -------------------------------------------

def test_status_line_says_verified_or_changed_since(qtbot):
    from PySide6.QtWidgets import QFrame
    trial = {"provider": "typeless", "target": "claude", "action": dict(PRESET)}
    widget = guide(qtbot, trial_record=trial)
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    row = widget.findChild(QFrame, "voiceStatusRow")
    assert row.isVisible() and "Claude" in widget._status_title.text()
    widget._requested_action = {**PRESET, "usage": 105}       # changed since it was checked
    widget._refresh()
    assert widget._status_title.text() == translate_ui_text("设置改过了，要重新试用一次")
    plain = guide(qtbot)
    plain.provider.setCurrentIndex(plain.provider.findData("typeless"))
    assert not plain.findChild(QFrame, "voiceStatusRow").isVisible()   # no status: no line


def test_help_opens_in_place_with_the_two_links(qtbot):
    from PySide6.QtWidgets import QFrame, QPushButton
    opened = []
    widget = guide(qtbot, open_device_voice=lambda: opened.append(True))
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    box = widget.findChild(QFrame, "voiceHelpBox")
    assert not box.isVisible()
    widget._help_button.setChecked(True)
    assert box.isVisible()
    widget.findChild(QPushButton, "voiceHelpDeviceVoice").click()
    assert opened == [True]
    assert guide(qtbot).findChild(QPushButton, "voiceHelpDeviceVoice").isHidden()   # no device voice key


def test_the_tick_names_the_software(qtbot):
    widget = guide(qtbot)
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    assert widget._toggle_supported.text() == translate_ui_text("{software} 里的快捷键我已经设好了").format(
        software="Typeless")


def test_demo_takes_the_practice_box_place_and_goes_back(qtbot):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    assert widget.findChild(QLabel, "voicePracticeHint").text() == translate_ui_text(
        "按设备上的 {number} 号键试一下").format(number=8)
    widget._demo_toggle.setChecked(True)
    assert widget.demo.isVisible() and not widget.practice.isVisible()
    widget.demo.back_requested.emit()
    assert widget.practice.isVisible() and not widget.demo.isVisible()
    assert widget.height() == 585


def test_demo_runs_six_point_four_seconds_in_seven_steps(qtbot, monkeypatch):
    from controller_config.views.voice_guide import VoiceDemo
    monkeypatch.setattr("controller_config.views.voice_guide.system_reduces_motion", lambda: False)
    demo = VoiceDemo()
    qtbot.addWidget(demo)
    assert len(demo._frames) == 7 and demo.LENGTH == 6.4
    pressed = []
    demo.key_pressed.connect(lambda: pressed.append(demo.time()))
    demo._t = -0.001
    for step in range(1, 641):
        demo._set_time(step / 100)
    assert [round(t, 2) for t in pressed] == [0.2, 2.2, 4.4, 4.64]
    assert demo.stage_at(6.3) == 6 and demo.sent_at(5.0) and not demo.sent_at(4.5)
