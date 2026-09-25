"""Non-modal update controls and the local workspace handoff across restart."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse
from xml.etree import ElementTree

from PySide6.QtCore import QObject, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QScrollArea,
)

from controller_config.build_identity import BuildIdentity
from controller_config.desktop_update import UpdateStatus, default_desktop_update_source
from controller_config import __version__
from controller_config.update_reminders import UpdateReminders
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.models import AppState
from controller_config.update_recovery import UpdateRecoveryStore
from controller_config.transactions import ConfigTransactionState
from controller_config.views.actions import ActionsPage
from controller_config.views.workflows import WorkflowPage
from controller_config.views.automation import AutomationEditingState, AutomationPage
from controller_config.views.macro_editor import MacroEditor
from controller_config.views.preferences_editor import PreferencesEditor


def _official_download_source(platform: str | None = None) -> dict:
    try:
        config = default_desktop_update_source()
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    platform_key = {'darwin': 'macos', 'win32': 'windows'}.get(
        platform or sys.platform
    )
    sources = config.get('official_download_sources', {}) if isinstance(config, dict) else {}
    selected = sources.get(platform_key, {}) if isinstance(sources, dict) else {}
    return selected if isinstance(selected, dict) else {}


def _official_download_feed_url(source: dict) -> str:
    feed_url = str(source.get('feed_url', '')).strip()
    parsed_feed = urlparse(feed_url)
    if parsed_feed.scheme != 'https' or not parsed_feed.hostname:
        raise ValueError('官方下载源尚未配置。')
    return feed_url


def _resolve_official_download_url(source: dict, payload: bytes) -> str:
    if source.get('format') == 'sparkle':
        root = ElementTree.fromstring(payload)
        enclosure = root.find('.//enclosure')
        package_url = enclosure.get('url', '') if enclosure is not None else ''
    elif source.get('format') == 'json':
        document = json.loads(payload)
        package_url = document.get('url', '') if isinstance(document, dict) else ''
    else:
        raise ValueError('官方下载源格式不可用。')
    parsed_package = urlparse(package_url)
    if (
        parsed_package.scheme != 'https'
        or not parsed_package.hostname
        or parsed_package.username
        or parsed_package.password
    ):
        raise ValueError('官方下载地址不可用。')
    return package_url


class DesktopUpdateUi(QObject):
    def __init__(
        self,
        window,
        *,
        build_identity: BuildIdentity,
        store=None,
        confirm_official_switch=None,
        official_url_resolver=None,
        official_network=None,
        url_opener=None,
        open_failure_notifier=None,
    ):
        super().__init__(window)
        self.window = window
        self.vm = window._view_model
        self.build_identity = build_identity
        self.store = store or UpdateRecoveryStore()
        self._confirm_official_switch = (
            confirm_official_switch or self._confirm_official_switch_dialog
        )
        self._official_url_resolver = official_url_resolver
        self._official_network = official_network
        self._official_download_reply = None
        self._url_opener = url_opener or QDesktopServices.openUrl
        self._open_failure_notifier = (
            open_failure_notifier or self._show_official_download_failure
        )
        self.official_download_source = _official_download_source()
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
        self.row = window._desktop_update_footer
        layout = QHBoxLayout(self.row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.message = QLabel(objectName='desktopUpdateMessage')
        self.message.setWordWrap(True)
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
        self.restore_recovery = QPushButton('恢复草稿', objectName='restoreUpdateWorkspace')
        self.restore_recovery.clicked.connect(self.restore_if_ready)
        self.export_recovery = QPushButton('导出保留的工作区…', objectName='exportUpdateWorkspace')
        self.export_recovery.clicked.connect(self.export_saved_workspace)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.progress)
        layout.addWidget(self.action)
        layout.addWidget(self.cancel_button)
        layout.addWidget(self.restore_recovery)
        layout.addWidget(self.export_recovery)
        self.snooze = QPushButton('稍后提醒', objectName='snoozeDesktopUpdate')
        self.snooze.clicked.connect(self.snooze_update)
        layout.addWidget(self.snooze)
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60000)
        self._reminder_timer.timeout.connect(lambda: self.show_status(self.status))
        self._restore_signal_connected = False
        self.vm.changed.connect(self._refresh_footer_location)
        window._language_manager.language_changed.connect(lambda *_: self.show_status(self.status))
        try:
            self._pending = self.store.load()
            if self._pending:
                self._recovery_message = '已保留更新前的草稿；连接原设备后恢复。'
                self.vm.changed.connect(self._schedule_restore)
                window._connection_fade.finished.connect(self._schedule_restore)
                self._restore_signal_connected = True
        except (OSError, ValueError) as exc:
            self._recovery_error = True
            self._recovery_message = f'更新草稿暂无法恢复：{exc}'
        self.show_status(self.status)
        if self._pending:
            QTimer.singleShot(0, self.restore_if_ready)

    def attach(self, updater):
        self.updater = updater
        updater.changed.connect(self.show_status)

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
        signature = (status.state, status.version, status.message, status.retry_action)
        if status.state == 'checking':
            self._manual_result = None
            self._user_operation = False
        elif self._manual_pending and not force and status.state in {
            'current', 'failed', 'available', 'unconfigured', 'custom', 'unavailable'
        }:
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
            'custom': '自定义版本不使用官方软件自动更新。',
            'unavailable': '构建信息不可用，已停用软件更新。',
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
        if status.state == 'unconfigured' and status.message:
            source = status.message or source
        text = self.window._language_manager.translate(source)
        if status.state in {'available', 'downloading'}:
            text = text.format(version=status.version)
        if status.state == 'downloading' and status.total > 0:
            text += f'  {status.received / 1048576:.1f} / {status.total / 1048576:.1f} MB'
        if status.state == 'failed' or status.retry_action:
            reason = self.window._language_manager.translate(status.message)
            text = self.window._language_manager.translate('更新未完成')
            if reason:
                text += '\n' + reason
        self.message.setText(text)
        self.message.setToolTip(status.message or text)
        action_text = {'available': '下载更新', 'ready': '重启并更新'}.get(status.state, '重新检查')
        if status.retry_action:
            action_text = {'download': '重试下载', 'install': '重试安装', 'check': '重新检查'}[status.retry_action]
        self.action.setText(self.window._language_manager.translate(action_text))
        self.action.setVisible(status.state in {'available', 'ready', 'failed'})
        self.progress.setVisible(status.state in {'downloading', 'verifying'})
        if status.total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, max(0, int(status.received * 100 / status.total))))
        else:
            self.progress.setRange(0, 0)
        self.cancel_button.setText(self.window._language_manager.translate('取消下载'))
        self.cancel_button.setVisible(status.state == 'downloading')
        self.restore_recovery.setText(self.window._language_manager.translate('恢复草稿'))
        self.restore_recovery.setVisible(bool(self._pending))
        self.export_recovery.setText(self.window._language_manager.translate('导出保留的工作区…'))
        self.export_recovery.setVisible(bool(self._pending) or self._recovery_error)
        if status.state == 'current':
            self.reminders.clear_target('app', __version__)
        paused = self.reminders.is_snoozed('app', status.version)
        reminder_waiting = status.state == 'available' and paused
        if reminder_waiting and not self._reminder_timer.isActive():
            self._reminder_timer.start()
        elif not reminder_waiting and self._reminder_timer.isActive():
            self._reminder_timer.stop()
        self.snooze.setText(self.window._language_manager.translate('稍后提醒'))
        self.snooze.setToolTip(self.window._language_manager.translate('同一版本 24 小时内不再主动提醒，仍可手动检查。'))
        self.snooze.setVisible(status.state == 'available')
        visible = force or manual or bool(self._recovery_message) or self._recovery_error
        visible = visible or status.state in {'downloading', 'verifying', 'ready', 'installing'}
        visible = visible or (status.state == 'available' and not paused)
        visible = visible or (status.state in {'failed', 'unconfigured'} and self._user_operation)
        notice = status.state in {'downloading', 'verifying', 'ready', 'installing'}
        notice = notice or (status.state == 'available' and (not paused or manual or force))
        detail = text
        if status.state == 'available':
            detail += '\n' + self.window._language_manager.translate('进入更新页下载；准备完成后，由你决定何时重启。')
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
        self._footer_visible = visible
        self._refresh_footer_location()
        if status.state == 'available' and paused and not manual and not force and self.updater is not None:
            # Release Sparkle's active offer so its scheduler can find later
            # versions. Windows cancel is a no-op at the available stage.
            self.updater.cancel()

    def switch_to_official(self) -> bool:
        """Open the official installer page without changing this DIY session."""

        if self.build_identity.allows_official_updates:
            return False
        message = self.window._language_manager.translate(
            '官方版不包含你的自定义修改。DIY 程序、源码和数据会保留；需要的配置或扩展可另行导入。'
        )
        if not self._confirm_official_switch(message):
            return False
        opened = False
        try:
            if self._official_url_resolver is not None:
                package_url = self._official_url_resolver(
                    self.official_download_source
                )
                opened = bool(self._url_opener(QUrl(package_url)))
            else:
                if self._official_download_reply is not None:
                    return False
                feed_url = _official_download_feed_url(
                    self.official_download_source
                )
                if self._official_network is None:
                    self._official_network = QNetworkAccessManager(self)
                reply = self._official_network.get(
                    QNetworkRequest(QUrl(feed_url))
                )
                self._official_download_reply = reply
                reply.finished.connect(
                    lambda current=reply: self._finish_official_download_lookup(
                        current
                    )
                )
                return True
        except (
            ElementTree.ParseError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            opened = False
        if not opened:
            self._open_failure_notifier(
                self.window._language_manager.translate(
                    '暂时无法获取官方安装包，请重试。'
                )
            )
            return False
        return True

    def _finish_official_download_lookup(self, reply) -> None:
        if reply is not self._official_download_reply:
            return
        self._official_download_reply = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise OSError(reply.errorString())
            package_url = _resolve_official_download_url(
                self.official_download_source,
                bytes(reply.readAll()),
            )
            if not self._url_opener(QUrl(package_url)):
                raise OSError('browser rejected the download URL')
        except (
            ElementTree.ParseError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            self._open_failure_notifier(
                self.window._language_manager.translate(
                    '暂时无法获取官方安装包，请重试。'
                )
            )
        finally:
            reply.deleteLater()

    def _confirm_official_switch_dialog(self, message: str) -> bool:
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle(
            self.window._language_manager.translate('切换到官方版本')
        )
        dialog.setText(message)
        open_button = dialog.addButton(
            self.window._language_manager.translate('下载官方版本'),
            QMessageBox.ButtonRole.AcceptRole,
        )
        dialog.addButton(
            self.window._language_manager.translate('取消'),
            QMessageBox.ButtonRole.RejectRole,
        )
        dialog.exec()
        return dialog.clickedButton() is open_button

    def _show_official_download_failure(self, message: str) -> None:
        QMessageBox.warning(
            self.window,
            self.window._language_manager.translate('切换到官方版本'),
            message,
        )

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
            self._disconnect_restore_signal()
            self._recovery_error = False
            self._recovery_message = '工作区已导出；停止自动恢复，可以继续更新。'
        self.show_status(self.status)

    def activate_navigation(self):
        self.window._select_settings_section('system')
        if self.vm.page == 'settings':
            self.show_status(self.status, force=True)

    def activate(self):
        if self.updater is None:
            return
        if self.status.retry_action:
            self._user_operation = True
            self.updater.retry()
        elif self.status.state == 'available':
            self._user_operation = True
            self.updater.download()
        elif self.status.state == 'ready':
            self._user_operation = True
            self.updater.install()
        else:
            self.check()

    def blocked_reason(self):
        vm = self.vm
        if vm.host_tasks.running:
            return '自定义动作正在运行，请完成或停止后再重启更新。'
        workflow = self.window.findChild(WorkflowPage)
        if workflow is not None and workflow._test_timer.isActive():
            return '自定义动作即将试运行，请先取消试运行，再重启更新。'
        if vm.normal_agent.busy:
            return '状态灯按键设置正在保存或读回，请稍后操作'
        if vm.normal_agent.pending_drafts():
            return '请先应用状态灯按键设置并确认读回，再重启更新。'
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
                 'macro_id': w._selected_macro_id, 'settings_section': w._settings_section,
                 'update_target_version': self.status.version}
        card = w._current_mapping_card()
        mapping = w._current_mapping_editing_state()
        if card is not None and mapping is None and not card.property("officialReadOnly"):
            raise ValueError('请先完成当前快捷键输入，再重启更新。')
        if card is None:
            mapping = w._pending_mapping_editing_state
        draft = self.vm.draft
        if mapping is not None and draft is not None and mapping.device_identity == (draft.serial, draft.hardware_id):
            state['mapping'] = asdict(mapping)
        preferences_page = w._cached_preferences_page
        prefs = (preferences_page.findChild(PreferencesEditor)
                 if preferences_page is not None and self.vm.draft is not None
                 and preferences_page.property('draftIdentity') == id(self.vm.draft)
                 and not preferences_page.property('discardEdits') else None)
        if prefs is not None:
            state['preferences'] = prefs.values()
        macro_page = w.findChild(QScrollArea, 'deviceKeySequencesPage')
        if macro_page is not None and (self.vm.draft is None
                or macro_page.property('draftIdentity') != id(self.vm.draft)
                or macro_page.property('macroId') != w._selected_macro_id):
            macro_page = None
        macro = macro_page.findChild(MacroEditor) if macro_page is not None else None
        name = macro_page.findChild(QLineEdit, 'macroNameEditor') if macro_page is not None else None
        if macro is not None and name is not None:
            state['macro'] = [name.text(), macro.steps()]
        prompt = w._prompt_editor
        if prompt is not None and prompt.editing_state() is not None:
            state['prompt'] = asdict(prompt.editing_state())
        actions = w.findChild(ActionsPage)
        if actions is not None and actions.device_serial == self.vm.automation_host.serial:
            state['workflow'] = asdict(actions._workflow_page.editing_state())
            state['actions_section'] = actions._stack.currentIndex()
            if actions._developer is not None:
                state['developer_lane'] = actions._developer._stack.currentIndex()
        script = w.findChild(AutomationPage)
        if script is not None and script.device_serial == self.vm.automation_host.serial:
            state['automation'] = asdict(script.editing_state())
            state['automation_control'] = script._control.currentData()
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
        workflow = w.findChild(WorkflowPage)
        if workflow is not None and workflow._dirty:
            return True
        script = w.findChild(AutomationPage)
        if script is not None and script.device_serial == self.vm.automation_host.serial:
            saved = script._definition(script._selected_id)
            expected = (AutomationEditingState(saved.automation_id, saved.name, saved.trigger_prompt_id,
                        saved.script_path, saved.enabled, saved.timeout_ms) if saved else
                        AutomationEditingState(None, "", None, "", False))
            if script.editing_state() != expected:
                return True
        prompt = w._prompt_editor
        if prompt is not None and prompt.has_unsaved_fields():
            return True
        preferences_page = w._cached_preferences_page
        prefs = (preferences_page.findChild(PreferencesEditor)
                 if preferences_page is not None and self.vm.draft is not None
                 and preferences_page.property('draftIdentity') == id(self.vm.draft)
                 and not preferences_page.property('discardEdits') else None)
        if prefs is not None and draft is not None and prefs.values() != (
            draft.config['lighting'], draft.config['haptic'], draft.config['display']
        ):
            return True
        macro_page = w.findChild(QScrollArea, 'deviceKeySequencesPage')
        if macro_page is not None and (self.vm.draft is None
                or macro_page.property('draftIdentity') != id(self.vm.draft)
                or macro_page.property('macroId') != w._selected_macro_id):
            macro_page = None
        macro = macro_page.findChild(MacroEditor) if macro_page is not None else None
        name = macro_page.findChild(QLineEdit, 'macroNameEditor') if macro_page is not None else None
        if macro is not None and name is not None and draft is not None:
            saved = draft.macro(w._selected_macro_id)
            if name.text() != saved.get('name', '') or macro.steps() != saved.get('steps', []):
                return True
        return any(d.is_dirty for d in (*w._screen_icon_drafts.values(), *w._screen_glyph_drafts.values()))

    def restore_if_ready(self):
        if not self._pending or self._restoring or self.restart_prepared:
            return
        # The connection transition defers page rendering. Restore after its
        # finished signal, before consuming or deleting any saved editor inputs.
        if self.window._connection_transition_model is not None:
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
            target_page = state.get('page', 'overview')
            # Cached editors can contain edits even while the user is on Settings.
            # Restore each in its own page before returning to the original page.
            for page, fields in (
                ('actions', ('automation', 'automation_control', 'workflow', 'actions_section', 'developer_lane')),
                ('lighting', ('preferences',)), ('sequences', ('macro',)),
                ('prompts', ('prompt',)), ('overview', ('mapping',)),
            ):
                payload = {key: state[key] for key in fields if key in state}
                if not payload:
                    continue
                w._update_workspace_restore = payload
                self.vm.navigate(page)
                if w._update_workspace_restore:
                    w.render(self.vm.model)
                if w._update_workspace_restore:
                    raise ValueError('编辑器尚未就绪，草稿仍保留。')
            self.vm.navigate(target_page)
            self.store.clear()
            self._pending = None
            self._disconnect_restore_signal()
            target_version = state.get('update_target_version')
            if target_version == __version__:
                self._recovery_message = '更新完成，已恢复编辑现场；尚未写入设备。'
            elif target_version:
                self._recovery_message = '仍在运行原版本，已恢复编辑现场；可重新尝试更新。'
            else:
                self._recovery_message = '已恢复更新前的本地草稿；尚未写入设备。'
            self.show_status(self.status)
        except (OSError, ValueError, TypeError) as exc:
            self._recovery_message = f'草稿保留在本地，暂无法恢复：{exc}'
            self.show_status(self.status)
        finally:
            self._restoring = False

    def _schedule_restore(self, *_args) -> None:
        QTimer.singleShot(0, self.restore_if_ready)

    def _disconnect_restore_signal(self) -> None:
        if not self._restore_signal_connected:
            return
        try:
            self.vm.changed.disconnect(self._schedule_restore)
            self.window._connection_fade.finished.disconnect(self._schedule_restore)
        except (RuntimeError, TypeError):
            pass
        self._restore_signal_connected = False
