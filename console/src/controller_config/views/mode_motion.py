"""Moving parts of the Mode page (Mode页 定稿 v1, 06; 单 design ticket 第 4 批).

Connecting: a sheet of dots wells up from the bottom of the page in 0.82 s
(250 high, small dots at its top edge growing towards the bottom until they
almost touch, then shrinking and melting away over the next 120); colour, the
dot floor and the device's light come back where it has melted. The word
behind the device flickers through other letters from 0.52 s and settles one
letter at a time until 1.86 s; the battery fades in from 0.70 to 1.00 s.

Disconnecting (the designer 11-20: "先倒着扫一遍，然后再暗下去"): the same sweep
played backwards, top to bottom, then the page dims to grey.

Everything here is plain arithmetic in the 960×532 page's units plus one overlay
widget over the device; the sheet crosses the whole window, top bar included, all
but the window's three buttons (design decision, drawn by views/mode_layers.py). Times come from time.monotonic so a
page rebuilt halfway (the main window rebuilds it when the connection changes)
carries on where the old one was.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from controller_config.digital_font import load_font_pack, layout_text
from controller_config.style import tokens

BASE_W = tokens.MODE_BASE_WIDTH
BASE_H = tokens.MODE_BASE_HEIGHT

LINK_IN_SECONDS = 2.0          # 2.00: everything has stopped
SWEEP_SECONDS = 0.82           # the sheet goes from the bottom edge off the top
BAND = 250.0                   # the growing part of the sheet
MELT = 120.0                   # where the big dots shrink back to floor dots
ARC = 14.0                     # the front edge is a shallow arc, lower in the middle
DOT_MIN, DOT_MAX = 1.2, 11.5   # the floor pitch is 13: the biggest dots almost touch
SCRAMBLE = (0.52, 1.86)
SCRAMBLE_TICK = 0.065          # each letter changes this often
BATTERY_FADE = (0.70, 1.00)
OUT_SWEEP_SECONDS = 0.62       # backwards, a little quicker than coming in
OUT_DIM_SECONDS = 0.30         # then down to grey
LINK_OUT_SECONDS = OUT_SWEEP_SECONDS + OUT_DIM_SECONDS
FRAME_MS = 16


def _clamp(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def phase(t: float, start: float, end: float) -> float:
    return _clamp((t - start) / (end - start))


PAGE = QRectF(0, 0, BASE_W, BASE_H)


def lead(progress: float, span: QRectF = PAGE) -> float:
    """Top edge of the sheet at the middle, for progress 0..1 of the sweep.

    `span` is what the sheet crosses, in page units: the page by default, the whole
    window on the Mode page (design decision: "整个窗口连顶条"). An even speed: half way at
    0.26 s, the top at about 0.5 s, gone at 0.82 s (06).
    """
    return span.bottom() - (span.height() + BAND + MELT + ARC) * _clamp(progress)


def edge_at(x: float, front: float, span: QRectF = PAGE) -> float:
    """The front edge bends: the middle a little lower than the sides."""
    across = (x - span.center().x()) / (span.width() / 2)
    return front + ARC * (1 - across * across)


def sweep_dots(progress: float, span: QRectF = PAGE) -> list[tuple[float, float, float, float]]:
    """(x, y, radius, brightness 0..1) for every dot of the sheet at this progress.

    The sheet uses the floor's grid (pitch 13), so the small dots left behind
    are the floor dots; past the page it carries the same grid on to `span`'s edges.
    """
    if progress <= 0 or progress >= 1:
        return []
    front = lead(progress, span)
    pitch = tokens.MODE_FLOOR_PITCH
    first_x = BASE_W / 2 % pitch
    first_x += math.ceil((span.left() - first_x) / pitch) * pitch
    dots = []
    first_row = max(math.ceil(span.top() / pitch - 0.5), math.floor(front / pitch))
    last_row = min(math.ceil(span.bottom() / pitch), math.ceil((front + ARC + BAND + MELT) / pitch))
    for row in range(first_row, last_row + 1):
        y = row * pitch + pitch / 2
        x = first_x
        while x < span.right():
            depth = y - edge_at(x, front, span)
            if 0 <= depth < BAND:
                share = (depth / BAND) ** 1.35
                dots.append((x, y, (DOT_MIN + (DOT_MAX - DOT_MIN) * share) / 2, 0.35 + 0.65 * share))
            elif BAND <= depth < BAND + MELT:
                share = 1 - (depth - BAND) / MELT
                radius = tokens.MODE_FLOOR_DOT_SIZE / 2 + (DOT_MAX - tokens.MODE_FLOOR_DOT_SIZE) / 2 * share
                dots.append((x, y, radius, share))
            x += pitch
    return dots


def revealed_from(progress: float, span: QRectF = PAGE) -> float:
    """Page y below which the sheet has melted: colour and floor are back there."""
    if progress <= 0:
        return span.bottom() + BAND + MELT + ARC
    if progress >= 1:
        return span.top() - 1.0
    return lead(progress, span) + ARC + BAND


@dataclass(frozen=True)
class Link:
    """One connect ("in") or disconnect ("out") animation, by its start time."""

    kind: str                 # "in" / "out"
    started: float            # time.monotonic()

    @property
    def duration(self) -> float:
        return LINK_IN_SECONDS if self.kind == "in" else LINK_OUT_SECONDS

    def elapsed(self, now: float | None = None) -> float:
        return max(0.0, (time.monotonic() if now is None else now) - self.started)

    def done(self, now: float | None = None) -> bool:
        return self.elapsed(now) >= self.duration

    def sweep_progress(self, now: float | None = None) -> float:
        """0..1 of the sweep; for "out" the same sweep run backwards."""
        t = self.elapsed(now)
        if self.kind == "in":
            return _clamp(t / SWEEP_SECONDS)
        return 1 - _clamp(t / OUT_SWEEP_SECONDS)

    def dim(self, now: float | None = None) -> float:
        """0 → 1 while the page goes down to grey after the backwards sweep."""
        if self.kind != "out":
            return 0.0
        return phase(self.elapsed(now), OUT_SWEEP_SECONDS, LINK_OUT_SECONDS)


# -- the word: scramble, then settle letter by letter ---------------------------------------

def letter_origins(word: str, scale: float) -> list[float]:
    """x of each letter's left edge in the word laid out at this scale."""
    gap = load_font_pack().profile.character_gap * scale
    origins = []
    for index in range(len(word)):
        prefix = word[:index]
        origins.append(layout_text(prefix, scale=scale).width + gap if prefix else 0.0)
    return origins


