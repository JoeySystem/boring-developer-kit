from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QLineEdit, QPushButton

from controller_config.desktop_update import UpdateStatus
from controller_config.drafts import LocalDraft
from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.protocol.device_auth import DeviceTrustState
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.update_recovery import UpdateRecoveryStore
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.action_editor import ActionEditor
from controller_config.views.desktop_update import DesktopUpdateUi
from controller_config.views.macro_editor import MacroEditor
from controller_config.views.main_window import MainWindow
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.prompt_library_editor import PromptLibraryEditor
from test_session_recovery import session
from test_write_transaction import FakeWriteGateway
from test_firmware_release import trust_test_signing_key


class FakeUpdater(QObject):
    changed = Signal(object)

    def __init__(self, prepare):
        super().__init__()
        self.status = UpdateStatus(state="idle")
        self.calls = []
        self.prepare = prepare

    def publish(self, state, **values):
        self.status = UpdateStatus(state=state, **values)
        self.changed.emit(self.status)

    def check(self):
        self.calls.append("check")
        self.publish("current")

    def download(self):
        self.calls.append("download")
        self.publish("downloading", version="0.2.0", received=25, total=100)

    def cancel(self):
        if self.status.state not in {'checking', 'downloading'}:
            return
        self.calls.append("cancel")
        self.publish("available", version="0.2.0")

    def install(self):
        self.calls.append("install")
        if self.prepare():
            self.publish("installing", version="0.2.0")


@pytest.fixture
def update_session(session, tmp_path, monkeypatch):
    window, vm, gateway, snapshot, prompt_store = session
    capabilities = deepcopy(snapshot.capabilities)
    # These updater recovery cases model older firmware, not name operations.
    # The fake gateway does not answer automatic BLE_NAME_GET requests.
    capabilities.setdefault("features", {})["ble_name"] = False
    snapshot = replace(snapshot, capabilities=capabilities,
                       trust=replace(snapshot.trust, state=DeviceTrustState.AUTHENTICATED))
    gateway.snapshot_ready.emit(snapshot)
    # Automatic artwork reads belong to the artwork flow, not update recovery.
    monkeypatch.setattr(vm, "refresh_screen_icon", lambda: None)
    monkeypatch.setattr(vm, "refresh_screen_glyphs", lambda *args: None)
    store = UpdateRecoveryStore(tmp_path / "recovery.json")
    ui = DesktopUpdateUi(window, store=store)
    window._desktop_update_ui = ui
    updater = FakeUpdater(ui.prepare_restart)
    ui.attach(updater)
    shutdown = []
    real_shutdown = vm.shutdown
    monkeypatch.setattr(vm, "shutdown", lambda: shutdown.append(True))
    yield window, vm, gateway, snapshot, prompt_store, ui, updater, shutdown
    ui.restart_prepared = True
    window._screen_icon_drafts.clear()
    window._screen_glyph_drafts.clear()
    real_shutdown()


def _restart_destination(source, contract, qtbot, monkeypatch, snapshot=None):
    old_window, _, _, original, prompt_store, old_ui, _, _ = source
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract, prompt_library_store=prompt_store)
    monkeypatch.setattr(vm, "refresh_screen_icon", lambda: None)
    monkeypatch.setattr(vm, "refresh_screen_glyphs", lambda *args: None)
    gateway.snapshot_ready.emit(snapshot or original)
    window = MainWindow(vm, language_manager=old_window._language_manager)
    ui = DesktopUpdateUi(window, store=old_ui.store)
    window._desktop_update_ui = ui

    def cleanup(_):
        ui.restart_prepared = True
        vm.shutdown()

    qtbot.addWidget(window, before_close_func=cleanup)
    gateway.commands.clear()
    return window, vm, gateway, ui


