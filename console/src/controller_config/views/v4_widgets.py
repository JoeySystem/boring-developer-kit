"""Small Qt-native v4 surfaces; colours and marks remain design candidates.

No fake glass blur: cards are opaque for readable text and status. The native
window material is installed separately by appearance.install_macos_vibrancy.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QWidget

from controller_config.appearance import V4_RADIUS, V4_TOKENS


class V4Card(QFrame):
    def __init__(self, parent: QWidget | None = None, *, role: str = "secondary", dots: bool = False) -> None:
        super().__init__(parent)
        self._role = "secondary"
        self._dots = dots
        self._dot_layer = None
        self._dot_layer_size = None
        self.set_role(role)

    def set_role(self, role: str) -> None:
        if role not in {"primary", "secondary", "widget", "focus"}:
            raise ValueError(f"Unknown v4 card role: {role}")
        self._role = role
        self.setProperty("v4Role", role)
        self.setProperty("cardRole", role)
        self.update()

    def set_dots(self, dots: bool) -> None:
        self._dots = bool(dots)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        focus = self._role == "focus"
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        colour = V4_TOKENS["focus"] if focus else "#242320" if self._role == "widget" else V4_TOKENS["baseDark"]
        painter.setBrush(QColor(colour))
        edge = QColor(V4_TOKENS["information"])
        edge.setAlpha(16)
        painter.setPen(Qt.PenStyle.NoPen if focus else QPen(edge, 1))
        painter.drawRoundedRect(body, V4_RADIUS, V4_RADIUS)
        clip = QPainterPath()
        clip.addRoundedRect(body.adjusted(1, 1, -1, -1), V4_RADIUS - 1, V4_RADIUS - 1)
        painter.setClipPath(clip)
        if self._dots:
            layer_size = (self.width(), self.height(), self.devicePixelRatioF())
            if self._dot_layer_size != layer_size:
                ratio = layer_size[2]
                layer = QPixmap(math.ceil(self.width() * ratio), math.ceil(min(self.height(), 240) * ratio))
                layer.setDevicePixelRatio(ratio)
                layer.fill(Qt.GlobalColor.transparent)
                dots = QPainter(layer)
                dots.setRenderHint(QPainter.RenderHint.Antialiasing)
                fade_height = min(body.height(), 240.0)
                dots.setPen(Qt.PenStyle.NoPen)
                y = body.top() + 6
                while y < body.top() + fade_height * .78:
                    alpha = max(0, 1 - (y - body.top()) / (fade_height * .78))
                    dot = QColor(V4_TOKENS["information"])
                    dot.setAlpha(round(26 * alpha))
                    dots.setBrush(dot)
                    x = body.left() + 6
                    while x < body.right():
                        dots.drawEllipse(QRectF(x - 1.55, y - 1.55, 3.1, 3.1))
                        x += 9
                    y += 9
                dots.end()
                self._dot_layer = layer
                self._dot_layer_size = layer_size
            painter.drawPixmap(0, 0, self._dot_layer)
        painter.setPen(QPen(QColor(255, 255, 255, 46 if focus else 31), 1))
        painter.drawLine(body.left() + V4_RADIUS, body.top() + 1, body.right() - V4_RADIUS, body.top() + 1)


class UsageRings(QWidget):
    """Concentric, clockwise remaining quota: seven-day outer/five-hour inner."""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.seven_day: float | None = None
        self.five_hour: float | None = None
        self._labels = ("7D", "5H")
        # The surrounding card may compress vertically in a short window.
        # Keep the dial's layout box square so the quota meter never appears
        # stretched or clipped during responsive relayouts.
        self.setFixedSize(self.sizeHint())
        self.set_usage(seven_day=None, five_hour=None)

    def sizeHint(self) -> QSize:
        return QSize(112, 112)

    def set_labels(self, seven_day: str, five_hour: str) -> None:
        self._labels = (seven_day, five_hour)
        self._update_accessibility()
        self.update()

    def set_usage(self, *, seven_day: float | None, five_hour: float | None) -> None:
        def percent(value):
            if value is None:
                return None
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Remaining quota must be between 0 and 100, or None")
            return float(value)
        self.seven_day = percent(seven_day)
        self.five_hour = percent(five_hour)
        self._update_accessibility()
        self.update()

    @staticmethod
    def _text(value: float | None) -> str:
        return "—" if value is None else f"{value:g}%"

    def _update_accessibility(self) -> None:
        self.setAccessibleName(f"{self._labels[0]} {self._text(self.seven_day)} · {self._labels[1]} {self._text(self.five_hour)}")
        self.setToolTip(self.accessibleName())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height(), 140)
        painter.translate((self.width() - side) / 2, (self.height() - side) / 2)
        painter.scale(side / 112, side / 112)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for inset, value, name in ((6, self.seven_day, "information"), (20, self.five_hour, "agent")):
            rect = QRectF(inset, inset, 112 - inset * 2, 112 - inset * 2)
            track = QColor(V4_TOKENS[name])
            track.setAlpha(41)
            painter.setPen(QPen(track, 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawEllipse(rect)
            if value is not None and value > 0:
                painter.setPen(QPen(QColor(V4_TOKENS[name]), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                if value == 100:
                    painter.drawEllipse(rect)
                else:
                    painter.drawArc(rect, 90 * 16, -round(value / 100 * 360 * 16))
        painter.setPen(QColor(V4_TOKENS["information"]))
        font = QFont(self.font())
        font.setPixelSize(16)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(QRectF(28, 38, 56, 22), Qt.AlignmentFlag.AlignCenter, self._text(self.seven_day))
        font.setPixelSize(10)
        painter.setFont(font)
        painter.setPen(QColor(V4_TOKENS["muted"]))
        painter.drawText(QRectF(28, 59, 56, 16), Qt.AlignmentFlag.AlignCenter, self._labels[0])


# Candidate geometric marks from the handoff descriptions, not font glyphs or
# purported copies of the unavailable original mark SVGs.
STATUS_MARKS = {
    "verified": ("00100", "01110", "11111", "01110", "00100"),
    "dev": ("00100", "01100", "11100", "01100", "00100"),
    "untrusted": ("00100", "01010", "01010", "10001", "11111"),
    "nolink": ("00100", "01010", "10001", "01010", "00100"),
    "draft": ("00100", "01010", "10101", "01010", "00100"),
    "writing": ("11111", "10001", "10001", "10001", "11111"),
    "synced": ("00000", "00000", "11111", "00000", "00000"),
    "failed": ("10001", "01010", "00100", "01010", "10001"),
    "tested": ("00001", "00010", "10100", "01000", "00000"),
    "live": ("00000", "00100", "01110", "00100", "00000"),
    "idle": ("00000", "00000", "00100", "00000", "00000"),
}
STATUS_CODES = {"verified": "VERIFIED", "dev": "DEV", "untrusted": "UNTRUSTED", "nolink": "NO LINK", "draft": "DRAFT", "writing": "WRITING", "synced": "SYNCED", "failed": "FAILED", "tested": "OK", "live": "LIVE", "idle": "IDLE"}


class StatusMark(QWidget):
    def __init__(self, status: str = "idle", parent: QWidget | None = None, *, dot_size: float = 2.0) -> None:
        super().__init__(parent)
        self._dot_size = dot_size
        self.setFixedSize(self.sizeHint())
        self.set_status(status)

    def sizeHint(self) -> QSize:
        extent = math.ceil(self._dot_size * (4 * 1.40625 + 1)) + 2
        return QSize(extent, extent)

    def set_status(self, status: str) -> None:
        if status not in STATUS_MARKS:
            raise ValueError(f"Unknown status mark: {status}")
        self.status = status
        self.setAccessibleName(f"[ {STATUS_CODES[status]} ]")
        self.update()

    def paintEvent(self, event) -> None:
        colour = "ok" if self.status in {"verified", "tested", "live"} else "signal" if self.status in {"draft", "failed", "untrusted"} else "information" if self.status == "writing" else "faint" if self.status in {"nolink", "idle"} else "muted"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(V4_TOKENS[colour]))
        pitch = self._dot_size * 1.40625
        for row, pixels in enumerate(STATUS_MARKS[self.status]):
            for col, active in enumerate(pixels):
                if active == "1":
                    painter.drawEllipse(QRectF(1 + col * pitch, 1 + row * pitch, self._dot_size, self._dot_size))
