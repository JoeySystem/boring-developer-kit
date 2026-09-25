from __future__ import annotations

import json
import re
from importlib.resources import files

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings
from PySide6.QtWidgets import QLabel, QPushButton, QWidget, QVBoxLayout

from controller_config import i18n
from controller_config.text_catalog import TextCatalog
from controller_config.transport.demo import DemoGateway
from controller_config.prompt_library import PromptLibraryStore
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


@pytest.fixture
def edited_catalog(tmp_path, monkeypatch):
    data = json.loads(files("controller_config.translations").joinpath("ui_text.json").read_text())
    data["messages"]["固件与系统"].update(zh_CN="系统中心", en_US="System Center")
    data["messages"]["切换到官方版本"].update(zh_CN="切换", en_US="Switch")
    data["messages"]["仅保存本地草稿"].update(zh_CN="存草稿", en_US="Save Draft")
    data["dynamic"]["prompt.slot_pending"].update(
        zh_CN="请先写入提示词 {v1}", en_US="Write prompt {v1} first"
    )
    data["dynamic"]["action.function_name"].update(
        zh_CN="名称：{v1}", en_US="Name: {v1}"
    )
    path = tmp_path / "ui_text.json"
    path.write_text(json.dumps(data, ensure_ascii=False))
    catalog = TextCatalog.load(path)
    monkeypatch.setattr(i18n, "get_text_catalog", lambda: catalog)
    return catalog


@pytest.fixture
def language_manager(qapp, tmp_path):
    owner = QObject()
    previous = qapp.property("boringUiLanguage")
    manager = i18n.LanguageManager(
        qapp, settings=QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat),
        initial_language="zh_CN", persist=False, parent=owner,
    )
    yield manager
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(owner, QEvent.DeferredDelete)
    qapp.setProperty("boringUiLanguage", previous)


def test_edit_file_changes_both_languages_and_preserves_user_values(edited_catalog):
    source = "提示词槽位 3 尚未写入设备；请先在快捷提示词页填写、写入并读回。"
    assert edited_catalog.translate(source, "zh_CN") == "请先写入提示词 3"
    assert edited_catalog.translate(source, "en_US") == "Write prompt 3 first"
    for language, prefix in (("zh_CN", "名称："), ("en_US", "Name: ")):
        # A user name that is also a translation key must remain user data.
        assert edited_catalog.translate("功能名称：设置", language) == prefix + "设置"
        assert edited_catalog.translate("功能名称：{name}/我的配置", language) == prefix + "{name}/我的配置"


def test_real_window_uses_edited_copy_after_repeated_language_switches(
    qtbot, tmp_path, contract, edited_catalog, language_manager,
):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract,
                       prompt_library_store=PromptLibraryStore(tmp_path / "prompts"))
    window = MainWindow(vm, language_manager=language_manager)
    qtbot.addWidget(window, before_close_func=lambda _: vm.shutdown())
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: window._nav_buttons["settings"].accessibleName() == "系统中心")
    for language, name, button in (
        ("en_US", "System Center", "Switch"),
        ("zh_CN", "系统中心", "切换"),
        ("en_US", "System Center", "Switch"),
    ):
        language_manager.set_language(language)
        vm.navigate("settings")
        qtbot.waitUntil(lambda: window._nav_buttons["settings"].accessibleName() == name)
        window._select_settings_section("system")
        qtbot.waitUntil(lambda: window.findChild(QPushButton, "switchOfficialVersion") is not None)
        assert window.findChild(QPushButton, "switchOfficialVersion").text() == button


def test_shortened_or_empty_copy_round_trips_without_reinterpreting_source(
    qtbot, edited_catalog, language_manager,
):
    root = QWidget()
    box = QVBoxLayout(root)
    label = QLabel("仅保存本地草稿")
    protected = QLabel("设置")
    protected.setProperty(i18n.SKIP_TRANSLATION_PROPERTY, True)
    box.addWidget(label)
    box.addWidget(protected)
    qtbot.addWidget(root)
    root.show()
    language_manager.retranslate_widget_tree(root)
    assert label.text() == "存草稿"
    language_manager.set_language("en_US")
    assert label.text() == "Save Draft"
    edited_catalog.messages["仅保存本地草稿"]["zh_CN"] = ""
    language_manager.set_language("zh_CN")
    assert label.text() == ""
    language_manager.set_language("en_US")
    assert label.text() == "Save Draft"
    assert protected.text() == "设置"