def test_footer_download_progress_cancel_and_restart(update_session):
    window, vm, _, _, _, ui, updater, shutdown = update_session
    ui.show_status(UpdateStatus(state="current"))
    assert ui.row.isHidden()
    updater.publish("available", version="0.2.0")
    assert window._nav_buttons['settings'].text() == '更新'
    assert ui.row.isHidden()
    assert ui.action.text() == "下载更新"
    ui.action.click()
    assert updater.calls == ["download"]
    assert ui.progress.value() == 25
    assert window.isEnabled()
    ui.cancel_button.click()
    assert updater.calls[-1] == "cancel"
    updater.publish("ready", version="0.2.0")
    assert ui.action.text() == "重启并更新"
    ui.action.click()
    assert shutdown == []  # Keep runtime alive until the installer requests quit.
    assert ui.restart_prepared
    assert ui.store.load()["workspace"]["page"] == vm.page
    assert not window.isEnabled()


def test_view_model_changes_do_not_queue_recovery_without_pending_workspace(update_session):
    _window, vm, _gateway, _snapshot, _store, ui, _updater, _shutdown = update_session
    assert ui._pending is None

    vm.changed.emit(vm.model)

    assert not ui._restore_timer.isActive()


@pytest.mark.parametrize("failure_state", ["failed", "ready"])
def test_installer_launch_failure_reenables_current_app_and_preserves_workspace(update_session, failure_state):
    window, vm, _, _, _, ui, updater, shutdown = update_session
    vm.rename_profile(vm.draft.config["active_profile"], "Still editable")
    before = deepcopy(vm.draft.config)
    assert ui.prepare_restart()
    updater.publish(failure_state, message="installer launch failed")
    assert window.isEnabled()
    assert not ui.restart_prepared
    assert shutdown == []
    assert vm.draft.config == before
    assert ui.store.load()["draft"]["candidate_config"] == before


def test_about_settings_exposes_explicit_check(update_session):
    window, vm, _, _, _, ui, updater, _ = update_session
    window._settings_section = "about"
    vm.navigate("settings")
    button = window.findChild(QPushButton, "checkDesktopUpdate")
    assert button is not None
    button.click()
    assert updater.calls == ["check"]
    assert not ui.row.isHidden()
    assert ui.message.text() == "当前已是最新版本"


@pytest.mark.parametrize("kind", ["mapping", "preferences", "macro", "prompt"])
def test_pending_editor_survives_fresh_window_without_device_write(update_session, contract, qtbot, monkeypatch, kind):
    window, vm, gateway, _, _, ui, _, _ = update_session
    if kind == "mapping":
        window._selected_control_id = "key.8"
        window.render(vm.model)
        editor = window._content.findChild(ActionEditor)
        editor.set_action({"type": "key", "usage": 17, "modifiers": [227]})
        window._content.findChild(QLineEdit, "mappingShortNameEditor").setText("新建任务")
    elif kind == "preferences":
        vm.navigate("lighting")
        window._content.findChild(PreferencesEditor)._haptic_duration.setValue(37)
    elif kind == "macro":
        window._selected_macro_id = vm.create_macro()
        vm.navigate("sequences")
        window._content.findChild(QLineEdit, "macroNameEditor").setText("Pending sequence")
        window._content.findChild(MacroEditor)._add_step("delay")
    else:
        vm.navigate("prompts")
        editor = window._content.findChild(PromptLibraryEditor)
        editor._select_direction(3)
        editor._name.setText("待保存提示词")
        editor._body.setPlainText("中文草稿\nKeep exact contents")
    pending = ui.capture_workspace()
    assert kind in pending
    gateway.commands.clear()
    assert ui.prepare_restart()
    assert gateway.commands == []
    restored_window, restored_vm, restored_gateway, restored = _restart_destination(update_session, contract, qtbot, monkeypatch)
    restored.restore_if_ready()
    after = restored.capture_workspace()
    assert after[kind] == pending[kind]
    assert restored_vm.page == vm.page
    assert restored_vm.draft.config == vm.draft.config
    assert restored_gateway.commands == []
    assert not ui.store.path.exists()


