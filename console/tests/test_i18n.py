from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from controller_config.actions import action_definitions
from controller_config.build_identity import BuildIdentity
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
from controller_config.views.actions import ActionsPage


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


def test_language_normalization_supports_registered_languages() -> None:
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


def test_show_event_translates_each_widget_without_rewalking_subtree(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    subtree_walks = []
    monkeypatch.setattr(
        manager,
        "retranslate_widget_tree",
        lambda root: subtree_walks.append(root),
    )
    parent = QWidget()
    layout = QVBoxLayout(parent)
    label = QLabel("设置")
    layout.addWidget(label)
    qtbot.addWidget(parent)

    parent.show()
    qtbot.waitUntil(lambda: label.text() == "Settings")

    assert subtree_walks == []


def test_only_latest_language_manager_filters_application_events(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    first = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=SIMPLIFIED_CHINESE,
    )
    second = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )
    first_widgets = []
    second_widgets = []
    monkeypatch.setattr(first, "_translate_widget", first_widgets.append)
    monkeypatch.setattr(second, "_translate_widget", second_widgets.append)
    label = QLabel("设置")
    qtbot.addWidget(label)

    label.show()
    qtbot.waitUntil(lambda: bool(second_widgets))

    assert first_widgets == []


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
        "3 steps · 13/256 bytes · Delay 50 ms · Within Capacity"
    )

    set_translatable_text(
        label,
        "4 个步骤 · 编码 18/256 字节 · 显式延时 75 ms · 容量内",
    )
    assert label.text().startswith("4 steps · 18/256 bytes")
    assert translate_ui_text("1/4 条按键序列\n12/1024 字节") == (
        "1/4 key macros\n12/1024 bytes"
    )

    manager.set_language(SIMPLIFIED_CHINESE)
    manager.retranslate_widget_tree(label)
    assert label.text() == "4 步 · 18/256 字节 · 延时 75 ms · 容量内"


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
        "已认证 [ VERIFIED ]": "Verified [ VERIFIED ]",
        "开发设备，未认证 [ DEV ]": "Development · Unverified [ DEV ]",
        "无法确认是 BORING 设备 [ UNTRUSTED ]": "BORING Identity Unverified [ UNTRUSTED ]",
        "未连接设备 [ NO LINK ]": "Disconnected [ NO LINK ]",
        "已断开 [ NO LINK ]": "Disconnected [ NO LINK ]",
        "已同步 [ SYNCED ]": "Synced [ SYNCED ]",
        "3  处未写入改动": "Changes Not Saved to Device: 3",
        "状态未接入": "Status Not Connected",
        "7D 外环 · 5H 内环 · 剩余额度\n更新于 09-09 10:30": (
            "Remaining · Outer 7d / Inner 5h\nUpdated 09-09 10:30"
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
    chinese_copy = {
        "配置变更提案审阅": "审阅配置提案",
        "开发设备，未认证 [ DEV ]": "开发设备 · 未认证 [ DEV ]",
        "无法确认是 BORING 设备 [ UNTRUSTED ]": "BORING 身份未确认 [ UNTRUSTED ]",
        "未连接设备 [ NO LINK ]": "未连接 [ NO LINK ]",
        "3  处未写入改动": "3 项修改未写入",
        "7D 外环 · 5H 内环 · 剩余额度\n更新于 09-09 10:30": (
            "剩余额度 · 外环 7 天／内环 5 小时\n更新于 09-09 10:30"
        ),
    }
    for label, source in labels:
        manager.retranslate_widget_tree(label)
        assert label.text() == chinese_copy.get(source, source)


def test_device_prompt_copy_round_trips_without_prototype_wording(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp, settings=_settings(tmp_path), initial_language=ENGLISH
    )
    expectations = {
        "写入设备并读回确认": "Save to device",
        "从设备重新读取": "Read from Device",
        "从设备删除": "Delete from Device",
        "正在读取设备提示词列表": "Reading the device prompt list",
        "设备提示词已完整读取": "Device prompts loaded",
        "设备删除已读回确认": "Prompt removed from device",
    }
    for source, expected in expectations.items():
        assert translate_ui_text(source) == expected
    manager.set_language(SIMPLIFIED_CHINESE)
    chinese_copy = {
        "写入设备并读回确认": "保存到设备",
        "从设备重新读取": "重新读取设备",
        "设备提示词已完整读取": "设备提示词已读取",
        "设备删除已读回确认": "提示词已从设备删除",
    }
    for source in expectations:
        assert translate_ui_text(source) == chinese_copy.get(source, source)


def test_function_key_lighting_copy_is_available_in_english(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp,
        settings=_settings(tmp_path),
        initial_language=ENGLISH,
    )

    assert translate_ui_text("功能键灯光") == "Function Key Lighting"
    assert translate_ui_text("功能键 8") == "Function Key 8"
    assert translate_ui_text("状态灯键 1") == "Status Light Key 1"
    assert translate_ui_text("STATUS KEYS · 状态灯键") == (
        "STATUS KEYS · STATUS LIGHTS"
    )
    assert translate_ui_text(
        "状态灯键由 Agent 状态语义接管，不在这里作为普通 RGB 灯编辑；现有配置值保持不变。"
    ).startswith("Status light keys follow Agent status")
    assert translate_ui_text(
        "每颗功能键灯可以单独设置 RGB；设为 0, 0, 0 可关闭该键灯。"
    ).startswith("Click a function key to choose its light color")

    manager.set_language("ja_JP")
    assert translate_ui_text("功能键 8") == "機能キー 8"
    assert translate_ui_text("状态灯键 1") == "ステータスライトキー 1"
    assert "機能キーをクリック" in translate_ui_text(
        "每颗功能键灯可以单独设置 RGB；设为 0, 0, 0 可关闭该键灯。"
    )

    manager.set_language(SIMPLIFIED_CHINESE)


def test_firmware_update_actions_are_distinct_in_all_languages(qapp, tmp_path) -> None:
    manager = LanguageManager(
        qapp, settings=_settings(tmp_path), initial_language=SIMPLIFIED_CHINESE
    )
    expectations = {
        SIMPLIFIED_CHINESE: (
            "在线更新（推荐）",
            "从文件安装（高级）",
            "安装下载的固件更新",
        ),
        ENGLISH: (
            "Online update (recommended)",
            "Install from file (advanced)",
            "Install downloaded firmware update",
        ),
        "ja_JP": (
            "オンライン更新（推奨）",
            "ファイルからインストール（詳細）",
            "ダウンロードした更新をインストール",
        ),
    }
    sources = (
        "在线更新（推荐）",
        "从文件安装（高级）",
        "安装下载的固件更新",
    )
    for language, expected in expectations.items():
        manager.set_language(language)
        assert tuple(translate_ui_text(source) for source in sources) == expected
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
        "Event 9 Sent to Script: Collect context"
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
    ).startswith("Prompt 1 is not saved on the device.")
    assert translate_ui_text("Claude Code 钥匙串读取超时，请完成系统授权后重新刷新。").startswith(
        "Claude Code Keychain access timed out."
    )
    assert translate_ui_text("提示词槽位 3 已绑定其他本地脚本") == (
        "Slot 3 Is Bound to Another Script"
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

    assert translate_ui_text("在设备上实时预览") == "Live Device Preview"
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
    window = MainWindow(
        view_model,
        language_manager=manager,
        build_identity=BuildIdentity("official"),
    )
    def cleanup(_window):
        manager.set_language(SIMPLIFIED_CHINESE)
        view_model.shutdown()
    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.prompt_library is not None, timeout=1000)
    view_model.save_prompt_draft(1, "代码审查", "保持用户输入原样。")
    view_model.navigate("prompts")
    qtbot.waitUntil(lambda: bool(window.findChildren(QPushButton, "promptOperationStep")))
    assert "Hold key 12" in window.findChildren(QPushButton, "promptOperationStep")[0].text()
    guide = window.findChild(QLabel, "promptPaletteGuide").text()
    assert "Hold key 12 for about 0.8 seconds" in guide
    assert "press the knob to confirm" in guide
    assert "Key 3 cancels" in guide and "10 seconds" in guide
    cards = window.findChildren(QPushButton, "promptDirectionCard")
    for button in cards:
        assert f"Prompt Slot {button.property('promptId')}" in button.toolTip()
    first = next(button for button in cards if button.property("promptId") == 1)
    assert "代码审查" in first.text()

    manager.set_language(SIMPLIFIED_CHINESE)
    assert "长按 12 号键" in window.findChildren(QPushButton, "promptOperationStep")[0].text()
    assert "按 3 号按键取消" in window.findChild(QLabel, "promptPaletteGuide").text()
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
    assert "Shortcut" in labels
    assert "Select Record, then press a key or shortcut on your keyboard." in labels

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
    window = MainWindow(
        view_model,
        language_manager=manager,
        build_identity=BuildIdentity("official"),
    )
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
        "Appearance & Feedback",
        "Firmware & System",
    }
    def expected_navigation_text(button: QPushButton) -> str:
        return "" if button.property("compactNavigation") else button.accessibleName()

    assert all(
        button.text() == expected_navigation_text(button)
        for button in window._nav_buttons.values()
    )
    assert {
        button.toolTip() for button in window._nav_buttons.values()
    } == (navigation_names - {"Appearance & Feedback"}) | {
        "Appearance & Feedback · Lighting, Haptics, Display"
    }
    assert {
        button.accessibleDescription() for button in window._nav_buttons.values()
    } == {"Switch View", "Firmware & System"}
    qtbot.waitUntil(
        lambda: window._device_auth_summary.text() == "Development · Unverified [ DEV ]",
        timeout=1000,
    )
    assert window.windowTitle() == "BORING Console"
    assert {
        button.accessibleName()
        for button in window.findChildren(QPushButton, "windowControl")
    } == {"Close Window", "Minimize Window", "Zoom Window"}
    settings_menu = window.findChild(QMenu, "settingsMenu")
    assert settings_menu is not None and settings_menu.title() == "Settings"
    english_action = window.findChild(QAction, "languageAction_en_US")
    assert english_action is not None and english_action.isChecked()
    qtbot.mouseClick(window._nav_buttons["settings"], Qt.LeftButton)
    assert all(
        button.text() == expected_navigation_text(button)
        for button in window._nav_buttons.values()
    )
    assert window.findChild(QPushButton, "openDiagnosticsSettings") is None
    qtbot.waitUntil(
        lambda: any(
            button.isVisible() and button.text() == "Firmware Update"
            for button in window.findChildren(QPushButton, "settingsGroup")
        )
    )
    language_buttons = {
        button.text(): button
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
    }
    assert set(language_buttons) == {"简体中文", "English", "日本語"}
    assert language_buttons["English"].property("active") is True
    assert language_buttons["简体中文"].property("active") is False
    view_model.navigate("overview")
    assert window.findChild(QLabel, "devicePanelLive").text() == "Connected"
    assert window.findChild(QLabel, "deviceConnectionSummary").text() == "USB-C · Connected"
    bluetooth_management = window.findChild(QPushButton, "bleSlotsDisclosure")
    assert bluetooth_management.text() == "Bluetooth & Computers…"
    assert bluetooth_management.accessibleName() == "Open Bluetooth and computer management"
    overview_text = set()

    def inspect_ble_details() -> None:
        dialog = window.findChild(QDialog)
        assert dialog is not None and dialog.isVisible()
        overview_text.update(label.text() for label in dialog.findChildren(QLabel))
        dialog.accept()

    QTimer.singleShot(0, inspect_ble_details)
    window.findChild(QPushButton, "bleSlotsDisclosure").click()
    assert "Bluetooth connection" in overview_text
    assert "Computer 1" in overview_text
    assert "Selected · Not connected" in overview_text
    assert not any("KEY 3" in text for text in overview_text)

    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    save_as_profile = window.findChild(QPushButton, "saveProfileAsButton")
    assert save_as_profile is not None
    assert save_as_profile.text() == "Save as New Profile…"
    create_profile = profile_pill.menu().findChild(QAction, "createProfileAction")
    assert create_profile is not None
    assert create_profile.text() == "New Blank Profile"
    save_as_action = profile_pill.menu().findChild(QAction, "saveProfileAsAction")
    assert save_as_action is not None
    assert save_as_action.text() == "Save as New Profile…"
    assert profile_pill.menu().findChild(
        QAction, "manageDeviceKeySequencesAction"
    ) is not None

    view_model.navigate("actions")
    qtbot.waitUntil(
        lambda: any(
            label.text() == "Advanced Customization"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    actions_page = window.findChild(ActionsPage, "actionsPage")
    assert actions_page is not None
    assert window.findChild(QPushButton, "createAction").text() == "Create Custom Action"
    sequence_action = window.findChild(QAction, "manageDeviceKeySequencesAction")
    assert sequence_action is not None
    assert sequence_action.text() == "Manage device key macros"
    actions_page.show_developer_lane(1)
    extension_tab = next(
        button
        for button in window.findChildren(QPushButton, "developerLaneTab")
        if button.text() == "Extensions"
    )
    extension_tab.click()
    qtbot.waitUntil(
        lambda: any(
            label.text() == "Install extension package"
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
            and "Hold key 12 for about 0.8 seconds" in guide.text()
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
            label.text() == "Device Check"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    assert any(
        button.text() == "Export Summary"
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
    assert window._nav_buttons["settings"].text() == expected_navigation_text(
        window._nav_buttons["settings"]
    )
    language_buttons = {
        button.text(): button
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
    }
    assert language_buttons["简体中文"].property("active") is True
    assert language_buttons["English"].property("active") is False
    assert window.findChild(QAction, "languageAction_zh_CN").isChecked()
    view_model.discard_draft()
