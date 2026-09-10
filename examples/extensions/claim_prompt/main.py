from __future__ import annotations

import json

from boring_console_sdk import BoringConsoleClient


def main() -> None:
    with BoringConsoleClient.from_environment() as console:
        print(json.dumps({"kind": "extension.ready"}), flush=True)
        while console.is_connected:
            invocation = console.next_action()
            if invocation is None:
                continue
            invocation_id = str(invocation["invocation_id"])
            console.respond_action(invocation_id, "accepted", "action accepted")
            print(json.dumps(invocation, ensure_ascii=False), flush=True)
            console.respond_action(invocation_id, "completed", "action completed")


if __name__ == "__main__":
    main()
