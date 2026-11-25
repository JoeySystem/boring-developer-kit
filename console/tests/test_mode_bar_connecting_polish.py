"""PR #105 和稿不一样的四处（the designer 11-23 "要改，现在就改"）：Mode 页底部那一条连接中的样子。"""
import dataclasses

from PySide6.QtCore import QElapsedTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from controller_config.style import tokens
from controller_config.views.mode_page import ModeBottomBar
from controller_config.views.ui.connect_steps_cell import ConnectStepsCell


def _bar(qtbot):
    bar = ModeBottomBar()
    qtbot.addWidget(bar)
    bar.show()
    base = dataclasses.replace(bar.state(), profile_name="Default", ble=True, ble_button=True, right="offline")
    bar.set_state(base)
    QApplication.processEvents()
    bar.set_state(base)          # the first pass lays out "Default" before the chip has its width
    QApplication.processEvents()
    return bar, base


def test_the_cream_bluetooth_button_folds_into_the_round_one_where_it_stands(qtbot, monkeypatch):
    from controller_config.views.ui import motion
    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    bar, base = _bar(qtbot)
    left, start = bar.ble.x(), bar.ble.width()
    bar.set_state(dataclasses.replace(base, ble_button=False, dimmed=True, right="connecting"))
    assert bar.ble_folding()
    assert bar.ble.property("round") == "false"          # still cream while it narrows
    qtbot.wait(tokens.BLE_FOLD_MS // 3)
    assert bar.ble.x() == left                            # in place: the left edge stays
    assert bar.ble.width() < start
    qtbot.waitUntil(lambda: not bar.ble_folding(), timeout=tokens.BLE_FOLD_MS * 3)
    assert bar.ble.property("round") == "true"
    assert not bar.ble_text.isVisible()
    assert bar.ble.width() == round(36 * bar._scale)


def test_without_motion_it_turns_round_at_once(qtbot, monkeypatch):
    from controller_config.views.ui import motion
    monkeypatch.setattr(motion, "reduces_motion", lambda: True)
    bar, base = _bar(qtbot)
    bar.set_state(dataclasses.replace(base, ble_button=False, dimmed=True, right="connecting"))
    assert not bar.ble_folding()
    assert bar.ble.property("round") == "true"


def test_default_goes_grey_while_connecting(qtbot):
    bar, base = _bar(qtbot)
    assert bar.profile_label.palette().windowText().color() == QColor(tokens.TEXT_PRIMARY)
    bar.set_state(dataclasses.replace(base, ble_button=False, dimmed=True, right="connecting"))
    QApplication.processEvents()
    assert bar.profile_label.palette().windowText().color() == QColor(tokens.TEXT_TERTIARY)


class _Clock:
    def __init__(self) -> None:
        self.now = 0

    def elapsed(self) -> int:
        return self.now


def test_a_new_target_mid_way_carries_the_speed_on(qtbot, monkeypatch):
    from controller_config.views.ui import motion
    monkeypatch.setattr(motion, "reduces_motion", lambda: False)
    cell = ConnectStepsCell()
    qtbot.addWidget(cell)
    clock = _Clock()
    cell._clock = clock
    cell.setFixedWidth(100)
    cell._spring_to = 100
    cell._spring_toward(200)
    clock.now = 150
    cell._spring_frame()
    speed_before = cell._spring_speed()
    cell._spring_toward(150)                 # the words change again while it still grows
    assert abs(cell._spring_speed() - speed_before) < 1e-6
    clock.now = 166
    cell._spring_frame()
    assert cell.width() >= cell._spring_from   # keeps growing a little before it turns back
    clock.now = 150 + tokens.CONNECT_STEPS_SPRING_MS
    cell._spring_frame()
    assert cell.width() == 150 and not cell.springing()
