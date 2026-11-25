"""The Mode page (首页), Mode页 定稿 v1 (the designer 2026-11-19, 单 design ticket).

One screen per set of key mappings, flipped left and right; the last screen is
Codex. What the page draws is decided by the main window (views/main_window.py)
and handed over as plain values; this module only draws and reports clicks:

    ModeStage       the whole picture, drawn at 960×532 and scaled with the
                    window (1280×800 → 1.29). The top bar and navigation are
                    outside it and do not scale. Holds the device, the cream
                    card on the right and the bottom bar.
    ModeBottomBar   from left to right only: the set's name, Bluetooth, and one
                    cell saying one thing (synced / 启用到设备 / N 处没保存 +
                    保存到设备 / 已保存 / 正在写入 / how to get to Codex /
                    没连上设备).
    ModeCard        the cream card the key editor sits in.
    BatteryIcon     34×20, the number knocked out of the fill, orange at 20%
                    and below, a bolt while the Console is connected over USB.

    ModeQuota       AI 额度 at the top left: one small ring per AI app, grown in
                    place to both windows (5h, week) on a click.
    ModeOverview    全部方案: every screen side by side, opened from the set's
                    name on the bar; new / rename / save as / delete / import /
                    export / enable live here.

Batch 4: the connect / disconnect animation (views/mode_motion.py) and the zoom
on the knob and the joystick (ModeStage.set_zoom): the camera pushes in, the
parts are chosen right on the zoomed knob / joystick, the card slides in, and
"‹ 回到整台设备" on the bar goes back.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion, QTransform,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from controller_config.accessibility import system_reduces_motion
from controller_config.digital_font import draw_qpainter, layout_text
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.mode_motion import (
    BATTERY_FADE, CAMERA, CARD_IN, DARKEN, FLOOR_ZOOM, FRAME_MS, PART_WORDS, RING_IN, RING_OUT, SCRAMBLE,
    SIDE_FADE, WORD_WIPE, WORD_ZOOM, ZOOM, ZOOM_CENTRE, ZOOM_IN_SECONDS, ZOOM_OUT_SECONDS, ZOOM_WORD, Link,
    LinkClock, LinkOverlay, Zoom, column_wipe, ease_camera, joystick_cards, letter_origins, phase,
    revealed_from, ripple_dots, scramble_frame,
)
from controller_config.views.mode_layers import ModeBackdrop, ModeSweep, window_root
from controller_config.views.ui.device_top_view import (
    JOYSTICK_CENTER, JOYSTICK_DIAMETER, KNOB_CENTER, KNOB_DIAMETER,
)
from controller_config.views.ui import motion
from controller_config.views.ui.mode_device import ModeDeviceView, paint_neighbour
from controller_config.views.ui.connect_steps_cell import ConnectStepsCell
from controller_config.views.ui.status_icon import StatusIcon

BASE_W = tokens.MODE_BASE_WIDTH
BASE_H = tokens.MODE_BASE_HEIGHT
# Where things sit at scale 1, in the 960×532 page (design y minus 108).
DEVICE_CENTRE = QPointF(480, 253)
PAGE_DOTS_Y = 447
PAGE_DOT_PITCH = 15
BAR_TOP = 465
BAR_HEIGHT = 52


def bar_height(scale: float) -> int:
    """The bar's height at `scale`: a 36 row with 8 above and below (52 at scale 1). Built
    from the rounded parts, so a 36 button sits exactly in the middle at every scale."""
    return round(36 * scale) + 2 * round(8 * scale)
CARD_RECT = QRectF(685, 80, 243, 305)
CARD_TOP_MIN = 8.0          # how high a tall card may rise (it then hides the battery)
KNOB_RIGHT = 297
KNOB_TITLE_Y = 313
KNOB_ROW_YS = (337, 367, 397)
STATUS_X = 672
STATUS_CAPTION_Y = 166
JOYSTICK_CENTRE = QPointF(754, 336)
JOYSTICK_TITLE_Y = 265
FIXED_NOTE_Y = 412
BATTERY_RECT = QRectF(871, 16, 34, 20)
QUOTA_PAD = 20                       # room left of the rings for the names under them
QUOTA_TOP_LEFT = QPointF(40 - QUOTA_PAD, 12)


def apply_scaled_sheet(widget: QWidget, sheet: str) -> None:
    """The one place this page sets a stylesheet: sizes in it follow the page scale,
    so they cannot live in the app-wide sheet (colours still come from tokens).

    Restyling a widget costs 10–20 ms, more than a frame while the window is dragged
    (8.3 ms at 120 Hz; design decision, Console窗口尺寸研究 第 3 节). So while the page is
    being dragged to a new size it waits, and goes on once the drag stops
    (ModeStage._settled).
    """
    stage = _stage_of(widget)
    if stage is not None and stage.resizing():
        widget._sheet_pending = sheet
        return
    widget._sheet_pending = None
    if widget.styleSheet() != sheet:
        widget.setStyleSheet(sheet)


def _stage_of(widget: QWidget | None):
    while widget is not None and not isinstance(widget, ModeStage):
        widget = widget.parentWidget()
    return widget


def settle_scaled_sheets(root: QWidget) -> None:
    """Put on every sheet that waited for a drag to stop, under `root`."""
    for widget in (root, *root.findChildren(QWidget)):
        sheet = getattr(widget, "_sheet_pending", None)
        if sheet is not None:
            apply_scaled_sheet(widget, sheet)
            if hasattr(widget, "_place"):
                widget._place()


def _font(pixel_size: float, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
    font.setPixelSize(max(1, round(pixel_size)))
    font.setBold(bold)
    return font


@dataclass(frozen=True)
class KnobRow:
    icon: str      # ↺ ↻ ●
    verb: str      # 逆时针 / 顺时针 / 按下
    value: str     # what it does


@dataclass(frozen=True)
class StageText:
    """The words around the device, already in the interface language's source text."""

    knob: tuple[KnobRow, ...] = ()
    status_caption: str = "状态灯 ×6"
    status_line: str = ""
    joystick: dict[str, str] = field(default_factory=dict)   # up / down / left / right / press
    joystick_note: tuple[str, str] | None = None             # Codex: what the joystick does instead
    fixed_note: tuple[str, str] | None = None                # Codex: the keys are fixed


@dataclass(frozen=True)
class StageState:
    connected: bool = False
    word: str = "NORMAL"
    screens: int = 1
    viewed: int = 0
    active: int | None = None      # the screen the device is using
    battery: int | None = None
    usb: bool = False
    battery_tip: str = ""
    text: StageText = StageText()


def floor_zoom(origin: QPointF, now: QPointF, camera: float) -> QTransform:
    """The floor's zoom while the camera pushes in on a part: up to FLOOR_ZOOM, centred
    on the part, so the point under it (`origin`, unzoomed) goes where the part is now
    (the designer 11-20 真屏验收 第 17 条)."""
    grow = 1 + (FLOOR_ZOOM - 1) * camera
    transform = QTransform()
    transform.translate(now.x(), now.y())
    transform.scale(grow, grow)
    transform.translate(-origin.x(), -origin.y())
    return transform


