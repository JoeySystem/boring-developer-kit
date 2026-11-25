"""A stationary mapping page must stop requesting layout work."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QWidget

from controller_config.views.mode_page import ModeStage
from test_session_recovery import session


class LayoutRequests(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.count = 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.LayoutRequest:
            self.count += 1
        return False


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def _watch(*widgets):
    requests = LayoutRequests(widgets[0])
    for widget in widgets:
        widget.installEventFilter(requests)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(requests)
    return requests


def test_mapping_layout_settles_after_resize_and_editor_change(session, qtbot):
    window, *_ = session
    window.show()
    stage = _stage(window)
    requests = _watch(stage)

    # Grow, shrink and return: the stage rescales the bar each time.
    for width, height in ((1100, 700), (1920, 1050), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(600)
        requests.count = 0
        qtbot.wait(400)
        assert requests.count == 0, "Stationary mapping page keeps invalidating its layout"

    # Opening the key card legitimately relayouts once, then must settle too.
    window._select_physical_control("key.9")
    stage = _stage(window)
    assert stage.card() is not None
    editor_requests = _watch(stage)
    # The fake device does not answer the editor's asynchronous reads. Let its
    # timeout/status messages finish before measuring a stationary editor.
    qtbot.wait(3500)
    editor_requests.count = 0
    qtbot.wait(400)
    assert editor_requests.count == 0, "Mapping editor keeps invalidating its layout"
