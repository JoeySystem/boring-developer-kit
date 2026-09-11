from PySide6.QtCore import QObject, QRect, QSize, Signal
from PySide6.QtWidgets import QDialog

from test_session_recovery import session


def test_default_and_minimum_window_size(session, qtbot):
    window, *_ = session
    assert window.size() == QSize(1280, 800)
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 1600, 1000))
    window.resize(600, 400)
    qtbot.wait(30)
    assert window.size() == QSize(1100, 700)
    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    dialog.resize(320, 200)
    assert dialog.size() == QSize(320, 200)


def test_small_work_area_then_return_to_large_screen(session, qtbot):
    window, *_ = session
    window.show()
    window.move(1400, 300)
    small = QRect(100, 40, 1024, 640)
    window._fit_window_to_available_area(small)
    qtbot.wait(30)
    assert window.minimumSize() == QSize(1024, 640)
    assert small.contains(window.geometry())
    window._fit_window_to_available_area(QRect(-1600, 30, 1600, 970))
    qtbot.wait(30)
    assert window.minimumSize() == QSize(1100, 700)
    assert QRect(-1600, 30, 1600, 970).contains(window.geometry())


def test_screen_work_area_changes_update_bounds(session, qtbot):
    class Screen(QObject):
        availableGeometryChanged = Signal(QRect)

        def availableGeometry(self):
            return QRect(0, 0, 1600, 1000)

    window, *_ = session
    window.show()
    screen = Screen()
    window._watch_window_screen(screen)
    screen.availableGeometryChanged.emit(QRect(0, 40, 1000, 620))
    qtbot.wait(30)
    assert window.minimumSize() == QSize(1000, 620)
    assert QRect(0, 40, 1000, 620).contains(window.geometry())
    window._watch_window_screen(window.screen())
    previous_size = window.minimumSize()
    screen.availableGeometryChanged.emit(QRect(0, 0, 780, 560))
    assert window.minimumSize() == previous_size