class ModeStage(QWidget):
    """The scaled picture; children are placed in 960×532 coordinates."""

    flip_requested = Signal(int)          # -1 / +1
    screen_requested = Signal(int)        # a page dot
    blank_clicked = Signal()              # outside every control: close the card
    zoom_part_chosen = Signal(str)        # a part of the zoomed knob / joystick, e.g. "encoder.cw"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeStage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._state = StageState()
        self._scale = 1.0
        self._origin = QPointF(0, 0)
        self.device = ModeDeviceView(self)
        self.bar = ModeBottomBar(self)
        self.card_host = QFrame(self, objectName="modeCardHost")
        host_layout = QVBoxLayout(self.card_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.card_host.hide()
        self._card: QWidget | None = None
        self.quota = ModeQuota(self)
        self.overview = ModeOverview(self)
        self.overview.hide()
        self._link: Link | None = None
        self._link_clock = LinkClock(self)
        self.link_overlay = LinkOverlay(self)
        self.link_overlay.stackUnder(self.bar)    # over the device, under the bar and the card
        self._zoom_target: str | None = None      # "encoder" / "joystick" while zoomed (or zooming out)
        self._zoom_sub: str | None = None         # the part chosen on it, e.g. "encoder.ccw"
        self._zoom: Zoom | None = None            # the zoom running now; None when still
        self._zoom_clock = QTimer(self)
        self._zoom_clock.setInterval(FRAME_MS)
        self._zoom_clock.timeout.connect(self._zoom_tick)
        self._quota_fade = QGraphicsOpacityEffect(self.quota)
        self._quota_fade.setOpacity(1.0)
        self._quota_fade.setEnabled(False)
        self.quota.setGraphicsEffect(self._quota_fade)
        self._card_home: QRectF | None = None    # where the card sits when it has slid in
        self._backdrop: ModeBackdrop | None = None   # under the whole window (design decision)
        self._sweep: ModeSweep | None = None         # over the whole window while the sheet runs
        self._before: QPixmap | None = None          # the device just before it disconnected

    # -- the knob and the joystick, zoomed (11, 12) ----------------------------------------

    def set_zoom(self, target: str | None, sub: str | None = None, *, animate: bool = True) -> None:
        """Zoom into the knob ("encoder") or the joystick, or back out with None."""
        target = target if target in ZOOM else None
        if target is not None and target == self._zoom_target:
            self._zoom_sub = sub
            if self._zoom is not None and self._zoom.kind == "out":
                self._start_zoom("in", animate)    # clicked again while it was going back
            self._redraw()
            return
        if target is None:
            if self._zoom_target is not None:
                self._start_zoom("out", animate)
            return
        self._zoom_target, self._zoom_sub = target, sub
        self._zoom = None
        self._start_zoom("in", animate, from_start=True)

    def zoom_target(self) -> str | None:
        return self._zoom_target

    def zoom_sub(self) -> str | None:
        return self._zoom_sub

    def zoom_timeline(self) -> float:
        """Where on the 0.72 s zoom-in timeline the picture is (0: whole device)."""
        if self._zoom_target is None:
            return 0.0
        return ZOOM_IN_SECONDS if self._zoom is None else self._zoom.timeline()

    def _start_zoom(self, kind: str, animate: bool, *, from_start: bool = False) -> None:
        if not animate or system_reduces_motion():
            self._zoom = None
            self._zoom_clock.stop()
            if kind == "out":
                self._zoom_target = self._zoom_sub = None
        else:
            now, at = time.monotonic(), 0.0 if from_start else self.zoom_timeline()
            started = (now - at if kind == "in"
                       else now - ZOOM_OUT_SECONDS * (1 - at / ZOOM_IN_SECONDS))
            self._zoom = Zoom(kind, started)
            self._zoom_clock.start()
        self._apply_zoom()
        self._redraw()

    def _zoom_tick(self) -> None:
        if self._zoom is not None and self._zoom.done():
            if self._zoom.kind == "out":
                self._zoom_target = self._zoom_sub = None
            self._zoom = None
            self._zoom_clock.stop()
        self._apply_zoom()
        self._redraw()

    def _apply_zoom(self) -> None:
        """Widgets that follow the zoom: the device widget (drawn by the stage while zoomed),
        the quota (fades), the card (slides in late)."""
        t = self.zoom_timeline()
        self.device.setVisible(t <= 0)
        fade = phase(t, *SIDE_FADE) if self._zoom_target else 0.0
        self._quota_fade.setEnabled(fade > 0)
        self._quota_fade.setOpacity(1 - fade)
        self.quota.setVisible(fade < 1)
        if self._card is not None and self._card_home is not None:
            share = ease_camera(phase(t, *CARD_IN)) if self._zoom_target else 1.0
            self.card_host.setVisible(share > 0)
            home = self._card_home
            self.card_host.move(round(home.left() + (1 - share) * 48 * self._scale), round(home.top()))

    def part_page(self, part: str) -> tuple[QPointF, float]:
        """Centre and radius of the knob or the joystick on the page (unzoomed)."""
        centre_mm, diameter = (KNOB_CENTER, KNOB_DIAMETER) if part == "encoder" else (JOYSTICK_CENTER, JOYSTICK_DIAMETER)
        point = self.device._point(*centre_mm)
        widget = QPointF(self.device.geometry().topLeft()) + point
        page = QPointF((widget.x() - self._origin.x()) / self._scale, (widget.y() - self._origin.y()) / self._scale)
        return page, diameter * self.device._scale() / 2 / self._scale

    def _zoom_frame(self) -> tuple[float, QPointF, float, float]:
        """(camera 0..1, where the part is now, how much it is enlarged, its radius now)."""
        target = self._zoom_target
        t = self.zoom_timeline()
        camera = ease_camera(phase(t, *CAMERA))
        origin, radius = self.part_page(target)
        grow = 1 + (ZOOM[target] - 1) * camera
        now = origin + (ZOOM_CENTRE - origin) * camera
        return camera, now, grow, radius * grow

    def zoom_parts(self) -> dict[str, QRectF]:
        """Where each part of the zoomed knob / joystick can be clicked (widget coordinates)."""
        if self._zoom_target is None or self.zoom_timeline() < PART_WORDS[0]:
            return {}
        _camera, centre, _grow, radius = self._zoom_frame()
        if self._zoom_target == "encoder":
            third = radius * 2 / 3
            top = centre.y() - radius
            return {
                "encoder.ccw": self.rect_to_widget(QRectF(centre.x() - radius, top, third, radius * 2)),
                "encoder.press": self.rect_to_widget(QRectF(centre.x() - radius + third, top, third, radius * 2)),
                "encoder.cw": self.rect_to_widget(QRectF(centre.x() - radius + third * 2, top, third, radius * 2)),
            }
        parts = {f"joystick.{direction}": self.rect_to_widget(rect)
                 for direction, rect in joystick_cards(centre, 1.0).items()}
        parts["joystick.press"] = self.rect_to_widget(QRectF(centre.x() - radius, centre.y() - radius,
                                                             radius * 2, radius * 2))
        return parts

    # -- connecting and disconnecting (06) -----------------------------------------------

    def play_link(self, link: Link | None) -> None:
        """Run (or carry on) the connect / disconnect animation; None or a finished one stops it."""
        if link is None or link.done() or system_reduces_motion() or link.kind == "in":
            self._before = None
        if link is None or link.done() or system_reduces_motion():
            self._link = None
            self._link_clock.stop()
            self.link_overlay.hide()
        else:
            if self._link != link:
                self.link_overlay.frames = self.link_overlay.dots_drawn = 0
            self._link = link
            self.link_overlay.setGeometry(self.rect())
            self.link_overlay.show()
            self._link_clock.start()
        self._show_sweep()
        self._redraw()

    def link(self) -> Link | None:
        return self._link

    def link_tick(self) -> None:
        if self._link is not None and self._link.done():
            self.play_link(None)
            return
        self._redraw()
        self.link_overlay.update()
        if self.sweep_shown():
            self._sweep.update()

    def origin(self) -> QPointF:
        return QPointF(self._origin)

    def device_page_rect(self) -> QRectF:
        body = tokens.MODE_DEVICE_BODY
        return QRectF(DEVICE_CENTRE.x() - body / 2, DEVICE_CENTRE.y() - body / 2, body, body)

    # -- state -----------------------------------------------------------------------

    def set_state(self, state: StageState) -> None:
        if self._state.connected and not state.connected:
            # The device as it was just before the connection went: it stays like this
            # until the backwards sweep has passed over it (design decision, -06 第 3 件).
            ratio = self.device.devicePixelRatioF()
            before = QPixmap(max(1, math.ceil(self.device.width() * ratio)),
                             max(1, math.ceil(self.device.height() * ratio)))
            before.setDevicePixelRatio(ratio)
            before.fill(Qt.GlobalColor.transparent)
            self.device.render_plain(before)
            self._before = before
        self._state = state
        self.device.set_connected(state.connected)
        self._redraw()

    def state(self) -> StageState:
        return self._state

    def scale(self) -> float:
        return self._scale

    def set_card(self, card: QWidget | None) -> None:
        """The cream card on the right, or None to show the words about the controls."""
        layout = self.card_host.layout()
        if self._card is not None and self._card is not card:
            layout.removeWidget(self._card)
            self._card.hide()
            self._card.setParent(None)
            self._card.deleteLater()
        self._card = card
        if card is not None:
            if card.parent() is not self.card_host:
                layout.addWidget(card)
            card.show()
        self.card_host.setVisible(card is not None)
        self._place()
        QTimer.singleShot(0, self, self._place)  # once the card's layout has settled
        self._redraw()

    def card(self) -> QWidget | None:
        return self._card

    def show_overview(self, shown: bool) -> None:
        """全部方案 over the whole page (10); the page underneath stays as it was."""
        if shown:
            self.overview.setGeometry(self.rect())
            self.overview.set_scale(self._scale, force=True)
            self.overview.show()
            self.overview.raise_()
        else:
            self.overview.hide()
        self._redraw()

    def overview_shown(self) -> bool:
        return self.overview.isVisible()

    # -- geometry --------------------------------------------------------------------

    def to_widget(self, point: QPointF) -> QPointF:
        return QPointF(self._origin.x() + point.x() * self._scale, self._origin.y() + point.y() * self._scale)

    def rect_to_widget(self, rect: QRectF) -> QRectF:
        top_left = self.to_widget(rect.topLeft())
        return QRectF(top_left.x(), top_left.y(), rect.width() * self._scale, rect.height() * self._scale)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        # While the window is dragged, costly pictures (the floor, the device) are stretched
        # and sheets wait; all are drawn again once it has stood still a moment (design decision).
        # Counted by the window's size: one resize of it can reach the stage twice.
        if self.isVisible():
            now, size = time.monotonic(), self.window().size()
            if size != getattr(self, "_window_size", None):
                # Counted from when the last one was drawn, not from when it came: by hand the
                # system sends the next size only once a frame is on screen, so a slow frame
                # would otherwise keep every gap too long to be a drag (真屏 11-22 手拖窗口).
                in_a_row = now - getattr(self, "_resized_at", -1.0) < tokens.MODE_DRAG_GAP_MS / 1000
                self._resized_at, self._window_size = now, size
                self._resize_unpainted = True
                # One resize on its own (maximise, a size set by the app) is laid out at once;
                # only a run of them close together is a drag.
                self._dragging = in_a_row or (getattr(self, "_dragging", False) and self.resizing())
                self._settle_clock().start(tokens.MODE_SHEET_SETTLE_MS)
                if self._dragging:
                    for view in self.findChildren(ModeDeviceView):
                        view.hold_for_resize()   # at its old size, before _place gives it the new one
        self._place()
        self._redraw()

    def _settle_clock(self) -> QTimer:
        clock = getattr(self, "_settle_timer", None)
        if clock is None:
            clock = self._settle_timer = QTimer(self)
            clock.setSingleShot(True)
            clock.timeout.connect(self._settled)
        return clock

    def _settled(self) -> None:
        """The drag has stopped: sizes, sheets and pictures as they should be at this size."""
        self._drag_cache = None
        self._dragging = False
        settle_scaled_sheets(self)
        self._place()
        for view in self.findChildren(ModeDeviceView):
            view.drop_resize_picture()
            view.update()
        self._redraw()

    def resizing(self) -> bool:
        """True while the window's edge is being dragged (resizes close together, the
        last one less than MODE_SHEET_SETTLE_MS ago)."""
        clock = getattr(self, "_settle_timer", None)
        return clock is not None and clock.isActive() and getattr(self, "_dragging", False)

    def moveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().moveEvent(event)
        self._redraw()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        root = window_root(self)
        if self._backdrop is None and root is not None:
            self._backdrop = ModeBackdrop(self, root)
            self._sweep = ModeSweep(self, root)
        self._show_sweep()
        if self._backdrop is not None:
            self._backdrop.show()
            self._backdrop.lower()
            self._backdrop.update()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().hideEvent(event)
        if self._backdrop is not None:
            self._backdrop.hide()
            self._sweep.hide()

    def _show_sweep(self) -> None:
        if self._sweep is not None:
            running = self._link is not None and self.isVisible()
            self._sweep.setVisible(running)
            if running:
                self._sweep.raise_()
                self._sweep.update()

    def before_picture(self) -> QPixmap | None:
        """The device as it was before disconnecting, while the backwards sweep runs."""
        return self._before

    def sweep_shown(self) -> bool:
        return self._sweep is not None and self._sweep.isVisible()

    def sweep_span(self) -> QRectF:
        """What the connect / disconnect sheet crosses, in page units: the whole window,
        top bar included (design decision); the stage itself outside a window."""
        return self.window_page_rect()

    def _backdrop_shown(self) -> bool:
        return self._backdrop is not None and self._backdrop.isVisible()

    def _redraw(self) -> None:
        """Repaint the stage and what it draws under the whole window."""
        self.update()
        if self._backdrop_shown():
            self._backdrop.update()

    def window_page_rect(self) -> QRectF:
        """The inside of the window in page units (the stage's own rect outside a window)."""
        s, origin = self._scale, self._origin
        root = window_root(self)
        if root is None:
            return QRectF(-origin.x() / s, -origin.y() / s, self.width() / s, self.height() / s)
        at = self.mapTo(root, QPoint(0, 0))
        return QRectF((-at.x() - origin.x()) / s, (-at.y() - origin.y()) / s, root.width() / s, root.height() / s)

    def _place(self) -> None:
        width, height = max(1, self.width()), max(1, self.height())
        self._scale = min(width / BASE_W, height / BASE_H)
        self._origin = QPointF((width - BASE_W * self._scale) / 2, (height - BASE_H * self._scale) / 2)
        s = self._scale
        side = round(tokens.MODE_DEVICE_BODY * 1.1 * s)
        centre = self.to_widget(DEVICE_CENTRE)
        self.device.setGeometry(round(centre.x() - side / 2), round(centre.y() - side / 2), side, side)
        self.bar.set_scale(s)
        hint = self.bar.sizeHint()
        bar_top = self.to_widget(QPointF(0, BAR_TOP)).y()
        self.bar.setGeometry(round(width / 2 - hint.width() / 2), round(bar_top),
                             hint.width(), hint.height())
        if self._card is not None:
            if hasattr(self._card, "set_scale"):
                self._card.set_scale(s)
            rect = self.rect_to_widget(CARD_RECT)
            room = bar_top - rect.top() - 16 * s
            self._card.ensurePolished()
            for widget in self._card.findChildren(QWidget):
                widget.ensurePolished()
            if self._card.layout() is not None:
                self._card.layout().activate()
            wanted = (self._card.wanted_height(round(rect.width())) if hasattr(self._card, "wanted_height")
                      else self._card.sizeHint().height())
            # The card shows everything and never scrolls (单 design ticket 第 1 处): when it is
            # taller than the room above the bar, it rises (no higher than CARD_TOP_MIN); the body
            # scrolls only if even that is not enough.
            body = self._card.findChild(QScrollArea, "mappingEditorBody")
            for _attempt in range(3):
                top = max(self.to_widget(QPointF(0, CARD_TOP_MIN)).y(), min(rect.top(), bar_top - 16 * s - wanted))
                height = round(max(min(wanted, bar_top - top - 16 * s), min(room, 160 * s)))
                self.card_host.setGeometry(round(rect.left()), round(top), round(rect.width()), height)
                if self.card_host.layout() is not None:
                    self.card_host.layout().activate()
                if self._card.layout() is not None:
                    self._card.layout().activate()
                hidden = body.verticalScrollBar().maximum() if body is not None and body.widget() is not None else 0
                if hidden <= 0 or height < wanted:
                    break
                wanted = height + hidden   # the estimate missed wrapped text: grow by what is hidden
            self._card_home = QRectF(self.card_host.geometry())
            self.card_host.raise_()
            self._apply_zoom()
        if self.overview.isVisible():
            self.overview.setGeometry(self.rect())
            self.overview.set_scale(s)
        quota = self.rect_to_widget(QRectF(QUOTA_TOP_LEFT.x(), QUOTA_TOP_LEFT.y(), QUOTA_WIDTH + QUOTA_PAD, QUOTA_HEIGHT))
        self.quota.setGeometry(quota.toAlignedRect())
        self.quota.set_scale(s)
        self.link_overlay.setGeometry(self.rect())

    # -- input -----------------------------------------------------------------------

    def page_dot_rects(self) -> list[QRectF]:
        count = self._state.screens
        first = BASE_W / 2 - (count - 1) * PAGE_DOT_PITCH / 2
        return [self.rect_to_widget(QRectF(first + index * PAGE_DOT_PITCH - 7, PAGE_DOTS_Y - 7, 14, 14))
                for index in range(count)]

    def neighbour_centres(self) -> tuple[QPointF | None, QPointF | None]:
        """Where the screens either side sit (page units): pushed out so that only
        MODE_NEIGHBOUR_SHOWN of each is inside the window, counted from the window's
        edge, not the page's (design ticket 第 2 件)."""
        body = tokens.MODE_DEVICE_BODY * tokens.MODE_NEIGHBOUR_SCALE
        inside = self.window_page_rect()
        reach = tokens.MODE_NEIGHBOUR_SHOWN * body - body / 2
        left = right = None
        if self._state.viewed > 0:
            left = QPointF(min(inside.left() + reach, DEVICE_CENTRE.x() - body), DEVICE_CENTRE.y())
        if self._state.viewed < self._state.screens - 1:
            right = QPointF(max(inside.right() - reach, DEVICE_CENTRE.x() + body), DEVICE_CENTRE.y())
        return left, right

    def neighbour_rects(self) -> tuple[QRectF | None, QRectF | None]:
        body = tokens.MODE_DEVICE_BODY * tokens.MODE_NEIGHBOUR_SCALE
        return tuple(None if centre is None else
                     self.rect_to_widget(QRectF(centre.x() - body / 2, centre.y() - body / 2, body, body))
                     for centre in self.neighbour_centres())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        position = event.position()
        for part, rect in self.zoom_parts().items():
            if rect.contains(position):
                self.zoom_part_chosen.emit(part)
                return
        if self._zoom_target is not None:
            self.blank_clicked.emit()     # beside the zoomed part: back to the whole device
            return
        for index, rect in enumerate(self.page_dot_rects()):
            if rect.contains(position):
                self.screen_requested.emit(index)
                return
        left, right = self.neighbour_rects()
        if left is not None and left.intersected(QRectF(self.rect())).contains(position):
            self.flip_requested.emit(-1)
            return
        if right is not None and right.intersected(QRectF(self.rect())).contains(position):
            self.flip_requested.emit(1)
            return
        if self.quota.is_open():
            self.quota.set_open(False)   # 再点一下，或点别处，收回去 (07)
            return
        self.blank_clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.key() == Qt.Key.Key_Escape and self._zoom_target is not None:
            self.blank_clicked.emit()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right} and self._zoom_target is None:
            self.flip_requested.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def event(self, event) -> bool:
        if event.type() == event.Type.ToolTip:
            state = self._state
            battery = self.rect_to_widget(BATTERY_RECT.adjusted(0, 0, 18, 0))
            if (state.connected and state.battery is not None and not self.card_hides_battery()
                    and battery.contains(QPointF(event.pos()))):
                QToolTip.showText(event.globalPos(), tr(state.battery_tip) or f"{state.battery}%", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        # A sideways swipe on the trackpad flips; one flip per gesture.
        dx = event.angleDelta().x()
        if abs(dx) >= 120 and abs(dx) > abs(event.angleDelta().y()) and event.phase() in {
            Qt.ScrollPhase.ScrollUpdate, Qt.ScrollPhase.NoScrollPhase,
        } and not getattr(self, "_swiped", False):
            self._swiped = True
            self.flip_requested.emit(1 if dx < 0 else -1)
        if event.phase() in {Qt.ScrollPhase.ScrollEnd, Qt.ScrollPhase.NoScrollPhase}:
            self._swiped = False
        event.accept()

    # -- painting --------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        try:
            if self.overview_shown():
                return   # 全部方案 covers the whole stage with its own fill
            if self.resizing() and self._link is None and self._zoom is None:
                self._paint_while_resizing()
                return
            painter = QPainter(self)
            self._paint_page(painter)
            painter.end()
        finally:
            if getattr(self, "_resize_unpainted", False):
                self._resize_unpainted = False
                self._resized_at = time.monotonic()   # the gap to the next resize starts now

    def _paint_while_resizing(self) -> None:
        """While the window is dragged, the page as drawn when the drag began, stretched
        to the new size; drawn sharp again once it stops (design decision: 拖窗口每帧不超过 8 毫秒)."""
        key = (id(self._state), id(self._card), self._zoom_target, self._zoom_sub,
               self._backdrop_shown(), self.card_hides_battery(), self.devicePixelRatioF())
        cache = getattr(self, "_drag_cache", None)
        if cache is None or cache[0] != key:
            ratio = self.devicePixelRatioF()
            picture = QPixmap(max(1, math.ceil(self.width() * ratio)), max(1, math.ceil(self.height() * ratio)))
            picture.setDevicePixelRatio(ratio)
            picture.fill(Qt.GlobalColor.transparent)
            painter = QPainter(picture)
            self._paint_page(painter)
            painter.end()
            cache = self._drag_cache = (key, picture, QPointF(self._origin), self._scale)
        _key, picture, origin, scale = cache
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(self._origin)
        painter.scale(self._scale / scale, self._scale / scale)
        painter.translate(-origin)
        painter.drawPixmap(QPointF(0, 0), picture)
        painter.end()

    def _paint_page(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # No floor of its own: the window's background shows through, so the stage has no
        # edges to see (Mode页 定稿 v1, design ticket).
        painter.translate(self._origin)
        painter.scale(self._scale, self._scale)
        state = self._state
        link = self._link
        lit = state.connected or (link is not None and link.kind == "out")
        zooming = self._zoom_target is not None and self.zoom_timeline() > 0
        t = self.zoom_timeline()
        if not self._backdrop_shown():
            self.paint_backdrop(painter)   # in a window, the backdrop draws these out to its edges
        if lit:
            painter.save()
            self._lit_layer(painter)
            self._paint_word(painter)
            painter.restore()
        painter.save()
        if zooming:
            painter.setOpacity(1 - phase(t, *SIDE_FADE))   # side words fade out
        self._paint_knob_words(painter)
        if self._card is None:
            self._paint_right_words(painter)
        painter.restore()
        self._paint_page_dots(painter)
        if zooming:
            self._paint_zoomed(painter)
        if state.connected and state.battery is not None and not self.card_hides_battery():
            painter.save()
            if link is not None and link.kind == "in":
                painter.setOpacity(phase(link.elapsed(), *BATTERY_FADE))
            paint_battery(painter, BATTERY_RECT, state.battery, usb=state.usb)
            painter.restore()

    def card_hides_battery(self) -> bool:
        """A tall card that rose to the battery's height hides it whole, not half."""
        if self._card is None or self.card_host.isHidden():
            return False
        return self.card_host.geometry().top() < self.rect_to_widget(BATTERY_RECT).bottom() + 4 * self._scale

    def floor_picture(self) -> tuple[QPixmap, float]:
        return self._floor()

    def _floor(self) -> tuple[QPixmap, float]:
        """The dot floor drawn once per size, and how far down it goes (page units): it
        never moves, and the connect animation repaints the page every frame."""
        ratio = self.devicePixelRatioF()
        bottom = max(float(BASE_H), math.ceil(self.window_page_rect().bottom()))
        key = (round(self._scale, 4), ratio, bottom)
        if getattr(self, "_floor_key", None) not in (None, key) and self.resizing():
            return self._floor_pixmap, self._floor_key[2]    # stretched until the drag stops
        if getattr(self, "_floor_key", None) != key:
            pixmap = QPixmap(max(1, math.ceil(BASE_W * self._scale * ratio)),
                             max(1, math.ceil(bottom * self._scale * ratio)))
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(self._scale * ratio, self._scale * ratio)
            paint_floor(painter, bottom=bottom)
            painter.end()
            self._floor_pixmap, self._floor_key = pixmap, key
        return self._floor_pixmap, bottom

    def _paint_word(self, painter: QPainter) -> None:
        word = self._state.word
        scale = tokens.MODE_WORD_SCALE
        layout = layout_text(word, scale=scale)
        left = BASE_W / 2 - layout.width / 2
        if self._zoom_target is not None and self.zoom_timeline() > 0:
            self._paint_zoom_word(painter, layout, left)
            return
        link = self._link
        if link is None or link.kind != "in" or link.elapsed() >= SCRAMBLE[1]:
            draw_qpainter(painter, word, left, 0, scale=scale, color=tokens.MODE_WORD_DOT)
            return
        # Connecting: every letter flickers through others, then they settle one by one (06).
        for x, (letter, light, flash) in zip(letter_origins(word, scale),
                                             scramble_frame(word, link.elapsed()), strict=True):
            if light <= 0 or letter == " ":
                continue
            colour = QColor(tokens.MODE_SWEEP_DOT if flash else tokens.MODE_WORD_DOT)
            colour.setAlphaF(light)
            draw_qpainter(painter, letter, left + x, 0, scale=scale, color=colour)

    def _paint_zoom_word(self, painter: QPainter, layout, left: float) -> None:
        """NORMAL goes out column by column, then KNOB / STICK comes in; the word grows 1.05× (12)."""
        t = self.zoom_timeline()
        camera = self._zoom_frame()[0]
        scale = tokens.MODE_WORD_SCALE
        new = layout_text(ZOOM_WORD[self._zoom_target], scale=scale)
        new_left = BASE_W / 2 - new.width / 2
        span_left = min(left, new_left)
        width = max(left + layout.width, new_left + new.width) - span_left
        old_dots = [(x + left - span_left, y, r) for x, y, r in layout.dots]
        new_dots = [(x + new_left - span_left, y, r) for x, y, r in new.dots]
        grow = 1 + (WORD_ZOOM - 1) * camera
        painter.save()
        painter.translate(BASE_W / 2, 0)
        painter.scale(grow, grow)
        painter.translate(-BASE_W / 2, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.MODE_WORD_DOT))
        for x, y, radius in column_wipe(old_dots, new_dots, phase(t, *WORD_WIPE), width):
            painter.drawEllipse(QPointF(span_left + x, y), radius, radius)
        painter.restore()

    def _paint_ripple(self, painter: QPainter) -> None:
        """One ring of bright floor dots spreading from the part, once (12).

        In floor coordinates (the caller has the floor's zoom on the painter): from
        the part's unzoomed place, which the floor's zoom takes to the part's centre now.
        """
        origin = self.part_page(self._zoom_target)[0]
        bright = QColor(tokens.MODE_SWEEP_DOT)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for x, y, light in ripple_dots(origin, self.zoom_timeline(), bottom=self._floor()[1]):
            colour = QColor(bright)
            colour.setAlphaF(0.55 * light)
            painter.setBrush(colour)
            radius = tokens.MODE_FLOOR_DOT_SIZE / 2 * (1 + 0.8 * light)
            painter.drawEllipse(QPointF(x, y), radius, radius)
        painter.restore()

    def _paint_zoomed(self, painter: QPainter) -> None:
        """The device pushed in on the knob / joystick, the keys round it darkening, the
        orange ring, then the words on the part (and the joystick's four cards)."""
        t = self.zoom_timeline()
        target = self._zoom_target
        camera, centre, grow, radius = self._zoom_frame()
        origin = self.part_page(target)[0]
        s = self._scale
        # The device goes into a layer of its own that fades out round the part, so the
        # floor and the word show through where the other keys have darkened (11, 12).
        ratio = self.devicePixelRatioF()
        size = (max(1, math.ceil(self.width() * ratio)), max(1, math.ceil(self.height() * ratio)))
        if getattr(self, "_zoom_layer", None) is None or (self._zoom_layer.width(), self._zoom_layer.height()) != size:
            self._zoom_layer = QImage(size[0], size[1], QImage.Format.Format_ARGB32_Premultiplied)
            self._zoom_layer.setDevicePixelRatio(ratio)
        layer = self._zoom_layer
        layer.fill(Qt.GlobalColor.transparent)
        paint = QPainter(layer)
        paint.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint.translate(self._origin)
        paint.scale(s, s)
        paint.save()
        paint.translate(centre)
        paint.scale(grow, grow)
        paint.translate(-origin)
        geometry = self.device.geometry()
        paint.translate((geometry.x() - self._origin.x()) / s, (geometry.y() - self._origin.y()) / s)
        paint.scale(1 / s, 1 / s)
        dark = phase(t, *DARKEN)
        keep_labels, keep_ticks = self.device.labels_from, self.device.knob_ticks
        if dark > 0.5:
            self.device.labels_from = 10 ** 9     # the words on the keys have faded by now
        self.device.knob_ticks = 1 - dark          # and so have the knob's tick marks
        self.device.render_plain(paint)
        self.device.labels_from, self.device.knob_ticks = keep_labels, keep_ticks
        paint.restore()
        if dark > 0:
            reach = radius * (2.4 if target == "encoder" else 3.6)
            gradient = QRadialGradient(centre, reach)
            keep, gone = QColor(Qt.GlobalColor.black), QColor(Qt.GlobalColor.black)   # a mask: only alpha counts
            gone.setAlphaF(1 - dark)
            gradient.setColorAt(0.0, keep)
            gradient.setColorAt(min(0.95, radius * 1.12 / reach), keep)
            gradient.setColorAt(1.0, gone)
            paint.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            paint.fillRect(QRectF(-BASE_W, -BASE_H, BASE_W * 3, BASE_H * 3), gradient)
        paint.end()
        painter.save()
        painter.resetTransform()
        painter.drawImage(0, 0, layer)
        painter.restore()
        if self._state.connected:
            self._paint_word(painter)   # the word stays readable over the faded device (11)
        ring = phase(t, *RING_IN) * (1 - phase(t, *RING_OUT))
        if ring > 0:
            colour = QColor(tokens.TOP_VIEW_SELECTED)
            colour.setAlphaF(ring)
            painter.setPen(QPen(colour, 2.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, radius + 3, radius + 3)
        share = phase(t, *PART_WORDS)
        if share > 0:
            unit = grow / ZOOM[target]
            if target == "encoder":
                self._paint_knob_parts(painter, centre, radius, unit, share)
            else:
                self._paint_joystick_parts(painter, centre, radius, unit, share)

    def _paint_knob_parts(self, painter: QPainter, centre: QPointF, radius: float, unit: float, share: float) -> None:
        """逆时针 / 按下 / 顺时针 on the knob with what each does; two turning arcs (11)."""
        rows = self._state.text.knob
        value = {part: rows[index].value if index < len(rows) else ""
                 for index, part in ((0, "encoder.ccw"), (1, "encoder.cw"), (2, "encoder.press"))}
        columns = (("encoder.ccw", "逆时针", -0.5), ("encoder.press", "按下", 0.0), ("encoder.cw", "顺时针", 0.5))
        for index, (part, verb, across) in enumerate(columns):
            alpha = phase(share, index * 0.22, index * 0.22 + 0.5)
            if alpha <= 0:
                continue
            chosen = part == self._zoom_sub
            painter.save()
            painter.setOpacity(alpha)
            x = centre.x() + across * radius
            self._text(painter, QRectF(x - radius * 0.3, centre.y() - radius * 0.2, radius * 0.6, 18 * unit), verb,
                       size=11 * unit, colour=tokens.TOP_VIEW_SELECTED if chosen else tokens.TEXT_SECONDARY,
                       align=Qt.AlignmentFlag.AlignCenter)
            shown = tr(value[part])
            size = 17.0 * unit
            while size > 9 * unit and QFontMetricsF(_font(size, bold=True)).horizontalAdvance(shown) > radius * 0.46:
                size -= 0.5
            self._text(painter, QRectF(x - radius * 0.3, centre.y() - radius * 0.04, radius * 0.6, 26 * unit), shown,
                       size=size, colour=tokens.TEXT_PRIMARY, bold=True, align=Qt.AlignmentFlag.AlignCenter)
            painter.restore()
        arc = radius * 0.82
        box = QRectF(centre.x() - arc, centre.y() - arc, arc * 2, arc * 2)
        for part, start, span in (("encoder.ccw", 112.0, 136.0), ("encoder.cw", 68.0, -136.0)):
            chosen = part == self._zoom_sub
            colour = QColor(tokens.TOP_VIEW_SELECTED if chosen else tokens.TEXT_TERTIARY)
            pen = QPen(colour, 2.4 * unit)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            drawn = span * share
            painter.drawArc(box, round(start * 16), round(drawn * 16))
            if share >= 0.98:   # the arrow head at the end of the turn
                end = math.radians(start + drawn)
                tip = QPointF(centre.x() + arc * math.cos(end), centre.y() - arc * math.sin(end))
                along = math.radians(start + drawn + (90 if span > 0 else -90))
                back = QPointF(-math.cos(along), math.sin(along))
                side = QPointF(-back.y(), back.x())
                head = 9 * unit
                for sign in (1, -1):
                    painter.drawLine(tip, tip + (back + side * sign) * (head / math.sqrt(2)))

    def _paint_joystick_parts(self, painter: QPainter, centre: QPointF, radius: float, unit: float,
                              share: float) -> None:
        """按下 on the stick; 向上 / 向下 / 向左 / 向右 on four small cards popping out (11)."""
        values = self._state.text.joystick
        names = {"up": "向上", "down": "向下", "left": "向左", "right": "向右"}
        pop = ease_camera(share)
        for direction, rect in joystick_cards(centre, pop * unit).items():
            chosen = self._zoom_sub == f"joystick.{direction}"
            painter.save()
            painter.setOpacity(min(1.0, share * 2))
            painter.setPen(QPen(QColor(tokens.MODE_BAR_EDGE), 1))
            painter.setBrush(QColor(tokens.MODE_CARD if chosen else tokens.MODE_ZOOM_CARD))
            painter.drawRoundedRect(rect, 14 * unit, 14 * unit)
            ink = tokens.MODE_CARD_TEXT if chosen else tokens.TEXT_PRIMARY
            soft = tokens.MODE_CARD_MUTED if chosen else tokens.TEXT_SECONDARY
            pad = 12 * unit * pop
            self._text(painter, QRectF(rect.left() + pad, rect.top() + 6 * unit, rect.width() - pad * 2, 16 * unit),
                       names[direction], size=10.5 * unit * pop + 0.01, colour=soft)
            self._text(painter, QRectF(rect.left() + pad, rect.top() + 24 * unit, rect.width() - pad * 2, 26 * unit),
                       values.get(direction, ""), size=18 * unit * pop + 0.01, colour=ink, bold=True)
            painter.restore()
        chosen = self._zoom_sub == "joystick.press"
        painter.save()
        painter.setOpacity(share)
        self._text(painter, QRectF(centre.x() - radius, centre.y() - radius * 0.42, radius * 2, 16 * unit), "按下",
                   size=10.5 * unit, colour=tokens.TOP_VIEW_SELECTED if chosen else tokens.MODE_CARD_MUTED,
                   align=Qt.AlignmentFlag.AlignCenter)
        press = tr(values.get("press", ""))
        size = 17.0 * unit
        while size > 8 * unit and QFontMetricsF(_font(size, bold=True)).horizontalAdvance(press) > radius * 1.6:
            size -= 0.5
        self._text(painter, QRectF(centre.x() - radius, centre.y() - radius * 0.12, radius * 2, 26 * unit), press,
                   size=size, colour=tokens.MODE_CARD_TEXT, bold=True, align=Qt.AlignmentFlag.AlignCenter)
        painter.restore()

    def _paint_neighbours(self, painter: QPainter) -> None:
        """The screens either side, blurred; they fade out while a part is zoomed (12)."""
        body = tokens.MODE_DEVICE_BODY * tokens.MODE_NEIGHBOUR_SCALE
        painter.save()
        if self._zoom_target is not None and self.zoom_timeline() > 0:
            painter.setOpacity(1 - phase(self.zoom_timeline(), *SIDE_FADE))
        for centre in self.neighbour_centres():
            if centre is not None:
                paint_neighbour(painter, centre, body, blur=tokens.MODE_NEIGHBOUR_BLUR)
        painter.restore()

    def _lit_layer(self, painter: QPainter) -> None:
        """Clip or dim the painter for what only shows with a device: while connecting
        it comes back only where the sheet has melted (06); going out it stays until the
        sweep has passed, then dims with the page."""
        link = self._link
        if link is not None and link.kind == "in":
            span = self.sweep_span()
            painter.setClipRect(QRectF(span.left() - BASE_W, revealed_from(link.sweep_progress(), span),
                                       span.width() + BASE_W * 2, span.height() + BASE_H * 2))
        elif link is not None:
            painter.setOpacity(1 - link.dim())

    def _paint_floor(self, painter: QPainter) -> None:
        """The dot floor, down to the window's bottom edge (design ticket 第 4 件)."""
        state, link = self._state, self._link
        if not (state.connected or (link is not None and link.kind == "out")):
            return
        painter.save()
        self._lit_layer(painter)
        zooming = self._zoom_target is not None and self.zoom_timeline() > 0
        if zooming:
            # The floor grows only to 1.4× round the part (12), and stays centred on it:
            # the dot under the knob / joystick moves with it to where the camera takes
            # it (the designer 11-20 真屏验收 第 17 条: the zoom was not on the part's centre).
            camera, now, _grow, _radius = self._zoom_frame()
            origin = self.part_page(self._zoom_target)[0]
            painter.setTransform(floor_zoom(origin, now, camera), True)
        floor, bottom = self._floor()
        painter.drawPixmap(QRectF(0, 0, BASE_W, bottom), floor, QRectF(floor.rect()))
        if zooming:
            # Drawn on the floor, so the ring spreads from the part's centre wherever
            # the camera has moved it, and its dots are the floor's dots.
            self._paint_ripple(painter)
        painter.restore()

    def paint_backdrop(self, painter: QPainter) -> None:
        """What reaches the window's edges, in page units (views/mode_layers.py): the dot
        floor and the screens either side. Outside a window the stage paints it itself,
        cut by its own edges. Nothing while 全部方案 covers the page: it has its own floor."""
        if self.overview_shown():
            return
        self._paint_floor(painter)
        self._paint_neighbours(painter)

    def _paint_page_dots(self, painter: QPainter) -> None:
        count = self._state.screens
        if count < 2:
            return
        first = BASE_W / 2 - (count - 1) * PAGE_DOT_PITCH / 2
        for index in range(count):
            centre = QPointF(first + index * PAGE_DOT_PITCH, PAGE_DOTS_Y)
            viewed = index == self._state.viewed
            inner = QColor(tokens.MODE_PAGE_DOT_VIEWED if viewed else tokens.MODE_PAGE_DOT)
            if index == self._state.active:
                painter.setPen(QPen(QColor(tokens.MODE_PAGE_RING), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(centre, 6, 6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(inner)
            painter.drawEllipse(centre, 2.6 if index == self._state.active else 2.8,
                                2.6 if index == self._state.active else 2.8)

    def _text(self, painter: QPainter, rect: QRectF, text: str, *, size: float, colour: str,
              bold: bool = False, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) -> float:
        font = _font(size, bold=bold)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        shown = tr(text)
        painter.drawText(rect, align, shown)
        return QFontMetricsF(font).horizontalAdvance(shown)

    def _paint_knob_words(self, painter: QPainter) -> None:
        rows = self._state.text.knob
        if not rows:
            return
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        self._text(painter, QRectF(KNOB_RIGHT - 160, KNOB_TITLE_Y - 8, 160, 16), "旋钮",
                   size=11, colour=tokens.MODE_LABEL_TITLE, align=right)
        for row, y in zip(rows, KNOB_ROW_YS):
            used = self._text(painter, QRectF(KNOB_RIGHT - 200, y - 10, 200, 20), row.value,
                              size=13.5, colour=tokens.MODE_LABEL_VALUE, bold=True, align=right)
            verb_right = KNOB_RIGHT - used - 8
            used_verb = self._text(painter, QRectF(verb_right - 120, y - 10, 120, 20), row.verb,
                                   size=13.5, colour=tokens.MODE_LABEL_MUTED, align=right)
            icon_right = verb_right - used_verb - 5
            if row.icon == "●":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.MODE_LABEL_MUTED))
                painter.drawEllipse(QPointF(icon_right - 5, y), 4.5, 4.5)
            else:
                self._text(painter, QRectF(icon_right - 20, y - 10, 20, 20), row.icon,
                           size=11, colour=tokens.MODE_LABEL_MUTED, align=right)

    def _paint_right_words(self, painter: QPainter) -> None:
        text = self._state.text
        if text.status_line:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.TEXT_PRIMARY))
            painter.drawRoundedRect(QRectF(STATUS_X, STATUS_CAPTION_Y - 5, 10, 10), 2, 2)
            self._text(painter, QRectF(STATUS_X + 16, STATUS_CAPTION_Y - 8, 200, 16), text.status_caption,
                       size=11, colour=tokens.MODE_LABEL_TITLE)
            self._text(painter, QRectF(STATUS_X, STATUS_CAPTION_Y + 12, 260, 20), text.status_line,
                       size=13.5, colour=tokens.MODE_LABEL_VALUE, bold=True)
        centre = JOYSTICK_CENTRE
        middle = Qt.AlignmentFlag.AlignCenter
        if text.joystick or text.joystick_note:
            self._text(painter, QRectF(centre.x() - 60, JOYSTICK_TITLE_Y - 8, 120, 16), "摇杆",
                       size=11, colour=tokens.MODE_LABEL_TITLE, align=middle)
        if text.joystick_note:
            title, note = text.joystick_note
            self._text(painter, QRectF(centre.x() - 110, centre.y() - 22, 220, 20), title,
                       size=13.5, colour=tokens.MODE_LABEL_VALUE, align=middle)
            self._text(painter, QRectF(centre.x() - 110, centre.y(), 220, 20), note,
                       size=13.5, colour=tokens.MODE_LABEL_MUTED, align=middle)
        elif text.joystick:
            spots = {"up": QPointF(0, -40), "down": QPointF(0, 40), "left": QPointF(-42, 0), "right": QPointF(42, 0)}
            for direction, offset in spots.items():
                value = text.joystick.get(direction, "")
                if value:
                    spot = centre + offset
                    self._text(painter, QRectF(spot.x() - 40, spot.y() - 11, 80, 22), value,
                               size=17 if len(value) == 1 else 12, colour=tokens.MODE_LABEL_VALUE, align=middle)
            painter.setPen(QPen(QColor(tokens.MODE_JOYSTICK_RING), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, 19, 19)
            press = text.joystick.get("press", "")
            if press:
                size = 11.0
                while size > 7 and QFontMetricsF(_font(size)).horizontalAdvance(tr(press)) > 34:
                    size -= 0.5
                self._text(painter, QRectF(centre.x() - 18, centre.y() - 9, 36, 18), press,
                           size=size, colour=tokens.MODE_LABEL_VALUE, align=middle)
        if text.fixed_note:
            title, note = text.fixed_note
            # A 12 line lock before it, the text's colour, 6 apart (单 design ticket 第 5 处).
            paint_lock(painter, QRectF(STATUS_X, FIXED_NOTE_Y - 6, 12, 12), tokens.MODE_LABEL_TITLE)
            self._text(painter, QRectF(STATUS_X + 18, FIXED_NOTE_Y - 8, 220, 16), title,
                       size=11, colour=tokens.MODE_LABEL_TITLE)
            self._text(painter, QRectF(STATUS_X, FIXED_NOTE_Y + 12, 280, 20), note,
                       size=13.5, colour=tokens.MODE_LABEL_VALUE, bold=True)

    def words(self) -> list[str]:
        """Everything written around the device (for tests and accessibility)."""
        text = self._state.text
        found = [word for row in text.knob for word in (row.verb, row.value)]
        if self._card is None:
            found += [text.status_caption, text.status_line, *text.joystick.values()]
            for pair in (text.joystick_note, text.fixed_note):
                if pair:
                    found += list(pair)
        return [word for word in found if word]


def paint_lock(painter: QPainter, box: QRectF, colour: str) -> None:
    """A line lock filling a square box: the body below, the shackle above."""
    unit = box.width() / 12
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(colour), 1.2 * unit))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(box.left() + 1.5 * unit, box.top() + 5 * unit, 9 * unit, 6.5 * unit),
                            1.5 * unit, 1.5 * unit)
    painter.drawArc(QRectF(box.left() + 3 * unit, box.top() + 1 * unit, 6 * unit, 8 * unit), 0, 180 * 16)
    painter.restore()


