"""Community stand-in for the Doubao link.

The official Console reads DoubaoWork's local data and drives its window
(macOS accessibility, Windows UI Automation) to put Doubao conversations on
the device's status lights. That integration is not part of the public kit.
This adapter keeps the desktop Agent hub's interface: it never reports a
running app or a conversation, so the lights stay with Codex and Claude, and
the settings page shows Doubao as not running.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, Signal

from controller_config.desktop_agent_hub import Conversation, OpenResult

APP_ID = "doubao"


class DoubaoAdapter(QObject):
    changed = Signal()
    account_changed = Signal(str)

    app_id = APP_ID
    running = False
    account = ""

    @classmethod
    def for_platform(cls, platform: str | None = None, parent=None) -> "DoubaoAdapter":
        return cls(parent=parent)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def poll(self) -> None:
        pass

    def check_viewing(self) -> None:
        pass

    def conversations(self) -> list[Conversation]:
        return []

    def open(self, conversation_id: str, finished: Callable[[OpenResult], None]) -> None:
        finished(OpenResult(error="not_included"))

    def problems(self) -> list[str]:
        return []

    def stats(self) -> dict:
        return {"format": "community", "running": False, "included": False}
