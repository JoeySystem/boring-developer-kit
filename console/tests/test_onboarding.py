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


def test_first_visible_window_starts_with_multiple_ai_choices(qtbot, qapp, contract, tmp_path):
    from controller_config.ai_setup import AI_SETUP_COMPLETED_KEY
    from controller_config.views.ai_setup import AISetupDialog

    settings = _settings(tmp_path)
    window = _window(qtbot, qapp, contract, settings)
    qtbot.waitUntil(lambda: window._ai_setup_dialog is not None, timeout=1000)
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
    qtbot.waitUntil(lambda: second._ai_setup_dialog is not None)
    assert second._ai_setup_dialog.selected() == ["codex", "claude"]
    second._ai_setup_dialog.reject()
    settings.setValue(AI_SETUP_COMPLETED_KEY, True)
    third = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert third._ai_setup_dialog is None


def test_existing_user_is_not_forced_back_into_optional_ai_setup(
    qtbot, qapp, contract, tmp_path
) -> None:
    from controller_config.ai_setup import AI_SETUP_STARTED_KEY
    from controller_config.voice_setup import voice_provider

    settings = _settings(tmp_path)
    settings.setValue(ONBOARDING_COMPLETED_KEY, True)
    settings.setValue(AI_SETUP_STARTED_KEY, True)
    settings.setValue("ui/voice_input_software", "typeless")
    window = _window(qtbot, qapp, contract, settings)
    qtbot.waitUntil(lambda: window._view_model.draft is not None)

    assert window._ai_setup_dialog is None
    snapshot = window._view_model.model.snapshot
    draft = window._view_model.draft
    assert snapshot is not None
    assert draft.config == snapshot.config
    assert voice_provider(
        settings,
        snapshot.identity["serial"],
        snapshot.active_profile_id,
        "codex",
    ) == "typeless"


def test_home_has_one_book_entry_and_reopens_animated_device_guide(
    qtbot, qapp, contract, tmp_path
) -> None:
    settings = _settings(tmp_path)
    settings.setValue(ONBOARDING_COMPLETED_KEY, True)
    window = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert window._onboarding_dialog is None
    qtbot.waitUntil(lambda: window.findChild(QPushButton, "openOnboardingGuide") is not None)

    for language, expected in (
        ("zh_CN", "设备操作演示"),
        ("en_US", "Device controls demo"),
        ("ja_JP", "デバイスの操作デモ"),
    ):
        window._language_manager.set_language(language)
        reopen = window.findChild(QPushButton, "openOnboardingGuide")
        assert reopen is not None
        assert reopen.text() == ""
        assert not reopen.icon().isNull()
        assert reopen.accessibleName() == expected
        assert reopen.toolTip() == expected
    assert window.findChild(QPushButton, "openCompanionGuide") is None
    assert window.findChild(QPushButton, "openDetailedGuide") is None

    qtbot.mouseClick(window._nav_buttons["settings"], Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not reopen.isVisibleTo(window))
    qtbot.mouseClick(window._nav_buttons["overview"], Qt.MouseButton.LeftButton)
    reopen = window.findChild(QPushButton, "openOnboardingGuide")
    assert reopen is not None

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
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *args: messages.append(args[2]),
    )
    assert window._view_model.draft is None
    window._open_onboarding_page("lighting")
    assert window._view_model.page == "overview"
    assert messages == ["连接并读取设备后，可调整外观与反馈。"]
    window._open_onboarding_page("settings")
    assert window._view_model.page == "settings"
