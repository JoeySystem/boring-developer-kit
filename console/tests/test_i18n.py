from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QMenu, QPushButton

from controller_config.actions import action_definitions
from controller_config.i18n import (
    ENGLISH,
    LANGUAGE_SETTING_KEY,
    SIMPLIFIED_CHINESE,
    LanguageManager,
    normalize_language,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.transport.demo import DemoGateway
from controller_config.prompt_library import PromptLibraryStore
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from controller_config.views.action_editor import ActionEditor


@pytest.fixture(autouse=True)
def scoped_language_managers(qapp, monkeypatch):
    """Release this test's application-wide translators and event filters."""
    owner = QObject()
    manager_type = LanguageManager
    previous_language = qapp.property("boringUiLanguage")

    def create_manager(*args, **kwargs):
        kwargs.setdefault("parent", owner)
        return manager_type(*args, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "LanguageManager", create_manager)
    yield
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
    qapp.setProperty("boringUiLanguage", previous_language)


def _settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "configurator.ini"), QSettings.Format.IniFormat)


def test_language_normalization_supports_only_simplified_chinese_and_english() -> None:
    assert normalize_language("zh-CN") == SIMPLIFIED_CHINESE
    assert normalize_language("zh_Hans_CN") == SIMPLIFIED_CHINESE
    assert normalize_language("en-GB") == ENGLISH
    assert normalize_language("fr_FR") is None


def test_language_choice_is_persisted(qapp, tmp_path) -> None:
    settings = _settings(tmp_path)
    manager = LanguageManager(
        qapp,
        settings=settings,
        initial_language=SIMPLIFIED_CHINESE,
    )

    assert manager.set_language(ENGLISH)
    assert settings.value(LANGUAGE_SETTING_KEY) == ENGLISH

    restored = LanguageManager(qapp, settings=settings)
    assert restored.language == ENGLISH


def test_dynamic_text_can_update_in_english_and_return_to_chinese(
    qapp, tmp_path
) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    label = QLabel()
    set_translatable_text(
        label,
        "3 个步骤 · 编码 13/256 字节 · 显式延时 50 ms · 容量内",
    )
    assert label.text() == (
        "3 steps · 13/256 encoded bytes · explicit delay 50 ms · Within Capacity"
    )

    set_translatable_text(
        label,
        "4 个步骤 · 编码 18/256 字节 · 显式延时 75 ms · 容量内",
    )
    assert label.text().startswith("4 steps · 18/256 encoded bytes")
    assert translate_ui_text("1/4 条按键序列\n12/1024 字节") == (
        "1/4 key sequences\n12/1024 bytes"
    )

    manager.set_language(SIMPLIFIED_CHINESE)
    manager.retranslate_widget_tree(label)
    assert label.text() == "4 个步骤 · 编码 18/256 字节 · 显式延时 75 ms · 容量内"


def test_v4_playground_status_and_quota_copy_round_trips(qapp, tmp_path) -> None:
    manager = LanguageManager(qapp, settings=_settings(tmp_path), initial_language=ENGLISH)
    expectations = {
        "工作流与脚本": "Workflows and Scripts",
        "脚本导入": "Script Import",
        "Codex 工作流": "Codex Workflow",
        "导入 JSON": "Import JSON",
        "导入脚本": "Import Script",
        "配置变更提案审阅": "Review Configuration Proposals",
        "通用": "General",
        "关于": "About",
        "已认证 [ VERIFIED ]": "Authenticated [ VERIFIED ]",
        "开发设备，未认证 [ DEV ]": "Development device, unauthenticated [ DEV ]",
        "无法确认是 BORING 设备 [ UNTRUSTED ]": "Unable to verify BORING identity [ UNTRUSTED ]",
        "未连接设备 [ NO LINK ]": "No device connected [ NO LINK ]",
        "已断开 [ NO LINK ]": "Disconnected [ NO LINK ]",
        "已同步 [ SYNCED ]": "Synced [ SYNCED ]",
        "3  处未写入改动": "3  changes not written",
        "状态未接入": "Status Not Connected",
        "7D 外环 · 5H 内环 · 剩余额度\n更新于 09-09 10:30": (
            "7D outer ring · 5H inner ring · Remaining quota\nUpdated 09-09 10:30"
        ),
    }
    labels = []
    for source, expected in expectations.items():
        label = QLabel()
        set_translatable_text(label, source)
        assert label.text() == expected
        labels.append((label, source))
    assert translate_ui_text("设备  BORING MIST") == "Device  BORING MIST"
    assert translate_ui_text("端口  /dev/cu.usbmodem1234") == "Port  /dev/cu.usbmodem1234"
    manager.set_language(SIMPLIFIED_CHINESE)
    for label, source in labels:
        manager.retranslate_widget_tree(label)
        assert label.text() == source


