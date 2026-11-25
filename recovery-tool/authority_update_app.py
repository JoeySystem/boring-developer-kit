#!/usr/bin/env python3
"""BORING MIST BLE recovery tool (internal).

Default: a native window (WebView2 on Windows, WKWebView on macOS) that owns
the process lifetime. Closing the window exits the tool; while firmware is
being written the close is refused. `--browser` keeps the old behaviour for
debugging, `--no-browser` runs headless for the packaged smoke test.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import webbrowser

from provisioner.authority_update_server import create_authority_update_server
from provisioner.errors import ProvisioningError
from provisioner.paths import resource_root
from provisioner.single_instance import (
    APP_TITLE,
    acquire_single_instance,
    notify_already_running,
)


def _stop_on_signal(_signum, _frame) -> None:
    raise KeyboardInterrupt


def check_gui() -> int:
    """Verify the native window backend is bundled (used by the smoke test)."""
    try:
        import webview  # noqa: F401

        if sys.platform == "win32":
            import webview.platforms.edgechromium  # noqa: F401
        elif sys.platform == "darwin":
            import webview.platforms.cocoa  # noqa: F401
    except Exception as exc:  # pragma: no cover - reported to the caller
        print(f"GUI backend unavailable: {exc}", flush=True)
        return 2
    return 0


def _serve_headless(server, state, open_browser: bool, url: str) -> int:
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    for signal_name in ("SIGHUP", "SIGTERM"):
        if shutdown_signal := getattr(signal, signal_name, None):
            signal.signal(shutdown_signal, _stop_on_signal)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if state.busy_job is not None:
            print("\n正在等待当前更新与回读安全结束…", flush=True)
    finally:
        state.shutdown()
        server.server_close()
    return 0


def _serve_window(webview, server, state, url: str) -> int:
    server_thread = threading.Thread(
        target=server.serve_forever, daemon=True, name="authority-updater-http"
    )
    server_thread.start()
    window = webview.create_window(
        APP_TITLE,
        url,
        width=820,
        height=920,
        min_size=(560, 640),
    )

    def show_in_page(message: str) -> None:
        script = (
            "(()=>{const b=document.getElementById('errorBanner');"
            f"if(b){{b.hidden=false;b.textContent={json.dumps(message, ensure_ascii=False)};}}}})()"
        )
        threading.Thread(
            target=lambda: window.evaluate_js(script), daemon=True
        ).start()

    def on_closing():
        try:
            state.require_shutdown_idle()
        except ProvisioningError as exc:
            show_in_page(f"{exc}。固件写入期间不能关闭窗口。")
            return False
        return True

    # The page's own "退出工具" button stops the server; close the window too.
    state.on_shutdown_requested = window.destroy
    window.events.closing += on_closing
    # The EXE already carries this icon and pywebview reads it from there;
    # passing the bundled file as well keeps the title bar and taskbar right
    # if that lookup ever fails.
    icon = resource_root() / "icons" / "recovery-icon.ico"
    try:
        webview.start(icon=str(icon) if icon.is_file() else None)
    finally:
        try:
            state.require_shutdown_idle()
        except ProvisioningError:
            # Only reachable if the window was torn down by the OS mid-OTA;
            # let the worker finish before the process exits.
            pass
        server.shutdown()
        state.shutdown()
        server.server_close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BORING MIST 固件更新与校准")
    parser.add_argument("--port", type=int, default=0)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--no-browser", action="store_true", help="只启动本地服务（自动化测试用）")
    mode.add_argument("--browser", action="store_true", help="用系统浏览器打开（调试用）")
    mode.add_argument("--check-gui", action="store_true", help="检查原生窗口组件后退出")
    args = parser.parse_args(argv)

    if args.check_gui:
        return check_gui()

    instance = acquire_single_instance()
    if instance is None:
        notify_already_running()
        return 1
    try:
        server, state = create_authority_update_server("127.0.0.1", args.port)
        address, port = server.server_address[:2]
        url = f"http://{address}:{port}/"
        print(f"BORING MIST 固件更新与校准已启动：{url}", flush=True)

        if args.no_browser or args.browser:
            return _serve_headless(server, state, args.browser, url)
        try:
            import webview
        except ImportError:
            print("未找到原生窗口组件，改用系统浏览器打开。", flush=True)
            return _serve_headless(server, state, True, url)
        return _serve_window(webview, server, state, url)
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
