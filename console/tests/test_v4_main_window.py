from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea

from controller_config import __version__
from controller_config.codex_usage import (
    CodexQuotaBucket, CodexQuotaWindow, CodexUsageSnapshot, CodexUsageStatus,
)
from controller_config.models import AppState, ScreenModel
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.v4_widgets import UsageRings, V4Card


@pytest.fixture
def console(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.state is AppState.READY)
    yield window, vm
    vm.shutdown()
    window.hide()


def test_v4_selected_editor_fits_and_keeps_complete_keycaps(console, qtbot):
    window, vm = console
    window._select_physical_control("key.9")
    scroll = window.findChild(QScrollArea, "overviewScroll")
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() == 0)
    editor = window.findChild(V4Card, "mappingEditorCard")
    sync = window.findChild(V4Card, "syncSummaryCard")
    assert editor is not None and sync is not None
    qtbot.waitUntil(lambda: editor.isVisible() and sync.isVisible()
                    and not editor.geometry().intersects(sync.geometry()))
    keys = [b for b in window.findChildren(QPushButton) if b.objectName() == "controlKey"]
    assert len(keys) == 12
    for key in keys:
        assert key.accessibleName()
        expected_ratio = 131 / 64 if key.property("controlId") == "key.8" else 1
        assert abs(key.width() / key.height() - expected_ratio) < .05
        assert all(not label.isVisible() for label in key.findChildren(QLabel, "controlId"))
        if key.property("role") == "agent":
            assert key.findChild(QLabel, "keycapInscription") is None
    assert not vm.draft.is_dirty


def test_home_quota_update_does_not_rebuild_editor_or_fake_missing_window(console):
    window, vm = console
    window._select_physical_control("key.9")
    editor = window.findChild(V4Card, "mappingEditorCard")
    snapshot = CodexUsageSnapshot(
        CodexUsageStatus.AVAILABLE,
        (CodexQuotaBucket("codex", None, None,
                          CodexQuotaWindow(10, 300, None),
                          CodexQuotaWindow(40, 10080, None)),),
    )
    window._update_home_usage(snapshot)
    rings = window.findChild(UsageRings, "homeUsageRings")
    assert (rings.seven_day, rings.five_hour) == (60, 90)
    assert window.findChild(V4Card, "mappingEditorCard") is editor
    window._update_home_usage(CodexUsageSnapshot.unavailable())
    assert rings.seven_day is None and rings.five_hour is None
    assert not vm.draft.is_dirty


def test_cocoa_profile_and_action_popups_follow_moved_reopened_window(console, qapp, qtbot):
    import sys
    if sys.platform != "darwin" or qapp.platformName() != "cocoa":
        pytest.skip("Requires native Cocoa popup coordinates")
    import AppKit
    import objc
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QComboBox
    window, vm = console
    screen_height = qapp.primaryScreen().geometry().height()

    def native_rect(popup):
        view = objc.objc_object(c_void_p=int(popup.winId()))
        rect = view.window().frame()
        return (rect.origin.x, screen_height - rect.origin.y - rect.size.height,
                rect.size.width, rect.size.height)

    for position in (QPoint(0, 30), QPoint(150, 100)):
        window.hide()
        window.move(position)
        window.show()
        qtbot.waitExposed(window)
        AppKit.NSApp.delegate().applicationDidBecomeActive_(None)
        qtbot.wait(50)
        button = window.findChild(QPushButton, "profilePill")
        menu = button.menu()
        button.showMenu()
        qtbot.waitUntil(menu.isVisible)
        anchor = button.mapToGlobal(QPoint(0, button.height()))
        x, y, width, height = native_rect(menu)
        assert abs(x - anchor.x()) < 12
        # Qt may open upward when space below the button is insufficient.
        assert min(abs(y - anchor.y()), abs(y + height - (anchor.y() - button.height()))) < 12
        AppKit.NSApp.delegate().applicationDidResignActive_(None)
        qtbot.waitUntil(lambda: not menu.isVisible())

        window._select_physical_control("key.9")
        toggle = window.findChild(QPushButton, "shortcutManualToggle")
        toggle.setChecked(True)
        combo = window.findChild(QComboBox, "actionTypeEditor")
        scroll = window.findChild(QScrollArea, "mappingEditorBody")
        scroll.ensureWidgetVisible(combo)
        qtbot.wait(50)
        AppKit.NSApp.delegate().applicationDidBecomeActive_(None)
        qtbot.wait(30)
        combo.showPopup()
        popup = combo.view().window()
        qtbot.waitUntil(popup.isVisible)
        anchor = combo.mapToGlobal(QPoint(0, 0))
        x, y, width, height = native_rect(popup)
        assert abs(x - anchor.x()) < 12
        assert y <= anchor.y() + combo.height() + 12 and y + height >= anchor.y()
        AppKit.NSApp.delegate().applicationDidResignActive_(None)
        qtbot.waitUntil(lambda: not popup.isVisible())
    assert not vm.draft.is_dirty


