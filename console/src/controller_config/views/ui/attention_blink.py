"""A status light turning "待处理" blinks twice, then stays on (-15 第 7 处, 乙).

Console only: the firmware's pending light (FF6D00) stays on without blinking;
the rhythm is borrowed from the device's two-buzz haptic (250 ms on, 300 ms
off, 250 ms on; tokens.ATTENTION_BLINK_*). Only a change into "待处理" blinks:
lights already pending when the page first shows them just stay on.

The host gives the colours it would show (`apply`) and a setter; during the
blink this passes the same colours with the blinking keys switched off
(None) for the dark beat.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QElapsedTimer, QObject, QTimer

from controller_config.style import tokens
from controller_config.views.ui import motion


def blink_lit(elapsed_ms: float) -> bool | None:
    """Lit at `elapsed_ms` into the blink; None once it is over (steady from then on)."""
    on, off = tokens.ATTENTION_BLINK_ON_MS, tokens.ATTENTION_BLINK_OFF_MS
    if elapsed_ms < on:
        return True
    if elapsed_ms < on + off:
        return False
    if elapsed_ms < on + off + on:
        return True
    return None


class AttentionBlink(QObject):
    def __init__(self, parent: QObject | None, attention: str) -> None:
        super().__init__(parent)
        self._attention = attention
        self._seen = False
        self._given: dict[str, str | None] = {}
        self._started: dict[str, float] = {}
        self._target: Callable[[dict[str, str | None]], None] | None = None
        self._clock = QElapsedTimer()
        self._clock.start()
        self._frozen_ms: float | None = None   # frame captures and tests
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._push)

    def apply(self, colours: Mapping[str, str | None],
              target: Callable[[dict[str, str | None]], None]) -> None:
        now = self.now()
        if self._seen and not motion.reduces_motion():
            for key, colour in colours.items():
                if colour == self._attention and self._given.get(key) != self._attention:
                    self._started[key] = now
        for key in list(self._started):
            if colours.get(key) != self._attention:
                del self._started[key]   # left 待处理 mid-blink: show what it is now
        self._seen = True
        self._given = dict(colours)
        self._target = target
        self._push()

    def blinking(self) -> set[str]:
        return set(self._started)

    def now(self) -> float:
        return self._frozen_ms if self._frozen_ms is not None else float(self._clock.elapsed())

    def freeze_at(self, ms: float | None) -> None:
        self._frozen_ms = ms
        self._push()

    def colours_now(self) -> dict[str, str | None]:
        now = self.now()
        shown = dict(self._given)
        for key, start in list(self._started.items()):
            lit = blink_lit(now - start)
            if lit is None:
                del self._started[key]
            elif not lit:
                shown[key] = None
        return shown

    def _push(self) -> None:
        if self._target is None:
            return
        try:
            self._target(self.colours_now())
        except RuntimeError:   # the stage went away with its page
            self._target = None
            return
        if self._started and self._frozen_ms is None:
            self._timer.start(16)
