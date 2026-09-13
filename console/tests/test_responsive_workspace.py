"""Resize the actual Qt workspace and exercise its scaled controls."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QLineEdit, QPushButton, QScrollArea, QWidget

from test_session_recovery import session


def _visible_inside(widget, viewport):
    return widget.isVisible() and viewport.rect().contains(
        QRect(widget.mapTo(viewport, QPoint()), widget.size())
    )


def test_resize_grows_device_and_keeps_actions_next_to_content(session, qtbot):
    window, *_ = session
    window.resize(1100, 700)
    window.show()
    overview = window.findChild(QScrollArea, "overviewScroll")
    qtbot.wait(300)
    assert overview.widget().width() <= overview.viewport().width()
    shell = window.findChild(QWidget, "deviceShell")
    small_side = shell.width()
    for width, height in ((1920, 1050), (1280, 800), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(150)
        assert overview.widget().width() <= overview.viewport().width()
        if height >= 1000:
            assert overview.verticalScrollBar().maximum() == 0
        assert shell.width() == shell.height()
        stage = window.findChild(QWidget, "deviceStage")
        assert stage.rect().contains(QRect(shell.mapTo(stage, QPoint()), shell.size()))
        sync = window.findChild(QWidget, "syncSummaryCard")
        inspector = window.findChild(QWidget, "selectionInspectorCard")
        assert sync.y() - inspector.geometry().bottom() <= 21
        apply = window.findChild(QPushButton, "saveConfigurationToDevice")
        overview.ensureWidgetVisible(apply, 0, 0)
        qtbot.wait(50)
        assert _visible_inside(apply, overview.viewport())
        if width == 1920:
            assert shell.width() > small_side * 1.3
            assert inspector.height() < overview.viewport().height() * .6
    assert abs(shell.width() - small_side) <= 2


def test_resize_retains_edit_and_scaled_key_hit_targets(session, qtbot):
    window, vm, *_ = session
    window.resize(1100, 700)
    window.show()
    qtbot.wait(300)
    key = next(button for button in window.findChildren(QPushButton) if button.property("controlId") == "key.3")
    qtbot.mouseClick(key, Qt.LeftButton, pos=key.rect().center())
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("缩放后保留")
    name.setCursorPosition(3)
    for width, height in ((1920, 1050), (1100, 700), (1280, 800)):
        window.resize(width, height)
        qtbot.wait(200)
        assert window.findChild(QLineEdit, "mappingShortNameEditor") is name
        assert name.text() == "缩放后保留"
        assert name.cursorPosition() == 3
        assert window._selected_control_id == "key.3"
        outer = window.findChild(QScrollArea, "overviewScroll")
        assert outer.widget().width() <= outer.viewport().width()
        if height >= 1000:
            assert outer.verticalScrollBar().maximum() == 0
        body = window.findChild(QScrollArea, "mappingEditorBody")
        assert body.widget().width() <= body.viewport().width()
        for button_name in ("applyMappingToDevice", "saveMappingDraft"):
            button = window.findChild(QPushButton, button_name)
            outer.ensureWidgetVisible(button, 0, 0)
            qtbot.wait(50)
            assert _visible_inside(button, outer.viewport())
    # Resolve the local input without issuing a device write before fixture cleanup.
    window.findChild(QPushButton, "saveMappingDraft").click()


def test_small_logical_screen_keeps_stacked_controls_reachable(session, qtbot):
    window, *_ = session
    # Mirrors the production available-screen clamp on a high-DPI small display.
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 960, 640))
    window.resize(960, 640)
    qtbot.wait(500)
    window._select_physical_control("key.3")
    qtbot.wait(200)
    overview = window.findChild(QScrollArea, "overviewScroll")
    assert overview.widget().width() <= overview.viewport().width()
    shell = window.findChild(QWidget, "deviceShell")
    assert shell.width() >= 240 and shell.width() == shell.height()
    overview.ensureWidgetVisible(shell, 0, 0)
    qtbot.wait(50)
    assert _visible_inside(shell, overview.viewport()), (shell.mapTo(overview.viewport(), QPoint()).toTuple(), shell.size().toTuple(), overview.viewport().size().toTuple(), overview.verticalScrollBar().value(), overview.verticalScrollBar().maximum())
    button = window.findChild(QPushButton, "applyMappingToDevice")
    overview.ensureWidgetVisible(button)
    qtbot.wait(100)
    assert _visible_inside(button, overview.viewport())
