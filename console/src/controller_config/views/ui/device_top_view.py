"""The flat top view of the BORING MIST (设备图俯视图 正式稿 v0.5, the designer 2026-11-19).

Flat colour blocks, no outlines, no 3D. Geometry and colours come from the
draft's value table (8-数值表.png): millimetres with the origin at the body
centre, x right, y down. Key positions match the old silhouette
(views/device_silhouette.py, `_matrix12_control_layout`): screen, 1, 2, 3 /
4-7 / 8 (2U), 9, 10 / knob, 11, 12, joystick.

What the lighting page (灯光页 排法二 v0.4) needs from it:

- The 6 function keys (3, 8-12) are filled with their light colour; off or
  "不亮" shows the plain key cap. The 6 status-light keys (1, 2, 4-7) are
  driven by Codex: never clickable, the tooltip says so.
- `set_selected` puts an orange ring on the key's outer edge with a panel
  coloured gap between ring and key (the key looks one size smaller).
- The pointer over a function key lifts its colour one step; no ring.
- `set_connected(False)` dims all 12 keys (status-light keys too) to one flat
  colour, TOP_VIEW_KEY_OFFLINE (design ticket 第 1 件).
- `set_brightness` makes a lit key paler as the brightness drops.

What 看演示 on the Prompt page needs (正式稿 v0.5 第 6 张, design decision):

- `set_part_mark("joystick.up")` (or .down/.left/.right) rings the joystick
  base in orange, leans the cap 2.4 mm that way and draws an orange arrow
  from the cap past the ring. `set_part_mark("encoder.press")` rings the knob
  in orange and darkens it (TOP_VIEW_KNOB_PRESSED). `None` clears the mark.

Three sizes (body width): `mode="interactive"` every key can be clicked,
180 px at least; `mode="marker"` only marks a key, 96 px; `mode="icon"`
stands for the device, 32 px. Detail drops with the drawn size: below 120 px
no knob lines and no joystick dimple, below 96 px no panel and no joystick base.

Keyboard (interactive): Tab focuses the view, arrow keys move to the nearest
function key in that direction and select it, Space / Enter selects the key
under the focus ring.

    view = DeviceTopView()
    view.set_key_colors({"key.3": (91, 75, 255), "key.8": None})
    view.set_selected("key.3")
    view.key_selected.connect(lambda control_id: ...)
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from controller_config.i18n import translate_ui_text
from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS
from controller_config.style import tokens

RGB = tuple[int, int, int]

# -- geometry, millimetres (8-数值表.png) -----------------------------------------
BODY = 120.0
BODY_RADIUS = 18.0
PANEL = 100.0
PANEL_RADIUS = 10.0
KEY = 18.0
KEY_RADIUS = 2.5
WIDE_KEY = 36.6                     # key 8
KEY_CENTERS = {
    "key.1": (-9.84, -27.78), "key.2": (9.16, -27.78), "key.3": (28.16, -27.78),
    "key.4": (-28.84, -8.78), "key.5": (-9.84, -8.78), "key.6": (9.16, -8.78), "key.7": (28.16, -8.78),
    "key.8": (-19.84, 10.52), "key.9": (9.16, 10.22), "key.10": (28.16, 10.22),
    "key.11": (-9.84, 29.22), "key.12": (9.16, 29.22),
}
SCREEN_CENTER, SCREEN_DIAMETER = (-27.91, -27.74), 14.8
KNOB_CENTER, KNOB_DIAMETER, KNOB_GAP = (-41.45, 41.40), 37.0, 1.6
# Three short radial lines (2-旋钮和摇杆照实机.png): angle in degrees, y down.
KNOB_TICKS = (-150.0, -30.0, 90.0)
KNOB_TICK_FROM, KNOB_TICK_TO, KNOB_TICK_WIDTH = 0.45, 0.80, 0.55   # of the radius; width in mm
JOYSTICK_CENTER, JOYSTICK_DIAMETER = (27.5, 30.10), 15.0
JOYSTICK_INNER = 0.56               # dimple diameter, of the cap
JOYSTICK_BASE = QRectF(19.0, 20.5, 17.0, 19.7)
JOYSTICK_BASE_RADIUS = 2.2
MARGIN = 0.05                       # free space on each side, of the body width

# -- selection ring (3-选中-悬停-亮灯-蓝牙高亮-定了按推荐.png) ------------------------
RING_OUTSET = 0.35                  # mm beyond the key edge
RING_WIDTH = 2.0                    # px
RING_GAP = 1.3                      # px of panel colour between ring and key
FOCUS_GAP = 1.5                     # px between the selection ring and the keyboard focus ring

# -- marking the joystick and the knob (正式稿 v0.5 第 6 张) ---------------------------
PART_MARKS = ("joystick.up", "joystick.down", "joystick.left", "joystick.right", "encoder.press")
JOYSTICK_DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
ARROW_PAST_MM = 1.2                 # the arrow tip ends this far beyond the ring
ARROW_HEAD_MM = 2.2                 # each leg of the arrow head

# -- light colour on a key ----------------------------------------------------------
# A lit key is the light mixed with 22% cream (设备图稿 v0.5, chosen over the lighting
# draft's 22% cap: design ticket 第 2 件), the same at every level above off
# (the designer 11-20 真屏验收); off is the plain cap.
HOVER_LIGHTER = 112                 # the draft's hover steps for both cap colours, within 1/255
KEY_NUMBER_OPACITY = 0.6
KEY_NUMBER_SIZE = 0.27              # pixel size, of the key width (sampled)

# -- the three sizes, body width in px (4c-最小尺寸-定了按推荐.png) --------------------
MODES = {"interactive": 180, "marker": 96, "icon": 32}
PREFERRED_BODY = {"interactive": 198, "marker": 96, "icon": 32}  # 198: body on the lighting page draft
FULL_DETAIL_FROM = 120              # knob lines and joystick dimple from here up
PANEL_FROM = 96                     # panel and joystick base from here up
NUMBERS_FROM = 20                   # key numbers when a key is at least this wide, px

TOP_VIEW_PUSH = tokens.TOP_VIEW_JOYSTICK_PUSH_MM

FUNCTION_KEYS = tuple(key for key in KEY_CENTERS if key not in MATRIX12_AGENT_STATUS_KEYS)
STATUS_KEYS = tuple(key for key in KEY_CENTERS if key in MATRIX12_AGENT_STATUS_KEYS)

# Copy kept from the old 3D lighting preview (views/device_silhouette.py, removed with 灯光页 v0.4).
TIP_FUNCTION_KEY = "点击调整该按键灯颜色"
TIP_STATUS_KEY = "状态灯由 Codex 自动控制。"
TIP_KNOB = "旋钮不带可配置灯光"


def _color(value) -> QColor:
    return QColor(*value) if isinstance(value, tuple) else QColor(value)


def _mix(base: QColor, top: QColor, share: float) -> QColor:
    share = min(1.0, max(0.0, share))
    return QColor.fromRgbF(*(b + (t - b) * share for b, t in zip(base.getRgbF()[:3], top.getRgbF()[:3])))


def _clean(rgb: RGB | None) -> RGB | None:
    """Tuple of ints; black means off (that is how "不亮" used to be stored)."""
    if rgb is None:
        return None
    value = tuple(max(0, min(255, int(channel))) for channel in rgb)
    return None if not any(value) else value


def _font() -> QFont:
    font = QFont()
    font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
    return font


class DeviceTopView(QWidget):
    """Flat top view; see the module docstring for the rules and sizes."""

    key_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, mode: str = "interactive",
                 show_key_numbers: bool = True) -> None:
        super().__init__(parent)
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}")
        self._mode = mode
        self._show_numbers = show_key_numbers
        self._colors: dict[str, RGB | None] = {key: None for key in FUNCTION_KEYS}
        self._selected: str | None = None
        self._part_mark: str | None = None
        self._connected = True
        self._brightness = 1.0
        self._hover: str | None = None
        self._cursor: str | None = None      # key under the keyboard focus ring
        self._keyboard_focus = False
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setAccessibleName(translate_ui_text("设备俯视图"))
        if self.interactive():
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        else:
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # -- public API ----------------------------------------------------------------

    def mode(self) -> str:
        return self._mode

    def interactive(self) -> bool:
        return self._mode == "interactive"

    def set_key_colors(self, colors: Mapping[str, RGB | None]) -> None:
        """Light colour per function key, (r, g, b) or None for off; keys left out are off.

        Black counts as off (that is how "不亮" used to be stored). Status-light
        keys are ignored: Codex drives them.
        """
        self._colors = {key: _clean(colors.get(key)) for key in FUNCTION_KEYS}
        self.update()

    def key_colors(self) -> dict[str, RGB | None]:
        """What each function key shows (black already turned into None)."""
        return dict(self._colors)

    def set_selected(self, control_id: str | None) -> None:
        """Ring one key (any key, so marker mode can point at a status key too). No signal."""
        if control_id is not None and control_id not in KEY_CENTERS:
            raise ValueError(f"Unknown key: {control_id}")
        self._selected = control_id
        if control_id in FUNCTION_KEYS:
            self._cursor = control_id
        self._describe()
        self.update()

    def selected(self) -> str | None:
        return self._selected

    def set_part_mark(self, mark: str | None) -> None:
        """Mark the joystick pushed one way ("joystick.up" ...) or the knob pressed ("encoder.press")."""
        if mark is not None and mark not in PART_MARKS:
            raise ValueError(f"Unknown part mark: {mark}")
        self._part_mark = mark
        self._describe()
        self.update()

    def part_mark(self) -> str | None:
        return self._part_mark

    def knob_fill(self) -> QColor:
        """The knob's colour right now: darker while it is marked as pressed."""
        return QColor(tokens.TOP_VIEW_KNOB_PRESSED if self._part_mark == "encoder.press" else tokens.TOP_VIEW_KNOB)

    def joystick_cap_center(self) -> QPointF:
        """The cap's centre in widget pixels; it leans towards a marked direction."""
        dx, dy = self._push()
        return self._point(JOYSTICK_CENTER[0] + dx * TOP_VIEW_PUSH, JOYSTICK_CENTER[1] + dy * TOP_VIEW_PUSH)

    def _push(self) -> tuple[float, float]:
        if self._part_mark and self._part_mark.startswith("joystick."):
            return JOYSTICK_DIRECTIONS[self._part_mark.split(".")[1]]
        return 0.0, 0.0

    def set_connected(self, connected: bool) -> None:
        """Without a device all 12 keys show one dimmed colour instead of their light."""
        self._connected = bool(connected)
        self.update()

    def connected(self) -> bool:
        return self._connected

    def set_brightness(self, level: float) -> None:
        """Key light brightness, 0 (off, plain caps) .. 1 (最高). The lighting page stops at 0, .25, .5, .75, 1."""
        self._brightness = min(1.0, max(0.0, float(level)))
        self.update()

    def brightness(self) -> float:
        return self._brightness

    def key_fill(self, control_id: str, *, hovered: bool = False) -> QColor:
        """The colour a key is painted with right now (also used by tests)."""
        if not self._connected:
            # 12 keys, one colour (design ticket 第 1 件); status keys do not react to the pointer.
            fill = QColor(tokens.TOP_VIEW_KEY_OFFLINE)
            if control_id in STATUS_KEYS:
                return fill
        elif control_id in STATUS_KEYS:
            return QColor(tokens.TOP_VIEW_STATUS_CAP)
        else:
            rgb = self._colors.get(control_id)
            if rgb is None or self._brightness <= 0:
                return QColor(tokens.TOP_VIEW_KEY_CAP_HOVER if hovered else tokens.TOP_VIEW_KEY_CAP)
            # Any brightness above off shows the light at full depth: the level is not drawn
            # on the keys (the designer 11-20 真屏验收: 低／中／高／最高 "都不应该影响 6 个按键上面的
            # 颜色深浅"); off stays the plain cap.
            fill = _mix(_color(rgb), QColor(tokens.TOP_VIEW_LIGHT_CREAM), tokens.TOP_VIEW_LIGHT_CREAM_SHARE)
        return fill.lighter(HOVER_LIGHTER) if hovered else fill

    # -- size ----------------------------------------------------------------------

    def _side_for(self, body: float) -> int:
        return math.ceil(round(body * (1 + 2 * MARGIN), 6))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        side = self._side_for(PREFERRED_BODY[self._mode])
        return QSize(side, side)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        side = self._side_for(MODES[self._mode])
        return QSize(side, side)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt virtual method
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt virtual method
        return width

    def body_width(self) -> float:
        """Drawn body width in px."""
        return round(min(self.width(), self.height()) / (1 + 2 * MARGIN), 6)

    # -- geometry helpers ----------------------------------------------------------

    def _scale(self) -> float:
        return self.body_width() / BODY

    def _point(self, x_mm: float, y_mm: float) -> QPointF:
        scale = self._scale()
        return QPointF(self.width() / 2 + x_mm * scale, self.height() / 2 + y_mm * scale)

    def _rect(self, center: tuple[float, float], width: float, height: float) -> QRectF:
        scale = self._scale()
        middle = self._point(*center)
        return QRectF(middle.x() - width * scale / 2, middle.y() - height * scale / 2,
                      width * scale, height * scale)

    def key_rect(self, control_id: str) -> QRectF:
        """The key cap in widget pixels (before the selection ring shrinks it)."""
        width = WIDE_KEY if control_id == "key.8" else KEY
        return self._rect(KEY_CENTERS[control_id], width, KEY)

    def _knob_rect(self, extra_mm: float = 0.0) -> QRectF:
        diameter = KNOB_DIAMETER + 2 * extra_mm
        return self._rect(KNOB_CENTER, diameter, diameter)

    def control_at(self, position: QPointF) -> str | None:
        """Key id, "encoder" or None under a widget position."""
        for control_id in KEY_CENTERS:
            if self.key_rect(control_id).contains(position):
                return control_id
        knob = self._knob_rect()
        offset = position - knob.center()
        if math.hypot(offset.x(), offset.y()) <= knob.width() / 2:
            return "encoder"
        return None

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        scale = self._scale()
        body_px = self.body_width()

        painter.setBrush(QColor(tokens.TOP_VIEW_BODY))
        painter.drawRoundedRect(self._rect((0, 0), BODY, BODY), BODY_RADIUS * scale, BODY_RADIUS * scale)
        if body_px >= PANEL_FROM:
            painter.setBrush(QColor(tokens.TOP_VIEW_PANEL))
            painter.drawRoundedRect(self._rect((0, 0), PANEL, PANEL), PANEL_RADIUS * scale, PANEL_RADIUS * scale)

        painter.setBrush(QColor(tokens.TOP_VIEW_SCREEN))
        painter.drawEllipse(self._rect(SCREEN_CENTER, SCREEN_DIAMETER, SCREEN_DIAMETER))

        if body_px >= PANEL_FROM:
            painter.setBrush(QColor(tokens.TOP_VIEW_JOYSTICK_BASE))
            painter.drawRoundedRect(self._base_rect(), JOYSTICK_BASE_RADIUS * scale, JOYSTICK_BASE_RADIUS * scale)
        cap_mm = (JOYSTICK_CENTER[0] + self._push()[0] * TOP_VIEW_PUSH,
                  JOYSTICK_CENTER[1] + self._push()[1] * TOP_VIEW_PUSH)
        painter.setBrush(QColor(tokens.TOP_VIEW_JOYSTICK_CAP))
        painter.drawEllipse(self._rect(cap_mm, JOYSTICK_DIAMETER, JOYSTICK_DIAMETER))
        if body_px >= FULL_DETAIL_FROM:
            inner = JOYSTICK_DIAMETER * JOYSTICK_INNER
            painter.setBrush(QColor(tokens.TOP_VIEW_JOYSTICK_INNER))
            painter.drawEllipse(self._rect(cap_mm, inner, inner))

        for control_id in KEY_CENTERS:
            self._paint_key(painter, control_id, scale)

        # The knob sits over the body's corner and slightly beyond it, as on the device.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.TOP_VIEW_KNOB_GAP))
        painter.drawEllipse(self._knob_rect(KNOB_GAP))
        painter.setBrush(self.knob_fill())
        knob = self._knob_rect()
        painter.drawEllipse(knob)
        if body_px >= FULL_DETAIL_FROM and self.knob_ticks > 0:
            tick = QColor(tokens.TOP_VIEW_KNOB_TICK)
            tick.setAlphaF(tick.alphaF() * self.knob_ticks)
            pen = QPen(tick, KNOB_TICK_WIDTH * scale)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            radius = knob.width() / 2
            for angle in KNOB_TICKS:
                direction = QPointF(math.cos(math.radians(angle)), math.sin(math.radians(angle)))
                painter.drawLine(knob.center() + direction * radius * KNOB_TICK_FROM,
                                 knob.center() + direction * radius * KNOB_TICK_TO)
        if self._part_mark == "encoder.press":
            self._paint_knob_ring(painter)
        elif self._part_mark is not None:
            self._paint_joystick_mark(painter)
        painter.end()

    def _mark_pen(self) -> QPen:
        pen = QPen(QColor(tokens.TOP_VIEW_MARK), tokens.TOP_VIEW_MARK_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _paint_knob_ring(self, painter: QPainter) -> None:
        """Orange ring in the dark gap round the knob."""
        ring = self._knob_rect(KNOB_GAP / 2)
        painter.setPen(self._mark_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)

    def _paint_joystick_mark(self, painter: QPainter) -> None:
        """Orange frame round the base and an arrow from the cap past the frame."""
        scale = self._scale()
        outset = RING_OUTSET * scale + tokens.TOP_VIEW_MARK_WIDTH / 2
        frame = self._base_rect().adjusted(-outset, -outset, outset, outset)
        radius = JOYSTICK_BASE_RADIUS * scale + outset
        painter.setPen(self._mark_pen())
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, radius, radius)
        dx, dy = self._push()
        start = self.joystick_cap_center()
        edge = {(0.0, -1.0): frame.top(), (0.0, 1.0): frame.bottom(),
                (-1.0, 0.0): frame.left(), (1.0, 0.0): frame.right()}[(dx, dy)]
        past = ARROW_PAST_MM * scale
        tip = (QPointF(start.x(), edge + dy * past) if dy else QPointF(edge + dx * past, start.y()))
        head = ARROW_HEAD_MM * scale
        back = QPointF(-dx, -dy) * head * math.sqrt(0.5)
        side = QPointF(-dy, dx) * head * math.sqrt(0.5)
        path = QPainterPath(start)
        path.lineTo(tip)
        path.moveTo(tip + back + side)
        path.lineTo(tip)
        path.lineTo(tip + back - side)
        painter.drawPath(path)

    knob_ticks: float = 1.0   # how strongly the knob's tick marks show (the Mode page fades them on zoom)

    def _base_rect(self) -> QRectF:
        scale = self._scale()
        corner = self._point(JOYSTICK_BASE.x(), JOYSTICK_BASE.y())
        return QRectF(corner.x(), corner.y(), JOYSTICK_BASE.width() * scale, JOYSTICK_BASE.height() * scale)

    def _paint_key(self, painter: QPainter, control_id: str, scale: float) -> None:
        rect = self.key_rect(control_id)
        radius = KEY_RADIUS * scale
        hovered = self.interactive() and control_id == self._hover and control_id in FUNCTION_KEYS
        face, face_radius = rect, radius
        if control_id == self._selected:
            outset = RING_OUTSET * scale
            outer = rect.adjusted(-outset, -outset, outset, outset)
            outer_radius = radius + outset
            ring = QPainterPath()
            ring.addRoundedRect(outer, outer_radius, outer_radius)
            hole = outer.adjusted(RING_WIDTH, RING_WIDTH, -RING_WIDTH, -RING_WIDTH)
            hole_radius = max(0.0, outer_radius - RING_WIDTH)
            inner = QPainterPath()
            inner.addRoundedRect(hole, hole_radius, hole_radius)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.TOP_VIEW_SELECTED))
            painter.drawPath(ring.subtracted(inner))
            painter.setBrush(QColor(tokens.TOP_VIEW_PANEL))
            painter.drawRoundedRect(hole, hole_radius, hole_radius)
            inset = RING_WIDTH + RING_GAP
            face = outer.adjusted(inset, inset, -inset, -inset)
            face_radius = max(0.0, outer_radius - inset)
        fill = self.key_fill(control_id, hovered=hovered)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(face, face_radius, face_radius)

        if (self._show_numbers and control_id in FUNCTION_KEYS and self.interactive()
                and KEY * scale >= NUMBERS_FROM):
            ink = QColor(tokens.TOP_VIEW_KEY_NUMBER)
            ink.setAlphaF(KEY_NUMBER_OPACITY)
            font = _font()
            font.setPixelSize(max(1, round(KEY * scale * KEY_NUMBER_SIZE)))
            painter.setFont(font)
            painter.setPen(ink)
            painter.drawText(face, Qt.AlignmentFlag.AlignCenter, control_id.split(".")[1])

        if self._keyboard_focus and self.hasFocus() and control_id == self._cursor:
            reach = (RING_OUTSET * scale if control_id == self._selected else 0.0) + FOCUS_GAP + 0.5
            focus = rect.adjusted(-reach, -reach, reach, reach)
            painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(focus, radius + reach, radius + reach)

    # -- pointer -------------------------------------------------------------------

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            control_id = self.control_at(QPointF(event.pos()))
            text = {"encoder": TIP_KNOB}.get(control_id)
            if control_id in FUNCTION_KEYS:
                text = TIP_FUNCTION_KEY if self.interactive() else None
            elif control_id in STATUS_KEYS:
                text = TIP_STATUS_KEY
            if text:
                QToolTip.showText(event.globalPos(), translate_ui_text(text), self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        control_id = self.control_at(event.position())
        hover = control_id if control_id in FUNCTION_KEYS else None
        if hover != self._hover:
            self._hover = hover
            if hover:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self._hover is not None:
            self._hover = None
            self.unsetCursor()
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.interactive() and event.button() == Qt.MouseButton.LeftButton:
            control_id = self.control_at(event.position())
            if control_id in FUNCTION_KEYS:
                self._keyboard_focus = False
                self._choose(control_id)
                event.accept()
                return
        super().mousePressEvent(event)

    def _choose(self, control_id: str) -> None:
        self.set_selected(control_id)
        self.key_selected.emit(control_id)

    # -- keyboard ------------------------------------------------------------------

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
        }
        if self._cursor is None:
            self._cursor = self._selected if self._selected in FUNCTION_KEYS else FUNCTION_KEYS[0]
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        directions = {
            Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
        }
        if not self.interactive():
            super().keyPressEvent(event)
            return
        if self._cursor is None:
            self._cursor = self._selected if self._selected in FUNCTION_KEYS else FUNCTION_KEYS[0]
        if event.key() in directions:
            self._keyboard_focus = True
            start = self._cursor
            target = self._neighbour(start, directions[event.key()]) if self._selected == start else start
            if target is not None:
                self._choose(target)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self._keyboard_focus = True
            self._choose(self._cursor)
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _neighbour(start: str, direction: tuple[int, int]) -> str | None:
        """Nearest function key in a direction; sideways distance counts double."""
        origin = KEY_CENTERS[start]
        best, best_score = None, math.inf
        for control_id in FUNCTION_KEYS:
            dx, dy = (KEY_CENTERS[control_id][0] - origin[0], KEY_CENTERS[control_id][1] - origin[1])
            ahead = dx * direction[0] + dy * direction[1]
            if control_id == start or ahead <= 1.0:
                continue
            sideways = abs(dx * direction[1] - dy * direction[0])
            score = ahead + 2 * sideways
            if score < best_score:
                best, best_score = control_id, score
        return best

    def _describe(self) -> None:
        if self._part_mark is not None:
            words = {"encoder.press": "旋钮 · 按下", "joystick.up": "摇杆 · 向上", "joystick.down": "摇杆 · 向下",
                     "joystick.left": "摇杆 · 向左", "joystick.right": "摇杆 · 向右"}
            self.setAccessibleDescription(translate_ui_text(words[self._part_mark]))
            return
        if self._selected is None:
            self.setAccessibleDescription("")
            return
        number = self._selected.split(".")[1]
        kind = "功能键" if self._selected in FUNCTION_KEYS else "状态灯键"
        self.setAccessibleDescription(translate_ui_text(f"已选 {kind} {number}"))
