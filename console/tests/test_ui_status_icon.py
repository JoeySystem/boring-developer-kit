"""The five status shapes (the designer status graphics v1 final)."""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from controller_config.style import tokens
from controller_config.views.ui import StatusIcon


def _image(qtbot, state, **kwargs):
    icon = StatusIcon(state, size=32, **kwargs)
    qtbot.addWidget(icon)
    icon.show()
    return icon, icon.grab().toImage()


def _colour_at(image, x, y):
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(x * ratio), round(y * ratio))


def test_ok_is_a_green_disc_with_the_check_cut_out(qtbot):
    _icon, image = _image(qtbot, "ok")
    assert _colour_at(image, 6, 16).name() == QColor(tokens.STATUS_DONE).name()
    # The check is a hole: whatever is behind shows through, not the disc colour.
    assert _colour_at(image, 14, 20).name() != QColor(tokens.STATUS_DONE).name()


@pytest.mark.parametrize("waiting_for, colour", [("you", tokens.ACCENT), ("device", tokens.TEXT_PRIMARY)])
def test_wait_colour_says_who_is_waited_for(qtbot, waiting_for, colour):
    icon, image = _image(qtbot, "wait", waiting_for=waiting_for)
    assert _colour_at(image, 16, 5).name() == QColor(colour).name()
    assert icon.accessibleName() in {"等你操作", "等待设备"}


def test_busy_spins_only_while_visible(qtbot):
    icon = StatusIcon("busy", size=16)
    qtbot.addWidget(icon)
    assert not icon._timer.isActive()
    icon.show()
    assert icon._timer.isActive()
    before = icon._angle
    qtbot.wait(80)
    assert icon._angle != before
    icon.set_state("ok")
    assert not icon._timer.isActive()


def test_off_and_error_paint_something(qtbot):
    for state in ("off", "error"):
        _icon, image = _image(qtbot, state)
        painted = sum(1 for x in range(32) for y in range(32) if _colour_at(image, x, y).alpha() > 0)
        assert painted > 20, state


def test_unknown_state_is_refused(qtbot):
    with pytest.raises(ValueError):
        StatusIcon("maybe")


def test_reduce_motion_busy_holds_still_and_without_it_it_spins(qtbot):
    """Console动效盘点 11-19 item 10; the still shape is a placeholder until the designer draws one."""
    still = StatusIcon("busy", size=16, reduce_motion=True)
    qtbot.addWidget(still)
    still.show()
    assert not still._timer.isActive()
    qtbot.wait(60)
    assert still._angle == 0
    still.set_state("ok")
    still.set_state("busy")
    assert not still._timer.isActive()
    assert not still.grab().toImage().isNull()

    spinning = StatusIcon("busy", size=16, reduce_motion=False)
    qtbot.addWidget(spinning)
    spinning.show()
    assert spinning._timer.isActive()
    qtbot.waitUntil(lambda: spinning._angle != 0, timeout=1000)
