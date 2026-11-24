"""设置与维护的方块墙：左侧五个分组图标，右边一面固定大小的方块墙。

版式出自 the designer 2026-11-17 定稿的视觉稿（设置与维护-小组件墙-v1）：
一格 176，间距 16，每个分组一面 4 格宽 × 2 格高的墙，方块位置写死、不随窗口重排。
方块里的排法：左上是图标，右上是能做的那一个动作，下面是名字和现在的状态。
"""
from __future__ import annotations

import sys
from datetime import datetime

from PySide6.QtCore import QByteArray, QEvent, QObject, QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from controller_config.views.ui import notice
from controller_config import desktop_permissions as perm
from controller_config.codex_agent_focus import installed_agent_clients
from controller_config.codex_usage import CodexUsageStatus, resolve_codex_executable
from controller_config.i18n import (
    SKIP_TRANSLATION_PROPERTY,
    set_translatable_text,
    translate_ui_text as tr,
)
from controller_config.style import tokens
from controller_config.views.ui.motion import Pop
from controller_config.views.ui.page_motion import CrossFade, clear_grab
from controller_config.views.ui import dialog as dialog_shell
from controller_config.views.ui import permission_row
from controller_config.viewmodels import desktop_agents as link_codes

# 一格的大小。v1 定稿是 176；the designer 2026-11-18 看成品后定成 200（大 14%），窗口里不显得空。
UNIT = 200
GAP = 16
PAD = 16
RADIUS = 20
COLUMNS = 4
ROWS = 2
# 屏幕很小、窗口被压到比墙还窄时，整面墙等比例缩一点（位置不变），最小缩到 MIN_UNIT。
MIN_UNIT = 150
# 窗口比墙大时方块不再变大、墙居中（视觉稿里的"甲"）。
# 要改成整面墙跟着窗口放大（"乙"），把这个上限调大即可，例如 250。
MAX_UNIT = UNIT
TITLE_BLOCK = 34
HOME_ICON_PREVIEW = 64  # the home picture circle on 灯光与反馈's 屏幕图标 tile

# 颜色都在 style/tokens.py；这里的短名字留给本文件用。
TILE = tokens.WALL_TILE
CHIP = tokens.WALL_CHIP
CREAM = tokens.WALL_TEXT
TEXT = tokens.WALL_TEXT
MUTED = tokens.TEXT_SECONDARY
RAIL = tokens.WALL_RAIL
ORANGE = tokens.ACCENT
GREEN = tokens.WALL_STATUS_OK

# 分组顺序和名字是 2026-11-17 定的；键名沿用原来的，"system" 现在显示成「通用」。
SECTIONS = (
    ("device", "设备与连接", "device"),
    ("ai", "AI 状态灯", "lamps"),
    # the designer 灯光页 v0.2 定的组名（2026-11-18）。
    ("appearance", "灯光与反馈", "sun"),
    ("system", "通用", "sliders"),
    ("updates", "更新", "update"),
)
SECTION_TITLES = {key: label for key, label, _icon in SECTIONS}


def _square(x: float, y: float) -> str:
    return (f'<rect x="{x}" y="{y}" width="3.4" height="3.4" rx="1" '
            'fill="currentColor" stroke="none"/>')


# 6 颗状态灯键在 BORING Mist 上的排法：上排中间 2 颗，下排 4 颗。
_LAMP_COLUMNS = (0.4, 5.0, 9.6, 14.2)
ICONS = {
    "device": '<rect x="2.6" y="3.4" width="12.8" height="8.6" rx="1.6"/><path d="M6.4 15h5.2M9 12v3"/>',
    "lamps": _square(_LAMP_COLUMNS[1], 4.7) + _square(_LAMP_COLUMNS[2], 4.7)
             + "".join(_square(x, 9.3) for x in _LAMP_COLUMNS),
    "sun": '<circle cx="9" cy="9" r="2.8"/><path d="M9 2.2v1.6M9 14.2v1.6M2.2 9h1.6M14.2 9h1.6'
           'M4.2 4.2l1.1 1.1M12.7 12.7l1.1 1.1M4.2 13.8l1.1-1.1M12.7 5.3l1.1-1.1"/>',
    "sliders": '<path d="M3 5.4h12M3 12.6h12"/><circle cx="6.6" cy="5.4" r="1.8" fill="knob"/>'
               '<circle cx="11.4" cy="12.6" r="1.8" fill="knob"/>',
    "update": '<circle cx="9" cy="9" r="6.6"/><path d="M9 5.6v6.2M6.4 9.4 9 12l2.6-2.6"/>',
    "joystick": '<circle cx="9" cy="4.6" r="2.3"/><path d="M9 7v5.5"/>'
                '<rect x="3.2" y="12.5" width="11.6" height="3" rx="1.5"/>',
    "name": '<path d="M4 4.5h10M9 4.5V14M7.2 14h3.6"/>',
    "reset": '<path d="M3.6 9a5.4 5.4 0 1 0 1.7-3.9"/><path d="M3.4 2.8v3.4h3.4"/>',
    "info": '<circle cx="9" cy="9" r="6.6"/><path d="M9 8.2v4.2M9 5.6v.3"/>',
    "help": '<path d="M3 3.8c2.2-.7 4.1-.4 6 1.1v10.3c-1.9-1.5-3.8-1.8-6-1.1Z"/>'
            '<path d="M15 3.8c-2.2-.7-4.1-.4-6 1.1v10.3c1.9-1.5 3.8-1.8 6-1.1Z"/>',
    "globe": '<circle cx="9" cy="9" r="6.6"/><path d="M2.4 9h13.2M9 2.4c2.3 2 2.3 11.2 0 13.2'
             'M9 2.4c-2.3 2-2.3 11.2 0 13.2"/>',
    "computer": '<rect x="2.6" y="3.4" width="12.8" height="8.6" rx="1.6"/><path d="M6.4 15h5.2M9 12v3"/>',
    "chip": '<rect x="4.4" y="4.4" width="9.2" height="9.2" rx="1.6"/>'
            '<path d="M7 2.4v2M11 2.4v2M7 13.6v2M11 13.6v2M2.4 7h2M2.4 11h2M13.6 7h2M13.6 11h2"/>',
    "screen": '<circle cx="9" cy="9" r="6.2"/><circle cx="9" cy="9" r="2.3"/>',
    "haptic": '<rect x="6" y="3" width="6" height="12" rx="1.6"/><path d="M3.4 6.6v4.8M14.6 6.6v4.8"/>',
    "image": '<rect x="2.8" y="3.6" width="12.4" height="10.8" rx="2"/><circle cx="6.6" cy="7.4" r="1.3"/>'
             '<path d="m3.4 13 4-3.6 3 2.6 2-1.6 2.4 2"/>',
    "warning": '<path d="M9 2.6 16.2 15H1.8z"/><path d="M9 7.2v3.6M9 12.6v.4"/>',
    # 「遇到问题」(自助诊断提示词, design ticket): a question mark in a ring.
    "question": '<circle cx="9" cy="9" r="6.6"/><path d="M7 7.2a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.3v.4M9 12.7v.3"/>',
}


