"""The interface every Console page implements (规划第 11 节 11.2).

The main window owns the shell (top navigation, connection area, banners and
one host widget per route). It asks the page for the route being shown to
either update itself in place or build a fresh widget; everything inside the
page widget belongs to the page object, which keeps direct references to the
widgets it refreshes instead of looking them up by name.

Every page implements :class:`Page`. Pages with unsaved edits will add
``editing_state()`` / ``restore(state)`` and ``can_leave(for_exit)`` when the
first such page moves here (see the plan, 11.2).
"""
from __future__ import annotations

from typing import Protocol

from PySide6.QtWidgets import QWidget

from controller_config.models import ScreenModel


class Page(Protocol):
    route: str

    def update(self, model: ScreenModel) -> bool:
        """Bring the live page widget up to date without rebuilding it.

        Return False when the page has no live widget or what it shows changed
        shape; the main window then clears the route host and calls build().
        """

    def build(self, model: ScreenModel) -> QWidget:
        """Build a fresh page widget for the main window to install."""

