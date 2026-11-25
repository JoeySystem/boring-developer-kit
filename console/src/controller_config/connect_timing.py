"""How long each step of a device connection took (SH-005).

Steps are stamped with ``time.monotonic()``, which is process-wide, so the
connection controller (GUI thread) and the USB/BLE workers (their own
threads) can add marks to the same timeline:

    scan_start       a reconnect or manual scan that found the device began
    device_found     the scan reported the device and the console picked it
    connect          the worker started opening the channel (usb/bluetooth)
    channel_open     the channel is ready for WMP frames (GATT subscribed)
    HELLO ... GET_CONFIG   each handshake response, with its size; GET_CONFIG
                     says "skipped" when the cached configuration was used
    ui_ready         the console switched to the connected screen

"Copy diagnostics" carries the last timeline, so a slow connection report
shows where the time went without asking the user to measure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class TimingMark:
    step: str
    at: float
    detail: str = ""


def timing_report(marks: Iterable[TimingMark]) -> dict[str, object]:
    """Per-step milliseconds since the previous mark and since the first."""

    ordered = list(marks)
    if not ordered:
        return {}
    start = previous = ordered[0].at
    steps = []
    for mark in ordered:
        steps.append({
            "step": mark.step,
            "ms": round((mark.at - previous) * 1000),
            "at_ms": round((mark.at - start) * 1000),
            **({"detail": mark.detail} if mark.detail else {}),
        })
        previous = mark.at
    # Wall-clock start, to line the timeline up with the system Bluetooth log.
    started_at = datetime.fromtimestamp(time.time() - (time.monotonic() - start)).astimezone()
    return {
        "started_at": started_at.isoformat(timespec="milliseconds"),
        "total_ms": round((ordered[-1].at - start) * 1000),
        "steps": steps,
    }


def timing_summary(marks: Iterable[TimingMark]) -> str:
    """One line for the diagnostics event log."""

    report = timing_report(marks)
    if not report:
        return ""
    parts = [f"total={report['total_ms']}ms"]
    parts.extend(f"{step['step']}={step['ms']}ms" for step in report["steps"][1:])
    return " ".join(parts)
