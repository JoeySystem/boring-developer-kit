import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QFrame

from controller_config.views.device_silhouette import (
    DeviceModelCanvas,
    create_device_silhouette,
)
from controller_config.views.main_window import APP_STYLE, V4_STYLE
from test_session_recovery import session


def test_reference_geometry_and_control_clicks(session, qtbot, tmp_path):
    _window, _vm, _gateway, snapshot, _store = session
    selected = []
    shell = create_device_silhouette(
        snapshot,
        mappings=snapshot.mappings,
        on_control=selected.append,
        selected_control_id=None,
    )
    shell.setStyleSheet(APP_STYLE + V4_STYLE)
    qtbot.addWidget(shell)
    shell.show()
    qtbot.wait(50)
    face = shell.findChild(QFrame, "deviceFace")
    canvas = shell.findChild(DeviceModelCanvas, "deviceModelCanvas")
    controls = {b.property("controlId"): b for b in face.findChildren(QPushButton)}
    assert shell.width() == shell.height() == 420
    assert shell.property("usesControlSchematic") is True
    assert canvas is not None
    for control_id, button in controls.items():
        expected = canvas.control_rect(control_id)
        assert button.geometry().getRect() == (
            round(expected.left()),
            round(expected.top()),
            max(24, round(expected.width())),
            max(24, round(expected.height())),
        )
    bluetooth_slots = {
        control_id: button.property("bluetoothSlot")
        for control_id, button in controls.items()
        if button.property("bluetoothSlot") is not None
    }
    assert bluetooth_slots == {"key.8": 1, "key.9": 2, "key.10": 3}
    for control_id, button in controls.items():
        assert face.rect().contains(button.geometry())
        qtbot.mouseClick(button, Qt.LeftButton)
        assert selected[-1] == control_id
    assert len(selected) == 14
    assert shell.grab().save(str(tmp_path / "device-reference.png"))


def test_public_schematic_controls_are_dynamic_without_screen_artwork(session, qtbot):
    _window, _vm, _gateway, snapshot, _store = session
    shell = create_device_silhouette(snapshot, mappings=snapshot.mappings)
    qtbot.addWidget(shell)
    shell.show()
    canvas = shell.findChild(DeviceModelCanvas, "deviceModelCanvas")
    assert canvas is not None
    for index in range(1, 13):
        control_id = f"key.{index}"
        canvas.preview_control(control_id)
        assert canvas._springs[control_id].target == 1.0
    canvas.preview_control("encoder.cw")
    clockwise = canvas._springs["encoder.rotation"].target
    canvas.preview_control("encoder.ccw")
    assert clockwise == 34.0
    assert canvas._springs["encoder.rotation"].target == 0.0
    for control_id, expected in {
        "joystick.up": (0.0, -1.0),
        "joystick.right": (1.0, 0.0),
        "joystick.down": (0.0, 1.0),
        "joystick.left": (-1.0, 0.0),
    }.items():
        canvas.preview_control(control_id)
        assert (
            canvas._springs["joystick.x"].target,
            canvas._springs["joystick.y"].target,
        ) == expected
    canvas.preview_control("encoder.press")
    canvas.preview_control("joystick.press")
    assert canvas._springs["encoder.press"].target == 1.0
    assert canvas._springs["joystick.press"].target == 1.0


@pytest.mark.parametrize("page", ["overview", "lighting", "prompts"])
def test_reference_layout_in_pages(session, qtbot, tmp_path, page):
    window, vm, _gateway, _snapshot, _store = session
    window.resize(1440, 900)
    window.show()
    vm.navigate(page)
    qtbot.wait(180)
    assert window.grab().save(str(tmp_path / f"device-{page}.png"))