def test_device_prompt_copy_round_trips_without_prototype_wording(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp, settings=_settings(tmp_path), initial_language=ENGLISH
    )
    expectations = {
        "写入设备并读回确认": "Write to Device and Verify",
        "从设备重新读取": "Read Again from Device",
        "从设备删除": "Delete from Device",
        "正在读取设备提示词列表": "Reading the device prompt list",
        "设备提示词已完整读取": "Device prompts have been read completely",
        "设备删除已读回确认": "Device deletion confirmed by readback",
    }
    for source, expected in expectations.items():
        assert translate_ui_text(source) == expected
    manager.set_language(SIMPLIFIED_CHINESE)
    for source in expectations:
        assert translate_ui_text(source) == source


def test_white_key_lighting_copy_is_available_in_english(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )

    assert translate_ui_text("白色动作键灯") == "White Action Key Lights"
    assert translate_ui_text("透明 Agent 状态键灯") == (
        "Transparent Agent Status Key Lights"
    )
    assert translate_ui_text(
        "透明键灯由 Agent 状态语义接管，不在这里作为普通 RGB 灯编辑；现有配置值保持不变。"
    ).startswith("Transparent key lights are reserved for Agent status semantics")

    manager.set_language(SIMPLIFIED_CHINESE)


def test_automation_runtime_messages_are_available_in_english(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )

    assert translate_ui_text("已保存本地脚本：Collect context") == (
        "Saved local script: Collect context"
    )
    assert translate_ui_text("运行失败：Collect context · exit 2") == (
        "Failed: Collect context · exit 2"
    )
    assert translate_ui_text("事件 9 已交给本地脚本：Collect context") == (
        "Event 9 dispatched to local script: Collect context"
    )
    assert translate_ui_text("本地脚本启动失败：Collect context") == (
        "Local script failed to start: Collect context"
    )
    assert translate_ui_text("本地脚本名称不能为空") == (
        "Local script name cannot be empty"
    )
    assert translate_ui_text("已删除本地脚本") == "Deleted local script"
    assert translate_ui_text("停止运行") == "Stop Run"
    assert translate_ui_text("已停止：Collect context") == "Stopped: Collect context"
    assert translate_ui_text(
        "提示词槽位 1 尚未写入设备；请先在快捷提示词页填写、写入并读回。"
    ).startswith("Prompt slot 1 is not on the device.")
    assert translate_ui_text("Claude Code 钥匙串读取超时，请完成系统授权后重新刷新。").startswith(
        "Claude Code Keychain access timed out."
    )
    assert translate_ui_text("提示词槽位 3 已绑定其他本地脚本") == (
        "Prompt slot 3 is already bound to another local script"
    )

    manager.set_language(SIMPLIFIED_CHINESE)


def test_lighting_live_preview_messages_are_available_in_english(
    qapp, tmp_path
) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )

    assert translate_ui_text("在设备上实时预览") == "Preview Live on Device"
    assert translate_ui_text("当前固件不支持实时预览") == (
        "The current firmware does not support live preview"
    )
    assert translate_ui_text("under_key 必须包含 12 项") == (
        "under_key must contain 12 items"
    )
    assert translate_ui_text("under_key[4].g 必须是 0..255 的整数") == (
        "under_key[4].g must be an integer from 0 to 255"
    )

    manager.set_language(SIMPLIFIED_CHINESE)


def test_prompt_palette_guide_and_fixed_slots_switch_languages(
    qtbot, qapp, tmp_path, contract
) -> None:
    manager = LanguageManager(
        qapp, settings=_settings(tmp_path), initial_language=ENGLISH
    )
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    window = MainWindow(view_model, language_manager=manager)
    def cleanup(_window):
        manager.set_language(SIMPLIFIED_CHINESE)
        view_model.shutdown()
    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.prompt_library is not None, timeout=1000)
    view_model.save_prompt_draft(1, "代码审查", "保持用户输入原样。")
    view_model.navigate("prompts")
    assert window.findChild(QLabel, "promptJoystickCenter").text() == (
        "Joystick: select\nKnob: confirm"
    )
    guide = window.findChild(QLabel, "promptPaletteGuide").text()
    assert "hold Key12 for about 0.8 seconds" in guide
    assert "short-press the knob to confirm" in guide
    assert "Key3 to cancel" in guide and "10 seconds of inactivity" in guide
    cards = window.findChildren(QPushButton, "promptDirectionCard")
    for button in cards:
        assert f"Prompt Slot {button.property('promptId')}" in button.toolTip()
    first = next(button for button in cards if button.property("promptId") == 1)
    assert "代码审查" in first.text()

    manager.set_language(SIMPLIFIED_CHINESE)
    assert window.findChild(QLabel, "promptJoystickCenter").text() == "摇杆选择\n旋钮确认"
    assert "Key3 取消" in window.findChild(QLabel, "promptPaletteGuide").text()
    assert window.findChild(QPushButton, "bindPromptDirection") is None
    view_model.shutdown()


