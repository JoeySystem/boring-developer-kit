"""A quiet, device-scoped shortcut to the existing firmware update page."""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy
from controller_config.firmware_release import RemoteFirmwareState, FirmwareReleaseError, release_is_newer
from controller_config.update_reminders import UpdateReminders
from controller_config.models import AppState
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY


class FirmwareReminderUi(QObject):
    def __init__(self, window, *, reminders=None):
        super().__init__(window)
        self.window = window
        self.vm = window._view_model
        self.reminders = reminders if reminders is not None else UpdateReminders()
        self._manual_scope = None
        self._acknowledged: set[tuple[str, str]] = set()
        self.row = window._firmware_update_footer
        layout = QHBoxLayout(self.row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.message = QLabel(objectName='firmwareUpdateNotice')
        self.message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.message.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.action = QPushButton(objectName='openFirmwareUpdateNotice')
        self.action.clicked.connect(self.open_update)
        self.snooze = QPushButton(objectName='snoozeFirmwareUpdate')
        self.snooze.clicked.connect(self.snooze_update)
        layout.addWidget(self.message, 1)
        layout.addWidget(self.action)
        layout.addWidget(self.snooze)
        self.vm.changed.connect(self.refresh)
        window._language_manager.language_changed.connect(self.refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(60000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def context(self):
        identity = self.vm.remote_firmware_device
        snapshot = self.vm.model.snapshot
        if identity is None or snapshot is None or self.vm.firmware_origin != "official" or self.vm.remote_firmware.restoration:
            return None
        scope = 'firmware:' + ':'.join(identity)
        self.reminders.clear_target(scope, f"{snapshot.versions.get('firmware')}|{snapshot.versions.get('build_id')}")
        release = self.vm.remote_firmware.release
        if release is None:
            return None
        try:
            if not release_is_newer(release, snapshot):
                return None
        except FirmwareReleaseError:
            return None
        return scope, f'{release.version}|{release.build_id}'

    def refresh(self, *_):
        context = self.context()
        if context is None:
            self.window._nav_buttons['settings'].set_update_notice(firmware=False)
            self.row.hide()
            snapshot = self.vm.model.snapshot
            current_scope = ('firmware:' + ':'.join(str(snapshot.identity[k])
                             for k in ('serial', 'product_id', 'hardware_id'))
                             if snapshot is not None and self.vm.model.state is AppState.READY else None)
            if current_scope != self._manual_scope:
                self._manual_scope = None
            return
        scope, target = context
        remote = self.vm.remote_firmware
        # Progress and failures remain in their existing transaction view.
        visible = remote.state in {RemoteFirmwareState.AVAILABLE, RemoteFirmwareState.DOWNLOADED}
        visible = visible and not self.vm.firmware_update.is_busy
        visible = visible and context not in self._acknowledged
        if remote.state == RemoteFirmwareState.AVAILABLE:
            visible = visible and (self._manual_scope == scope or not self.reminders.is_snoozed(scope, target))
        translate = self.window._language_manager.translate
        text = translate('设备固件已下载，等待安装' if remote.state == RemoteFirmwareState.DOWNLOADED else '设备固件有更新')
        self.message.setText(text)
        self.message.setToolTip(f'{text}\n{remote.release.version} · {remote.release.build_id}')
        self.action.setText(translate('查看安装' if remote.state == RemoteFirmwareState.DOWNLOADED else '查看更新'))
        self.snooze.setText(translate('稍后提醒'))
        self.snooze.setToolTip(translate('同一版本 24 小时内不再主动提醒，仍可手动检查。'))
        self.snooze.setVisible(remote.state == RemoteFirmwareState.AVAILABLE)
        self.window._nav_buttons['settings'].set_update_notice(firmware=bool(visible))
        self.row.setVisible(visible and self.vm.page in {'settings', 'firmware'})

    def reveal(self):
        context = self.context()
        if context:
            self._manual_scope = context[0]
        else:
            snapshot = self.vm.model.snapshot
            if snapshot is not None:
                identity = tuple(str(snapshot.identity[k]) for k in ('serial', 'product_id', 'hardware_id'))
                self._manual_scope = 'firmware:' + ':'.join(identity)
        self.refresh()

    def snooze_update(self):
        context = self.context()
        if context and self.vm.remote_firmware.state == RemoteFirmwareState.AVAILABLE:
            self.reminders.snooze(*context)
            self._manual_scope = None
            self.refresh()

    def open_update(self):
        context = self.context()
        if context is None or self.vm.firmware_update.is_busy:
            return
        self._acknowledged.add(context)
        self._manual_scope = None
        self.refresh()
        self.window._navigate('firmware')
