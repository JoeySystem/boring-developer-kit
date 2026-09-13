from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit

from controller_config.firmware_release import RemoteFirmwareRelease, RemoteFirmwareState
from controller_config.views.claude_status_settings import ClaudeStatusSettings
from test_session_recovery import session
from test_ble_name_ui import editor


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_release_summary_keeps_builds_distinct_and_details_optional(session, qtbot, language):
    window, vm, gateway, snapshot, _ = session
    window._language_manager.set_language(language)
    gateway.snapshot_ready.emit(replace(snapshot, versions={**snapshot.versions,
        'firmware': '0.3.0-alpha.1', 'build_id': '20260912.04-gabc-dirty'}))
    notes = '<b>Customer notes</b> / 用户内容'
    release = RemoteFirmwareRelease(dict(version='0.3.0-alpha.1',
        build_id='20260912.05-gabc-dirty', size=1024, channel='sample',
        release_notes=notes), 'https://example.test/manifest.json', 'https://example.test/fw.bin')
    vm._set_remote_firmware(replace(vm.remote_firmware,
        state=RemoteFirmwareState.AVAILABLE, release=release))
    vm.navigate('firmware')
    window.show()
    qtbot.waitUntil(lambda: window._relink_button.text() == {'zh_CN': '重新连接', 'en_US': 'Reconnect', 'ja_JP': '再接続'}[language])
    current = window.findChild(QLabel, 'firmwareCurrentVersion')
    target = window.findChild(QLabel, 'remoteFirmwareReleaseSummary')
    assert '20260912.04' in current.text()
    assert '20260912.05' in target.text()
    assert 'git_dirty' not in target.text() and '-gabc' not in target.text()
    assert notes in target.text() and target.textFormat() == Qt.PlainText
    details = window.findChild(QLabel, 'remoteFirmwareTechnical')
    assert details.isHidden()
    assert '20260912.05-gabc-dirty' in details.text()
    window.findChild(QPushButton, 'remoteFirmwareDetailsToggle').click()
    assert not details.isHidden()
    assert window.findChild(QPushButton, 'downloadRemoteFirmware').isEnabled()
    assert vm.remote_firmware.state is RemoteFirmwareState.AVAILABLE
    assert not vm.firmware_update.is_busy
    vm.navigate('overview')
    window._language_manager.set_language('zh_CN')


def test_diagnostics_and_prompt_logs_expand_without_hiding_primary_actions(session, qtbot):
    window, vm, _, _, _ = session
    window.show()
    vm.navigate('diagnostics')
    log = window.findChild(QPlainTextEdit, 'diagnosticEventLog')
    assert not log.isVisible()
    start = window.findChild(QPushButton, 'startDiagnosticCapture')
    qtbot.waitUntil(start.isVisible)
    window.findChild(QPushButton, 'diagnosticDetailsToggle').click()
    assert log.isVisible() and start.isVisible()
    vm.navigate('prompts')
    log = window.findChild(QPlainTextEdit, 'promptEventLog')
    assert log.isHidden()
    window.findChild(QPushButton, 'promptEventDetailsToggle').click()
    assert not log.isHidden()


def test_name_help_and_error_details_do_not_change_draft_or_save_state(editor):
    widget, vm = editor
    assert widget.counter.isHidden()
    widget.findChild(QPushButton, 'bleNameHelpToggle').click()
    assert not widget.counter.isHidden()
    vm.ble_name.technical = 'GET_BLE_NAME timeout'
    vm.ble_name.message = '名称保存失败，输入已保留'
    vm.ble_name.changed.emit()
    assert widget.message.isVisible() and widget.technical.isHidden()
    widget.findChild(QPushButton, 'bleNameDetailsToggle').click()
    assert widget.technical.isVisible()
    vm.ble_name.changed.emit()
    assert widget.technical.isVisible()
    assert widget.name_input.text() == 'Boring Mist'
    assert not widget.save_button.isEnabled()
    assert not vm.ble_name.calls


def test_claude_problem_remains_visible_while_paths_are_collapsed(qtbot, qapp):
    previous = qapp.property('boringUiLanguage')
    qapp.setProperty('boringUiLanguage', 'zh_CN')
    class Bridge(QObject):
        changed = Signal()
        enabled = False
        message = '未启用 Claude Code 状态联动'
        inspection = SimpleNamespace(executable='/example/private/claude', version='2.1.63',
                                     note='Hook technical note', problem='无法读取 Claude Code 版本。', installed=False)
        registry = SimpleNamespace(sessions=(), overflow=())
        def retry(self): pass
    widget = ClaudeStatusSettings(Bridge())
    qtbot.addWidget(widget)
    widget.show()
    assert widget.status.isVisible()
    assert '无法读取' in widget.status.text()
    assert '/example/private' not in widget.status.text()
    assert not widget.detail.isVisible()
    widget.findChild(QPushButton, 'claudeStatusDetailsToggle').click()
    assert widget.detail.isVisible()
    assert '/example/private/claude' in widget.detail.text()
    qapp.setProperty('boringUiLanguage', previous)
