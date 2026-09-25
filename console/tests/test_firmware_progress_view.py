from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QScrollArea

from controller_config.firmware_release import RemoteFirmwareState
from controller_config.firmware_update import FirmwarePackage, FirmwareUpdateState
from controller_config.appearance import V4_STYLE, V4_TOKENS
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import APP_STYLE, MainWindow


class LayoutEvents(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Hide, QEvent.ParentChange}:
            self.events.append(event.type().name)
        return False


def test_interactive_accent_and_progress_use_pantone_1505_screen_value():
    assert V4_TOKENS['control'] == '#FF6A00'
    assert 'QPushButton#primary, QPushButton[buttonRole="primary"] { background: #FF6A00;' in V4_STYLE
    assert 'QProgressBar::chunk { background: #FF6A00;' in V4_STYLE
    assert 'QPushButton#settingsGroup[selected="true"] { background: #2B2A27; border-left: 3px solid #FF6A00;' in V4_STYLE
    assert 'QPushButton[buttonRole="primary"] {\n    background: #FF6A00;' in APP_STYLE


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
        qtbot.waitUntil(lambda: window.findChild(QScrollArea, 'firmwareMaintenanceScroll') is not None)
        scroll = window.findChild(QScrollArea, 'firmwareMaintenanceScroll')
        advanced = window.findChild(QPushButton, 'firmwarePackageToggle')
        if advanced is not None and not advanced.isChecked():
            advanced.click()
        window.resize(1280, 560)
        qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
        target = min(180, scroll.verticalScrollBar().maximum())
        scroll.verticalScrollBar().setValue(target)
        qtbot.wait(100)
        page = scroll.widget()
        progress = window.findChild(QProgressBar, 'remoteFirmwareProgress' if download else 'firmwareProgress')
        focus = window.findChild(QObject, 'firmwareUpdateFocusCard')
        assert progress is not None
        assert focus is not None and focus.isAncestorOf(progress)
        assert progress.property('firmwareAction') is True
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
        expected_scroll = min(target, scroll.verticalScrollBar().maximum())
        actual_scroll = scroll.verticalScrollBar().value()
        assert abs(actual_scroll - expected_scroll) <= 8
        assert page.y() == -actual_scroll
        assert progress.value() == 88
        assert progress.format() == ('正在下载固件 · %p%' if download else '正在安装固件 · %p%')
        assert any(
            '1000448' in label.text() and '1136864' in label.text()
            for label in page.findChildren(QLabel)
        )
        if not download:
            assert not window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            for state in [FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.COMPLETED]:
                vm._set_firmware_update(replace(vm.firmware_update, state=state, message=state.value))
                qtbot.wait(50)
                assert scroll.verticalScrollBar().value() == min(target, scroll.verticalScrollBar().maximum())
                assert events.events == []
            completed = window.findChild(QProgressBar, 'firmwareProgress')
            assert completed is not None and completed.value() == 100
            assert completed.format() == '固件更新完成 · %p%'
            assert window.findChild(QObject, 'firmwareTransactionCard').isHidden()
            before_failure_scroll = scroll.verticalScrollBar().value()
            vm._set_firmware_update(replace(vm.firmware_update,
                state=FirmwareUpdateState.FAILED, message='传输失败', technical='设备断开'))
            qtbot.wait(100)
            assert any(label.text() == '设备断开' for label in scroll.widget().findChildren(QLabel))
            assert window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            assert abs(
                scroll.verticalScrollBar().value()
                - min(before_failure_scroll, scroll.verticalScrollBar().maximum())
            ) <= 8
    finally:
        vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.IDLE)
        vm.shutdown()
