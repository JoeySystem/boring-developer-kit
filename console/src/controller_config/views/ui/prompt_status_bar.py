"""The Prompt page's top row: one line of status above the 4×2 wall.

Spec: the designer, Prompt页-定稿-v1-20261118-图/3-顶部一行和编辑区的各种样子.png
("顶部一行", six rows) and 5-小图形和数值表.png (宽 848、高 44、圆角 22; right side,
in order: "看演示", 重新读取, 诊断信息). Three-language text:
Prompt页-定稿-v1-界面文字中英日对照-20261118.md.

    READY          ● 可以使用 | 长按 12 号键打开 → 摇杆选 → 按旋钮确认 · 3 号键取消   [▶ 看演示] (↻) (∿)
    PREPARING      ◌ 正在准备…   (neutral grey, no warning colour)                     (↻) (∿)
    PERMISSION     ▲ 还差一步 | 允许 BORING Console 控制这台电脑，按键才能把提示词打出来  [去允许] (↻) (∿)
    BLUETOOTH      ● 现在用蓝牙连接 | BLUETOOTH_TEXT  (DRAFT, the firmware owner checks)           [说明] (↻) (∿)
    DISCONNECTED   ○ 设备没连上 | 现在改的先存在这台电脑，连上后再存进设备                      (∿)
    READY_DIRECT   ● 可以使用 | 在 Mode 里把摇杆方向绑到提示词，拨一下就打出来               (↻) (∿)
    HELPER_OFFLINE ○ 暂时打不出提示词 | 点右边的诊断信息看原因  (DRAFT, not on the spec)  (↻) (∿)

Which status when: `controller_config.prompt_status_state` (pure, table in its
docstring). `apply()` takes `PromptStatusInputs` or a `PromptStatusKind`.

Permission: give the bar a `PermissionWatcher` (kinds may include
ACCESSIBILITY) and it answers `permission_missing` itself, the same way as
`paste_permission_missing()` (missing only when macOS says NOT_TRUSTED).
〔去允许〕 calls `watcher.open_settings(ACCESSIBILITY)`; when the grant comes
back the watcher says so and the row turns back to "可以使用" by itself.

Signals (the bar never runs anything itself, except 〔去允许〕):

    demo_requested         ▶ 看演示   -> swap the cross for the "怎么用" block
    refresh_requested      重新读取   -> read the four prompts from the device again
    diagnostics_requested  诊断信息   -> open `PromptDiagnostics` in a `Dialog`
    explain_requested      说明       -> what works over Bluetooth (the firmware owner writes it)
    kind_changed(str)      the status changed (PromptStatusKind value)

Width follows the wall (a tile 150-200 wide: the row is MIN_WIDTH-MAX_WIDTH).
The status word and the buttons always fit; the sentence in the middle is cut
with "…" when there is no room, and the pointer resting on it shows it whole.

Object names: promptStatusBar (the row), promptStatusIcon, promptStatusWord,
promptStatusDivider, promptStatusText, promptStatusDemo, promptStatusAllow,
promptStatusExplain, promptStatusRefresh, promptStatusDiagnostics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from controller_config import desktop_permissions as perm
from controller_config.i18n import set_translatable_accessible_name, set_translatable_text
from controller_config.i18n import translate_ui_text as tr
from controller_config.prompt_status_state import (
    PromptStatusInputs, PromptStatusKind, prompt_status_kind,
)
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.button import Button
from controller_config.views.ui.page_motion import CrossFade, clear_grab
from controller_config.views.ui.permission_row import PermissionWatcher
from controller_config.views.ui.status_icon import StatusIcon

MIN_WIDTH = 4 * 150 + 3 * 16   # 648: wall tiles 150 wide
MAX_WIDTH = 4 * 200 + 3 * 16   # 848: wall tiles 200 wide (the spec)
ICON_SLOT = 16                 # the status shape sits centred in a 16 × 16 slot
LEFT_PADDING = 13              # + the 1 px outline: dot centre 22 from the left edge (spec)
RIGHT_PADDING = 6              # + the 1 px outline = (44 - 30) / 2: round buttons sit evenly in the row
GAP = 10                       # icon -> word -> divider -> sentence
BUTTON_GAP = 6
DIVIDER_HEIGHT = 16

# DRAFT: the spec leaves this sentence to the firmware owner ("这一句等 the firmware owner 在实机上核对蓝牙下能做什么以后再写").
# The firmware's prompt trigger is the USB helper event path (`prompt_trigger_usb`).
BLUETOOTH_TEXT = "插上 USB 线，按键才能把提示词打出来"


@dataclass(frozen=True)
class _Look:
    icon: str                  # StatusIcon state
    icon_size: int
    word: str                  # the status word, bold
    text: str = ""             # the sentence after the divider
    action: str = ""           # "demo" | "allow" | "explain" | ""
    refresh: bool = True
    quiet: bool = False        # neutral grey word (PREPARING)
    waiting_for: str = "you"


LOOKS: dict[PromptStatusKind, _Look] = {
    PromptStatusKind.READY: _Look(
        "ok", tokens.CHECK_SIZE, "可以使用", "长按 12 号键打开 → 摇杆选 → 按旋钮确认 · 3 号键取消", action="demo"),
    PromptStatusKind.PREPARING: _Look("busy", 14, "正在准备…", quiet=True),
    PromptStatusKind.PERMISSION: _Look(
        "error", 16, "还差一步", "允许 BORING Console 控制这台电脑，按键才能把提示词打出来", action="allow"),
    # Spec: a grey dot. StatusIcon has no grey dot; "waiting for the device"
    # (cream) is the nearest of the five shapes. the designer to confirm.
    PromptStatusKind.BLUETOOTH: _Look(
        "wait", 8, "现在用蓝牙连接", BLUETOOTH_TEXT, action="explain", waiting_for="device"),
    PromptStatusKind.DISCONNECTED: _Look(
        "off", 8, "设备没连上", "现在改的先存在这台电脑，连上后再存进设备", refresh=False),
    PromptStatusKind.READY_DIRECT: _Look(
        "ok", tokens.CHECK_SIZE, "可以使用", "在 Mode 里把摇杆方向绑到提示词，拨一下就打出来"),
    # DRAFT (not on the spec): the helper is off for good on this device or
    # platform; the reason (listener.message) is in the diagnostics.
    PromptStatusKind.HELPER_OFFLINE: _Look("off", 8, "暂时打不出提示词", "点右边的诊断信息看原因"),
}

DEMO_TEXT = "看演示"
ALLOW_TEXT = "去允许"
EXPLAIN_TEXT = "说明"
REFRESH_TEXT = "重新读取"
DIAGNOSTICS_TEXT = "诊断信息"


class ElidedLabel(QLabel):
    """One line that ends in "…" when it does not fit; the tooltip then has it all."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # The tooltip is the label's own (already translated) text, not a source string.
        self.setProperty("boringI18nSkip_toolTip", True)

    def setText(self, text: str) -> None:  # noqa: N802 - keeps the tooltip in step
        super().setText(text)
        self._update_tooltip()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(0, super().minimumSizeHint().height())

    def elided_text(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width())

    def is_elided(self) -> bool:
        return bool(self.text()) and self.elided_text() != self.text()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._update_tooltip()

    def changeEvent(self, event) -> None:  # noqa: N802 - font changes from the stylesheet
        super().changeEvent(event)
        self._update_tooltip()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        self.style().drawItemText(painter, self.contentsRect(),
                                  int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                                  self.palette(), self.isEnabled(), self.elided_text(), self.foregroundRole())
        painter.end()

    def _update_tooltip(self) -> None:
        tip = self.text() if self.is_elided() else ""
        if self.toolTip() != tip:
            self.setToolTip(tip)


