from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from controller_config.models import AppState, ScreenModel
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.views.action_editor import ShortcutRecorder
from test_session_recovery import session


@pytest.mark.parametrize('control_id,name,wheel,expected', [
    ('encoder.ccw', 'Scroll up', 1, '逆时针'),
    ('encoder.cw', 'Scroll down', -1, '顺时针'),
])
def test_encoder_direction_copy_keeps_existing_device_mapping(session, control_id, name, wheel, expected):
    import copy
    from PySide6.QtWidgets import QLineEdit
    window, vm, gateway, snapshot, _ = session
    config = copy.deepcopy(snapshot.config)
    profile = next(p for p in config['profiles'] if p['id'] == config['active_profile'])
    mapping = next(m for m in profile['mappings'] if m['control_id'] == control_id)
    mapping.update(short_name=name, action={
        'type': 'mouse', 'button': 0, 'x': 0, 'y': 0, 'wheel': wheel, 'pan': 0,
    })
    gateway.snapshot_ready.emit(replace(snapshot, config_result={
        **snapshot.config_result, 'config': config,
        'generation': snapshot.config_result['generation'] + 1,
    }))
    window._select_control(control_id)
    card = window._current_mapping_card()
    assert card.findChild(QLabel, 'actualActionValue').text() == '页面滚动'
    assert card.findChild(QLineEdit, 'mappingShortNameEditor').text() == expected
    assert not card.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
    assert not window._mapping_editor_has_uncommitted_changes()
    assert window._save_mapping_editor_draft()
    window._close_control_editor()
    assert vm.draft.config == config
    assert not vm.draft.is_dirty
    assert not any(c.name == 'SET_CONFIG' for c in gateway.commands)


@pytest.mark.parametrize('host,device,display,expected', [
    ('darwin', 'windows', 'macos', '⌥⌘D'),
    ('win32', 'macos', 'windows', 'Alt+Win+D'),
])
def test_recorder_uses_host_names_and_capture_without_changing_device_platform(session, qtbot, monkeypatch, host, device, display, expected):
    window, vm, gateway, snapshot, _ = session
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', host)
    gateway.snapshot_ready.emit(replace(snapshot, status={
        **snapshot.status,
        'platform': device,
        'operating_mode': 'normal',
    }))
    window._select_physical_control('key.8')
    recorder = window.findChild(ShortcutRecorder)
    recorder.set_action({'type': 'key', 'usage': 7, 'modifiers': [226, 227]})
    assert recorder._preview.text() == expected
    assert recorder._capture._platform == display
    assert vm.model.snapshot.status['platform'] == device
    assert window.findChild(QLabel, 'devicePlatformNotice') is None
    recorded = []
    recorder.shortcut_recorded.connect(recorded.append)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_K, Qt.ControlModifier | Qt.AltModifier)
    assert recorded == [{'type': 'key', 'usage': 14, 'modifiers': [226, 227] if host == 'darwin' else [224, 226]}]
    assert not vm.draft.is_dirty


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_confirmation_editor_stays_usable_without_overflow(session, qtbot, tmp_path, language):
    window, vm, gateway, snapshot, _ = session
    # Exercise the editable normal-mode key, not the official CODEX voice card.
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'operating_mode': 'normal'}))
    window._language_manager.set_language(language)
    vm._write_transaction = ConfigTransaction(
        state=ConfigTransactionState.AWAITING_CONFIRMATION,
        message='设备验证通过，等待用户确认写入',
        technical='candidate validation details',
        candidate_digest='a' * 64, base_generation=2,
    )
    window.resize(1100, 700)
    window.show()
    window._select_physical_control('key.8')
    qtbot.wait(80)
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
        assert window.findChild(QWidget, 'mappingEditorCard').isVisible()
    assert window.findChild(QWidget, 'configurationWriteDetailsToggle') is None
    path = tmp_path / 'confirmation-wide.png'
    assert window.grab().save(str(path))
    print(f'UI evidence: {path}')
    window._language_manager.set_language('zh_CN')


