"""Direction cross and small glyph on the Prompt page (Prompt 页 定稿 v1)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor

from controller_config.prompt_library import QUICK_PROMPT_DIRECTIONS
from controller_config.style import tokens
from controller_config.views.ui import DirectionCross, DirectionGlyph, direction_of
from controller_config.views.ui.direction_cross import (
    ARROW_ROWS, ARROW_STEP, ARROW_TIP, CELL, STEP_MS, arrow_dots, wrap_lines,
)

LONG_BODY = "请审查这段代码：找出潜在的 bug、边界条件和可读性问题，按严重程度从高到低列出，每条给出修改建议。"


def _cross(qtbot, side: int = 384, **kwargs) -> DirectionCross:
    kwargs.setdefault("reduce_motion", False)
    cross = DirectionCross(**kwargs)
    qtbot.addWidget(cross)
    cross.resize(side, side)
    cross.show()
    return cross


def _colour_at(widget, point: QPointF) -> str:
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x() * ratio), round(point.y() * ratio)).name()


def test_directions_follow_the_prompt_library():
    assert {prompt_id: direction_of(prompt_id) for _c, prompt_id, _a, _l in QUICK_PROMPT_DIRECTIONS} == {
        1: "up", 2: "right", 3: "down", 4: "left",
    }
    with pytest.raises(ValueError):
        direction_of(5)


def test_cells_sit_in_a_cross_around_the_joystick(qtbot):
    cross = _cross(qtbot)
    assert cross.scale() == pytest.approx(1.0)
    up, right, down, left = (cross.cell_rect(prompt_id) for prompt_id in (1, 2, 3, 4))
    centre = cross.joystick_center()
    assert up.width() == pytest.approx(CELL)
    assert up.center().x() == pytest.approx(centre.x()) == pytest.approx(down.center().x())
    assert left.center().y() == pytest.approx(centre.y()) == pytest.approx(right.center().y())
    assert up.bottom() < centre.y() < down.top()
    assert left.right() < centre.x() < right.left()


def test_states_choose_the_status_shape_and_validate(qtbot):
    cross = _cross(qtbot)
    cross.set_direction(1, "代码审查", LONG_BODY, "dirty")
    cross.set_direction(2, "翻译成英文", "请把下面的内容翻译成英文", "saved")
    assert cross._icons[1].state() == "wait"
    assert cross._icons[2].state() == "ok"
    assert cross._icons[4].state() == "off"
    with pytest.raises(ValueError):
        cross.set_direction(1, "x", "y", "draft")
    with pytest.raises(ValueError):
        cross.set_direction(9, "x", "y", "saved")


def test_empty_cell_says_add(qtbot):
    cross = _cross(qtbot)
    cross.set_direction(4, "ignored", "ignored", "empty")
    assert cross.shown_text(4) == (["＋ 添加"], [])


def test_body_gets_the_lines_the_name_leaves(qtbot):
    cross = _cross(qtbot)
    cross.set_direction(1, "代码审查", LONG_BODY, "saved")
    cross.set_direction(3, "总结要点并列出下一步要做的事", LONG_BODY, "saved")
    cross.set_direction(2, "一个非常非常长的名称会折成很多很多行一直写下去还没完", LONG_BODY, "saved")
    for prompt_id in (1, 3, 2):
        names, previews = cross.shown_text(prompt_id)
        assert 1 <= len(names) <= 3
        assert len(names) + len(previews) == 4
    assert len(cross.shown_text(1)[0]) == 1
    assert len(cross.shown_text(3)[0]) == 2
    names, previews = cross.shown_text(2)
    assert len(names) == 3 and names[-1].endswith("…")
    assert previews[-1].endswith("…")


def test_cut_lines_fit_the_cell(qtbot):
    cross = _cross(qtbot)
    font = cross._preview_font()
    width = cross.cell_rect(1).width() - 24
    lines = wrap_lines(LONG_BODY, font, width, 3)
    assert len(lines) == 3 and lines[-1].endswith("…")
    from PySide6.QtGui import QFontMetricsF
    metrics = QFontMetricsF(font)
    assert all(metrics.horizontalAdvance(line) <= width + 0.5 for line in lines)
    assert wrap_lines("短", font, width, 3) == ["短"]
    assert wrap_lines("", font, width, 3) == []
    assert wrap_lines("emoji 😀 不会切坏", font, width, 1)[0].startswith("emoji")


def test_selected_cell_is_cream_and_others_are_dark(qtbot):
    cross = _cross(qtbot)
    cross.set_direction(1, "代码审查", LONG_BODY, "dirty")
    cross.set_selected(1)
    up, left = cross.cell_rect(1), cross.cell_rect(4)
    # Bottom-left inside the corner radius margin, away from any text.
    assert _colour_at(cross, QPointF(up.left() + 20, up.bottom() - 4)) == QColor(tokens.PROMPT_CELL_SELECTED).name()
    assert _colour_at(cross, QPointF(left.left() + 20, left.bottom() - 4)) == QColor(tokens.PROMPT_CELL).name()


def test_arrow_has_five_dots_pointing_at_the_selection():
    dots = arrow_dots("up", ARROW_TIP, ARROW_STEP)
    assert len(dots) == 5
    tip = [point for row, point in dots if row == 0][0]
    assert (tip.x(), tip.y()) == (0.0, -ARROW_TIP)
    assert sorted(round(point.x(), 1) for row, point in dots if row == 2) == [-15.6, 15.6]
    right_tip = [point for row, point in arrow_dots("right", ARROW_TIP, ARROW_STEP) if row == 0][0]
    assert (right_tip.x(), right_tip.y()) == (ARROW_TIP, 0.0)


def test_tip_dot_is_drawn_lit(qtbot):
    cross = _cross(qtbot)
    cross.set_selected(2)
    tip = cross.joystick_center() + QPointF(ARROW_TIP, 0)
    assert _colour_at(cross, tip) == QColor(tokens.PROMPT_JOYSTICK).name()


def test_changing_direction_lights_from_the_disc_out_then_stops(qtbot):
    cross = _cross(qtbot)
    cross.set_selected(1)
    assert cross.lit_rows() == ARROW_ROWS          # first selection: no animation
    cross.set_selected(2)
    assert cross.lit_rows() == 0
    qtbot.waitUntil(lambda: cross.lit_rows() == 1, timeout=STEP_MS * 4)
    qtbot.waitUntil(lambda: cross.lit_rows() == ARROW_ROWS, timeout=STEP_MS * 6)
    assert not cross._timer.isActive()


def test_reduce_motion_lights_everything_at_once(qtbot):
    cross = _cross(qtbot, reduce_motion=True)
    cross.set_selected(1)
    cross.set_selected(3)
    assert cross.lit_rows() == ARROW_ROWS
    assert not cross._timer.isActive()


def test_click_asks_for_a_direction_without_selecting_it(qtbot):
    cross = _cross(qtbot)
    cross.set_selected(1)
    with qtbot.waitSignal(cross.direction_selected) as blocker:
        qtbot.mouseClick(cross, Qt.MouseButton.LeftButton, pos=cross.cell_rect(4).center().toPoint())
    assert blocker.args == [4]
    assert cross.selected() == 1                   # the page decides
    with qtbot.assertNotEmitted(cross.direction_selected):
        qtbot.mouseClick(cross, Qt.MouseButton.LeftButton, pos=cross.cell_rect(1).center().toPoint())
        qtbot.mouseClick(cross, Qt.MouseButton.LeftButton, pos=cross.joystick_center().toPoint())


def test_keyboard_arrows_and_enter(qtbot):
    cross = _cross(qtbot)
    cross.set_selected(1)
    cross.setFocus(Qt.FocusReason.TabFocusReason)
    with qtbot.waitSignal(cross.direction_selected) as blocker:
        qtbot.keyClick(cross, Qt.Key.Key_Down)
    assert blocker.args == [3]
    assert cross._cursor == 3
    with qtbot.waitSignal(cross.direction_selected) as blocker:
        qtbot.keyClick(cross, Qt.Key.Key_Return)
    assert blocker.args == [3]
    cross.set_selected(3)
    with qtbot.assertNotEmitted(cross.direction_selected):
        qtbot.keyClick(cross, Qt.Key.Key_Space)
    assert cross.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_four_dots_fill_for_directions_that_are_set(qtbot):
    cross = _cross(qtbot)
    cross.set_direction(1, "代码审查", LONG_BODY, "saved")
    corner = cross._grid_rect(0, 0)
    up_dot = QPointF(corner.left() + 2 + 4 + 12, corner.top() + 2 + 4)
    left_dot = QPointF(corner.left() + 2 + 4, corner.top() + 2 + 4 + 12)
    assert _colour_at(cross, up_dot) == QColor(tokens.PROMPT_SET_DOT).name()
    assert _colour_at(cross, left_dot) != QColor(tokens.PROMPT_SET_DOT).name()   # hollow


@pytest.mark.parametrize("side", [284, 316, 384])
def test_shrinks_with_the_tile(qtbot, side):
    cross = _cross(qtbot, side=side)
    cross.set_direction(3, "总结要点并列出下一步要做的事", LONG_BODY, "saved")
    assert cross.scale() == pytest.approx((side - 24) / 3 / CELL)
    names, previews = cross.shown_text(3)
    rect = cross.cell_rect(3)
    used = 30 + len(names) * 18 + (4 + len(previews) * 15 if previews else 0)
    assert names and used <= rect.height() - 10 + 0.5
    assert cross.minimumSizeHint().width() == 284
    assert cross.hasHeightForWidth() and cross.heightForWidth(300) == 300


def test_english_add_text(qtbot, qapp):
    previous = qapp.property("boringUiLanguage")
    qapp.setProperty("boringUiLanguage", "en_US")
    try:
        cross = _cross(qtbot)
        assert cross.shown_text(4) == (["+ Add"], [])
    finally:
        qapp.setProperty("boringUiLanguage", previous)


def test_glyph_points_and_validates(qtbot):
    glyph = DirectionGlyph("up")
    qtbot.addWidget(glyph)
    glyph.show()
    assert glyph.size().width() == 32 and glyph.size().height() == 32
    # Disc moved 4 px away from the arrow (down), arrow tip above the centre.
    assert _colour_at(glyph, QPointF(16, 20)) == QColor(tokens.PROMPT_JOYSTICK).name()
    assert _colour_at(glyph, QPointF(16, 16 - 9.6)) != QColor(tokens.PROMPT_GLYPH_WELL).name()
    assert _colour_at(glyph, QPointF(4, 28)) == QColor(tokens.PROMPT_GLYPH_WELL).name()
    glyph.set_direction("left")
    assert glyph.direction() == "left"
    with pytest.raises(ValueError):
        glyph.set_direction("north")