class RoundIconButton(QPushButton):
    """A 30 round outline button with a drawn glyph: "refresh" or "pulse"."""

    GLYPHS = ("refresh", "pulse")

    def __init__(self, glyph: str, name: str, *, object_name: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if glyph not in self.GLYPHS:
            raise ValueError(f"Unknown glyph: {glyph}")
        self._glyph = glyph
        self.setProperty("statusBarRound", True)
        if object_name:
            self.setObjectName(object_name)
        self.setFixedSize(tokens.STATUS_BAR_ROUND, tokens.STATUS_BAR_ROUND)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoDefault(False)
        set_translatable_accessible_name(self, name)
        self.setToolTip(tr(name))
        self.setProperty("boringI18nSource_toolTip", name)
        self.setProperty("boringI18nRendered_toolTip", self.toolTip())

    def glyph(self) -> str:
        return self._glyph

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(tokens.STATUS_BAR_ROUND, tokens.STATUS_BAR_ROUND)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().paintEvent(event)  # outline and hover from the stylesheet
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(tokens.TEXT_PRIMARY if self.isEnabled() else tokens.TEXT_TERTIARY)
        pen = QPen(colour, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        centre = QRectF(self.rect()).center()
        if self._glyph == "refresh":
            self._paint_refresh(painter, centre, colour)
        else:
            self._paint_pulse(painter, centre)
        painter.end()

    @staticmethod
    def _paint_refresh(painter: QPainter, centre: QPointF, colour: QColor) -> None:
        radius = 5.5
        box = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
        # An open circle from the top-right round to the top, arrow at its start.
        start, span = 40, 290
        painter.drawArc(box, start * 16, span * 16)
        angle = math.radians(start)
        tip = QPointF(centre.x() + radius * math.cos(angle), centre.y() - radius * math.sin(angle))
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        head = QPolygonF([tip + QPointF(-3.6, -1.6), tip + QPointF(1.4, -3.0), tip + QPointF(0.6, 1.8)])
        painter.drawPolygon(head)

    @staticmethod
    def _paint_pulse(painter: QPainter, centre: QPointF) -> None:
        x, y = centre.x(), centre.y()
        path = QPainterPath(QPointF(x - 6.5, y))
        for dx, dy in ((-3.2, 0), (-1.4, -4.2), (1.2, 4.2), (3.0, 0), (6.5, 0)):
            path.lineTo(QPointF(x + dx, y + dy))
        painter.drawPath(path)


def _play_icon() -> QIcon:
    """The small ▶ in front of "看演示"."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.TEXT_PRIMARY))
    painter.drawPolygon(QPolygonF([QPointF(5, 4), QPointF(12, 8), QPointF(5, 12)]))
    painter.end()
    return QIcon(pixmap)


class PromptStatusBar(QFrame):
    """`PromptStatusBar(watcher)`, then `apply(PromptStatusInputs(...))` whenever things change."""

    demo_requested = Signal()
    refresh_requested = Signal()
    diagnostics_requested = Signal()
    explain_requested = Signal()
    kind_changed = Signal(str)

    def __init__(self, watcher: PermissionWatcher | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("promptStatusBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(tokens.STATUS_BAR_HEIGHT)
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.watcher = watcher
        self._inputs = PromptStatusInputs(connected=True)
        self._kind: PromptStatusKind | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(LEFT_PADDING, 0, RIGHT_PADDING, 0)
        row.setSpacing(GAP)
        self._icon_slot = QWidget(objectName="promptStatusIconSlot")
        self._icon_slot.setFixedSize(ICON_SLOT, ICON_SLOT)
        slot = QHBoxLayout(self._icon_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        self.icon = StatusIcon("busy", size=14)
        self.icon.setObjectName("promptStatusIcon")
        slot.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._icon_slot)
        self.word = QLabel(objectName="promptStatusWord")
        self.word.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row.addWidget(self.word)
        self.divider = QFrame(objectName="promptStatusDivider")
        self.divider.setFixedSize(1, DIVIDER_HEIGHT)
        row.addWidget(self.divider)
        self.text = ElidedLabel()
        self.text.setObjectName("promptStatusText")
        row.addWidget(self.text, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(BUTTON_GAP)
        self.demo_button = Button(DEMO_TEXT, "secondary", size="small", object_name="promptStatusDemo")
        self.demo_button.setIcon(_play_icon())
        self.demo_button.setIconSize(QSize(12, 12))
        self.allow_button = Button(ALLOW_TEXT, "primary", size="small", object_name="promptStatusAllow")
        self.explain_button = Button(EXPLAIN_TEXT, "secondary", size="small", object_name="promptStatusExplain")
        self.refresh_button = RoundIconButton("refresh", REFRESH_TEXT, object_name="promptStatusRefresh")
        self.diagnostics_button = RoundIconButton("pulse", DIAGNOSTICS_TEXT, object_name="promptStatusDiagnostics")
        for button in (self.demo_button, self.allow_button, self.explain_button,
                       self.refresh_button, self.diagnostics_button):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            buttons.addWidget(button)
        row.addLayout(buttons)

        self.demo_button.clicked.connect(self.demo_requested)
        self.explain_button.clicked.connect(self.explain_requested)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.diagnostics_button.clicked.connect(self.diagnostics_requested)
        self.allow_button.clicked.connect(self._allow)
        if watcher is not None:
            watcher.changed.connect(self._permission_changed)
        self.apply(PromptStatusKind.PREPARING)

    # -- state -------------------------------------------------------------------

    def kind(self) -> PromptStatusKind:
        return self._kind

    def inputs(self) -> PromptStatusInputs:
        return self._inputs

    def permission_missing(self) -> bool | None:
        """The watcher's answer (None without a watcher): as `paste_permission_missing()`."""
        if self.watcher is None:
            return None
        try:
            return self.watcher.status(perm.ACCESSIBILITY) == perm.NOT_TRUSTED
        except KeyError:  # a watcher made for other permissions only
            return None

    def apply(self, state: PromptStatusInputs | PromptStatusKind | str) -> PromptStatusKind:
        """Show the row for `state`; returns the status it settled on.

        With a watcher, `permission_missing` in the inputs is replaced by what
        the watcher read last.
        """
        if isinstance(state, PromptStatusInputs):
            missing = self.permission_missing()
            if missing is not None:
                state = replace(state, permission_missing=missing)
            self._inputs = state
            kind = prompt_status_kind(state)
        else:
            kind = PromptStatusKind(state)
        if kind is self._kind:
            return kind
        previous = self._kind
        came_back = (previous is PromptStatusKind.PERMISSION and LOOKS[kind].icon == "ok"
                     and self.isVisible() and not motion.reduces_motion())
        before = clear_grab(self, own_background=True) if came_back else None
        self._kind = kind
        self._show(LOOKS[kind])
        if before is not None:
            # 授权回来以后 (-15 第 5 处, 乙): cross-fade 短, the check pops.
            CrossFade(self, before, tokens.MOTION_SHORT_MS)
            self.icon.pop()
        self.kind_changed.emit(kind.value)
        return kind

    def visible_buttons(self) -> list[str]:
        """Object names of the buttons this status shows, left to right."""
        return [b.objectName() for b in (self.demo_button, self.allow_button, self.explain_button,
                                         self.refresh_button, self.diagnostics_button)
                if not b.isHidden()]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(MAX_WIDTH, tokens.STATUS_BAR_HEIGHT)

    # -- inside --------------------------------------------------------------------

    def _show(self, look: _Look) -> None:
        if self.icon.width() != look.icon_size:
            old = self.icon
            self.icon = StatusIcon(look.icon, size=look.icon_size, waiting_for=look.waiting_for)
            self.icon.setObjectName("promptStatusIcon")
            self._icon_slot.layout().replaceWidget(old, self.icon)
            old.setParent(None)
            old.deleteLater()
        else:
            self.icon.set_state(look.icon, waiting_for=look.waiting_for)
        set_translatable_text(self.word, look.word)
        self.word.setProperty("quiet", look.quiet)
        self.word.style().unpolish(self.word)
        self.word.style().polish(self.word)
        if look.text:
            set_translatable_text(self.text, look.text)
        else:
            self.text.setProperty("boringI18nSource_text", None)
            self.text.setText("")
        self.divider.setVisible(bool(look.text))  # the empty sentence still fills the middle
        self.demo_button.setVisible(look.action == "demo")
        self.allow_button.setVisible(look.action == "allow")
        self.explain_button.setVisible(look.action == "explain")
        self.refresh_button.setVisible(look.refresh)
        self.layout().invalidate()

    def _allow(self) -> None:
        if self.watcher is not None:
            self.watcher.open_settings(perm.ACCESSIBILITY)

    def _permission_changed(self) -> None:
        self.apply(self._inputs)
