from __future__ import annotations

import argparse
import sys

# Installed Hooks must exit quickly without Qt, a window or a second serial
# owner. Keep this dispatch before every GUI/platform import.
if "--claude-status-hook" in sys.argv:
    from pathlib import Path
    from controller_config.claude_hook import run_hook
    try:
        run_hook(Path(sys.argv[sys.argv.index("--claude-status-hook") + 1]))
    except Exception:
        pass
    raise SystemExit(0)

# A material check is a short-lived local decoder, not another console instance.
# Dispatch before auth, single-instance coordination and all device integrations.
# It inherits Qt's image plugin paths from the parent; do not copy the macOS
# platform plugins again for every selected image.
if "--check-icon-material" in sys.argv:
    from controller_config.icon_material_check import run_worker
    raise SystemExit(run_worker(sys.argv[sys.argv.index("--check-icon-material") + 1]))

from controller_config.qt_bootstrap import prepare_qt_platform_plugins

prepare_qt_platform_plugins()

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QStyle  # noqa: E402

from controller_config.protocol.contract import Contract, ContractError
from controller_config.protocol.device_auth import load_default_authenticator
from controller_config.claude_usage import ClaudeUsageMonitor
from controller_config.codex_usage import CodexUsageMonitor
from controller_config.background_helper import (
    ApplicationInstanceCoordinator,
    MacLaunchAgent,
    PromptBackgroundController,
    background_program_arguments,
)
from controller_config.firmware_release import HttpFirmwareReleaseSource, default_firmware_source
from controller_config.extensions.manager import ExtensionManagerError
from controller_config.extensions.bindings import ExtensionBindingError
from controller_config.extensions.platform import ExtensionPlatformController
from controller_config.i18n import LanguageManager
from controller_config.prompt_helper import MacClipboardPaster, PromptHelperRuntime
from controller_config.transport.demo import DemoGateway
from controller_config.transport.device_gateway import DeviceGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


DEMO_STATES = (
    "ready",
    "no-device",
    "incompatible",
    "authenticity-failed",
    "read-failed",
    "disconnected",
    "read-only",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BORING desktop console")
    parser.add_argument("--demo", choices=DEMO_STATES, help="使用只读共享 fixture 演示指定界面状态")
    parser.add_argument(
        "--firmware-manifest-url",
        help=(
            "配置 HTTPS 固件发布 manifest 地址；可使用 {product_id} 和 "
            "{hardware_id} 占位符"
        ),
    )
    parser.add_argument(
        "--firmware-channel", choices=("stable", "sample"),
        help="固件渠道；sample 仅用于样品联调，保留工程包验证状态",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="以 macOS 菜单栏主机自动化助手模式启动，不主动显示主窗口",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
    # Community settings and drafts are separate from the official application.
    app.setApplicationName("BORING Console Community")
    app.setApplicationDisplayName("BORING Console Community")
    app.setOrganizationName("BORING")
    instance = ApplicationInstanceCoordinator()
    try:
        if not instance.acquire(activate_existing=not args.background):
            return 0
    except RuntimeError as exc:
        QMessageBox.critical(None, "无法启动 BORING 控制台", str(exc))
        return 2
    ui_settings = QSettings()
    language_manager = LanguageManager(
        app,
        settings=ui_settings,
        use_system_default=True,
    )
    try:
        contract = Contract.load()
        authenticator = load_default_authenticator()
    except (ContractError, ValueError) as exc:
        QMessageBox.critical(None, "设备信任配置不可用", str(exc))
        return 2
    gateway = (
        DemoGateway(contract, args.demo)
        if args.demo
        else DeviceGateway(contract, authenticator)
    )
    firmware_source = default_firmware_source()
    manifest_url = args.firmware_manifest_url or firmware_source["manifest_url"]
    release_source = (
        HttpFirmwareReleaseSource(manifest_url, channel=args.firmware_channel or firmware_source["channel"])
        if manifest_url and not args.demo
        else None
    )
    prompt_helper = (
        PromptHelperRuntime(MacClipboardPaster(app.clipboard()))
        if sys.platform == "darwin"
        else None
    )
    view_model = MainViewModel(
        gateway,
        contract,
        firmware_release_source=release_source,
        prompt_helper_runtime=prompt_helper,
    )
    if sys.platform == "darwin" and not args.demo:
        from controller_config.codex_agent_focus import MacChatGPTActivator
        focus_activator = MacChatGPTActivator(app)
        view_model.codex_agent_focus.requested.connect(focus_activator.activate)
        focus_activator.failed.connect(view_model.report_chatgpt_focus_failure)
        app.aboutToQuit.connect(focus_activator.shutdown)
    try:
        extension_platform = ExtensionPlatformController(
            view_model,
            contract,
            prompt_helper=prompt_helper,
            parent=app,
        )
        view_model.attach_extension_platform(extension_platform)
    except (ExtensionManagerError, ExtensionBindingError, RuntimeError) as exc:
        view_model.shutdown()
        instance.close()
        QMessageBox.critical(None, "扩展平台无法启动", str(exc))
        return 2
    launch_agent = (
        MacLaunchAgent(background_program_arguments())
        if sys.platform == "darwin"
        else None
    )
    codex_usage = CodexUsageMonitor() if sys.platform == "darwin" else None
    claude_usage = ClaudeUsageMonitor() if sys.platform == "darwin" else None
    background = PromptBackgroundController(
        app,
        shutdown=view_model.shutdown,
        launch_agent=launch_agent,
        codex_usage_monitor=codex_usage,
        claude_usage_monitor=claude_usage,
        language_manager=language_manager,
    )
    window = MainWindow(
        view_model,
        language_manager=language_manager,
        background_controller=background,
        onboarding_settings=ui_settings,
    )
    from controller_config.desktop_update import create_desktop_updater
    from controller_config.views.desktop_update import DesktopUpdateUi
    if not args.demo:
        update_ui = DesktopUpdateUi(window)
        window._desktop_update_ui = update_ui
        updater = create_desktop_updater(update_ui.prepare_restart, parent=app)
        update_ui.attach(updater)
        updater.quit_requested.connect(app.quit)
        app.aboutToQuit.connect(updater.shutdown)
        QTimer.singleShot(1500, updater.start)
    background.attach_window(window)
    instance.activate_requested.connect(background.show_window)
    app.aboutToQuit.connect(view_model.shutdown)
    if codex_usage is not None:
        app.aboutToQuit.connect(codex_usage.stop)
    if claude_usage is not None:
        app.aboutToQuit.connect(claude_usage.stop)
    app.aboutToQuit.connect(instance.close)
    if not args.background or not background.background_enabled:
        window.show()
    view_model.start()
    if not args.demo:
        try:
            view_model.claude_status.start()
        except (OSError, ValueError, RuntimeError) as exc:
            view_model.claude_status.message = f"Claude Code 状态联动未启动：{exc}"
    if codex_usage is not None:
        codex_usage.start()
    if claude_usage is not None:
        claude_usage.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