def paint_icon(painter: QPainter, name: str, rect: QRectF, colour: str, *, knob: str = TILE) -> None:
    body = ICONS[name].replace("currentColor", colour).replace('fill="knob"', f'fill="{knob}"')
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 18 18" fill="none" '
        f'stroke="{colour}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )
    QSvgRenderer(QByteArray(source.encode())).render(painter, rect)


WALL_STYLE = f"""
QWidget#settingsWorkspace QLabel {{ background: transparent; }}
QFrame[wallTile="true"] {{ background: transparent; border: none; border-radius: 0; }}
QPushButton#settingsGroup {{ min-width: 0; min-height: 0; padding: 0; border: none; background: transparent; }}
QLabel#settingsWallTitle {{ color: {TEXT}; font-size: 16px; font-weight: 600; }}
QLabel#wallTileTitle {{ color: {TEXT}; font-size: 15px; font-weight: 600; }}
QLabel[wallTitle="true"] {{ color: {TEXT}; font-size: {tokens.FONT_EMPHASIS}px; font-weight: 600; }}
QLabel[wallNote="true"] {{ color: {MUTED}; font-size: {tokens.FONT_CAPTION}px; font-weight: 400; }}
QLabel#wallTileNote, QLabel#wallTileRowValue {{ color: {MUTED}; font-size: 12px; font-weight: 400; }}
QLabel[wallState="true"] {{ color: {TEXT}; font-size: 13px; font-weight: 500; }}
QLabel[wallState="true"][tone="quiet"] {{ color: {MUTED}; }}
QLabel#wallTileRowName {{ color: {TEXT}; font-size: 13px; font-weight: 400; }}
QLabel#appReleaseSummaryTitle {{ color: {TEXT}; font-size: 13px; font-weight: 500; }}
QLabel#appReleaseHighlight {{ color: {MUTED}; font-size: 12px; font-weight: 400; }}
QLabel#wallTag {{ color: {MUTED}; font-size: 11px; font-weight: 500;
    border: 1px solid {tokens.WALL_TAG_BORDER}; border-radius: 6px; padding: 0 6px; }}
QFrame#wallTileRule {{ background: {tokens.WALL_RULE}; border: none; max-height: 1px; min-height: 1px; }}
QPushButton[wallRole] {{ min-height: 30px; max-height: 30px; min-width: 0; padding: 0 14px;
    border-radius: 16px; font-size: 13px; font-weight: 500;
    color: {TEXT}; background: transparent; border: 1px solid {tokens.WALL_BUTTON_BORDER}; }}
QPushButton[wallRole]:hover {{ border-color: {tokens.WALL_BUTTON_BORDER_HOVER}; }}
QPushButton[wallRole]:disabled {{ color: {tokens.WALL_TEXT_DISABLED}; border-color: {tokens.WALL_BUTTON_BORDER_DISABLED}; }}
QPushButton[wallRole="primary"] {{ color: {tokens.WHITE}; background: {ORANGE}; border-color: {ORANGE}; font-weight: 600; }}
QPushButton[wallRole="primary"]:hover {{ background: {tokens.ACCENT_HOVER}; border-color: {tokens.ACCENT_HOVER}; }}
QPushButton[wallRole="link"] {{ min-height: 20px; max-height: 20px; padding: 0; border: none;
    color: {MUTED}; font-size: 12px; font-weight: 400; }}
QPushButton[wallRole="link"]:hover {{ color: {TEXT}; }}
QLabel#lightingRowName {{ color: {MUTED}; font-size: {tokens.FONT_BODY}px; font-weight: 400; }}
QLabel#lightingRowValue {{ color: {TEXT}; font-size: {tokens.FONT_BODY}px; font-weight: 500; }}
QLabel[valuePill="true"] {{ color: {TEXT}; font-size: {tokens.FONT_BODY}px; font-weight: 500; min-width: 38px;
    background: {tokens.WALL_SEGMENT}; border-radius: {tokens.RADIUS_SEGMENT_OPTION_COMPACT}px; padding: 3px 9px; }}
QLabel#homeIconPreview {{ color: {MUTED}; font-size: {tokens.FONT_TAG}px; font-weight: 600;
    background: {tokens.WALL_SEGMENT}; border-radius: {HOME_ICON_PREVIEW // 2}px; }}
QLabel#promptPaletteGuide, QLabel#promptDirectTriggerGuide {{ color: {MUTED}; font-size: {tokens.FONT_CAPTION}px; }}
QPushButton#promptMoreMenu::menu-indicator {{ image: none; width: 0; }}
QPushButton[wallRole="cream"] {{ color: {tokens.WALL_TEXT_ON_CREAM}; background: {CREAM}; border-color: {CREAM}; font-weight: 600; }}
QPushButton#promptOperationStep[current="true"] {{ color: {TEXT}; }}
"""


