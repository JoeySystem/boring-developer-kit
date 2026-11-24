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
from controller_config.views.mode_page import ModeStage
from controller_config.official_controls import MATRIX12_CODEX_KEY_NAMES
from controller_config.i18n import translate_ui_text
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
    qtbot.waitUntil(
        lambda: window.findChild(QScrollArea, "overviewScroll") is not None,
        timeout=2000,
    )
    yield window, vm
    vm.shutdown()
    window.hide()


def _stage(window) -> ModeStage:
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def test_v4_selected_editor_fits_and_keeps_complete_keycaps(console, qtbot):
    window, vm = console
    window._select_physical_control("key.9")
    scroll = window.findChild(QScrollArea, "overviewScroll")
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() == 0)
    stage = _stage(window)

    def editor_is_laid_out():
        editor = stage.card()
        return (
            editor is not None
            and editor.objectName() == "mappingEditorCard"
            and editor.isVisible()
            and not stage.card_host.geometry().intersects(stage.bar.geometry())
            and not stage.card_host.geometry().intersects(stage.device.geometry())
        )
    qtbot.waitUntil(editor_is_laid_out)
    device = stage.device
    assert device.selected() == "key.9"
    # The demo device is in Codex: every key carries its whole fixed name.
    assert stage.state().word == "CODEX"
    assert device.labels() == {key: translate_ui_text(name) for key, name in MATRIX12_CODEX_KEY_NAMES.items()}
    for control_id in (f"key.{n}" for n in range(1, 13)):
        assert device.control_at(device.key_rect(control_id).center()) == control_id
    assert not vm.draft.is_dirty


def test_home_quota_update_does_not_rebuild_editor_or_fake_missing_window(console, qtbot, monkeypatch):
    from types import SimpleNamespace
    from controller_config.views.mode_page import ModeStage
    window, vm = console
    window._select_physical_control("key.9")
    editor = window._current_mapping_card()
    usage = CodexUsageSnapshot(
        CodexUsageStatus.AVAILABLE,
        (CodexQuotaBucket("codex", None, None,
                          CodexQuotaWindow(10, 300, None),
                          CodexQuotaWindow(40, 10080, None)),),
    )

    class Background(SimpleNamespace):
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    monkeypatch.setattr(window, "_background_controller",
                        Background(codex_usage_snapshot=usage, claude_usage_snapshot=CodexUsageSnapshot.unavailable()))
    window._refresh_mode_quota()
    stage = next(s for s in window.findChildren(ModeStage) if s.isVisible())
    codex, claude = stage.quota.providers()
    assert [(w.label, w.remaining) for w in codex.windows] == [("5h", 90), ("week", 60)]
    assert claude.windows[0].remaining is None   # not read: a dash, never a made-up number
    assert window._current_mapping_card() is editor
    monkeypatch.setattr(window, "_background_controller",
                        Background(codex_usage_snapshot=CodexUsageSnapshot.unavailable(),
                                   claude_usage_snapshot=CodexUsageSnapshot.unavailable()))
    window._refresh_mode_quota()
    assert all(p.windows[0].remaining is None for p in stage.quota.providers())
    assert not vm.draft.is_dirty


def test_cocoa_trigger_popup_follows_moved_reopened_window(console, qapp, qtbot):
    import sys
    if sys.platform != "darwin" or qapp.platformName() != "cocoa":
        pytest.skip("Requires native Cocoa popup coordinates")
    import AppKit
    import objc
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QComboBox
    window, vm = console
    window._select_configuration_mode('normal')
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
        # The set pill and its menu are gone (Mode页 定稿 v1: 全部方案 replaced them);
        # the trigger popup is the native popup left on this page.

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
    window._select_settings_section("updates")
    assert window.findChild(QLabel, "consoleVersion").text() == __version__
    window._select_settings_section("system")
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
        window._select_settings_section("updates")
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
    assert window._device_auth_summary.text() == "BORING 身份未确认"
    assert window._device_status.kind() == "untrusted"
    assert window._device_status.icon.state() == "error"


def test_settings_device_page_has_no_user_diagnostics_entry(console):
    window, vm = console
    window._select_settings_section("device")
    assert window.findChild(QPushButton, "openDeviceDiagnostics") is None
    assert window.findChild(QWidget, "diagnosticsPage") is None
    assert vm.page == "settings"
    assert window.findChild(QWidget, "settingsWall") is not None
    selected = [
        button.text()
        for button in window._content.findChildren(QPushButton, "settingsGroup")
        if button.property("selected") is True
    ]
    assert selected == ["设备与连接"]
    window._select_settings_section("system")
    assert vm.page == "settings"
    assert window.findChild(QScrollArea, "settingsScroll") is not None


def test_settings_groups_obey_firmware_transaction_lock(console, qtbot):
    window, vm = console
    vm.navigate("firmware")
    vm._firmware.replace_silently(replace(vm.firmware_update, state=FirmwareUpdateState.TRANSFERRING))
    try:
        window.render(vm.model)
        # Five group icons since the Settings rail (the designer 灯光页 v0.2, 2026-11-18);
        # the firmware page lights up the 更新 group.
        qtbot.waitUntil(lambda: len(window.findChildren(QPushButton, "settingsGroup")) == 5)
        groups = window.findChildren(QPushButton, "settingsGroup")
        assert {button.text() for button in groups} == {"设备与连接", "AI 状态灯", "灯光与反馈", "通用", "更新"}
        assert [button.property("settingsSection") for button in groups if button.property("selected")] == ["updates"]
        for button in groups:
            assert button.isEnabled() is bool(button.property("selected"))
        assert not window._nav_buttons["overview"].isEnabled()
    finally:
        vm._firmware.replace_silently(replace(vm.firmware_update, state=FirmwareUpdateState.IDLE))


def test_old_ble_firmware_preserves_workspace_and_shows_usb_recovery(console, qtbot):
    from controller_config.protocol.bootstrap import BootstrapKind
    window, vm = console
    vm._on_failure(BootstrapKind.FIRMWARE_UPDATE_REQUIRED,
        '请通过 USB 更新固件后再使用蓝牙配置', '请连接 USB 数据线后重新扫描，认证后进入设置 → 固件维护。')
    qtbot.waitUntil(lambda: any(label.isVisible() and 'USB' in label.text()
        for label in window.findChildren(QLabel)))
    assert vm.model.state is AppState.FIRMWARE_UPDATE_REQUIRED
    assert not vm._connection.reconnect_timer.isActive()
    # The last-read workspace remains available offline, while writing is blocked.
    stage = _stage(window)
    assert stage.device.labels()
    bar = stage.bar.state()
    assert bar.right == 'offline' and not bar.can_save
    assert not stage.bar.save.isVisible() and not stage.bar.enable.isVisible()