@pytest.mark.parametrize("blocker", ["writing", "unknown", "image_draft", "maintenance"])
def test_unsafe_restart_is_blocked_without_saving_or_shutting_down(update_session, monkeypatch, blocker):
    window, vm, _, _, _, ui, _, shutdown = update_session
    if blocker in {"writing", "unknown"}:
        vm._write_transaction = ConfigTransaction(
            ConfigTransactionState.WRITING if blocker == "writing" else ConfigTransactionState.UNKNOWN
        )
    elif blocker == "image_draft":
        window._screen_icon_drafts["home"] = SimpleNamespace(is_dirty=True)
    else:
        vm._firmware_update = FirmwareUpdateTransaction(FirmwareUpdateState.TRANSFERRING)
    assert not ui.prepare_restart()
    assert shutdown == []
    assert not ui.store.path.exists()
    assert window.isEnabled()
    vm._firmware_update = FirmwareUpdateTransaction()
    window._screen_icon_drafts.clear()


def test_failed_draft_save_cancels_restart(update_session, monkeypatch):
    window, _, _, _, _, ui, _, shutdown = update_session
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(ui.store, "save", fail)
    assert not ui.prepare_restart()
    assert shutdown == []
    assert "disk full" in ui.message.text()
    assert window.isEnabled()


@pytest.mark.parametrize("conflict", ["device", "config"])
def test_recovery_conflict_keeps_file_and_current_workspace(update_session, contract, qtbot, monkeypatch, conflict):
    _, vm, _, snapshot, _, ui, _, _ = update_session
    vm.rename_profile(snapshot.active_profile_id, "Recovery draft")
    assert ui.prepare_restart()
    if conflict == "device":
        snapshot = replace(snapshot, identity={**snapshot.identity, "serial": "OTHER-DEVICE"})
    else:
        config = deepcopy(snapshot.config)
        config["profiles"][0]["name"] = "Device changed"
        snapshot = replace(snapshot, config_result={**snapshot.config_result, "config": config})
    window, fresh_vm, gateway, restored = _restart_destination(update_session, contract, qtbot, monkeypatch, snapshot)
    before = deepcopy(fresh_vm.draft.config)
    restored.restore_if_ready()
    assert fresh_vm.draft.config == before
    assert gateway.commands == []
    assert ui.store.path.exists()
    assert restored._pending


@pytest.mark.parametrize("page", ["lighting", "prompts"])
def test_recovery_never_overwrites_new_pending_editor_input(update_session, contract, qtbot, monkeypatch, page):
    window, vm, _, _, _, ui, _, _ = update_session
    vm.navigate(page)
    if page == "lighting":
        window._content.findChild(PreferencesEditor)._haptic_duration.setValue(37)
    else:
        window._content.findChild(PromptLibraryEditor)._body.setPlainText("更新前的输入")
    assert ui.prepare_restart()
    window, fresh_vm, _, restored = _restart_destination(update_session, contract, qtbot, monkeypatch)
    fresh_vm.navigate(page)
    if page == "lighting":
        window._content.findChild(PreferencesEditor)._haptic_duration.setValue(91)
    else:
        window._content.findChild(PromptLibraryEditor)._body.setPlainText("重启后刚输入的内容")
    before = restored.capture_workspace()
    restored.restore_if_ready()
    assert restored.capture_workspace() == before
    assert restored.store.path.exists()
    assert restored._pending


@pytest.mark.parametrize("cached_kind", ["other_device", "old_generation"])
def test_cached_inactive_dirty_draft_blocks_update_restart(update_session, contract, cached_kind):
    window, vm, _, snapshot, _, ui, _, shutdown = update_session
    if cached_kind == "other_device":
        old_snapshot = replace(snapshot, identity={**snapshot.identity, "serial": "CACHED-OTHER-DEVICE"})
    else:
        old_snapshot = replace(snapshot, config_result={
            **snapshot.config_result, "generation": snapshot.config_result["generation"] - 1,
        })
    cached = LocalDraft.from_snapshot(old_snapshot, contract)
    cached.rename_profile(old_snapshot.active_profile_id, "Do not lose cached draft")
    vm._drafts[cached.key] = cached
    assert not ui.prepare_restart()
    assert shutdown == []
    assert not ui.store.path.exists()
    assert window.isEnabled()
    assert cached.is_dirty
    assert vm._drafts[cached.key] is cached
    del vm._drafts[cached.key]


