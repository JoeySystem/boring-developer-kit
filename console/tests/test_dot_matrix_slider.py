"""The shared dot matrix slider (the designer 2026-11-18 final, every Console slider)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QSlider

from controller_config.style import tokens
from controller_config.views.ui import DotMatrixSlider
from controller_config.views.ui.dot_slider import DOT, MAX_SCALE, PITCH, ROWS


def _slider(qtbot, columns=36, low=0, high=100, value=59):
    slider = DotMatrixSlider(Qt.Orientation.Horizontal, columns=columns, objectName="testDots")
    slider.setRange(low, high)
    slider.setValue(value)
    qtbot.addWidget(slider)
    slider.resize(slider.sizeHint())
    slider.show()
    qtbot.waitExposed(slider)
    return slider


def _x(slider, column):
    return round(slider._column_x(column))


def test_is_a_qslider_so_existing_code_keeps_working(qtbot):
    slider = _slider(qtbot)
    assert isinstance(slider, QSlider)
    assert slider.objectName() == "testDots"


def test_size_follows_columns_and_leaves_room_for_the_bulge(qtbot):
    small = _slider(qtbot, columns=18)
    wide = _slider(qtbot, columns=36)
    assert wide.sizeHint().width() - small.sizeHint().width() == round(18 * PITCH)
    # Three rows of 4 px dots with 3 px gaps, plus room for a 1.7x column.
    assert wide.sizeHint().height() >= ROWS * PITCH - 3 + (MAX_SCALE - 1) * DOT


def test_lit_columns_follow_the_value(qtbot):
    slider = _slider(qtbot, value=0)
    assert slider.lit_columns() == 0
    slider.setValue(100)
    assert slider.lit_columns() == 36
    slider.setValue(50)
    assert slider.lit_columns() == 18


def test_click_and_drag_set_full_precision_values(qtbot):
    slider = _slider(qtbot, value=0)
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(_x(slider, 35), slider.height() // 2))
    assert slider.value() >= 98
    qtbot.mouseMove(slider, QPoint(_x(slider, 9), slider.height() // 2))
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(_x(slider, 9), slider.height() // 2))
    # Column 9 of 36 sits near 26 %; the value is not snapped to columns.
    assert 24 <= slider.value() <= 29
    assert not slider.isSliderDown()


def test_keyboard_steps_one_unit(qtbot):
    slider = _slider(qtbot, value=40)
    slider.setFocus()
    qtbot.keyClick(slider, Qt.Key.Key_Right)
    assert slider.value() == 41


def test_hover_raises_a_bulge_under_the_pointer_and_leaving_settles_it(qtbot):
    # Not shown: offscreen windows open under the pointer and would get hover events.
    slider = DotMatrixSlider(Qt.Orientation.Horizontal, columns=36)
    qtbot.addWidget(slider)
    slider._bulge_x = slider._column_x(10)
    slider._set_bulge(1.0)
    assert abs(slider._column_scale(10) - MAX_SCALE) < 1e-6
    assert 1.0 < slider._column_scale(12) < MAX_SCALE
    assert slider._column_scale(20) == 1.0
    slider._hovering = False
    slider._animate_bulge(0.0)
    qtbot.waitUntil(lambda: slider.bulge() == 0.0, timeout=2000)
    assert slider._column_scale(10) == 1.0


def test_dragging_keeps_the_bulge_on_the_lit_boundary(qtbot):
    slider = _slider(qtbot, value=0)
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(_x(slider, 14), slider.height() // 2))
    assert abs(slider._bulge_x - slider._boundary_x()) < 1e-6
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(_x(slider, 14), slider.height() // 2))


def test_paints_lit_and_dim_dots_in_the_token_colours(qtbot):
    slider = _slider(qtbot, value=50)
    image = slider.grab().toImage()
    ratio = image.devicePixelRatio()
    y = round(slider.height() / 2 * ratio)
    lit = image.pixelColor(round(slider._column_x(2) * ratio), y)
    dim = image.pixelColor(round(slider._column_x(33) * ratio), y)
    assert lit.name() == QColor(tokens.DOT_SLIDER_LIT).name()
    assert dim.name() == QColor(tokens.DOT_SLIDER_UNLIT).name()


def test_disabled_slider_shows_no_bulge(qtbot):
    slider = _slider(qtbot)
    slider._set_bulge(1.0)
    slider.setEnabled(False)
    assert slider.bulge() == 0.0


def test_a_tight_layout_gets_fewer_columns_not_overflow(qtbot):
    slider = _slider(qtbot, columns=36, value=50)
    assert slider.visible_columns() == 36
    slider.resize(slider.minimumSizeHint().width() + 20, slider.height())
    assert 2 <= slider.visible_columns() < 36
    assert slider.lit_columns() == round(slider.visible_columns() / 2)
    slider.resize(10, slider.height())
    assert slider.visible_columns() == 2


# -- snap: one continuous bar that lands on a few stops (lighting page v0.4) --

def _snap_slider(qtbot, value=0):
    slider = DotMatrixSlider(Qt.Orientation.Horizontal, columns=36, snap=True)
    slider.setRange(0, 4)  # off, low, medium, high, max
    slider.setValue(value)
    qtbot.addWidget(slider)
    slider.resize(slider.sizeHint())
    slider.show()
    qtbot.waitExposed(slider)
    return slider


def _at(slider, fraction):
    left = slider._column_x(0) - PITCH / 2
    right = slider._column_x(35) + PITCH / 2
    return QPoint(round(left + fraction * (right - left)), slider.height() // 2)


def test_snap_drag_moves_freely_and_the_value_is_the_nearest_stop(qtbot):
    slider = _snap_slider(qtbot)
    qtbot.mousePress(slider, Qt.MouseButton.LeftButton, pos=_at(slider, 0.1))
    qtbot.mouseMove(slider, _at(slider, 0.43))
    # Lit part follows the pointer (43 % of 36 columns), not the 50 % stop.
    assert slider.lit_columns() in (15, 16)
    assert slider.value() == 2
    qtbot.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=_at(slider, 0.43))
    qtbot.waitUntil(lambda: slider.lit_columns() == 18, timeout=2000)
    assert slider.value() == 2


def test_snap_release_at_36_percent_goes_back_to_25(qtbot):
    slider = _snap_slider(qtbot)
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton, pos=_at(slider, 0.36))
    assert slider.value() == 1
    qtbot.waitUntil(lambda: slider.lit_columns() == 9, timeout=2000)


def test_snap_ends_are_off_and_max(qtbot):
    slider = _snap_slider(qtbot, value=2)
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton, pos=_at(slider, 0.02))
    assert slider.value() == 0
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton, pos=_at(slider, 0.98))
    assert slider.value() == 4


def test_snap_keys_move_one_stop(qtbot):
    slider = _snap_slider(qtbot, value=1)
    slider.setFocus()
    qtbot.keyClick(slider, Qt.Key.Key_Right)
    assert slider.value() == 2
    assert slider.lit_columns() == 18


def test_a_value_between_stops_is_shown_until_touched(qtbot):
    slider = _snap_slider(qtbot, value=1)
    slider.set_between(0.375)
    assert slider.value() == 1
    assert slider.lit_columns() == round(0.375 * 36)
    slider.setValue(3)
    assert slider.lit_columns() == 27


# -- the system's "reduce motion" (Console动效盘点 11-19, item 7) ---------------

def _motion_slider(qtbot, reduce_motion, snap=False):
    slider = DotMatrixSlider(Qt.Orientation.Horizontal, columns=36, snap=snap, reduce_motion=reduce_motion)
    slider.setRange(0, 4 if snap else 100)
    slider.setValue(0 if snap else 59)
    qtbot.addWidget(slider)
    slider.resize(slider.sizeHint())
    slider.show()
    qtbot.waitExposed(slider)
    return slider


def test_reduce_motion_bulge_jumps_and_without_it_the_bulge_animates(qtbot):
    still = _motion_slider(qtbot, True)
    still._animate_bulge(1.0)
    assert still.bulge() == 1.0 and not still._animation.state() == still._animation.State.Running
    still._animate_bulge(0.0)
    assert still.bulge() == 0.0

    moving = _motion_slider(qtbot, False)
    moving._animate_bulge(1.0)
    assert moving._animation.state() == moving._animation.State.Running
    assert moving._animation.duration() == tokens.DOT_SLIDER_RISE_MS == 180 and moving.bulge() < 1.0
    qtbot.waitUntil(lambda: moving.bulge() == 1.0, timeout=2000)
    moving._animate_bulge(0.0)
    assert moving._animation.duration() == tokens.DOT_SLIDER_SETTLE_MS == 260


def test_reduce_motion_snap_lands_at_once_and_without_it_it_slides(qtbot):
    still = _motion_slider(qtbot, True, snap=True)
    qtbot.mouseClick(still, Qt.MouseButton.LeftButton, pos=_at(still, 0.36))
    assert still.value() == 1 and still.lit_columns() == 9  # on the stop straight away
    assert not still._snap_animation.state() == still._snap_animation.State.Running

    moving = _motion_slider(qtbot, False, snap=True)
    qtbot.mouseClick(moving, Qt.MouseButton.LeftButton, pos=_at(moving, 0.36))
    assert moving._snap_animation.state() == moving._snap_animation.State.Running
    assert moving._snap_animation.duration() == tokens.DOT_SLIDER_SNAP_MS == 180
    qtbot.waitUntil(lambda: moving.lit_columns() == 9, timeout=2000)
