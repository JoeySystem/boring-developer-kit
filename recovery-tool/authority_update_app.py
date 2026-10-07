#!/usr/bin/env python3
from __future__ import annotations

import argparse
import signal
import threading
import webbrowser

from provisioner.authority_update_server import create_authority_update_server


def _stop_on_signal(_signum, _frame) -> None:
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BORING MIST 恢复工具")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    server, state = create_authority_update_server("127.0.0.1", args.port)
    address, port = server.server_address[:2]
    url = f"http://{address}:{port}/"
    print(f"BORING MIST 恢复工具已启动：{url}", flush=True)
    if not args.no_browser:
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


if __name__ == "__main__":
    raise SystemExit(main())
