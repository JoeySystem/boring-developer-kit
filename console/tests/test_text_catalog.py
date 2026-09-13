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
    data["messages"]["设置"].update(zh_CN="偏好", en_US="Prefs")
    data["messages"]["打开诊断"].update(zh_CN="检查", en_US="Check")
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
    qtbot.waitUntil(lambda: window._nav_buttons["settings"].accessibleName() == "偏好")
    for language, name, button in (("en_US", "Prefs", "Check"), ("zh_CN", "偏好", "检查"),
                                   ("en_US", "Prefs", "Check")):
        language_manager.set_language(language)
        vm.navigate("settings")
        qtbot.waitUntil(lambda: window._nav_buttons["settings"].accessibleName() == name)
        qtbot.waitUntil(lambda: window.findChild(QPushButton, "openDiagnosticsSettings") is not None)
        assert window.findChild(QPushButton, "openDiagnosticsSettings").text() == button


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


def test_fixed_copy_preserves_named_parameters_in_both_languages():
    catalog = TextCatalog.load()
    fields = lambda text: set(re.findall(r"\{[A-Za-z_]\w*\}", text))
    for source, entry in catalog.messages.items():
        for language in ("zh_CN", "en_US"):
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
