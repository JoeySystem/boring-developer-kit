"""固件更新没做成 (design ticket 第 7 件): the ring stays, greyed, last dot orange, triangle in the middle."""
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton

from controller_config.firmware_update import FirmwarePackage, FirmwareUpdateState
from controller_config.style import tokens
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.ui import ProgressRingRow


def test_failed_update_keeps_the_ring_with_an_orange_triangle(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    package = FirmwarePackage(Path('fixture.json'), Path('fixture.bin'), contract.product_id,
                              'WMP-S3-MATRIX12-POWER-V2', '0.3.0', 1000000, '')
    vm._firmware.set_transaction(replace(vm.firmware_update, state=FirmwareUpdateState.FAILED,
                                         package=package, received_size=620000,
                                         message='设备断开，固件没有装完。'))
    vm.navigate('firmware')
    qtbot.waitUntil(lambda: window.findChild(ProgressRingRow, 'firmwareFailedProgress') is not None)
    row = window.findChild(ProgressRingRow, 'firmwareFailedProgress')
    ring = row.ring
    assert ring.state() == 'failed'
    assert ring.lit_dots() == 13                      # 62% of 20, rounded up
    colours = ring.dot_colours()
    assert colours[12] == QColor(tokens.PROGRESS_RING_FAILED)
    assert colours[0] == QColor(tokens.PROGRESS_RING_FAILED)   # every lit dot (the designer 11-20)
    assert ring.triangle_visible() and ring.number_text() == ''
    assert row.text() == '固件更新没做成'
    retry = window.findChild(QPushButton, 'startFirmwareUpdate')
    assert retry is not None and retry.text() == '重新尝试安装…'
    vm._firmware.set_transaction(replace(vm.firmware_update, state=FirmwareUpdateState.PACKAGE_READY))
    qtbot.waitUntil(lambda: window.findChild(ProgressRingRow, 'firmwareFailedProgress') is None)


def test_failure_that_reports_no_progress_keeps_where_it_stopped(qtbot, contract):
    """the designer 11-20 真屏验收 第 13 条: at 62 % the whole lit part turns orange, even
    when the failure itself comes back with nothing received."""
    vm = MainViewModel(DemoGateway(contract, 'ready'), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    package = FirmwarePackage(Path('fixture.json'), Path('fixture.bin'), contract.product_id,
                              'WMP-S3-MATRIX12-POWER-V2', '0.3.0', 1000000, '')
    vm.navigate('firmware')
    page = window._firmware_page
    qtbot.waitUntil(page._live)
    vm._firmware.set_transaction(replace(vm.firmware_update, state=FirmwareUpdateState.TRANSFERRING,
                                         package=package, received_size=620000))
    qtbot.waitUntil(lambda: page._firmware_reached == 62)
    vm._firmware.set_transaction(replace(vm.firmware_update, state=FirmwareUpdateState.FAILED,
                                         package=package, received_size=0,
                                         message='设备断开，固件没有装完。'))
    qtbot.waitUntil(lambda: window.findChild(ProgressRingRow, 'firmwareFailedProgress') is not None)
    ring = window.findChild(ProgressRingRow, 'firmwareFailedProgress').ring
    assert ring.lit_dots() == 13
    assert all(colour == QColor(tokens.PROGRESS_RING_FAILED) for colour in ring.dot_colours()[:13])