def test_settings_group_uses_application_version_and_reset_guard(console):
    window, vm = console
    window._select_settings_section("about")
    assert window.findChild(QLabel, "consoleVersion").text() == __version__
    assert window._language_buttons == {}
    window._select_settings_section("device")
    reset = window.findChild(QPushButton, "settingsFactoryReset")
    assert reset.isEnabled()
    readonly = replace(vm.model.snapshot, compatibility={"write": False})
    assert not window._factory_reset_available(readonly)
    assert not window._factory_reset_available(None)


def test_authentication_failure_is_not_presented_as_disconnected(console):
    window, _ = console
    window.render(ScreenModel(AppState.AUTHENTICITY_FAILED, "证书校验失败"))
    assert "无法确认是 BORING 设备" in window._device_auth_summary.text()
    assert "UNTRUSTED" in window._device_auth_summary.text()
    assert window._device_auth_summary.property("trust") == "untrusted"


def test_settings_diagnostics_keeps_viewmodel_page_lifecycle(console):
    window, vm = console
    window._select_settings_section("diagnostics")
    assert vm.page == "diagnostics"
    assert window.findChild(V4Card, "settingsFocusCard") is not None
    window._select_settings_section("general")
    assert vm.page == "settings"
    assert window.findChild(QScrollArea, "settingsScroll") is not None


def test_settings_groups_obey_firmware_transaction_lock(console, qtbot):
    window, vm = console
    vm.navigate("firmware")
    vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.TRANSFERRING)
    try:
        window.render(vm.model)
        qtbot.waitUntil(lambda: len(window.findChildren(QPushButton, "settingsGroup")) == 5)
        groups = window.findChildren(QPushButton, "settingsGroup")
        for button in groups:
            assert button.isEnabled() is bool(button.property("selected"))
        assert not window._nav_buttons["overview"].isEnabled()
    finally:
        vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.IDLE)


@pytest.mark.parametrize("previous_session", [False, True])
def test_old_ble_firmware_shows_usb_recovery_and_blocks_device_write(qtbot, contract, previous_session):
    from controller_config.protocol.bootstrap import BootstrapKind
    from test_write_transaction import FakeWriteGateway
    from controller_config.transport.demo import _power_v2_snapshot
    gateway = FakeWriteGateway()
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    try:
        if previous_session:
            snapshot = _power_v2_snapshot(contract, read_only=False)
            gateway.snapshot_ready.emit(snapshot)
            window._select_physical_control("key.1")
        gateway.failure.emit(BootstrapKind.FIRMWARE_UPDATE_REQUIRED,
            '请通过 USB 更新固件后再使用蓝牙配置', '请连接 USB 数据线后重新扫描，认证后进入设置 → 固件维护。')
        assert vm.model.state is AppState.FIRMWARE_UPDATE_REQUIRED
        assert not vm._reconnect_timer.isActive()
        assert window._connection_message.isVisible()
        assert "USB" in window._connection_message.text()
        if previous_session:
            # An offline editor is intentional; it cannot write to the device.
            assert not window.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
            with pytest.raises(ValueError, match='不可写入'):
                vm.prepare_device_write()
        else:
            qtbot.waitUntil(lambda: any(b.isVisible() and b.text() == '连接 USB 后重新扫描'
                for b in window.findChildren(QPushButton)))
            assert vm.draft is None
        assert gateway.commands == []
    finally:
        vm.shutdown()
