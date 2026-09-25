"""Minimal BORING local automation example.

Select this file in BORING Console's Automation page. The console sends one
UTF-8 JSON event to standard input for every manual test or enabled physical
trigger. This example prints a compact summary that appears in the run log.
"""

from __future__ import annotations

import json
import sys


event = json.load(sys.stdin)
payload = event.get("payload", {})
print(
    "BORING event",
    event.get("kind", "unknown"),
    "prompt_id=",
    payload.get("prompt_id", "unknown"),
    "name=",
    payload.get("prompt_name", ""),
)
