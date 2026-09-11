from __future__ import annotations

import plistlib
import sys
from uuid import uuid4

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QLabel, QMenu, QProgressBar, QPushButton, QWidget

import controller_config.background_helper as background_helper
from controller_config.background_helper import (
    ApplicationInstanceCoordinator,
    BACKGROUND_ENABLED_KEY,
    _CodexQuotaProgress,
    _configure_translucent_tray_menu,
    MacLaunchAgent,
    PromptBackgroundController,
    _MacTextStatusItem,
    _menu_bar_usage_label,
    _provider_menu_bar_usage_label,
)
from controller_config.codex_usage import (
    CodexQuotaBucket,
    CodexQuotaWindow,
    CodexUsageSnapshot,
    CodexUsageStatus,
)
from controller_config.i18n import ENGLISH, SIMPLIFIED_CHINESE, LanguageManager


def test_mac_launch_agent_contains_only_background_configurator_command(tmp_path) -> None:
    launch_agent = MacLaunchAgent(
        ("/Applications/BORING.app/Contents/MacOS/BORING", "--background"),
        directory=tmp_path,
    )

    launch_agent.set_enabled(True)

    value = plistlib.loads(launch_agent.path.read_bytes())
    assert value == {
        "Label": "com.boring.controller-config.community.prompt-helper",
        "ProgramArguments": [
            "/Applications/BORING.app/Contents/MacOS/BORING",
            "--background",
        ],
        "RunAtLoad": True,
    }
    assert launch_agent.enabled

    launch_agent.set_enabled(False)
    assert not launch_agent.path.exists()


def test_background_program_arguments_use_python_module_for_source(monkeypatch) -> None:
    monkeypatch.setattr(sys, "executable", "/development/venv/bin/python")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(background_helper, "__compiled__", raising=False)

    assert background_helper.background_program_arguments() == (
        "/development/venv/bin/python",
        "-m",
        "controller_config.app",
        "--background",
    )


@pytest.mark.parametrize("runtime", ("frozen", "nuitka"))
def test_native_background_launch_agent_arguments_are_accepted_by_app(
    tmp_path, monkeypatch, runtime
) -> None:
    from controller_config.app import build_parser

    executable = "/Applications/BORING Console.app/Contents/MacOS/BORING"
    monkeypatch.setattr(sys, "executable", executable)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(background_helper, "__compiled__", raising=False)
    if runtime == "frozen":
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.setattr(background_helper, "__compiled__", object(), raising=False)
    launch_agent = MacLaunchAgent(
        background_helper.background_program_arguments(), directory=tmp_path
    )

    launch_agent.set_enabled(True)

    arguments = plistlib.loads(launch_agent.path.read_bytes())["ProgramArguments"]
    assert arguments == [executable, "--background"]
    assert build_parser().parse_args(arguments[1:]).background


def test_background_preference_does_not_claim_tray_on_unsupported_platform(
    qapp, tmp_path
) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue(BACKGROUND_ENABLED_KEY, True)
    controller = PromptBackgroundController(
        qapp,
        shutdown=lambda: None,
        settings=settings,
        platform="linux",
    )

    assert not controller.background_enabled
    assert not controller.hide_window()
    assert "关闭控制台窗口会退出助手" in controller.runtime_message


def test_second_configurator_instance_activates_existing_process(qtbot) -> None:
    name = f"bct-{uuid4().hex[:8]}"
    primary = ApplicationInstanceCoordinator(name)
    secondary = ApplicationInstanceCoordinator(name)
    activations = []
    primary.activate_requested.connect(lambda: activations.append(True))

    try:
        try:
            assert primary.acquire(activate_existing=False)
        except RuntimeError as exc:
            pytest.skip(f"当前测试沙箱不允许创建本地 IPC socket：{exc}")
        assert not secondary.acquire(activate_existing=True)
        qtbot.waitUntil(lambda: activations == [True], timeout=1000)
    finally:
        secondary.close()
        primary.close()


