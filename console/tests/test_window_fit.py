"""design decision 窗口尺寸规矩 for 固件更新 and 摇杆校准, measured by tools/fit_study.py.

At the smallest window (design decision: 960 × 640), in all three languages and in
every state the tool knows (normal, running, failed, expanded, error notes),
nothing scrolls, no text is cut and the wall keeps 16 px free under it.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QPushButton

from controller_config.joystick_calibration import CalibrationState, CalibrationTransaction
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MINIMUM_WINDOW_HEIGHT, MINIMUM_WINDOW_WIDTH, MainWindow

_TOOL = Path(__file__).resolve().parents[1] / "tools" / "fit_study.py"
_spec = importlib.util.spec_from_file_location("fit_study", _TOOL)
fit_study = importlib.util.module_from_spec(_spec)
sys.modules["fit_study"] = fit_study  # its dataclass looks itself up there
_spec.loader.exec_module(fit_study)


def test_window_opens_and_stops_at_960_by_640(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    assert (MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT) == (960, 640)
    assert window.minimumSize().toTuple() == (960, 640)
    assert window.size().toTuple() == (960, 640)


@pytest.mark.parametrize("page", ["firmware", "joystick"])
def test_every_state_fits_the_smallest_window(qapp, page):
    # menu_bar=0: the offscreen content area is 24 px shorter than on a real Mac (456 instead of
    # 480), so this is stricter than the study's real-size numbers.
    results = fit_study.run(sizes=((MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT),), pages=(page,), app=qapp,
                            menu_bar=0)
    assert results
    bad = [r for r in results if not r.ok]
    assert not bad, fit_study.table(bad)


def test_calibration_locks_the_settings_rail(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None, timeout=2000)
    vm.navigate("joystick")

    def rail():
        return [button for button in window._content.findChildren(QPushButton, "settingsGroup")
                if button.isVisible()]

    qtbot.waitUntil(lambda: len(rail()) == 5)
    selected = [button.property("settingsSection") for button in rail() if button.property("selected")]
    assert selected == ["device"]
    assert all(button.isEnabled() for button in rail())
    try:
        vm._calibration = replace(vm.calibration, state=CalibrationState.CAPTURING)
        assert vm.calibration.blocks_editing
        vm.changed.emit(vm.model)
        qtbot.waitUntil(lambda: bool(rail()) and not any(button.isEnabled() for button in rail()))
        assert window.findChild(QPushButton, "backToDeviceSettings") is None   # the rail is the way back
    finally:
        vm._calibration = CalibrationTransaction()
        vm.changed.emit(vm.model)
    qtbot.waitUntil(lambda: bool(rail()) and all(button.isEnabled() for button in rail()))
