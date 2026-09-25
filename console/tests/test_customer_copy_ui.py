from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit, QWidget

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
    qtbot.waitUntil(lambda: window._nav_buttons['settings'].accessibleName() == {
        'zh_CN': '固件与系统',
        'en_US': 'Firmware & System',
        'ja_JP': 'ファームウェアとシステム',
    }[language])
    current = window.findChild(QLabel, 'firmwareCurrentVersion')
    target = window.findChild(QLabel, 'remoteFirmwareReleaseSummary')
    focus = window.findChild(QWidget, 'firmwareUpdateFocusCard')
    assert focus is not None
    assert current in focus.findChildren(QLabel)
    assert target in focus.findChildren(QLabel)
    assert current.text() == '0.3.0-alpha.1'
    assert window.findChild(QLabel, 'firmwareCurrentBuild').text() == '20260912.04'
    assert '20260912.05' in target.text()
    assert '测试版' not in target.text() and 'sample' not in target.text()
    assert 'git_dirty' not in target.text() and '-gabc' not in target.text()
    assert notes not in target.text() and target.textFormat() == Qt.PlainText
    release_notes = window.findChild(QLabel, 'remoteFirmwareReleaseNotes')
    assert release_notes.text() == notes and release_notes.textFormat() == Qt.PlainText
    assert not release_notes.isHidden()
    details = window.findChild(QLabel, 'remoteFirmwareTechnical')
    assert details.isHidden()
    assert '20260912.05-gabc-dirty' in details.text()
    window.findChild(QPushButton, 'remoteFirmwareDetailsToggle').click()
    assert not details.isHidden()
    install = window.findChild(QPushButton, 'installRemoteFirmware')
    assert install in focus.findChildren(QPushButton)
    assert install.isEnabled()
    assert install.property('buttonRole') == 'primary'
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
    assert not any(
        item.isVisible()
        for item in window.findChildren(QPlainTextEdit, 'promptEventLog')
    )
    qtbot.waitUntil(lambda: any(
        item.isVisible()
        for item in window.findChildren(QPushButton, 'promptEventDetailsToggle')
    ))
    toggle = next(
        item
        for item in window.findChildren(QPushButton, 'promptEventDetailsToggle')
        if item.isVisible()
    )
    toggle.click()
    qtbot.waitUntil(lambda: any(
        item.isVisible()
        for item in window.findChildren(QPlainTextEdit, 'promptEventLog')
    ))


def test_name_help_and_error_details_do_not_change_draft_or_save_state(editor):
    widget, vm = editor
    assert widget.counter.isHidden()
    assert widget.findChild(QPushButton, 'bleNameHelpToggle') is None
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