def test_macos_activation_restores_hidden_console(qapp, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(background_helper.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(PromptBackgroundController, "background_enabled", property(lambda self: True))
    controller = PromptBackgroundController(
        qapp, shutdown=lambda: None, platform="darwin",
        settings=QSettings(str(tmp_path / "reopen.ini"), QSettings.IniFormat),
    )
    window = QWidget()
    qtbot.addWidget(window)
    controller.attach_window(window)
    try:
        qapp.applicationStateChanged.emit(Qt.ApplicationInactive)
        assert window.isHidden()
        # Login startup with --background must stay hidden.
        qapp.applicationStateChanged.emit(Qt.ApplicationActive)
        assert window.isHidden()
        window.show()
        assert controller.hide_window()
        qapp.applicationStateChanged.emit(Qt.ApplicationActive)
        assert window.isVisible()
        assert controller.hide_window()
        # Finder/Dock sends active again even when the app was already active.
        qapp.applicationStateChanged.emit(Qt.ApplicationActive)
        assert window.isVisible()
        assert controller.hide_window()
        controller._quitting = True
        qapp.applicationStateChanged.emit(Qt.ApplicationActive)
        assert window.isHidden()
    finally:
        controller.deleteLater()


def test_macos_native_reopen_restores_hidden_console(qapp, qtbot, tmp_path, monkeypatch):
    if sys.platform != "darwin" or qapp.platformName() != "cocoa":
        pytest.skip("Requires Cocoa's real reopen event delivery")
    import AppKit
    monkeypatch.setattr(background_helper.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    monkeypatch.setattr(PromptBackgroundController, "background_enabled", property(lambda self: True))
    controller = PromptBackgroundController(
        qapp, shutdown=lambda: None, platform="darwin",
        settings=QSettings(str(tmp_path / "native-reopen.ini"), QSettings.IniFormat),
    )
    window = QWidget()
    qtbot.addWidget(window)
    controller.attach_window(window)
    try:
        window.show()
        qtbot.waitExposed(window)
        for _ in range(2):
            assert controller.hide_window()
            qtbot.wait(30)
            AppKit.NSApp.delegate().applicationShouldHandleReopen_hasVisibleWindows_(AppKit.NSApp, False)
            qtbot.waitUntil(window.isVisible, timeout=1000)
    finally:
        controller.deleteLater()


@pytest.mark.parametrize("popup_kind", ["menu", "combo"])
def test_macos_app_deactivation_dismisses_popups(qapp, qtbot, tmp_path, monkeypatch, popup_kind):
    if qapp.platformName() != "cocoa":
        pytest.skip("Requires native Cocoa activation events; emitting a Qt signal does not deactivate the app")
    from PySide6.QtWidgets import QComboBox
    monkeypatch.setattr(background_helper.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    controller = PromptBackgroundController(
        qapp, shutdown=lambda: None, platform="darwin",
        settings=QSettings(str(tmp_path / "popup.ini"), QSettings.IniFormat),
    )
    window = QWidget()
    qtbot.addWidget(window)
    window.resize(600, 400)
    controller.attach_window(window)
    window.show()
    qtbot.waitExposed(window)
    try:
        if sys.platform == "darwin" and qapp.platformName() == "cocoa":
            import AppKit
            AppKit.NSApp.delegate().applicationDidBecomeActive_(None)
            qtbot.waitUntil(lambda: qapp.applicationState() == Qt.ApplicationActive)
        if popup_kind == "menu":
            popup = QMenu(window)
            popup.addAction("配置方案")
            popup.popup(window.mapToGlobal(QPoint(30, 60)))
        else:
            combo = QComboBox(window)
            combo.addItems(["键盘按键", "鼠标按键"])
            combo.show()
            combo.showPopup()
            popup = combo.view().window()
        assert popup.isVisible()
        if sys.platform == "darwin" and qapp.platformName() == "cocoa":
            import AppKit
            AppKit.NSApp.delegate().applicationDidResignActive_(None)
            qtbot.waitUntil(lambda: not popup.isVisible(), timeout=1000)
        else:
            qapp.applicationStateChanged.emit(Qt.ApplicationInactive)
            assert not popup.isVisible()
        assert window.isVisible()
    finally:
        controller.deleteLater()


def test_menu_bar_usage_shows_primary_and_weekly_remaining_quota() -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex",
                limit_name=None,
                plan_type="pro",
                primary=CodexQuotaWindow(12, 300, None),
                secondary=CodexQuotaWindow(34, 10_080, None),
            ),
        ),
        source="app_server",
    )

    assert _menu_bar_usage_label(snapshot) == "Codex  5H 88%  ·  7D 66%"


def test_menu_bar_usage_keeps_last_values_while_refreshing() -> None:
    previous = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex",
                limit_name=None,
                plan_type="pro",
                primary=CodexQuotaWindow(1.5, 300, None),
                secondary=None,
            ),
        ),
        source="app_server",
    )
    refreshing = CodexUsageSnapshot.loading(previous)

    assert _menu_bar_usage_label(refreshing) == "Codex  5H 98.5%"