def test_user_macro_and_profile_names_are_not_translated(
    qtbot, qapp, tmp_path, contract
) -> None:
    LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    editor = ActionEditor(
        action_definitions(contract, ("macro", "profile")),
        {"type": "macro", "macro_id": 7},
        reference_choices={
            ("macro", "macro_id"): (("诊断", 7),),
            ("profile", "profile_id"): (("设备概览", 0),),
        },
    )
    qtbot.addWidget(editor)
    editor.show()

    macro = editor.findChild(QComboBox, "actionField_macro_id")
    assert macro is not None and macro.currentText() == "诊断"
    type_selector = editor.findChild(QComboBox, "actionTypeEditor")
    type_selector.setCurrentIndex(type_selector.findData("profile"))
    profile = editor.findChild(QComboBox, "actionField_profile_id")
    assert profile is not None and profile.currentText() == "设备概览"


def test_shortcut_recorder_is_available_in_english(
    qtbot, qapp, tmp_path, contract
) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    editor = ActionEditor(
        action_definitions(contract, ("key", "none")),
        {"type": "key", "usage": 25, "modifiers": [227]},
        platform="macos",
    )
    qtbot.addWidget(editor)
    editor.show()

    record = editor.findChild(QPushButton, "shortcutRecordButton")
    labels = {label.text() for label in editor.findChildren(QLabel)}
    assert record is not None and record.text() == "Record New Shortcut"
    assert "Type the Shortcut Directly" in labels
    assert "Click Record, then press the target key or shortcut on your keyboard." in labels

    manager.set_language(SIMPLIFIED_CHINESE)


