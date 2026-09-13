from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.views.action_editor import ShortcutRecorder
from test_session_recovery import session


@pytest.mark.parametrize('host,device,display,expected', [
    ('darwin', 'windows', 'macos', '⌥⌘D'),
    ('win32', 'macos', 'windows', 'Alt+Win+D'),
])
def test_recorder_uses_host_names_and_capture_without_changing_device_platform(session, qtbot, monkeypatch, host, device, display, expected):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', host)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'platform': device}))
    window._select_physical_control('key.3')
    recorder = window.findChild(ShortcutRecorder)
    recorder.set_action({'type': 'key', 'usage': 7, 'modifiers': [226, 227]})
    assert recorder._preview.text() == expected
    assert recorder._capture._platform == display
    assert vm.model.snapshot.status['platform'] == device
    recorded = []
    recorder.shortcut_recorded.connect(recorded.append)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_K, Qt.ControlModifier | Qt.AltModifier)
    assert recorded == [{'type': 'key', 'usage': 14, 'modifiers': [226, 227] if host == 'darwin' else [224, 226]}]
    assert not vm.draft.is_dirty


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_confirmation_editor_grows_and_does_not_overflow(session, qtbot, tmp_path, language):
    window, vm, *_ = session
    window._language_manager.set_language(language)
    vm._write_transaction = ConfigTransaction(
        state=ConfigTransactionState.AWAITING_CONFIRMATION,
        message='设备验证通过，等待用户确认写入',
        candidate_digest='a' * 64, base_generation=2,
    )
    window.resize(1100, 700)
    window.show()
    window._select_physical_control('key.3')
    qtbot.wait(80)
    narrow = window.findChild(QWidget, 'mappingEditorCard').width()
    window.findChild(QScrollArea, 'overviewScroll').ensureWidgetVisible(
        window.findChild(QWidget, 'mappingEditorCard'), 0, 0
    )
    qtbot.wait(30)
    assert window.grab().save(str(tmp_path / 'confirmation-narrow.png'))
    for width in (1800, 1100, 1800):
        window.resize(width, 900)
        qtbot.wait(80)
        body = window.findChild(QScrollArea, 'mappingEditorBody')
        assert body.widget().width() <= body.viewport().width()
    assert window.findChild(QWidget, 'mappingEditorCard').width() > narrow
    path = tmp_path / 'confirmation-wide.png'
    assert window.grab().save(str(path))
    print(f'UI evidence: {path}')
    window._language_manager.set_language('zh_CN')


def test_mode_status_updates_visible_header_on_other_pages(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    window.show()
    vm.navigate('settings')
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        label = window.findChild(QLabel, 'deviceModeSummary')
        assert label is not None and label.isVisible()
        assert {'normal': '普通模式', 'codex': 'CODEX', 'claude_code': 'CC'}[mode] in label.text()
    assert '已切换至' in label.text()
    window._mode_notice_timer.timeout.emit()
    assert '当前模式' in label.text() and 'CC' in label.text()
    gateway.disconnected.emit('test disconnect')
    assert 'CC' not in window.findChild(QLabel, 'deviceModeSummary').text()
