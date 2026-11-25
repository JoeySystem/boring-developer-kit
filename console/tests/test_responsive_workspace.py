"""Resize the actual Qt workspace and exercise its scaled controls (Mode页 定稿 v1)."""
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QLineEdit, QPushButton, QScrollArea

from controller_config.views.mode_page import ModeStage
from test_session_recovery import session


def _visible_inside(widget, viewport):
    return widget.isVisible() and viewport.rect().contains(
        QRect(widget.mapTo(viewport, QPoint()), widget.size())
    )


def _stage(window) -> ModeStage:
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def test_resize_grows_device_and_keeps_actions_next_to_content(session, qtbot):
    window, *_ = session
    window.resize(1100, 700)
    window.show()
    overview = window.findChild(QScrollArea, "overviewScroll")
    qtbot.wait(300)
    qtbot.waitUntil(lambda: overview.verticalScrollBar().maximum() == 0)
    stage = _stage(window)
    small_side = stage.device.width()
    for width, height in ((1920, 1050), (1280, 800), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(150)
        qtbot.waitUntil(lambda: overview.verticalScrollBar().maximum() == 0)
        stage = _stage(window)
        device = stage.device
        assert device.width() == device.height()
        assert stage.rect().contains(QRect(device.mapTo(stage, QPoint()), device.size()))
        # The bar sits right under the device, inside the page.
        assert _visible_inside(stage.bar, overview.viewport())
        assert 0 <= stage.bar.y() - device.geometry().bottom() <= 120 * stage.scale()
        assert _visible_inside(stage.bar.profile, overview.viewport())
        if width == 1920:
            assert device.width() > small_side * 1.3
    qtbot.waitUntil(lambda: abs(_stage(window).device.width() - small_side) <= 2, timeout=1000)


def test_resize_retains_edit_and_scaled_key_hit_targets(session, qtbot):
    window, vm, *_ = session
    window.resize(1100, 700)
    window.show()
    qtbot.wait(300)
    device = _stage(window).device
    qtbot.mouseClick(device, Qt.LeftButton, pos=device.key_rect("key.9").center().toPoint())
    assert window._selected_control_id == "key.9"
    name = window.findChild(QLineEdit, "mappingShortNameEditor")
    name.setText("缩放后保留")
    name.setCursorPosition(3)
    for width, height in ((1920, 1050), (1100, 700), (1280, 800)):
        window.resize(width, height)
        qtbot.wait(200)
        assert window.findChild(QLineEdit, "mappingShortNameEditor") is name
        assert name.text() == "缩放后保留"
        assert name.cursorPosition() == 3
        assert window._selected_control_id == "key.9"
        outer = window.findChild(QScrollArea, "overviewScroll")
        assert outer.verticalScrollBar().maximum() == 0
        body = window.findChild(QScrollArea, "mappingEditorBody")
        qtbot.waitUntil(
            lambda: body.widget().width() <= body.viewport().width(), timeout=500
        )
        stage = _stage(window)
        assert _visible_inside(stage.card(), outer.viewport())
        assert _visible_inside(stage.bar.save, outer.viewport())
        assert not window.findChild(QPushButton, "saveMappingDraft").isVisible()
        # The scaled key is still where the device draws it.
        device = stage.device
        assert device.control_at(device.key_rect("key.9").center()) == "key.9"
    # Resolve the local input without issuing a device write before fixture cleanup.
    window.findChild(QPushButton, "saveMappingDraft").click()


def test_small_logical_screen_keeps_stacked_controls_reachable(session, qtbot):
    window, *_ = session
    # Mirrors the production available-screen clamp on a high-DPI small display.
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 960, 640))
    window.resize(960, 640)
    qtbot.wait(500)
    window._select_physical_control("key.9")
    qtbot.wait(200)
    overview = window.findChild(QScrollArea, "overviewScroll")
    assert overview.widget().width() <= overview.viewport().width()
    stage = _stage(window)
    device = stage.device
    assert device.width() >= 240 and device.width() == device.height()
    assert _visible_inside(device, overview.viewport())
    assert _visible_inside(stage.card(), overview.viewport())
    assert _visible_inside(stage.bar, overview.viewport())

    window.resize(1280, 800)
    qtbot.wait(300)
    assert overview.verticalScrollBar().maximum() == 0
    stage = _stage(window)
    assert _visible_inside(stage.device, overview.viewport())
    assert _visible_inside(stage.card(), overview.viewport())