def test_window_switches_between_english_and_chinese_without_translating_profile_names(
    qtbot,
    qapp,
    tmp_path,
    contract,
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    window = MainWindow(view_model, language_manager=manager)
    def cleanup(_window):
        manager.set_language(SIMPLIFIED_CHINESE)
        view_model.shutdown()
    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: window._nav_buttons["overview"].accessibleName() == "Key Mapping",
        timeout=1000,
    )

    navigation_names = {
        button.accessibleName() for button in window._nav_buttons.values()
    }
    assert navigation_names == {
        "Key Mapping",
        "Quick Prompts",
        "Playground",
        "Appearance & Feedback",
        "Settings",
    }
    assert window._nav_buttons["overview"].text() == "Key Mapping"
    assert all(
        button.text() == button.accessibleName()
        for page, button in window._nav_buttons.items()
        if page != "overview"
    )
    assert {
        button.toolTip() for button in window._nav_buttons.values()
    } == (navigation_names - {"Appearance & Feedback"}) | {
        "Appearance & Feedback · Lighting, Haptics, Display"
    }
    assert {
        button.accessibleDescription() for button in window._nav_buttons.values()
    } == {"Switch View"}
    qtbot.waitUntil(
        lambda: window._device_auth_summary.text() == "Development device, unauthenticated [ DEV ]",
        timeout=1000,
    )
    assert window.windowTitle() == "BORING Console Community"
    assert {
        button.accessibleName()
        for button in window.findChildren(QPushButton, "windowControl")
    } == {"Close Window", "Minimize Window", "Zoom Window"}
    settings_menu = window.findChild(QMenu, "settingsMenu")
    assert settings_menu is not None and settings_menu.title() == "Settings"
    english_action = window.findChild(QAction, "languageAction_en_US")
    assert english_action is not None and english_action.isChecked()
    qtbot.mouseClick(window._nav_buttons["settings"], Qt.LeftButton)
    assert window._nav_buttons["settings"].text() == "Settings"
    assert window._nav_buttons["overview"].text() == "Key Mapping"
    assert window.findChild(QPushButton, "openDiagnosticsSettings").text() == (
        "Open Diagnostics"
    )
    assert window.findChild(QPushButton, "openFirmwareSettings").text() == (
        "Open Firmware Maintenance"
    )
    language_buttons = {
        button.text(): button
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
    }
    assert set(language_buttons) == {"简体中文", "English"}
    assert language_buttons["English"].property("active") is True
    assert language_buttons["简体中文"].property("active") is False
    view_model.navigate("overview")
    overview_text = set()

    def inspect_ble_details() -> None:
        dialog = window.findChild(QDialog)
        assert dialog is not None and dialog.isVisible()
        overview_text.update(label.text() for label in dialog.findChildren(QLabel))
        dialog.accept()

    QTimer.singleShot(0, inspect_ble_details)
    window.findChild(QPushButton, "bleSlotsDisclosure").click()
    assert "Device Shortcuts" in overview_text
    assert "KEY 3 + KEY 8 → Slot 1" in overview_text
    assert "KEY 3 + KEY 9 → Slot 2" in overview_text
    assert "KEY 3 + KEY 10 → Slot 3" in overview_text
    assert (
        "Press the chord to switch slots; hold it for about 3 seconds to clear "
        "that slot and enter pairing."
    ) in overview_text

    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    save_as_profile = window.findChild(QPushButton, "saveProfileAsButton")
    assert save_as_profile is not None
    assert save_as_profile.text() == "Save as New Profile…"
    create_profile = profile_pill.menu().findChild(QAction, "createProfileAction")
    assert create_profile is not None
    assert create_profile.text() == "New Blank Profile"
    sequence_action = profile_pill.menu().findChild(
        QAction, "manageDeviceKeySequencesAction"
    )
    assert sequence_action is not None
    assert sequence_action.text() == "Manage Device Key Sequences…"
    sequence_action.trigger()
    qtbot.waitUntil(lambda: view_model.page == "sequences", timeout=1000)

    view_model.navigate("actions")
    qtbot.waitUntil(
        lambda: any(
            label.text() == "Workflows and Scripts"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    catalog_status = window.findChild(QLabel, "actionCatalogStatus")
    assert catalog_status is not None
    assert catalog_status.text() == (
        "Total Automations 0  ·  Available 0  ·  Bound Controls 0"
    )
    develop_tab = next(
        button
        for button in window.findChildren(QPushButton, "actionTab")
        if button.text() == "Workflows and Scripts"
    )
    develop_tab.click()
    extension_tab = next(
        button
        for button in window.findChildren(QPushButton, "developerLaneTab")
        if button.text() == "Codex Workflow"
    )
    extension_tab.click()
    qtbot.waitUntil(
        lambda: any(
            label.text() == "Codex Workflow"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    import_directory = window.findChild(QPushButton, "importExtensionDirectory")
    import_extension = window.findChild(QPushButton, "importExtensionPackage")
    assert import_directory is not None
    assert import_directory.text() == "Import Extension Folder"
    assert import_extension is not None
    assert import_extension.text() == "Import ZIP"
    assert any(
        label.text() == "Installed Extensions"
        for label in window.findChildren(QLabel)
    )

    view_model.navigate("prompts")
    qtbot.waitUntil(
        lambda: (
            (guide := window.findChild(QLabel, "promptPaletteGuide")) is not None
            and "hold Key12 for about 0.8 seconds" in guide.text()
        ),
        timeout=1000,
    )
    qtbot.waitUntil(
        lambda: (
            (helper_state := window.findChild(QLabel, "promptHelperOnlineState"))
            is not None
            and helper_state.text() == "Helper Online"
        ),
        timeout=1000,
    )

    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    view_model.navigate("diagnostics")
    qtbot.waitUntil(
        lambda: any(
            label.text() == "Diagnostics and Live Input"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    assert any(
        button.text() == "Export Diagnostic Summary"
        for button in window.findChildren(QPushButton)
    )
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    open_calibration = window.findChild(QPushButton, "openJoystickCalibration")
    assert report_issue is not None
    assert report_issue.text() == "Still Experiencing an Issue"
    assert open_calibration is not None and open_calibration.isHidden()
    report_issue.click()
    assert not open_calibration.isHidden()
    assert open_calibration.text() == "Open Joystick Calibration"
    assert any(
        label.text() == "Joystick Issue Still Present"
        for label in window.findChildren(QLabel)
    )

    manager.set_language(SIMPLIFIED_CHINESE)
    assert any(
        label.text() == "已确认仍有摇杆异常"
        for label in window.findChildren(QLabel)
    )
    assert open_calibration.text() == "进入摇杆校准"
    manager.set_language(ENGLISH)

    snapshot = view_model.model.snapshot
    assert snapshot is not None
    view_model.rename_profile(snapshot.active_profile_id, "诊断")
    view_model.navigate("overview")
    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    profile_action = profile_pill.menu().findChild(QAction, "profileSelectAction_0")
    assert profile_action is not None and profile_action.text() == "诊断"

    view_model.navigate("settings")
    chinese_button = next(
        button
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
        if button.text() == "简体中文"
    )
    qtbot.mouseClick(chinese_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: window._nav_buttons["overview"].accessibleName() == "按键配置",
        timeout=1000,
    )
    assert manager.language == SIMPLIFIED_CHINESE
    assert window._nav_buttons["settings"].text() == "设置"
    language_buttons = {
        button.text(): button
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
    }
    assert language_buttons["简体中文"].property("active") is True
    assert language_buttons["English"].property("active") is False
    assert window.findChild(QAction, "languageAction_zh_CN").isChecked()
    view_model.discard_draft()