def _alphabet() -> str:
    return "".join(character for character in load_font_pack().character_order
                   if character.isalpha() and character.isupper())


def scramble_frame(word: str, t: float, seed: int = 7) -> list[tuple[str, float, bool]]:
    """(letter shown, brightness, just settled) for each position at time t.

    Before 0.52 s nothing; then every position flickers through other letters,
    changing every 0.065 s, some dimmer, some with dots missing (drawn dimmer
    here); positions settle in a shuffled order until 1.86 s, each with a flash.
    """
    start, end = SCRAMBLE
    if t < start:
        return [(" ", 0.0, False) for _ in word]
    order = list(range(len(word)))
    random.Random(seed).shuffle(order)
    settle_at = {position: start + 0.45 + (end - start - 0.45) * (rank + 1) / len(word)
                 for rank, position in enumerate(order)}
    letters = _alphabet() or "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    tick = int((t - start) / SCRAMBLE_TICK)
    frame = []
    for position, final in enumerate(word):
        if t >= settle_at[position]:
            flash = t - settle_at[position] < 0.12
            frame.append((final, 1.0, flash))
            continue
        pick = random.Random(seed * 1009 + position * 131 + tick)
        frame.append((pick.choice(letters), 0.35 + 0.5 * pick.random(), False))
    return frame


def column_wipe(old_dots, new_dots, t: float, width: float):
    """Zoom word change (12): the old word goes out column by column left to right,
    then the new one comes in the same way. t is 0..1 of the change."""
    if t <= 0.5:
        cut = width * (t / 0.5)
        return [dot for dot in old_dots if dot[0] >= cut]
    cut = width * ((t - 0.5) / 0.5)
    return [dot for dot in new_dots if dot[0] < cut]


# -- drawing -------------------------------------------------------------------------------

_STAMPS: dict[tuple[int, int, int, float], QPixmap] = {}


