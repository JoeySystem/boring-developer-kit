"""选别的动作… (Mode页 两个弹窗 v0, 2026-11-20; design ticket 答复第 2 件).

Two steps, 480 wide, in the shared dialog shell (B v0.4):

1. Pick one: four common shortcuts on top (copy, paste, undo, redo: click one,
   then 用这个), then ten kinds in two columns. A kind this control can never
   use is left out; one that works under another condition is greyed with the
   reason. 什么都不做 is picked like a common shortcut, with no second step.
2. Pick the exact one for that kind; 上一步 goes back, 用这个 closes the dialog
   and hands the action to the card.

Connected sequences and computer tasks only list what already exists here:
"编辑 ›" and "＋ 新建…" close the dialog and open the full page (`follow_up`).

    dialog = OtherActionDialog(parent, OtherActionContext(...))
    if dialog.exec():
        action = dialog.chosen        # or dialog.follow_up == "sequences" / "tasks"
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from controller_config.actions import (
    CONSUMER_USAGE_LABELS, ActionDefinition, action_field_choices, describe_action,
)
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.action_editor import ShortcutRecorder
from controller_config.views.ui.dialog import MEDIUM, Dialog, _WarningMark
from controller_config.views.ui.segmented import SegmentedControl

# The ten kinds, in the draft's order: (kind, title, what it does).
KINDS = (
    ("consumer", "媒体与系统", "播放暂停、音量、切歌"),
    ("mouse", "鼠标", "点按、移动光标、滚轮"),
    ("macro", "连续按键", "依次按几下、输入英文、等一会儿"),
    ("key_gesture", "单击和双击分开", "单击发一个快捷键，双击发另一个"),
    ("prompt", "快捷提示词", "发出一条存好的提示词"),
    ("profile", "换一套方案", "让设备换到另一套键位"),
    ("device", "设备功能", "切换灯光、震动、屏幕页面"),
    ("key", "手动选一个键", "录不到的键（比如 F13）在这里选"),
    ("host_action", "电脑任务", "保存选中的文字、打开应用或网址"),
    ("none", "什么都不做", "这颗键先空着"),
)
CONSUMER_ORDER = (0x00CD, 0x00B0, 0x00B1, 0x00B7, 0x00B6, 0x00B5, 0x00B3, 0x00B4, 0x00E2, 0x00E9, 0x00EA,
                  0x00B2, 0x00B8)
DEVICE_NAMES = {"lighting_toggle": "切换灯光", "haptic_toggle": "切换震动", "display_next": "切换屏幕页面",
                "macro_cancel": "停下正在发的连续按键"}
MOUSE_BUTTONS = ((1, "左键"), (2, "右键"), (4, "中键"))
MODIFIERS = ((224, "⌃ Control"), (225, "⇧ Shift"), (226, "⌥ Option"), (227, "⌘ Command"))
RIGHT_MODIFIERS = ((228, "右 ⌃"), (229, "右 ⇧"), (230, "右 ⌥"), (231, "右 ⌘"))


@dataclass
class OtherActionContext:
    """Everything the dialog shows; the main window fills it."""

    control_title: str                      # "功能键 12", "旋钮 · 顺时针"
    current: dict[str, Any]                 # the action on the key now
    definitions: tuple[ActionDefinition, ...]
    platform: str = ""
    common: tuple[tuple[str, dict[str, Any]], ...] = ()      # (name, action): copy, paste, undo, redo
    hidden: frozenset[str] = frozenset()    # kinds this control can never use
    greyed: dict[str, str] = field(default_factory=dict)     # kind → why it cannot be used now
    notices: dict[str, str] = field(default_factory=dict)    # kind → orange notice in step 2
    macros: tuple[tuple[int, str, int], ...] = ()            # (id, name, steps)
    max_macros: int = 32
    tasks: tuple[tuple[str, dict[str, Any]], ...] = ()       # (name, action) of this key's applied computer tasks
    prompts: tuple[tuple[str, int], ...] = ()                # (label, prompt id)
    profiles: tuple[tuple[str, int], ...] = ()               # (name, profile id)
    active_profile: int | None = None


def _font(size: float, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setPixelSize(max(1, round(size)))
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


class _KindTile(QAbstractButton):
    """One kind in step 1: the name, what it does (or why not, in orange-brown), "›",
    and a cream "现在" when the key does this kind now."""

    HEIGHT = 58

    def __init__(self, kind: str, title: str, note: str, *, reason: str = "", current: bool = False,
                 arrow: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind, self.title, self.note, self.reason = kind, title, note, reason
        self.current, self.arrow = current, arrow
        self.setObjectName(f"otherActionKind_{kind}")
        self.setAccessibleName(tr(title))
        self.setCheckable(kind == "none")
        self.setEnabled(not reason)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(200, self.HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        chosen = self.isChecked()
        fill = (tokens.TEXT_PRIMARY if chosen else tokens.OTHER_ACTION_TILE_OFF if self.reason
                else tokens.SURFACE_3 if self.underMouse() else tokens.SURFACE_2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(body, tokens.RADIUS_LIST_ROW, tokens.RADIUS_LIST_ROW)
        ink = tokens.SURFACE_BASE if chosen else tokens.TEXT_TERTIARY if self.reason else tokens.TEXT_PRIMARY
        soft = tokens.OTHER_ACTION_REASON if self.reason else tokens.SURFACE_3 if chosen else tokens.TEXT_SECONDARY
        right = body.right() - (26 if self.arrow else 14)
        if self.current:
            badge = QRectF(right - 40, body.center().y() - 10, 40, 20)
            painter.setBrush(QColor(tokens.TEXT_PRIMARY))
            painter.drawRoundedRect(badge, 10, 10)
            painter.setFont(_font(tokens.FONT_TAG, bold=True))
            painter.setPen(QColor(tokens.SURFACE_BASE))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, tr("现在"))
            right = badge.left() - 6
        painter.setPen(QColor(ink))
        painter.setFont(_font(13.5, bold=True))
        painter.drawText(QRectF(body.left() + 16, body.top() + 9, right - body.left() - 16, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tr(self.title))
        painter.setPen(QColor(soft))
        small = _font(11)
        painter.setFont(small)
        note = tr(self.reason or self.note)
        note = QFontMetricsF(small).elidedText(note, Qt.TextElideMode.ElideRight, right - body.left() - 16)
        painter.drawText(QRectF(body.left() + 16, body.top() + 30, right - body.left() - 16, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note)
        if self.arrow and not self.reason:
            painter.setPen(QPen(QColor(tokens.TEXT_SECONDARY), 1.4))
            centre = QPointF(body.right() - 14, body.center().y())
            painter.drawLine(centre + QPointF(-3, -4), centre)
            painter.drawLine(centre + QPointF(-3, 4), centre)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.update()
        super().leaveEvent(event)


class _ChoiceRow(QAbstractButton):
    """A row to pick in step 2: name, a second line, an optional "编辑 ›", a radio."""

    edit_clicked = Signal()

    def __init__(self, title: str, note: str = "", *, edit: bool = False, disabled_note: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title, self.note, self.edit = title, note, edit
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        if disabled_note:
            self.note = disabled_note
            self.setEnabled(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(200, 52 if self.note else 44)

    def _edit_rect(self) -> QRectF:
        return QRectF(self.width() - 92, 0, 52, self.height())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if self.edit and self._edit_rect().contains(event.position()):
            self.setDown(False)
            self.edit_clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        chosen = self.isChecked()
        painter.setPen(QPen(QColor(tokens.TEXT_PRIMARY), 1.4) if chosen else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.SURFACE_3 if chosen else tokens.SURFACE_2))
        painter.drawRoundedRect(body, 14, 14)
        enabled = self.isEnabled()
        painter.setPen(QColor(tokens.TEXT_PRIMARY if enabled else tokens.TEXT_TERTIARY))
        painter.setFont(_font(13))
        top = body.top() + (8 if self.note else 0)
        painter.drawText(QRectF(body.left() + 14, top, body.width() - 120, 20 if self.note else body.height()),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        if self.note:
            painter.setPen(QColor(tokens.TEXT_SECONDARY if enabled else tokens.TEXT_TERTIARY))
            painter.setFont(_font(11))
            painter.drawText(QRectF(body.left() + 14, top + 20, body.width() - 120, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.note)
        if self.edit:
            painter.setPen(QColor(tokens.TEXT_SECONDARY))
            painter.setFont(_font(11.5))
            painter.drawText(self._edit_rect(), Qt.AlignmentFlag.AlignCenter, f"{tr('编辑')} ›")
        centre = QPointF(body.right() - 22, body.center().y())
        painter.setPen(QPen(QColor(tokens.ACCENT if chosen else tokens.TEXT_SECONDARY), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, 8, 8)
        if chosen:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.ACCENT))
            painter.drawEllipse(centre, 4.5, 4.5)
        painter.end()


def _chip(text: str, *, object_name: str = "") -> QPushButton:
    chip = QPushButton(text)
    chip.setCheckable(True)
    chip.setProperty("otherActionChip", True)
    chip.setProperty(SKIP_TRANSLATION_PROPERTY, True)
    chip.setCursor(Qt.CursorShape.PointingHandCursor)
    if object_name:
        chip.setObjectName(object_name)
    return chip


def _notice(text: str) -> QFrame:
    """The orange-triangle notice of the dialog draft: what is wrong, what to do."""
    box = QFrame(objectName="otherActionNotice")
    row = QHBoxLayout(box)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(10)
    row.addWidget(_WarningMark(), 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(tr(text))
    label.setWordWrap(True)
    row.addWidget(label, 1)
    return box


def _flow(widgets: list[QWidget], per_row: int) -> QWidget:
    holder = QWidget()
    grid = QVBoxLayout(holder)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    for start in range(0, len(widgets), per_row):
        row = QHBoxLayout()
        row.setSpacing(8)
        for widget in widgets[start:start + per_row]:
            row.addWidget(widget)
        row.addStretch(1)
        grid.addLayout(row)
    return holder


def _caption(text: str) -> QLabel:
    label = QLabel(tr(text))
    label.setProperty("tone", "secondary")
    label.setWordWrap(True)
    return label


class OtherActionDialog(Dialog):
    """选别的动作…; `chosen` is the action picked, `follow_up` a page to open instead."""

    def __init__(self, parent: QWidget | None, context: OtherActionContext) -> None:
        super().__init__(parent, "选别的动作", step=" ", width=MEDIUM, object_name="otherActionDialog")
        self.context = context
        self.chosen: dict[str, Any] | None = None
        self.follow_up: str | None = None
        self._pending: dict[str, Any] | None = None       # what 用这个 would hand over now
        self._definitions = {definition.action_type: definition for definition in context.definitions}
        self.set_step("")
        self._step_label.hide()
        self._second: QWidget | None = None    # step 2, rebuilt for each kind
        self.back = self.add_button("上一步", "secondary", object_name="otherActionBack")
        self.use = self.add_button("用这个", "primary", object_name="otherActionUse")
        self.use.setDefault(True)
        self.back.clicked.connect(self.show_first)
        self.use.clicked.connect(self._use)
        self.first = self._build_first()
        self.body_layout().addWidget(self.first)
        self.show_first()

    # -- steps ---------------------------------------------------------------------------

    def show_first(self) -> None:
        self.title_label.setText(tr("选别的动作"))
        self._step_label.hide()
        self.back.hide()
        self._drop_second()
        self.first.show()
        self._set_pending(self._common_pick())
        self._refit()

    def show_kind(self, kind: str) -> None:
        """Step 2 for one kind (also used by tests)."""
        page = self._build_kind(kind)
        self._drop_second()
        self.first.hide()
        self._second = page
        self.body_layout().addWidget(page)
        page.show()   # a child added to a shown dialog stays hidden until shown
        self.title_label.setText(tr(dict((k, t) for k, t, _ in KINDS)[kind]))
        self.set_step(tr("选别的动作"))
        self._step_label.show()
        self.back.show()
        self._refit()

    def _refit(self) -> None:
        """Each step takes its own height (the draft: 601 for step 1, 326–432 for step 2)."""
        if not self.isVisible():
            return
        centre = self.geometry().center()
        self.fit()
        self.move(centre.x() - self.width() // 2, centre.y() - self.height() // 2)

    def _drop_second(self) -> None:
        if self._second is not None:
            self.body_layout().removeWidget(self._second)
            self._second.hide()
            self._second.deleteLater()
            self._second = None

    def current_step(self) -> QWidget:
        return self._second if self._second is not None else self.first

    def _set_pending(self, action: dict[str, Any] | None) -> None:
        self._pending = copy.deepcopy(action) if action is not None else None
        self.use.setEnabled(action is not None)

    def _use(self) -> None:
        if self._pending is None:
            return
        self.chosen = copy.deepcopy(self._pending)
        self.accept()

    def _open_page(self, page: str) -> None:
        self.follow_up = page
        self.accept()

    # -- step 1 ----------------------------------------------------------------------------

    def _build_first(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        context = self.context
        current = describe_action(context.current, platform=context.platform, compact=True)
        line = QLabel(f"{tr(context.control_title)}　{tr('现在：')}{tr(current)}", objectName="otherActionNow")
        line.setProperty("tone", "secondary")
        line.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        box.addWidget(line)
        self._common = QButtonGroup(self)
        self._common.setExclusive(False)
        chips = []
        if context.common:
            box.addWidget(_caption("常用"))
            for index, (name, action) in enumerate(context.common):
                keys = describe_action(action, platform=context.platform, compact=True)
                chip = _chip(f"{tr(name)}  {keys}", object_name=f"otherActionCommon_{index}")
                chip.setProperty("action", action)
                self._common.addButton(chip, index)
                chips.append(chip)
            box.addWidget(_flow(chips, 4))
            self._common.buttonClicked.connect(self._common_clicked)
        box.addWidget(_caption("换成别的种类"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.tiles: dict[str, _KindTile] = {}
        shown = [(k, t, n) for k, t, n in KINDS if k not in context.hidden]
        now = context.current.get("type")
        for index, (kind, title, note) in enumerate(shown):
            tile = _KindTile(kind, title, note, reason=context.greyed.get(kind, ""),
                             current=kind == now and kind not in {"key", "none"}, arrow=kind != "none")
            if kind == "none":
                tile.clicked.connect(self._none_clicked)
            else:
                tile.clicked.connect(lambda _checked=False, value=kind: self.show_kind(value))
            self.tiles[kind] = tile
            grid.addWidget(tile, index // 2, index % 2)
        box.addLayout(grid)
        return page

    def _common_pick(self) -> dict[str, Any] | None:
        for button in self._common.buttons():
            if button.isChecked():
                return button.property("action")
        none = self.tiles.get("none")
        if none is not None and none.isChecked():
            return {"type": "none"}
        return None

    def _common_clicked(self, clicked: QAbstractButton) -> None:
        for button in self._common.buttons():
            if button is not clicked:
                button.setChecked(False)
        if "none" in self.tiles:
            self.tiles["none"].setChecked(False)
        self._set_pending(self._common_pick())

    def _none_clicked(self) -> None:
        for button in self._common.buttons():
            button.setChecked(False)
        self._set_pending(self._common_pick())

    # -- step 2 ----------------------------------------------------------------------------

    def _build_kind(self, kind: str) -> QWidget:
        page = QWidget(objectName=f"otherActionStep_{kind}")
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        self._set_pending(None)
        notice = self.context.notices.get(kind)
        if notice:
            box.addWidget(_notice(notice))
        builder = getattr(self, f"_kind_{kind}")
        builder(box, blocked=bool(notice))
        return page

    def _same_kind(self) -> dict[str, Any] | None:
        current = self.context.current
        return current if current.get("type") else None

    def _kind_consumer(self, box: QVBoxLayout, *, blocked: bool) -> None:
        box.addWidget(_caption("按下这颗键，电脑收到下面这个媒体键："))
        group = QButtonGroup(self)
        chips = []
        now = self._same_kind()
        for usage in CONSUMER_ORDER:
            chip = _chip(tr(CONSUMER_USAGE_LABELS[usage]), object_name=f"otherActionConsumer_{usage}")
            group.addButton(chip, usage)
            chips.append(chip)
            if now and now.get("type") == "consumer" and now.get("usage") == usage:
                chip.setChecked(True)
                self._set_pending({"type": "consumer", "usage": usage})
        group.idClicked.connect(lambda usage: self._set_pending({"type": "consumer", "usage": usage}))
        box.addWidget(_flow(chips, 5))

    def _kind_mouse(self, box: QVBoxLayout, *, blocked: bool) -> None:
        fields = {item.name: item for item in self._definitions["mouse"].fields}
        low = min((item.minimum for item in fields.values() if item.minimum is not None and item.name != "button"),
                  default=-127)
        high = max((item.maximum for item in fields.values() if item.maximum is not None and item.name != "button"),
                   default=127)
        segment = SegmentedControl((("click", tr("点按")), ("move", tr("移动光标")), ("wheel", tr("滚轮"))), "move")
        segment.setObjectName("otherActionMouseKind")
        box.addWidget(segment)
        pages = QStackedWidget()
        clicks = QButtonGroup(self)
        click_chips = []
        for value, label in MOUSE_BUTTONS:
            chip = _chip(tr(label), object_name=f"otherActionMouseButton_{value}")
            clicks.addButton(chip, value)
            click_chips.append(chip)
        click_page = QWidget()
        click_box = QVBoxLayout(click_page)
        click_box.setContentsMargins(0, 0, 0, 0)
        click_box.addWidget(_flow(click_chips, 3))
        pages.addWidget(click_page)
        inputs: dict[str, QLineEdit] = {}

        def pair(names: tuple[tuple[str, str], ...]) -> QWidget:
            holder = QWidget()
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            for column, (name, label) in enumerate(names):
                grid.addWidget(_caption(label), 0, column)
                edit = QLineEdit("0", objectName=f"otherActionMouse_{name}")
                inputs[name] = edit
                grid.addWidget(edit, 1, column)
            return holder

        pages.addWidget(pair((("x", "往右（负数往左）"), ("y", "往下（负数往上）"))))
        pages.addWidget(pair((("wheel", "往上滚（负数往下）"), ("pan", "往右滚（负数往左）"))))
        box.addWidget(pages)
        error = QLabel(objectName="otherActionMouseError")
        error.setProperty("tone", "warning")
        error.hide()
        box.addWidget(error)
        box.addWidget(_caption(tr("按一下移动多少，范围 {low} 到 {high}。").format(low=low, high=high)))
        if "x" in inputs:
            inputs["x"].setText("20")

        def update(*_args) -> None:
            kind = segment.value()
            pages.setCurrentIndex(("click", "move", "wheel").index(kind))
            # The schema wants all five fields; the ones this kind does not use are 0.
            action: dict[str, Any] = {"type": "mouse", "button": 0, "x": 0, "y": 0, "wheel": 0, "pan": 0}
            if kind == "click":
                error.hide()
                button = clicks.checkedId()
                self._set_pending({**action, "button": button} if button > 0 else None)
                return
            names = ("x", "y") if kind == "move" else ("wheel", "pan")
            bad = False
            for name in names:
                edit = inputs[name]
                try:
                    value = int(edit.text().strip() or "0")
                except ValueError:
                    value = None
                wrong = value is None or not low <= value <= high
                edit.setProperty("invalid", wrong)
                edit.style().unpolish(edit)
                edit.style().polish(edit)
                bad = bad or wrong
                if not wrong:
                    action[name] = value
            error.setText(tr("要在 {low} 到 {high} 之间").format(low=low, high=high))
            error.setVisible(bad)
            self._set_pending(None if bad or not any(action[name] for name in names) else action)

        segment.valueChanged.connect(update)
        clicks.idClicked.connect(update)
        for edit in inputs.values():
            edit.textChanged.connect(update)
        now = self._same_kind()
        if now and now.get("type") == "mouse":
            if now.get("button"):
                segment.setValue("click")
                checked = clicks.button(int(now["button"]))
                if checked is not None:
                    checked.setChecked(True)
            elif now.get("wheel") or now.get("pan"):
                segment.setValue("wheel")
                inputs["wheel"].setText(str(now.get("wheel", 0)))
                inputs["pan"].setText(str(now.get("pan", 0)))
            else:
                segment.setValue("move")
                inputs["x"].setText(str(now.get("x", 0)))
                inputs["y"].setText(str(now.get("y", 0)))
        update()

    def _kind_key_gesture(self, box: QVBoxLayout, *, blocked: bool) -> None:
        now = self._same_kind()
        gesture = now if now and now.get("type") == "key_gesture" else None
        single = {"type": "key", "usage": (gesture or {}).get("usage", 6), "modifiers": (gesture or {}).get("modifiers", [227])}
        double = {"type": "key", "usage": (gesture or {}).get("double_usage", 25),
                  "modifiers": (gesture or {}).get("double_modifiers", [227])}
        state = {"single": single, "double": double}

        def row(name: str, title: str) -> QWidget:
            holder = QFrame(objectName="otherActionGestureRow")
            layout = QVBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addWidget(QLabel(tr(title)))
            recorder = ShortcutRecorder(state[name], platform=self.context.platform)
            recorder.setObjectName(f"otherActionGesture_{name}")

            def recorded(action: dict, key=name) -> None:
                state[key] = action
                apply()

            recorder.shortcut_recorded.connect(recorded)
            layout.addWidget(recorder)
            return holder

        def apply() -> None:
            self._set_pending({"type": "key_gesture", "usage": state["single"].get("usage", 0),
                               "modifiers": list(state["single"].get("modifiers", [])),
                               "double_usage": state["double"].get("usage", 0),
                               "double_modifiers": list(state["double"].get("modifiers", []))})

        box.addWidget(row("single", "单击"))
        box.addWidget(row("double", "双击"))
        box.addWidget(_caption("有了双击，单击会晚一点点才发出去：要等一下看你是不是还会按第二下。"))
        apply()

    def _radio_rows(self, box: QVBoxLayout, rows: list[tuple[str, str, dict[str, Any] | None, bool]],
                    *, edit: str | None = None, disabled: bool = False) -> None:
        """rows: (title, note, action or None for a row that cannot be picked, is current)."""
        group = QButtonGroup(self)
        for index, (title, note, action, current) in enumerate(rows):
            choice = _ChoiceRow(title, note, edit=edit is not None,
                                disabled_note=note if action is None else "")
            choice.setObjectName(f"otherActionChoice_{index}")
            choice.setEnabled(action is not None and not disabled)
            if edit is not None:
                choice.edit_clicked.connect(lambda page=edit: self._open_page(page))
            group.addButton(choice, index)
            if current and action is not None and not disabled:
                choice.setChecked(True)
                self._set_pending(action)
            if action is not None:
                choice.clicked.connect(lambda _checked=False, value=action: self._set_pending(value))
            box.addWidget(choice)

    def _empty(self, box: QVBoxLayout, title: str, note: str, button: str = "", page: str = "") -> None:
        holder = QWidget(objectName="otherActionEmpty")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(6)
        heading = QLabel(tr(title), objectName="otherActionEmptyTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        detail = _caption(note)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail)
        if button:
            row = QHBoxLayout()
            row.addStretch(1)
            new = QPushButton(tr(button), objectName="otherActionNew")
            new.setProperty("boringButton", "secondary")
            new.setProperty("boringButtonSize", "small")
            new.clicked.connect(lambda: self._open_page(page))
            row.addWidget(new)
            row.addStretch(1)
            layout.addLayout(row)
        box.addWidget(holder)

    def _footer(self, box: QVBoxLayout, new_text: str, page: str, capacity: str, *, can_add: bool) -> None:
        row = QHBoxLayout()
        new = QPushButton(f"＋ {tr(new_text)} ›", objectName="otherActionNew")
        new.setProperty("boringButton", "text")
        new.setEnabled(can_add)
        new.clicked.connect(lambda: self._open_page(page))
        row.addWidget(new)
        row.addStretch(1)
        if capacity:
            row.addWidget(_caption(capacity))
        box.addLayout(row)

    def _kind_macro(self, box: QVBoxLayout, *, blocked: bool) -> None:
        context = self.context
        if not context.macros:
            self._empty(box, "还没有连续按键", "一条连续按键可以依次按几下、输入一段英文、中间等一会儿。",
                        "新建连续按键…", "sequences")
            return
        now = self._same_kind()
        current_id = now.get("macro_id") if now and now.get("type") == "macro" else None
        self._radio_rows(box, [(name, tr("{count} 步").format(count=steps),
                                {"type": "macro", "macro_id": macro_id}, macro_id == current_id)
                               for macro_id, name, steps in context.macros], edit="sequences")
        full = len(context.macros) >= context.max_macros
        capacity = (tr("已经存满 {count} 条，删掉一条才能新建").format(count=context.max_macros) if full
                    else f"{len(context.macros)} / {context.max_macros} {tr('条')}")
        self._footer(box, "新建连续按键…", "sequences", capacity, can_add=not full)

    def _kind_prompt(self, box: QVBoxLayout, *, blocked: bool) -> None:
        if not self.context.prompts:
            self._empty(box, "还没有存好的提示词", "先在“快捷提示词”里存几条，再回来选。")
            return
        box.addWidget(_caption("按下这颗键，把这条提示词发到正在用的 AI 输入框："))
        now = self._same_kind()
        current_id = now.get("prompt_id") if now and now.get("type") == "prompt" else None
        self._radio_rows(box, [(label, "", {"type": "prompt", "prompt_id": prompt_id}, prompt_id == current_id)
                               for label, prompt_id in self.context.prompts])

    def _kind_profile(self, box: QVBoxLayout, *, blocked: bool) -> None:
        box.addWidget(_caption("按下这颗键，设备换到选中的那套方案："))
        now = self._same_kind()
        current_id = now.get("profile_id") if now and now.get("type") == "profile" else None
        rows = []
        for name, profile_id in self.context.profiles:
            if profile_id == self.context.active_profile:
                rows.append((name, tr("现在这套"), None, False))
            else:
                rows.append((name, "", {"type": "profile", "profile_id": profile_id}, profile_id == current_id))
        self._radio_rows(box, rows)

    def _kind_device(self, box: QVBoxLayout, *, blocked: bool) -> None:
        names = next((item.choices for item in self._definitions["device"].fields if item.name == "name"), ())
        now = self._same_kind()
        current = now.get("name") if now and now.get("type") == "device" else None
        self._radio_rows(box, [(tr(DEVICE_NAMES.get(str(name), str(name))), "", {"type": "device", "name": name},
                                name == current) for name in names])

    def _kind_key(self, box: QVBoxLayout, *, blocked: bool) -> None:
        definition = self._definitions["key"]
        usage_field = next(item for item in definition.fields if item.name == "usage")
        now = self._same_kind()
        action = copy.deepcopy(now) if now and now.get("type") == "key" else {"type": "key", "usage": 104, "modifiers": []}
        box.addWidget(_caption("键"))
        keys = QComboBox(objectName="otherActionKey")
        keys.setMaxVisibleItems(18)
        for label, usage in action_field_choices("key", usage_field, action.get("usage"), platform=self.context.platform):
            keys.addItem(tr(label), usage)
        keys.setCurrentIndex(max(0, keys.findData(action.get("usage"))))
        box.addWidget(keys)
        box.addWidget(_caption("同时按住"))
        held = set(action.get("modifiers", []))
        group: dict[int, QPushButton] = {}
        left = []
        for usage, label in MODIFIERS:
            chip = _chip(label, object_name=f"otherActionModifier_{usage}")
            chip.setChecked(usage in held)
            group[usage] = chip
            left.append(chip)
        box.addWidget(_flow(left, 4))
        split = QPushButton(f"{tr('左右分开选')} ›", objectName="otherActionSplit")
        split.setProperty("boringButton", "text")
        split.setCheckable(True)
        right_row = []
        for usage, label in RIGHT_MODIFIERS:
            chip = _chip(tr(label), object_name=f"otherActionModifier_{usage}")
            chip.setChecked(usage in held)
            group[usage] = chip
            right_row.append(chip)
        right_holder = _flow(right_row, 4)
        right_holder.setVisible(any(usage in held for usage, _ in RIGHT_MODIFIERS))
        split.setChecked(right_holder.isVisible())
        split.toggled.connect(right_holder.setVisible)
        split.toggled.connect(lambda _on: self._refit())
        box.addWidget(split, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(right_holder)
        preview = QFrame(objectName="otherActionPreview")
        row = QHBoxLayout(preview)
        row.setContentsMargins(14, 10, 14, 10)
        row.addWidget(_caption("按下这颗键会发出"))
        row.addStretch(1)
        result = QLabel(objectName="otherActionPreviewKeys")
        result.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        row.addWidget(result)
        box.addWidget(preview)

        def update(*_args) -> None:
            chosen = {"type": "key", "usage": keys.currentData(),
                      "modifiers": sorted(usage for usage, chip in group.items() if chip.isChecked())}
            result.setText(describe_action(chosen, platform=self.context.platform, compact=True))
            self._set_pending(chosen)

        keys.currentIndexChanged.connect(update)
        for chip in group.values():
            chip.toggled.connect(update)
        update()

    def _kind_host_action(self, box: QVBoxLayout, *, blocked: bool) -> None:
        context = self.context
        now = self._same_kind()
        current_id = now.get("action_id") if now and now.get("type") == "host_action" else None
        if blocked and not context.tasks:
            return
        if not context.tasks and not blocked:
            self._empty(box, "还没有电脑任务", "电脑任务可以保存选中的文字、打开应用或网址。", "新建电脑任务…", "tasks")
            return
        self._radio_rows(box, [(name, "", action, action.get("action_id") == current_id)
                               for name, action in context.tasks], edit="tasks", disabled=blocked)
        self._footer(box, "新建电脑任务…", "tasks", "", can_add=not blocked)
