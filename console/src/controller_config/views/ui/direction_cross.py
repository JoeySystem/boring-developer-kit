"""The direction cross on the Prompt page (Prompt 页 定稿 v1, the designer 2026-11-18).

Four quick prompts sit where the joystick sends them: a 3 x 3 grid with the
up, left, right and down cells around a drawing of the joystick; the corners
stay empty except the top-left one, which holds four dots in a cross (one
per direction, filled when it is set). Values come from the draft's value
table (Console视觉稿/Prompt页-定稿-v1-20261118-图/5-小图形和数值表.png);
colours were sampled from 1-主画面-960x640-正在改上这一条.png.

A direction cell: the direction word top left, a status shape top right
(saved = green check, dirty = orange three dots, empty = dashed circle, from
`StatusIcon`), the name (13, bold, up to 3 lines) and the start of the body
below it (11.5). Name and body share 4 lines: the more lines the name really
wraps to, the fewer the body gets (3 / 2 / 1). An empty cell says "＋ 添加".
The selected cell is cream all over with dark text, like the selected icon
in the Settings rail.

The joystick in the middle: a disc and an arrow of 5 dots pointing at the
selected direction. When the selection moves to another direction the dots
light up from the disc outwards in three steps (about 0.15 s each) and then
stay lit; with the system's "reduce motion" on they are lit at once.

The cross only asks for a direction: a click, an arrow key or Space / Enter
emits `direction_selected(prompt_id)`, and the page calls `set_selected`
once it has dealt with unsaved changes. Prompt ids and directions come from
`QUICK_PROMPT_DIRECTIONS` (1 up, 2 right, 3 down, 4 left).

    cross = DirectionCross()
    cross.set_direction(1, "代码审查", "请审查这段代码：…", "dirty")
    cross.set_direction(4, "", "", "empty")
    cross.set_selected(1)
    cross.direction_selected.connect(page.ask_to_switch)

`DirectionGlyph` is the same drawing at 32 px for the editor title.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QSizePolicy, QWidget

from controller_config.accessibility import system_reduces_motion
from controller_config.i18n import translate_ui_text
from controller_config.prompt_library import QUICK_PROMPT_DIRECTIONS
from controller_config.style import tokens
from controller_config.views.ui.status_icon import StatusIcon

# -- directions (prompt_library.QUICK_PROMPT_DIRECTIONS) -----------------------------
DIRECTIONS = {prompt_id: control_id.split(".")[1] for control_id, prompt_id, _arrow, _label in QUICK_PROMPT_DIRECTIONS}
DIRECTION_WORDS = {prompt_id: label for _control, prompt_id, _arrow, label in QUICK_PROMPT_DIRECTIONS}
PROMPT_IDS = {direction: prompt_id for prompt_id, direction in DIRECTIONS.items()}
VECTORS = {"up": (0.0, -1.0), "right": (1.0, 0.0), "down": (0.0, 1.0), "left": (-1.0, 0.0)}
GRID = {"up": (0, 1), "left": (1, 0), "right": (1, 2), "down": (2, 1)}   # (row, column)
KEYS = {Qt.Key.Key_Up: "up", Qt.Key.Key_Right: "right", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left"}

STATES = ("saved", "dirty", "empty")
STATUS_SHAPES = {"saved": "ok", "dirty": "wait", "empty": "off"}
ADD_TEXT = "＋ 添加"

# -- the cross (数值表 "十字" "方向格") -------------------------------------------------
CELL = 120.0                # at full size; the cross shrinks with its widget
GAP = 12.0
CELL_RADIUS = 16.0
PAD_X, PAD_Y = 12.0, 10.0
STATUS_SIZE = 16
NAME_TOP = 30.0             # from the cell top: header row (10 + 16) and 4 px air, measured
NAME_LINE = 18.0            # 13 px bold, measured line pitch
PREVIEW_GAP = 4.0
PREVIEW_LINE = 15.0         # 11.5 px, measured line pitch
MAX_NAME_LINES = 3
TEXT_LINES = 4              # name lines + preview lines
FULL_SIDE = 3 * CELL + 2 * GAP   # 384: the 416 tile minus its 16 px padding
MIN_SIDE = 284              # a 2 x 2 tile on the smallest wall (one wall cell 150)
FOCUS_OUTSET = 3.0          # keyboard focus ring, outside the cell so it shows on cream too

# -- four dots in a cross, top-left corner (数值表 "四个圆点") ---------------------------
FOUR_DOT = 8.0
FOUR_DOT_GAP = 4.0
FOUR_DOT_INSET = 2.0        # from the grid corner, measured
FOUR_DOT_RING = 1.5

# -- joystick and dot arrow (数值表 "十字正中") -----------------------------------------
CANVAS = 112.0
JOYSTICK = 43.0             # disc diameter
ARROW_DOT = 6.7
ARROW_STEP = 7.8            # neighbouring dots, along and across
ARROW_TIP = 51.0            # tip dot centre from the disc centre
ROW_ALPHA = (1.0, 0.85, 0.65)   # tip, middle pair, back pair when lit (sampled)
UNLIT_ALPHA = 0.16              # a dot not lit yet (sampled from the four steps)
ARROW_ROWS = 3
STEP_MS = tokens.MOTION_SHORT_MS  # 数值表 "动起来": three steps, 短 each (design decision; was 0.15 s)

# -- small glyph for the editor title (数值表 "标题前") ----------------------------------
GLYPH_WELL = 32
GLYPH_RADIUS = 10.0
GLYPH_DISC = 9.0
GLYPH_DISC_SHIFT = 4.0      # away from the arrow
GLYPH_DOT = 2.2
GLYPH_TIP = 9.6             # measured on the draft at 4x
GLYPH_STEP = 2.7            # measured


def direction_of(prompt_id: int) -> str:
    """"up" / "right" / "down" / "left" for a quick prompt id (1-4)."""
    try:
        return DIRECTIONS[prompt_id]
    except KeyError:
        raise ValueError(f"Not a quick prompt id: {prompt_id}") from None


def arrow_dots(direction: str, tip: float, step: float, rows: int = ARROW_ROWS) -> list[tuple[int, QPointF]]:
    """(row, offset from the disc centre) of each dot; row 0 is the tip."""
    dx, dy = VECTORS[direction]
    dots = [(0, QPointF(dx * tip, dy * tip))]
    for row in range(1, rows):
        along = tip - row * step
        across = row * step
        for side in (-1.0, 1.0):
            dots.append((row, QPointF(dx * along - dy * across * side, dy * along + dx * across * side)))
    return dots


def _font(widget: QWidget, pixels: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setFamilies([name.strip().strip('"') for name in tokens.FONT_FAMILY.split(",")])
    # Points, so 11.5 px stays 11.5 px; logical DPI is 72 on macOS, 96 on Windows.
    font.setPointSizeF(pixels * 72.0 / max(1, widget.logicalDpiY()))
    font.setWeight(weight)
    return font


def _utf16_slice(text: str, start: int, length: int) -> str:
    data = text.encode("utf-16-le")
    return data[2 * start:2 * (start + length)].decode("utf-16-le", errors="ignore")


def wrap_lines(text: str, font: QFont, width: float, max_lines: int) -> list[str]:
    """`text` wrapped to `width`, at most `max_lines`; a cut last line ends in "…"."""
    text = " ".join(text.split())
    if max_lines <= 0 or not text or width <= 0:
        return []
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    spans: list[tuple[int, int]] = []
    while len(spans) <= max_lines:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        spans.append((line.textStart(), line.textLength()))
    layout.endLayout()
    lines = [_utf16_slice(text, start, length).strip() for start, length in spans[:max_lines]]
    if len(spans) > max_lines:
        rest = _utf16_slice(text, spans[max_lines - 1][0], len(text.encode("utf-16-le")))
        cut = QFontMetricsF(font).elidedText(rest, Qt.TextElideMode.ElideRight, width)
        lines[-1] = cut[:-1].rstrip() + "…" if cut.endswith("…") else cut   # "into …" -> "into…"
    return lines


def _with_alpha(value: str, alpha: float) -> QColor:
    colour = QColor(value)
    colour.setAlphaF(alpha)
    return colour


@dataclass
class _Entry:
    name: str = ""
    preview: str = ""
    state: str = "empty"


class DirectionCross(QWidget):
    """The 3 x 3 direction cross; see the module docstring."""

    direction_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, reduce_motion: bool | None = None) -> None:
        super().__init__(parent)
        self._entries = {prompt_id: _Entry() for prompt_id in DIRECTIONS}
        self._selected: int | None = None
        self._cursor = PROMPT_IDS["up"]          # cell under the keyboard focus ring
        self._keyboard_focus = False
        self._reduce_motion = system_reduces_motion() if reduce_motion is None else reduce_motion
        self._lit = ARROW_ROWS                   # arrow rows lit, counted from the disc outwards
        self._timer = QTimer(self)
        self._timer.setInterval(STEP_MS)
        self._timer.timeout.connect(self._step)
        self._icons = {prompt_id: StatusIcon("off", self, size=STATUS_SIZE) for prompt_id in DIRECTIONS}
        for icon in self._icons.values():
            icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        size_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        size_policy.setHeightForWidth(True)
        self.setSizePolicy(size_policy)
        self.setAccessibleName(translate_ui_text("快捷提示词"))
        self._describe()

    # -- public ----------------------------------------------------------------------

    def set_direction(self, prompt_id: int, name: str, body_preview: str, state: str) -> None:
        """What one cell shows; `state` is "saved", "dirty" or "empty" (name and body ignored)."""
        direction_of(prompt_id)
        if state not in STATES:
            raise ValueError(f"Unknown prompt state: {state}")
        entry = _Entry(name or "", body_preview or "", state)
        if entry == self._entries[prompt_id]:
            return
        self._entries[prompt_id] = entry
        self._icons[prompt_id].set_state(STATUS_SHAPES[state])
        self._describe()
        self.update()

    def set_selected(self, prompt_id: int | None) -> None:
        """Mark the direction being edited; does not emit `direction_selected`."""
        if prompt_id is not None:
            direction_of(prompt_id)
        if prompt_id == self._selected:
            return
        moved = self._selected is not None and prompt_id is not None
        self._selected = prompt_id
        if prompt_id is not None:
            self._cursor = prompt_id
        if moved and not self._reduce_motion and self.isVisible():
            self._lit = 0
            self._timer.start()
        else:
            self._timer.stop()
            self._lit = ARROW_ROWS
        self._describe()
        self.update()

    def selected(self) -> int | None:
        return self._selected

    def state(self, prompt_id: int) -> str:
        return self._entries[prompt_id].state

    def lit_rows(self) -> int:
        """How many arrow rows are lit, from the disc outwards (3 = all, resting)."""
        return self._lit

    def cell_rect(self, prompt_id: int) -> QRectF:
        row, column = GRID[direction_of(prompt_id)]
        return self._grid_rect(row, column)

    def joystick_center(self) -> QPointF:
        return self._grid_rect(1, 1).center()

    def scale(self) -> float:
        """Drawn size against the draft (1.0 = cells of 120)."""
        return self._cell() / CELL

    def shown_text(self, prompt_id: int) -> tuple[list[str], list[str]]:
        """(name lines, body lines) as painted now, after wrapping and cutting."""
        entry = self._entries[prompt_id]
        rect = self.cell_rect(prompt_id)
        width = rect.width() - 2 * PAD_X
        room = rect.height() - NAME_TOP - PAD_Y
        if entry.state == "empty":
            return [translate_ui_text(ADD_TEXT)], []
        name_room = min(MAX_NAME_LINES, int(room // NAME_LINE))
        names = wrap_lines(entry.name, self._name_font(), width, name_room)
        used = len(names) * NAME_LINE + (PREVIEW_GAP if names else 0.0)
        preview_room = min(TEXT_LINES - len(names), int(max(0.0, room - used) // PREVIEW_LINE))
        return names, wrap_lines(entry.preview, self._preview_font(), width, preview_room)

    # -- size --------------------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(round(FULL_SIDE), round(FULL_SIDE))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(MIN_SIDE, MIN_SIDE)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt virtual method
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt virtual method
        return width

    def _side(self) -> float:
        return float(min(self.width(), self.height()))

    def _cell(self) -> float:
        return max(1.0, (self._side() - 2 * GAP) / 3)

    def _grid_rect(self, row: int, column: int) -> QRectF:
        side, cell = self._side(), self._cell()
        left = (self.width() - side) / 2 + column * (cell + GAP)
        top = (self.height() - side) / 2 + row * (cell + GAP)
        return QRectF(left, top, cell, cell)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._place_icons()
        super().resizeEvent(event)

    def _place_icons(self) -> None:
        for prompt_id, icon in self._icons.items():
            rect = self.cell_rect(prompt_id)
            icon.move(round(rect.right() - PAD_X - STATUS_SIZE), round(rect.top() + PAD_Y))

    # -- painting ----------------------------------------------------------------------

    def _name_font(self) -> QFont:
        return _font(self, tokens.FONT_BODY, QFont.Weight.DemiBold)

    def _preview_font(self) -> QFont:
        return _font(self, tokens.FONT_PROMPT_PREVIEW)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        for prompt_id in DIRECTIONS:
            self._paint_cell(painter, prompt_id)
        self._paint_four_dots(painter)
        self._paint_joystick(painter)
        painter.end()

    def _paint_cell(self, painter: QPainter, prompt_id: int) -> None:
        rect = self.cell_rect(prompt_id)
        entry = self._entries[prompt_id]
        selected = prompt_id == self._selected
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.PROMPT_CELL_SELECTED))
            painter.drawRoundedRect(rect, CELL_RADIUS, CELL_RADIUS)
        else:
            painter.setPen(QPen(QColor(tokens.PROMPT_CELL_EDGE), 1))
            painter.setBrush(QColor(tokens.PROMPT_CELL))
            inner = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(inner, CELL_RADIUS - 0.5, CELL_RADIUS - 0.5)
        strong = QColor(tokens.PROMPT_CELL_TEXT_ON_CREAM if selected else tokens.PROMPT_CELL_TEXT)
        muted = QColor(tokens.PROMPT_CELL_MUTED_ON_CREAM if selected else tokens.PROMPT_CELL_MUTED)
        width = rect.width() - 2 * PAD_X
        left = rect.left() + PAD_X

        painter.setFont(_font(self, tokens.FONT_CAPTION))
        painter.setPen(muted)
        header = QRectF(left, rect.top() + PAD_Y, width - STATUS_SIZE - 4, STATUS_SIZE)
        painter.drawText(header, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         translate_ui_text(DIRECTION_WORDS[prompt_id]))

        names, previews = self.shown_text(prompt_id)
        top = rect.top() + NAME_TOP
        if entry.state == "empty":
            painter.setFont(_font(self, tokens.FONT_BODY))
            painter.setPen(muted)
            painter.drawText(QRectF(left, top, width, NAME_LINE),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, names[0])
        else:
            painter.setFont(self._name_font())
            painter.setPen(strong)
            for line in names:
                painter.drawText(QRectF(left, top, width, NAME_LINE),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                top += NAME_LINE
            top += PREVIEW_GAP if names else 0.0
            painter.setFont(self._preview_font())
            painter.setPen(muted)
            for line in previews:
                painter.drawText(QRectF(left, top, width, PREVIEW_LINE),
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                top += PREVIEW_LINE

        if self._keyboard_focus and self.hasFocus() and prompt_id == self._cursor:
            ring = rect.adjusted(-FOCUS_OUTSET, -FOCUS_OUTSET, FOCUS_OUTSET, FOCUS_OUTSET)
            painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(ring, CELL_RADIUS + FOCUS_OUTSET, CELL_RADIUS + FOCUS_OUTSET)

    def _paint_four_dots(self, painter: QPainter) -> None:
        corner = self._grid_rect(0, 0)
        pitch = FOUR_DOT + FOUR_DOT_GAP
        centre = QPointF(corner.left() + FOUR_DOT_INSET + pitch + FOUR_DOT / 2,
                         corner.top() + FOUR_DOT_INSET + pitch + FOUR_DOT / 2)
        radius = FOUR_DOT / 2
        for prompt_id, direction in DIRECTIONS.items():
            dx, dy = VECTORS[direction]
            point = QPointF(centre.x() + dx * pitch, centre.y() + dy * pitch)
            if self._entries[prompt_id].state == "empty":
                painter.setPen(QPen(QColor(tokens.PROMPT_UNSET_DOT), FOUR_DOT_RING))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                ring = radius - FOUR_DOT_RING / 2
                painter.drawEllipse(point, ring, ring)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(tokens.PROMPT_SET_DOT))
                painter.drawEllipse(point, radius, radius)

    def _paint_joystick(self, painter: QPainter) -> None:
        centre = self.joystick_center()
        scale = self.scale()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.PROMPT_JOYSTICK))
        disc = JOYSTICK * scale / 2
        painter.drawEllipse(centre, disc, disc)
        if self._selected is None:
            return
        radius = ARROW_DOT * scale / 2
        for row, offset in arrow_dots(direction_of(self._selected), ARROW_TIP * scale, ARROW_STEP * scale):
            lit = row >= ARROW_ROWS - self._lit
            painter.setBrush(_with_alpha(tokens.PROMPT_JOYSTICK, ROW_ALPHA[row] if lit else UNLIT_ALPHA))
            painter.drawEllipse(centre + offset, radius, radius)

    def _step(self) -> None:
        self._lit += 1
        if self._lit >= ARROW_ROWS:
            self._lit = ARROW_ROWS
            self._timer.stop()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._timer.stop()
        self._lit = ARROW_ROWS
        super().hideEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.type() == QEvent.Type.LanguageChange:
            self._describe()
            self.update()
        super().changeEvent(event)

    # -- pointer and keyboard ----------------------------------------------------------

    def prompt_at(self, point: QPointF) -> int | None:
        for prompt_id in DIRECTIONS:
            if self.cell_rect(prompt_id).contains(point):
                return prompt_id
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.prompt_at(event.position()) is None:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        prompt_id = self.prompt_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and prompt_id is not None:
            self._keyboard_focus = False
            self._cursor = prompt_id
            self._ask(prompt_id)
            event.accept()
            return
        super().mousePressEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason,
        }
        if self._selected is not None:
            self._cursor = self._selected
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.key() in KEYS:
            self._keyboard_focus = True
            self._cursor = PROMPT_IDS[KEYS[event.key()]]
            self._describe()
            self._ask(self._cursor)
            self.update()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self._keyboard_focus = True
            self._ask(self._cursor)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def _ask(self, prompt_id: int) -> None:
        if prompt_id != self._selected:
            self.direction_selected.emit(prompt_id)

    def _describe(self) -> None:
        state_words = {"saved": "已存进设备", "dirty": "有修改，还没存进设备", "empty": "还没设置"}
        entry = self._entries[self._cursor]
        name = entry.name if entry.state != "empty" else ""
        parts = [translate_ui_text(DIRECTION_WORDS[self._cursor]), name, translate_ui_text(state_words[entry.state])]
        self.setAccessibleDescription(" · ".join(part for part in parts if part))


class DirectionGlyph(QWidget):
    """The joystick and a 3-dot arrow at 32 px, in front of the editor title."""

    def __init__(self, direction: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._direction: str | None = None
        self.setFixedSize(GLYPH_WELL, GLYPH_WELL)
        self.set_direction(direction)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(GLYPH_WELL, GLYPH_WELL)

    def direction(self) -> str | None:
        return self._direction

    def set_direction(self, direction: str | None) -> None:
        """"up" / "right" / "down" / "left" (see `direction_of`), or None for the disc alone."""
        if direction is not None and direction not in VECTORS:
            raise ValueError(f"Unknown direction: {direction}")
        self._direction = direction
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.PROMPT_GLYPH_WELL))
        painter.drawRoundedRect(QRectF(0, 0, GLYPH_WELL, GLYPH_WELL), GLYPH_RADIUS, GLYPH_RADIUS)
        centre = QPointF(GLYPH_WELL / 2, GLYPH_WELL / 2)
        disc = QPointF(centre)
        if self._direction is not None:
            dx, dy = VECTORS[self._direction]
            disc = QPointF(centre.x() - dx * GLYPH_DISC_SHIFT, centre.y() - dy * GLYPH_DISC_SHIFT)
            for row, offset in arrow_dots(self._direction, GLYPH_TIP, GLYPH_STEP, rows=2):
                painter.setBrush(_with_alpha(tokens.PROMPT_JOYSTICK, ROW_ALPHA[row]))
                painter.drawEllipse(centre + offset, GLYPH_DOT / 2, GLYPH_DOT / 2)
        painter.setBrush(QColor(tokens.PROMPT_JOYSTICK))
        painter.drawEllipse(disc, GLYPH_DISC / 2, GLYPH_DISC / 2)
        painter.end()
