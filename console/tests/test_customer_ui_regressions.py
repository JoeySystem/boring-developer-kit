from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QScrollArea, QWidget

from controller_config.models import AppState, ScreenModel
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.mode_page import ModeStage
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
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'platform': device}))
    window._select_physical_control('key.9')
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
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    for width in (1800, 1100, 1800):
        window.resize(width, 900)
        qtbot.wait(80)
        # Resizes this close together count as a drag of the window edge: the card's sizes
        # wait until it stops (design decision). What must not overflow is the card once settled.
        qtbot.waitUntil(lambda: not stage.resizing(), timeout=2000)
        qtbot.wait(30)
        body = window.findChild(QScrollArea, 'mappingEditorBody')
        assert body.widget().width() <= body.viewport().width()
        assert window.findChild(QWidget, 'mappingEditorCard').isVisible()
    assert window.findChild(QWidget, 'configurationWriteDetailsToggle') is None
    path = tmp_path / 'confirmation-wide.png'
    assert window.grab().save(str(path))
    print(f'UI evidence: {path}')
    window._language_manager.set_language('zh_CN')


def test_header_shows_device_status_not_mode(session, qtbot):
    """顶条 v1 定稿: the “当前模式” line and BORING MIST are gone; one status capsule stays."""
    window, vm, gateway, snapshot, _ = session
    window.show()
    vm.navigate('settings')
    for mode in ('normal', 'codex', 'claude_code'):
        gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': mode})
        assert window.findChild(QLabel, 'deviceModeSummary') is None
        assert window._device_status.isVisible()
        assert window._device_status.kind() in {'verified', 'dev'}
    gateway.disconnected.emit('test disconnect')
    assert window._device_status.kind() == 'disconnected'
    assert window._device_auth_summary.text() == '已断开'


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
    window, vm, *_ = session
    window.show()

    vm.navigate('overview')
    qtbot.wait(30)
    # The "你想设置什么？" card is gone; its shortcut entry lives on the bottom bar.
    assert window.findChild(QWidget, 'selectionInspectorCard') is None
    assert window.findChild(QWidget, 'selectionInspectorDetails') is None
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    assert stage.bar.bind.text() == '绑定软件快捷键'
    # Nothing waiting: the bar says one plain thing (here: the device is in Codex), no count.
    assert stage.bar.state().right == 'codex'
    assert stage.bar.status_text.text() == '双击设备上的旋钮，切回普通模式'
    assert not stage.bar.pending.isVisible() and not stage.bar.save.isVisible()

    window._select_physical_control('key.9')
    recorder_message = window.findChild(QLabel, 'shortcutRecorderMessage')
    assert recorder_message is not None and not recorder_message.isVisible()
    window.findChild(QPushButton, 'shortcutRecordButton').click()
    qtbot.wait(20)
    assert not recorder_message.isHidden()

    vm.navigate('lighting')
    qtbot.wait(30)
    labels = _visible_label_texts(window)
    assert '点击按键选择颜色；黑色表示关闭。' not in labels
    assert '不带键号的 6 颗是状态灯，由 Codex 自动控制。' in labels
    assert not any('本地效果示意' in text for text in labels)
    assert not any('此处不模拟状态颜色' in text for text in labels)
    preview_status = window.findChild(QLabel, 'lightingPreviewStatus')
    assert preview_status is not None and not preview_status.isVisible()


def _settings_page_now(window) -> QWidget:
    """主窗口当前显示的那一页（换页后旧页还留在内存里，不算）。"""
    layout = window._content_layout
    return layout.itemAt(layout.count() - 1).widget()