def paint_floor(painter: QPainter, *, bottom: float = BASE_H) -> None:
    """The dot floor under the device, brightest low in the middle (960×532 units).

    It runs on down to `bottom` (the window's bottom edge, design ticket 第 4 件);
    below the brightest row (y 560) every row is as bright as that one.
    """
    pitch, size = tokens.MODE_FLOOR_PITCH, tokens.MODE_FLOOR_DOT_SIZE
    centre_x, centre_y, reach_x, reach_y = BASE_W / 2, 560.0, 470.0, 370.0
    painter.setPen(Qt.PenStyle.NoPen)
    y = BASE_H - pitch / 2
    while y + pitch < bottom:
        y += pitch
    while y > centre_y - reach_y:
        x = centre_x % pitch
        while x < BASE_W:
            weight = 1 - ((x - centre_x) / reach_x) ** 2 - ((min(y, centre_y) - centre_y) / reach_y) ** 2
            if weight > 0:
                # Faded by alpha, not towards one background colour: the stage lays no floor.
                dot = QColor(tokens.MODE_FLOOR_DOT)
                dot.setAlphaF(min(1.0, weight * 1.6))
                painter.setBrush(dot)
                painter.drawEllipse(QPointF(x, y), size / 2, size / 2)
            x += pitch
        y -= pitch


