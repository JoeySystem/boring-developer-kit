from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox, QPushButton

from controller_config.i18n import LanguageManager, SIMPLIFIED_CHINESE
from controller_config.onboarding import (
    ONBOARDING_COMPLETED_KEY,
    ONBOARDING_STEPS,
    OnboardingDialog,
)
from controller_config.models import AppState, ScreenModel
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from notice_helpers import answer_information


def _settings(tmp_path) -> QSettings:
    return QSettings(
        str(tmp_path / "onboarding.ini"),
        QSettings.Format.IniFormat,
    )


def _window(qtbot, qapp, contract, settings: QSettings) -> MainWindow:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    language = LanguageManager(
        qapp,
        settings=settings,
        initial_language=SIMPLIFIED_CHINESE,
        persist=False,
    )
    window = MainWindow(
        view_model,
        language_manager=language,
        onboarding_settings=settings,
    )

    def cleanup(_window):
        view_model.screen_icon.attach(None)
        view_model.screen_glyphs.attach(None)
        view_model.shutdown()

    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    return window


def test_first_visible_window_offers_tasks_without_forcing_ai_setup(qtbot, qapp, contract, tmp_path):
    from controller_config.ai_setup import AI_SETUP_COMPLETED_KEY
    from controller_config.views.ai_setup import AISetupDialog

    settings = _settings(tmp_path)
    window = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert window._ai_setup_dialog is None
    window._show_ai_setup()
    dialog = window._ai_setup_dialog
    assert isinstance(dialog, AISetupDialog)
    assert dialog.isModal()
    assert not dialog._next.isEnabled()
    dialog._choices["codex"].setChecked(True)
    dialog._choices["claude"].setChecked(True)
    assert dialog.selected() == ["codex", "claude"]
    dialog.reject()
    assert not settings.value(AI_SETUP_COMPLETED_KEY, False, type=bool)
    second = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert second._ai_setup_dialog is None
    second._show_ai_setup()
    assert second._ai_setup_dialog.selected() == ["codex", "claude"]
    second._ai_setup_dialog.reject()
    settings.setValue(AI_SETUP_COMPLETED_KEY, True)
    third = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert third._ai_setup_dialog is None


def test_later_stops_automatic_setup_but_manual_entry_still_reopens(
    qtbot, qapp, contract, tmp_path
):
    from controller_config.ai_setup import AI_SETUP_DEFERRED_KEY
    from controller_config.views.ai_setup import AISetupDialog

    settings = _settings(tmp_path)
    window = _window(qtbot, qapp, contract, settings)
    window._show_ai_setup()
    dialog = window._ai_setup_dialog
    assert isinstance(dialog, AISetupDialog)
    dialog._choices["codex"].setChecked(True)
    qtbot.mouseClick(
        dialog.findChild(QPushButton, "aiSetupLater"),
        Qt.MouseButton.LeftButton,
    )
    assert settings.value(AI_SETUP_DEFERRED_KEY, False, type=bool)

    second = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert second._ai_setup_dialog is None

    second._show_ai_setup()
    assert isinstance(second._ai_setup_dialog, AISetupDialog)
    assert second._ai_setup_dialog.selected() == ["codex"]


def test_guide_entry_lives_in_settings_and_reopens_animated_device_guide(
    qtbot, qapp, contract, tmp_path
) -> None:
    # The connected Mode page has no book icon any more (Mode页 定稿 v1); the guide is the
    # 开机与操作说明 tile under Settings › 设备与连接. The disconnected page keeps its icon
    # (test_disconnected_home_keeps_book_guide_entry).
    settings = _settings(tmp_path)
    settings.setValue(ONBOARDING_COMPLETED_KEY, True)
    window = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert window._onboarding_dialog is None
    qtbot.waitUntil(lambda: window._view_model.draft is not None)
    assert window.findChild(QPushButton, "openOnboardingGuide") is None
    assert window.findChild(QPushButton, "openCompanionGuide") is None
    assert window.findChild(QPushButton, "openDetailedGuide") is None

    qtbot.mouseClick(window._nav_buttons["settings"], Qt.MouseButton.LeftButton)
    window._select_settings_section("device")
    for language, expected in (
        ("zh_CN", "开机与操作说明"),
        ("en_US", "Power-on & controls guide"),
        ("ja_JP", "電源と操作のガイド"),
    ):
        window._language_manager.set_language(language)
        qtbot.waitUntil(lambda: any(
            button.isVisible() and button.accessibleName() == expected
            for button in window.findChildren(QPushButton, "openOnboardingGuide")))
    window._language_manager.set_language("zh_CN")

    qtbot.waitUntil(lambda: any(
        button.isVisible() for button in window.findChildren(QPushButton, "openOnboardingGuide")))
    reopen = next(button for button in window.findChildren(QPushButton, "openOnboardingGuide")
                  if button.isVisible())
    qtbot.mouseClick(reopen, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is not None, timeout=1000)
    dialog = window._onboarding_dialog
    assert isinstance(dialog, OnboardingDialog)
    assert dialog.step_index == 0
    assert dialog.player.error_message
    assert "社区源码版" in dialog._cue.text()


def test_disconnected_home_keeps_book_guide_entry(
    qtbot, qapp, contract, tmp_path
) -> None:
    settings = _settings(tmp_path)
    settings.setValue(ONBOARDING_COMPLETED_KEY, True)
    window = _window(qtbot, qapp, contract, settings)
    window._view_model._set(ScreenModel(AppState.NO_DEVICE, "尚未发现 BORING 设备"))

    reopen = window.findChild(QPushButton, "openOnboardingGuide")
    assert reopen is not None
    assert reopen.text() == ""
    assert not reopen.icon().isNull()
    qtbot.mouseClick(reopen, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is not None, timeout=1000)
    assert isinstance(window._onboarding_dialog, OnboardingDialog)


def test_guide_page_navigation_still_respects_missing_device(
    qtbot, qapp, contract, tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    settings.setValue(ONBOARDING_COMPLETED_KEY, True)
    window = _window(qtbot, qapp, contract, settings)
    messages = []
    answer_information(monkeypatch, lambda *args: messages.append(args[2]),
    )
    assert window._view_model.draft is None
    window._open_onboarding_page("lighting")
    assert window._view_model.page == "overview"
    assert messages == ["连接并读取设备后，可调整外观与反馈。"]
    window._open_onboarding_page("settings")
    assert window._view_model.page == "settings"