def test_mode_status_updates_visible_header_on_other_pages(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    window.show()
    vm.navigate('settings')
    expected_modes = {
        'normal': '普通模式',
        'codex': 'CODEX',
        'claude_code': 'CC',
    }
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        label = window.findChild(QLabel, 'deviceModeSummary')
        assert label is not None and label.isVisible()
        assert expected_modes[mode] in label.text()
    assert '已切换至' in label.text()
    window._mode_notice_timer.timeout.emit()
    assert '当前模式' in label.text() and 'CC' in label.text()
    gateway.disconnected.emit('test disconnect')
    assert 'CC' not in window.findChild(QLabel, 'deviceModeSummary').text()


def _visible_label_texts(root: QWidget) -> set[str]:
    return {
        label.text()
        for label in root.findChildren(QLabel)
        if label.isVisible() and label.text().strip()
    }


@pytest.mark.parametrize('language, expected', [
    ('zh_CN', '连接设备'),
    ('en_US', 'Connect Device'),
    ('ja_JP', 'デバイスを接続'),
])
def test_disconnected_view_has_one_connection_action_that_starts_scan(
    session, qtbot, language, expected
):
    window, vm, gateway, *_ = session
    window._language_manager.set_language(language)
    window.show()
    vm._set(ScreenModel(AppState.NO_DEVICE, '尚未发现 BORING 设备'))

    qtbot.waitUntil(lambda: any(
        button.isVisible()
        for button in window.findChildren(QPushButton, 'connectDeviceButton')
    ))
    connect = next(
        button
        for button in window.findChildren(QPushButton, 'connectDeviceButton')
        if button.isVisible()
    )
    assert connect.text() == expected
    assert window.findChild(QPushButton, 'relinkButton') is None

    scan_calls = gateway.scan_calls
    qtbot.mouseClick(connect, Qt.LeftButton)
    assert gateway.scan_calls == scan_calls + 1
    assert vm.model.state is AppState.SCANNING


def test_primary_pages_keep_only_customer_action_copy(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, status={
        **snapshot.status,
        'operating_mode': 'normal',
    }))
    window.show()

    vm.navigate('overview')
    qtbot.wait(30)
    inspector = window.findChild(QWidget, 'selectionInspectorCard')
    assert inspector is not None
    assert window.findChild(QWidget, 'selectionInspectorDetails') is None
    assert '点击设备上的控件开始设置。' in _visible_label_texts(inspector)
    sync = window.findChild(QWidget, 'syncSummaryCard')
    assert sync is not None
    assert not sync.findChild(QLabel, 'syncDraftState').isVisible()
    assert not sync.findChild(QLabel, 'syncChangeCount').isVisible()

    window._select_physical_control('key.8')
    recorder_message = window.findChild(QLabel, 'shortcutRecorderMessage')
    assert recorder_message is not None and not recorder_message.isVisible()
    window.findChild(QPushButton, 'shortcutRecordButton').click()
    qtbot.wait(20)
    assert not recorder_message.isHidden()

    vm.navigate('lighting')
    qtbot.wait(30)
    labels = _visible_label_texts(window)
    assert '点击按键选择颜色；黑色表示关闭。' not in labels
    assert '状态灯由 Codex 自动控制。' in labels
    assert not any('本地效果示意' in text for text in labels)
    assert not any('此处不模拟状态颜色' in text for text in labels)
    preview_status = window.findChild(QLabel, 'lightingPreviewStatus')
    assert preview_status is not None and not preview_status.isVisible()


