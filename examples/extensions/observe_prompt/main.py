from __future__ import annotations

import json

from boring_console_sdk import BoringConsoleClient


def main() -> None:
    with BoringConsoleClient.from_environment() as console:
        context = console.get_context()
        print(
            json.dumps(
                {
                    "kind": "extension.ready",
                    "device_serial": context["device_serial"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        console.subscribe_events()
        for event in console.events():
            print(json.dumps(event, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
