from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea, QWidget

from controller_config import __version__
from controller_config.codex_usage import (
    CodexQuotaBucket, CodexQuotaWindow, CodexUsageSnapshot, CodexUsageStatus,
)
from controller_config.models import AppState, ScreenModel
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.device_silhouette import DeviceModelCanvas
from controller_config.views.main_window import MainWindow
from controller_config.views.v4_widgets import UsageRings, V4Card
from controller_config.i18n import ENGLISH, JAPANESE, SIMPLIFIED_CHINESE


@pytest.fixture
def console(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    window.resize(1280, 800)
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
    def editor_is_laid_out():
        editor = window.findChild(V4Card, "mappingEditorCard")
        sync = window.findChild(V4Card, "syncSummaryCard")
        return (
            editor is not None
            and sync is not None
            and editor.isVisible()
            and (
                not sync.isVisible()
                or not editor.geometry().intersects(sync.geometry())
            )
        )
    qtbot.waitUntil(editor_is_laid_out)
    keys = [b for b in window.findChildren(QPushButton) if b.objectName() == "controlKey"]
    assert len(keys) == 12
    canvas = window.findChild(DeviceModelCanvas, "deviceModelCanvas")
    assert canvas is not None
    assert canvas._preset == "mapping"
    assert canvas._selected_control_id == "key.9"
    assert canvas.modelYaw == -90.0
    for key in keys:
        assert key.accessibleName()
        control_id = key.property("controlId")
        assert key.height() == max(24, round(canvas.control_rect(control_id).height()))
        assert all(not label.isVisible() for label in key.findChildren(QLabel, "controlId"))
        if key.property("role") == "agent":
            assert key.findChild(QLabel, "keycapInscription") is None
    assert not vm.draft.is_dirty


def test_home_quota_update_does_not_rebuild_editor_or_fake_missing_window(console, qtbot):
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
    assert window.findChild(QPushButton, "homeUsageRefresh") is None
    assert window.findChild(V4Card, "mappingEditorCard") is editor
    window._update_home_usage(CodexUsageSnapshot.unavailable())
    assert rings.seven_day is None and rings.five_hour is None
    assert not vm.draft.is_dirty
    for width, height in ((1280, 800), (1920, 1080), (1100, 700), (1280, 800)):
        window.resize(width, height)
        qtbot.wait(40)
        rings = window.findChild(UsageRings, "homeUsageRings")
        assert rings.isVisible(), "Resizing must retain the original quota dial"
        assert rings.width() == rings.height()


def test_cocoa_profile_and_trigger_popups_follow_moved_reopened_window(console, qapp, qtbot):
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
        # Cocoa may align the menu with either edge of the button, while Qt
        # may open upward when space below it is insufficient.
        assert min(
            abs(y - anchor.y()),
            abs(y - (anchor.y() - button.height())),
            abs(y + height - (anchor.y() - button.height())),
        ) < 12
        AppKit.NSApp.delegate().applicationDidResignActive_(None)
        qtbot.waitUntil(lambda: not menu.isVisible())

        window._select_physical_control("joystick.up")
        combo = window.findChild(QComboBox, "triggerSelector")
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
    window._select_settings_section("system")
    assert window.findChild(QLabel, "consoleVersion").text() == __version__
    assert set(window._language_buttons) == {
        SIMPLIFIED_CHINESE, ENGLISH, JAPANESE,
    }
    window._select_settings_section("device")
    reset = window.findChild(QPushButton, "factoryResetDevice")
    assert reset.isEnabled()
    readonly = replace(vm.model.snapshot, compatibility={"write": False})
    assert not window._factory_reset_available(readonly)
    assert not window._factory_reset_available(None)


def test_about_page_shows_the_current_release_summary_in_all_languages(
    console, qtbot
):
    window, _vm = console
    from controller_config.app_release_summary import load_app_release_summary

    for language in (SIMPLIFIED_CHINESE, ENGLISH, JAPANESE):
        first_line = load_app_release_summary(language).highlights[0]
        window._language_manager.set_language(language)
        window._select_settings_section("system")
        qtbot.waitUntil(
            lambda: any(
                card.isVisible()
                for card in window.findChildren(QWidget, "appReleaseSummary")
            )
        )

        card = next(
            card
            for card in window.findChildren(QWidget, "appReleaseSummary")
            if card.isVisible()
        )
        title = card.findChild(QLabel, "appReleaseSummaryTitle")
        assert title is not None and title.text() in {
            "本版本更新", "What’s new", "このバージョンの更新",
        }
        version_range = card.findChild(QLabel, "appReleaseRange")
        assert version_range is not None
        assert version_range.text() == f"0.1.92 → {__version__}"
        highlights = card.findChildren(QLabel, "appReleaseHighlight")
        assert 1 <= len(highlights) <= 4
        assert highlights[0].text().startswith("•  " + first_line)


@pytest.mark.parametrize("page", ["actions", "settings"])
def test_playground_and_settings_remain_model_free(console, qtbot, page):
    window, vm = console
    vm.navigate(page)
    qtbot.wait(50)
    assert not any(
        canvas.isVisible()
        for canvas in window.findChildren(DeviceModelCanvas, "deviceModelCanvas")
    )


def test_authentication_failure_is_not_presented_as_disconnected(console):
    window, _ = console
    window.render(ScreenModel(AppState.AUTHENTICITY_FAILED, "证书校验失败"))
    assert "BORING 身份未确认" in window._device_auth_summary.text()
    assert "UNTRUSTED" in window._device_auth_summary.text()
    assert window._device_auth_summary.property("trust") == "untrusted"


def test_settings_diagnostics_keeps_viewmodel_page_lifecycle(console):
    window, vm = console
    window._select_settings_section("device")
    window.findChild(QPushButton, "openDeviceDiagnostics").click()
    assert vm.page == "diagnostics"
    assert window.findChild(V4Card, "settingsFocusCard") is not None
    selected = [
        button.text()
        for button in window._content.findChildren(QPushButton, "settingsGroup")
        if button.property("selected") is True
    ]
    assert selected == ["设备"]
    window._select_settings_section("system")
    assert vm.page == "settings"
    assert window.findChild(QScrollArea, "settingsScroll") is not None


def test_settings_groups_obey_firmware_transaction_lock(console, qtbot):
    window, vm = console
    vm.navigate("firmware")
    vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.TRANSFERRING)
    try:
        window.render(vm.model)
        qtbot.waitUntil(lambda: len(window.findChildren(QPushButton, "settingsGroup")) == 3)
        groups = window.findChildren(QPushButton, "settingsGroup")
        for button in groups:
            assert button.isEnabled() is bool(button.property("selected"))
        assert not window._nav_buttons["overview"].isEnabled()
    finally:
        vm._firmware_update = replace(vm.firmware_update, state=FirmwareUpdateState.IDLE)


def test_old_ble_firmware_preserves_workspace_and_shows_usb_recovery(console, qtbot):
    from controller_config.protocol.bootstrap import BootstrapKind
    window, vm = console
    vm._on_failure(BootstrapKind.FIRMWARE_UPDATE_REQUIRED,
        '请通过 USB 更新固件后再使用蓝牙配置', '请连接 USB 数据线后重新扫描，认证后进入设置 → 固件维护。')
    qtbot.waitUntil(lambda: any(label.isVisible() and 'USB' in label.text()
        for label in window.findChildren(QLabel)))
    assert vm.model.state is AppState.FIRMWARE_UPDATE_REQUIRED
    assert not vm._reconnect_timer.isActive()
    # The last-read workspace remains available offline, while writing is blocked.
    assert window.findChild(DeviceModelCanvas) is not None
    save = window.findChild(QPushButton, 'saveConfigurationToDevice')
    assert save is not None and not save.isEnabled()