def paint_battery(painter: QPainter, rect: QRectF, percent: int, *, usb: bool) -> None:
    """34×20: a solid fill as wide as the charge, the number knocked out of it."""
    percent = max(0, min(100, int(percent)))
    low = percent <= tokens.MODE_BATTERY_LOW_FROM
    colour = QColor(tokens.MODE_BATTERY_LOW if low else tokens.MODE_BATTERY)
    radius = 5.0
    shell = QPainterPath()
    shell.addRoundedRect(rect, radius, radius)
    filled = QPainterPath()
    filled.addRect(QRectF(rect.left(), rect.top(), rect.width() * percent / 100, rect.height()))
    filled = shell.intersected(filled)
    rest = shell.subtracted(filled)
    number = QPainterPath()
    font = _font(15, bold=True)
    metrics = QFontMetricsF(font)
    text = str(percent)
    width = metrics.horizontalAdvance(text)
    baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
    number.addText(QPointF(rect.center().x() - width / 2, baseline), font, text)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    track = QColor(tokens.MODE_BATTERY)
    track.setAlphaF(0.28)
    painter.setBrush(track)
    painter.drawPath(rest.subtracted(number))
    painter.setBrush(colour)
    painter.drawPath(filled.subtracted(number))
    painter.setBrush(track)
    painter.drawRoundedRect(QRectF(rect.right() + 2, rect.center().y() - 3.5, 2.5, 7), 1, 1)
    if usb:
        bolt = QPainterPath()
        x, y = rect.right() + 9, rect.top() + 1
        for index, (dx, dy) in enumerate(((5.5, 0), (1, 9.5), (5, 9.5), (2.5, 18), (9, 7), (5, 7), (8.5, 0))):
            (bolt.moveTo if index == 0 else bolt.lineTo)(x + dx, y + dy)
        bolt.closeSubpath()
        painter.setBrush(QColor(tokens.MODE_BATTERY))
        painter.drawPath(bolt)
    painter.restore()


