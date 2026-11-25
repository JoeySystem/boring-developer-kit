"""Shared components in views/ui/: size, state, keyboard and screen-reader name."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from controller_config.style import tokens
from controller_config.style.sheets import application_stylesheet
from controller_config.views.ui import (
    Button, Card, CardHeader, Dialog, Disclosure, EmptyState, SegmentedControl, SettingRow, Tag,
)
from controller_config.views.ui.dot_slider import DotMatrixSlider


_HOSTS: list[QWidget] = []  # keep hosts alive until qtbot closes them


def _host(qtbot, *widgets: QWidget, width: int = 480) -> QWidget:
    """A styled window holding the widgets, the way the main window carries the sheet."""
    host = QWidget()
    _HOSTS.append(host)
    host.setStyleSheet(application_stylesheet())
    box = QVBoxLayout(host)
    for widget in widgets:
        box.addWidget(widget)
    box.addStretch(1)
    host.resize(width, 400)
    qtbot.addWidget(host)
    host.show()
    host.activateWindow()
    qtbot.waitActive(host)
    return host


def _name(widget: QWidget) -> str:
    return QAccessible.queryAccessibleInterface(widget).text(QAccessible.Text.Name)


def _checked(widget: QWidget) -> bool:
    return QAccessible.queryAccessibleInterface(widget).state().checked


# -- Button -------------------------------------------------------------------

def test_button_sizes_follow_the_dialog_final(qtbot):
    large = Button("保存到设备", "primary")
    small = Button("改名", "primary", size="small")
    outline = Button("返回")
    short = Button("好")
    _host(qtbot, large, small, outline, short)
    assert large.height() == 40
    assert small.height() == 34
    # The 1 px outline is drawn outside the 40 box (same in the dialogs today).
    assert outline.height() == 40 + 2
    assert short.sizeHint().width() >= 88  # large buttons are at least 88 wide
    assert small.sizeHint().width() < 88  # small ones follow the text
    assert large.property("boringButton") == "primary"
    assert small.property("boringButtonSize") == "small"


def test_button_rejects_unknown_kind_and_size():
    with pytest.raises(ValueError):
        Button("删除", "danger")
    with pytest.raises(ValueError):
        Button("删除", size="medium")


def test_button_keyboard_and_screen_reader(qtbot):
    button = Button("开始校准", object_name="startCalibration")
    _host(qtbot, button)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    button.setFocus()
    qtbot.keyClick(button, Qt.Key.Key_Space)
    assert clicks == [True]
    assert button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert _name(button) == "开始校准"
    assert not button.autoDefault()


def test_dialog_buttons_are_the_shared_button(qtbot):
    dialog = Dialog(None, "蓝牙名称", close="later")
    accept = dialog.add_button("保存", "primary", role="accept")
    qtbot.addWidget(dialog)
    assert isinstance(accept, Button)
    assert isinstance(dialog.close_button, Button)


# -- SegmentedControl -----------------------------------------------------------

def test_segmented_values_and_signal(qtbot):
    control = SegmentedControl([(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")], 90)
    _host(qtbot, control)
    seen = []
    control.valueChanged.connect(seen.append)
    assert control.value() == 90
    control.setValue(270)
    assert control.value() == 270
    assert seen == [270]
    control.buttons()[0].click()
    assert control.value() == 0
    assert seen == [270, 0]
    with pytest.raises(ValueError):
        control.setValue(45)


def test_segmented_unknown_start_value_picks_the_first():
    control = SegmentedControl([("zh", "中文"), ("en", "English")], "fr")
    assert control.value() == "zh"


def test_segmented_size_matches_screen_rotation(qtbot):
    control = SegmentedControl([(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")])
    _host(qtbot, control)
    buttons = control.buttons()
    assert all(button.height() == 30 for button in buttons)
    assert control.height() == 30 + 2 * 4  # 4 px inset, outline included
    widths = {button.width() for button in buttons}
    assert max(widths) - min(widths) <= 1  # equal shares of the track
    assert buttons[0].geometry().topLeft().toTuple() == (4, 4)


def test_segmented_keyboard_and_screen_reader(qtbot):
    control = SegmentedControl([("normal", "普通"), ("codex", "Codex")])
    _host(qtbot, control)
    first, second = control.buttons()
    first.setFocus()
    qtbot.waitUntil(first.hasFocus)
    qtbot.keyClick(first, Qt.Key.Key_Right)
    assert control.value() == "codex"
    assert second.hasFocus()
    assert _name(second) == "Codex"
    assert _checked(second) and not _checked(first)


# -- Disclosure -----------------------------------------------------------------

def test_disclosure_shows_and_hides_its_content(qtbot):
    details = QLabel("设备在电脑蓝牙列表里显示的名字。")
    more = Disclosure("了解更多", details)
    host = _host(qtbot, more, details)
    assert not details.isVisible()
    assert more.height() == 40  # words only, no outline
    assert more.width() == more.sizeHint().width() and more.x() == host.layout().contentsMargins().left()  # hugs its words, at the left
    assert more.property("boringButton") == "text"
    more.setFocus()
    qtbot.keyClick(more, Qt.Key.Key_Space)
    assert details.isVisible()
    assert _checked(more)
    qtbot.keyClick(more, Qt.Key.Key_Space)
    assert not details.isVisible()
    assert _name(more) == "了解更多"


def test_disclosure_can_start_open(qtbot):
    details = QLabel("已展开")
    more = Disclosure("了解更多", details, expanded=True)
    _host(qtbot, more, details)
    assert more.isChecked() and details.isVisible()


# -- Tag ------------------------------------------------------------------------

def test_tag_is_a_small_capsule_sized_to_its_text(qtbot):
    tag = Tag("测试")
    _host(qtbot, tag)
    assert tag.objectName() == "boringTag"
    assert tag.font().pixelSize() == 11
    assert 14 <= tag.height() <= 20
    assert tag.width() < 60
    assert _name(tag) == "测试"


# -- Card and CardHeader ----------------------------------------------------------

def test_card_with_header_and_action(qtbot):
    action = Button("打开", size="small")
    card = Card("屏幕", role="widget", action=action)
    card.layout().addWidget(QLabel("亮度"))
    host = _host(qtbot, card)
    assert card.property("cardRole") == "widget"
    assert isinstance(card.header, CardHeader)
    assert card.header.title_label.text() == "屏幕"
    assert card.header.title_label.font().pixelSize() == 15
    margins = card.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (24, 20, 24, 22)
    # the action sits at the right edge of the content, the title at the left
    right_edge = action.mapTo(card, action.rect().topRight()).x()
    assert right_edge == card.width() - 1 - 24 - card.frameWidth()
    inset = 24 + card.frameWidth()  # the card's 1 px outline comes first
    assert card.header.title_label.mapTo(card, card.header.title_label.rect().topLeft()).x() == inset
    assert _name(card) == "屏幕"
    host.close()


def test_card_without_title_has_no_header():
    card = Card(role="primary", dots=True)
    assert card.header is None
    with pytest.raises(ValueError):
        Card(action=QPushButton())


# -- SettingRow -----------------------------------------------------------------

def test_setting_row_gives_wide_controls_the_rest_of_the_row(qtbot):
    slider = DotMatrixSlider(Qt.Orientation.Horizontal)
    slider.setSizePolicy(QSizePolicy.Policy.Expanding, slider.sizePolicy().verticalPolicy())
    row = SettingRow("亮度", slider)
    _host(qtbot, row, width=480)
    assert slider.width() > row.width() // 2
    assert _name(slider) == "亮度"
    assert row.title_label.buddy() is slider


def test_setting_row_puts_compact_controls_at_the_right_end(qtbot):
    toggle = QPushButton("开")
    toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    toggle.setAccessibleName("按键反馈开关")
    row = SettingRow("按键反馈", toggle, note="按下按键时轻震一下。")
    _host(qtbot, row, width=480)
    assert toggle.geometry().right() == row.width() - 1
    assert row.note_label is not None and row.note_label.text() == "按下按键时轻震一下。"
    assert row.note_label.font().pixelSize() == 12
    assert row.title_label.font().pixelSize() == 13
    assert toggle.accessibleName() == "按键反馈开关"  # an existing name is kept


def test_setting_row_without_control(qtbot):
    row = SettingRow("固件版本")
    _host(qtbot, row)
    assert row.control is None and row.note_label is None


# -- EmptyState -----------------------------------------------------------------

def test_empty_state_text_and_optional_button(qtbot):
    plain = EmptyState("尚无运行记录")
    with_action = EmptyState("还没有自定义动作。", action_text="新建动作")
    _host(qtbot, plain, with_action)
    assert plain.action is None
    assert plain.label.text() == "尚无运行记录"
    assert plain.label.wordWrap()
    assert isinstance(with_action.action, Button)
    assert with_action.action.height() == 34 + 2  # small outline button
    clicks = []
    with_action.action.clicked.connect(lambda: clicks.append(True))
    with_action.action.setFocus()
    qtbot.keyClick(with_action.action, Qt.Key.Key_Space)
    assert clicks == [True]
    with_action.set_text("已清空")
    assert with_action.label.text() == "已清空"
    assert _name(with_action.label) == "已清空"


def test_components_keep_their_look_inside_a_dialog(qtbot):
    """Dialog label rules must not repaint tags and empty states as body text."""
    dialog = Dialog(None, "详情")
    tag = Tag("测试")
    empty = EmptyState("尚无会话")
    row = QHBoxLayout()
    row.addWidget(tag)
    row.addWidget(empty)
    holder = QWidget()
    holder.setLayout(row)
    dialog.body_layout().addWidget(holder)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert tag.font().pixelSize() == 11
    assert empty.label.palette().color(empty.label.foregroundRole()).name().upper() == tokens.TEXT_SECONDARY.upper()
