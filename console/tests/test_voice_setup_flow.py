"""The guide must distinguish observed input, user-confirmed hardware, and app use."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel
import copy
import pytest

from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.i18n import translate_ui_text
from test_voice_input_ui import confirm_shortcut

PRESET = {"type": "key_gesture", "usage": 104, "double_usage": 40}


def guide(qtbot, **kwargs):
    widget = VoiceSetupDialog(action=PRESET, **kwargs)
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
    assert widget.findChild(QLabel, "voiceBindingHint").text() == translate_ui_text(
        "设备语音键在 Typeless 中会显示为 F13，不用在电脑键盘上寻找。"
        "设备保存完成后，打开 Typeless，在“语音输入”快捷键框中按一下设备语音键；"
        "显示 F13 即绑定成功。"
    )
    assert widget._shortcut_panel.isHidden()
    assert trial_hint.text() == original
    widget.provider.setCurrentIndex(widget.provider.findData("other"))
    assert widget.findChild(QLabel, "voiceBindingHint").text() == translate_ui_text(
        "下面会把语音软件的启停快捷键保存到设备。"
    )
    assert trial_hint.text() == original
    assert not widget._next.isEnabled()
    widget._next_step()
    assert widget._pages.currentIndex() == 0
    widget._toggle_supported.setChecked(True)
    assert widget._next.isEnabled()


@pytest.mark.parametrize("mode,expected", [
    ("normal", "普通模式语音键"),
    ("codex", "CODEX 模式语音键"),
])
def test_voice_setup_names_the_mode_being_changed(qtbot, mode, expected):
    widget = VoiceSetupDialog(action=PRESET, mode=mode)
    qtbot.addWidget(widget)
    assert expected in widget.findChild(QLabel, "voiceModeContext").text()
    assert "分别保存" in widget.findChild(QLabel, "voiceModeContext").text()


def test_standalone_finishes_after_practice_and_target_trial_is_optional(qtbot):
    widget = guide(qtbot, target_key="codex")
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    local_practice(qtbot, widget)
    assert widget._next.isEnabled()
    assert widget._target_trial.isVisible() and widget._target_trial.isEnabled()
    widget._next.click()
    assert accepted == [True]

    optional = guide(qtbot, target_key="codex")
    optional_accepted = []
    optional.accepted.connect(lambda: optional_accepted.append(True))
    local_practice(qtbot, optional)
    optional._target_trial.click()
    assert optional._pages.currentIndex() == 2 and optional.target_key == "codex"
    assert optional._step_number.text() == translate_ui_text("可选 ·")
    assert optional.findChild(QLabel, "voiceTargetOptionalNote").text() == translate_ui_text(
        "这一步是可选验证，不会修改设备设置。你可以在常用 AI 中再试一次，也可以直接完成。"
    )
    assert all(button.property("buttonRole") == "secondary"
               for button in optional.findChildren(type(optional._next))
               if button.objectName().startswith("voiceRepair_"))
    assert optional._next.isEnabled()
    optional._target.setCurrentIndex(optional._target.findData("claude"))
    assert optional._next.isEnabled()
    optional._next.click()
    assert optional_accepted == [True]


def test_nested_guide_finishes_local_practice_without_duplicate_target(qtbot):
    widget = guide(qtbot, include_target=False)
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    assert widget._pages.count() == 2
    local_practice(qtbot, widget)
    assert widget._next.isEnabled()
    widget._next.click()
    assert accepted == [True]


def test_retry_and_provider_change_clear_previous_result(qtbot):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    widget.practice.reset()
    assert not widget._next.isEnabled()
    widget.practice.input.setPlainText("再试一次")
    qtbot.keyClick(widget.practice.input, Qt.Key_Return)
    assert widget._next.isEnabled()
    widget._target_trial.click()
    widget.provider.setCurrentIndex(widget.provider.findData("other"))
    assert widget._pages.currentIndex() == 0
    assert not widget.practice.completed
    assert not widget._toggle_supported.isChecked()


def test_context_loss_blocks_completion_and_preserves_practice(qtbot):
    widget = guide(qtbot)
    accepted = []
    widget.accepted.connect(lambda: accepted.append(True))
    local_practice(qtbot, widget)
    widget._target_trial.click()
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
        "推荐使用右 Option。设备保存完成后，在千问“语音输入”中设为右 Option，并打开“短按也唤起语音输入”。")
    widget.provider.setCurrentIndex(widget.provider.findData("doubao"))
    assert widget._voice_app_path() is not None
    assert widget._voice_app_path().name == "DoubaoImeSettings.app"
    assert widget._open_voice_button.isHidden()  # Revealed only after device readback.
    widget.provider.setCurrentIndex(widget.provider.findData("typeless"))
    assert not widget._open_voice_button.isHidden()
    assert widget._voice_app_path().name == "Typeless.app"


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
    widget.demo.show()
    widget.resize(480, 620)
    qtbot.wait(30)
    assert widget.height() == 620
    assert widget._next.isVisible()
    assert widget._next.mapTo(widget, widget._next.rect().bottomRight()).y() < widget.height()
    assert widget._body.verticalScrollBar().maximum() > 0


def test_completed_practice_exposes_next_actions_in_fixed_footer(qtbot):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    assert widget._trial_hint.text() == translate_ui_text(
        "练习已完成。可以直接完成设置，或到常用 AI 中再试一次。"
    )
    assert widget._next.text() == translate_ui_text("完成设置")
    assert widget._next.isVisible() and widget._next.isEnabled()
    assert widget._target_trial.isVisible() and widget._target_trial.isEnabled()
    assert not widget._later.isVisible()
    for button in (widget._next, widget._target_trial):
        assert button.mapTo(widget, button.rect().bottomRight()).y() < widget.height()


@pytest.mark.parametrize("reason", ["no_voice", "conflict", "send"])
def test_repair_preserves_choice_and_shortcuts_but_requires_fresh_practice(qtbot, reason):
    widget = guide(qtbot)
    local_practice(qtbot, widget)
    original = copy.deepcopy(widget._requested_action)
    widget._repair(reason)
    assert widget._pages.currentIndex() == 0
    assert widget.provider.currentData() == "typeless"
    assert widget._requested_action == original
    assert not widget.practice.completed
    assert widget._toggle_supported.isChecked() is (reason == "send")
    assert widget._issue_notice.isVisible()