class BatteryIcon(QWidget):
    """The battery on its own (tests and the snapshot tool)."""

    def __init__(self, percent: int, *, usb: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.percent, self.usb = percent, usb
        self.setFixedSize(52, 22)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_battery(painter, QRectF(0, 1, 34, 20), self.percent, usb=self.usb)
        painter.end()


# -- the bottom bar -----------------------------------------------------------------

RIGHT_KINDS = ("synced", "enable", "pending", "saved", "writing", "codex", "offline", "connecting",
               "failed", "readonly")


@dataclass(frozen=True)
class BarState:
    profile_name: str = ""
    profile_active: bool = False   # the device is using the set on show (green radio)
    ble: bool = True               # the device has Bluetooth computers to manage
    ble_button: bool = False       # no device: "蓝牙连接" written out
    bind: bool = False             # nothing mapped yet: "绑定软件快捷键"
    editing_key: bool = False      # the open key has changes: × and "这颗键有改动"
    edit_text: str = "这颗键有改动"   # or "状态灯键有改动" for the six status-light keys
    right: str = "synced"
    text: str = ""                 # the right cell's sentence
    count: int = 0                 # "N 处没保存"
    can_save: bool = False
    dimmed: bool = False           # while writing
    zoomed: bool = False           # the knob / joystick is zoomed: "‹ 回到整台设备" instead of the set (11)


class _Radio(QWidget):
    """Green ring for the set the device is using, a grey dot for the others."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.active = False
        self.scale = 1.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        side = round(18 * self.scale)
        return QSize(side, side)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QPointF(self.width() / 2, self.height() / 2)
        s = self.scale
        if self.active:
            painter.setPen(QPen(QColor(tokens.MODE_PAGE_RING), 1.8 * s))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(centre, 7.5 * s, 7.5 * s)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.TEXT_PRIMARY))
            painter.drawEllipse(centre, 4 * s, 4 * s)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.MODE_PAGE_DOT))
            painter.drawEllipse(centre, 3.5 * s, 3.5 * s)
        painter.end()


class _Columns(QWidget):
    """The three-columns mark after the set's name: it opens all screens."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scale = 1.0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(round(16 * self.scale), round(16 * self.scale))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.scale
        painter.setPen(QPen(QColor(tokens.TEXT_SECONDARY), 1.2 * s))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        top = self.height() / 2 - 5 * s
        for index, height in enumerate((8, 10, 8)):
            x = self.width() / 2 + (index - 1) * 4.5 * s
            painter.drawRoundedRect(QRectF(x - 1.5 * s, top + (10 - height) / 2 * s, 3 * s, height * s),
                                    1.2 * s, 1.2 * s)
        painter.end()


class _BluetoothMark(QWidget):
    def __init__(self, parent: QWidget | None = None, *, colour: str = tokens.TEXT_SECONDARY) -> None:
        super().__init__(parent)
        self.scale = 1.0
        self.colour = colour
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(round(14 * self.scale), round(16 * self.scale))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width() / 14, self.height() / 16)
        painter.translate((self.width() - 14 * s) / 2, (self.height() - 16 * s) / 2)
        painter.scale(s, s)
        pen = QPen(QColor(self.colour), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath(QPointF(3, 4.5))
        for point in ((11, 11.5), (7, 15), (7, 1), (11, 4.5), (3, 11.5)):
            path.lineTo(*point)
        painter.drawPath(path)
        painter.end()


def _mix(a: str, b: str, k: float) -> str:
    """Colour `k` of the way from `a` to `b`, as #rrggbb."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * k), round(ca.green() + (cb.green() - ca.green()) * k),
                  round(ca.blue() + (cb.blue() - ca.blue()) * k)).name()


class _ChipButton(QPushButton):
    """A button whose content is a row of widgets; sized by that row, not by text.

    `fill` (a colour) paints a plain pill instead of the style sheet's look: used while
    the cream 蓝牙连接 button folds into the round one."""

    fill: str | None = None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.fill is None:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.fill))
        body = QRectF(self.rect())
        painter.drawRoundedRect(body, body.height() / 2, body.height() / 2)
        painter.end()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        layout = self.layout()
        hint = layout.totalSizeHint() if layout is not None else super().sizeHint()
        return QSize(hint.width(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return self.sizeHint()


class ModeBottomBar(QFrame):
    """The pill under the device; `set_state(BarState(...))` decides what shows."""

    profile_clicked = Signal()
    ble_clicked = Signal()
    bind_clicked = Signal()
    discard_clicked = Signal()
    save_clicked = Signal()
    enable_clicked = Signal()
    pending_clicked = Signal()
    back_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeBottomBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scale = 1.0
        self._state = BarState()
        row = QHBoxLayout(self)
        self._row = row

        self.discard = QPushButton("×", objectName="modeBarDiscard")
        self.discard.setAccessibleName(tr("放弃这颗键的改动"))
        self.discard.clicked.connect(self.discard_clicked)

        self.profile = _ChipButton(objectName="modeProfileChip")
        self.profile.setAccessibleName(tr("展开看所有屏"))
        chip = QHBoxLayout(self.profile)
        self._chip = chip
        self.radio = _Radio(self.profile)
        self.radio.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.profile_label = QLabel(objectName="modeProfileName")
        self.profile_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.profile_label.setProperty(SKIP_TRANSLATION_PROPERTY, True)  # the user's own name for the set
        self.columns = _Columns(self.profile)
        self.columns.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        chip.addWidget(self.radio)
        chip.addWidget(self.profile_label)
        chip.addWidget(self.columns)
        self.profile.clicked.connect(self.profile_clicked)

        self.back = QPushButton("‹ 回到整台设备", objectName="modeBarBack")
        self.back.clicked.connect(self.back_clicked)

        self.bind = QPushButton("绑定软件快捷键", objectName="startShortcutBinding")
        self.bind.clicked.connect(self.bind_clicked)

        self.ble = _ChipButton(objectName="modeBarBluetooth")
        self.ble.setAccessibleName(tr("蓝牙连接"))
        self.ble.setToolTip(tr("蓝牙连接"))
        ble_row = QHBoxLayout(self.ble)
        self._ble_row = ble_row
        self.ble_mark = _BluetoothMark(self.ble)
        self.ble_text = QLabel("蓝牙连接", objectName="modeBarBluetoothText")
        self.ble_text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ble_row.addWidget(self.ble_mark)
        ble_row.addWidget(self.ble_text)
        self.ble.clicked.connect(self.ble_clicked)

        self.edit_icon = StatusIcon("wait", self, size=16)
        self.edit_icon.setObjectName("modeBarEditIcon")
        self.edit_text = QLabel("这颗键有改动", objectName="modeBarEdit")
        self.status = StatusIcon("ok", self, size=16)
        self.status.setObjectName("modeBarStatusIcon")
        self.status_text = QLabel(objectName="modeBarStatus")
        # 连接设备… → 确认是你的 BORING… → 读取设置… in place of the status while connecting (design decision).
        self.steps = ConnectStepsCell(self)
        self.steps.hide()
        self.steps.width_changed.connect(self._steps_width_changed)
        self._steps_source = None
        self._steps_timer = QTimer(self)
        self._steps_timer.setInterval(40)
        self._steps_timer.timeout.connect(self.refresh_steps)
        # The cream 蓝牙连接 button folds into the round one in place (design decision 动效).
        self._ble_was_button = False
        self._ble_fold_from = 0
        self._ble_fold_c1 = 0.0
        self._ble_fold_clock = QElapsedTimer()
        self._ble_fold_timer = QTimer(self)
        self._ble_fold_timer.setInterval(16)
        self._ble_fold_timer.timeout.connect(self._ble_fold_frame)
        self.pending = QPushButton(objectName="modeBarPending")
        self.pending.clicked.connect(self.pending_clicked)
        self.save = QPushButton("保存到设备", objectName="modeBarSave")
        self.save.clicked.connect(self.save_clicked)
        self.enable = QPushButton("启用到设备", objectName="modeBarEnable")
        self.enable.clicked.connect(self.enable_clicked)

        self._rules = [QFrame(self, objectName="modeBarRule") for _ in range(3)]
        self._order = [self.discard, self.edit_icon, self.edit_text, self._rules[2], self.back, self.profile,
                       self._rules[0], self.bind, self.ble, self._rules[1],
                       self.status, self.status_text, self.steps, self.pending, self.save, self.enable]
        for widget in self._order:
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self._apply_scale()
        self.set_state(BarState())

    def set_scale(self, scale: float) -> None:
        if abs(scale - self._scale) < 1e-4:
            return
        stage = _stage_of(self)
        if stage is not None and stage.resizing() and self.isVisible():
            return   # keeps its size while the window is dragged; ModeStage._settled sets it
        self._scale = scale
        self._apply_scale()

    def _apply_scale(self) -> None:
        s = self._scale

        def px(value: float) -> str:
            return f"{max(1, round(value * s))}px"

        # 8 from the outside edge to every 36 item: the 1 px border (sheet below) is inside the 8.
        # Before, the 8 came on top of the border, the row was 34 and the 36/38 items did not
        # fit: 「Default」 sat lower than its gap above, the steps ring was cut at the bottom.
        edge = 1
        self._row.setContentsMargins(round(10 * s), round(8 * s) - edge, round(10 * s), round(8 * s) - edge)
        self._row.setSpacing(round(10 * s))
        self._chip.setContentsMargins(round(10 * s), 0, round(12 * s), 0)
        self._chip.setSpacing(round(8 * s))
        self._ble_row.setContentsMargins(round(11 * s), 0, round(11 * s), 0)
        self._ble_row.setSpacing(round(6 * s))
        for widget in (self.radio, self.columns, self.ble_mark):
            widget.scale = s
            widget.setFixedSize(widget.sizeHint())
        for rule in self._rules:
            rule.setFixedSize(1, round(20 * s))
        self.status.setFixedSize(round(16 * s), round(16 * s))
        self.edit_icon.setFixedSize(round(16 * s), round(16 * s))
        self.steps.set_scale(s)
        round_side = px(36)
        apply_scaled_sheet(self, f"""
            QFrame#modeBottomBar {{ background: {tokens.MODE_BAR_FILL}; border: 1px solid {tokens.MODE_BAR_EDGE};
                border-radius: {max(1, round(26 * s) - 1)}px; }}
            QFrame#modeBarRule {{ background: {tokens.MODE_BAR_RULE}; border: 0; }}
            QPushButton {{ font-size: {px(14)}; min-height: {round_side}; max-height: {round_side};
                border-radius: {max(1, round(18 * s) - 1)}px; padding: 0 {px(16)}; }}
            QPushButton#modeProfileChip {{ background: {tokens.MODE_BACKGROUND};
                border: 1px solid {tokens.MODE_BAR_EDGE}; padding: 0;
                min-height: {max(1, round(36 * s) - 2)}px; max-height: {max(1, round(36 * s) - 2)}px; }}
            QPushButton#modeProfileChip:hover, QPushButton#modeBarBack:hover {{
                border-color: {tokens.BORDER_CONTROL_HOVER}; }}
            QPushButton#modeBarBack {{ background: {tokens.MODE_BACKGROUND}; border: 1px solid {tokens.MODE_BAR_EDGE};
                color: {tokens.TEXT_PRIMARY}; font-weight: 600; padding: 0 {px(14)};
                min-height: {max(1, round(36 * s) - 2)}px; max-height: {max(1, round(36 * s) - 2)}px; }}
            QLabel#modeProfileName {{ color: {tokens.TEXT_PRIMARY}; font-size: {px(14.5)}; font-weight: 600; }}
            QLabel#modeProfileName:disabled {{ color: {tokens.TEXT_TERTIARY}; }}
            QPushButton#modeBarDiscard, QPushButton#modeBarBluetooth[round="true"] {{
                background: {tokens.MODE_BAR_ROUND}; color: {tokens.TEXT_SECONDARY}; border: 0;
                min-width: {round_side}; max-width: {round_side}; padding: 0; font-size: {px(17)}; }}
            QPushButton#modeBarDiscard:hover, QPushButton#modeBarBluetooth[round="true"]:hover {{
                background: {tokens.SURFACE_3}; color: {tokens.TEXT_PRIMARY}; }}
            QPushButton#modeBarBluetooth[round="false"], QPushButton#startShortcutBinding,
            QPushButton#modeBarEnable {{ background: {tokens.TEXT_PRIMARY}; color: {tokens.WALL_TEXT_ON_CREAM};
                border: 0; font-weight: 700; }}
            QPushButton#modeBarBluetooth[round="false"]:hover, QPushButton#startShortcutBinding:hover,
            QPushButton#modeBarEnable:hover {{ background: {tokens.WHITE}; }}
            QLabel#modeBarBluetoothText {{ color: {tokens.WALL_TEXT_ON_CREAM}; font-size: {px(14)}; font-weight: 700; }}
            QPushButton#modeBarSave {{ background: {tokens.ACCENT}; color: {tokens.WALL_TEXT_ON_CREAM}; border: 0;
                font-weight: 700; }}
            QPushButton#modeBarSave:hover {{ background: {tokens.ACCENT_HOVER}; }}
            QPushButton#modeBarSave:disabled {{ background: {tokens.SURFACE_ACCENT_DISABLED}; color: {tokens.TEXT_TERTIARY}; }}
            QPushButton#modeBarPending {{ background: transparent; border: 0; padding: 0 {px(2)};
                color: {tokens.TEXT_PRIMARY}; font-weight: 600; }}
            QPushButton#modeBarPending:hover {{ color: {tokens.WHITE}; }}
            QLabel#modeBarStatus {{ color: {tokens.TEXT_SECONDARY}; font-size: {px(14)}; }}
            QLabel#modeBarEdit {{ color: {tokens.TEXT_PRIMARY}; font-size: {px(14)}; font-weight: 600; }}
            QLabel#modeBarStatus[strong="true"] {{ color: {tokens.TEXT_PRIMARY}; font-weight: 600; }}
            QWidget[dimmed="true"] {{ color: {tokens.TEXT_TERTIARY}; }}
        """)
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.adjustSize()

    def state(self) -> BarState:
        return self._state

    def set_state(self, state: BarState) -> None:
        self._state = state
        if state.right not in RIGHT_KINDS:
            raise ValueError(f"Unknown bar cell: {state.right}")
        editing = state.editing_key
        # Editing: × · 这颗键有改动 | then the save button, or why it cannot save yet.
        self.discard.setVisible(editing)
        self.edit_icon.setVisible(editing)
        self.edit_text.setVisible(editing)
        self.edit_text.setText(tr(state.edit_text))
        self._rules[2].setVisible(editing)
        zoomed = state.zoomed and not editing
        self.back.setVisible(zoomed)
        self.profile.setVisible(not editing and not zoomed)
        self.radio.active = state.profile_active
        self.radio.update()
        self.profile_label.setText(state.profile_name)
        self.profile_label.setToolTip(state.profile_name)
        self.profile.setToolTip(f"{state.profile_name} · {tr('展开看所有屏')}" if state.profile_name else "")
        self.bind.setVisible(state.bind and not editing and not zoomed)
        ble_shown = state.ble and not editing and not zoomed
        folds = (self._ble_was_button and not state.ble_button and ble_shown and self.ble.isVisible()
                 and not motion.reduces_motion())
        if self._ble_fold_timer.isActive() and (state.ble_button or not ble_shown):
            self._ble_fold_end()
        self.ble.setVisible(ble_shown)
        if folds:
            self._ble_fold_start()
        elif not self._ble_fold_timer.isActive():
            self.ble.setProperty("round", "false" if state.ble_button else "true")
            self.ble_text.setVisible(state.ble_button)
            self.ble_mark.colour = tokens.WALL_TEXT_ON_CREAM if state.ble_button else tokens.TEXT_SECONDARY
            self.ble_mark.update()
            self._repolish(self.ble)
        self._ble_was_button = state.ble_button and ble_shown
        self._rules[0].setVisible(not editing)
        self._rules[1].setVisible(not editing and not zoomed)
        shows_save = state.right in {"pending", "failed"}
        icon, text, strong = self._cell(state)
        if editing and shows_save:
            icon, text = None, ""   # the save button is the whole right side
        steps = self.steps_view(state)
        self.status.setVisible(icon is not None and steps is None)
        if icon is not None:
            kind, waiting_for = icon
            self.status.set_state(kind, waiting_for=waiting_for)
        self.status_text.setText(tr(text))
        self.status_text.setVisible(bool(text) and steps is None)
        self.status_text.setProperty("strong", strong)
        self._repolish(self.status_text)
        self.pending.setVisible(state.count > 0 and state.right in {"pending", "enable"} and not editing)
        self.pending.setText(f"{tr('{count} 处没保存').format(count=state.count)} ›")
        self.save.setVisible(shows_save and state.right != "enable")
        self.save.setText(tr("再试一次") if state.right == "failed" else tr("保存到设备"))
        self.save.setEnabled(state.can_save)
        self.enable.setVisible(state.right == "enable" and not editing)
        self.enable.setEnabled(state.can_save)
        for widget in (self.profile, self.ble, self.profile_label):
            widget.setEnabled(not state.dimmed)
        self.setProperty("dimmed", state.dimmed)
        self._show_steps(steps)
        self.updateGeometry()
        self.adjustSize()
        stage = self.parentWidget()
        if isinstance(stage, ModeStage) and getattr(stage, "bar", None) is self:
            stage._place()

    # -- connecting steps (design decision) --------------------------------------------------

    def set_steps_source(self, source) -> None:
        """`source` is MainViewModel.connection_steps (anything with view(now) and
        needs_frames(now)); None turns the steps off."""
        self._steps_source = source
        self.refresh_steps()

    def steps_view(self, state: BarState | None = None):
        """The StepsView the right cell shows now, or None for the usual cell.
        Running steps only while the bar says "connecting"; "已连接" and the
        orange ring stay a moment after (the bar has moved on by then)."""
        state = state or self._state
        source = self._steps_source
        if source is None or state.editing_key:
            return None
        view = source.view(time.monotonic())
        if view is None or (view.ring == "running" and state.right != "connecting"):
            return None
        return view

    def refresh_steps(self) -> None:
        steps = self.steps_view()
        if (steps is None) != self.steps.isHidden():
            self.set_state(self._state)   # swaps the usual cell and the steps
            return
        self._show_steps(steps)

    # -- 蓝牙连接 folding into the round button ------------------------------------------

    def ble_folding(self) -> bool:
        return self._ble_fold_timer.isActive()

    def _ble_round_side(self) -> int:
        return round(36 * self._scale)

    def _ble_fold_start(self) -> None:
        """Keeps the cream look and narrows from where it is; _ble_fold_end turns it round."""
        self._ble_fold_from = self.ble.width()
        end = self._ble_round_side()
        self._ble_fold_c1 = motion.back_out_for(tokens.CONNECT_STEPS_OVERSHOOT * end,
                                                max(0, self._ble_fold_from - end))
        self.ble_text.setMinimumWidth(0)
        fade = QGraphicsOpacityEffect(self.ble_text)
        self.ble_text.setGraphicsEffect(fade)
        self._ble_fold_clock.start()
        self._ble_fold_timer.start()
        self._ble_fold_frame()

    def _ble_fold_frame(self) -> None:
        t = min(1.0, self._ble_fold_clock.elapsed() / tokens.BLE_FOLD_MS)
        if t >= 1.0:
            self._ble_fold_end()
            return
        end = self._ble_round_side()
        width = self._ble_fold_from + (end - self._ble_fold_from) * motion.back_out(t, self._ble_fold_c1)
        width = max(end - 2, round(width))
        self.ble.setFixedWidth(width)
        margins = self._ble_row.contentsMargins()
        room = width - margins.left() - margins.right() - self.ble_mark.width() - self._ble_row.spacing()
        self.ble_text.setMaximumWidth(max(0, room))
        effect = self.ble_text.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0 - motion.out_cubic(min(1.0, t / tokens.BLE_FOLD_TEXT_SHARE)))
        k = motion.out_cubic(min(1.0, t / tokens.BLE_FOLD_COLOUR_SHARE))
        self.ble.fill = _mix(tokens.TEXT_PRIMARY, tokens.MODE_BAR_ROUND, k)
        self.ble.update()
        self.ble_mark.colour = _mix(tokens.WALL_TEXT_ON_CREAM, tokens.TEXT_SECONDARY, k)
        self.ble_mark.update()
        self._ble_fold_relayout()

    def _ble_fold_end(self) -> None:
        self._ble_fold_timer.stop()
        self.ble.fill = None
        self.ble.setMinimumWidth(0)
        self.ble.setMaximumWidth(16777215)
        self.ble_text.setGraphicsEffect(None)
        self.ble_text.setMaximumWidth(16777215)
        self.ble_text.setVisible(self._state.ble_button)
        self.ble.setProperty("round", "false" if self._state.ble_button else "true")
        self.ble_mark.colour = tokens.WALL_TEXT_ON_CREAM if self._state.ble_button else tokens.TEXT_SECONDARY
        self.ble_mark.update()
        self._repolish(self.ble)
        self._ble_fold_relayout()

    def _ble_fold_relayout(self) -> None:
        self.updateGeometry()
        self.adjustSize()
        stage = self.parentWidget()
        if isinstance(stage, ModeStage) and getattr(stage, "bar", None) is self:
            stage._place()

    def _show_steps(self, steps) -> None:
        source = self._steps_source
        running = source is not None and source.needs_frames(time.monotonic())
        if running and not self._steps_timer.isActive():
            self._steps_timer.start()
        elif not running:
            self._steps_timer.stop()
        if steps is None:
            if not self.steps.isHidden():
                self.steps.hide()
                self.steps.clear()
            return
        if self.steps.isHidden():
            # Grows out of the cell it replaces: the status and its sentence.
            replaced = self.status.width() + self._row.spacing() + self.status_text.sizeHint().width()
            self.steps.show_view(steps, from_width=replaced)
            self.steps.show()
        else:
            self.steps.show_view(steps)

    def _steps_width_changed(self) -> None:
        if self.steps.isHidden():
            return
        self.updateGeometry()
        self.adjustSize()
        stage = self.parentWidget()
        if isinstance(stage, ModeStage) and getattr(stage, "bar", None) is self:
            stage._place()

    @staticmethod
    def _cell(state: BarState) -> tuple[tuple[str, str] | None, str, bool]:
        """(status shape, sentence, strong) for the right cell."""
        return {
            "synced": (("ok", "you"), state.text or "已和设备同步", False),
            "enable": (None, "", False),
            "pending": (("wait", "you"), "", False),
            "saved": (("ok", "you"), state.text or "已保存，去目标软件里试试", True),
            "writing": (("busy", "you"), state.text or "正在写入设备，再读回核对…", True),
            "codex": (("off", "you"), state.text or "双击设备上的旋钮，切到 Codex", True),
            "offline": (("off", "you"), state.text or "没连上设备", True),
            "connecting": (("busy", "you"), state.text or "正在连接设备…", True),
            "failed": (("error", "you"), state.text or "没保存上", True),
            "readonly": (("off", "you"), state.text or "设备只读，不能写入", False),
        }[state.right]

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        hint = super().sizeHint()
        return QSize(hint.width(), bar_height(self._scale))


# -- the cream card -------------------------------------------------------------------

class ModeCard(QFrame):
    """The cream card on the right (Prompt 页定稿 v1 colours); the editor is put inside."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._scale = 1.0
        self.set_scale(1.0, force=True)

    def set_scale(self, scale: float, *, force: bool = False) -> None:
        if not force and abs(scale - self._scale) < 1e-4:
            return
        self._scale = scale
        apply_scaled_sheet(self, card_sheet(scale))

    def scale(self) -> float:
        return self._scale

    def wanted_height(self, width: int) -> int:
        """Height for everything at this width, wrapped text included (the body counted apart:
        its size policy is Ignored, so the layout leaves it out)."""
        layout = self.layout()
        wrapped = layout.heightForWidth(width) if layout is not None and layout.hasHeightForWidth() else -1
        body = self.findChild(QScrollArea, "mappingEditorBody")
        extra = 0
        if body is not None and body.widget() is not None:
            inner = body.widget()
            extra = inner.heightForWidth(width) if inner.hasHeightForWidth() else inner.sizeHint().height()
            extra = max(extra, inner.sizeHint().height()) + round(6 * self._scale)
        return max(self.sizeHint().height(), wrapped + extra if wrapped >= 0 else 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        """Tall enough for everything in it: the editor body scrolls only when the window is short."""
        hint = super().sizeHint()
        body = self.findChild(QScrollArea, "mappingEditorBody")
        if body is not None and body.widget() is not None:
            # The body's size policy is Ignored, so the layout counted nothing for it.
            hint.setHeight(hint.height() + body.widget().sizeHint().height() + round(6 * self._scale))
        return hint


def card_sheet(s: float) -> str:
    def px(value: float) -> str:
        return f"{max(1, round(value * s))}px"

    ink, muted, well = tokens.MODE_CARD_TEXT, tokens.MODE_CARD_MUTED, tokens.MODE_CARD_WELL
    return f"""
        QFrame#mappingEditorCard {{ background: {tokens.MODE_CARD}; border: 0; border-radius: {px(16)}; }}
        QFrame#mappingEditorCard QWidget {{ color: {ink}; font-size: {px(13)}; background: transparent; }}
        QFrame#mappingEditorCard QLabel#inspectorTitle {{ font-size: {px(15)}; font-weight: 700; }}
        QFrame#mappingEditorCard QLabel#modeCardCurrent, QFrame#mappingEditorCard QLabel[muted="true"] {{
            color: {muted}; font-size: {px(12)}; }}
        QFrame#mappingEditorCard QPushButton {{ background: transparent; border: 1px solid {tokens.MODE_CARD_EDGE};
            border-radius: {px(15)}; min-height: {px(34)}; max-height: {px(34)}; padding: 0 {px(14)}; font-weight: 600; }}
        QFrame#mappingEditorCard QPushButton:hover {{ background: {well}; }}
        QFrame#mappingEditorCard QPushButton:disabled {{ color: {muted}; }}
        QFrame#mappingEditorCard QPushButton#shortcutRecordButton {{ background: {ink}; color: {tokens.MODE_CARD};
            border: 0; }}
        QFrame#mappingEditorCard QPushButton#shortcutRecordButton:hover {{ background: {tokens.MODE_CARD_INK_HOVER}; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose {{ border: 0; border-radius: {px(7)};
            min-height: 0; max-height: none; padding: {px(5)} {px(8)}; font-size: {px(12)};
            color: {tokens.MODE_CARD_SEGMENT_TEXT}; font-weight: 500; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose[fit="1"] {{ font-size: {px(12 * 0.88)}; padding: {px(5)} {px(3)}; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose[fit="2"] {{ font-size: {px(12 * 0.78)}; padding: {px(5)} {px(2)}; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose[fit="3"] {{ font-size: {px(12 * 0.7)}; padding: {px(5)} {px(1)}; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose:checked {{ background: {ink}; color: {tokens.MODE_CARD};
            font-weight: 600; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose:hover:!checked {{ background: transparent; }}
        QFrame#mappingEditorCard QPushButton#modeCardPurpose:disabled {{ color: {tokens.MODE_CARD_DISABLED}; }}
        QFrame#mappingEditorCard QFrame#modeCardPurposes {{ background: {tokens.MODE_CARD_SEGMENT};
            border-radius: {px(10)}; padding: {px(3)}; }}
        QFrame#mappingEditorCard QLabel#shortcutRecorderPreview {{ font-size: {px(20)}; font-weight: 600; }}
        QFrame#mappingEditorCard QScrollBar:vertical {{ width: {px(4)}; background: transparent; }}
        QFrame#mappingEditorCard QScrollBar::handle:vertical {{ background: {tokens.MODE_CARD_EDGE}; border-radius: {px(2)}; }}
        QFrame#mappingEditorCard QScrollBar::add-line, QFrame#mappingEditorCard QScrollBar::sub-line {{ height: 0; }}
        QFrame#mappingEditorCard QWidget#voiceInputSetup {{ background: {well}; border-radius: {px(12)}; }}
        QFrame#mappingEditorCard QLabel#modeVoiceSummary {{ font-size: {px(16)}; font-weight: 700; }}
        QFrame#mappingEditorCard QPushButton#voiceConnectApp {{ background: {ink}; color: {tokens.MODE_CARD}; border: 0; }}
        QFrame#mappingEditorCard QPushButton#voiceConnectApp:hover {{ background: {tokens.MODE_CARD_INK_HOVER}; }}
        QFrame#mappingEditorCard QPushButton[modeLink="true"] {{ border: 0; background: transparent; padding: 0;
            min-height: {px(18)}; max-height: {px(18)}; color: {muted}; font-size: {px(12)}; font-weight: 500; }}
        QFrame#mappingEditorCard QPushButton[modeLink="true"]:hover {{ color: {ink}; background: transparent; }}
        QFrame#mappingEditorCard QFrame#normalAgentUnread {{ background: {tokens.MODE_CARD_ALERT}; border-radius: {px(12)}; }}
        QFrame#mappingEditorCard QLabel#normalAgentSharedTitle {{ font-weight: 700; }}
        QFrame#mappingEditorCard QFrame#shortcutRecorder {{ background: {well}; border: 0; border-radius: {px(12)}; }}
        QFrame#mappingEditorCard QLineEdit {{ background: {well}; border: 0; border-radius: {px(10)};
            min-height: {px(34)}; padding: 0 {px(12)}; color: {ink}; selection-background-color: {tokens.ACCENT}; }}
        QFrame#mappingEditorCard QComboBox {{ background: {well}; border: 0; border-radius: {px(10)};
            min-height: {px(32)}; min-width: 0; padding: 0 {px(10)}; color: {ink}; }}
        QFrame#mappingEditorCard QComboBox QAbstractItemView {{ background: {tokens.MODE_CARD}; color: {ink};
            selection-background-color: {well}; selection-color: {ink}; }}
        QFrame#mappingEditorCard QScrollArea, QFrame#mappingEditorCard QScrollArea > QWidget > QWidget {{
            background: transparent; border: 0; }}
        QFrame#mappingEditorCard QCheckBox {{ color: {ink}; }}
    """


class PurposeSegment(QPushButton):
    """One of the card's three purpose segments: the text steps down a size or two
    when a language's word is longer than the segment (sizes in card_sheet)."""

    STEPS = (1.0, 0.88, 0.78, 0.7)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("modeCardPurpose")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setProperty("fit", 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        if self.property("fit") == 0 and self.font().pixelSize() > 0:
            self._base_size = self.font().pixelSize()   # the size the card sheet gives at full width
        base = self._base_size or 12
        room = self.width() - 18   # the sheet pads each side by up to 8
        level = len(self.STEPS) - 1
        for index, share in enumerate(self.STEPS):
            font = QFont(self.font())
            font.setPixelSize(max(1, round(base * share)))
            if QFontMetricsF(font).horizontalAdvance(self.text()) <= room:
                level = index
                break
        if self.property("fit") != level:
            self.setProperty("fit", level)
            self.style().unpolish(self)
            self.style().polish(self)

    _base_size: int = 0


class ModeKeyGlyph(QWidget):
    """The key number in BORING 5R dots at the top left of the card (12 → ⠿⠿)."""

    SCALE = 3.4     # dot 6.8, pitch 10.2: the draft's "12"

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeKeyGlyph")
        self._text = text
        self.setAccessibleName(text)
        self._scale = 1.0
        self._fit()

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self._fit()

    def glyph_scale(self) -> float:
        """Two letters or digits at full size; more (CCW) shrink to the width of two,
        so the title beside them keeps its last character (design decision, -06 第 5 件)."""
        full = self.SCALE * self._scale
        if len(self._text) <= len(tokens.MODE_KEY_GLYPH_FIT):
            return full
        room = layout_text(tokens.MODE_KEY_GLYPH_FIT, scale=full).width
        return full * min(1.0, room / max(1e-6, layout_text(self._text, scale=full).width))

    def _fit(self) -> None:
        full = layout_text(self._text[:2] or " ", scale=self.SCALE * self._scale)
        layout = layout_text(self._text, scale=self.glyph_scale())
        self.setFixedSize(round(layout.width) + 1, round(max(layout.height, full.height)) + 1)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        scale = self.glyph_scale()
        top = (self.height() - 1 - layout_text(self._text, scale=scale).height) / 2   # level with the others
        draw_qpainter(painter, self._text, 0, top, scale=scale, color=tokens.MODE_CARD_TEXT)
        painter.end()


class ModeKeySwatch(QWidget):
    """The chosen key, small: its cap with the orange selection ring."""

    def __init__(self, parent: QWidget | None = None, *, colour: str = tokens.TOP_VIEW_KEY_CAP) -> None:
        super().__init__(parent)
        self._colour = colour
        self.setFixedSize(18, 18)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(tokens.TOP_VIEW_SELECTED), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(1, 1, 16, 16), 4, 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._colour))
        painter.drawRoundedRect(QRectF(4, 4, 10, 10), 2, 2)
        painter.end()


