"""One row of key light colours (灯光页 排法二 v0.4)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from controller_config.style import tokens
from controller_config.style.sheets import application_stylesheet
from controller_config.views.ui import ColorPresetRow
from controller_config.views.ui.color_presets import _Swatches, preset_colors

PURPLE = (0x5B, 0x4B, 0xFF)
OLD_CUSTOM = (140, 43, 217)
_HOSTS: list[QWidget] = []  # keep hosts alive until qtbot closes them


def _row(qtbot, label: str = "键 3", rgb=None) -> ColorPresetRow:
    host = QWidget()
    _HOSTS.append(host)
    host.setStyleSheet(application_stylesheet())
    qtbot.addWidget(host)
    row = ColorPresetRow(host)
    row.set_key(label, rgb)
    host.resize(420, 60)
    row.resize(420, 40)
    host.show()
    return row


def _swatches(row: ColorPresetRow) -> _Swatches:
    return row.findChild(_Swatches)


def _dot(row: ColorPresetRow, index: int) -> QPoint:
    strip = _swatches(row)
    return QPoint(round(strip.centers()[index]), strip.height() // 2)


def test_eleven_presets_from_the_tokens() -> None:
    presets = preset_colors()
    assert [name for name, _ in presets] == ["白", "暖白", "黄", "橙", "红", "粉", "紫", "蓝紫", "蓝", "青", "绿"]
    assert presets[7][1] == PURPLE
    assert len({value for _, value in presets}) == 11


def test_row_is_label_presets_and_off(qtbot) -> None:
    row = _row(qtbot, rgb=PURPLE)
    kinds = [kind for kind, _value, _name in row.entries()]
    assert kinds == ["preset"] * 11 + ["off"]
    assert row.label_text() == "键 3"
    assert row.chosen_index() == 7
    assert not row.has_custom_color()


def test_off_and_black_choose_the_slash(qtbot) -> None:
    row = _row(qtbot, rgb=None)
    assert row.chosen_index() == 11
    row.set_key("键 8", (0, 0, 0))
    assert row.current() is None
    assert row.chosen_index() == 11


def test_click_applies_at_once(qtbot) -> None:
    row = _row(qtbot, rgb=PURPLE)
    with qtbot.waitSignal(row.color_chosen) as blocker:
        qtbot.mouseClick(_swatches(row), Qt.MouseButton.LeftButton, pos=_dot(row, 5))
    assert blocker.args == [preset_colors()[5][1]]
    assert row.current() == preset_colors()[5][1]
    with qtbot.waitSignal(row.color_chosen) as blocker:
        qtbot.mouseClick(_swatches(row), Qt.MouseButton.LeftButton, pos=_dot(row, 11))
    assert blocker.args == [None]


def test_clicking_the_chosen_colour_again_is_quiet(qtbot) -> None:
    row = _row(qtbot, rgb=PURPLE)
    with qtbot.assertNotEmitted(row.color_chosen):
        qtbot.mouseClick(_swatches(row), Qt.MouseButton.LeftButton, pos=_dot(row, 7))


def test_set_key_does_not_emit(qtbot) -> None:
    row = _row(qtbot)
    with qtbot.assertNotEmitted(row.color_chosen):
        row.set_key("键 9", PURPLE)


def test_old_custom_colour_stays_until_replaced(qtbot) -> None:
    row = _row(qtbot, rgb=OLD_CUSTOM)
    assert row.has_custom_color()
    assert row.entries()[0] == ("custom", OLD_CUSTOM, "原来的颜色")
    assert row.chosen_index() == 0
    assert _swatches(row).divider_x() is not None
    width_with_custom = _swatches(row).sizeHint().width()
    with qtbot.waitSignal(row.color_chosen):
        qtbot.mouseClick(_swatches(row), Qt.MouseButton.LeftButton, pos=_dot(row, 1))  # 白
    assert row.current() == (255, 255, 255)
    assert not row.has_custom_color()                # no way back
    assert len(row.entries()) == 12
    assert _swatches(row).sizeHint().width() < width_with_custom


def test_keyboard_moves_the_choice(qtbot) -> None:
    row = _row(qtbot, rgb=PURPLE)
    strip = _swatches(row)
    chosen = []
    row.color_chosen.connect(chosen.append)
    qtbot.keyClick(strip, Qt.Key.Key_Right)
    qtbot.keyClick(strip, Qt.Key.Key_End)
    qtbot.keyClick(strip, Qt.Key.Key_Home)
    assert chosen == [preset_colors()[8][1], None, preset_colors()[0][1]]
    assert strip.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_chosen_dot_has_a_cream_ring_with_a_gap(qtbot) -> None:
    row = _row(qtbot, rgb=PURPLE)
    strip = _swatches(row)
    image = strip.grab().toImage()
    ratio = image.devicePixelRatio()
    center = strip.centers()[7]
    y = round(strip.height() / 2 * ratio)
    # dot edge at 10, gap 10..12, ring 12..14 px from the centre
    def at(offset):
        return image.pixelColor(round((center + offset) * ratio), y)
    assert at(0).name() == QColor(*PURPLE).name()
    ring = QColor(tokens.LIGHT_SWATCH_RING)

    def is_ring(colour):  # one step of antialiasing either way
        return max(abs(colour.red() - ring.red()), abs(colour.green() - ring.green()),
                   abs(colour.blue() - ring.blue())) <= 2

    assert is_ring(at(13))
    assert not is_ring(at(11))


def test_label_uses_the_component_sheet(qtbot) -> None:
    row = _row(qtbot)
    label = row.findChild(type(row._label), "colorPresetLabel")
    assert label.palette().color(label.foregroundRole()).name() == QColor(tokens.WALL_TEXT).name()
    assert label.font().pixelSize() == tokens.FONT_BODY
