"""Physical controls must retain their proportions after selection/style changes."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QWidget

from test_session_recovery import session


def assert_physical_proportions(window):
    shell = window.findChild(QWidget, 'deviceShell')
    assert shell.width() == shell.height()
    display = shell.findChild(QWidget, 'displayControl')
    assert display.width() == display.height()
    controls = {button.property('controlId'): button for button in shell.findChildren(QPushButton)}
    for name, button in controls.items():
        if name == 'key.8':
            assert abs(button.width() / button.height() - 131 / 64) < .05
        else:
            assert button.width() == button.height(), (name, button.size(), shell.size())
    return controls


@pytest.mark.parametrize('size', [(1920, 1050), (1100, 700)])
def test_select_after_resize_preserves_all_physical_control_proportions(session, qtbot, tmp_path, size):
    window, *_ = session
    window.resize(*size)
    window.show()
    qtbot.wait(300)
    # Selection repolishes every physical button without necessarily resizing the stage.
    for name in ('key.9', 'key.1', 'encoder', 'joystick'):
        shell = window.findChild(QWidget, 'deviceShell')
        button = next(b for b in shell.findChildren(QPushButton) if b.property('controlId') == name)
        qtbot.mouseClick(button, Qt.LeftButton)
        qtbot.wait(150)
        assert window._selected_control_id == name or window._selected_control_id.startswith(name + '.')
        assert window.grab().save(str(tmp_path / f'selected-{name}.png'))
        assert_physical_proportions(window)
    for width, height in ((1280, 800), (1920, 1050), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(150)
        assert_physical_proportions(window)
    window.hide()
    window.show()
    qtbot.wait(150)
    assert_physical_proportions(window)
