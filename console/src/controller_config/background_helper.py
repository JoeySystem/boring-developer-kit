from __future__ import annotations

import plistlib
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from controller_config.codex_usage import (
    CodexQuotaBucket,
    CodexQuotaWindow,
    CodexUsageMonitor,
    CodexUsageSnapshot,
    CodexUsageStatus,
)
from controller_config.claude_usage import ClaudeUsageMonitor
from controller_config.appearance import (
    MACOS_GLASS_MENU_STYLE,
    install_macos_vibrancy,
)
from controller_config.i18n import LanguageManager, translate_ui_text


BACKGROUND_ENABLED_KEY = "prompt_helper/background_enabled"
LOGIN_ENABLED_KEY = "prompt_helper/start_at_login"
MENU_BAR_DISPLAY_KEY = "prompt_helper/menu_bar_display"
MENU_BAR_DISPLAYS = {
    "compact": "紧凑 · 仅 BORING",
    "codex": "仅显示 Codex",
    "claude": "仅显示 Claude",
    "both": "显示 Codex 和 Claude",
}

try:
    from AppKit import NSStatusBar, NSVariableStatusItemLength
    from Foundation import NSObject
except ImportError:  # Installed only for the supported macOS runtime.
    NSStatusBar = None
    NSVariableStatusItemLength = None
    NSObject = object


class _MacStatusItemTarget(NSObject):
    def clicked_(self, _sender) -> None:
        callback = getattr(self, "callback", None)
        if callback is not None:
            callback()


class _MacTextStatusItem:
    """Native variable-width macOS menu-bar item for readable quota text."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        if NSStatusBar is None or NSVariableStatusItemLength is None:
            raise RuntimeError("macOS 原生菜单栏运行库不可用")
        self._status_bar = NSStatusBar.systemStatusBar()
        self._item = self._status_bar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        button = self._item.button()
        if button is None:
            self._status_bar.removeStatusItem_(self._item)
            raise RuntimeError("无法创建 macOS 菜单栏按钮")
        self._target = _MacStatusItemTarget.alloc().init()
        self._target.callback = on_click
        button.setTarget_(self._target)
        button.setAction_("clicked:")
        self._button = button
        self._visible = True

    def set_title(self, title: str) -> None:
        self._button.setTitle_(title)

    def set_tooltip(self, tooltip: str) -> None:
        self._button.setToolTip_(tooltip)

    def hide(self) -> None:
        if not self._visible:
            return
        self._status_bar.removeStatusItem_(self._item)
        self._visible = False


class _CodexQuotaProgress(QWidget):
    """Compact, non-interactive quota bar embedded in the tray menu."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        color = "#FF6A00"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 2)
        self.progress = QProgressBar(objectName="menuBarQuotaProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet(
            "QProgressBar {"
            " background: #34383c; border: none; border-radius: 2px;"
            "}"
            "QProgressBar::chunk {"
            f" background: {color}; border: none; border-radius: 2px;"
            "}"
        )
        layout.addWidget(self.progress)

    def set_window(self, window: CodexQuotaWindow | None) -> None:
        if window is None:
            self.progress.setValue(0)
            self.hide()
            return
        self.progress.setValue(round(window.remaining_percent))
        self.show()


