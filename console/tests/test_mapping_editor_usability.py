"""The first view of a selected key must expose recording without scrolling."""
from dataclasses import replace
from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea, QWidget

from test_session_recovery import session
from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.mode_page import ModeStage


def _visible_inside(widget, viewport):
    return widget.isVisible() and viewport.rect().contains(
        QRect(widget.mapTo(viewport, QPoint()), widget.size())
    )


def _stage(window) -> ModeStage:
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
@pytest.mark.parametrize('mode', ['normal', 'codex'])
@pytest.mark.parametrize('purpose', ['shortcut', 'native'])
def test_key_recording_visible_on_open_and_after_resize(session, qtbot, monkeypatch, tmp_path, language, mode, purpose):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', 'darwin')
    window._language_manager.set_language(language)
    config_result = deepcopy(snapshot.config_result)
    profile = next(p for p in config_result['config']['profiles'] if p['id'] == config_result['config']['active_profile'])
    profile['mappings'].append({
        'control_id': 'key.9',
        'short_name': 'Shortcut',
        'action': {'type': 'key', 'usage': 17, 'modifiers': [224]},
    })
    snapshot = replace(snapshot, config_result=config_result)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'platform': 'windows_linux', 'operating_mode': mode}))
    window.resize(1100, 700)
    window.show()
    window._select_physical_control('key.9')
    from controller_config.views.action_editor import ActionEditor
    window._current_mapping_card().findChild(ActionEditor).set_purpose(purpose)
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
            assert _visible_inside(widget, body.viewport()), (language, mode, width, height, name)
            assert _visible_inside(widget, outer.viewport())
        name_editor = window.findChild(QLineEdit, 'mappingShortNameEditor')
        if purpose == 'native':
            # AI 语音 has no 名称 row (单 design ticket 第 2 处).
            assert not name_editor.isVisible()
        else:
            assert _visible_inside(name_editor, body.viewport()), str({
                'language': language, 'size': (width, height),
                'name': name_editor.geometry(), 'body': body.viewport().geometry(),
                'name_bottom': name_editor.mapTo(body.viewport(), QPoint()).y() + name_editor.height(),
                'card': _stage(window).card().geometry(),
            })
        assert window.findChild(QPushButton, 'shortcutManualToggle') is not None
        assert not window.findChild(QWidget, 'manualActionEditor').isVisible()
        # Saving moved from the editor footer to the bottom bar (保存到设备).
        assert _visible_inside(_stage(window).bar, outer.viewport())
        assert window.findChild(QPushButton, 'saveMappingDraft').isHidden()
    assert window.findChild(QLabel, 'devicePlatformNotice') is None
    recorder = window.findChild(ShortcutRecorder)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_N, Qt.ControlModifier)
    qtbot.wait(150)
    assert _visible_inside(recorder._capture, outer.viewport())
    bar = _stage(window).bar
    assert _visible_inside(bar.save, outer.viewport())
    assert bar.save.isEnabled()
    # Only this key has an edit; nothing is waiting in the draft yet.
    assert bar.state().editing_key is True
    assert bar.state().count == 0
    assert not bar.pending.isVisible()
    typed = window._current_mapping_card().findChild(ActionEditor).action()
    gateway.disconnected.emit('unplugged')
    # Offline, the edit goes into the local draft MODE_OFFLINE_KEEP_MS after the last
    # change (Mode页 定稿 v1: no 仅保存草稿 button). Wait for that instead of racing it:
    # under load a 150 ms wait already ran past it and the bar's 这颗键有改动 was gone.
    qtbot.waitUntil(lambda: not window._mode_keep_timer.isActive()
                    and _stage(window).bar.state().right == 'offline')
    bar = _stage(window).bar
    assert vm.draft.is_dirty
    assert vm.draft.mapping(window._editing_profile(vm.draft), 'key.9')['action'] == typed
    assert window._current_mapping_card().findChild(ActionEditor).action() == typed   # still shown
    assert bar.state().editing_key is False   # nothing left that is not in the draft
    window._language_manager.set_language('zh_CN')


def test_offline_key_edit_is_kept_without_a_draft_button(session, qtbot):
    # Mode页 定稿 v1: without a device the change is kept by itself; no 仅保存草稿 button.
    window, vm, gateway, *_ = session
    window.resize(1280, 800)
    window.show()
    window._select_physical_control('key.9')
    recorder = window.findChild(ShortcutRecorder)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_N, Qt.ControlModifier)
    gateway.disconnected.emit('unplugged')
    qtbot.waitUntil(lambda: vm.draft.is_dirty, timeout=3000)
    bar = _stage(window).bar
    assert not bar.save.isVisible()
    assert bar.state().right == "offline" and "连上以后再保存" in bar.status_text.text()


def test_shortcut_name_is_immediately_available_and_survives_same_device_refresh(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    window.show()
    window._select_physical_control('key.9')
    qtbot.wait(150)
    name = window.findChild(QLineEdit, 'mappingShortNameEditor')
    assert name.isVisible()
    assert window.findChild(QPushButton, 'mappingMoreSettingsToggle') is None
    assert window.findChild(QWidget, 'mappingMoreSettings') is None
    name.setText('保留名称')
    gateway.snapshot_ready.emit(snapshot)
    qtbot.wait(150)
    assert window.findChild(QLineEdit, 'mappingShortNameEditor').text() == '保留名称'
    window.findChild(QPushButton, 'saveMappingDraft').click()
    assert vm.draft.mapping(vm.draft.config['active_profile'], 'key.9')['short_name'] == '保留名称'


@pytest.mark.parametrize('connected', [True, False])
def test_opening_a_key_card_opens_no_stray_window_and_records_on_first_click(
    session, qtbot, monkeypatch, connected,
):
    # Buttons given visibility before the layout adopted them used to appear as windows of
    # their own for a moment: 保存全部更改到设备 on a voice-capable key (firmware with
    # key_gesture), 仅保存草稿 when offline. That took activation from the main window and
    # the first click on 录制 cancelled itself.
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtGui import QWindow
    from PySide6.QtWidgets import QApplication
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', 'darwin')
    assert 'key_gesture' in snapshot.capabilities['actions']
    window.show()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window)
    if not connected:
        gateway.disconnected.emit('unplugged')
        qtbot.wait(30)

    surfaces = []

    class Spy(QObject):
        def eventFilter(self, obj, event):
            if isinstance(obj, QWindow) and event.type() == QEvent.Type.PlatformSurface:
                surfaces.append(obj.objectName())
            return False

    spy = Spy()
    QApplication.instance().installEventFilter(spy)
    try:
        window._select_physical_control('key.9')
        recorder = window._current_mapping_card().findChildren(ShortcutRecorder)[0]
        recorder._capture.click()
        qtbot.wait(80)
    finally:
        QApplication.instance().removeEventFilter(spy)
    assert surfaces == []
    assert QApplication.activeWindow() is window
    assert recorder.is_recording
    recorder._capture.cancel_recording()