def test_menu_bar_usage_has_clear_empty_state() -> None:
    snapshot = CodexUsageSnapshot.unavailable()

    assert _menu_bar_usage_label(snapshot) == "Codex —"


def test_menu_bar_usage_supports_claude_code_windows() -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="claude",
                limit_name="Claude Code",
                plan_type="max",
                primary=CodexQuotaWindow(28, 300, None),
                secondary=CodexQuotaWindow(41, 10_080, None),
            ),
        ),
        source="claude_oauth",
    )

    assert _provider_menu_bar_usage_label(
        snapshot,
        provider_name="Claude",
        limit_id="claude",
    ) == "Claude  5H 72%  ·  7D 59%"


def test_menu_bar_title_does_not_mislabel_a_specialized_bucket_as_codex() -> None:
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex_bengalfox",
                limit_name="GPT-5.3-Codex-Spark",
                plan_type="pro",
                primary=CodexQuotaWindow(4, 300, None),
                secondary=None,
            ),
        ),
        source="app_server",
    )

    assert _menu_bar_usage_label(snapshot) == "Codex —"


def test_menu_bar_quota_progress_keeps_text_external_and_uses_remaining_quota(
    qapp,
) -> None:
    primary = _CodexQuotaProgress("primary")
    secondary = _CodexQuotaProgress("secondary")

    primary.set_window(CodexQuotaWindow(12, 300, None))
    secondary.set_window(CodexQuotaWindow(34, 10_080, None))

    assert primary.progress.value() == 88
    assert secondary.progress.value() == 66
    assert not primary.progress.isTextVisible()
    assert "#54b9d1" in primary.progress.styleSheet()
    assert "#e85b42" in secondary.progress.styleSheet()

    primary.set_window(None)
    assert primary.isHidden()


def test_tray_menu_uses_readable_v4_opaque_fallback(qapp) -> None:
    menu = QMenu()

    _configure_translucent_tray_menu(menu)

    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert menu.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert "background-color: #242320" in menu.styleSheet()
    assert "border-radius: 18px" in menu.styleSheet()