class _UsageProviderSection(QWidget):
    """One provider's quota status and windows inside the menu-bar dropdown."""

    def __init__(self, provider_title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="menuBarUsageProvider")
        self._provider_title = provider_title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.source = QLabel(objectName="menuBarUsageSource")
        self.source.setWordWrap(True)
        layout.addWidget(self.source)
        self._buckets_layout = QVBoxLayout()
        self._buckets_layout.setContentsMargins(0, 0, 0, 0)
        self._buckets_layout.setSpacing(9)
        layout.addLayout(self._buckets_layout)
        self.message = QLabel(objectName="menuBarUsageMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self._bucket_widgets: list[QWidget] = []
        self._rendered_buckets: tuple[CodexQuotaBucket, ...] = ()

    def update_snapshot(
        self,
        snapshot: CodexUsageSnapshot,
        *,
        rebuild_buckets: bool = False,
    ) -> None:
        self.source.setText(
            _menu_bar_usage_source_line(snapshot, self._provider_title)
        )
        next_shape = tuple(_bucket_shape(bucket) for bucket in snapshot.buckets)
        current_shape = tuple(
            _bucket_shape(bucket) for bucket in self._rendered_buckets
        )
        same_shape = next_shape == current_shape
        if rebuild_buckets or not same_shape:
            for widget in self._bucket_widgets:
                self._buckets_layout.removeWidget(widget)
                widget.deleteLater()
            self._bucket_widgets.clear()

            for bucket in snapshot.buckets:
                section = self._bucket_section(bucket)
                self._bucket_widgets.append(section)
                self._buckets_layout.addWidget(section)
        else:
            for section, bucket in zip(self._bucket_widgets, snapshot.buckets):
                self._update_bucket_section(section, bucket)
        self._rendered_buckets = snapshot.buckets

        show_message = not snapshot.buckets or snapshot.status is CodexUsageStatus.ERROR
        self.message.setText(
            translate_ui_text(snapshot.message) if show_message else ""
        )
        self.message.setVisible(show_message and bool(snapshot.message))

    def _bucket_section(self, bucket: CodexQuotaBucket) -> QWidget:
        section = QWidget(objectName="menuBarQuotaBucket")
        section.setProperty("limitId", bucket.limit_id)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        heading = QHBoxLayout()
        name = QLabel(bucket.display_name, objectName="menuBarQuotaBucketName")
        heading.addWidget(name)
        heading.addStretch(1)
        if bucket.plan_type:
            plan = QLabel(bucket.plan_type.upper(), objectName="menuBarQuotaPlan")
            heading.addWidget(plan)
        layout.addLayout(heading)

        for role, window in (
            ("primary", bucket.primary),
            ("secondary", bucket.secondary),
        ):
            if window is None:
                continue
            label = QLabel(
                _menu_bar_window_line(window),
                objectName="menuBarQuotaWindowLabel",
            )
            label.setProperty("limitId", bucket.limit_id)
            label.setProperty("windowRole", role)
            label.setWordWrap(True)
            layout.addWidget(label)
            progress = _CodexQuotaProgress(role)
            progress.setProperty("limitId", bucket.limit_id)
            progress.setProperty("windowRole", role)
            progress.set_window(window)
            layout.addWidget(progress)
        return section

    def _update_bucket_section(
        self, section: QWidget, bucket: CodexQuotaBucket
    ) -> None:
        name = section.findChild(QLabel, "menuBarQuotaBucketName")
        if name is not None:
            name.setText(bucket.display_name)
        plan = section.findChild(QLabel, "menuBarQuotaPlan")
        if plan is not None and bucket.plan_type:
            plan.setText(bucket.plan_type.upper())
        windows = {"primary": bucket.primary, "secondary": bucket.secondary}
        for label in section.findChildren(QLabel, "menuBarQuotaWindowLabel"):
            window = windows.get(str(label.property("windowRole")))
            if window is not None:
                label.setText(_menu_bar_window_line(window))
        for progress in section.findChildren(_CodexQuotaProgress):
            progress.set_window(windows.get(str(progress.property("windowRole"))))


class _UsageMenuPanel(QWidget):
    """Codex and Claude Code quota details in one compact macOS menu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="menuBarUsagePanel")
        self.setFixedWidth(286)
        self.setStyleSheet(
            "QWidget#menuBarUsagePanel { background: transparent; }"
            "QLabel { background: transparent; color: #f5f5f7; }"
            "QLabel#menuBarUsageSource { color: rgba(235, 235, 245, 150);"
            " font-size: 11px; }"
            "QLabel#menuBarQuotaBucketName { font-size: 13px; font-weight: 700; }"
            "QLabel#menuBarQuotaPlan { color: #ff8068; font-size: 10px;"
            " font-weight: 700; }"
            "QLabel#menuBarQuotaWindowLabel { color: rgba(245, 245, 247, 210);"
            " font-size: 11px; }"
            "QLabel#menuBarUsageMessage { color: rgba(235, 235, 245, 170);"
            " font-size: 11px; }"
            "QFrame#menuBarUsageDivider {"
            " background: rgba(255, 255, 255, 30); border: none; max-height: 1px;"
            "}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 5)
        layout.setSpacing(9)
        self.codex = _UsageProviderSection("Codex 额度")
        self.claude = _UsageProviderSection("Claude Code 额度")
        divider = QFrame(objectName="menuBarUsageDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(self.codex)
        layout.addWidget(divider)
        layout.addWidget(self.claude)

    def update_snapshots(
        self,
        codex_snapshot: CodexUsageSnapshot,
        claude_snapshot: CodexUsageSnapshot,
        *,
        rebuild_buckets: bool = False,
    ) -> None:
        self.codex.update_snapshot(
            codex_snapshot,
            rebuild_buckets=rebuild_buckets,
        )
        self.claude.update_snapshot(
            claude_snapshot,
            rebuild_buckets=rebuild_buckets,
        )


def _bucket_shape(bucket: CodexQuotaBucket) -> tuple[str, bool, bool, bool]:
    return (
        bucket.limit_id,
        bucket.primary is not None,
        bucket.secondary is not None,
        bool(bucket.plan_type),
    )


class _PersistentMenuButton(QPushButton):
    """Menu command that does not dismiss the quota panel when clicked."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent, objectName="persistentTrayMenuButton")
        self.setFlat(True)
        self.setMinimumHeight(34)
        self.setStyleSheet(
            "QPushButton#persistentTrayMenuButton {"
            " background: transparent; color: #f5f5f7; border: none;"
            " border-radius: 7px; padding: 7px 12px; text-align: left;"
            "}"
            "QPushButton#persistentTrayMenuButton:hover {"
            " background: rgba(255, 255, 255, 28); color: #ffffff;"
            "}"
            "QPushButton#persistentTrayMenuButton:pressed {"
            " background: rgba(255, 255, 255, 38);"
            "}"
            "QPushButton#persistentTrayMenuButton:disabled {"
            " color: rgba(235, 235, 245, 138);"
            "}"
        )


def _configure_translucent_tray_menu(menu: QMenu) -> None:
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    menu.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
    menu.setStyleSheet(MACOS_GLASS_MENU_STYLE)


class ApplicationInstanceCoordinator(QObject):
    """Keep one serial-owning configurator process and reactivate it on launch."""

    activate_requested = Signal()

    def __init__(
        self,
        server_name: str = "com.boring.controller-config",
        *,
        origin: str = "official",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._server_name = server_name
        self._origin = "official" if origin == "official" else "custom"
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._primary = False
        self._conflict_message = ""

    @property
    def is_primary(self) -> bool:
        return self._primary

    @property
    def conflict_message(self) -> str:
        return self._conflict_message

    def acquire(self, *, activate_existing: bool) -> bool:
        if self._primary:
            return True
        if self._server.listen(self._server_name):
            self._primary = True
            return True

        socket = QLocalSocket(self)
        socket.connectToServer(self._server_name)
        if socket.waitForConnected(500):
            request = json.dumps(
                {
                    "action": "show" if activate_existing else "background",
                    "origin": self._origin,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            socket.write(request)
            socket.waitForBytesWritten(500)
            if socket.waitForReadyRead(500):
                try:
                    response = json.loads(bytes(socket.readAll()))
                except (UnicodeError, ValueError, TypeError):
                    response = {}
                if response.get("status") == "occupied":
                    self._conflict_message = (
                        "官方版 BORING 控制台正在运行。请先从菜单栏退出它，再打开当前版本。"
                        if response.get("origin") == "official"
                        else "DIY 版 BORING 控制台正在运行。请先从菜单栏退出它，再打开当前版本。"
                    )
                elif response.get("status") != "activated":
                    self._conflict_message = (
                        "另一份 BORING 控制台正在运行。请先从菜单栏退出它，再打开当前版本。"
                    )
            else:
                # An older process understands only the legacy activation
                # message and cannot report its origin. It still owns the
                # global device host, so do not start a second process.
                self._conflict_message = (
                    "另一份 BORING 控制台正在运行。请先从菜单栏退出它，再打开当前版本。"
                )
            socket.disconnectFromServer()
            return False

        # A crashed local process can leave the named socket behind. Only remove
        # it after a real connection attempt failed, then become the primary.
        QLocalServer.removeServer(self._server_name)
        if not self._server.listen(self._server_name):
            raise RuntimeError(
                f"无法建立控制台单实例通道：{self._server.errorString()}"
            )
        self._primary = True
        return True

    def close(self) -> None:
        if not self._primary:
            return
        self._server.close()
        QLocalServer.removeServer(self._server_name)
        self._primary = False

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.setParent(self)
            socket.disconnected.connect(socket.deleteLater)
            socket.readyRead.connect(lambda current=socket: self._consume(current))
            if socket.bytesAvailable():
                self._consume(socket)

    def _consume(self, socket: QLocalSocket) -> None:
        message = bytes(socket.readAll())
        response, _activate = self.handle_request(message)
        if response:
            socket.write(response)
        socket.disconnectFromServer()

    def handle_request(self, message: bytes) -> tuple[bytes, bool]:
        """Handle one request; kept separate so identity behavior is testable."""

        if message in {b"show", b"background"}:
            activate = message == b"show"
            if activate:
                self.activate_requested.emit()
            return b"", activate
        try:
            request = json.loads(message)
        except (UnicodeError, ValueError, TypeError):
            return b"", False
        if not isinstance(request, dict):
            return b"", False
        action = request.get("action")
        requester_origin = request.get("origin")
        same_origin = requester_origin == self._origin
        activate = same_origin and action == "show"
        if activate:
            self.activate_requested.emit()
        response = json.dumps(
            {
                "status": "activated" if same_origin else "occupied",
                "origin": self._origin,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return response, activate


class MacLaunchAgent:
    LABEL = "com.boring.controller-config.prompt-helper"

    def __init__(
        self,
        program_arguments: tuple[str, ...],
        *,
        directory: Path | None = None,
        label: str | None = None,
        other_labels: tuple[str, ...] = (),
    ) -> None:
        if not program_arguments:
            raise ValueError("后台助手启动命令不能为空")
        self._program_arguments = program_arguments
        self._directory = directory or Path.home() / "Library" / "LaunchAgents"
        self._label = label or self.LABEL
        self._other_labels = tuple(other_labels)

    @property
    def path(self) -> Path:
        return self._directory / f"{self._label}.plist"

    @property
    def enabled(self) -> bool:
        return self.path.is_file()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if any((self._directory / f"{label}.plist").is_file()
                   for label in self._other_labels):
                raise ValueError(
                    "另一版本的 BORING 控制台已设置登录后启动；请先在该版本中关闭。"
                )
            self._directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "Label": self._label,
                "ProgramArguments": list(self._program_arguments),
                "RunAtLoad": True,
            }
            temporary = self.path.with_suffix(".plist.tmp")
            temporary.write_bytes(plistlib.dumps(payload, sort_keys=True))
            temporary.replace(self.path)
        elif self.path.exists():
            self.path.unlink()


class PromptBackgroundController(QObject):
    """Keep the same serial-owning process alive behind a menu-bar icon."""

    state_changed = Signal()
    codex_usage_changed = Signal(object)

    def __init__(
        self,
        application: QApplication,
        *,
        shutdown: Callable[[], None],
        settings: QSettings | None = None,
        launch_agent: MacLaunchAgent | None = None,
        codex_usage_monitor: CodexUsageMonitor | None = None,
        claude_usage_monitor: ClaudeUsageMonitor | None = None,
        language_manager: LanguageManager | None = None,
        build_origin: str = "official",
        platform: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._application = application
        self._shutdown = shutdown
        self._settings = settings or QSettings()
        self._platform = platform or sys.platform
        self._window: QWidget | None = None
        self._window_closed_to_background = False
        self._quitting = False
        self._background_enabled = self._read_bool(BACKGROUND_ENABLED_KEY, True)
        display = self._settings.value(MENU_BAR_DISPLAY_KEY, "both")
        self._menu_bar_display = display if display in MENU_BAR_DISPLAYS else "both"
        self._launch_agent = launch_agent
        self._codex_usage_monitor = codex_usage_monitor
        self._claude_usage_monitor = claude_usage_monitor
        self._language_manager = language_manager
        self._custom_build = build_origin != "official"
        self._codex_usage_snapshot = (
            codex_usage_monitor.snapshot
            if codex_usage_monitor is not None
            else CodexUsageSnapshot.unavailable()
        )
        self._claude_usage_snapshot = (
            claude_usage_monitor.snapshot
            if claude_usage_monitor is not None
            else CodexUsageSnapshot(
                status=CodexUsageStatus.UNAVAILABLE,
                message=(
                    "未找到 Claude Code 登录信息。"
                    "请先在 Claude Code 中登录订阅账户。"
                ),
            )
        )
        self._tray: QSystemTrayIcon | None = None
        self._native_status_item: _MacTextStatusItem | None = None
        self._native_claude_status_item: _MacTextStatusItem | None = None
        self._tray_menu: QMenu | None = None
        self._tray_status: QAction | None = None
        self._tray_usage_panel: _UsageMenuPanel | None = None
        self._tray_usage_panel_action: QWidgetAction | None = None
        self._tray_refresh_usage: QPushButton | None = None
        self._tray_show_console: QAction | None = None
        self._tray_quit_helper: QAction | None = None
        self._helper_online = False
        self._helper_state_label = "助手离线"
        self._helper_status_message = "助手离线；等待设备连接"
        if self._platform in {"darwin", "win32"} and QSystemTrayIcon.isSystemTrayAvailable():
            self._build_tray()
            self._application.setQuitOnLastWindowClosed(False)
        if self._codex_usage_monitor is not None:
            self._codex_usage_monitor.changed.connect(self.set_codex_usage_snapshot)
        if self._claude_usage_monitor is not None:
            self._claude_usage_monitor.changed.connect(self.set_claude_usage_snapshot)
        if self._language_manager is not None:
            self._language_manager.language_changed.connect(
                lambda _language: self._retranslate_tray()
            )
        if self._platform == "darwin":
            # Finder/Dock reopens the existing process without launching a
            # second instance. Cocoa forwards that request as ApplicationActive.
            self._application.applicationStateChanged.connect(self._on_application_state_changed)

    @property
    def background_enabled(self) -> bool:
        return self._background_enabled and (
            self._native_status_item is not None or self._tray is not None
        )

    @property
    def login_enabled(self) -> bool:
        return self._launch_agent.enabled if self._launch_agent is not None else False

    @property
    def menu_bar_display(self) -> str:
        return self._menu_bar_display

    def set_menu_bar_display(self, display: str) -> None:
        if display not in MENU_BAR_DISPLAYS:
            raise ValueError("未知的菜单栏显示方式")
        self._menu_bar_display = display
        self._settings.setValue(MENU_BAR_DISPLAY_KEY, display)
        self._settings.sync()
        self._update_tray_usage()

    @property
    def quitting(self) -> bool:
        return self._quitting

    @property
    def runtime_message(self) -> str:
        if self.background_enabled:
            return "关闭控制台窗口后，助手仍会在菜单栏继续运行"
        return "关闭控制台窗口会退出助手并停止实体提示词监听"

    def attach_window(self, window: QWidget) -> None:
        self._window = window

    def set_background_enabled(self, enabled: bool) -> None:
        self._background_enabled = bool(enabled)
        self._settings.setValue(BACKGROUND_ENABLED_KEY, self._background_enabled)
        self._settings.sync()
        self.state_changed.emit()
        if not self._background_enabled and self._window is not None:
            self.show_window()

    def set_login_enabled(self, enabled: bool) -> None:
        if self._launch_agent is None:
            raise ValueError("当前运行环境不支持 macOS 登录启动")
        self._launch_agent.set_enabled(enabled)
        self._settings.setValue(LOGIN_ENABLED_KEY, bool(enabled))
        self._settings.sync()
        self.state_changed.emit()

    def set_helper_status(
        self, *, online: bool, state_label: str, message: str
    ) -> None:
        self._helper_online = bool(online)
        self._helper_state_label = state_label
        self._helper_status_message = message
        if self._tray_status is not None:
            translated_state = translate_ui_text(state_label)
            detail = translate_ui_text(message)
            for separator in ("；", ";"):
                prefix = f"{translated_state}{separator}"
                if detail.casefold().startswith(prefix.casefold()):
                    detail = detail[len(prefix) :].lstrip()
                    break
            status = translated_state
            if detail and detail.casefold() != translated_state.casefold():
                status = f"{translated_state} · {detail}"
            self._tray_status.setText(status)
        self._update_tray_tooltip()

    def set_codex_usage_snapshot(self, snapshot: CodexUsageSnapshot) -> None:
        self._codex_usage_snapshot = snapshot
        self._update_tray_usage()
        self.codex_usage_changed.emit(snapshot)

    @property
    def codex_usage_snapshot(self) -> CodexUsageSnapshot:
        return self._codex_usage_snapshot

    def set_claude_usage_snapshot(self, snapshot: CodexUsageSnapshot) -> None:
        self._claude_usage_snapshot = snapshot
        self._update_tray_usage()

    def refresh_usage(self) -> None:
        if (self._codex_usage_monitor is not None
                and self._codex_usage_snapshot.status is not CodexUsageStatus.LOADING):
            self._codex_usage_monitor.refresh()
        if (self._claude_usage_monitor is not None
                and self._claude_usage_snapshot.status is not CodexUsageStatus.LOADING):
            self._claude_usage_monitor.refresh()

    def hide_window(self) -> bool:
        if not self.background_enabled or self._quitting or self._window is None:
            return False
        self._window_closed_to_background = True
        self._window.hide()
        if self._tray is not None:
            self._tray.showMessage(
                (
                    "BORING 主机自动化助手在线"
                    if self._helper_online
                    else "BORING 主机自动化助手尚未就绪"
                ),
                self._helper_status_message,
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        return True

    def show_window(self) -> None:
        if self._window is None:
            return
        self._window_closed_to_background = False
        if self._window.isMinimized():
            self._window.showNormal()
        else:
            self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if (state == Qt.ApplicationState.ApplicationActive
                and not self._quitting and self._window is not None
                and (self._window_closed_to_background or self._window.isMinimized())):
            self.show_window()

    def quit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self._window is not None and not self._window.close():
            self._quitting = False
            return
        if self._window is None:
            self._shutdown()
        if self._tray is not None:
            self._tray.hide()
        if self._native_status_item is not None:
            self._native_status_item.hide()
        if self._native_claude_status_item is not None:
            self._native_claude_status_item.hide()
        self._application.quit()

    def _build_tray(self) -> None:
        menu = QMenu()
        _configure_translucent_tray_menu(menu)

        usage_panel = _UsageMenuPanel()
        usage_panel_action = QWidgetAction(menu)
        usage_panel_action.setDefaultWidget(usage_panel)
        menu.addAction(usage_panel_action)
        refresh_usage = _PersistentMenuButton(translate_ui_text("更新额度信息"))
        refresh_usage.setEnabled(
            self._codex_usage_monitor is not None
            or self._claude_usage_monitor is not None
        )
        refresh_usage.clicked.connect(self.refresh_usage)
        refresh_usage_action = QWidgetAction(menu)
        refresh_usage_action.setDefaultWidget(refresh_usage)
        menu.addAction(refresh_usage_action)
        menu.addSeparator()

        status = QAction("助手离线 · 等待设备连接", menu)
        status.setEnabled(False)
        menu.addAction(status)
        menu.addSeparator()
        self._tray_usage_panel = usage_panel
        self._tray_usage_panel_action = usage_panel_action
        self._tray_refresh_usage = refresh_usage
        self._tray_status = status
        show_source = (
            "打开 BORING 控制台 DIY"
            if self._custom_build
            else "打开 BORING 控制台"
        )
        quit_source = (
            "退出 BORING DIY 主机自动化助手"
            if self._custom_build
            else "退出 BORING 主机自动化助手"
        )
        show = QAction(translate_ui_text(show_source), menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        menu.addSeparator()
        quit_action = QAction(
            translate_ui_text(quit_source), menu
        )
        quit_action.triggered.connect(self.quit_application)
        self._tray_show_console = show
        self._tray_quit_helper = quit_action
        self._tray_display_menu = menu.addMenu(translate_ui_text("菜单栏显示"))
        self._menu_bar_display_actions = QActionGroup(menu)
        self._menu_bar_display_actions.setExclusive(True)
        for display, label in MENU_BAR_DISPLAYS.items():
            action = self._tray_display_menu.addAction(translate_ui_text(label))
            action.setCheckable(True)
            action.setData(display)
            action.triggered.connect(lambda _checked=False, value=display: self.set_menu_bar_display(value))
            self._menu_bar_display_actions.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray_menu = menu
        try:
            if self._platform != "darwin":
                raise RuntimeError("使用系统托盘")
            self._native_status_item = _MacTextStatusItem(self._show_tray_menu)
            if self._menu_bar_display == "both":
                self._native_claude_status_item = _MacTextStatusItem(self._show_tray_menu)
        except RuntimeError:
            if self._native_status_item is not None:
                self._native_status_item.hide()
                self._native_status_item = None
            if self._native_claude_status_item is not None:
                self._native_claude_status_item.hide()
                self._native_claude_status_item = None
            tray = QSystemTrayIcon(self)
            from PySide6.QtWidgets import QStyle
            tray.setIcon(self._application.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
            tray.setToolTip(
                translate_ui_text(
                    "BORING 控制台 DIY" if self._custom_build else "BORING 控制台"
                )
            )
            tray.setContextMenu(menu)
            tray.activated.connect(
                lambda reason: self.show_window()
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
            tray.show()
            self._tray = tray
        self._update_tray_usage()
        self.set_helper_status(
            online=self._helper_online,
            state_label=self._helper_state_label,
            message=self._helper_status_message,
        )

    def _update_tray_usage(self, *, rebuild_buckets: bool = False) -> None:
        if self._native_status_item is None and self._tray is None:
            return
        codex_snapshot = self._codex_usage_snapshot
        claude_snapshot = self._claude_usage_snapshot
        codex_label = _menu_bar_usage_label(codex_snapshot)
        claude_label = _provider_menu_bar_usage_label(
            claude_snapshot,
            provider_name="Claude",
            limit_id="claude",
        )
        if self._native_status_item is not None:
            title = ("BORING" if self._menu_bar_display == "compact" else
                     claude_label if self._menu_bar_display == "claude" else codex_label)
            if self._custom_build:
                title += " · DIY"
            self._native_status_item.set_title(title)
            if self._menu_bar_display == "both":
                if self._native_claude_status_item is None:
                    self._native_claude_status_item = _MacTextStatusItem(self._show_tray_menu)
                self._native_claude_status_item.set_title(claude_label)
            elif self._native_claude_status_item is not None:
                self._native_claude_status_item.hide()
                self._native_claude_status_item = None
        elif self._tray is not None:
            self._tray.setIcon(_menu_bar_usage_icon("AI 额度"))
        if self._tray_usage_panel is not None:
            self._tray_usage_panel.update_snapshots(
                codex_snapshot,
                claude_snapshot,
                rebuild_buckets=rebuild_buckets,
            )
        if self._tray_refresh_usage is not None:
            self._tray_refresh_usage.setText(translate_ui_text("更新额度信息"))
            self._tray_refresh_usage.setEnabled(
                (
                    self._codex_usage_monitor is not None
                    and codex_snapshot.status is not CodexUsageStatus.LOADING
                ) or (
                    self._claude_usage_monitor is not None
                    and claude_snapshot.status is not CodexUsageStatus.LOADING
                )
            )
        if self._tray_show_console is not None:
            self._tray_show_console.setText(
                translate_ui_text(
                    "打开 BORING 控制台 DIY"
                    if self._custom_build
                    else "打开 BORING 控制台"
                )
            )
        if self._tray_quit_helper is not None:
            self._tray_quit_helper.setText(
                translate_ui_text(
                    "退出 BORING DIY 主机自动化助手"
                    if self._custom_build
                    else "退出 BORING 主机自动化助手"
                )
            )
        self._tray_display_menu.setTitle(translate_ui_text("菜单栏显示"))
        self._tray_display_menu.menuAction().setVisible(self._native_status_item is not None)
        for action in self._menu_bar_display_actions.actions():
            action.setText(translate_ui_text(MENU_BAR_DISPLAYS[action.data()]))
            action.setChecked(action.data() == self._menu_bar_display)
        self._update_tray_tooltip()

    def _retranslate_tray(self) -> None:
        self._update_tray_usage(rebuild_buckets=True)
        self.set_helper_status(
            online=self._helper_online,
            state_label=self._helper_state_label,
            message=self._helper_status_message,
        )

    def _update_tray_tooltip(self) -> None:
        if self._native_status_item is None and self._tray is None:
            return
        helper = (
            f"{translate_ui_text('BORING DIY 主机自动化助手' if self._custom_build else 'BORING 主机自动化助手')} "
            f"{translate_ui_text(self._helper_state_label)}"
        )
        if self._native_status_item is not None:
            label = (_provider_menu_bar_usage_label(self._claude_usage_snapshot, provider_name="Claude", limit_id="claude")
                     if self._menu_bar_display == "claude" else
                     "BORING" if self._menu_bar_display == "compact" else
                     _menu_bar_usage_label(self._codex_usage_snapshot))
            if self._custom_build:
                label += " · DIY"
            self._native_status_item.set_tooltip(
                f"{label} · {helper}"
            )
            if self._native_claude_status_item is not None:
                claude_label = _provider_menu_bar_usage_label(
                    self._claude_usage_snapshot,
                    provider_name="Claude",
                    limit_id="claude",
                )
                self._native_claude_status_item.set_tooltip(
                    f"{claude_label} · {helper}"
                )
        elif self._tray is not None:
            claude_label = _provider_menu_bar_usage_label(
                self._claude_usage_snapshot,
                provider_name="Claude",
                limit_id="claude",
            )
            self._tray.setToolTip(
                f"{_menu_bar_usage_label(self._codex_usage_snapshot)} · "
                f"{claude_label} · "
                f"{helper}"
            )

    def _show_tray_menu(self) -> None:
        if self._tray_menu is not None:
            self._tray_menu.popup(QCursor.pos())
            QTimer.singleShot(
                0,
                lambda: install_macos_vibrancy(
                    self._tray_menu,
                    corner_radius=14.0,
                ),
            )

    def _read_bool(self, key: str, default: bool) -> bool:
        value = self._settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}


def background_program_arguments() -> tuple[str, ...]:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return sys.executable, "--background"
    return sys.executable, "-m", "controller_config.app", "--background"


def _menu_bar_usage_label(snapshot: CodexUsageSnapshot) -> str:
    return _provider_menu_bar_usage_label(
        snapshot,
        provider_name="Codex",
        limit_id="codex",
    )


def _provider_menu_bar_usage_label(
    snapshot: CodexUsageSnapshot,
    *,
    provider_name: str,
    limit_id: str,
) -> str:
    bucket = next(
        (item for item in snapshot.buckets if item.limit_id == limit_id),
        None,
    )
    if bucket is None:
        if snapshot.status is CodexUsageStatus.LOADING:
            return f"{provider_name} …"
        if snapshot.status is CodexUsageStatus.ERROR:
            return f"{provider_name} !"
        return f"{provider_name} —"

    windows = [window for window in (bucket.primary, bucket.secondary) if window]
    values = [
        f"{_menu_bar_window_short(window)} {_remaining_percent(window)}%"
        for window in windows
    ]
    return (
        f"{provider_name}  {'  ·  '.join(values)}"
        if values
        else f"{provider_name} —"
    )


def _menu_bar_usage_source_line(
    snapshot: CodexUsageSnapshot,
    provider_title: str,
) -> str:
    source_label = {
        "app_server": translate_ui_text("实时数据"),
        "claude_oauth": translate_ui_text("实时数据"),
        "session_log": translate_ui_text("本地日志 · 可能滞后"),
    }.get(snapshot.source, translate_ui_text("等待读取"))
    if snapshot.status is CodexUsageStatus.LOADING:
        source_label = translate_ui_text("正在更新")
    elif snapshot.status is CodexUsageStatus.ERROR:
        source_label = translate_ui_text("读取失败")
    elif snapshot.status is CodexUsageStatus.UNAVAILABLE:
        source_label = translate_ui_text("暂无数据")
    updated = ""
    if snapshot.updated_at is not None:
        updated_at = datetime.fromtimestamp(snapshot.updated_at).astimezone()
        updated = (
            f" · {translate_ui_text('更新于')} "
            f"{updated_at.strftime('%m-%d %H:%M')}"
        )
    return f"{translate_ui_text(provider_title)} · {source_label}{updated}"


def _menu_bar_window_short(window: CodexQuotaWindow) -> str:
    if window.window_minutes == 300:
        return "5H"
    if window.window_minutes == 10_080:
        return "7D"
    if window.window_minutes is not None and window.window_minutes % 1_440 == 0:
        return f"{window.window_minutes // 1_440}D"
    if window.window_minutes is not None and window.window_minutes % 60 == 0:
        return f"{window.window_minutes // 60}H"
    return "LIMIT"


def _menu_bar_window_line(window: CodexQuotaWindow) -> str:
    label = {
        300: translate_ui_text("5 小时窗口"),
        10_080: translate_ui_text("周额度"),
    }.get(window.window_minutes, translate_ui_text("额度窗口"))
    reset = ""
    if window.resets_at is not None:
        reset_at = datetime.fromtimestamp(window.resets_at).astimezone()
        reset = (
            f" · {translate_ui_text('预计恢复')} "
            f"{reset_at.strftime('%m-%d %H:%M')}"
        )
    return (
        f"{label} · {translate_ui_text('剩余')} "
        f"{_remaining_percent(window)}%{reset}"
    )


def _remaining_percent(window: CodexQuotaWindow) -> str:
    value = round(window.remaining_percent, 1)
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _menu_bar_usage_icon(label: str) -> QIcon:
    font = QFont("Helvetica Neue")
    font.setPixelSize(13)
    font.setWeight(QFont.Weight.DemiBold)
    metrics = QFontMetrics(font)
    logical_width = max(38, metrics.horizontalAdvance(label) + 12)
    logical_height = 22
    pixmap = QPixmap(logical_width, logical_height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setFont(font)
    painter.setPen(QColor("#000000"))
    painter.drawText(
        QRectF(0, 0, logical_width, logical_height),
        Qt.AlignmentFlag.AlignCenter,
        label,
    )
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)
    return icon
