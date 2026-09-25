from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QLabel, QPushButton

from controller_config.build_identity import BuildIdentity
from controller_config.desktop_update import UpdateStatus
from controller_config.update_recovery import UpdateRecoveryStore
from controller_config.views.desktop_update import (
    DesktopUpdateUi,
    _official_download_source,
    _resolve_official_download_url,
)
from test_session_recovery import session


class _UpdaterSpy(QObject):
    changed = Signal(object)

    def __init__(self, state: str) -> None:
        super().__init__()
        self.status = UpdateStatus(state=state)
        self.calls: list[str] = []

    def check(self) -> None:
        self.calls.append("check")

    def download(self) -> None:
        self.calls.append("download")

    def install(self) -> None:
        self.calls.append("install")

    def cancel(self) -> None:
        self.calls.append("cancel")


def _attach_update_ui(
    session,
    tmp_path,
    identity: BuildIdentity,
    *,
    confirm_switch=None,
    official_url_resolver=None,
    official_network=None,
    url_opener=None,
    open_failure_notifier=None,
):
    window, vm, gateway, _snapshot, _store = session
    window._build_identity = identity
    ui = DesktopUpdateUi(
        window,
        store=UpdateRecoveryStore(tmp_path / f"{identity.origin or 'unknown'}-recovery.json"),
        build_identity=identity,
        confirm_official_switch=confirm_switch,
        official_url_resolver=official_url_resolver,
        official_network=official_network,
        url_opener=url_opener,
        open_failure_notifier=open_failure_notifier,
    )
    window._desktop_update_ui = ui
    updater = _UpdaterSpy(identity.update_state if not identity.allows_official_updates else "idle")
    ui.attach(updater)
    ui.show_status(
        UpdateStatus(state=updater.status.state, message=identity.update_message)
    )
    gateway.commands.clear()
    return window, vm, gateway, ui, updater


@pytest.mark.parametrize(
    ("identity", "expected_source"),
    [
        (BuildIdentity("custom"), "自定义版本"),
        (BuildIdentity(None, "missing resource"), "构建信息不可用"),
    ],
)
def test_non_official_about_page_has_switch_entry_without_update_controls(
    session, tmp_path, identity, expected_source
):
    window, vm, _gateway, ui, _updater = _attach_update_ui(
        session, tmp_path, identity, confirm_switch=lambda _message: False
    )
    window._settings_section = "system"
    vm.navigate("settings")

    assert ui.build_identity is identity
    assert ui._official_network is None
    assert window.findChild(QLabel, "consoleBuildOrigin").text() == expected_source
    assert window.findChild(QPushButton, "switchOfficialVersion") is not None
    assert window.findChild(QPushButton, "checkDesktopUpdate") is None
    assert window._nav_buttons["settings"].text() == "固件与系统"
    assert not window._nav_buttons["settings"].property("desktopUpdateState")
    assert ui.progress.isHidden()
    assert ui.row.isHidden()

    window._nav_buttons["settings"].set_update_notice(firmware=True)
    assert window._nav_buttons["settings"].firmware_notice_visible()


def test_custom_demo_about_page_does_not_claim_official_origin(session):
    window, vm, _gateway, _snapshot, _store = session
    window._build_identity = BuildIdentity("custom")
    window._settings_section = "system"
    vm.navigate("settings")

    assert window.findChild(QLabel, "consoleBuildOrigin").text() == "自定义版本"
    switch = window.findChild(QPushButton, "switchOfficialVersion")
    assert switch is not None
    assert not switch.isEnabled()
    assert window.findChild(QPushButton, "checkDesktopUpdate") is None


def test_custom_switch_cancel_and_confirm_only_open_configured_download_page(
    session, tmp_path
):
    decisions = iter((False, True))
    confirmations: list[str] = []
    opened: list[str] = []

    def confirm(message: str) -> bool:
        confirmations.append(message)
        return next(decisions)

    def open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    identity = BuildIdentity("custom")
    window, vm, gateway, ui, updater = _attach_update_ui(
        session,
        tmp_path,
        identity,
        confirm_switch=confirm,
        official_url_resolver=lambda _source: (
            'https://updates.example.test/BORING-Console-1.2.3.dmg'
        ),
        url_opener=open_url,
    )
    vm.rename_profile(vm.draft.config["active_profile"], "My DIY draft")
    dirty_before = vm.draft.is_dirty
    window._settings_section = "system"
    vm.navigate("settings")
    switch = window.findChild(QPushButton, "switchOfficialVersion")

    switch.click()
    assert opened == []
    switch.click()
    assert opened == [
        "https://updates.example.test/BORING-Console-1.2.3.dmg"
    ]
    assert len(confirmations) == 2
    assert "官方版不包含你的自定义修改" in confirmations[-1]
    assert "DIY 程序、源码和数据会保留" in confirmations[-1]
    assert ui.build_identity is identity
    assert vm.draft.is_dirty is dirty_before
    assert gateway.commands == []
    assert updater.calls == []