def test_background_menu_keeps_top_numbers_and_updates_both_quota_bars(
    qapp, tmp_path, monkeypatch
) -> None:
    class FakeSignal:
        def connect(self, _callback) -> None:
            pass

    class FakeUsageMonitor:
        def __init__(self) -> None:
            self.snapshot = CodexUsageSnapshot.unavailable()
            self.changed = FakeSignal()
            self.refresh_calls = 0

        def refresh(self) -> None:
            self.refresh_calls += 1

    class FakeStatusItem:
        def __init__(self, _on_click) -> None:
            self.title = ""
            self.tooltip = ""

        def set_title(self, title: str) -> None:
            self.title = title

        def set_tooltip(self, tooltip: str) -> None:
            self.tooltip = tooltip

        def hide(self) -> None:
            pass

    monkeypatch.setattr(
        background_helper.QSystemTrayIcon,
        "isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(background_helper, "_MacTextStatusItem", FakeStatusItem)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monitor = FakeUsageMonitor()
    claude_monitor = FakeUsageMonitor()
    controller = PromptBackgroundController(
        qapp,
        shutdown=lambda: None,
        settings=settings,
        codex_usage_monitor=monitor,
        claude_usage_monitor=claude_monitor,
        platform="darwin",
    )
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex",
                limit_name=None,
                plan_type="pro",
                primary=CodexQuotaWindow(12, 300, None),
                secondary=CodexQuotaWindow(34, 10_080, None),
            ),
            CodexQuotaBucket(
                limit_id="codex_bengalfox",
                limit_name="GPT-5.3-Codex-Spark",
                plan_type="pro",
                primary=CodexQuotaWindow(4, 300, 1_800_000_000),
                secondary=None,
            ),
        ),
        source="app_server",
        updated_at=1_799_999_000,
    )

    controller.set_codex_usage_snapshot(snapshot)
    controller.set_claude_usage_snapshot(
        CodexUsageSnapshot(
            status=CodexUsageStatus.AVAILABLE,
            buckets=(
                CodexQuotaBucket(
                    limit_id="claude",
                    limit_name="Claude Code",
                    plan_type="max",
                    primary=CodexQuotaWindow(28, 300, None),
                    secondary=CodexQuotaWindow(41, 10_080, None),
                ),
            ),
            source="claude_oauth",
            updated_at=1_799_999_000,
        )
    )

    assert controller._native_status_item.title == "Codex  5H 88%  ·  7D 66%"
    assert controller._native_claude_status_item.title == "Claude  5H 72%  ·  7D 59%"
    assert controller._native_status_item.tooltip.startswith("Codex  5H 88%")
    assert "Codex Codex" not in controller._native_status_item.tooltip
    panel = controller._tray_usage_panel
    assert panel is not None
    assert [
        label.text()
        for label in panel.findChildren(QLabel, "menuBarQuotaBucketName")
    ] == ["Codex", "GPT-5.3-Codex-Spark", "Claude Code"]
    assert [
        progress.value()
        for progress in panel.findChildren(QProgressBar, "menuBarQuotaProgress")
    ] == [88, 66, 96, 72, 59]
    assert "实时数据" in panel.codex.source.text()
    assert "更新于" in panel.codex.source.text()
    assert "Claude Code 额度" in panel.claude.source.text()
    assert "实时数据" in panel.claude.source.text()
    spark_line = next(
        label
        for label in panel.findChildren(QLabel, "menuBarQuotaWindowLabel")
        if label.property("limitId") == "codex_bengalfox"
    )
    assert "剩余 96%" in spark_line.text()
    assert "预计恢复" in spark_line.text()
    assert controller._tray_usage_panel_action is not None
    assert controller._tray_usage_panel_action.isVisible()
    assert controller._tray_refresh_usage is not None
    controller._tray_refresh_usage.click()
    assert monitor.refresh_calls == 1
    assert claude_monitor.refresh_calls == 1

    first_bucket = panel.codex._bucket_widgets[0]
    initial_source = panel.codex.source.text()
    controller.set_codex_usage_snapshot(CodexUsageSnapshot.loading(snapshot))
    assert panel.codex._bucket_widgets[0] is first_bucket
    assert "正在更新" in panel.codex.source.text()
    assert controller._tray_refresh_usage.isEnabled()
    controller._tray_refresh_usage.click()
    assert monitor.refresh_calls == 1
    assert claude_monitor.refresh_calls == 2
    controller.set_claude_usage_snapshot(CodexUsageSnapshot.loading(snapshot))
    assert not controller._tray_refresh_usage.isEnabled()

    updated = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex",
                limit_name=None,
                plan_type="pro",
                primary=CodexQuotaWindow(20, 300, None),
                secondary=CodexQuotaWindow(34, 10_080, None),
            ),
            snapshot.buckets[1],
        ),
        source="app_server",
        updated_at=1_800_000_000,
    )
    controller.set_codex_usage_snapshot(updated)
    assert panel.codex._bucket_widgets[0] is first_bucket
    assert (
        panel.codex._bucket_widgets[0]
        .findChild(QProgressBar, "menuBarQuotaProgress")
        .value()
        == 80
    )
    assert panel.codex.source.text() != initial_source
    assert controller._tray_refresh_usage.isEnabled()