def test_settings_and_advanced_pages_default_to_plain_language(session, qtbot, monkeypatch):
    from controller_config.views import settings_wall

    window, vm, *_ = session
    window.show()
    vm.navigate('settings')
    qtbot.wait(30)
    labels = _visible_label_texts(window)
    assert '在这里管理设备检查、固件维护和控制台显示语言。' not in labels
    assert window.findChild(QPushButton, 'openFirmwareSettings') is None
    assert window.findChild(QPushButton, 'openDiagnostics') is None
    page = _settings_page_now(window)
    assert [
        button.text() for button in page.findChildren(QPushButton, 'settingsGroup') if button.isVisible()
    ] == ['设备与连接', 'AI 状态灯', '灯光与反馈', '通用', '更新']

    # AI 状态灯：墙上只有开关和一句状态；排障、管理这些动作收在"了解更多"里，不直接摆出来。
    window._select_settings_section('ai')
    qtbot.wait(30)
    page = _settings_page_now(window)
    assert page.findChild(QWidget, 'claudeStatusEnabled').isVisible()
    assert page.findChild(QPushButton, 'claudeStatusMore').text() == '了解更多 ›'
    assert not page.findChild(QPushButton, 'claudeStatusFix').isVisible()
    for name in ('enableClaudeStatus', 'retryClaudeStatus', 'manageClaudeStatus', 'claudeStatusDetailsToggle'):
        assert page.findChild(QPushButton, name) is None

    # 设备与连接：蓝牙名称、恢复出厂设置各是一块，入口直接看得见。
    window._select_settings_section('device')
    qtbot.wait(30)
    page = _settings_page_now(window)
    rename = page.findChild(QPushButton, 'openBleNameEditor')
    reset = page.findChild(QPushButton, 'factoryResetDevice')
    assert rename is not None and rename.isVisible() and rename.text() == '改名'
    assert reset is not None and reset.isVisible() and reset.text() == '恢复出厂…'
    assert page.findChild(QPushButton, 'deviceDetailsToggle').text() == '更多信息'
    assert page.findChild(QPushButton, 'deviceAdvancedToggle') is None  # 旧的折叠开关已经没有了
    assert page.findChild(QWidget, 'bleNameEditor') is None  # 编辑器不摊在页面上，点"改名"才弹出
    assert page.findChild(QPushButton, 'openDeviceDiagnostics') is None
    assert window.findChild(QWidget, 'diagnosticsPage') is None
    assert '设备检查' not in _visible_label_texts(window)
    top = lambda widget: widget.mapTo(page, widget.rect().topLeft()).y()
    assert top(rename) < top(reset)

    # 点"改名"：弹出的还是原来的编辑器，技术信息默认收着。
    seen = {}

    def fake_exec(dialog):
        dialog.show()
        qtbot.wait(30)
        editor = dialog.findChild(QWidget, 'bleNameEditor')
        seen['editor'] = editor is not None
        seen['hidden'] = [
            dialog.findChild(QLabel, name).isVisible()
            for name in ('bleNameSerial', 'bleNameSaved', 'bleNameActive', 'bleNameRestart')
        ]
        seen['toggle'] = dialog.findChild(QPushButton, 'bleNameDetailsToggle').text()
        dialog.hide()
        return 0

    monkeypatch.setattr(settings_wall.CardDialog, 'exec', fake_exec)
    rename.click()
    assert seen == {'editor': True, 'hidden': [False] * 4, 'toggle': '更多信息'}

    window._select_settings_section('system')
    qtbot.wait(30)
    assert not any(
        'HarmonyOS Sans SC' in text or 'Digital Core' in text
        for text in _visible_label_texts(window)
    )

    window._select_settings_section('firmware')
    qtbot.wait(30)
    package_card = window.findChild(QWidget, 'firmwarePackageCard')
    transaction_card = window.findChild(QWidget, 'firmwareTransactionCard')
    advanced = window.findChild(QPushButton, 'firmwarePackageToggle')
    assert package_card is not None and not package_card.isVisible()
    assert transaction_card is not None and not transaction_card.isVisible()
    assert advanced is not None and advanced.text() == '▸ 高级选项 · 自定义固件与详情'
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
    # 操作记录 waits in 诊断信息 (定稿 v1): not on the page, one click away.
    log = window.findChild(QWidget, 'promptDiagnosticsLog')
    assert log is not None and not log.isVisible()
    window.findChild(QPushButton, 'promptStatusDiagnostics').click()
    qtbot.wait(20)
    assert log.isVisible()
    window.findChild(QDialog, 'promptDiagnosticsDialog').reject()

    vm.navigate('actions')
    qtbot.wait(30)
    assert 'actions' not in window._nav_buttons
    labels = _visible_label_texts(window)
    assert '组合保存文字、打开应用等电脑操作，由设备触发。' in labels
    assert window.findChild(QWidget, 'workflowWorkspace').isHidden()
    create = window.findChild(QPushButton, 'createAction')
    tools = window.findChild(QPushButton, 'openActionTools')
    more = window.findChild(QPushButton, 'actionMore')
    assert create.isHidden()
    assert tools.isHidden()
    assert more.isVisible() and more.menu() is not None
    assert any(action.text() == '导入自定义动作' for action in more.menu().actions())
    assert not window.findChildren(QPushButton, 'actionTab')
    assert window.findChild(QLabel, 'actionCatalogStatus') is None