def test_current_dirty_draft_in_cache_is_saved_and_allows_restart(update_session):
    _, vm, _, snapshot, _, ui, _, shutdown = update_session
    vm.rename_profile(snapshot.active_profile_id, "Current cached draft")
    assert vm._drafts[vm.draft.key] is vm.draft
    assert vm.draft.is_dirty
    assert ui.prepare_restart()
    assert shutdown == []
    assert ui.store.load()["draft"]["candidate_config"] == vm.draft.config


def test_english_update_footer_translates_dynamic_version_progress_and_restart(update_session):
    window, _, _, _, _, ui, updater, _ = update_session
    window._language_manager.set_language("en_US")
    try:
        updater.publish("available", version="0.2.7")
        assert "0.2.7" in ui.message.text()
        assert ui.message.text().isascii()
        assert "download" in ui.action.text().lower()
        updater.publish("downloading", version="0.2.7", received=21, total=84)
        assert "0.2.7" in ui.message.text()
        assert ui.message.text().isascii()
        assert ui.progress.value() == 25
        assert "cancel" in ui.cancel_button.text().lower()
        updater.publish("ready", version="0.2.7")
        assert ui.message.text().isascii()
        assert "restart" in ui.action.text().lower()
    finally:
        window._language_manager.set_language("zh_CN")


def _saved_workspace_for_export(update_session, contract, qtbot, monkeypatch, *, malformed=False):
    _, vm, _, snapshot, _, ui, _, _ = update_session
    if malformed:
        ui.store.path.write_bytes(b'{"workspace": "unfinished')
    else:
        vm.rename_profile(snapshot.active_profile_id, "Retained recovery")
        ui.store.save(snapshot, vm.draft, ui.capture_workspace())
    other_device = replace(snapshot, identity={**snapshot.identity, "serial": "EXPORT-OTHER-DEVICE"})
    return _restart_destination(update_session, contract, qtbot, monkeypatch, other_device)


def test_malformed_recovery_is_not_overwritten_by_another_update(update_session, contract, qtbot, monkeypatch):
    window, _, gateway, ui = _saved_workspace_for_export(update_session, contract, qtbot, monkeypatch, malformed=True)
    original = ui.store.path.read_bytes()
    assert ui._recovery_error
    assert not ui.export_recovery.isHidden()
    assert not ui.prepare_restart()
    assert ui.store.path.read_bytes() == original
    assert window.isEnabled()
    assert gateway.commands == []


def test_canceling_recovery_export_retains_pending_workspace(update_session, contract, qtbot, monkeypatch):
    _, _, _, ui = _saved_workspace_for_export(update_session, contract, qtbot, monkeypatch)
    original = ui.store.path.read_bytes()
    pending = deepcopy(ui._pending)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    ui.export_recovery.click()
    assert ui.store.path.read_bytes() == original
    assert ui._pending == pending
    assert ui.blocked_reason()


@pytest.mark.parametrize("malformed", [False, True])
def test_export_preserves_exact_recovery_bytes_and_releases_auto_restore(update_session, contract, qtbot, monkeypatch, tmp_path, malformed):
    _, _, _, ui = _saved_workspace_for_export(update_session, contract, qtbot, monkeypatch, malformed=malformed)
    original = ui.store.path.read_bytes()
    exported = tmp_path / "manually-retained-workspace.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(exported), "JSON (*.json)"))
    ui.export_recovery.click()
    assert exported.read_bytes() == original
    assert not ui.store.path.exists()
    assert ui._pending is None
    assert not ui._recovery_error
    assert ui.export_recovery.isHidden()
    assert ui.blocked_reason() == ""


def test_failed_recovery_export_retains_original_and_pending_state(update_session, contract, qtbot, monkeypatch, tmp_path):
    _, _, _, ui = _saved_workspace_for_export(update_session, contract, qtbot, monkeypatch)
    original = ui.store.path.read_bytes()
    pending = deepcopy(ui._pending)
    exported = tmp_path / "failed-export.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(exported), "JSON (*.json)"))
    def fail_copy(*args):
        raise OSError("export disk full")
    monkeypatch.setattr("controller_config.views.desktop_update.shutil.copyfile", fail_copy)
    ui.export_recovery.click()
    assert ui.store.path.read_bytes() == original
    assert ui._pending == pending
    assert ui.blocked_reason()
    assert "export disk full" in ui.message.text()


