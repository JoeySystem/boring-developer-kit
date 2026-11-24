"""The visible entry completes a real write and a trial for an unlisted app."""
import copy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QComboBox

from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transactions import ConfigTransactionState
from controller_config.views.action_editor import ActionEditor
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
    assert not dialog._adapt_button.isEnabled()
    confirm_shortcut(dialog)
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
    assert not dialog._shortcut_panel.isVisible()
    assert not dialog._next.isEnabled()
    assert dialog._toggle_supported.isChecked()
    assert dialog._pages.currentIndex() == 1
    dialog.practice.input.setPlainText('我的输入软件')
    qtbot.keyClick(dialog.practice.input, Qt.Key_Return)
    assert not dialog.practice.completed  # Plain Return is no longer Send.
    qtbot.keyClick(dialog.practice.input, Qt.Key_Return, Qt.ControlModifier)
    assert dialog.practice.completed
    assert not dialog._next.isEnabled()  # Hardware use still needs confirmation.
    dialog._physical_confirmed.setChecked(True)
    dialog._next.click()
    assert dialog._pages.currentIndex() == 2
    dialog._target_confirmed.setChecked(True)
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


def test_applied_doubao_action_reopens_with_confirmed_provider_without_marking_complete(
    session, monkeypatch, tmp_path,
):
    from controller_config.voice_setup import voice_trial

    window, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData('doubao'))
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    action = copy.deepcopy(dialog.action)
    provider_key = dialog._provider_key
    control_id = dialog._control_id
    mode = dialog._mode
    dialog.reject()

    assert not window._onboarding_settings.contains(provider_key)
    assert voice_trial(window._onboarding_settings, vm.draft.serial,
                       vm.model.snapshot.active_profile_id, control_id,
                       action, mode=mode) is None
    window._open_voice_setup(control_id, mode=mode)
    reopened = window._voice_setup_dialog
    assert reopened.provider.currentData() == 'doubao'
    assert reopened._pages.currentIndex() == 0
    assert reopened._next.isEnabled()
    reopened.reject()


def test_cancel_before_apply_leaves_device_and_draft_untouched(session, monkeypatch, tmp_path):
    _, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    choose_custom(dialog)
    dialog.reject()
    assert vm.draft.config == snapshot.config
    assert not any(c.name in {'SET_CONFIG', 'VALIDATE_CONFIG'} for c in gateway.commands)


def test_macos_doubao_cancel_does_not_persist_provider(session, monkeypatch, tmp_path):
    window, _, _, _, dialog = open_new(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData('qianwen'))
    assert window._onboarding_settings.value(dialog._provider_key) == 'qianwen'
    dialog.provider.setCurrentIndex(dialog.provider.findData('doubao'))
    assert not window._onboarding_settings.contains(dialog._provider_key)
    dialog.reject()
    assert not window._onboarding_settings.contains(dialog._provider_key)


def test_macos_doubao_commits_provider_and_trial_only_after_target_success(
    session, monkeypatch, tmp_path, qtbot,
):
    from controller_config.voice_setup import voice_trial

    window, vm, gateway, snapshot, dialog = open_new(session, monkeypatch, tmp_path)
    dialog.provider.setCurrentIndex(dialog.provider.findData('doubao'))
    assert not window._onboarding_settings.contains(dialog._provider_key)
    assert dialog._adapt_button.isEnabled(), (
        dialog._shortcut_problem(), dialog._adaptation_needed(), dialog._device_available(),
    )
    dialog._adapt_button.click()
    readback(vm, gateway, snapshot)
    assert dialog._pages.currentIndex() == 0 and dialog._next.isEnabled()
    assert not window._onboarding_settings.contains(dialog._provider_key)

    dialog._next.click()
    dialog.practice.input.setPlainText('豆包目标验证')
    qtbot.keyClick(dialog.practice.input, Qt.Key_Return)
    dialog._next.click()
    assert dialog._pages.currentIndex() == 2
    assert voice_trial(window._onboarding_settings, vm.draft.serial,
                       vm.model.snapshot.active_profile_id, dialog._control_id,
                       dialog.action, mode=dialog._mode) is None
    assert dialog._next.isEnabled()
    from controller_config.views.main_window import _canonical_action
    from controller_config.voice_setup import voice_mapping
    current = vm.model.snapshot
    current_action = voice_mapping(current, dialog._control_id, dialog._mode)['action']
    assert _canonical_action(current_action) == _canonical_action(dialog.action)
    assert current.identity.get('serial') == dialog._serial
    assert current.active_profile_id == dialog._profile_id
    assert current.status.get('operating_mode', 'normal') == dialog._mode
    from controller_config.models import AppState
    assert vm.model.state is AppState.READY
    assert not vm.write_transaction.blocks_editing
    assert not vm.firmware_update.blocks_editing
    assert not vm.calibration.blocks_editing
    dialog._next.click()
    assert dialog.result() == QDialog.Accepted
    assert window._voice_setup_dialog is None
    assert window._onboarding_settings.value(dialog._provider_key) == 'doubao'
    record = voice_trial(window._onboarding_settings, vm.draft.serial,
                         vm.model.snapshot.active_profile_id, dialog._control_id,
                         dialog.action, mode=dialog._mode)
    assert record and record['target'] == dialog.target_key

    editor = window.findChild(ActionEditor)
    editor._send_recorder.shortcut_recorded.emit(
        {'type': 'key', 'usage': 41, 'modifiers': []}
    )
    pending_action = copy.deepcopy(editor.action())
    window.findChild(QPushButton, 'voiceConnectApp').click()
    reverify = window._voice_setup_dialog
    assert reverify._history_panel.isVisible()
    reverify._reverify_button.click()
    reverify.practice.input.setPlainText('保留未应用修改')
    qtbot.keyClick(reverify.practice.input, Qt.Key_Return)
    reverify._next.click()
    reverify._next.click()
    assert window._voice_setup_dialog is None
    assert window.findChild(ActionEditor).action() == pending_action
    assert window.findChild(QLabel, 'voiceSetupStatus').text() == (
        '当前修改尚未应用；上次验证仍对应设备中的设置。'
    )


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
    assert dialog._custom_toggle.text() == translate_ui_text('调整双击发送')
    output = Path(os.environ.get('VOICE_ENTRY_PREVIEW_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    dialog.grab().save(str(output / f'other-{language}.png'))
    window._language_manager.set_language('zh_CN')
    dialog.reject()
