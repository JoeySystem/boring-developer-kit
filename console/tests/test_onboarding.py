from __future__ import annotations

import pytest

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from controller_config.i18n import ENGLISH, LanguageManager, SIMPLIFIED_CHINESE
from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.onboarding import (
    ONBOARDING_COMPLETED_KEY,
    ONBOARDING_STEPS,
    OnboardingDialog,
)


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
        # Page navigation can start asynchronous icon reads in the demo device.
        view_model.screen_icon.attach(None)
        view_model.screen_glyphs.attach(None)
        view_model.shutdown()

    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    return window


def test_first_visible_window_shows_guide_once(
    qtbot, qapp, contract, tmp_path
) -> None:
    settings = _settings(tmp_path)
    window = _window(qtbot, qapp, contract, settings)

    qtbot.waitUntil(lambda: window._onboarding_dialog is not None, timeout=1000)
    dialog = window._onboarding_dialog
    assert isinstance(dialog, OnboardingDialog)
    assert dialog.isModal()
    assert len(ONBOARDING_STEPS) == 7

    next_button = dialog.findChild(QPushButton, "onboardingNext")
    assert next_button is not None
    for expected_step in range(1, len(ONBOARDING_STEPS)):
        qtbot.mouseClick(next_button, Qt.MouseButton.LeftButton)
        assert dialog.step_index == expected_step
    assert next_button.text() == "开始配置"
    qtbot.mouseClick(next_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is None, timeout=1000)
    assert settings.value(ONBOARDING_COMPLETED_KEY, False, type=bool) is True

    second = _window(qtbot, qapp, contract, settings)
    qtbot.wait(30)
    assert second._onboarding_dialog is None


def test_guide_can_be_skipped_and_reopened_from_settings(
    qtbot, qapp, contract, tmp_path
) -> None:
    settings = _settings(tmp_path)
    window = _window(qtbot, qapp, contract, settings)
    qtbot.waitUntil(lambda: window._onboarding_dialog is not None, timeout=1000)
    dialog = window._onboarding_dialog
    assert dialog is not None

    skip = dialog.findChild(QPushButton, "onboardingSkip")
    assert skip is not None
    qtbot.mouseClick(skip, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is None, timeout=1000)

    qtbot.mouseClick(window._nav_buttons["settings"], Qt.MouseButton.LeftButton)
    reopen = window.findChild(QPushButton, "openOnboardingGuide")
    assert reopen is not None
    assert reopen.text() == "重新查看引导"
    qtbot.mouseClick(reopen, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is not None, timeout=1000)
    assert window._onboarding_dialog.step_index == 0


def test_guide_is_fully_available_in_english(qtbot, qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
        persist=False,
    )
    dialog = OnboardingDialog()
    qtbot.addWidget(dialog)
    manager.retranslate_widget_tree(dialog)

    assert dialog.findChild(QLabel, "onboardingProgress").text() == "Step 1 of 7"
    assert dialog.findChild(QLabel, "onboardingTitle").text() == (
        "Connect Your BORING Device"
    )
    next_button = dialog.findChild(QPushButton, "onboardingNext")
    assert next_button is not None
    for _ in range(len(ONBOARDING_STEPS) - 1):
        qtbot.mouseClick(next_button, Qt.MouseButton.LeftButton)
    assert next_button.text() == "Start Configuring"


@pytest.mark.parametrize("step_index", range(len(ONBOARDING_STEPS)))
def test_guide_opens_matching_page(qtbot, qapp, contract, tmp_path, step_index):
    window = _window(qtbot, qapp, contract, _settings(tmp_path))
    qtbot.waitUntil(lambda: window._onboarding_dialog is not None)
    qtbot.waitUntil(lambda: window._view_model.draft is not None, timeout=5000)
    dialog = window._onboarding_dialog
    for _ in range(step_index):
        dialog.next_step()
    qtbot.mouseClick(dialog._open_page, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window._onboarding_dialog is None)
    assert window._view_model.page == ONBOARDING_STEPS[step_index].page
    assert not window._view_model.draft.is_dirty


@pytest.mark.parametrize("language", [SIMPLIFIED_CHINESE, ENGLISH])
def test_all_guide_steps_fit_and_translate(qtbot, qapp, tmp_path, language):
    manager = LanguageManager(qapp, settings=_settings(tmp_path),
                              initial_language=language, persist=False)
    dialog = OnboardingDialog()
    from controller_config.views.main_window import APP_STYLE
    dialog.setStyleSheet(APP_STYLE)
    qtbot.addWidget(dialog)
    manager.retranslate_widget_tree(dialog)
    dialog.show()
    for index, step in enumerate(ONBOARDING_STEPS):
        qtbot.wait(20)
        assert dialog._open_page.isVisible()
        assert dialog.rect().contains(dialog._next.mapTo(dialog, QPoint(
            dialog._next.width() - 1, dialog._next.height() - 1)))
        if language == ENGLISH:
            assert not any('\u4e00' <= char <= '\u9fff' for char in
                           dialog._description.text() + dialog._action.text())
        assert dialog.grab().save(str(tmp_path / f"guide-{language}-{index}.png"))
        if index < len(ONBOARDING_STEPS) - 1:
            dialog.next_step()


def test_guide_explains_missing_device_without_blocking(qtbot, qapp, contract, tmp_path, monkeypatch):
    window = _window(qtbot, qapp, contract, _settings(tmp_path))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    assert window._view_model.draft is None
    window._open_onboarding_page("lighting")
    assert window._view_model.page == "overview"
    assert messages == ["连接设备并读取配置后，即可调整外观与反馈。"]
    window._open_onboarding_page("settings")
    assert window._view_model.page == "settings"
