"""Flat device top view (设备图俯视图 正式稿 v0.5) as the lighting page uses it."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
from controller_config.style import tokens
from controller_config.views.ui import DeviceTopView
from controller_config.views.ui.device_top_view import FUNCTION_KEYS, STATUS_KEYS


def _view(qtbot, side: int = 220, **kwargs) -> DeviceTopView:
    view = DeviceTopView(**kwargs)
    qtbot.addWidget(view)
    view.resize(side, side)
    view.show()
    return view


def _colour_at(view: DeviceTopView, point: QPointF) -> str:
    image = view.grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x() * ratio), round(point.y() * ratio)).name()


def _center(view: DeviceTopView, control_id: str) -> QPoint:
    return view.key_rect(control_id).center().toPoint()


def test_status_keys_come_from_the_official_list() -> None:
    assert set(STATUS_KEYS) == set(MATRIX12_AGENT_STATUS_KEYS)
    assert FUNCTION_KEYS == ("key.3", "key.8", "key.9", "key.10", "key.11", "key.12")


def test_plain_view_uses_the_draft_colours(qtbot) -> None:
    view = _view(qtbot)
    assert _colour_at(view, view.key_rect("key.1").center()) == QColor(tokens.TOP_VIEW_STATUS_CAP).name()
    assert _colour_at(view, view.key_rect("key.3").topLeft() + QPointF(4, 4)) == QColor(tokens.TOP_VIEW_KEY_CAP).name()
    # Body corner, inside the 5% margin ring: the frame colour.
    body = view.body_width()
    corner = QPointF((view.width() - body) / 2 + body * 0.5, (view.height() - body) / 2 + 2)
    assert _colour_at(view, corner) == QColor(tokens.TOP_VIEW_BODY).name()


def test_key_8_is_two_units_wide(qtbot) -> None:
    view = _view(qtbot)
    assert view.key_rect("key.8").width() == pytest.approx(view.key_rect("key.9").width() * 36.6 / 18)


def test_lit_key_is_filled_and_off_returns_to_the_cap(qtbot) -> None:
    view = _view(qtbot)
    view.set_key_colors({"key.3": (91, 75, 255), "key.9": (0, 0, 0), "key.1": (255, 0, 0)})
    lit = view.key_fill("key.3")
    assert lit.name() != QColor(tokens.TOP_VIEW_KEY_CAP).name()
    assert lit.blue() > lit.red()
    assert view.key_colors()["key.9"] is None          # black means off
    assert "key.1" not in view.key_colors()             # status keys are Codex's
    assert view.key_fill("key.9").name() == QColor(tokens.TOP_VIEW_KEY_CAP).name()
    assert view.key_fill("key.1").name() == QColor(tokens.TOP_VIEW_STATUS_CAP).name()


def test_lit_key_is_the_light_with_22_percent_cream(qtbot) -> None:
    # design ticket 第 2 件: light + 22% cream (#EFEAE0, the designer 11-20) at full brightness.
    view = _view(qtbot)
    view.set_key_colors({"key.9": (0x22, 0xD3, 0xC5), "key.3": (0xFF, 0x3B, 0x5C)})
    view.set_brightness(1)
    cream = QColor(tokens.TEXT_PRIMARY)
    assert cream.name() == "#efeae0" and tokens.TOP_VIEW_LIGHT_CREAM_SHARE == 0.22
    for control_id, light in (("key.9", (0x22, 0xD3, 0xC5)), ("key.3", (0xFF, 0x3B, 0x5C))):
        got = view.key_fill(control_id)
        want = [c + (k - c) * 0.22 for c, k in zip(light, (cream.red(), cream.green(), cream.blue()))]
        assert max(abs(g - w) for g, w in zip((got.red(), got.green(), got.blue()), want)) <= 1, (control_id, got.name())
    # the designer 11-20 真屏验收: 低／中／高／最高 do not change how deep the keys look; off is the cap
    full = view.key_fill("key.9").name()
    for level in (0.25, 0.5, 0.75):
        view.set_brightness(level)
        assert view.key_fill("key.9").name() == full
    cap = QColor(tokens.TOP_VIEW_KEY_CAP)
    view.set_brightness(0)
    assert view.key_fill("key.9").name() == cap.name()


def test_disconnected_dims_all_twelve_keys_to_one_colour(qtbot) -> None:
    # design ticket 第 1 件: without a device all 12 keys are #8C8A84.
    view = _view(qtbot)
    view.set_key_colors({"key.3": (255, 0, 0)})
    view.set_connected(False)
    offline = QColor("#8C8A84").name()
    assert QColor(tokens.TOP_VIEW_KEY_OFFLINE).name() == offline
    assert {view.key_fill(f"key.{n}").name() for n in range(1, 13)} == {offline}
    view.set_connected(True)
    assert view.key_fill("key.1").name() == QColor(tokens.TOP_VIEW_STATUS_CAP).name()


def test_selection_ring_has_a_gap(qtbot) -> None:
    view = _view(qtbot)
    view.set_selected("key.12")
    rect = view.key_rect("key.12")
    image = view.grab().toImage()
    ratio = image.devicePixelRatio()
    y = round(rect.center().y() * ratio)
    row = [image.pixelColor(x, y).name() for x in range(round((rect.left() - 2) * ratio), round(rect.center().x() * ratio))]
    accent, cap = QColor(tokens.ACCENT).name(), QColor(tokens.TOP_VIEW_KEY_CAP).name()
    assert accent in row and cap in row
    last_ring, first_face = len(row) - 1 - row[::-1].index(accent), row.index(cap)
    panel = QColor(tokens.TOP_VIEW_PANEL)
    # ring, then a panel-coloured gap (anti-aliased, 1.3 px), then the key face
    gap = [QColor(name) for name in row[last_ring + 1:first_face]]
    assert any(abs(c.red() - panel.red()) + abs(c.green() - panel.green()) + abs(c.blue() - panel.blue()) < 60
               for c in gap), row[last_ring:first_face + 1]


def test_hover_lifts_one_step(qtbot) -> None:
    view = _view(qtbot)
    assert view.key_fill("key.3", hovered=True).name() == QColor(tokens.TOP_VIEW_KEY_CAP_HOVER).name()
    qtbot.mouseMove(view, _center(view, "key.3"))
    qtbot.waitUntil(lambda: view.cursor().shape() == Qt.CursorShape.PointingHandCursor)


def test_click_selects_function_keys_only(qtbot) -> None:
    view = _view(qtbot)
    with qtbot.waitSignal(view.key_selected) as blocker:
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=_center(view, "key.10"))
    assert blocker.args == ["key.10"]
    assert view.selected() == "key.10"
    with qtbot.assertNotEmitted(view.key_selected):
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=_center(view, "key.1"))
    assert view.selected() == "key.10"


def test_set_selected_does_not_emit(qtbot) -> None:
    view = _view(qtbot)
    with qtbot.assertNotEmitted(view.key_selected):
        view.set_selected("key.3")
    with pytest.raises(ValueError):
        view.set_selected("key.99")


@pytest.mark.parametrize("control_id, tip", [
    ("key.3", "点击调整该按键灯颜色"), ("key.1", "状态灯由 Codex 自动控制。"), ("encoder", "旋钮不带可配置灯光"),
])
def test_tooltips_keep_the_current_copy(qtbot, monkeypatch, control_id, tip) -> None:
    from controller_config.views.ui import device_top_view

    view = _view(qtbot)
    shown = []
    monkeypatch.setattr(device_top_view.QToolTip, "showText", lambda _pos, text, *_args: shown.append(text))
    if control_id == "encoder":
        knob = view._knob_rect()
        point = knob.center().toPoint()
    else:
        point = _center(view, control_id)
    assert view.control_at(QPointF(point)) == control_id
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtCore import QEvent
    view.event(QHelpEvent(QEvent.Type.ToolTip, point, view.mapToGlobal(point)))
    assert shown == [tip]


def test_keyboard_moves_between_function_keys(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    view = DeviceTopView(host)
    view.resize(220, 220)
    host.show()
    view.setFocus(Qt.FocusReason.TabFocusReason)
    selected = []
    view.key_selected.connect(selected.append)
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert view.selected() == "key.3"
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.selected() == "key.10"
    qtbot.keyClick(view, Qt.Key.Key_Left)
    assert view.selected() == "key.9"
    qtbot.keyClick(view, Qt.Key.Key_Left)
    assert view.selected() == "key.8"
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert view.selected() == "key.11"
    qtbot.keyClick(view, Qt.Key.Key_Right)
    assert view.selected() == "key.12"
    assert selected == ["key.3", "key.10", "key.9", "key.8", "key.11", "key.12"]


@pytest.mark.parametrize("mode, side", [("interactive", 198), ("marker", 106), ("icon", 36)])
def test_three_minimum_sizes(qtbot, mode, side) -> None:
    """Body 180 / 96 / 32 px plus the 5% margin on each side."""
    view = DeviceTopView(mode=mode)
    qtbot.addWidget(view)
    assert view.minimumSizeHint().width() == side
    assert view.heightForWidth(150) == 150
    assert (view.focusPolicy() == Qt.FocusPolicy.StrongFocus) == (mode == "interactive")


def test_marker_mode_is_not_clickable(qtbot) -> None:
    view = _view(qtbot, side=110, mode="marker")
    view.set_selected("key.12")
    with qtbot.assertNotEmitted(view.key_selected):
        qtbot.mouseClick(view, Qt.MouseButton.LeftButton, pos=_center(view, "key.3"))
    assert view.selected() == "key.12"


def test_icon_size_drops_the_panel(qtbot) -> None:
    view = _view(qtbot, side=36, mode="icon")
    panel_spot = QPointF(view.width() / 2 - view.body_width() * 0.40, view.height() / 2)
    assert _colour_at(view, panel_spot) == QColor(tokens.TOP_VIEW_BODY).name()


def _near(colour: str, token: str, tolerance: int = 32) -> bool:
    """Close to a token colour (the mark's thin lines are antialiased)."""
    a, b = QColor(colour), QColor(token)
    return all(abs(x - y) <= tolerance for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))