class RailTip(QWidget):
    """分组图标的名字提示：出现在图标右侧、和图标上下居中对齐，不跟着鼠标。

    自己画一层圆角。系统自带的提示窗口是直角的，套上圆角样式后四个角会露出直角底。
    """

    GAP = 10
    RADIUS = 9

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("settingsGroupTip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._text = ""
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)

    def text(self) -> str:
        return self._text

    def show_beside(self, anchor: QWidget, text: str) -> None:
        self._text = text
        metrics = QFontMetrics(self.font())
        self.resize(metrics.horizontalAdvance(text) + 24, 30)
        right_middle = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() // 2))
        self.move(right_middle.x() + self.GAP, right_middle.y() - self.height() // 2)
        self.show()
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(*tokens.WALL_TIP_EDGE), 1))
        painter.setBrush(QColor(TILE))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        painter.setPen(QColor(TEXT))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)


class RailButton(QPushButton):
    """分组图标。文字留着给读屏、翻译和悬停提示用，画面上只画图标。"""

    def __init__(self, label: str, icon: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setObjectName("settingsGroup")
        self._icon = icon
        self._tip: RailTip | None = None
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)

    def sizeHint(self) -> QSize:
        return QSize(40, 40)

    def tip(self) -> RailTip | None:
        return self._tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.hide()

    def event(self, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.ToolTip:
            if self._tip is None:
                self._tip = RailTip(self)
            self._tip.show_beside(self, self.text())
            return True
        if kind in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Hide,
                    QEvent.Type.WindowDeactivate):
            self._hide_tip()
        return super().event(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.property("selected") is True
        body = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        if selected:
            painter.setBrush(QColor(CREAM))
            painter.drawRoundedRect(body, 16, 16)
        elif self.isEnabled() and self.underMouse():
            painter.setBrush(QColor(*tokens.WALL_HOVER))
            painter.drawRoundedRect(body, 16, 16)
        colour = tokens.WALL_TEXT_ON_CREAM if selected else tokens.WALL_ICON if self.isEnabled() else tokens.WALL_ICON_DISABLED
        size = 25 if self._icon == "lamps" else 20
        paint_icon(
            painter, self._icon,
            QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size),
            colour, knob=CREAM if selected else RAIL,
        )
        if self.hasFocus() and not selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1.5))
            painter.drawRoundedRect(body.adjusted(1, 1, -1, -1), 15, 15)