# -- AI 额度 (07 / 08) -----------------------------------------------------------------

@dataclass(frozen=True)
class QuotaWindow:
    label: str                  # "5h" / "week"
    remaining: float | None     # percent left; None when it could not be read
    reset: str = ""             # short reset time: "00:20" today, "11-21" later


@dataclass(frozen=True)
class QuotaProvider:
    name: str                   # "Codex" / "Claude", under the small ring
    word: str                   # "CODEX" / "CLAUDE" in dots when open
    plan: str = ""              # "PRO" / "MAX" / "ULTRA"
    windows: tuple[QuotaWindow, ...] = ()   # 5h first when there is one, then week
    shown: int = 0              # which window the small ring shows (the setting, or week without 5h)


QUOTA_OPEN_MS = tokens.MOTION_LONG_MS      # 长 (design decision; was 480)
QUOTA_CLOSE_MS = tokens.MOTION_MEDIUM_MS   # 中 (design decision; was 300)
QUOTA_WIDTH = 170
QUOTA_HEIGHT = 300


def _phase(p: float, start: float, end: float) -> float:
    return min(1.0, max(0.0, (p - start) / (end - start)))


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3     # quick start, slow finish


class ModeQuota(QWidget):
    """Top left: one small ring per AI app; a click grows it in place to both windows.

    Drawn in the page's 960×532 units like the rest of the stage; `set_scale`
    follows the stage. Timing per 07-AI额度-点开原地长大 (0.48 s open, 0.30 s close).
    """

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None, *, reduce_motion: bool | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeQuota")
        self.setAccessibleName(tr("AI 额度"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._providers: tuple[QuotaProvider, ...] = ()
        self._updated = ""
        self._scale = 1.0
        self._open = False
        self._progress = 0.0
        self._reduce_motion = system_reduces_motion() if reduce_motion is None else reduce_motion
        self._animation = QVariantAnimation(self)
        self._animation.valueChanged.connect(self._set_progress)

    # -- state -----------------------------------------------------------------------

    def set_data(self, providers: tuple[QuotaProvider, ...], updated: str = "") -> None:
        self._providers = tuple(providers)
        self._updated = updated
        self.update()

    def providers(self) -> tuple[QuotaProvider, ...]:
        return self._providers

    def is_open(self) -> bool:
        return self._open

    def progress(self) -> float:
        return self._progress

    def set_open(self, open_: bool, *, animate: bool = True) -> None:
        if open_ == self._open:
            return
        self._open = open_
        self._animation.stop()
        target = 1.0 if open_ else 0.0
        if not animate or self._reduce_motion:
            self._set_progress(target)
        else:
            start = self._progress   # setting the values can move the old animation's value
            span = QUOTA_OPEN_MS if open_ else QUOTA_CLOSE_MS
            self._animation.setDuration(max(1, round(span * abs(target - start))))
            self._animation.setCurrentTime(0)
            self._animation.setStartValue(start)
            self._animation.setEndValue(target)
            self._animation.start()
        self.toggled.emit(open_)

    def toggle(self) -> None:
        self.set_open(not self._open)

    def _set_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        self.update()

    # -- geometry (widget units = page units × scale) ------------------------------------

    def _collapsed_centre(self, index: int) -> QPointF:
        return QPointF(18 + 50 * index, 38)

    def _open_centre(self, index: int, window: int) -> QPointF:
        return QPointF(24 + 62 * window, 74 + 122 * index)

    def content_rect(self) -> QRectF:
        """What a click on counts as a click on the quota (page units)."""
        count = max(1, len(self._providers))
        closed = QRectF(0, 0, 18 + 50 * (count - 1) + 30, 96)
        opened = QRectF(0, 0, QUOTA_WIDTH, 21 + 122 * count + 16)
        return opened if self._progress > 0.5 else closed

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        point = QPointF(event.position().x() / self._scale - QUOTA_PAD, event.position().y() / self._scale)
        if event.button() == Qt.MouseButton.LeftButton and self.content_rect().contains(point):
            self.toggle()
            event.accept()
            return
        event.ignore()   # the stage behind takes it (closes the card / the quota)

    # -- painting --------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self._scale, self._scale)
        painter.translate(QUOTA_PAD, 0)
        p = self._progress
        self._label(painter, QRectF(0, 0, 160, 12), "AI 额度", 11, tokens.TEXT_SECONDARY, spacing=1.0)
        grow = _ease(_phase(p, 0.0, 0.75))
        for index, provider in enumerate(self._providers):
            if not provider.windows:
                continue
            shown = provider.windows[min(provider.shown, len(provider.windows) - 1)]
            first_open = provider.windows[0]
            closed, opened = self._collapsed_centre(index), self._open_centre(index, 0)
            centre = closed + (opened - closed) * grow
            radius = 18 + 6 * grow
            # The small ring shows the chosen window; open, the first ring is always the first window.
            window = shown if grow < 0.5 else first_open
            dots = 16 if grow < 0.5 else 20
            self._ring(painter, centre, radius, dots, window.remaining, number_px=12 + 3 * grow)
            names = 1 - _phase(p, 0.0, 0.25)
            if names > 0:
                painter.setOpacity(names)
                self._label(painter, QRectF(centre.x() - 30, 58, 60, 16), provider.name, 13,
                            tokens.TEXT_SECONDARY_ALT, align=Qt.AlignmentFlag.AlignCenter)
                self._label(painter, QRectF(centre.x() - 30, 73, 60, 14), shown.label, 10.5,
                            tokens.TEXT_TERTIARY, align=Qt.AlignmentFlag.AlignCenter)
                painter.setOpacity(1.0)
            if p <= 0:
                continue
            for number, extra in enumerate(provider.windows[1:], start=1):
                slide = _ease(_phase(p, 0.25, 0.854))
                if slide > 0:
                    target = self._open_centre(index, number)
                    position = centre + (target - centre) * slide
                    painter.setOpacity(slide)
                    self._ring(painter, position, 24 * (0.4 + 0.6 * slide), 20, extra.remaining,
                               number_px=15 * (0.4 + 0.6 * slide))
                    painter.setOpacity(1.0)
            reveal = _phase(p, 0.29, 0.79)
            if reveal > 0:
                top = 21 + 122 * index
                layout = layout_text(provider.word, scale=1.5)
                painter.save()
                painter.setClipRect(QRectF(0, top - 2, layout.width * reveal + 1, layout.height + 4))
                draw_qpainter(painter, provider.word, 0, top, scale=1.5, color=tokens.TEXT_PRIMARY)
                painter.restore()
                if provider.plan:
                    painter.setOpacity(_phase(p, 0.5, 0.79))
                    self._label(painter, QRectF(layout.width + 6, top + layout.height - 11, 60, 11),
                                provider.plan, 9, tokens.TEXT_SECONDARY, spacing=0.5)
                    painter.setOpacity(1.0)
            rise = _phase(p, 0.6, 1.0)
            if rise > 0:
                painter.setOpacity(rise)
                lift = 4 * (1 - rise)
                for number, item in enumerate(provider.windows):
                    spot = self._open_centre(index, number)
                    self._label(painter, QRectF(spot.x() - 30, spot.y() + 29 + lift, 60, 15), item.label,
                                12.5, tokens.TEXT_PRIMARY, align=Qt.AlignmentFlag.AlignCenter)
                    if item.reset:
                        self._label(painter, QRectF(spot.x() - 30, spot.y() + 46 + lift, 60, 13), item.reset,
                                    10.5, tokens.TEXT_TERTIARY, align=Qt.AlignmentFlag.AlignCenter)
                painter.setOpacity(1.0)
        if self._updated and p > 0.85:
            painter.setOpacity(_phase(p, 0.85, 1.0))
            top = 21 + 122 * len(self._providers) + 1
            self._label(painter, QRectF(0, top, 160, 13), self._updated, 10.5, tokens.TEXT_TERTIARY)
            painter.setOpacity(1.0)
        painter.end()

    def _label(self, painter: QPainter, rect: QRectF, text: str, size: float, colour: str, *,
               align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, spacing: float = 0.0) -> None:
        font = _font(size)
        if spacing:
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        painter.drawText(rect, align, tr(text))

    def _ring(self, painter: QPainter, centre: QPointF, radius: float, dots: int,
              remaining: float | None, *, number_px: float) -> None:
        """A quota ring of dots (进度条点阵样式 v1): lit clockwise from the top up to what is left."""
        dot = radius * (4.0 / 24 if dots == 20 else 3.6 / 18)
        lit = 0 if remaining is None else round(max(0.0, min(100.0, remaining)) / 100 * dots)
        low = remaining is not None and remaining <= tokens.QUOTA_LOW_PERCENT
        lit_colour = QColor(tokens.PROGRESS_RING_LOW if low else tokens.PROGRESS_RING_LIT)
        unlit_colour = QColor(tokens.PROGRESS_RING_UNLIT)
        orbit = radius - dot / 2 - 0.5
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(dots):
            angle = 2 * math.pi * index / dots
            painter.setBrush(lit_colour if index < lit else unlit_colour)
            painter.drawEllipse(QPointF(centre.x() + orbit * math.sin(angle), centre.y() - orbit * math.cos(angle)),
                                dot / 2, dot / 2)
        text = "—" if remaining is None else str(round(remaining))
        font = _font(number_px, bold=True)
        painter.setFont(font)
        painter.setPen(QColor(tokens.TEXT_PRIMARY))
        painter.drawText(QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2),
                         Qt.AlignmentFlag.AlignCenter, text)


# -- 展开看所有屏 (10) ------------------------------------------------------------------

@dataclass(frozen=True)
class OverviewScreen:
    key: tuple                  # ("normal", profile id) or ("codex", None)
    name: str
    note: str                   # 设备正在用这一套 / 设备没在用 / 在设备上双击旋钮切换
    active: bool = False        # the device is using it: green edge, green ring
    codex: bool = False         # fixed keys: a lock, no menu, no 启用到设备
    can_enable: bool = False
    labels: dict = field(default_factory=dict)
    unset: frozenset = frozenset()


OVERVIEW_CARD = QSize(204, 315)     # one screen, 960×532 units (10, measured)
OVERVIEW_GAP = 20
OVERVIEW_TOP = 89
OVERVIEW_THUMB = 160                # thumbnail body width
OVERVIEW_BAR_Y = 492                # bottom pill, centre


class _OverviewCard(QFrame):
    """One screen in 全部方案: the device, its name and what the device does with it."""

    clicked = Signal()

    def __init__(self, screen: OverviewScreen | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.screen = screen
        self.setObjectName("modeOverviewCard" if screen is not None else "modeOverviewNew")
        self.setProperty("active", bool(screen and screen.active))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        box = QVBoxLayout(self)
        self._box = box
        self.thumb: ModeDeviceView | None = None
        self.enable: QPushButton | None = None
        self.more: QPushButton | None = None
        if screen is None:
            box.addStretch(1)
            plus = QLabel("+", objectName="modeOverviewPlus")
            plus.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(plus)
            title = QLabel("新建一屏", objectName="modeOverviewName")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(title)
            note = QLabel("空白 · 另存当前 · AI 模板", objectName="modeOverviewNote")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(note)
            box.addStretch(1)
            for label in (plus, title, note):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            return
        self.thumb = ModeDeviceView(self)
        self.thumb.labels_from = 120
        self.thumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.thumb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.thumb.set_labels(screen.labels, unset=screen.unset)
        box.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        name_row.addStretch(1)
        self.radio = _Radio(self)
        self.radio.active = screen.active
        self.radio.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        name_row.addWidget(self.radio)
        name = QLabel(screen.name, objectName="modeOverviewName")
        name.setProperty(SKIP_TRANSLATION_PROPERTY, not screen.codex)   # the user's own name
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        name_row.addWidget(name)
        if screen.codex:
            name_row.addWidget(_LockMark(self))
        name_row.addStretch(1)
        box.addLayout(name_row)
        note = QLabel(screen.note, objectName="modeOverviewNote")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note.setWordWrap(True)
        note.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        box.addWidget(note)
        box.addStretch(1)
        actions = QHBoxLayout()
        if screen.can_enable:
            self.enable = QPushButton("启用到设备", objectName="modeOverviewEnable")
            actions.addWidget(self.enable)
        actions.addStretch(1)
        if not screen.codex:
            self.more = QPushButton("···", objectName="modeOverviewMore")
            self.more.setAccessibleName(tr("更多"))
            actions.addWidget(self.more)
        box.addLayout(actions)

    def set_scale(self, s: float) -> None:
        self.setFixedSize(round(OVERVIEW_CARD.width() * s), round(OVERVIEW_CARD.height() * s))
        self._box.setContentsMargins(round(14 * s), round(18 * s), round(14 * s), round(14 * s))
        self._box.setSpacing(round(6 * s))
        if self.thumb is not None:
            side = round(OVERVIEW_THUMB * 1.1 * s)
            self.thumb.setFixedSize(side, side)
            self.radio.scale = s
            self.radio.setFixedSize(self.radio.sizeHint())

    @property
    def movable(self) -> bool:
        """Only the user's own screens move; Codex stays last and 新建一屏 stays at the end."""
        return self.screen is not None and not self.screen.codex

    def _host(self) -> "ModeOverview | None":
        widget = self.parentWidget()
        while widget is not None and not isinstance(widget, ModeOverview):
            widget = widget.parentWidget()
        return widget

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_at = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        start = getattr(self, "_pressed_at", None)
        host = self._host()
        if start is None or host is None or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        point = event.globalPosition().toPoint()
        if host.dragging(self):
            host.drag_to(point)
        elif ((point - start).manhattanLength() >= QApplication.startDragDistance()
              and host.begin_drag(self, start)):
            host.drag_to(point)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() != Qt.MouseButton.LeftButton or getattr(self, "_pressed_at", None) is None:
            super().mouseReleaseEvent(event)
            return
        self._pressed_at = None
        host = self._host()
        event.accept()
        if host is not None and host.dragging(self):
            host.drop()
        elif self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().paintEvent(event)
        if self.screen is not None:
            return
        painter = QPainter(self)   # the 新建一屏 card: a dashed outline
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(tokens.MODE_BAR_RULE), 1.2)
        pen.setDashPattern([4, 4])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        radius = 18 * self.width() / OVERVIEW_CARD.width()
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)
        painter.end()


