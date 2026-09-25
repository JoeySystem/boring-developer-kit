from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from controller_config.build_identity import BuildIdentity
from controller_config.extensions.manager import ExtensionManager
from controller_config.firmware_origin import FirmwareReleaseHistory
from controller_config.update_recovery import UpdateRecoveryStore
from controller_config.update_reminders import UpdateReminders
from controller_config.views.main_window import _translated_application_display_name
from test_session_recovery import session


def test_build_identity_keeps_official_paths_and_separates_diy_paths() -> None:
    official = BuildIdentity("official")
    custom = BuildIdentity("custom")

    assert official.application_name == "BORING Controller Config"
    assert official.application_display_name == "BORING 控制台"
    assert official.launch_agent_label == "com.boring.controller-config.prompt-helper"
    assert official.claude_owner_id == "com.boring.controller-config"
    assert official.claude_endpoint_path == Path.home() / ".boring/claude-status/endpoint.json"

    assert custom.application_name == "BORING Console Community"
    assert custom.application_display_name == "BORING Console Community"
    assert custom.launch_agent_label == "com.boring.controller-config.community.prompt-helper"
    assert custom.claude_owner_id == "com.boring.controller-config.community"
    assert custom.claude_endpoint_path == Path.home() / ".boring/community/claude-status/endpoint.json"


def test_missing_identity_uses_non_official_storage_identity() -> None:
    missing = BuildIdentity(None, "missing")

    assert missing.application_name == "BORING Console Community"
    assert missing.claude_owner_id == "com.boring.controller-config.community"


def test_default_qsettings_stores_follow_the_qt_application_identity(monkeypatch) -> None:
    reminder_calls: list[tuple[object, ...]] = []
    firmware_calls: list[tuple[object, ...]] = []

    class FakeSettings:
        def __init__(self, *args) -> None:
            reminder_calls.append(args)

    class FakeFirmwareSettings:
        def __init__(self, *args) -> None:
            firmware_calls.append(args)

        def value(self, _key, default=None, **_kwargs):
            return default

    import controller_config.update_reminders as reminders_module
    import controller_config.firmware_origin as firmware_module

    monkeypatch.setattr(reminders_module, "QSettings", FakeSettings)
    monkeypatch.setattr(firmware_module, "QSettings", FakeFirmwareSettings)

    UpdateReminders()
    FirmwareReleaseHistory(bundled=[])

    assert reminder_calls == [()]
    assert firmware_calls == [()]


def test_qstandardpaths_separate_diy_recovery_and_extensions(qapp) -> None:
    original_name = qapp.applicationName()
    original_display = qapp.applicationDisplayName()
    original_org = qapp.organizationName()
    try:
        qapp.setOrganizationName("BORING")
        qapp.setApplicationName(BuildIdentity("official").application_name)
        qapp.setApplicationDisplayName(BuildIdentity("official").application_display_name)
        official_settings = QSettings().fileName()
        official_recovery = UpdateRecoveryStore().path
        official_extensions = ExtensionManager().managed_root

        qapp.setApplicationName(BuildIdentity("custom").application_name)
        qapp.setApplicationDisplayName(BuildIdentity("custom").application_display_name)
        custom_settings = QSettings().fileName()
        custom_recovery = UpdateRecoveryStore().path
        custom_extensions = ExtensionManager().managed_root

        assert official_settings != custom_settings
        assert official_recovery != custom_recovery
        assert official_extensions != custom_extensions
        assert "BORING Controller Config" in str(official_recovery)
        assert "BORING Console Community" in str(custom_recovery)
    finally:
        qapp.setOrganizationName(original_org)
        qapp.setApplicationName(original_name)
        qapp.setApplicationDisplayName(original_display)


def test_language_change_keeps_diy_in_application_display_name(qapp) -> None:
    class LanguageManager:
        def translate(self, _text: str) -> str:
            return "BORING Console"

    assert _translated_application_display_name(
        BuildIdentity("custom"), LanguageManager()
    ) == "BORING Console Community"
    assert _translated_application_display_name(
        BuildIdentity("official"), LanguageManager()
    ) == "BORING Console"


def test_login_start_failure_restores_action_to_actual_state(
    session, monkeypatch
) -> None:
    window, _view_model, _gateway, _snapshot, _store = session

    class Background:
        login_enabled = False

        @staticmethod
        def set_login_enabled(_enabled: bool) -> None:
            raise ValueError(
                "另一版本的 BORING 控制台已设置登录后启动；请先在该版本中关闭。"
            )

        @staticmethod
        def set_helper_status(**_status) -> None:
            pass

    original_background = window._background_controller
    window._background_controller = Background()
    action = window.findChild(QAction, "promptHelperLoginAction")
    action.setEnabled(True)
    action.setChecked(True)
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    try:
        window._set_prompt_helper_login(True)

        assert not action.isChecked()
        assert warnings and "另一版本" in warnings[0][1]
    finally:
        window._background_controller = original_background
