"""A real UI-to-transaction flow against the supported Demo device, not hardware."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget

from controller_config.firmware_update import FirmwareUpdateState
from controller_config.firmware_release import RemoteFirmwareState
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from test_firmware_release import (
    FakeReleaseSource, RecordingDemoGateway, _bundle, _release,
    official_demo_release, trust_test_signing_key,
)


def test_dot_download_confirm_transfer_reconnect_and_clear(qtbot, contract):
    source = FakeReleaseSource()
    gateway = RecordingDemoGateway(contract, 'ready')
    vm = MainViewModel(gateway, contract, firmware_release_source=source)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    # Demo firmware reports the requested version as its build identifier.
    bundle = _bundle(contract, build_id='0.3.0-alpha.2')
    source.release_found.emit(_release(bundle))
    button = window._nav_buttons['settings']
    assert button.firmware_notice_visible()
    button.click()
    assert vm.page == 'settings'
    assert window._settings_section == 'system'
    assert source.downloads == []
    qtbot.waitUntil(lambda: bool(window._content.findChildren(QPushButton, 'settingsGroup')))
    firmware_tab = next(
        item for item in window._content.findChildren(QPushButton, 'settingsGroup')
        if item.text() == window._language_manager.translate('固件更新')
    )
    firmware_tab.click()
    assert vm.page == 'firmware'
    focus = next(
        card
        for card in reversed(window.findChildren(QWidget, 'firmwareUpdateFocusCard'))
        if card.findChild(QLabel, 'remoteFirmwareReleaseSummary') is not None
    )
    assert focus.findChild(QLabel, 'firmwareCurrentVersion') is not None
    assert focus.findChild(QLabel, 'remoteFirmwareReleaseSummary') is not None
    assert focus.findChild(QPushButton, 'installRemoteFirmware') is not None
    assert window.findChild(QPushButton, 'remoteFirmwareDetailsToggle') is not None

    def choose(choice):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(choice).click()

    qtbot.waitUntil(lambda: window.findChild(QPushButton, 'installRemoteFirmware') is not None)
    window.findChild(QPushButton, 'installRemoteFirmware').click()
    assert len(source.downloads) == 1
    QTimer.singleShot(0, lambda: choose(QMessageBox.Cancel))
    source.download_completed.emit(bundle)
    window._confirm_downloaded_remote_firmware()
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert window.findChild(QPushButton, 'startFirmwareUpdate').text() == window._language_manager.translate('安装最新版固件…')
    assert not any(c.name.startswith('FW_') for c in gateway.commands)

    # Cancelling the automatic confirmation leaves one clear retry action.
    assert not any(c.name.startswith('FW_') for c in gateway.commands)
    QTimer.singleShot(0, lambda: choose(QMessageBox.Yes))
    window.findChild(QPushButton, 'startFirmwareUpdate').click()
    qtbot.waitUntil(lambda: vm.firmware_update.state in {
        FirmwareUpdateState.COMPLETED, FirmwareUpdateState.FAILED,
    }, timeout=10000)
    assert vm.firmware_update.state is FirmwareUpdateState.COMPLETED
    names = [c.name for c in gateway.commands if c.name.startswith('FW_')]
    assert names.index('FW_STATUS') < names.index('FW_BEGIN') < names.index('FW_DATA') < names.index('FW_END')
    assert names[-1] == 'FW_STATUS'
    assert vm.model.snapshot.versions['build_id'] == bundle.manifest['build_id']
    assert gateway._firmware_update['running_partition'] == 'ota_1'
    assert gateway._firmware_update['rollback_pending'] is False
    assert vm.remote_firmware.state is RemoteFirmwareState.CURRENT
    assert not button.firmware_notice_visible()
    assert window.findChild(QPushButton, 'startFirmwareUpdate') is None
