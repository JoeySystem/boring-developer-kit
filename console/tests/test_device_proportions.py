"""The drawn device keeps its proportions through selection and window resizes (Mode页 定稿 v1)."""
import pytest
from PySide6.QtCore import QRectF, Qt

from controller_config.views.mode_page import ModeStage
from test_session_recovery import session

KEYS = tuple(f"key.{number}" for number in range(1, 13))


def _device(window):
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    return stage.device


def _proportions(device):
    """Every key's rectangle relative to the device's side, plus the device's own shape."""
    assert device.width() == device.height()
    side = float(device.width())
    bounds = QRectF(device.rect())
    found = {}
    for name in KEYS:
        rect = device.key_rect(name)
        assert bounds.contains(rect), name
        found[name] = (rect.x() / side, rect.y() / side, rect.width() / side, rect.height() / side)
    return found


def _assert_same(found, expected, side):
    for name, values in expected.items():
        for got, want in zip(found[name], values):
            # One pixel of rounding on the device's side.
            assert abs(got - want) * side <= 1.5, name


def _click_point(device, name):
    if name == "encoder":
        return device._point(-41.45, 41.4).toPoint()
    if name == "joystick":
        return device._point(27.5, 30.1).toPoint()
    return device.key_rect(name).center().toPoint()


@pytest.mark.parametrize("size", [(1920, 1050), (1100, 700)])
def test_select_after_resize_preserves_all_physical_control_proportions(session, qtbot, tmp_path, size):
    window, *_ = session
    window.resize(*size)
    window.show()
    qtbot.wait(300)
    expected = _proportions(_device(window))
    # Choosing a control rings it or opens the card; neither may reshape the device.
    for name in ("key.9", "key.1", "encoder", "joystick"):
        device = _device(window)
        qtbot.mouseClick(device, Qt.LeftButton, pos=_click_point(device, name))
        qtbot.wait(150)
        assert window._selected_control_id == name or window._selected_control_id.startswith(name + ".")
        assert window.grab().save(str(tmp_path / f"selected-{name}.png"))
        device = _device(window)
        _assert_same(_proportions(device), expected, device.width())
    sides = []
    for width, height in ((1280, 800), (1920, 1050), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(150)
        device = _device(window)
        sides.append(device.width())
        _assert_same(_proportions(device), expected, device.width())
    assert sides[1] > sides[2], "The device must grow with the window"
    window.hide()
    window.show()
    qtbot.wait(150)
    device = _device(window)
    _assert_same(_proportions(device), expected, device.width())