def test_manual_async_current_stays_visible_background_current_is_quiet(update_session, monkeypatch):
    window, _, _, _, _, ui, updater, _ = update_session
    window.show()
    updater.publish('current')
    assert ui.row.isHidden()
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('checking'))
    ui.check()
    updater.publish('current')
    assert not ui.row.isHidden()
    assert ui.message.text() == '当前已是最新版本'


def test_manual_current_supersedes_successful_workspace_recovery_message(
        update_session, contract, qtbot, monkeypatch):
    _, vm, _, snapshot, _, ui, _, _ = update_session
    vm.rename_profile(snapshot.active_profile_id, "Recovered draft")
    assert ui.prepare_restart()

    _, _, gateway, restored = _restart_destination(
        update_session, contract, qtbot, monkeypatch)
    restored.restore_if_ready()
    assert gateway.commands == []
    assert restored._pending is None
    assert restored.message.text() == '已恢复更新前的本地草稿；尚未写入设备。'

    updater = FakeUpdater(restored.prepare_restart)
    restored.attach(updater)
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('checking'))
    restored.check()
    assert restored.message.text() == '正在检查应用更新…'
    updater.publish('current')
    assert restored.message.text() == '当前已是最新版本'


@pytest.mark.parametrize(
    ('state', 'values', 'expected'),
    [
        ('available', {'version': '0.2.0'}, '控制台有更新 0.2.0'),
        ('failed', {'message': 'network unavailable'}, '更新未完成，可稍后重试'),
    ],
)
def test_manual_terminal_result_is_not_masked_by_successful_recovery_message(
        update_session, monkeypatch, state, values, expected):
    _, _, _, _, _, ui, updater, _ = update_session
    ui._recovery_message = '已恢复更新前的本地草稿；尚未写入设备。'
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('checking'))
    ui.check()
    updater.publish(state, **values)
    assert ui.message.text() == expected


@pytest.mark.parametrize('recovery_state', ['pending', 'failed'])
def test_manual_current_keeps_unresolved_recovery_visible_and_actionable(
        update_session, monkeypatch, recovery_state):
    _, _, _, _, _, ui, updater, _ = update_session
    message = '更新前的草稿尚未恢复，请连接原设备。'
    ui._recovery_message = message
    if recovery_state == 'pending':
        ui._pending = {'device': {'serial': 'ORIGINAL-DEVICE'}}
    else:
        ui._recovery_error = True
    ui.show_status(ui.status)
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('checking'))
    ui.check()
    updater.publish('current')
    assert ui.message.text() == message
    assert not ui.export_recovery.isHidden()
    assert ui.blocked_reason()


def test_snooze_and_manual_check_ready_state(update_session, tmp_path, monkeypatch):
    from controller_config.update_reminders import UpdateReminders
    from PySide6.QtCore import QSettings
    window, _, _, _, _, ui, updater, _ = update_session
    window.show()
    now = [1000.]
    ui.reminders = UpdateReminders(QSettings(str(tmp_path/'pause.ini'), QSettings.IniFormat), clock=lambda: now[0])
    updater.publish('available', version='0.2.0')
    ui.snooze.click()
    assert ui.row.isHidden()
    updater.publish('available', version='0.2.0')
    assert ui.row.isHidden()
    monkeypatch.setattr(updater, 'check', lambda: updater.publish('available', version='0.2.0'))
    ui.check()
    assert not ui.row.isHidden()
    updater.publish('ready', version='0.2.0')
    assert ui.action.text() == '重启并更新'
    assert window._nav_buttons['settings'].text() == '更新'


def test_update_notifications_never_resize_editor(update_session):
    window, _, _, _, _, ui, updater, _ = update_session
    window.resize(1100, 700)
    window.show()
    from PySide6.QtWidgets import QApplication
    QApplication.processEvents()
    before = window._content.geometry()
    updater.publish('available', version='0.2.0')
    QApplication.processEvents()
    assert window._content.geometry() == before
    updater.publish('current')
    QApplication.processEvents()
    assert window._content.geometry() == before