def test_custom_switch_open_failure_keeps_retry_action(
    session, tmp_path
):
    failures: list[str] = []
    window, vm, _gateway, _ui, _updater = _attach_update_ui(
        session,
        tmp_path,
        BuildIdentity("custom"),
        confirm_switch=lambda _message: True,
        official_url_resolver=lambda _source: (
            'https://updates.example.test/BORING-Console-1.2.3.dmg'
        ),
        url_opener=lambda _url: False,
        open_failure_notifier=failures.append,
    )
    window._settings_section = "system"
    vm.navigate("settings")
    switch = window.findChild(QPushButton, "switchOfficialVersion")

    switch.click()

    assert failures == ["暂时无法获取官方安装包，请重试。"]
    assert switch.isEnabled()
    assert switch.isVisibleTo(window)


def test_official_about_page_keeps_existing_update_check(session, tmp_path):
    window, vm, _gateway, _ui, updater = _attach_update_ui(
        session, tmp_path, BuildIdentity("official")
    )
    window._settings_section = "system"
    vm.navigate("settings")

    assert window.findChild(QLabel, "consoleBuildOrigin").text() == "官方版本"
    assert window.findChild(QPushButton, "switchOfficialVersion") is None
    check = window.findChild(QPushButton, "checkDesktopUpdate")
    assert check is not None
    check.click()
    assert updater.calls == ["check"]


def test_update_source_has_separate_official_download_feeds():
    path = Path(__file__).resolve().parents[1] / "src/controller_config/assets/app-update-source.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    sources = config["official_download_sources"]
    assert sources["macos"]["format"] == "sparkle"
    assert sources["windows"]["format"] == "json"
    assert _official_download_source("darwin") == sources["macos"]
    assert _official_download_source("win32") == sources["windows"]


@pytest.mark.parametrize(
    ("source", "payload", "expected"),
    [
        (
            {"feed_url": "https://updates.example.test/appcast.xml", "format": "sparkle"},
            b'<rss><channel><item><enclosure url="https://updates.example.test/BORING-Console-1.2.3.dmg" /></item></channel></rss>',
            "https://updates.example.test/BORING-Console-1.2.3.dmg",
        ),
        (
            {"feed_url": "https://updates.example.test/update.json", "format": "json"},
            b'{"url":"https://updates.example.test/BORING-Console-1.2.3-Setup.exe"}',
            "https://updates.example.test/BORING-Console-1.2.3-Setup.exe",
        ),
    ],
)
def test_official_download_resolver_opens_package_from_current_feed(
    source, payload, expected
):
    assert _resolve_official_download_url(source, payload) == expected


def test_custom_switch_uses_qt_network_then_opens_current_package(
    session, tmp_path
):
    class Reply(QObject):
        finished = Signal()

        def error(self):
            return QNetworkReply.NetworkError.NoError

        def errorString(self):
            return ""

        def readAll(self):
            return b'<rss><channel><item><enclosure url="https://updates.example.test/BORING-Console-1.2.3.dmg" /></item></channel></rss>'

        def deleteLater(self):
            pass

    class Network:
        def __init__(self):
            self.reply = Reply()
            self.requests = []

        def get(self, request):
            self.requests.append(request.url().toString())
            return self.reply

    network = Network()
    opened = []
    _window, _vm, _gateway, ui, _updater = _attach_update_ui(
        session,
        tmp_path,
        BuildIdentity("custom"),
        confirm_switch=lambda _message: True,
        official_network=network,
        url_opener=lambda url: opened.append(url.toString()) or True,
    )
    ui.official_download_source = {
        "feed_url": "https://updates.example.test/appcast.xml",
        "format": "sparkle",
    }

    assert ui.switch_to_official()
    assert network.requests == ["https://updates.example.test/appcast.xml"]
    assert opened == []
    network.reply.finished.emit()

    assert opened == [
        "https://updates.example.test/BORING-Console-1.2.3.dmg"
    ]


@pytest.mark.parametrize(
    ("source", "en", "ja"),
    [
        ("自定义版本", "Custom build", "カスタム版"),
        ("构建信息不可用", "Build information unavailable", "ビルド情報を確認できません"),
        ("官方版本", "Official build", "公式版"),
        ("切换到官方版本", "Get the official version", "公式版を入手"),
        ("下载官方版本", "Download official version", "公式版をダウンロード"),
        ("暂时无法获取官方安装包，请重试。", "Could not get the official installer. Try again.", "公式インストーラーを取得できませんでした。もう一度お試しください。"),
        (
            "另一版本的 BORING 控制台已设置登录后启动；请先在该版本中关闭。",
            "Another BORING Console starts at login. Turn that option off in the other version first.",
            "別の BORING コンソールがログイン時に起動する設定です。先に別バージョンでオフにしてください。",
        ),
        (
            "检测到不支持 Hooks 归属切换的旧官方版。请先升级官方版，再在 DIY 版中启用 Claude Hooks。",
            "An older official build cannot switch Hooks ownership. Update the official Console before enabling Claude Hooks in the DIY build.",
            "Hooks の所有権切り替えに対応していない旧公式版があります。公式コンソールを更新してから、DIY 版で Claude Hooks を有効にしてください。",
        ),
    ],
)
def test_build_origin_copy_is_complete_in_all_languages(source, en, ja):
    from controller_config.text_catalog import TextCatalog

    catalog = TextCatalog.load()
    assert catalog.translate(source, "en_US") == en
    assert catalog.translate(source, "ja_JP") == ja