def test_settings_and_advanced_pages_default_to_plain_language(session, qtbot):
    window, vm, *_ = session
    window.show()
    vm.navigate('settings')
    qtbot.wait(30)
    labels = _visible_label_texts(window)
    assert '在这里管理设备检查、固件维护和控制台显示语言。' not in labels
    assert window.findChild(QPushButton, 'openFirmwareSettings') is None
    assert window.findChild(QPushButton, 'openDiagnostics') is None
    assert sum(
        button.isVisible() and button.text() == '固件更新'
        for button in window.findChildren(QPushButton, 'settingsGroup')
    ) == 1
    assert {
        button.text()
        for button in window.findChildren(QPushButton, 'settingsGroup')
        if button.isVisible()
    } == {'系统', '设备', '固件更新'}
    assert window.findChild(QPushButton, 'claudeStatusDetailsToggle').text() == '更多信息'
    assert window.findChild(QPushButton, 'enableClaudeStatus').isVisible()
    assert not window.findChild(QPushButton, 'retryClaudeStatus').isVisible()
    assert not window.findChild(QPushButton, 'manageClaudeStatus').isVisible()

    window._select_settings_section('device')
    qtbot.wait(30)
    ble_editor = window.findChild(QWidget, 'bleNameEditor')
    assert ble_editor is not None
    assert not window.findChild(QLabel, 'bleNameSerial').isVisible()
    assert not window.findChild(QLabel, 'bleNameSaved').isVisible()
    assert not window.findChild(QLabel, 'bleNameActive').isVisible()
    assert not window.findChild(QLabel, 'bleNameRestart').isVisible()
    assert window.findChild(QPushButton, 'bleNameDetailsToggle').text() == '更多信息'
    advanced = window.findChild(QPushButton, 'deviceAdvancedToggle')
    reset = window.findChild(QPushButton, 'factoryResetDevice')
    diagnostics = window.findChild(QPushButton, 'openDeviceDiagnostics')
    assert advanced is not None and reset is not None
    assert diagnostics is not None and diagnostics.text() == '开始检查'
    assert ble_editor.geometry().top() < advanced.geometry().top()
    assert not reset.isVisible()
    advanced.click()
    assert reset.isVisible()

    window._select_settings_section('system')
    qtbot.wait(30)
    assert not any(
        'HarmonyOS Sans SC' in text or 'Digital Core' in text
        for text in _visible_label_texts(window)
    )

    window._select_settings_section('device')
    window.findChild(QPushButton, 'openDeviceDiagnostics').click()
    qtbot.wait(30)
    assert '设备检查' in _visible_label_texts(window)
    assert not any('所有内容都来自当前读取链路' in text for text in _visible_label_texts(window))
    assert not any('不等同于设备内部日志' in text for text in _visible_label_texts(window))
    assert window.findChild(QPushButton, 'startDiagnosticCapture').isVisible()
    assert window.findChild(QPushButton, 'stopDiagnosticCapture').isHidden()
    assert window.findChild(QWidget, 'joystickCalibrationCard').isHidden()

    window._select_settings_section('firmware')
    qtbot.wait(30)
    package_card = window.findChild(QWidget, 'firmwarePackageCard')
    transaction_card = window.findChild(QWidget, 'firmwareTransactionCard')
    advanced = window.findChild(QPushButton, 'firmwarePackageToggle')
    assert package_card is not None and not package_card.isVisible()
    assert transaction_card is not None and not transaction_card.isVisible()
    assert advanced is not None and advanced.text() == '高级选项 · 从文件安装固件'
    advanced.click()
    qtbot.wait(20)
    assert package_card.isVisible()
    assert window._content.findChild(QPushButton, 'factoryResetDevice') is None


def test_prompt_and_automation_pages_remove_redundant_engineering_copy(session, qtbot):
    window, vm, *_ = session
    window.show()

    vm.navigate('prompts')
    qtbot.wait(30)
    labels = _visible_label_texts(window)
    assert not any('点击方向编辑提示词' in text for text in labels)
    assert not any('UTF-8 字节' in text for text in labels)
    event_card = window.findChild(QWidget, 'promptEventsCard')
    assert event_card is not None and not event_card.isVisible()
    event_toggle = window.findChild(QPushButton, 'promptEventDetailsToggle')
    assert event_toggle is not None and event_toggle.text() == '操作记录'
    event_toggle.click()
    qtbot.wait(20)
    assert event_card.isVisible()

    vm.navigate('actions')
    qtbot.wait(30)
    assert 'actions' not in window._nav_buttons
    labels = _visible_label_texts(window)
    assert '组合保存文字、打开应用等电脑操作，由设备触发。' in labels
    assert window.findChild(QWidget, 'workflowWorkspace').isHidden()
    create = window.findChild(QPushButton, 'createAction')
    more = window.findChild(QPushButton, 'actionMore')
    assert create.isVisible() and create.property('buttonRole') == 'primary'
    assert more.isVisible() and more.menu() is not None
    assert any(action.text() == '导入自定义动作' for action in more.menu().actions())
    assert not window.findChildren(QPushButton, 'actionTab')
    assert window.findChild(QLabel, 'actionCatalogStatus') is None
