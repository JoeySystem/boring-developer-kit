"""The device status capsule at the right of the top bar (the designer, 顶条认证标 整条顶条 v1 定稿).

One small capsule with a status shape and one sentence in the interface
language; no bracketed English codes. Height 32, corner 16, a 1 px edge in the
tab capsule's colour, no fill; inside 8 left, 13 right, shape 16, gap 7, text
13 semibold.

    verified      green check            已认证              cream text
    dev           cream dashed circle    开发设备 · 未认证    grey text
    untrusted     orange triangle        BORING 身份未确认    cream text
    no_device     grey dashed circle     未连接              grey text
    disconnected  grey dashed circle     已断开              grey text
    connecting    spinner                连接中              grey text
    read_failed   grey dashed circle     配置未读取          grey text   (the firmware owner 11-19: grey dashed circle, as 未连接)

Which one when is the main window's logic (`_refresh_device_summary`); the
capsule only shows it. It shows, it does not react to clicks (spec).
“仅固件维护” is not a capsule state: it sits at the top of the firmware page and
the capsule keeps showing 已认证 / 开发设备 (the firmware owner 11-19, design ticket B).
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.style import tokens
from controller_config.views.ui.status_icon import StatusIcon

HEIGHT = 32
LEFT_PADDING = 8
RIGHT_PADDING = 13
SHAPE = 16
GAP = 7

# kind -> (StatusIcon state, cream shape, text, cream text)
LOOKS = {
    "verified": ("ok", False, "已认证", True),
    "dev": ("off", True, "开发设备 · 未认证", False),
    "untrusted": ("error", False, "BORING 身份未确认", True),
    "no_device": ("off", False, "未连接", False),
    "disconnected": ("off", False, "已断开", False),
    "connecting": ("busy", False, "连接中", False),
    "read_failed": ("off", False, "配置未读取", False),
}


class DeviceStatusCapsule(QFrame):
    """`DeviceStatusCapsule()`, then `set_status(kind)`; kinds are the keys of LOOKS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deviceStatusCapsule")
        self.setFixedHeight(HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(LEFT_PADDING, 0, RIGHT_PADDING, 0)
        row.setSpacing(GAP)
        self.icon = StatusIcon("off", size=SHAPE)
        self.icon.setObjectName("deviceStatusIcon")
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.label = QLabel(objectName="deviceAuthSummary")
        row.addWidget(self.label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._kind = ""
        self.set_status("no_device")

    def kind(self) -> str:
        return self._kind

    def set_status(self, kind: str) -> None:
        state, cream_shape, text, cream_text = LOOKS[kind]
        self._kind = kind
        self.icon.set_state(state, waiting_for="device")
        self.icon.set_cream(cream_shape)
        set_translatable_text(self.label, text)
        self.label.setProperty("cream", cream_text)
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(*tokens.SHELL_CAPSULE_EDGE), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
