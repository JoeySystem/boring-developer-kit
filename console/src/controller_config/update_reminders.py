"""Local 24-hour pauses for the two existing update flows."""
from __future__ import annotations

import json
import time
from PySide6.QtCore import QSettings


class UpdateReminders:
    def __init__(self, settings=None, *, clock=time.time):
        self.settings = settings if settings is not None else QSettings()
        self.clock = clock

    def _key(self, scope):
        return 'update_reminders/' + scope

    def snooze(self, scope: str, target: str) -> None:
        self.settings.setValue(self._key(scope), json.dumps({'target': target, 'until': self.clock() + 86400}))
        self.settings.sync()

    def is_snoozed(self, scope: str, target: str) -> bool:
        value = self.settings.value(self._key(scope))
        if value is None:
            return False
        try:
            saved = json.loads(str(value))
            if float(saved.get('until', 0)) <= self.clock():
                self.clear(scope)
                return False
            return saved.get('target') == target
        except (ValueError, TypeError, AttributeError):
            return False

    def clear(self, scope: str) -> None:
        self.settings.remove(self._key(scope))
        self.settings.sync()

    def clear_target(self, scope: str, target: str) -> None:
        try:
            saved = json.loads(str(self.settings.value(self._key(scope), '{}')))
            if saved.get('target') == target:
                self.clear(scope)
        except (ValueError, TypeError, AttributeError):
            pass
