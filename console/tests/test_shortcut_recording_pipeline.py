"""A captured shortcut must retain its HID usages through confirmed device readback."""
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea

from controller_config.actions import describe_action
from controller_config.views.action_editor import ActionEditor, ShortcutRecorder
from test_session_recovery import session
from test_feedback_write_experience import complete_write
from test_responsive_workspace import _visible_inside


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
@pytest.mark.parametrize('host,device,qt_modifier,usage,preview', [
    ('darwin', 'windows_linux', Qt.ControlModifier, 227, '⌘N'),
    ('darwin', 'windows_linux', Qt.MetaModifier, 224, '⌃N'),
    ('win32', 'macos', Qt.ControlModifier, 224, 'Ctrl+N'),
    ('win32', 'macos', Qt.MetaModifier, 227, 'Win+N'),
])
def test_recorded_modifiers_survive_edit_validate_write_and_readback(session, qtbot, monkeypatch, host, device, qt_modifier, usage, preview, language):
    window, vm, gateway, snapshot, _ = session
    window._language_manager.set_language(language)
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', host)
    snapshot = replace(snapshot, status={**snapshot.status, 'platform': device, 'operating_mode': 'normal'})
    gateway.snapshot_ready.emit(snapshot)
    window._select_physical_control('key.8')
    shortcut_name = window.findChild(QLineEdit, 'mappingShortNameEditor')
    shortcut_name.setText('Next item')
    recorder = window.findChild(ShortcutRecorder)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_N, qt_modifier)
    expected = {'type': 'key', 'usage': 17, 'modifiers': [usage]}
    assert recorder._preview.text() == preview
    assert window.findChild(ActionEditor).action() == expected
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    validate = gateway.commands[-1]
    assert validate.name == 'VALIDATE_CONFIG'
    config = validate.payload['config']
    profile = next(p for p in config['profiles'] if p['id'] == config['active_profile'])
    written_mapping = next(m for m in profile['mappings'] if m['control_id'] == 'key.8')
    assert written_mapping['short_name'] == 'Next item'
    assert written_mapping['action'] == expected
    complete_write(vm, gateway, snapshot)
    written = next(c for c in gateway.commands if c.name == 'SET_CONFIG')
    assert written.payload['config'] == config
    assert vm.model.snapshot.mappings['key.8']['action'] == expected
    assert not window.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
    # Host naming must remain consistent after readback without rewriting device preferences.
    assert vm.model.snapshot.status['platform'] == device
    assert window.findChild(QLabel, 'actualActionValue').text() == describe_action(expected, platform="macos" if host == "darwin" else "windows")


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_codex_editor_does_not_claim_normal_mapping_executes_in_current_mode(session, qtbot, tmp_path, language):
    window, vm, gateway, snapshot, _ = session
    window._language_manager.set_language(language)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'operating_mode': 'codex'}))
    window._select_physical_control('key.9')
    notice = window.findChild(QLabel, 'mappingExecutionNotice')
    normal_label = '普通模式' if language == 'zh_CN' else 'Normal'
    assert notice is not None and normal_label in notice.text() and 'CODEX' in notice.text()
    if language == 'en_US':
        assert 'official functions' in notice.text()
    assert normal_label in window.findChild(QPushButton, 'applyMappingToDevice').text()
    window.resize(1100, 700)
    window.show()
    qtbot.wait(300)
    viewport = window._current_mapping_card()
    assert _visible_inside(notice, viewport)
    assert _visible_inside(window.findChild(QPushButton, 'applyMappingToDevice'), viewport)
    assert window.grab().save(str(tmp_path / f'codex-notice-{language}.png'))
    gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': 'normal'})
    assert window.findChild(QLabel, 'mappingExecutionNotice') is None
    window._language_manager.set_language('zh_CN')
