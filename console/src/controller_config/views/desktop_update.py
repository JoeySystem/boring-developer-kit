"""Non-modal update controls and the local workspace handoff across restart."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QProgressBar, QLineEdit, QFileDialog, QSizePolicy

from controller_config.desktop_update import UpdateStatus
from controller_config import __version__
from controller_config.update_reminders import UpdateReminders
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.models import AppState
from controller_config.update_recovery import UpdateRecoveryStore
from controller_config.transactions import ConfigTransactionState
from controller_config.views.macro_editor import MacroEditor
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.prompt_library_editor import PromptLibraryEditor


class DesktopUpdateUi(QObject):
    def __init__(self, window, *, store=None):
        super().__init__(window)
        self.window = window
        self.vm = window._view_model
        self.store = store or UpdateRecoveryStore()
        self.updater = None
        self.status = UpdateStatus()
        self.restart_prepared = False
        self._restoring = False
        self._pending = None
        self._recovery_message = ''
        self._recovery_error = False
        self.reminders = UpdateReminders()
        self.reminders.clear_target('app', __version__)
        self._manual_pending = False
        self._manual_result = None
        self._user_operation = False
        self._navigation_update_target = None
        self.row = window._desktop_update_footer
        layout = QHBoxLayout(self.row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.message = QLabel(objectName='desktopUpdateMessage')
        self.message.setTextFormat(Qt.PlainText)
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.message.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.action = QPushButton(objectName='desktopUpdateAction')
        self.action.clicked.connect(self.activate)
        self.progress = QProgressBar(objectName='desktopUpdateProgress')
        self.progress.setFixedWidth(70)
        self.progress.setMaximumHeight(12)
        self.progress.setTextVisible(False)
        self.cancel_button = QPushButton('取消下载', objectName='cancelDesktopUpdate')
        self.cancel_button.clicked.connect(lambda: self.updater.cancel())
        self.export_recovery = QPushButton('导出保留的工作区…', objectName='exportUpdateWorkspace')
        self.export_recovery.clicked.connect(self.export_saved_workspace)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.action)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.export_recovery)
        self.snooze = QPushButton('稍后提醒', objectName='snoozeDesktopUpdate')
        self.snooze.clicked.connect(self.snooze_update)
        layout.addWidget(self.snooze)
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60000)
        self._reminder_timer.timeout.connect(lambda: self.show_status(self.status))
        self._reminder_timer.start()
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self.restore_if_ready)
        self.vm.changed.connect(self._schedule_restore)
        self.vm.changed.connect(self._refresh_footer_location)
        window._language_manager.language_changed.connect(lambda *_: self.show_status(self.status))
        try:
            self._pending = self.store.load()
            if self._pending:
                self._recovery_message = '已保留更新前的草稿；连接原设备后恢复。'
        except (OSError, ValueError) as exc:
            self._recovery_error = True
            self._recovery_message = f'更新草稿暂无法恢复：{exc}'
        self.show_status(self.status)
        self._schedule_restore()

    def attach(self, updater):
        self.updater = updater
        updater.changed.connect(self.show_status)

    def _schedule_restore(self, *_):
        if (
            self._pending
            and not self._restoring
            and not self.restart_prepared
            and not self._restore_timer.isActive()
        ):
            self._restore_timer.start(0)

    def check(self):
        if self.updater is not None:
            if self.status.state in {'checking', 'downloading', 'verifying', 'ready', 'installing'}:
                if self.status.state == 'checking':
                    self._manual_pending = True
                self.show_status(self.status, force=True)
                return
            self._manual_pending = True
            self.updater.check()
            self.show_status(getattr(self.updater, 'status', self.status), force=True)

    def show_status(self, status, *, force=False):
        signature = (status.state, status.version, status.message)
        if status.state == 'checking':
            self._manual_result = None
            self._user_operation = False
        elif self._manual_pending and not force and status.state in {'current', 'failed', 'available', 'unconfigured'}:
            # Sparkle starts asynchronously; repainting the old status after
            # check() returns is not the response to this manual request.
            self._manual_result = signature
            self._manual_pending = False
        manual = self._manual_pending or self._manual_result == signature
        if status.state in {'idle', 'current', 'available'}:
            self._user_operation = False
        self.status = status
        if self.restart_prepared and status.state in {'failed', 'ready', 'available', 'idle', 'current'}:
            self.restart_prepared = False
            self.window.setEnabled(True)
        texts = {
            'unconfigured': '此安装包尚未配置在线更新。',
            'idle': '检查应用更新', 'checking': '正在检查应用更新…',
            'current': '当前已是最新版本', 'available': '控制台有更新 {version}',
            'downloading': '正在下载 {version}，可继续使用',
            'verifying': '正在验证并准备更新…', 'ready': '更新已准备好，重启后生效',
            'installing': '正在重启并更新…', 'failed': '更新未完成，可稍后重试',
        }
        source = texts.get(status.state, '应用更新')
        recovery_needs_attention = bool(self._pending) or self._recovery_error
        if status.state in {'unconfigured', 'idle', 'current'} and (
                not manual or recovery_needs_attention):
            source = self._recovery_message or source
        if status.state == 'unconfigured' and force:
            source = status.message or source
        text = self.window._language_manager.translate(source)
        if status.state in {'available', 'downloading'}:
            text = text.format(version=status.version)
        self.message.setText(text)
        self.message.setToolTip(status.message or text)
        self.action.setText(self.window._language_manager.translate({
            'available': '下载更新', 'ready': '重启并更新', 'failed': '重试',
        }.get(status.state, '检查更新')))
        self.action.setVisible(status.state in {'available', 'ready', 'failed'})
        capsule_only = (
            self._navigation_update_target is not None
            and status.state in {'downloading', 'verifying', 'ready', 'installing'}
        )
        self.progress.setVisible(status.state in {'downloading', 'verifying'} and not capsule_only)
        if status.total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, max(0, int(status.received * 100 / status.total))))
        else:
            self.progress.setRange(0, 0)
        self.cancel_button.setText(self.window._language_manager.translate('取消下载'))
        self.cancel_button.setVisible(status.state == 'downloading' and not capsule_only)
        self.export_recovery.setText(self.window._language_manager.translate('导出保留的工作区…'))
        self.export_recovery.setVisible(bool(self._pending) or self._recovery_error)
        if status.state == 'current':
            self.reminders.clear_target('app', __version__)
        paused = self.reminders.is_snoozed('app', status.version)
        self.snooze.setText(self.window._language_manager.translate('稍后提醒'))
        self.snooze.setToolTip(self.window._language_manager.translate('同一版本 24 小时内不再主动提醒，仍可手动检查。'))
        self.snooze.setVisible(status.state == 'available')
        visible = force or manual or bool(self._recovery_message) or self._recovery_error
        visible = visible or status.state in {'downloading', 'verifying', 'ready', 'installing'}
        visible = visible or (status.state == 'available' and not paused)
        visible = visible or (status.state == 'failed' and self._user_operation)
        notice = status.state in {'downloading', 'verifying', 'ready', 'installing'}
        notice = notice or (status.state == 'available' and (not paused or manual or force))
        detail = text
        if status.state == 'available':
            detail += '\n' + self.window._language_manager.translate('点击下载，验证完成后重启更新；未完成的设备操作会阻止重启。')
        if status.state == 'downloading' and status.total > 0:
            detail += f' {int(status.received * 100 / status.total)}%'
        navigation_progress = None
        if status.state == 'downloading':
            navigation_progress = (
                min(100, max(0, int(status.received * 100 / status.total)))
                if status.total > 0 else -1
            )
        self.window._nav_buttons['settings'].set_update_notice(
            desktop=status.state if notice else '', detail=detail,
            progress=navigation_progress,
        )
        # Background offers live in the navigation. Explicit results/progress
        # and the settings detail view keep their existing controls.
        self._footer_settings_only = status.state in {'available', 'ready'} and not (
            force or manual or self._recovery_message or self._recovery_error)
        self._footer_visible = visible and not (capsule_only and not recovery_needs_attention)
        self._refresh_footer_location()
        if status.state in {'failed', 'idle', 'current', 'available'}:
            self._navigation_update_target = None
        if status.state == 'ready' and self._navigation_update_target is not None:
            QTimer.singleShot(0, self._install_navigation_update)
        if status.state == 'available' and paused and not manual and not force and self.updater is not None:
            # Release Sparkle's active offer so its scheduler can find later
            # versions. Windows cancel is a no-op at the available stage.
            self.updater.cancel()

    def _refresh_footer_location(self, *_):
        self.row.setVisible(self._footer_visible and (
            not self._footer_settings_only or self.vm.page == 'settings'))

    def snooze_update(self):
        if self.status.state != 'available':
            return
        self.reminders.snooze('app', self.status.version)
        self._manual_result = None
        self._manual_pending = False
        self.show_status(self.status)

    def export_saved_workspace(self):
        path, _ = QFileDialog.getSaveFileName(
            self.window, self.window._language_manager.translate('导出保留的工作区'),
            'BORING-update-workspace.json', 'JSON (*.json)',
        )
        if not path:
            return
        try:
            if Path(path).resolve() == self.store.path.resolve():
                raise ValueError('请选择其他文件位置，以保留导出的工作区。')
            shutil.copyfile(self.store.path, path)
            self.store.clear()
        except (OSError, ValueError) as exc:
            self._recovery_message = f'导出未完成，原工作区仍保留：{exc}'
        else:
            self._pending = None
            self._recovery_error = False
            self._recovery_message = '工作区已导出；停止自动恢复，可以继续更新。'
        self.show_status(self.status)

    def activate_navigation(self):
        if self.updater is None:
            return
        if self.status.state == 'available':
            self._navigation_update_target = self.status.version
            self._user_operation = True
            self.updater.download()
        elif self.status.state == 'ready':
            self.updater.install()
        elif self.status.state == 'failed':
            self.check()
        else:
            self.show_status(self.status, force=True)

    def _install_navigation_update(self):
        target, self._navigation_update_target = self._navigation_update_target, None
        if target is not None and self.status.state == 'ready' and self.status.version == target:
            # Both platform backends still call prepare_restart, including all
            # existing workspace preservation and device-transaction guards.
            self.updater.install()

    def activate(self):
        if self.status.state == 'available':
            self._user_operation = True
            self.updater.download()
        elif self.status.state == 'ready':
            self.updater.install()
        else:
            self.check()

    def blocked_reason(self):
        vm = self.vm
        if vm.ble_name.busy:
            return '蓝牙名称正在保存或读回，请稍后操作'
        if self._pending or self._recovery_error:
            return '更新前的草稿尚未恢复，请先连接原设备处理保留的草稿。'
        if any(d is not vm.draft for d in vm.pending_dirty_workspaces()):
            return '其他设备或旧配置仍有本地草稿，请先切回处理，再重启更新。'
        if vm.screen_icon.busy or vm.screen_glyphs.busy:
            return '素材传输或读回尚未结束，请完成后再重启更新。'
        if vm.calibration.blocks_editing or vm.firmware_update.blocks_editing:
            return '设备维护尚未结束，请完成后再重启更新。'
        if vm.write_transaction.blocks_editing or vm.write_transaction.state is ConfigTransactionState.UNKNOWN:
            return '设备写入或对账尚未完成，请完成后再重启更新。'
        if vm.prompt_device.status.is_busy:
            return '提示词设备操作尚未结束，请完成后再重启更新。'
        if any(d.is_dirty for d in (*self.window._screen_icon_drafts.values(), *self.window._screen_glyph_drafts.values())):
            return '素材裁剪尚未应用，请先完成或取消素材编辑，再重启更新。'
        return ''

    def capture_workspace(self):
        w = self.window
        state = {'page': self.vm.page, 'control': w._selected_control_id,
                 'macro_id': w._selected_macro_id, 'settings_section': w._settings_section}
        card = w._current_mapping_card()
        mapping = w._current_mapping_editing_state()
        if card is not None and mapping is None:
            raise ValueError('请先完成当前快捷键输入，再重启更新。')
        if mapping is not None:
            state['mapping'] = asdict(mapping)
        prefs = w._content.findChild(PreferencesEditor)
        if prefs is not None:
            state['preferences'] = prefs.values()
        macro = w._content.findChild(MacroEditor)
        name = w._content.findChild(QLineEdit, 'macroNameEditor')
        if macro is not None and name is not None:
            state['macro'] = [name.text(), macro.steps()]
        prompt = w._content.findChild(PromptLibraryEditor)
        if prompt is not None and prompt.editing_state() is not None:
            state['prompt'] = asdict(prompt.editing_state())
        return state

    def prepare_restart(self):
        self._footer_settings_only = False
        self._footer_visible = True
        reason = self.blocked_reason()
        if reason:
            self.message.setText(self.window._language_manager.translate(reason))
            self.row.show()
            return False
        if not self.window._confirm_discard_ble_names():
            return False
        reason = self.blocked_reason()
        if reason:
            self.message.setText(self.window._language_manager.translate(reason))
            self.row.show()
            return False
        try:
            self.store.save(self.vm.model.snapshot, self.vm.draft, self.capture_workspace())
        except (OSError, ValueError, TypeError) as exc:
            self.message.setText(self.window._language_manager.translate(f'无法保留草稿，已取消重启：{exc}'))
            self.row.show()
            return False
        self.restart_prepared = True
        self.vm.stop_lighting_preview(clear_candidate=True)
        # Actual shutdown belongs to closeEvent/aboutToQuit; installer launch may fail.
        self.window.setEnabled(False)
        return True

    def has_new_editor_input(self):
        w, draft = self.window, self.vm.draft
        if w._mapping_editor_has_uncommitted_changes():
            return True
        prompt = w._content.findChild(PromptLibraryEditor)
        if prompt is not None and prompt.has_unsaved_fields():
            return True
        prefs = w._content.findChild(PreferencesEditor)
        if prefs is not None and draft is not None and prefs.values() != (
            draft.config['lighting'], draft.config['haptic'], draft.config['display']
        ):
            return True
        macro = w._content.findChild(MacroEditor)
        name = w._content.findChild(QLineEdit, 'macroNameEditor')
        if macro is not None and name is not None and draft is not None:
            saved = draft.macro(w._selected_macro_id)
            if name.text() != saved.get('name', '') or macro.steps() != saved.get('steps', []):
                return True
        return any(d.is_dirty for d in (*w._screen_icon_drafts.values(), *w._screen_glyph_drafts.values()))

    def restore_if_ready(self):
        if not self._pending or self._restoring or self.restart_prepared:
            return
        # Recovery must not replace edits entered after the new process opened.
        if self.has_new_editor_input():
            self._recovery_message = '当前有新输入，请先保存或取消，再恢复更新前的草稿。'
            self.show_status(self.status)
            return
        if self._pending.get('device') is not None and self.vm.model.state not in {AppState.READY, AppState.READ_ONLY}:
            return
        self._restoring = True
        try:
            if not self.store.apply(self.vm, self._pending):
                self._recovery_message = self.store.last_error or '连接原设备后恢复更新前的草稿。'
                self.show_status(self.status)
                return
            state = self._pending.get('workspace', {})
            w = self.window
            w._selected_control_id = state.get('control')
            w._selected_macro_id = state.get('macro_id')
            w._settings_section = state.get('settings_section', 'general')
            w._update_workspace_restore = state
            self.vm.navigate(state.get('page', 'overview'))
            # navigate may be a no-op if already on the same page.
            if w._update_workspace_restore:
                w.render(self.vm.model)
            self.store.clear()
            self._pending = None
            self._recovery_message = '已恢复更新前的本地草稿；尚未写入设备。'
            self.show_status(self.status)
        except (OSError, ValueError, TypeError) as exc:
            self._recovery_message = f'草稿保留在本地，暂无法恢复：{exc}'
            self.show_status(self.status)
        finally:
            self._restoring = False