class SettingsRail(QFrame):
    """竖着的胶囊，装五个分组图标；整组在内容区上下居中。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsNavigation")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def add(self, button: RailButton) -> None:
        self.layout().addWidget(button)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 和顶上一级导航的胶囊同一种画法：窗口是半透明的，实色底在上面会看不出来。
        painter.setPen(QPen(QColor(*tokens.SHELL_CAPSULE_EDGE), 1))
        painter.setBrush(QColor(*tokens.SHELL_CAPSULE_FILL))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 22, 22)


class TileIcon(QWidget):
    """方块左上角的图标底：一个线条图标，或者一个字母（应用图标的占位）。"""

    def __init__(self, icon: str | None = None, *, letter: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._letter = letter
        self.setFixedSize(28, 28)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(CHIP))
        painter.setPen(QPen(QColor(*tokens.WALL_EDGE), 1) if self._letter else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(body, 9, 9)
        if self._letter:
            font = painter.font()
            font.setPixelSize(12)
            font.setWeight(font.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(MUTED))
            painter.drawText(body, Qt.AlignmentFlag.AlignCenter, self._letter)
        elif self._icon:
            size = 22 if self._icon == "lamps" else 16
            paint_icon(painter, self._icon,
                       QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size),
                       tokens.WALL_ICON_TILE, knob=CHIP)


class RoundArrowButton(QPushButton):
    """方块右上角 32 的奶白圆钮，里面一个箭头：点开方块，或把点开的方块收回去（灯光页 v0.2）。"""

    SIZE = tokens.ROUND_ARROW_SIZE

    def __init__(self, object_name: str, accessible_name: str, *, direction: str = "right",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("roundArrow", True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tr(accessible_name))
        self._direction = direction

    def direction(self) -> str:
        return self._direction

    def set_direction(self, direction: str) -> None:
        self._direction = direction
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        disc = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.hasFocus() and self.focusPolicy() != Qt.FocusPolicy.NoFocus:
            painter.setPen(QPen(QColor(ORANGE), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc)
            disc = disc.adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(CREAM if self.isEnabled() else tokens.WALL_ICON_DISABLED))
        painter.drawEllipse(disc)
        pen = QPen(QColor(tokens.WALL_TEXT_ON_CREAM), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        centre = disc.center()
        step = 1 if self._direction == "right" else -1
        tip = QPoint(round(centre.x() + 2 * step), round(centre.y()))
        painter.drawPolyline([QPoint(round(centre.x() - 2 * step), round(centre.y() - 5)), tip,
                              QPoint(round(centre.x() - 2 * step), round(centre.y() + 5))])


class LampLayout(QWidget):
    """6 颗状态灯键的位置和编号：上排中间 1、2，下排 3～6（固件的 Agent 1～6）。"""

    SIZE, STEP = 20, 26
    ROWS = (((1, 1), (2, 2)), ((0, 3), (1, 4), (2, 5), (3, 6)))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(3 * self.STEP + self.SIZE + 2, self.STEP + self.SIZE + 2)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        for row, lamps in enumerate(self.ROWS):
            for column, number in lamps:
                box = QRectF(column * self.STEP + 1, row * self.STEP + 1, self.SIZE, self.SIZE)
                painter.setPen(QPen(QColor(tokens.WALL_LAMP_EDGE), 1.2))
                painter.setBrush(QColor(*tokens.WALL_LAMP_FILL))
                painter.drawRoundedRect(box, 6, 6)
                painter.setPen(QColor(tokens.WALL_ICON_TILE))
                painter.drawText(box, Qt.AlignmentFlag.AlignCenter, str(number))


class StateLine(QWidget):
    """一行状态：前面一个 8px 圆点或警示图标，后面一句话。"""

    def __init__(self, object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = "off"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self._mark = _StateMark(self)
        row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel(objectName=object_name or "wallTileState")
        # 颜色和字号按这个属性走：各块会给这行换 objectName，不能靠名字匹配样式。
        self.label.setProperty("wallState", True)
        self.label.setProperty("tone", "quiet")
        self.label.setWordWrap(True)
        self.label.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        row.addWidget(self.label, 1)

    def set_state(self, tone: str, text: str) -> None:
        """tone：ok 绿点；off 空心灰圈；wait 灰点；warn 橙色警示。"""
        self._mark.set_tone(tone)
        self.label.setProperty("tone", "quiet" if tone in {"off", "wait"} else "plain")
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)
        self.label.setText(text)

    def pop(self) -> None:
        self._mark.pop()

    def text(self) -> str:
        return self.label.text()


class _StateMark(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tone = "off"
        self.setFixedSize(16, 18)
        self._pop = None   # 授权回来以后弹一下 (-15 第 5 处), made on first use

    def pop(self) -> None:
        if self._pop is None:
            self._pop = Pop(self)
        self._pop.start()

    def set_tone(self, tone: str) -> None:
        self._tone = tone
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self._pop.scale() if self._pop is not None else 1.0
        if scale != 1.0:
            painter.translate(7, 9)
            painter.scale(scale, scale)
            painter.translate(-7, -9)
        if self._tone == "warn":
            paint_icon(painter, "warning", QRectF(0, 1, 16, 16), ORANGE)
            return
        dot = QRectF(3, 5, 8, 8)
        if self._tone == "off":
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(MUTED), 1.5))
            painter.drawEllipse(dot.adjusted(0.75, 0.75, -0.75, -0.75))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(GREEN if self._tone == "ok" else MUTED))
        painter.drawEllipse(dot)


class WallTile(QFrame):
    """一块方块。大小由占几格决定，不跟着窗口拉伸。"""

    def __init__(self, columns: int = 1, rows: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wallTile")
        # 语义上它就是一张小组件卡；全局样式表里这个角色的底色和描边在下面的样式里关掉，由本类自己画。
        self.setProperty("cardRole", "widget")
        self.setProperty("wallTile", True)
        self.span = (rows, columns)
        self.apply_unit(UNIT)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(PAD, PAD, PAD, PAD)
        self.box.setSpacing(4)

    def apply_unit(self, unit: int) -> None:
        rows, columns = self.span
        self.setFixedSize(columns * unit + (columns - 1) * GAP, rows * unit + (rows - 1) * GAP)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(TILE))
        # 点开盖在别的方块上时边框亮一档，看得出它在上面（灯光页 v0.2）。
        edge = tokens.WALL_EDGE_RAISED if self.property("raised") else tokens.WALL_EDGE
        painter.setPen(QPen(QColor(*edge), 1))
        painter.drawRoundedRect(body, RADIUS, RADIUS)

    def header(self, icon: QWidget, *, title: str = "", control: QWidget | None = None,
               extras: tuple[QWidget, ...] = ()) -> QHBoxLayout:
        """顶上一行：左边图标（横 2 格的方块名字跟在图标后面），右边是那一个动作。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        if title:
            name = title_label(title)
            # 译文比中文长时名字折成两行，右边的按钮留整字（design decision：不截字）。
            name.setWordWrap(True)
            row.addWidget(name, 1, Qt.AlignmentFlag.AlignVCenter)
        for extra in extras:
            row.addWidget(extra, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self.box.addLayout(row)
        return row

    def spacer(self) -> None:
        self.box.addStretch(1)


def title_label(text: str) -> QLabel:
    label = QLabel(objectName="wallTileTitle")
    set_translatable_text(label, text)
    return label


def note_label(text: str = "", object_name: str = "wallTileNote") -> QLabel:
    label = QLabel(objectName=object_name)
    label.setWordWrap(True)
    if text:
        set_translatable_text(label, text)
    if object_name != "wallTileNote":
        label.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 400;")
    return label


def pill_button(text: str, object_name: str, *, role: str = "plain") -> QPushButton:
    button = QPushButton(objectName=object_name)
    set_translatable_text(button, text)
    button.setProperty("wallRole", role)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def toggle(object_name: str, accessible_name: str) -> QCheckBox:
    box = QCheckBox(objectName=object_name)
    box.setAccessibleName(accessible_name)
    box.setCursor(Qt.CursorShape.PointingHandCursor)
    return box


def square_tile(icon: str, title: str, note: QWidget | str | None = None, *,
                control: QWidget | None = None) -> WallTile:
    """1 格的方块：左上图标，右上动作，下面名字和一句状态。"""
    tile = WallTile(1, 1)
    tile.header(TileIcon(icon), control=control)
    tile.spacer()
    name = title_label(title)
    name.setWordWrap(True)  # 英文、日文的名字比中文长，放不下时折成两行
    tile.box.addWidget(name)
    if isinstance(note, str):
        note = note_label(note)
    if note is not None:
        tile.box.addWidget(note)
    return tile


def wide_tile(icon: QWidget | str, title: str, *, control: QWidget | None = None,
              extras: tuple[QWidget, ...] = (), columns: int = 2, rows: int = 1) -> WallTile:
    """横 2 格或 2×2 的方块：名字跟图标排在同一行，内容由调用方往下加。"""
    tile = WallTile(columns, rows)
    tile.header(TileIcon(icon) if isinstance(icon, str) else icon, title=title,
                control=control, extras=extras)
    return tile


def rule() -> QFrame:
    return QFrame(objectName="wallTileRule")


def value_row(name: str, value: str, *, translate_value: bool = False) -> QWidget:
    """大方块里的一行：左边名字，右边值。"""
    row = QWidget()
    row.setFixedHeight(33)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(objectName="wallTileRowName")
    set_translatable_text(label, name)
    layout.addWidget(label)
    layout.addStretch(1)
    shown = QLabel(objectName="wallTileRowValue")
    shown.setTextFormat(Qt.TextFormat.PlainText)
    if translate_value:
        set_translatable_text(shown, value)
    else:
        shown.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        shown.setText(value)
    layout.addWidget(shown)
    return row


class SettingsWall(QWidget):
    """一面 4×2 的墙。每块的位置在这里写死。

    墙上面默认是分组名；给了 header（Prompt 页顶上那一行）就换成它，宽度跟墙一样。
    """

    def __init__(self, section: str, parent: QWidget | None = None, *,
                 header: QWidget | None = None, header_height: int = TITLE_BLOCK - 12) -> None:
        super().__init__(parent)
        self.setObjectName("settingsWall")
        self.setProperty("settingsSection", section)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        if header is None:
            header = QLabel(objectName="settingsWallTitle")
            set_translatable_text(header, SECTION_TITLES[section])
            header.setContentsMargins(4, 0, 0, 0)
        header.setFixedHeight(header_height)
        self._header = header
        self._header_block = header_height + 12
        outer.addWidget(header)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(GAP)
        self._grid.setVerticalSpacing(GAP)
        outer.addWidget(self._grid_host)
        self._tiles: list[WallTile] = []
        self._unit = 0
        self.set_unit(UNIT)

    def place(self, tile: WallTile, row: int, column: int) -> WallTile:
        rows, columns = tile.span
        tile.apply_unit(self._unit)
        self._tiles.append(tile)
        self._grid.addWidget(tile, row, column, rows, columns,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        return tile

    def move(self, tile: WallTile, row: int, column: int) -> None:
        """把墙上已有的一块挪到新位置（占的格数按 tile.span 重新算）。"""
        rows, columns = tile.span
        self._grid.removeWidget(tile)
        tile.apply_unit(self._unit)
        self._grid.addWidget(tile, row, column, rows, columns,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._grid.activate()  # the tile is where it belongs before the next paint

    def set_unit(self, unit: int) -> None:
        """整面墙按同一个格子大小重排尺寸；每块在第几行第几列不变。"""
        unit = max(MIN_UNIT, min(MAX_UNIT, unit))
        if unit == self._unit:
            return
        self._unit = unit
        for tile in self._tiles:
            tile.apply_unit(unit)
        for column in range(COLUMNS):
            self._grid.setColumnMinimumWidth(column, unit)
        for row in range(ROWS):
            self._grid.setRowMinimumHeight(row, unit)
        width = COLUMNS * unit + (COLUMNS - 1) * GAP
        height = ROWS * unit + (ROWS - 1) * GAP
        self._grid_host.setFixedSize(width, height)
        self._header.setFixedWidth(width)
        self.setFixedSize(width, height + self._header_block)

    def grid_height(self) -> int:
        """方块那一片的高（不算上面的分组名）。"""
        return self._grid_host.height()


RAIL_GAP = 20
# 墙底下到窗口内容区底边至少留这么多（design decision 窗口尺寸规矩：每个状态都不滚动，底下留 16 以上）。
BOTTOM_ROOM = 16
# 墙在滚动区里上下居中、略偏上：上面的空和下面的空按 2 : 3 分。
TOP_STRETCH, BOTTOM_STRETCH = 2, 3


class WallScroll(QScrollArea):
    """装墙的滚动区：按可用的宽和高给墙定格子大小，墙整面放得下、底下留出 BOTTOM_ROOM。

    窗口小到格子缩到 MIN_UNIT 还放不下时（比最小窗口还小的屏幕），才会出现滚动。
    """

    def __init__(self, wall: SettingsWall, parent: QWidget | None = None, *, reserved: int = 0) -> None:
        super().__init__(parent)
        self._wall = wall
        self._reserved = reserved  # 墙左边图标栏占掉的宽度

    def wall(self) -> SettingsWall:
        return self._wall

    def fit_unit(self) -> None:
        wall = self._wall
        by_width = (self.viewport().width() - self._reserved - (COLUMNS - 1) * GAP) // COLUMNS
        extra = wall.height() - wall.grid_height()  # 分组名那一行，以及墙自己加的别的东西
        # 下面那份空要有 BOTTOM_ROOM，上下两份合起来就得留这么多。
        free = -(-BOTTOM_ROOM * (TOP_STRETCH + BOTTOM_STRETCH) // BOTTOM_STRETCH)
        by_height = (self.viewport().height() - free - extra - (ROWS - 1) * GAP) // ROWS
        wall.set_unit(min(by_width, by_height))

    def resizeEvent(self, event) -> None:
        self.fit_unit()
        super().resizeEvent(event)


def wall_scroll(wall: SettingsWall, rail: QWidget | None = None, *,
                scroll: WallScroll | None = None) -> QScrollArea:
    """把墙居中放进滚动区；墙按窗口大小定格子，正常窗口里不用滚。

    给了图标栏时，图标栏紧挨在墙的左边、和方块那一片上下居中，两样一起在窗口里居中：
    窗口拉大以后图标栏不会留在最左边，和墙之间不空出一大截。
    给了 scroll（以前这个函数做的那个）时，换上新墙，滚动区本身留着不动，
    页面换状态时不用把它从窗口里摘下来（固件更新页）。
    """
    reserved = rail.sizeHint().width() + RAIL_GAP if rail is not None else 0
    if scroll is None:
        scroll = WallScroll(wall, reserved=reserved)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
    else:
        scroll._wall = wall
        scroll._reserved = reserved
    page = QWidget(objectName="settingsPage")
    page.setStyleSheet("QWidget#settingsPage { background: transparent; }")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(TOP_STRETCH)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(RAIL_GAP)
    row.addStretch(1)
    if rail is not None:
        column = QVBoxLayout()
        column.setContentsMargins(0, TITLE_BLOCK, 0, 0)  # 对着方块那一片居中，不算上面的分组名
        column.addStretch(1)
        column.addWidget(rail)
        column.addStretch(1)
        row.addLayout(column)
    row.addWidget(wall)
    row.addStretch(1)
    layout.addLayout(row)
    layout.addStretch(BOTTOM_STRETCH)
    scroll.setWidget(page)
    if scroll.viewport().height() > 0:
        scroll.fit_unit()  # a new wall in a live scroll area: size it before it paints
    return scroll


DIALOG_MAX_HEIGHT = dialog_shell.MAX_HEIGHT


class CardDialog(dialog_shell.Dialog):
    """装一张原来的卡片的对话框：用统一的弹窗外壳（B 方向 v0.4），大小刚好装下内容。

    标题只写一遍：卡片自己的大标题和弹窗标题一样时藏起来。左下角是圆形关闭，
    底部不再放“关闭”按钮（只读弹窗的规矩）。内容变高时弹窗跟着长，超过上限才滚动。
    """

    def __init__(self, parent: QWidget | None, title: str, card: QWidget, *,
                 width: int = dialog_shell.SMALL) -> None:
        super().__init__(parent, title, width=width, close="round", object_name="settingsCardDialog")
        self._card = card
        for label in card.findChildren(QLabel, "inspectorTitle"):
            if label.text() in {title, tr(title)}:
                label.hide()
        self.body_layout().addWidget(card)
        card.installEventFilter(self)

    def content_height(self) -> int:
        inner = self._width - 2 * dialog_shell.PADDING
        card = self._card
        card.ensurePolished()
        layout = card.layout()
        if layout is not None and layout.hasHeightForWidth():
            return layout.totalHeightForWidth(inner)
        return card.sizeHint().height()

    def eventFilter(self, watched: QObject, event) -> bool:
        if watched is self._card and event.type() == QEvent.Type.LayoutRequest:
            # With self as context Qt drops the call if the dialog is gone by then.
            QTimer.singleShot(0, self, self.fit)
        return super().eventFilter(watched, event)


def show_card_dialog(parent: QWidget, title: str, card: QWidget, *, width: int = dialog_shell.SMALL) -> None:
    """把原来那张完整的卡片放进对话框：细节、排障和管理动作都在里面。"""
    dialog = CardDialog(parent, title, card, width=width)
    dialog.exec()
    dialog.deleteLater()


class ClaudeStatusTile(WallTile):
    """Claude 状态灯：一个开关、一句状态。细节和管理动作在「了解更多」里。"""

    USE = "在 Claude 桌面端的本地 Code 会话里，用状态灯显示任务进度。"

    def __init__(self, bridge, parent: QWidget | None = None) -> None:
        super().__init__(2, 1, parent)
        self.bridge = bridge
        self.enabled = toggle("claudeStatusEnabled", "Claude 状态灯")
        tag = QLabel(objectName="wallTag")
        set_translatable_text(tag, "测试")
        self.header(TileIcon(letter="C"), title="Claude 状态灯", control=self.enabled, extras=(tag,))
        self.spacer()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.state = StateLine()
        self.state.label.setObjectName("claudeTileState")
        row.addWidget(self.state, 1)
        self.more = pill_button("了解更多 ›", "claudeStatusMore", role="link")
        row.addWidget(self.more, 0, Qt.AlignmentFlag.AlignTop)
        self.fix = pill_button("修复", "claudeStatusFix", role="primary")
        row.addWidget(self.fix, 0, Qt.AlignmentFlag.AlignBottom)
        self.box.addLayout(row)
        self.note = note_label(self.USE)
        self.box.addWidget(self.note)
        self._fix_action = None
        self.enabled.toggled.connect(self._toggled)
        self.more.clicked.connect(self._open_details)
        self.fix.clicked.connect(self._fix)
        bridge.changed.connect(self.refresh)
        self.refresh()

    def _run(self, action) -> None:
        try:
            action()
        except (ValueError, OSError, RuntimeError) as exc:
            notice.notify(self, tr("Claude 状态灯"), tr(str(exc)))
        self.refresh()

    def _toggled(self, checked: bool) -> None:
        if checked == bool(self.bridge.enabled):
            return
        if not checked:
            self._run(self.bridge.disable)
            return
        answer = notice.ask(
            self, tr("启用 Claude 状态灯"),
            tr("将合并 BORING Hooks 到本机 Claude 设置（Claude 桌面端与 Claude Code 命令行共用），保留已有配置。"
               "只上报状态，不批准权限或替你操作。是否启用？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
            labels={QMessageBox.StandardButton.Yes: "启用"},
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run(self.bridge.enable)
        else:
            self.refresh()

    def _open_details(self) -> None:
        from controller_config.views.claude_status_settings import ClaudeStatusSettings

        show_card_dialog(self.window(), "Claude 状态灯", ClaudeStatusSettings(self.bridge), width=dialog_shell.MEDIUM)
        self.refresh()

    def _fix(self) -> None:
        if self._fix_action is None:
            self._open_details()
        else:
            self._run(self._fix_action)

    def _shown_sessions(self) -> tuple[int, int]:
        registry = self.bridge.registry
        delivery = getattr(self.bridge, "delivery", None)
        shown = 0
        for session in registry.sessions:
            slot = delivery.slot_of(session.session_id) if delivery is not None else session.slot
            shown += slot is not None
        return shown, len(registry.overflow)

    def refresh(self) -> None:
        bridge = self.bridge
        inspection = bridge.inspection
        problem = bool(inspection and inspection.problem)
        repair = bool(getattr(inspection, "needs_repair", False)) and not problem
        fault = bool(getattr(bridge, "fault", False))
        enabled = bool(bridge.enabled)
        if self.enabled.isChecked() != enabled:
            self.enabled.blockSignals(True)
            self.enabled.setChecked(enabled)
            self.enabled.blockSignals(False)
        trouble = True
        if problem:
            self.state.set_state("warn", tr("状态灯没有生效") + "：" + tr(inspection.problem))
            self._fix_action, fix_text = None, "处理"
        elif repair:
            self.state.set_state("warn", tr("状态灯没有生效") + "：" + tr(bridge.message))
            self._fix_action, fix_text = bridge.repair, "修复"
        elif enabled and fault:
            self.state.set_state("warn", tr("状态灯没有生效") + "：" + tr(bridge.message))
            self._fix_action, fix_text = bridge.retry, "重试"
        else:
            trouble = False
            fix_text = "修复"
            if not enabled:
                self.state.set_state("off", tr("未开启"))
            else:
                shown, overflow = self._shown_sessions()
                if overflow:
                    detail = tr("6 个灯已占满，新会话暂不显示")
                elif shown:
                    detail = tr("正在显示 {count} 个 Claude 会话").format(count=shown)
                else:
                    detail = tr("尚无 Claude 会话")
                self.state.set_state("ok", tr("已开启") + " · " + detail)
        set_translatable_text(self.fix, fix_text)
        self.fix.setVisible(trouble)
        self.note.setVisible(not trouble)


class LampsTile(WallTile):
    """6 颗状态灯：灯的编号和分灯规则；Claude、豆包同时开着时换成一句提醒。

    规则是 the firmware owner 定的（design decision）：只开一个 AI 时 6 颗灯都给它，开两个以上时提醒用户自己分。
    分灯的界面还没做，所以提醒里直接说明这一点。Codex / ChatGPT 是内置的、没有开关，不计入。
    """

    RULE = "只开一个 AI 时，6 颗灯都给它；同时开两个以上时，需要自己分灯。"
    SHARED = "Claude 和豆包同时开着，需要自己分灯。分灯设置后续版本提供。"

    def __init__(self, bridge, link, parent: QWidget | None = None) -> None:
        super().__init__(2, 1, parent)
        self.bridge = bridge
        self.link = link
        self.header(TileIcon("lamps"), title="6 颗状态灯")
        self.spacer()
        row = QHBoxLayout()
        row.setSpacing(14)
        self.layout_view = LampLayout()
        row.addWidget(self.layout_view, 0, Qt.AlignmentFlag.AlignBottom)
        self.note = note_label(self.RULE)
        self.note.setObjectName("lampsRule")
        self.note.setStyleSheet(f"color: {MUTED}; font-size: 12px; font-weight: 400;")
        row.addWidget(self.note, 1, Qt.AlignmentFlag.AlignBottom)
        self.shared = StateLine()
        self.shared.label.setObjectName("lampsShared")
        self.shared.label.setStyleSheet("font-size: 12.5px; font-weight: 500;")
        self.shared.set_state("warn", "")
        set_translatable_text(self.shared.label, self.SHARED)
        self.shared.label.setProperty(SKIP_TRANSLATION_PROPERTY, False)
        row.addWidget(self.shared, 1, Qt.AlignmentFlag.AlignBottom)
        self.box.addLayout(row)
        for source in (bridge, link):
            changed = getattr(source, "changed", None)
            if changed is not None:
                changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        both = bool(getattr(self.bridge, "enabled", False)) and bool(getattr(self.link, "enabled", False))
        self.shared.setVisible(both)
        self.note.setVisible(not both)


# Codex 的灯是 Codex 桌面端直接发给设备的（不经过 Console），所以没有开关；这一块只说现在的情况。
# 数据都是现成的：设备 GET_STATUS 里的 codex_micro.status_age_ms（Codex 上次发任务状态到现在
# 多少毫秒，这次开机还没发过是 null，旧固件没有这一项）、额度读取（codex_usage），和本机装没装 Codex。
CODEX_RECENT_MS = 60_000


def detect_codex() -> tuple[str, ...] | None:
    """这台电脑上的 Codex：桌面端（Mac）和 codex 命令。看不出来时（Windows 又没有命令）是 None。"""
    found = tuple(installed_agent_clients())
    if resolve_codex_executable() is not None:
        found += ("codex",)
    if not found and sys.platform != "darwin":
        return None
    return found


def codex_state(micro: dict | None, detected: tuple[str, ...] | None) -> tuple[str, str]:
    """(tone, 一句状态)。micro 是设备的 codex_micro；没连设备时是 None。"""
    age = micro.get("status_age_ms") if isinstance(micro, dict) else None
    if age is not None:
        if age <= CODEX_RECENT_MS:
            return "ok", tr("工作中 · Codex 刚发来任务状态")
        minutes = age // 60_000
        if minutes < 60:
            return "ok", tr("{count} 分钟前收到 Codex 的任务状态").format(count=minutes)
        return "ok", tr("{count} 小时前收到 Codex 的任务状态").format(count=minutes // 60)
    if detected == ():
        return "off", tr("没有检测到 Codex 或 ChatGPT")
    if not isinstance(micro, dict):
        return "wait", tr("连接设备后显示 Codex 的状态")
    if "status_age_ms" not in micro:
        return "ok", tr("内置支持 · 当前固件不报告 Codex 的状态")
    return "wait", tr("等待 Codex · 设备这次开机后还没收到任务状态")


def codex_usage_note(snapshot) -> str:
    """额度数据是不是在更新、最后什么时候更新的。没有额度读取时是空的。"""
    if snapshot is None:
        return ""
    if snapshot.status is CodexUsageStatus.LOADING:
        return tr("正在更新额度数据…")
    if snapshot.status is not CodexUsageStatus.AVAILABLE or snapshot.updated_at is None:
        return tr("暂时读不到额度数据")
    when = datetime.fromtimestamp(snapshot.updated_at).strftime("%m-%d %H:%M")
    if snapshot.source == "session_log":
        return tr("额度数据更新于 {time} · 来自本地日志，可能滞后").format(time=when)
    return tr("额度数据更新于 {time}").format(time=when)


class CodexStatusTile(WallTile):
    """Codex / ChatGPT 状态灯：没有开关，一句真实状态，加一行额度数据的更新情况。

    codex_micro：返回设备 GET_STATUS 里的 codex_micro（没连设备返回 None）。
    usage：返回现在的额度快照（CodexUsageSnapshot；没有额度读取时返回 None）。
    signals：这些信号一来就重读（设备状态、额度变化）。
    """

    def __init__(self, *, codex_micro, usage=lambda: None, detect=None, signals=(),
                 parent: QWidget | None = None) -> None:
        super().__init__(2, 1, parent)
        self._codex_micro = codex_micro
        self._usage = usage
        self._detected = (detect or detect_codex)()  # 装没装 Codex 只在建这一块时看一次
        self._shown: tuple[str, str, str] | None = None
        self.header(TileIcon(letter="G"), title="Codex / ChatGPT 状态灯")
        self.spacer()
        self.state = StateLine()
        self.state.label.setObjectName("codexTileState")
        self.box.addWidget(self.state)
        self.note = note_label()  # 名字留着 wallTileNote：样式按它走
        self.note.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.box.addWidget(self.note)
        for signal in signals:
            signal.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        tone, text = codex_state(self._codex_micro(), self._detected)
        note = codex_usage_note(self._usage())
        if (tone, text, note) == self._shown:
            return  # 设备状态很频繁，没变就不重画
        self._shown = (tone, text, note)
        self.state.set_state(tone, text)
        self.note.setText(note)
        self.note.setVisible(bool(note))


class DoubaoTile(WallTile):
    """豆包状态灯：一个开关、一句状态；缺权限时把权限提示放在这一块里。"""

    USE = "用状态灯显示豆包里对话的进度，按灯可以跳到对应对话。"
    NEEDS_ACCESS = "按灯跳到豆包对话需要「辅助功能」权限。灯的显示不受影响。"
    # 开关开着、设备固件却不支持：先说"已开启"，再说差什么，旁边给一个去「更新」的按钮。
    NEEDS_FIRMWARE = "已开启，但设备固件不支持"
    # 开着、豆包里还没有对话：灯不亮是正常的，要说出来，不然会以为灯该亮了。
    ACTIVE_IDLE = "工作中 · 有对话时才亮灯"

    def __init__(self, link, permissions: perm.SystemPermissions | None = None,
                 parent: QWidget | None = None, *, poll_ms: int = permission_row.POLL_MS,
                 open_updates=None) -> None:
        super().__init__(2, 1, parent)
        self.link = link
        self._open_updates = open_updates
        # 权限是在系统设置里改的，Console 收不到通知：回到 Console 时（应用回到前台、窗口重新激活）
        # 自动重读；点了“去允许”以后的两分钟里也每 poll_ms 读一次，用户把两个窗口并排放时也能跟上。
        self.watcher = permission_row.PermissionWatcher(permissions, self, kinds=(perm.ACCESSIBILITY,),
                                                        poll_ms=poll_ms)
        self.permissions = self.watcher.permissions
        self.enabled = toggle("doubaoLinkEnabled", "豆包状态灯")
        self.header(TileIcon(letter="豆"), title="豆包状态灯", control=self.enabled)
        self.spacer()
        self.access = StateLine()
        self.access.set_state("warn", "")
        set_translatable_text(self.access.label, self.NEEDS_ACCESS)
        self.access.label.setProperty(SKIP_TRANSLATION_PROPERTY, False)
        self.box.addWidget(self.access)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.state = StateLine()
        self.state.label.setObjectName("doubaoLinkStatus")
        row.addWidget(self.state, 1)
        self.more = pill_button("详情 ›", "doubaoLinkMore", role="link")
        row.addWidget(self.more, 0, Qt.AlignmentFlag.AlignVCenter)
        self.allow = pill_button("去允许", "openAccessibilitySettings", role="primary")
        row.addWidget(self.allow, 0, Qt.AlignmentFlag.AlignVCenter)
        self.update_firmware = pill_button("去更新", "doubaoOpenUpdates", role="primary")
        row.addWidget(self.update_firmware, 0, Qt.AlignmentFlag.AlignVCenter)
        self.box.addLayout(row)
        self.note = note_label(self.USE)
        self.box.addWidget(self.note)
        self.enabled.toggled.connect(self._toggled)
        self.more.clicked.connect(self._open_details)
        self.allow.clicked.connect(lambda: self.watcher.open_settings(perm.ACCESSIBILITY))
        self.update_firmware.clicked.connect(self._go_update)
        link.changed.connect(self.refresh)
        self.watcher.changed.connect(self.refresh)
        self.refresh()

    def _toggled(self, checked: bool) -> None:
        self.link.set_enabled(checked)

    def _go_update(self) -> None:
        if self._open_updates is not None:
            self._open_updates()

    def _open_details(self) -> None:
        from controller_config.views.desktop_agent_settings import DoubaoLinkSettings

        details = DoubaoLinkSettings(self.link, watcher=self.watcher)
        show_card_dialog(self.window(), "豆包状态灯", details, width=dialog_shell.MEDIUM)
        self.refresh()

    def _short_state(self, access: str) -> tuple[str, str]:
        from controller_config.views.desktop_agent_settings import STATUS_TEXT

        link = self.link
        availability = link.availability
        if availability == link_codes.DISABLED:
            return "off", tr("未开启")
        if availability == link_codes.APP_NOT_RUNNING:
            return "wait", tr("等待豆包打开")
        if availability == link_codes.ACTIVE:
            if link.lights_in_use > 0:
                return "ok", tr("工作中，占用 {count} 颗灯").format(count=link.lights_in_use)
            return "ok", tr(self.ACTIVE_IDLE)
        if availability == link_codes.NEEDS_FIRMWARE:
            return "warn", tr(self.NEEDS_FIRMWARE)
        # 其余状态先用原来那句话的前半句；完整的原因和下一步在「详情」里。
        text = tr(STATUS_TEXT.get(availability, STATUS_TEXT[link_codes.APP_NOT_RUNNING]))
        tone = "wait" if availability in (link_codes.UNAVAILABLE_NO_DEVICE, link_codes.PAUSED,
                                          link_codes.DEVICE_BUSY) else "warn"
        return tone, text.split(" · ")[0]

    def refresh(self) -> None:
        link = self.link
        if self.enabled.isChecked() != link.enabled:
            self.enabled.blockSignals(True)
            self.enabled.setChecked(link.enabled)
            self.enabled.blockSignals(False)
        access = self.watcher.status(perm.ACCESSIBILITY)
        tone, text = self._short_state(access)
        needs_access = bool(link.enabled and access == perm.NOT_TRUSTED)
        came_back = (getattr(self, "_needed_access", False) and not needs_access and tone == "ok"
                     and self.isVisible())
        self._needed_access = needs_access
        if came_back:
            # 授权回来以后 (-15 第 5 处, 乙): the tile cross-fades 短 and its green mark pops.
            CrossFade(self, clear_grab(self, own_background=True), tokens.MOTION_SHORT_MS)
        self.state.set_state(tone, text)
        if came_back:
            self.state.pop()
        needs_firmware = bool(link.enabled and link.availability == link_codes.NEEDS_FIRMWARE)
        self.access.setVisible(needs_access)
        self.allow.setVisible(needs_access)
        # 一块里只给一个橙色按钮：缺权限时是"去允许"，固件不支持时是"去更新"。
        self.update_firmware.setVisible(needs_firmware and not needs_access
                                        and self._open_updates is not None)
        self.more.setVisible(not needs_access)
        self.note.setVisible(not needs_access)
