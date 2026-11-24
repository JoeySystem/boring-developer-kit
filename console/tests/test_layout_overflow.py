import pytest
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget, QVBoxLayout, QScrollArea, QPushButton

from controller_config.style.sheets import application_stylesheet
from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.actions import ActionsPage
import controller_config.views.main_window as main_window_module
from controller_config.views.mode_page import ModeStage
from test_session_recovery import session as base_session


@pytest.fixture
def session(base_session):
    yield base_session
    base_session[0]._language_manager.set_language('zh_CN')


def test_long_shortcut_fits_narrow_editor(qtbot):
    host = QWidget()
    host.setStyleSheet(application_stylesheet())
    host.setFixedWidth(250)
    box = QVBoxLayout(host)
    box.setContentsMargins(0, 0, 0, 0)
    recorder = ShortcutRecorder({'type': 'key', 'usage': 7, 'modifiers': ['ctrl', 'shift', 'alt']}, platform='windows')
    recorder._preview.setText('Ctrl+Shift+Alt+D')
    box.addWidget(recorder)
    qtbot.addWidget(host)
    host.show()
    qtbot.wait(30)
    assert recorder.minimumSizeHint().width() <= 250
    assert not recorder._preview.geometry().intersects(recorder._capture.geometry())
    assert recorder.rect().contains(recorder._capture.geometry())


