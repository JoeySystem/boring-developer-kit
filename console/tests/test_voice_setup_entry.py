"""The visible entry completes a real write and a trial for an unlisted app."""
import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QComboBox

from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transactions import ConfigTransactionState
from test_session_recovery import session
from test_codex_voice_choice import ready
from test_ai_setup import readback
from test_voice_input_ui import confirm_shortcut


def open_new(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    original = copy.deepcopy(snapshot.config)
    window.findChild(QComboBox, 'mappingPurpose').setCurrentIndex(1)
    entry = window.findChild(QPushButton, 'voiceConnectApp')
    assert entry.isEnabled()
    commands = list(gateway.commands)
    entry.click()
    dialog = window._voice_setup_dialog
    assert dialog is not None and dialog._context_ready
    assert gateway.commands == commands and vm.draft.config == original
    assert dialog.provider.currentData() == ''  # No implicit Typeless selection.
    assert not dialog._next.isEnabled()
    return window, vm, gateway, snapshot, dialog


def choose_custom(dialog):
    dialog.provider.setCurrentIndex(dialog.provider.findData('other'))
    assert not dialog._adapt_button.isEnabled()  # A new unknown app needs its own shortcut.
    # The same signal is emitted by the real recorder. Manual controls below
    # exercise the second supported input path rather than bypassing the editor.
    voice = dialog._shortcut_editors[0][1]
    voice._shortcut_recorder.shortcut_recorded.emit({'type': 'key', 'usage': 231})
    send = dialog._shortcut_editors[1][1]
    usage = send._field_widgets['usage']
    usage.setCurrentIndex(usage.findData(40))
    send._shortcut_recorder.shortcut_recorded.emit({'type': 'key', 'usage': 40, 'modifiers': [227]})
    assert dialog._adapt_button.isEnabled()


def test_unlisted_app_one_write_then_real_practice(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    choose_custom(dialog)
    original = copy.deepcopy(snapshot.active_profile['mappings'])
    dialog._adapt_button.click()
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    assert not dialog._next.isEnabled()
    readback(vm, gateway, snapshot)
    assert dialog.action['usage'] == 231
    assert dialog.action['double_modifiers'] == [227]
    assert vm.model.snapshot.active_profile['mappings'] == original
    assert dialog._context_ready and not vm.draft.is_dirty
    assert dialog._shortcut_panel.isVisible()
    assert not dialog._next.isEnabled()
    assert not dialog._toggle_supported.isChecked()
    assert dialog._toggle_supported.isEnabled()
    assert dialog._pages.currentIndex() == 0
    dialog._toggle_supported.click()
    dialog._next.click()
    assert dialog._pages.currentIndex() == 1
    dialog.practice.input.setPlainText('我的输入软件')
    qtbot.keyClick(dialog.practice.input, Qt.Key_Return)
    assert not dialog.practice.completed  # Plain Return is no longer Send.
    qtbot.keyClick(dialog.practice.input, Qt.Key_Return, Qt.ControlModifier)
    assert dialog.practice.completed
    assert dialog._next.isEnabled()
    assert dialog._target_trial.isVisible() and dialog._target_trial.isEnabled()
    dialog._target_trial.click()
    assert dialog._pages.currentIndex() == 2
    assert dialog._next.isEnabled()
    dialog._next.click()
    assert window._voice_setup_dialog is None
    assert [c.name for c in gateway.commands].count('SET_CONFIG') == 1
    assert not window._mapping_editor_has_uncommitted_changes()


def test_new_setup_failure_keeps_selection_and_retries(session, monkeypatch, tmp_path):
    _, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData('doubao'))
    confirm_shortcut(dialog, 228)
    dialog._adapt_button.click()
    gateway.command_failed.emit('VALIDATE_CONFIG', BootstrapError(BootstrapKind.READ_FAILED, '验证失败', 'test'))
    assert vm.write_transaction.state is ConfigTransactionState.FAILED
    assert dialog._adapt_error.text() and not dialog._next.isEnabled()
    assert dialog._requested_action['usage'] == 228
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action['usage'] == 228 and dialog._context_ready
    dialog.reject()


def test_cancel_before_apply_leaves_device_and_draft_untouched(session, monkeypatch, tmp_path):
    _, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    choose_custom(dialog)
    dialog.reject()
    assert vm.draft.config == snapshot.config
    assert not any(c.name in {'SET_CONFIG', 'VALIDATE_CONFIG'} for c in gateway.commands)


def test_retained_voice_draft_can_continue_after_reconnect(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window.findChild(QComboBox, 'mappingPurpose').setCurrentIndex(1)
    assert window._save_mapping_editor_draft()
    assert vm.draft.is_dirty
    entry = window.findChild(QPushButton, 'voiceConnectApp')
    assert entry.isEnabled()
    entry.click()
    dialog = window._voice_setup_dialog
    dialog.provider.setCurrentIndex(dialog.provider.findData('qianwen'))
    confirm_shortcut(dialog, 230)
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action['usage'] == 230 and not vm.draft.is_dirty
    dialog.reject()


def test_failed_custom_write_can_change_shortcut_before_retry(session, monkeypatch, tmp_path):
    _, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    choose_custom(dialog)
    dialog._adapt_button.click()
    gateway.command_failed.emit('VALIDATE_CONFIG', BootstrapError(BootstrapKind.READ_FAILED, '验证失败', 'test'))
    dialog._shortcut_editors[0][1]._shortcut_recorder.shortcut_recorded.emit({'type': 'key', 'usage': 230})
    confirm_shortcut(dialog)
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog.action['usage'] == 230 and not vm.draft.is_dirty
    dialog.reject()


def test_existing_voice_name_can_be_saved_without_changing_shortcuts(session, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QLineEdit
    from test_voice_provider_adaptation import applied_guide
    window, vm, gateway, snapshot, dialog = applied_guide(session, monkeypatch, tmp_path)
    dialog.reject()
    window._select_control('key.8')
    window.findChild(QLineEdit, 'mappingShortNameEditor').setText('我的听写')
    window.findChild(QPushButton, 'voiceConnectApp').click()
    dialog = window._voice_setup_dialog
    dialog.provider.setCurrentIndex(dialog.provider.findData('other'))
    confirm_shortcut(dialog)
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert vm.model.snapshot.mappings['key.8']['short_name'] == '我的听写'
    assert vm.model.snapshot.mappings['key.8']['action'] == snapshot.mappings['key.8']['action']
    assert not window._mapping_editor_has_uncommitted_changes()
    dialog.reject()


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_custom_setup_layout(session, monkeypatch, tmp_path, qtbot, language):
    import os
    from pathlib import Path
    from controller_config.i18n import translate_ui_text
    window, _, _, _, dialog = open_new(session, monkeypatch, tmp_path)
    window._language_manager.set_language(language)
    dialog.provider.setCurrentIndex(dialog.provider.findData('other'))
    dialog.resize(480, 620)
    qtbot.wait(50)
    assert dialog._next.mapTo(dialog, dialog._next.rect().bottomRight()).y() < dialog.height()
    assert dialog._body.horizontalScrollBar().maximum() == 0
    assert dialog._adapt_button.isVisible()
    assert dialog._adapt_button.mapTo(dialog, dialog._adapt_button.rect().bottomRight()).y() < dialog.height()
    assert dialog._shortcut_editors[0][1]._shortcut_recorder._preview.text() == '—'
    assert dialog._shortcut_editors[0][1]._shortcut_recorder._capture.text() == translate_ui_text(
        '录制电脑快捷键'
    )
    assert dialog._shortcut_hint.text() == translate_ui_text(
        '先在语音输入软件中确认开始／结束快捷键。录制不会修改语音软件；'
        '回到这里点击录制，再按电脑键盘上的同一快捷键。不要按设备语音键。'
    )
    assert dialog._custom_toggle.text() == translate_ui_text('调整双击发送（可选）…')
    output = Path(os.environ.get('VOICE_ENTRY_PREVIEW_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    dialog.grab().save(str(output / f'other-{language}.png'))
    window._language_manager.set_language('zh_CN')
    dialog.reject()


def test_other_voice_app_records_a_real_computer_keyboard_event(
        session, monkeypatch, tmp_path, qtbot):
    _, _, gateway, _, dialog = open_new(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData('other'))
    recorder = dialog._shortcut_editors[0][1]._shortcut_recorder
    commands = list(gateway.commands)

    qtbot.mouseClick(recorder._capture, Qt.LeftButton)
    qtbot.keyClick(recorder._capture, Qt.Key_F13)

    assert dialog._requested_action['usage'] == 104
    assert dialog._custom_shortcut_chosen
    assert recorder._message.text() == '已识别，尚未保存到设备。'
    assert gateway.commands == commands
    dialog.reject()
