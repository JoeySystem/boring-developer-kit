"""The ring-of-dots progress and the one-row dot reading (进度条点阵样式 定稿 v1, design decision)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QPushButton

from controller_config.style import tokens
from controller_config.views.ui import DotRow, ProgressRing, ProgressRingRow
from controller_config.views.ui import progress_ring as module


@pytest.fixture
def ring(qtbot):
    def make(size=48, **kwargs):
        widget = ProgressRing(size, **kwargs)
        qtbot.addWidget(widget)
        return widget
    return make


@pytest.fixture
def reduced_motion(monkeypatch):
    monkeypatch.setattr(module, "system_reduces_motion", lambda: True)


def test_three_sizes_and_their_dots(ring):
    assert [(ring(size).dot_count(), ring(size).width()) for size in (48, 36, 18)] == [
        (20, 48), (16, 36), (12, 18),
    ]
    with pytest.raises(ValueError):
        ProgressRing(88)  # the large ring was dropped in v0.2


@pytest.mark.parametrize("value, lit", [(0, 0), (5, 1), (37, 8), (62, 13), (100, 20)])
def test_dots_light_clockwise_and_any_start_lights_the_first(ring, value, lit):
    widget = ring(48)
    widget.set_value(value)
    assert widget.lit_dots() == lit


def test_number_is_the_real_value_without_a_percent_sign(ring):
    medium, small, tiny = ring(48), ring(36), ring(18)
    for widget in (medium, small, tiny):
        widget.set_value(37.4)
    assert medium.number_text() == "37"
    assert small.number_text() == "37"
    assert tiny.number_text() == ""  # no room: the number goes in the tab's tooltip
    assert medium.accessibleName() == "进度 37%"


def test_number_uses_equal_width_digits(ring):
    font = module._number_font(ring(48), tokens.PROGRESS_RING_NUMBER_MEDIUM)
    assert font.featureValue(QFont.Tag("tnum")) == 1


def test_not_known_yet_chases_round_and_stops_when_hidden(ring, qtbot):
    widget = ring(48)
    widget.set_value(None)
    widget.show()
    assert widget.is_animating()
    assert widget.number_text() == ""
    first = [colour.name() for colour in widget.dot_colours()]
    qtbot.wait(module.CHASE_MS // 3)
    assert [colour.name() for colour in widget.dot_colours()] != first
    widget.hide()
    assert not widget.is_animating()


def test_reached_dot_blinks_while_running(ring, qtbot):
    widget = ring(48)
    widget.show()
    widget.set_value(40)
    qtbot.wait(module.GROW_MS + 20)
    assert widget.is_animating()
    head = widget.lit_dots() - 1
    seen = set()
    for _ in range(6):
        seen.add(widget.dot_colours()[head].name())
        qtbot.wait(module.BLINK_MS // 6)
    assert len(seen) > 1
    assert all(colour == QColor(tokens.PROGRESS_RING_LIT) for colour in widget.dot_colours()[:head])


def test_reduce_motion_keeps_the_head_still_and_breathes_instead_of_chasing(ring, reduced_motion, qtbot):
    widget = ring(48)
    widget.show()
    widget.set_value(40)
    assert not widget.is_animating()
    widget.set_value(None)
    assert widget.is_animating()
    colours = widget.dot_colours()
    assert len({colour.name() for colour in colours}) == 1  # the whole ring together


def test_done_holds_all_lit_then_shows_the_green_check(ring, qtbot):
    widget = ring(48)
    widget.show()
    widget.set_value(100)
    widget.set_state("done")
    assert widget.lit_dots() == 20
    assert widget.number_text() == ""
    assert not widget.check_visible()
    qtbot.waitUntil(widget.check_visible, timeout=module.DONE_HOLD_MS + 500)
    assert not widget.is_animating()
    assert widget.accessibleName() == "已完成"


def test_value_updates_after_done_keep_the_check(ring, reduced_motion):
    widget = ring(48)
    widget.show()
    widget.set_state("done")
    widget.set_value(99)
    assert widget.check_visible()


def test_done_shows_the_check_at_once_with_reduce_motion(ring, reduced_motion):
    widget = ring(36)
    widget.show()
    widget.set_state("done")
    assert widget.check_visible()


def test_failed_turns_every_lit_dot_orange_and_shows_the_triangle(ring):
    # the designer 11-20 真屏验收 第 13 条: all the lit dots go orange, not only one.
    widget = ring(48)
    widget.show()
    widget.set_value(62)
    widget.set_state("failed")
    colours = widget.dot_colours()
    assert all(colour == QColor(tokens.PROGRESS_RING_FAILED) for colour in colours[:13])
    assert all(colour == QColor(tokens.PROGRESS_RING_UNLIT) for colour in colours[13:])
    assert widget.triangle_visible()
    assert widget.number_text() == ""
    assert not widget.is_animating()


def test_waiting_stops_and_greys_but_keeps_the_number(ring):
    widget = ring(48)
    widget.show()
    widget.set_value(62)
    widget.set_state("paused")
    assert not widget.is_animating()
    assert widget.number_text() == "62"
    assert widget.dot_colours()[0] == QColor(tokens.PROGRESS_RING_PAUSED)


def test_smallest_ring_has_no_shape_in_the_middle(ring):
    widget = ring(18)
    widget.show()
    widget.set_value(50)
    widget.set_state("failed")
    assert not widget.triangle_visible()
    assert widget.dot_colours()[5] == QColor(tokens.PROGRESS_RING_FAILED)


def test_quota_ring_never_moves_and_turns_orange_when_nearly_used_up(ring):
    widget = ring(36, quota=True, lit=tokens.MENUBAR_QUOTA_RING_LIT, unlit=tokens.MENUBAR_QUOTA_RING_UNLIT)
    widget.show()
    widget.set_value(62)
    assert not widget.is_animating()
    assert not widget.is_low()
    assert widget.dot_colours()[0] == QColor(tokens.MENUBAR_QUOTA_RING_LIT)
    assert widget.dot_colours()[-1] == QColor(*tokens.MENUBAR_QUOTA_RING_UNLIT)
    widget.set_value(tokens.QUOTA_LOW_PERCENT)
    assert widget.is_low()
    assert widget.dot_colours()[0] == QColor(tokens.PROGRESS_RING_LOW)
    assert widget.accessibleName() == f"剩余 {tokens.QUOTA_LOW_PERCENT}%"


def test_ring_ignores_the_pointer_and_focus(ring):
    widget = ring(48)
    assert widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_unknown_state_is_rejected(ring):
    with pytest.raises(ValueError):
        ring(48).set_state("busy")


def test_ring_row_puts_the_ring_left_text_right_and_a_button_at_the_end(qtbot):
    row = ProgressRingRow(48, object_name="firmwareProgress")
    qtbot.addWidget(row)
    cancel = QPushButton("取消上传")
    row.addWidget(cancel)
    row.set_text("正在上传图标")
    row.resize(400, 60)
    row.show()
    assert row.objectName() == "firmwareProgress"
    assert row.text() == "正在上传图标"
    assert row.ring.geometry().right() < row.label.geometry().left() < cancel.geometry().left()


def test_dot_row_lights_up_to_the_reading(qtbot):
    row = DotRow(64, maximum=4095)
    qtbot.addWidget(row)
    row.resize(row.sizeHint())
    assert row.visible_dots() == 64
    row.set_value(0)
    assert row.lit_dots() == 0
    row.set_value(4095)
    assert row.lit_dots() == 64
    row.set_value(200)
    assert row.lit_dots() == 4
    assert row.accessibleName() == "200 / 4095"


def test_dot_row_demo_steps_and_back_and_forth_run(qtbot):
    row = DotRow(48, maximum=8)
    qtbot.addWidget(row)
    row.resize(row.sizeHint())
    row.show()
    row.set_value(5)
    assert row.lit_dots() == 30  # 6 dots a step
    row.set_running(True)
    first = row.lit_mask()
    assert sum(first) == 8
    qtbot.wait(300)
    assert row.lit_mask() != first
    row.set_running(False)
    assert row.lit_mask()[:30] == [True] * 30


def test_dot_row_takes_fewer_dots_when_the_layout_is_tighter(qtbot):
    row = DotRow(64)
    qtbot.addWidget(row)
    assert row.maximumWidth() == row.sizeHint().width()
    row.resize(120, row.sizeHint().height())
    assert row.visible_dots() == 17