@pytest.mark.parametrize('page', ['overview', 'prompts', 'lighting', 'actions', 'settings', 'firmware', 'diagnostics'])
@pytest.mark.parametrize('width,height', [(1280, 720), (1024, 768), (780, 560)])
@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_pages_do_not_hide_horizontal_content(session, qtbot, tmp_path, page, width, height, language):
    window, vm, _gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(width, height)
    window.show()
    # The two smaller cases represent a screen whose work area is below the
    # normal minimum, not a user shrinking the window past its new limit.
    if width < 960 or height < 640:  # design decision smallest window
        window._fit_window_to_available_area(QRect(0, 0, width, height))
    vm.navigate(page)

    def overflowing():
        return [(scroll.objectName(), scroll.widget().width(), scroll.viewport().width())
                for scroll in window.findChildren(QScrollArea)
                if scroll.isVisible() and scroll.widget() is not None
                and scroll.widget().width() > scroll.viewport().width()]

    # Wait for layout instead of a fixed 150 ms (under load the firmware page was
    # measured half laid out). Real overflow still fails once the 3 s run out.
    try:
        qtbot.waitUntil(lambda: window.width() == width and not overflowing(), timeout=3000)
    except qtbot.TimeoutError:
        pass
    assert window.width() == width, (page, language, width, window.width())
    assert not overflowing(), (page, width, overflowing())
    assert window.grab().save(str(tmp_path / f'{page}-{width}.png'))


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_open_inspector_and_profile_menu_fit(session, qtbot, tmp_path, language, monkeypatch):
    window, vm, _gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(1280, 720)
    window.show()
    window._select_physical_control('key.9')
    qtbot.wait(100)
    recorder = window.findChild(ShortcutRecorder)
    recorder._preview.setText('Ctrl+Shift+Alt+D')
    qtbot.wait(30)
    body = window.findChild(QScrollArea, 'mappingEditorBody')
    assert body.widget().width() <= body.viewport().width(), [(w.objectName(), type(w).__name__, w.minimumSizeHint().width()) for w in body.widget().findChildren(QWidget) if w.isVisible() and w.minimumSizeHint().width() > 220]
    assert recorder.rect().contains(recorder._capture.geometry())
    body.ensureWidgetVisible(recorder._capture, 0, 0)
    qtbot.wait(30)
    capture_rect = recorder._capture.rect().translated(recorder._capture.mapTo(body.viewport(), QPoint(0, 0)))
    assert body.viewport().rect().contains(capture_rect)
    assert window.grab().save(str(tmp_path / 'inspector.png'))
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    card = stage.card()
    assert card is not None and card.objectName() == 'mappingEditorCard'
    host = stage.card_host.geometry()
    assert stage.rect().contains(host)
    assert card.width() <= host.width()
    assert not host.intersects(stage.bar.geometry())
    # The editor's own apply and save buttons are hidden; the bar's save takes over once typed.
    window.findChild(QWidget, 'mappingShortNameEditor').setText('fit')
    qtbot.wait(30)
    button = stage.bar.save
    assert button.isVisible() and button.width() >= button.sizeHint().width()
    assert stage.rect().contains(stage.bar.geometry())
    assert window.findChild(QPushButton, 'saveMappingDraft').isHidden()
    assert body.widget().width() <= body.viewport().width(), [(w.objectName(), type(w).__name__, w.minimumSizeHint().width()) for w in body.widget().findChildren(QWidget) if w.isVisible() and w.minimumSizeHint().width() > 220]
    # Keep the typing as a local draft (no device write), which brings the set chip back.
    window.findChild(QPushButton, 'saveMappingDraft').click()
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    pill = stage.bar.profile
    assert pill.isVisible()
    # The chip opens 全部方案 (Mode页 定稿 v1, 10); it and a screen's ··· menu must fit.
    qtbot.mouseClick(pill, Qt.MouseButton.LeftButton)
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    overview = stage.overview
    assert stage.overview_shown()
    qtbot.wait(30)
    assert stage.rect().contains(overview.geometry())
    assert overview.rect().contains(overview.bar.geometry())
    for button in (overview.import_button, overview.export_button, overview.done):
        assert button.isVisible() and button.width() >= button.sizeHint().width(), (language, button.objectName())
    more = next(card.more for card in overview.cards if card.more is not None)
    opened = []

    class RecordingMenu(QMenu):   # PySide6 ignores QMenu.exec patched on the class itself
        def exec(self, pos=None, *_args):  # noqa: A003 - Qt's name
            opened.append((self, pos))

    with monkeypatch.context() as scope:
        scope.setattr(main_window_module, 'QMenu', RecordingMenu)
        more.click()
    assert len(opened) == 1
    menu, pos = opened[0]
    assert menu.findChild(QAction, 'saveProfileAsAction') is not None
    menu.popup(pos)
    qtbot.wait(30)
    assert menu.isVisible() and menu.isWindow()
    assert menu.screen().availableGeometry().contains(menu.frameGeometry())
    menu.close()


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_playground_sections_fit_small_window(session, qtbot, language):
    window, vm, *_ = session
    window._language_manager.set_language(language)
    window.resize(780, 560)
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 780, 560))
    vm.navigate('actions')
    page = window.findChild(ActionsPage)
    for section in (
        page.MY_ACTIONS,
        page.EDIT_ACTION,
        page.RUN_HISTORY,
        page.DEVELOP_ACTIONS,
    ):
        page.show_section(section)
        qtbot.wait(50)
        assert window.width() == 780
        for scroll in page.findChildren(QScrollArea):
            if scroll.isVisible() and scroll.widget() is not None:
                assert scroll.widget().width() <= scroll.viewport().width(), (language, section, scroll.objectName())
    page._developer.show_lane(1)
    qtbot.wait(50)
    for scroll in page.findChildren(QScrollArea):
        if scroll.isVisible() and scroll.widget() is not None:
            assert scroll.widget().width() <= scroll.viewport().width(), (language, 'extensions', scroll.objectName())


def test_resizing_open_editor_preserves_input_and_reflows(session, qtbot):
    window, *_ = session
    window.show()
    window._select_physical_control('key.9')
    recorder = window.findChild(ShortcutRecorder)
    recorder._preview.setText('Ctrl+Shift+Alt+D')
    for width in (1280, 780, 1280, 1024, 1280):
        window._fit_window_to_available_area(QRect(0, 0, width, 900))
        window.resize(width, 720)
        qtbot.wait(50)
        assert window.width() == width
        assert recorder._preview.text() == 'Ctrl+Shift+Alt+D'
        for scroll in window.findChildren(QScrollArea):
            if scroll.isVisible() and scroll.widget() is not None:
                assert scroll.widget().width() <= scroll.viewport().width(), (width, scroll.objectName())