def _stamp(size: int, light: int, pixels: int, ratio: float) -> QPixmap:
    """One dot of the sheet, drawn once: `size` quarter units across, brightness light/8,
    `pixels` device pixels per 100 page units."""
    key = (size, light, pixels, ratio)
    stamp = _STAMPS.get(key)
    if stamp is None:
        diameter = size / 4 * pixels / 100            # device pixels
        side = math.ceil(diameter) + 2
        stamp = QPixmap(side, side)
        stamp.fill(Qt.GlobalColor.transparent)
        dim, bright = QColor(tokens.MODE_FLOOR_DOT), QColor(tokens.MODE_SWEEP_DOT)
        share = light / 8
        colour = QColor(round(dim.red() + (bright.red() - dim.red()) * share),
                        round(dim.green() + (bright.green() - dim.green()) * share),
                        round(dim.blue() + (bright.blue() - dim.blue()) * share))
        painter = QPainter(stamp)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(side / 2, side / 2), diameter / 2, diameter / 2)
        painter.end()
        stamp.setDevicePixelRatio(ratio)
        _STAMPS[key] = stamp
    return stamp


def paint_sweep(painter: QPainter, progress: float, span: QRectF = PAGE) -> int:
    """Draw the sheet in page units; returns how many dots it drew (for the frame-rate check).

    Each dot is a stamp drawn once per size and brightness and copied into place: some
    2400 anti-aliased dots a frame cover the whole window (design decision), too many to draw
    one by one in 16.7 ms.
    """
    dots = sweep_dots(progress, span)
    if not dots:
        return 0
    transform = painter.worldTransform()
    ratio = painter.device().devicePixelRatioF() if painter.device() is not None else 1.0
    unit = math.hypot(transform.m11(), transform.m12())    # widget pixels per page unit
    pixels = max(1, round(unit * ratio * 100))
    painter.save()
    painter.resetTransform()
    for x, y, radius, light in dots:
        stamp = _stamp(round(radius * 8), round(light * 8), pixels, ratio)
        at = transform.map(QPointF(x, y))
        half = stamp.width() / ratio / 2
        painter.drawPixmap(QPointF(at.x() - half, at.y() - half), stamp)
    painter.restore()
    return len(dots)


class LinkOverlay(QWidget):
    """Over the device and under the bar and card: the grey wash on the parts of the
    device the sheet has not reached yet. The sheet itself goes over the whole window
    (views/mode_layers.py ModeSweep); outside a window this overlay draws it too."""

    def __init__(self, stage) -> None:
        super().__init__(stage)
        self._stage = stage
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()
        self.frames = 0          # frames of the sheet drawn in this animation (frame-rate check)
        self.dots_drawn = 0

    def count(self, dots: int) -> None:
        self.frames += 1
        self.dots_drawn += dots

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        link = self._stage.link()
        if link is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        origin, scale = self._stage.origin(), self._stage.scale()
        progress = link.sweep_progress()
        span = self._stage.sweep_span()
        before = self._stage.before_picture()
        if link.kind == "out" and before is not None:
            # Going out, the device keeps its colours until the sheet has passed (design decision):
            # the picture from just before, below where the sheet has reached.
            top = origin.y() + revealed_from(progress, span) * scale
            if top < self.height():
                painter.save()
                painter.setClipRect(QRectF(0, top, self.width(), self.height() - top))
                painter.drawPixmap(self._stage.device.geometry().topLeft(), before)
                painter.restore()
        painter.translate(origin)
        painter.scale(scale, scale)
        device = self._stage.device_page_rect()
        wash = QColor(tokens.MODE_BACKGROUND)
        if link.kind == "in":
            # The device is lit already; until the sheet melts past a part, it stays grey.
            top = revealed_from(progress, span)
            if top > device.top():
                wash.setAlphaF(0.62)
                painter.fillRect(QRectF(device.left(), device.top(), device.width(),
                                        min(device.height(), top - device.top())), wash)
        # Going out, the device greys where the sheet has passed over it.
        if not self._stage.sweep_shown():
            self.count(paint_sweep(painter, progress, span))
        painter.end()


class LinkClock:
    """Repaints the stage every frame while a link animation runs."""

    def __init__(self, stage) -> None:
        self._stage = stage
        self._timer = QTimer(stage)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def running(self) -> bool:
        return self._timer.isActive()

    def _tick(self) -> None:
        self._stage.link_tick()


# -- the knob and the joystick, zoomed (11, 12) ----------------------------------------------
#
# A click on the knob or the joystick pushes the camera in: the device grows from 1× to
# 2.5× (the joystick 2.9×) with the part moving in a straight line to the middle left;
# fast at first, slower towards the end. Everything below runs on the 0.72 s timeline
# of zooming in; zooming out plays the same timeline backwards in 0.40 s, so the words
# on the part and the card go first, the camera pulls back, the side words come last.

