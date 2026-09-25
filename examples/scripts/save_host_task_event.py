"""Import this script in BORING; each explicit run saves one event in a temp folder."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile


def main() -> None:
    event = json.load(sys.stdin)
    if event.get("kind") != "host_action.triggered":
        raise ValueError("Bind this example to a computer task and press its device key")
    payload = event.get("payload", {})
    if set(payload) != {"action_id", "task_token"} or type(payload["action_id"]) is not int:
        raise ValueError("Expected action_id and task_token without prompt text")
    directory = Path(tempfile.mkdtemp(prefix="boring-script-example-"))
    output = directory / "task-event.json"
    output.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved: {output}", flush=True)


if __name__ == "__main__":
    main()
