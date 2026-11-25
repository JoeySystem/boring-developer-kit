"""The shared dialog shell (the designer B direction v0.4 final)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QWidget

from controller_config.style import tokens
from controller_config.views.ui import MEDIUM, SMALL, Dialog, RoundCloseButton, confirm
from controller_config.views.ui import dialog as shell


def test_dark_card_without_title_bar_and_one_title(qtbot):
    dialog = Dialog(None, "蓝牙名称", width=SMALL)
    dialog.add_text("设备在电脑蓝牙列表里显示的名字。")
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.width() == SMALL
    inside = dialog.grab().toImage().pixelColor(40, dialog.height() // 2)
    assert inside.name() == QColor(tokens.DIALOG_SURFACE).name()
    titles = [label for label in dialog.findChildren(QLabel) if label.text() == "蓝牙名称"]
    assert len(titles) == 1


def test_close_styles_follow_the_bottom_left_rule(qtbot):
    round_close = Dialog(None, "只读", close="round")
    later = Dialog(None, "设置常用 AI", close="later", later_text="稍后设置")
    none = Dialog(None, "确认", close="none")
    for dialog in (round_close, later, none):
        qtbot.addWidget(dialog)
    assert isinstance(round_close.close_button, RoundCloseButton)
    assert later.close_button.text() == "稍后设置"
    assert none.close_button is None
    # Read-only dialogs carry no "关闭" text button at the bottom.
    assert not [b for b in round_close.findChildren(QPushButton) if b.text() == "关闭"]


def test_height_follows_content_and_caps_at_560(qtbot):
    dialog = Dialog(None, "长内容", width=MEDIUM)
    tall = QWidget()
    tall.setMinimumHeight(2000)
    dialog.body_layout().addWidget(tall)
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.height() == shell.MAX_HEIGHT
    short = Dialog(None, "短内容", width=MEDIUM)
    short.add_text("一句话")
    qtbot.addWidget(short)
    short.show()
    assert short.height() < 250


def test_buttons_sit_right_primary_last(qtbot):
    dialog = Dialog(None, "改名")
    secondary = dialog.add_button("取消", "secondary", role="reject")
    primary = dialog.add_button("保存", "primary", role="accept")
    qtbot.addWidget(dialog)
    dialog.show()
    assert secondary.x() < primary.x()
    assert primary.geometry().right() > dialog.width() - shell.PADDING - 4
    assert primary.height() == 40


def test_danger_confirm_has_no_orange_and_enter_cancels(qtbot, monkeypatch):
    seen = {}

    def fake_exec(dialog):
        seen["kinds"] = {b.objectName(): b.property("boringButton") for b in dialog.findChildren(QPushButton)}
        seen["default"] = [b.objectName() for b in dialog.findChildren(QPushButton) if b.isDefault()]
        seen["close"] = dialog.close_button
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(Dialog, "exec", fake_exec)
    assert confirm(None, "恢复出厂设置", "会清除设备配置。", "继续", danger=True) is False
    assert seen["kinds"]["confirmCancel"] == "cream"
    assert seen["default"] == ["confirmCancel"]
    assert seen["kinds"]["confirmAction"] == "secondary"
    assert seen["close"] is None
    assert "primary" not in seen["kinds"].values()


def test_ordinary_confirm_returns_true_on_accept(qtbot, monkeypatch):
    monkeypatch.setattr(Dialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)
    assert confirm(None, "保存", "保存到设备？", "保存") is True


def test_full_height_step_dialog_keeps_one_height(qtbot):
    dialog = Dialog(None, "设置语音输入", step="设置语音输入 · 1 / 3", full_height=True)
    dialog.add_text("一句话")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.height() == shell.MAX_HEIGHT
    dialog.set_step("设置语音输入 · 2 / 3")
    assert any(label.text() == "设置语音输入 · 2 / 3" for label in dialog.findChildren(QLabel))


def test_embedded_dialog_draws_no_card_and_takes_no_padding(qtbot):
    host = Dialog(None, "设置语音键", full_height=True)
    step = Dialog(None, "确认现有快捷键")
    step.add_text("步骤内容")
    step.setParent(host)
    step.embed()
    host.body_layout().addWidget(step, 1)
    qtbot.addWidget(host)
    host.show()
    assert not step.isWindow() and step.isVisible()
    assert step.layout().contentsMargins().left() == 0
    assert step.width() == MEDIUM - 2 * shell.PADDING


def test_step_dialog_takes_the_tallest_step_and_never_jumps(qtbot):
    """B v0.4: 高取最高的那一步，换步不跳；短的那步不出滚动条。"""
    from PySide6.QtWidgets import QStackedWidget

    dialog = Dialog(None, "设置语音输入", step="设置语音输入 · 1 / 2", close="later")
    stack = QStackedWidget()
    short, tall = QWidget(), QWidget()
    short.setMinimumHeight(60)
    tall.setMinimumHeight(300)
    stack.addWidget(short)
    stack.addWidget(tall)
    dialog.body_layout().addWidget(stack)
    dialog.use_steps(stack)
    qtbot.addWidget(dialog)
    dialog.show()
    opened = dialog.height()
    assert dialog.height() < shell.MAX_HEIGHT
    assert dialog._shell_scroll.height() >= 300
    assert not dialog._shell_scroll.verticalScrollBar().maximum()
    stack.setCurrentIndex(1)
    qtbot.wait(50)
    assert dialog.height() == opened
    assert not dialog._shell_scroll.verticalScrollBar().maximum()


def test_dialogs_stop_at_560_and_ai_setup_keeps_640():
    """design decision 第 4 件: a dialog is smaller than the smallest window (960×640); 设置常用 AI
    keeps 640 for now because its step 3 needs 34 more than 560 (design decision)."""
    from controller_config.views.ai_setup import AISetupDialog
    assert shell.MAX_HEIGHT == 560 and shell.Dialog.max_height == 560
    assert AISetupDialog.max_height == shell.TALL_MAX_HEIGHT == 640
