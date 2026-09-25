from dataclasses import replace

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QCheckBox, QMessageBox, QPushButton, QScrollArea

from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.screen_icon_editor import ScreenIconEditor
from controller_config.views.screen_glyph_editor import ScreenGlyphEditor
from test_session_recovery import session


def test_appearance_navigation_retains_editor_values_and_scroll(session, qtbot):
    window, vm, _, _, _ = session
    window.show()
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    editor._display_brightness.setValue(42)
    left = editor.findChild(QScrollArea, 'lightingLeftScroll')
    qtbot.wait(20)
    left.verticalScrollBar().setValue(left.verticalScrollBar().maximum())
    position = left.verticalScrollBar().value()
    vm.navigate('prompts')
    assert not editor.isVisible()
    vm.navigate('lighting')
    qtbot.wait(20)
    assert window._content.findChild(PreferencesEditor) is editor
    assert editor.values()[2]['brightness'] == 42
    assert left.verticalScrollBar().value() == position


def test_same_page_refresh_has_no_detach_hide_or_show(session):
    window, vm, _, _, _ = session
    window.show()
    vm.navigate('lighting')
    page = window._cached_preferences_page
    changes = []

    class Observer(QObject):
        def eventFilter(self, watched, event):
            if event.type() in {QEvent.ParentChange, QEvent.Hide, QEvent.Show}:
                changes.append(event.type())
            return False

    observer = Observer()
    page.installEventFilter(observer)
    vm.changed.emit(vm.model)
    assert changes == []
    assert window._cached_preferences_page is page


def test_cached_editor_handles_write_state_without_recreation(session):
    window, vm, _, _, _ = session
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    editor._display_brightness.setValue(42)
    vm._write_transaction = ConfigTransaction(state=ConfigTransactionState.WRITING)
    vm.changed.emit(vm.model)
    assert window._content.findChild(PreferencesEditor) is editor
    assert not editor._screen_card.isEnabled()
    assert not window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    vm._write_transaction = ConfigTransaction(state=ConfigTransactionState.FAILED, message='写入失败')
    vm.changed.emit(vm.model)
    assert window._content.findChild(PreferencesEditor) is editor
    assert editor._screen_card.isEnabled()
    assert window.findChild(QPushButton, 'savePreferencesToDevice').isEnabled()
    assert editor.values()[2]['brightness'] == 42


def test_cached_editor_refreshes_changed_config_and_device(session):
    window, vm, gateway, snapshot, _ = session
    vm.navigate('lighting')
    original = window._content.findChild(PreferencesEditor)
    vm.navigate('settings')
    vm.draft.config['display']['brightness'] = 42
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    assert editor is not original
    assert editor.values()[2]['brightness'] == 42
    editor._display_brightness.setValue(64)
    vm.navigate('settings')
    gateway.disconnected.emit('unplugged')
    gateway.snapshot_ready.emit(replace(snapshot, identity={**snapshot.identity, 'serial': 'CP01-001122334455'}))
    vm.navigate('lighting')
    assert window._content.findChild(PreferencesEditor).values()[2]['brightness'] == snapshot.config['display']['brightness']


def test_discarded_edits_do_not_return_from_cache(session, monkeypatch):
    window, vm, _, _, _ = session
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    original = editor.values()
    editor._display_brightness.setValue(42)
    monkeypatch.setattr('controller_config.views.main_window.confirm_local_draft', lambda *_: QMessageBox.Discard)
    window._navigate('settings')
    window._navigate('lighting')
    assert window._content.findChild(PreferencesEditor).values() == original


def test_local_save_keeps_cached_editor_and_leave_stops_preview(session, monkeypatch):
    window, vm, _, _, _ = session
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    editor._display_brightness.setValue(42)
    monkeypatch.setattr('controller_config.views.main_window.confirm_local_draft', lambda *_: QMessageBox.Save)
    vm._lighting_preview = replace(vm.lighting_preview, requested=True, active=True)
    window._navigate('settings')
    assert not vm.lighting_preview.requested
    assert not vm.lighting_preview.active
    window._navigate('lighting')
    assert window._content.findChild(PreferencesEditor) is editor
    assert editor.values()[2]['brightness'] == 42
    assert not window.findChild(QCheckBox, 'lightingPreviewEnabled').isChecked()


def test_advanced_editors_created_once_when_opened(session):
    window, vm, _, _, _ = session
    vm.navigate('lighting')
    editor = window._content.findChild(PreferencesEditor)
    assert editor.findChild(ScreenIconEditor) is None
    assert editor.findChild(ScreenGlyphEditor) is None
    toggle = editor.findChild(QPushButton, 'expandScreenIcons')
    toggle.click()
    icon = editor.findChild(ScreenIconEditor)
    assert icon._transfer is vm.screen_icon
    assert editor.findChild(ScreenGlyphEditor) is None
    glyph_toggle = editor.findChild(QPushButton, 'expandScreenGlyphs')
    glyph_toggle.click()
    glyph = editor.findChild(ScreenGlyphEditor)
    assert glyph._transfer is vm.screen_glyphs
    toggle.click()
    toggle.click()
    vm.navigate('settings')
    vm.navigate('lighting')
    assert window._content.findChild(ScreenIconEditor) is icon
    assert window._content.findChild(ScreenGlyphEditor) is glyph