ZOOM_IN_SECONDS = 0.72
ZOOM_OUT_SECONDS = 0.40
ZOOM = {"encoder": 2.5, "joystick": 2.9}
ZOOM_WORD = {"encoder": "KNOB", "joystick": "STICK"}
ZOOM_CENTRE = QPointF(392, 246)       # where the part ends up (page units)
FLOOR_ZOOM, WORD_ZOOM = 1.4, 1.05
RING_IN, RING_OUT = (0.0, 0.09), (0.40, 0.54)
SIDE_FADE = (0.0, 0.17)               # side words, the next screen, the quota
CAMERA = (0.04, 0.60)
DARKEN = (0.04, 0.48)                 # the other keys darken outwards; key words fade
RIPPLE = (0.06, 0.70)                 # one ring of bright floor dots from the part
WORD_WIPE = (0.09, 0.47)              # the word goes out and in column by column
PART_WORDS = (0.45, 0.72)             # words on the part one by one; arcs; the four cards
CARD_IN = (0.46, 0.71)                # the cream card slides in


def ease_camera(t: float) -> float:
    """Moves at once and slows down towards the end."""
    return 1 - (1 - _clamp(t)) ** 3


@dataclass(frozen=True)
class Zoom:
    """One zoom in ("in") or out ("out") of a part, by its start time."""

    kind: str
    started: float

    def timeline(self, now: float | None = None) -> float:
        """Where on the 0.72 s zoom-in timeline the picture is now."""
        elapsed = max(0.0, (time.monotonic() if now is None else now) - self.started)
        if self.kind == "in":
            return min(ZOOM_IN_SECONDS, elapsed)
        return ZOOM_IN_SECONDS * (1 - min(1.0, elapsed / ZOOM_OUT_SECONDS))

    def done(self, now: float | None = None) -> bool:
        elapsed = max(0.0, (time.monotonic() if now is None else now) - self.started)
        return elapsed >= (ZOOM_IN_SECONDS if self.kind == "in" else ZOOM_OUT_SECONDS)


def ripple_dots(origin: QPointF, t: float, *, bottom: float = BASE_H) -> list[tuple[float, float, float]]:
    """(x, y, light 0..1) of floor dots lit by the one ring that spreads from the part.

    On the floor's own dots (paint_floor counts its rows up from the page's bottom
    edge, so they sit 1 unit off a grid counted down from the top), and down to the
    floor's bottom (`bottom`, the window's, design ticket 第 4 件): the ring is
    not cut off at the page's edge while the floor goes on below it.
    """
    share = phase(t, *RIPPLE)
    if share <= 0 or share >= 1:
        return []
    radius, width = 40 + 720 * share, 34.0
    fade = 1 - share
    pitch = tokens.MODE_FLOOR_PITCH
    first_x = (BASE_W / 2) % pitch
    dots = []
    y = (BASE_H - pitch / 2) % pitch
    while y < bottom:
        dy = y - origin.y()
        outer = (radius + width) ** 2 - dy * dy
        if outer > 0:
            # Only the columns the ring crosses on this row: one span each side of the part.
            reach = math.sqrt(outer)
            inner = (radius - width) ** 2 - dy * dy if radius > width else -1.0
            near = math.sqrt(inner) if inner > 0 else 0.0
            for low, high in ((origin.x() - reach, origin.x() - near), (origin.x() + near, origin.x() + reach)):
                x = first_x + math.ceil((max(0.0, low) - first_x) / pitch) * pitch
                while x <= min(BASE_W, high):
                    gap = abs(math.hypot(x - origin.x(), dy) - radius)
                    if gap < width:
                        dots.append((x, y, (1 - gap / width) * fade))
                    x += pitch
        y += pitch
    return dots


def joystick_cards(centre: QPointF, share: float) -> dict[str, QRectF]:
    """The four small cards round the zoomed joystick, popping out from the middle."""
    width, height = 111.0, 58.0
    spots = {"up": (0, -128), "down": (0, 128), "left": (-160, 0), "right": (160, 0)}
    cards = {}
    for direction, (dx, dy) in spots.items():
        x = centre.x() + dx * share
        y = centre.y() + dy * share
        scale = 0.4 + 0.6 * share
        cards[direction] = QRectF(x - width * scale / 2, y - height * scale / 2, width * scale, height * scale)
    return cards