def test_part_marks_for_the_prompt_demo(qtbot):
    """看演示第 2、3 步：摇杆橙框＋朝上箭头、帽子往上挪；旋钮橙环＋变深（正式稿 v0.5 第 6 张，design decision）."""
    view = _view(qtbot, mode="marker")
    resting = view.joystick_cap_center()
    assert view.knob_fill().name().upper() == tokens.TOP_VIEW_KNOB

    view.set_part_mark("joystick.up")
    pushed = view.joystick_cap_center()
    assert pushed.x() == pytest.approx(resting.x())
    assert pushed.y() < resting.y()
    base = view._base_rect()
    above_base = QPointF(pushed.x(), base.top() - 1)   # the arrow runs past the orange frame
    assert _near(_colour_at(view, above_base), tokens.TOP_VIEW_MARK)
    assert view.selected() is None

    view.set_part_mark("encoder.press")
    assert view.knob_fill().name().upper() == tokens.TOP_VIEW_KNOB_PRESSED
    assert view.joystick_cap_center() == resting
    ring = view._knob_rect(0.8)
    assert _near(_colour_at(view, QPointF(ring.center().x(), ring.top())), tokens.TOP_VIEW_MARK)

    view.set_part_mark(None)
    assert view.part_mark() is None
    with pytest.raises(ValueError):
        view.set_part_mark("joystick.press")


def test_mode_page_device_also_dims_all_twelve_keys(qtbot) -> None:
    from controller_config.views.ui.mode_device import ModeDeviceView

    view = ModeDeviceView()
    qtbot.addWidget(view)
    view.set_status_colors({"key.1": "#FF6D00"})
    view.set_connected(False)
    offline = QColor(tokens.TOP_VIEW_KEY_OFFLINE).name()
    assert {view.key_fill(f"key.{n}").name() for n in range(1, 13)} == {offline}
    view.set_connected(True)
    assert view.key_fill("key.1").name() == QColor("#FF6D00").name()