class _LockMark(QWidget):
    """The small lock after "Codex": its keys are fixed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 14)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        paint_lock(painter, QRectF(0, 1, 12, 12), tokens.TEXT_SECONDARY)
        painter.end()


def _rgba(value: str) -> tuple[int, int, int, int]:
    """A token's CSS colour string (four numbers in it) as a tuple QColor(*…) takes."""
    red, green, blue, alpha = (int(number) for number in re.findall(r"\d+", value))
    return red, green, blue, alpha


class _LiftedCard(QWidget):
    """The picked-up card in 全部方案: a picture of it, a little larger, its outline one step brighter.

    `lift` runs 0 → 1 as it is picked up and back to 0 as it is put down; the card
    itself stays in the row, invisible, so its place reads as empty. No shadow
    (design decision): the outline goes from the card's own MODE_BAR_EDGE to the wall's
    "open tile" step, OVERVIEW_LIFT_EDGE. The card the device is using keeps its
    green ring, which is already the brightest line on the page.
    """

    def __init__(self, picture: QPixmap, size: QSize, parent: QWidget, *, active: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("modeOverviewLifted")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._picture = picture
        self._card = size
        self._active = active
        self.lift = 0.0
        grown = tokens.OVERVIEW_LIFT_SCALE
        self.resize(math.ceil(size.width() * grown), math.ceil(size.height() * grown))
        self.set_lift(0.0)

    def scale(self) -> float:
        return 1.0 + (tokens.OVERVIEW_LIFT_SCALE - 1.0) * self.lift

    def edge(self) -> QColor | None:
        """The outline drawn over the picture now, or None for the active card."""
        if self._active:
            return None
        rest = _rgba(tokens.MODE_BAR_EDGE)   # QColor() does not read the CSS form
        lit = tokens.OVERVIEW_LIFT_EDGE
        return QColor(*(round(a + (b - a) * self.lift) for a, b in zip(rest, lit)))

    def set_lift(self, value: float) -> None:
        self.lift = min(1.0, max(0.0, value))
        self.update()

    def place(self, card_top_left: QPointF) -> None:
        """Put it so that an unscaled card would have its top left here."""
        extra = QPointF((self.width() - self._card.width()) / 2, (self.height() - self._card.height()) / 2)
        self.move((card_top_left - extra).toPoint())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.scale()
        width, height = self._card.width() * scale, self._card.height() * scale
        body = QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)
        painter.drawPixmap(body, self._picture, QRectF(self._picture.rect()))
        edge = self.edge()
        if edge is not None:
            # Over the card's own 1 px outline, same corners (QFrame#modeOverviewCard).
            radius = 18 * width / OVERVIEW_CARD.width()
            painter.setPen(QPen(edge, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        painter.end()


@dataclass
class _Drag:
    card: "_OverviewCard"
    slots: list[QPoint]            # where each movable place is, in the row (left to right)
    others: list["_OverviewCard"]  # the other movable cards, in their order
    start: int                     # the place it was picked up from
    target: int                    # the place it would go now
    grab: QPointF                  # where in the card it is held
    ghost: _LiftedCard
    cursor: QPoint = field(default_factory=QPoint)
    dropping: bool = False


class ModeOverview(QWidget):
    """全部方案: every screen side by side, opened from the set's name on the bar (10).

    A click on a screen goes into it; 启用到设备 and the ··· menu (重命名、另存为、删除)
    act on that screen; the dashed card makes a new one. 导入… / 全部导出… / 完成 below.
    """

    screen_chosen = Signal(object)
    enable_requested = Signal(object)
    menu_requested = Signal(object, object)      # key, the ··· button
    new_requested = Signal(object)               # the new card
    import_requested = Signal()
    export_requested = Signal()
    discard_requested = Signal()
    done_requested = Signal()
    order_changed = Signal(list)                 # the screens' keys in their new order (design decision)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modeOverview")
        self._scale = 1.0
        self._connected = False
        self._can_reorder = False
        self._drag: _Drag | None = None
        self._deferred: tuple | None = None      # set_screens while a card is held: applied after the drop
        self._slides: dict[int, QVariantAnimation] = {}
        self._lift_animation: QVariantAnimation | None = None
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(FRAME_MS)
        self._autoscroll.timeout.connect(self._autoscroll_tick)
        self.title = QLabel("全部方案", self, objectName="modeOverviewTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle = QLabel("点一屏进去改", self, objectName="modeOverviewSubtitle")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea(self, objectName="modeOverviewScroll")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidgetResizable(False)
        self.row = QWidget(objectName="modeOverviewRow")
        self._row = QHBoxLayout(self.row)
        self._row.setContentsMargins(0, 0, 0, 0)
        self.scroll.setWidget(self.row)
        self.cards: list[_OverviewCard] = []
        self.bar = QFrame(self, objectName="modeBottomBar")
        self.bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar_row = QHBoxLayout(self.bar)
        self._bar_row = bar_row
        self.import_button = QPushButton("导入…", objectName="modeOverviewImport")
        self.export_button = QPushButton("全部导出…", objectName="modeOverviewExport")
        self.discard_button = QPushButton("放弃全部改动…", objectName="modeOverviewDiscard")
        self.rule = QFrame(objectName="modeBarRule")
        self.done = QPushButton("完成", objectName="modeOverviewDone")
        for widget in (self.import_button, self.export_button, self.discard_button, self.rule, self.done):
            bar_row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        self.import_button.clicked.connect(self.import_requested)
        self.export_button.clicked.connect(self.export_requested)
        self.discard_button.clicked.connect(self.discard_requested)
        self.done.clicked.connect(self.done_requested)
        self.discard_button.hide()

    def set_screens(self, screens: list[OverviewScreen], *, can_add: bool, connected: bool,
                    dirty: bool = False, can_reorder: bool = True) -> None:
        if self._drag is not None:
            # Rebuilding now would pull the held card out from under the pointer.
            self._deferred = (screens, can_add, connected, dirty, can_reorder)
            return
        self._deferred = None
        self._connected = connected
        self._can_reorder = can_reorder
        for card in self.cards:
            self._row.removeWidget(card)
            card.deleteLater()
        self.cards = []
        for screen in screens:
            card = _OverviewCard(screen, self.row)
            card.clicked.connect(lambda key=screen.key: self.screen_chosen.emit(key))
            if card.enable is not None:
                card.enable.clicked.connect(lambda _checked=False, key=screen.key: self.enable_requested.emit(key))
            if card.more is not None:
                card.more.clicked.connect(lambda _checked=False, key=screen.key, button=card.more:
                                          self.menu_requested.emit(key, button))
            self.cards.append(card)
            self._row.addWidget(card)
        if can_add:
            new = _OverviewCard(None, self.row)
            new.clicked.connect(lambda card=new: self.new_requested.emit(card))
            self.cards.append(new)
            self._row.addWidget(new)
        for card in self.cards:
            # Shown now, not on the layout's queued show: a hidden card counts as zero
            # width, and a refresh while 全部方案 is open would size the row to nothing.
            card.show()
        self.discard_button.setVisible(dirty)
        self.set_scale(self._scale, force=True)
        self.update()

    def set_scale(self, s: float, *, force: bool = False) -> None:
        if not force and abs(s - self._scale) < 1e-4:
            self._place()
            return
        self._cancel_drag()   # the places it was measured against are gone
        self._scale = s

        def px(value: float) -> str:
            return f"{max(1, round(value * s))}px"

        for card in self.cards:
            card.set_scale(s)
        self._row.setSpacing(round(OVERVIEW_GAP * s))
        self.row.adjustSize()
        self._bar_row.setContentsMargins(round(10 * s), round(8 * s), round(10 * s), round(8 * s))
        self._bar_row.setSpacing(round(10 * s))
        self.rule.setFixedSize(1, round(20 * s))
        apply_scaled_sheet(self, f"""
            QLabel#modeOverviewTitle {{ color: {tokens.TEXT_PRIMARY}; font-size: {px(19)}; font-weight: 700; }}
            QLabel#modeOverviewSubtitle {{ color: {tokens.TEXT_SECONDARY}; font-size: {px(13)}; }}
            QScrollArea#modeOverviewScroll, QWidget#modeOverviewRow {{ background: transparent; border: 0; }}
            QFrame#modeOverviewCard {{ background: {tokens.MODE_BAR_FILL}; border: 1px solid {tokens.MODE_BAR_EDGE};
                border-radius: {px(18)}; }}
            QFrame#modeOverviewCard[active="true"] {{ border: {px(1.5)} solid {tokens.MODE_PAGE_RING}; }}
            QFrame#modeOverviewNew {{ background: transparent; border: 0; }}
            QLabel#modeOverviewName {{ color: {tokens.TEXT_PRIMARY}; font-size: {px(16)}; font-weight: 600; }}
            QLabel#modeOverviewNote {{ color: {tokens.TEXT_SECONDARY}; font-size: {px(12.5)}; }}
            QLabel#modeOverviewPlus {{ color: {tokens.TEXT_SECONDARY}; font-size: {px(28)}; font-weight: 300; }}
            QPushButton#modeOverviewEnable, QPushButton#modeOverviewDone {{ background: {tokens.TEXT_PRIMARY};
                color: {tokens.WALL_TEXT_ON_CREAM}; border: 0; font-weight: 700; font-size: {px(13.5)};
                min-height: {px(30)}; max-height: {px(30)}; border-radius: {max(1, round(15 * s) - 1)}px;
                padding: 0 {px(14)}; }}
            QPushButton#modeOverviewEnable:hover, QPushButton#modeOverviewDone:hover {{ background: {tokens.WHITE}; }}
            QPushButton#modeOverviewMore {{ background: transparent; color: {tokens.TEXT_SECONDARY};
                border: 1px solid {tokens.MODE_BAR_EDGE}; min-width: {px(30)}; max-width: {px(30)};
                min-height: {px(30)}; max-height: {px(30)}; border-radius: {max(1, round(15 * s) - 1)}px;
                padding: 0; font-size: {px(12)}; }}
            QPushButton#modeOverviewMore:hover {{ color: {tokens.TEXT_PRIMARY}; border-color: {tokens.BORDER_CONTROL_HOVER}; }}
            QFrame#modeBottomBar {{ background: {tokens.MODE_BAR_FILL}; border: 1px solid {tokens.MODE_BAR_EDGE};
                border-radius: {max(1, round(26 * s) - 1)}px; }}
            QFrame#modeBarRule {{ background: {tokens.MODE_BAR_RULE}; border: 0; }}
            QPushButton#modeOverviewImport, QPushButton#modeOverviewExport, QPushButton#modeOverviewDiscard {{
                background: transparent; color: {tokens.TEXT_PRIMARY}; border: 1px solid {tokens.MODE_BAR_EDGE};
                font-size: {px(14)}; min-height: {px(36)}; max-height: {px(36)};
                border-radius: {max(1, round(18 * s) - 1)}px; padding: 0 {px(16)}; }}
            QPushButton#modeOverviewImport:hover, QPushButton#modeOverviewExport:hover,
            QPushButton#modeOverviewDiscard:hover {{ border-color: {tokens.BORDER_CONTROL_HOVER}; }}
        """)
        self._place()

    def _place(self) -> None:
        stage = self.parentWidget()
        if not isinstance(stage, ModeStage):
            return
        s = stage.scale()
        title = stage.rect_to_widget(QRectF(0, 20, BASE_W, 26))
        self.title.setGeometry(title.toAlignedRect())
        subtitle = stage.rect_to_widget(QRectF(0, 46, BASE_W, 20))
        self.subtitle.setGeometry(subtitle.toAlignedRect())
        row_width = self.row.sizeHint().width()
        top = stage.to_widget(QPointF(0, OVERVIEW_TOP)).y()
        height = round(OVERVIEW_CARD.height() * s) + round(14 * s)
        room = self.width() - round(2 * 24 * s)
        width = min(row_width, room)
        self.scroll.setGeometry(round(self.width() / 2 - width / 2), round(top), width, height)
        self.row.resize(row_width, round(OVERVIEW_CARD.height() * s))
        self.bar.adjustSize()
        hint = self.bar.sizeHint()
        bar_h = bar_height(s)
        centre = stage.to_widget(QPointF(0, OVERVIEW_BAR_Y)).y()
        self.bar.setGeometry(round(self.width() / 2 - hint.width() / 2), round(centre - bar_h / 2), hint.width(), bar_h)

    # -- press and drag to reorder (design decision) -------------------------------------------

    def movable_cards(self) -> list[_OverviewCard]:
        """The user's screens, left to right: they come first, before Codex and 新建一屏."""
        return [card for card in self.cards if card.movable]

    def dragging(self, card: _OverviewCard | None = None) -> bool:
        return self._drag is not None and (card is None or self._drag.card is card)

    def begin_drag(self, card: _OverviewCard, pressed_at: QPoint) -> bool:
        movable = self.movable_cards()
        if (self._drag is not None or not self._can_reorder or card not in movable or len(movable) < 2
                or not self.isVisible()):
            return False
        self._stop_slides()
        slots = [other.pos() for other in movable]
        self._row.setEnabled(False)      # the row is placed by hand until the card is put down
        start = movable.index(card)
        ghost = _LiftedCard(card.grab(), card.size(), self,
                            active=bool(card.screen and card.screen.active))
        grab = QPointF(card.mapFromGlobal(pressed_at))
        self._drag = _Drag(card, slots, [other for other in movable if other is not card], start, start, grab,
                           ghost, pressed_at)
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.0)           # its place stays empty
        card.setGraphicsEffect(effect)
        card.setCursor(Qt.CursorShape.ClosedHandCursor)
        ghost.place(self._slot_in_overview(start))
        ghost.show()
        ghost.raise_()
        self._animate_lift(1.0, tokens.OVERVIEW_LIFT_MS)
        self._autoscroll.start()
        return True

    def drag_to(self, global_point: QPoint) -> None:
        drag = self._drag
        if drag is None or drag.dropping:
            return
        drag.cursor = global_point
        self._follow_pointer()

    def drop(self) -> None:
        """Put the held card down in the place it is over; Codex and 新建一屏 never move."""
        drag = self._drag
        if drag is None or drag.dropping:
            return
        drag.dropping = True
        self._autoscroll.stop()
        if motion.reduces_motion():
            self._finish_drop()
            return
        self._animate_lift(0.0, tokens.OVERVIEW_SETTLE_MS, glide_to=self._slot_in_overview(drag.target),
                           then=self._finish_drop)

    def _slot_in_overview(self, index: int) -> QPointF:
        return QPointF(self.row.mapTo(self, self._drag.slots[index]))

    def _follow_pointer(self) -> None:
        drag = self._drag
        local = QPointF(self.mapFromGlobal(drag.cursor))
        first, last = self._slot_in_overview(0), self._slot_in_overview(len(drag.slots) - 1)
        # Held level with the row, and never past the last movable place (Codex stays last).
        x = min(max(local.x() - drag.grab.x(), first.x()), last.x())
        drag.ghost.place(QPointF(x, first.y()))
        centre = x + drag.card.width() / 2
        target = min(range(len(drag.slots)),
                     key=lambda i: abs(self._slot_in_overview(i).x() + drag.card.width() / 2 - centre))
        if target != drag.target:
            drag.target = target
            self._make_room()

    def _make_room(self) -> None:
        """The other cards slide out of the way of the place the held card is over."""
        drag = self._drag
        instant = motion.reduces_motion()
        for index, card in enumerate(drag.others):
            place = drag.slots[index if index < drag.target else index + 1]
            if instant:
                self._stop_slide(card)
                card.move(place)
            else:
                self._slide(card, place)

    def _slide(self, card: _OverviewCard, place: QPoint) -> None:
        self._stop_slide(card)
        if card.pos() == place:
            return
        animation = QVariantAnimation(self)
        animation.setStartValue(QPointF(card.pos()))
        animation.setEndValue(QPointF(place))
        animation.setDuration(tokens.OVERVIEW_SETTLE_MS)
        animation.setEasingCurve(motion.curve_swap())
        animation.valueChanged.connect(lambda value, card=card: card.move(value.toPoint()))
        self._slides[id(card)] = animation
        animation.start()

    def _stop_slide(self, card: _OverviewCard) -> None:
        animation = self._slides.pop(id(card), None)
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def _stop_slides(self) -> None:
        for animation in self._slides.values():
            animation.stop()
            animation.deleteLater()
        self._slides.clear()

    def _animate_lift(self, to: float, duration: int, *, glide_to: QPointF | None = None, then=None) -> None:
        drag = self._drag
        if self._lift_animation is not None:
            self._lift_animation.stop()
            self._lift_animation.deleteLater()
            self._lift_animation = None
        ghost = drag.ghost
        if motion.reduces_motion():
            ghost.set_lift(to)
            if glide_to is not None:
                ghost.place(glide_to)
            if then is not None:
                then()
            return
        start_lift = ghost.lift
        start_place = QPointF(ghost.pos()) + QPointF((ghost.width() - drag.card.width()) / 2,
                                                     (ghost.height() - drag.card.height()) / 2)
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(duration)
        animation.setEasingCurve(motion.curve_in())

        def step(value) -> None:
            t = float(value)
            ghost.set_lift(start_lift + (to - start_lift) * t)
            if glide_to is not None:
                ghost.place(start_place + (glide_to - start_place) * t)

        animation.valueChanged.connect(step)
        if then is not None:
            animation.finished.connect(then)
        self._lift_animation = animation
        animation.start()

    def _autoscroll_tick(self) -> None:
        drag = self._drag
        if drag is None or drag.dropping:
            self._autoscroll.stop()
            return
        bar = self.scroll.horizontalScrollBar()
        x = self.scroll.viewport().mapFromGlobal(drag.cursor).x()
        edge = tokens.OVERVIEW_AUTOSCROLL_EDGE * self._scale
        step = round(tokens.OVERVIEW_AUTOSCROLL_STEP * self._scale)
        width = self.scroll.viewport().width()
        if x < edge and bar.value() > bar.minimum():
            bar.setValue(bar.value() - step)
        elif x > width - edge and bar.value() < bar.maximum():
            bar.setValue(bar.value() + step)
        else:
            return
        self._follow_pointer()

    def _finish_drop(self) -> None:
        drag = self._drag
        if drag is None:
            return
        order = list(drag.others)
        order.insert(drag.target, drag.card)
        self._end_drag(order)
        if drag.target != drag.start:
            self.order_changed.emit([card.screen.key for card in order])
        if self._deferred is not None:
            self.set_screens(self._deferred[0], can_add=self._deferred[1], connected=self._deferred[2],
                             dirty=self._deferred[3], can_reorder=self._deferred[4])

    def _cancel_drag(self) -> None:
        drag = self._drag
        if drag is not None:
            order = list(drag.others)
            order.insert(drag.start, drag.card)
            self._end_drag(order)

    def _end_drag(self, order: list[_OverviewCard]) -> None:
        """Back to a laid-out row, with the movable cards in `order` (Codex, 新建一屏 after them)."""
        drag = self._drag
        self._drag = None
        self._autoscroll.stop()
        self._stop_slides()
        if self._lift_animation is not None:
            self._lift_animation.stop()
            self._lift_animation.deleteLater()
            self._lift_animation = None
        drag.ghost.hide()
        drag.ghost.deleteLater()
        drag.card.setGraphicsEffect(None)
        drag.card.setCursor(Qt.CursorShape.PointingHandCursor)
        fixed = [card for card in self.cards if not card.movable]
        for card in self.cards:
            self._row.removeWidget(card)
        self.cards = [*order, *fixed]
        for card in self.cards:
            self._row.addWidget(card)
        self._row.setEnabled(True)
        self._row.activate()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._place()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(tokens.MODE_BACKGROUND))
        stage = self.parentWidget()
        if self._connected and isinstance(stage, ModeStage):
            origin = stage.to_widget(QPointF(0, 0))
            painter.translate(origin)
            painter.scale(stage.scale(), stage.scale())
            floor, bottom = stage.floor_picture()   # drawn once, not every frame (design decision)
            painter.drawPixmap(QRectF(0, 0, BASE_W, bottom), floor, QRectF(floor.rect()))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        event.accept()   # clicks between the cards stay here, not on the page behind