def test_refresh_usage_keeps_menu_open_without_showing_console(
    qapp, qtbot, tmp_path, monkeypatch
) -> None:
    class FakeSignal:
        def connect(self, _callback) -> None:
            pass

    class FakeUsageMonitor:
        def __init__(self) -> None:
            self.snapshot = CodexUsageSnapshot.unavailable()
            self.changed = FakeSignal()
            self.refresh_calls = 0

        def refresh(self) -> None:
            self.refresh_calls += 1

    class FakeStatusItem:
        def __init__(self, _on_click) -> None:
            pass

        def set_title(self, _title: str) -> None:
            pass

        def set_tooltip(self, _tooltip: str) -> None:
            pass

        def hide(self) -> None:
            pass

    monkeypatch.setattr(
        background_helper.QSystemTrayIcon,
        "isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(background_helper, "_MacTextStatusItem", FakeStatusItem)
    monitor = FakeUsageMonitor()
    claude_monitor = FakeUsageMonitor()
    controller = PromptBackgroundController(
        qapp,
        shutdown=lambda: None,
        settings=QSettings(
            str(tmp_path / "persistent-menu-settings.ini"),
            QSettings.Format.IniFormat,
        ),
        codex_usage_monitor=monitor,
        claude_usage_monitor=claude_monitor,
        platform="darwin",
    )
    window = QWidget()
    controller.attach_window(window)
    controller.set_codex_usage_snapshot(
        CodexUsageSnapshot(
            status=CodexUsageStatus.AVAILABLE,
            buckets=(
                CodexQuotaBucket(
                    limit_id="codex",
                    limit_name=None,
                    plan_type="pro",
                    primary=CodexQuotaWindow(25, 10_080, None),
                    secondary=None,
                ),
            ),
            source="app_server",
            updated_at=1_799_999_000,
        )
    )
    menu = controller._tray_menu
    refresh = controller._tray_refresh_usage
    assert menu is not None
    assert refresh is not None

    menu.popup(QPoint(40, 40))
    qtbot.waitUntil(menu.isVisible, timeout=1000)
    assert isinstance(refresh, QPushButton)
    qtbot.mouseClick(refresh, Qt.MouseButton.LeftButton)

    assert monitor.refresh_calls == 1
    assert claude_monitor.refresh_calls == 1
    assert menu.isVisible()
    assert window.isHidden()

    controller.set_codex_usage_snapshot(
        CodexUsageSnapshot(
            status=CodexUsageStatus.AVAILABLE,
            buckets=(
                CodexQuotaBucket(
                    limit_id="codex",
                    limit_name=None,
                    plan_type="pro",
                    primary=CodexQuotaWindow(21, 10_080, None),
                    secondary=None,
                ),
            ),
            source="app_server",
            updated_at=1_800_000_000,
        )
    )
    panel = controller._tray_usage_panel
    assert panel is not None
    assert panel.findChild(QProgressBar, "menuBarQuotaProgress").value() == 79
    assert menu.isVisible()
    assert window.isHidden()


def test_background_menu_surfaces_quota_read_failure(qapp, tmp_path, monkeypatch) -> None:
    class FakeStatusItem:
        def __init__(self, _on_click) -> None:
            self.title = ""

        def set_title(self, title: str) -> None:
            self.title = title

        def set_tooltip(self, _tooltip: str) -> None:
            pass

        def hide(self) -> None:
            pass

    monkeypatch.setattr(
        background_helper.QSystemTrayIcon,
        "isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(background_helper, "_MacTextStatusItem", FakeStatusItem)
    controller = PromptBackgroundController(
        qapp,
        shutdown=lambda: None,
        settings=QSettings(
            str(tmp_path / "error-settings.ini"),
            QSettings.Format.IniFormat,
        ),
        platform="darwin",
    )

    controller.set_codex_usage_snapshot(
        CodexUsageSnapshot(
            status=CodexUsageStatus.ERROR,
            message="Codex 本地服务无响应",
        )
    )

    panel = controller._tray_usage_panel
    assert panel is not None
    assert "读取失败" in panel.codex.source.text()
    assert not panel.codex.message.isHidden()
    assert panel.codex.message.text() == "Codex 本地服务无响应"

    controller.set_codex_usage_snapshot(
        CodexUsageSnapshot(
            status=CodexUsageStatus.AVAILABLE,
            buckets=(
                CodexQuotaBucket(
                    limit_id="codex",
                    limit_name=None,
                    plan_type="pro",
                    primary=CodexQuotaWindow(18, 300, None),
                    secondary=None,
                ),
            ),
            source="session_log",
            updated_at=1_799_999_000,
            message="来自 Codex 本地会话日志，可能滞后",
        )
    )
    assert "本地日志 · 可能滞后" in panel.codex.source.text()
    assert panel.findChild(QProgressBar, "menuBarQuotaProgress").value() == 82


def test_background_quota_panel_retranslates_without_reopening(
    qapp, tmp_path, monkeypatch
) -> None:
    class FakeStatusItem:
        def __init__(self, _on_click) -> None:
            self.title = ""

        def set_title(self, title: str) -> None:
            self.title = title

        def set_tooltip(self, _tooltip: str) -> None:
            pass

        def hide(self) -> None:
            pass

    monkeypatch.setattr(
        background_helper.QSystemTrayIcon,
        "isSystemTrayAvailable",
        staticmethod(lambda: True),
    )
    monkeypatch.setattr(background_helper, "_MacTextStatusItem", FakeStatusItem)
    manager = LanguageManager(
        qapp,
        settings=QSettings(
            str(tmp_path / "language-settings.ini"),
            QSettings.Format.IniFormat,
        ),
        initial_language=ENGLISH,
        persist=False,
    )
    controller = PromptBackgroundController(
        qapp,
        shutdown=lambda: None,
        settings=QSettings(
            str(tmp_path / "background-settings.ini"),
            QSettings.Format.IniFormat,
        ),
        codex_usage_monitor=background_helper.CodexUsageMonitor(),
        language_manager=manager,
        platform="darwin",
    )
    snapshot = CodexUsageSnapshot(
        status=CodexUsageStatus.AVAILABLE,
        buckets=(
            CodexQuotaBucket(
                limit_id="codex",
                limit_name=None,
                plan_type="plus",
                primary=CodexQuotaWindow(25, 300, 1_800_000_000),
                secondary=None,
            ),
        ),
        source="app_server",
        updated_at=1_799_999_000,
    )

    try:
        controller.set_codex_usage_snapshot(snapshot)
        panel = controller._tray_usage_panel
        assert panel is not None
        line = panel.findChild(QLabel, "menuBarQuotaWindowLabel")
        assert line is not None
        assert "5-hour window" in line.text()
        assert "Remaining 75%" in line.text()
        assert "Expected recovery" in line.text()
        assert "Codex Usage · Live data · Updated" in panel.codex.source.text()
        assert controller._tray_refresh_usage is not None
        assert controller._tray_refresh_usage.text() == "Update Usage Information"
        assert controller._tray_show_console is not None
        assert controller._tray_show_console.text() == "Open BORING Console"
        assert controller._tray_status is not None
        assert controller._tray_status.text() == (
            "Helper Offline · waiting for a device connection"
        )

        manager.set_language(SIMPLIFIED_CHINESE)
        translated_line = panel.codex._bucket_widgets[0].findChild(
            QLabel, "menuBarQuotaWindowLabel"
        )
        assert translated_line is not None
        assert "5 小时窗口" in translated_line.text()
        assert "Codex 额度 · 实时数据 · 更新于" in panel.codex.source.text()
        assert controller._tray_refresh_usage.text() == "更新额度信息"
        assert controller._tray_status.text() == "助手离线 · 等待设备连接"
    finally:
        if manager.language != SIMPLIFIED_CHINESE:
            manager.set_language(SIMPLIFIED_CHINESE)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native menu bar only")
def test_native_menu_bar_item_keeps_quota_as_readable_text(qapp) -> None:
    activations = []
    item = _MacTextStatusItem(lambda: activations.append(True))
    try:
        item.set_title("Codex  5H 88%  ·  7D 66%")
        item.set_tooltip("Codex 剩余额度")

        assert item._button.title() == "Codex  5H 88%  ·  7D 66%"
        assert item._button.toolTip() == "Codex 剩余额度"
        item._button.performClick_(None)
        assert activations == [True]
    finally:
        item.hide()
