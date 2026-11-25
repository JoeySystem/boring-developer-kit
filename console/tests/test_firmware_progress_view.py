from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea

from controller_config.style.sheets import application_stylesheet
from controller_config.firmware_release import RemoteFirmwareState
from controller_config.firmware_update import FirmwarePackage, FirmwareUpdateState
from controller_config.appearance import V4_TOKENS
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.ui import DotRow, ProgressRingRow


class LayoutEvents(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in {QEvent.Hide, QEvent.ParentChange}:
            self.events.append(event.type().name)
        return False


def _centre_colour(widget) -> QColor:
    image = widget.grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(widget.width() / 2 * ratio), round(widget.height() / 2 * ratio))


def test_interactive_accent_and_progress_use_pantone_1505_screen_value(qapp):
    # Check what is painted, not the stylesheet text, so the styles can be
    # regrouped or generated from tokens without rewriting this test.
    assert V4_TOKENS['control'] == '#FF6A00'
    accent = QColor(V4_TOKENS['control'])
    style = application_stylesheet()

    primary = QPushButton()
    primary.setProperty('buttonRole', 'primary')
    primary.setStyleSheet(style)
    primary.resize(160, 44)
    assert _centre_colour(primary) == accent

    # Progress itself is a cream ring now (进度条点阵样式 定稿 v1); the orange
    # stays on readings and demos, one row of dots.
    reading = DotRow(12, maximum=1)
    reading.set_value(1)
    reading.resize(reading.sizeHint())
    image = reading.grab().toImage()
    ratio = image.devicePixelRatio()
    assert image.pixelColor(round(3 * ratio), round(reading.height() / 2 * ratio)) == accent


def test_unpublished_firmware_replaces_stale_release_notes_placeholder(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    vm._set_remote_firmware(replace(
        vm.remote_firmware,
        state=RemoteFirmwareState.UNPUBLISHED,
        message='暂无可用的固件发布',
    ))
    vm.navigate('firmware')
    qtbot.waitUntil(
        lambda: window.findChild(QObject, 'firmwareReleaseNotesCard') is not None
    )
    notes = window.findChild(QObject, 'firmwareReleaseNotesCard')
    texts = [label.text() for label in notes.findChildren(QLabel)]
    assert '当前渠道没有可展示的官方版本说明。' in texts
    assert '查看官方版本后，这里会显示该版本的说明。' not in texts


@pytest.mark.parametrize('download', [False, True], ids=['device-transfer', 'online-download'])
def test_progress_updates_keep_the_wall_still_at_the_smallest_window(qtbot, contract, download):
    """design decision: at 960 × 640 nothing scrolls, and no state makes the page taller."""
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.resize(960, 640)
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
            vm._firmware.set_transaction(replace(vm.firmware_update,
                state=FirmwareUpdateState.TRANSFERRING, package=package))
        vm.navigate('firmware')
        qtbot.waitUntil(lambda: window.findChild(QScrollArea, 'firmwareMaintenanceScroll') is not None)
        scroll = window.findChild(QScrollArea, 'firmwareMaintenanceScroll')
        qtbot.wait(50)
        assert scroll.verticalScrollBar().maximum() == 0
        page = scroll.widget()
        focus = window.findChild(QObject, 'firmwareUpdateFocusCard')
        focus_size = focus.size()
        wall_size = page.findChild(QObject, 'firmwareMaintenancePage').size()
        progress = window.findChild(ProgressRingRow, 'remoteFirmwareProgress' if download else 'firmwareProgress')
        assert progress is not None
        assert focus is not None and focus.isAncestorOf(progress)
        assert progress.ring.ring_size() == 48
        # 更新时藏起“从文件安装”“恢复官方固件”.
        assert not window.findChild(QPushButton, 'selectFirmwarePackage').isVisible()
        events = LayoutEvents()
        scroll.installEventFilter(events)
        for count in [851968, 852480, 999936, 1000448]:
            if download:
                vm._set_remote_firmware(replace(vm.remote_firmware, received_size=count,
                    message=f'正在下载固件：{count} / {package.size} 字节'))
            else:
                vm._firmware.set_transaction(replace(vm.firmware_update, received_size=count,
                    in_flight_size=512, message=f'正在传输固件：{count} / {package.size} 字节'))
            qtbot.wait(30)
        assert events.events == [], f'Progress caused visible layout resets: {events.events}'
        assert scroll.widget() is page
        assert scroll.verticalScrollBar().maximum() == 0
        assert progress.ring.value() == 88
        assert progress.ring.number_text() == '88'
        assert progress.text() == ('正在下载固件' if download else '正在安装固件')
        assert any(
            '1000448' in label.text() and '1136864' in label.text()
            for label in page.findChildren(QLabel)
        )
        if not download:
            assert not window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            assert window.findChild(QObject, 'firmwareTransactionCard').isVisible()
            for state in [FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.COMPLETED]:
                vm._firmware.set_transaction(replace(vm.firmware_update, state=state, message=state.value))
                qtbot.wait(50)
                assert scroll.verticalScrollBar().maximum() == 0
                assert events.events == []
            completed = window.findChild(ProgressRingRow, 'firmwareProgress')
            assert completed is not None and completed.ring.value() == 100
            assert completed.ring.state() == 'done'
            assert completed.text() == '固件更新完成'
            assert window.findChild(QObject, 'firmwareTransactionCard').isHidden()
            vm._firmware.set_transaction(replace(vm.firmware_update,
                state=FirmwareUpdateState.FAILED, message='传输失败', technical='设备断开'))
            qtbot.wait(100)
            assert window.findChild(QScrollArea, 'firmwareMaintenanceScroll') is scroll
            # The failure shows in the same tiles: same sizes, still no scrolling.
            failed = window.findChild(ProgressRingRow, 'firmwareFailedProgress')
            focus = window.findChild(QObject, 'firmwareUpdateFocusCard')
            assert failed is not None and focus.isAncestorOf(failed)
            assert focus.size() == focus_size
            assert scroll.widget().findChild(QObject, 'firmwareMaintenancePage').size() == wall_size
            assert scroll.verticalScrollBar().maximum() == 0
            assert any(label.text() == '设备断开' for label in scroll.widget().findChildren(QLabel))
            assert window.findChild(QPushButton, 'selectFirmwarePackage').isEnabled()
            assert window.findChild(QPushButton, 'selectFirmwarePackage').isVisible()
    finally:
        vm._firmware.replace_silently(replace(vm.firmware_update, state=FirmwareUpdateState.IDLE))
        vm.shutdown()
