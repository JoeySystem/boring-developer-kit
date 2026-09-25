"""The first view of a selected key must expose recording without scrolling."""
from dataclasses import replace
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

from test_session_recovery import session
from test_responsive_workspace import _visible_inside
from controller_config.views.action_editor import ShortcutRecorder


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_key_recording_visible_on_open_and_after_resize(session, qtbot, monkeypatch, tmp_path, language):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', 'darwin')
    window._language_manager.set_language(language)
    config_result = deepcopy(snapshot.config_result)
    profile = next(p for p in config_result['config']['profiles'] if p['id'] == config_result['config']['active_profile'])
    profile['mappings'].append({
        'control_id': 'key.8',
        'short_name': 'Shortcut',
        'action': {'type': 'key', 'usage': 17, 'modifiers': [224]},
    })
    snapshot = replace(snapshot, config_result=config_result)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'platform': 'windows_linux', 'operating_mode': 'normal'}))
    window.resize(1100, 700)
    window.show()
    window._select_physical_control('key.8')
    for width, height in ((1100, 700), (1920, 1050), (1280, 800), (1100, 700)):
        window.resize(width, height)
        qtbot.wait(150)
        body = window.findChild(QScrollArea, 'mappingEditorBody')
        outer = window.findChild(QScrollArea, 'overviewScroll')
        assert body.verticalScrollBar().value() == 0
        actual = window.findChild(QLabel, 'actualActionValue')
        assert actual.width() >= actual.fontMetrics().horizontalAdvance(actual.text())
        assert actual.height() >= actual.fontMetrics().height(), actual.geometry()
        assert window.grab().save(str(tmp_path / f'editor-{width}-{height}.png'))
        for name in ('shortcutRecorderPreview', 'shortcutRecordButton'):
            widget = window.findChild(QLabel if name == 'shortcutRecorderPreview' else QPushButton, name)
            assert _visible_inside(widget, body.viewport()), (language, width, height, name)
            assert _visible_inside(widget, outer.viewport())
        assert window.findChild(QPushButton, 'shortcutManualToggle') is None
        assert not window.findChild(QWidget, 'manualActionEditor').isVisible()
        assert _visible_inside(
            window.findChild(QPushButton, 'applyMappingToDevice'), outer.viewport()
        )
        assert window.findChild(QPushButton, 'saveMappingDraft').isHidden()
    assert window.findChild(QLabel, 'devicePlatformNotice') is None
    recorder = window.findChild(ShortcutRecorder)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_N, Qt.ControlModifier)
    qtbot.wait(150)
    assert _visible_inside(recorder._capture, outer.viewport())
    assert _visible_inside(window.findChild(QPushButton, 'applyMappingToDevice'), outer.viewport())
    sync = window.findChild(QWidget, 'syncSummaryCard')
    pending = sync.findChild(QLabel, 'syncDraftState')
    assert sync.isVisible()
    assert not pending.isVisible()
    assert sync.findChild(QPushButton, 'ghostOnDark') is None
    gateway.disconnected.emit('unplugged')
    local_save = window.findChild(QPushButton, 'saveMappingDraft')
    assert _visible_inside(local_save, outer.viewport())
    local_save.click()
    qtbot.wait(30)
    sync = window.findChild(QWidget, 'syncSummaryCard')
    pending = sync.findChild(QLabel, 'syncDraftState')
    assert sync.isVisible()
    assert _visible_inside(pending, sync)
    window._language_manager.set_language('zh_CN')


def test_shortcut_name_is_immediately_available_and_survives_same_device_refresh(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, status={
        **snapshot.status,
        'operating_mode': 'normal',
    }))
    window.show()
    window._select_physical_control('key.8')
    qtbot.wait(150)
    name = window.findChild(QLineEdit, 'mappingShortNameEditor')
    assert name.isVisible()
    assert window.findChild(QPushButton, 'mappingMoreSettingsToggle') is None
    assert window.findChild(QWidget, 'mappingMoreSettings') is None
    name.setText('保留名称')
    gateway.snapshot_ready.emit(replace(snapshot, status={
        **snapshot.status,
        'operating_mode': 'normal',
    }))
    qtbot.wait(150)
    assert window.findChild(QLineEdit, 'mappingShortNameEditor').text() == '保留名称'
    window.findChild(QPushButton, 'saveMappingDraft').click()
    assert vm.draft.mapping(vm.draft.config['active_profile'], 'key.8')['short_name'] == '保留名称'
