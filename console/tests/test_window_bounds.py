from PySide6.QtCore import QObject, QPoint, QRect, QSize, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

from controller_config.appearance import uses_native_window_controls
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_session_recovery import session
from test_write_transaction import FakeWriteGateway


def test_default_and_minimum_window_size(qtbot, contract):
    vm = MainViewModel(FakeWriteGateway(), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    try:
        # Check the default before Show fits it to the actual screen work area.
        # design decision: it opens at the smallest size, 960 × 640.
        assert window.size() == QSize(960, 640)
        window.show()
        window._fit_window_to_available_area(QRect(0, 0, 1600, 1000))
        window.resize(600, 400)
        qtbot.wait(30)
        assert window.size() == QSize(960, 640)
        dialog = QDialog(window)
        qtbot.addWidget(dialog)
        dialog.resize(320, 200)
        assert dialog.size() == QSize(320, 200)
    finally:
        vm.shutdown()


def test_small_work_area_then_return_to_large_screen(session, qtbot, monkeypatch):
    window, *_ = session
    window.show()
    window.move(1400, 300)
    small = QRect(100, 40, 900, 600)
    window._fit_window_to_available_area(small)
    qtbot.wait(30)
    assert window.minimumSize() == QSize(900, 600)
    assert small.contains(window.geometry())
    large = QRect(-1600, 30, 1600, 970)
    requested = []
    move = window.move
    monkeypatch.setattr(
        window, "move", lambda x, y: (requested.append(QPoint(x, y)), move(x, y))
    )
    window._fit_window_to_available_area(large)
    qtbot.wait(30)
    assert window.minimumSize() == QSize(960, 640)
    # The console must ask for a position inside the new work area.
    assert large.contains(QRect(requested[-1], window.size()))
    # macOS keeps a window with a system title bar partly on a real display,
    # so it only lands there when a display actually covers that area.
    display_exists = any(
        screen.geometry().contains(large.center())
        for screen in QGuiApplication.screens()
    )
    if display_exists or not uses_native_window_controls():
        assert large.contains(window.geometry())


def test_screen_work_area_changes_update_bounds(session, qtbot):
    class Screen(QObject):
        availableGeometryChanged = Signal(QRect)

        def availableGeometry(self):
            return QRect(0, 0, 1600, 1000)

    window, *_ = session
    window.show()
    screen = Screen()
    window._watch_window_screen(screen)
    screen.availableGeometryChanged.emit(QRect(0, 40, 900, 600))
    qtbot.wait(30)
    assert window.minimumSize() == QSize(900, 600)
    assert QRect(0, 40, 900, 600).contains(window.geometry())
    window._watch_window_screen(window.screen())
    previous_size = window.minimumSize()
    screen.availableGeometryChanged.emit(QRect(0, 0, 780, 560))
    assert window.minimumSize() == previous_size
