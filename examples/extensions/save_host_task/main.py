from __future__ import annotations
import json
from pathlib import Path
import tempfile
from boring_console_sdk import BoringConsoleClient


def main() -> None:
    with BoringConsoleClient.from_environment() as console:
        print("Ready: save_event", flush=True)
        while console.is_connected:
            invocation = console.next_action()
            if invocation is None:
                continue
            invocation_id = invocation["invocation_id"]
            event = invocation["event"]
            if invocation["action_id"] != "save_event" or event["kind"] != "host_action.triggered":
                console.respond_action(invocation_id, "rejected", "此示例需要独立电脑任务事件")
                continue
            console.respond_action(invocation_id, "accepted")
            cancel = console.next_cancellation(timeout_ms=0)
            if cancel and cancel["invocation_id"] == invocation_id:
                console.respond_action(invocation_id, "failed", "已停止；未写文件")
                continue
            try:
                output = Path(tempfile.mkdtemp(prefix="boring-extension-example-")) / "task-event.json"
                output.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                # Longer tasks should check cancellation between each bounded step.
                cancel = console.next_cancellation(timeout_ms=0)
                status = "failed" if cancel and cancel["invocation_id"] == invocation_id else "completed"
                message = f"已停止；此前写入的文件保留：{output}" if status == "failed" else f"已保存：{output}"
                console.respond_action(invocation_id, status, message)
                print(f"Saved: {output}", flush=True)
            except OSError as exc:
                console.respond_action(invocation_id, "failed", str(exc))


if __name__ == "__main__":
    main()
