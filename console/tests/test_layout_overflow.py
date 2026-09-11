import pytest
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QPushButton

from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.actions import ActionsPage
from controller_config.views.main_window import APP_STYLE, V4_STYLE, _ResponsiveMappingWorkspace
from test_session_recovery import session as base_session


@pytest.fixture
def session(base_session):
    yield base_session
    base_session[0]._language_manager.set_language('zh_CN')


def test_long_shortcut_fits_narrow_editor(qtbot):
    host = QWidget()
    host.setStyleSheet(APP_STYLE + V4_STYLE)
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


def test_mapping_stacks_before_columns_collide(session, qtbot):
    window, *_ = session
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 960, 700))
    window.resize(960, 700)
    qtbot.wait(100)
    workspace = window.findChild(_ResponsiveMappingWorkspace)
    assert workspace.property('stacked') is True
    overview = window.findChild(QScrollArea, 'overviewScroll')
    assert overview.widget().width() <= overview.viewport().width()


@pytest.mark.parametrize('page', ['overview', 'prompts', 'lighting', 'actions', 'settings', 'firmware', 'diagnostics'])
@pytest.mark.parametrize('width,height', [(1280, 720), (1024, 768), (780, 560)])
@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_pages_do_not_hide_horizontal_content(session, qtbot, tmp_path, page, width, height, language):
    window, vm, _gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(width, height)
    window.show()
    # The two smaller cases represent a screen whose work area is below the
    # normal minimum, not a user shrinking the window past its new limit.
    if width < 1100 or height < 700:
        window._fit_window_to_available_area(QRect(0, 0, width, height))
    vm.navigate(page)
    qtbot.wait(150)
    assert window.width() == width, (page, language, width, window.width())
    for scroll in window.findChildren(QScrollArea):
        if scroll.isVisible() and scroll.widget() is not None:
            assert scroll.widget().width() <= scroll.viewport().width(), (page, width, scroll.objectName())
    assert window.grab().save(str(tmp_path / f'{page}-{width}.png'))


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_open_inspector_and_profile_menu_fit(session, qtbot, tmp_path, language):
    window, vm, _gateway, _snapshot, _store = session
    window._language_manager.set_language(language)
    window.resize(1280, 720)
    window.show()
    window._select_physical_control('key.7')
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
    rail = window.findChild(QWidget, 'deviceContextRail')
    cards = [rail.layout().itemAt(i).widget() for i in range(rail.layout().count()) if rail.layout().itemAt(i).widget() is not None]
    for first, second in zip(cards, cards[1:]):
        assert first.geometry().bottom() < second.geometry().top()
        assert first.height() >= first.minimumSizeHint().height()
    for name in ('applyMappingToDevice', 'saveMappingDraft'):
        button = window.findChild(QPushButton, name)
        assert button.width() >= button.sizeHint().width()
    window.findChild(QPushButton, 'shortcutManualToggle').click()
    qtbot.wait(30)
    assert body.widget().width() <= body.viewport().width(), [(w.objectName(), type(w).__name__, w.minimumSizeHint().width()) for w in body.widget().findChildren(QWidget) if w.isVisible() and w.minimumSizeHint().width() > 220]
    pill = window.findChild(QPushButton, 'profilePill')
    menu = pill.menu()
    menu.popup(pill.mapToGlobal(QPoint(0, pill.height())))
    qtbot.wait(30)
    assert menu.isVisible() and menu.isWindow()
    assert menu.screen().availableGeometry().contains(menu.frameGeometry())
    menu.close()


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_playground_sections_fit_small_window(session, qtbot, language):
    window, vm, *_ = session
    window._language_manager.set_language(language)
    window.resize(780, 560)
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 780, 560))
    vm.navigate('actions')
    page = window.findChild(ActionsPage)
    for section in range(5):
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
    window._select_physical_control('key.7')
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


def test_returning_from_compact_layout_does_not_keep_stacked_height(session, qtbot):
    window, *_ = session
    window.show()
    window.resize(1280, 800)
    qtbot.wait(100)
    workspace = window.findChild(_ResponsiveMappingWorkspace)
    initial_height = workspace.height()
    for _ in range(3):
        window._fit_window_to_available_area(QRect(0, 0, 960, 700))
        window.resize(960, 700)
        qtbot.wait(100)
        assert workspace.property('stacked')
        window._fit_window_to_available_area(QRect(0, 0, 1920, 1080))
        window.resize(1280, 800)
        qtbot.wait(100)
        assert not workspace.property('stacked')
        assert workspace.height() <= initial_height + 20


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_unselected_inspector_cards_fit_their_rail(session, qtbot, language):
    window, *_ = session
    window._language_manager.set_language(language)
    window.resize(1280, 800)
    window.show()
    qtbot.wait(100)
    rail = window.findChild(QWidget, 'mappingInspectorRail')
    for index in range(rail.layout().count()):
        card = rail.layout().itemAt(index).widget()
        if card is not None:
            assert rail.rect().contains(card.geometry()), (card.objectName(), card.geometry(), rail.rect())
