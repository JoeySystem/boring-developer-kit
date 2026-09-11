import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QFrame

from controller_config.views.main_window import _device_shell_widget, APP_STYLE, V4_STYLE
from test_session_recovery import session


def test_reference_geometry_and_control_clicks(session, qtbot, tmp_path):
    _window, _vm, _gateway, snapshot, _store = session
    selected = []
    shell = _device_shell_widget(snapshot, mappings=snapshot.mappings,
                                on_control=selected.append, selected_control_id=None)
    shell.setStyleSheet(APP_STYLE + V4_STYLE)
    qtbot.addWidget(shell)
    shell.show()
    qtbot.wait(50)
    face = shell.findChild(QFrame, "deviceFace")
    controls = {b.property("controlId"): b for b in face.findChildren(QPushButton)}
    assert shell.width() == shell.height() == 420
    assert controls["key.1"].geometry().getRect() == (147, 80, 64, 64)
    assert controls["key.8"].geometry().getRect() == (80, 214, 131, 64)
    assert controls["encoder"].geometry().getRect() == (0, 292, 128, 128)
    assert controls["joystick"].geometry().getRect() == (287, 287, 52, 52)
    assert {control_id: button.property("bluetoothSlot") for control_id, button in controls.items()
            if button.property("bluetoothSlot") is not None} == {"key.8": 1, "key.9": 2, "key.10": 3}
    for control_id, button in controls.items():
        assert face.rect().contains(button.geometry())
        qtbot.mouseClick(button, Qt.LeftButton)
        assert selected[-1] == control_id
    assert len(selected) == 14
    assert shell.grab().save(str(tmp_path / "device-reference.png"))


@pytest.mark.parametrize("page", ["overview", "lighting", "prompts"])
def test_reference_layout_in_pages(session, qtbot, tmp_path, page):
    window, vm, _gateway, _snapshot, _store = session
    window.resize(1440, 900)
    window.show()
    vm.navigate(page)
    qtbot.wait(180)
    assert window.grab().save(str(tmp_path / f"device-{page}.png"))