def test_invalid_placeholder_is_reported_with_entry_name():
    with pytest.raises(ValueError, match="example: invalid placeholder"):
        TextCatalog({"messages": {}, "dynamic": {
            "example": {"zh_CN": "{v1}", "en_US": "{count}"},
        }}, {"rules": [{"message": "example", "match": r"value (.+)"}]})


def test_fixed_copy_preserves_named_parameters_in_all_languages():
    catalog = TextCatalog.load()
    fields = lambda text: set(re.findall(r"\{[A-Za-z_]\w*\}", text))
    for source, entry in catalog.messages.items():
        for language in ("zh_CN", "en_US", "ja_JP"):
            assert fields(entry[language]) == fields(source), (source, language)


@pytest.mark.parametrize("source,chinese,english", [
    (
        "将覆盖设备的上方向快捷提示词，随后立即读回全文确认。是否继续？",
        "覆盖设备上方向的提示词并读回验证？",
        "Overwrite the device's Up prompt and verify it?",
    ),
    (
        "将删除设备的右方向快捷提示词。该方向在重新配置前不会执行提示词。是否继续？",
        "删除设备右方向的提示词？重新设置前，该方向将无法触发提示词。",
        "Delete the device's Right prompt? That direction cannot trigger a prompt until configured again.",
    ),
    ("在线固件下载失败", "固件下载失败", "Firmware Download Failed"),
    ("在线固件检查失败", "固件检查失败", "Firmware Check Failed"),
    ("滚轮上", "滚轮 上", "Scroll Up"),
])
def test_short_copy_keeps_operation_and_translates_application_values(source, chinese, english):
    catalog = TextCatalog.load()
    assert catalog.translate(source, "zh_CN") == chinese
    assert catalog.translate(source, "en_US") == english


@pytest.mark.parametrize("language", ["en_US", "ja_JP"])
def test_local_p0_and_update_copy_preserves_identity_and_meaning(language):
    catalog = TextCatalog.load()
    for source in (
        "停止本地等待",
        "正在读取设备最新配置，请稍后再操作",
        "固件校验未通过，未安装。",
        "检查应用更新",
        "其他设备或旧配置仍有本地草稿，请先切回处理，再重启更新。",
    ):
        assert catalog.translate(source, language) != source
    serial = "CP01-设置・日本語"
    source = f"设备 {serial} 的草稿只保留在本进程中。可以先导出草稿，或丢弃后关闭。"
    assert serial in catalog.translate(source, language)
    assert catalog.translate(source, language) != source
    assert "0.1.18" in catalog.translate("有新版本 {version}", language).format(version="0.1.18")


@pytest.mark.parametrize("language", ["en_US", "ja_JP"])
def test_voice_setup_stage_copy_is_translated(language):
    catalog = TextCatalog.load()
    for source in (
        "设置要求与帮助…",
        "使用其他快捷键…",
        "1  设备快捷键 · 待保存",
        "2  输入软件绑定 · 保存设备后继续",
        "练习完成，内容未发送到 AI。",
        "更换语音输入软件",
        "从 Mac 顶部输入法菜单切换到千问，打开千问设置 → 语音输入，将快捷键设为右 Option，并开启“短按也唤起语音输入”。",
    ):
        assert catalog.translate(source, language) != source


@pytest.mark.parametrize("language,expected", [
    ("zh_CN", "单击 F14 / 双击 Enter"),
    ("en_US", "Press once: F14 / Press twice quickly: Enter"),
    ("ja_JP", "1回押す：F14 / 素早く2回押す：Enter"),
])
def test_custom_voice_gesture_distinguishes_double_press(language, expected):
    from controller_config.actions import describe_action
    action = {"type": "key_gesture", "usage": 105, "modifiers": [],
              "double_usage": 40, "double_modifiers": [], "double_window_ms": 300}
    # Use a custom voice shortcut to exercise the generic gesture summary.
    source = describe_action(action, platform="macos")
    assert TextCatalog.load().translate(source, language) == expected


@pytest.mark.parametrize('language, expected', [
    ('zh_CN', ('发送超时', '尚未保存')),
    ('en_US', ('while sending', 'has not been saved')),
    ('ja_JP', ('送信がタイムアウト', 'まだ保存されていません')),
])
def test_bluetooth_send_and_validation_timeout_copy(language, expected):
    catalog = TextCatalog.load()
    for source, phrase in zip((
        '蓝牙请求发送超时，请重新连接后重试。',
        '设备校验回复超时，配置尚未保存，请重试。',
        '设备校验未完成，配置尚未保存，请查看详情后重试。',
    ), (*expected, expected[1])):
        assert phrase in catalog.translate(source, language)
