"""A real UI-to-transaction flow against the supported Demo device, not hardware."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

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
    assert vm.page == 'firmware'
    visible_copy = {
        label.text()
        for label in window.findChildren(QLabel)
    }
    assert '在线更新（推荐）' in visible_copy
    assert '从文件安装（高级）' in visible_copy
    assert window.findChild(QPushButton, 'firmwareDeviceDetailsDetailsToggle') is not None
    qtbot.waitUntil(lambda: window.findChild(QPushButton, 'downloadRemoteFirmware') is not None)
    window.findChild(QPushButton, 'downloadRemoteFirmware').click()
    assert len(source.downloads) == 1
    source.download_completed.emit(bundle)
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert window.findChild(QPushButton, 'startFirmwareUpdate').text() == '安装下载的固件更新'
    assert not any(c.name.startswith('FW_') for c in gateway.commands)

    def choose(choice):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        dialog.button(choice).click()

    # Cancel at the actual confirmation UI: no transaction reaches the device.
    QTimer.singleShot(0, lambda: choose(QMessageBox.Cancel))
    window.findChild(QPushButton, 'startFirmwareUpdate').click()
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