@pytest.mark.parametrize('result', ['current', 'failed'])
def test_manual_check_joins_inflight_background_request(update_session, result):
    _, _, _, _, _, ui, updater, _ = update_session
    updater.publish('checking')
    ui.check()
    updater.publish(result, message='network unavailable' if result == 'failed' else '')
    assert not ui.row.isHidden()
    assert updater.calls == []


def test_cancelled_download_does_not_expose_later_background_error(update_session):
    _, _, _, _, _, ui, updater, _ = update_session
    updater.publish('available', version='0.2.0')
    ui.activate()
    updater.cancel()
    updater.publish('checking')
    updater.publish('failed', message='network unavailable')
    assert ui.row.isHidden()


def test_snooze_releases_native_offer_and_allows_newer_release(update_session, tmp_path):
    from controller_config.desktop_update_macos import MacDesktopUpdater
    from controller_config.update_reminders import UpdateReminders
    from PySide6.QtCore import QSettings
    _, _, _, _, _, ui, _, _ = update_session
    ui.reminders = UpdateReminders(QSettings(str(tmp_path/'native-pause.ini'), QSettings.IniFormat))
    native = MacDesktopUpdater({}, lambda: True)
    ui.attach(native)
    choices = []
    native._version = '0.2.0'
    native._download_reply = choices.append
    native._publish('available')
    ui.snooze_update()
    assert choices == [2]  # Sparkle DISMISS, never permanent SKIP.
    assert native._download_reply is None
    assert ui.row.isHidden()
    native._download_reply = choices.append
    native._publish('available')
    assert choices == [2, 2]
    native._version = '0.3.0'
    native._download_reply = choices.append
    native._publish('available')
    assert ui.window._nav_buttons['settings'].text() == '更新'
    assert native._download_reply is not None


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_both_reminders_fit_small_window_in_all_languages(update_session, qtbot, tmp_path, language):
    from PySide6.QtWidgets import QApplication
    from test_firmware_reminder_ui import offer
    window, vm, gateway, snapshot, store, ui, updater, _ = update_session
    firmware_ui, _ = offer((window, vm, gateway, snapshot, store))
    window._language_manager.set_language(language)
    window.resize(1100, 700)
    window.show()
    updater.publish('available', version='0.2.0')
    QApplication.processEvents()
    button = window._nav_buttons['settings']
    assert button.text() == window._language_manager.translate('更新')
    assert button.property('firmwareUpdateAvailable') is True
    assert not button.firmware_notice_visible()
    assert button.fontMetrics().horizontalAdvance(button.text()) < button.width()
    assert button.parentWidget().rect().contains(button.geometry())
    assert ui.row.isHidden() and firmware_ui.row.isHidden()
    assert window.grab().save(str(tmp_path / f'controlled-navigation-{language}.png'))


def test_about_button_dispatches_manual_update_request(update_session, qtbot):
    from PySide6.QtCore import Qt
    window, vm, _, _, _, ui, updater, _ = update_session
    window._settings_section = 'about'
    vm.navigate('settings')
    window.show()
    button = window.findChild(QPushButton, 'checkDesktopUpdate')
    qtbot.mouseClick(button, Qt.LeftButton)
    assert updater.calls == ['check']
    assert not ui.row.isHidden()


@pytest.mark.parametrize('previous', ['idle', 'current'])
def test_deferred_native_check_does_not_settle_on_previous_status(update_session, monkeypatch, previous):
    _, _, _, _, _, ui, updater, _ = update_session
    updater.publish(previous)
    # Sparkle checkForUpdates returns before its checking callback is delivered.
    monkeypatch.setattr(updater, 'check', lambda: None)
    ui.check()
    updater.publish('checking')
    updater.publish('current')
    assert not ui.row.isHidden()
    assert ui.message.text() == '当前已是最新版本'


@pytest.mark.parametrize('cancelled_state', ['idle', 'available'])
def test_transfer_cancel_clears_user_error_marker_without_check_callback(update_session, cancelled_state):
    _, _, _, _, _, ui, updater, _ = update_session
    updater.publish('available', version='0.2.0')
    ui.activate()
    updater.publish(cancelled_state)
    # Sparkle's scheduled checks need not call its user-initiated checking delegate.
    updater.publish('failed', message='background unavailable')
    assert ui.row.isHidden()
