from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QScrollArea

from controller_config.firmware_release import RemoteFirmwareState
from controller_config.firmware_update import FirmwarePackage, FirmwareUpdateState
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


class LayoutEvents(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Hide, QEvent.Show, QEvent.ParentChange, QEvent.Move}:
            self.events.append(event.type().name)
        return False


@pytest.mark.parametrize('download', [False, True], ids=['device-transfer', 'online-download'])
def test_progress_updates_do_not_detach_or_replace_scrolled_page(qtbot, contract, download):
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    package = FirmwarePackage(Path('fixture.json'), Path('fixture.bin'), contract.product_id,
                              'WMP-S3-MATRIX12-POWER-V2', '0.3.0', 1136864, '')
    try:
        if download:
            vm._set_remote_firmware(replace(vm.remote_firmware,
                state=RemoteFirmwareState.DOWNLOADING, total_size=package.size))
        else:
            vm._set_firmware_update(replace(vm.firmware_update,
                state=FirmwareUpdateState.TRANSFERRING, package=package))
        vm.navigate('firmware')
        scroll = window.findChild(QScrollArea, 'firmwareMaintenanceScroll')
        qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 500)
        scroll.verticalScrollBar().setValue(500)
        qtbot.wait(100)
        page = scroll.widget()
        progress = window.findChild(QProgressBar, 'remoteFirmwareProgress' if download else 'firmwareProgress')
        before = page.pos()
        events = LayoutEvents()
        scroll.installEventFilter(events)
        for count in [851968, 852480, 999936, 1000448]:
            if download:
                vm._set_remote_firmware(replace(vm.remote_firmware, received_size=count,
                    message=f'正在下载固件：{count} / {package.size} 字节'))
            else:
                vm._set_firmware_update(replace(vm.firmware_update, received_size=count,
                    in_flight_size=512, message=f'正在传输固件：{count} / {package.size} 字节'))
            qtbot.wait(30)
        assert events.events == [], f'Progress caused visible layout resets: {events.events}'
        assert scroll.widget() is page
        assert page.pos() == before
        assert scroll.verticalScrollBar().value() == 500
        assert progress.value() == 88
        assert '1000448 / 1136864' in progress.format()
        assert any(
            '1000448' in label.text() and '1136864' in label.text()
            for label in page.findChildren(QLabel)
        )
        if not download:
            assert not window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            for state in [FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.COMPLETED]:
                vm._set_firmware_update(replace(vm.firmware_update, state=state, message=state.value))
                qtbot.wait(50)
                assert scroll.verticalScrollBar().value() == min(500, scroll.verticalScrollBar().maximum())
                assert events.events == []
            vm._set_firmware_update(replace(vm.firmware_update,
                state=FirmwareUpdateState.FAILED, message='传输失败', technical='设备断开'))
            qtbot.wait(100)
            assert any(label.text() == '设备断开' for label in scroll.widget().findChildren(QLabel))
            assert window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            assert scroll.verticalScrollBar().value() == min(500, scroll.verticalScrollBar().maximum())
    finally:
        vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.IDLE)
        vm.shutdown()
