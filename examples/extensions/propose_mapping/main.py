from __future__ import annotations

import json
import uuid

from boring_console_sdk import BoringConsoleClient


def main() -> None:
    with BoringConsoleClient.from_environment() as console:
        context = console.get_context()
        serial = context.get("device_serial")
        summary = context.get("config_summary", {})
        profile = context.get("active_profile") or {}
        if not serial:
            raise RuntimeError("connect one BORING device before running this example")
        proposal = {
            "schema_version": 1,
            "proposal_id": uuid.uuid4().hex,
            "extension_id": console.extension_id,
            "device_serial": serial,
            "base_generation": summary["generation"],
            "base_digest": summary["digest"],
            "profile_id": profile["id"],
            "control_id": "key.12",
            "action": {"type": "key", "usage": 40, "modifiers": []},
        }
        result = console.propose_mapping(proposal)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        # The Console only allows review while the proposing extension is ready.
        # Remain connected until the user stops this example or the host closes.
        while console.is_connected:
            console.next_event(timeout_ms=250)


if __name__ == "__main__":
    main()
