"""Reusable BORING product silhouette used by every device-facing page.

Keep product geometry, material painting, and physical-control placement in this
module. Pages may decorate the returned widget for their own purpose, but they
should not recreate the product outline or control coordinates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import math

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
    QTransform,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller_config.actions import describe_action
from controller_config.appearance import V4_STYLE, V4_TOKENS
from controller_config.digital_font import layout_text
from controller_config.i18n import translate_ui_text
from controller_config.models import DeviceSnapshot
from controller_config.views.digital_label import Boring5RLabel


# Product-design surface: update these values when the physical appearance changes.
MATRIX12_UNIT = 64
MATRIX12_GAP = 3
MATRIX12_CANVAS_SIZE = 420

# Public schematic coordinates; independent of internal product artwork.
_MODEL_MANIFEST = {'source_size': [420, 420],
 'screen_rect': [80, 80, 132, 132],
 'screen_rotation_degrees': 0,
 'controls': {'key.1': [144, 70, 208, 134],
              'key.2': [212, 70, 276, 134],
              'key.3': [280, 70, 344, 134],
              'key.4': [76, 138, 140, 202],
              'key.5': [144, 138, 208, 202],
              'key.6': [212, 138, 276, 202],
              'key.7': [280, 138, 344, 202],
              'key.8': [76, 206, 208, 270],
              'key.9': [212, 206, 276, 270],
              'key.10': [280, 206, 344, 270],
              'key.11': [144, 274, 208, 338],
              'key.12': [212, 274, 276, 338],
              'encoder': [20, 280, 130, 390],
              'joystick': [284, 280, 344, 340]}}

MATRIX12_HARDWARE_IDS = frozenset(
    {"WMP-S3-MATRIX12-V1", "WMP-S3-MATRIX12-POWER-V2"}
)
MATRIX12_AGENT_STATUS_KEYS = frozenset(
    {"key.1", "key.2", "key.4", "key.5", "key.6", "key.7"}
)

DEVICE_SILHOUETTE_STYLE = """
QPushButton#controlKey { background: #f7f7f2; border: 1px solid #aeb3b1; border-radius: 6px; text-align: left; }
QPushButton#controlKey[mapped="true"] { border: 2px solid #343839; }
QPushButton#controlKey[role="agent"] { background: #dfeaf5; border: 1px solid #9bb2c9; }
QPushButton#controlKey[role="agent"][mapped="true"] { border: 2px solid #617b99; }
QPushButton#controlKey:hover, QPushButton#secondaryControl:hover, QPushButton#encoderControl:hover, QPushButton#joystickControl:hover { border: 2px solid #e85b42; }
QPushButton#controlKey[selected="true"], QPushButton#secondaryControl[selected="true"], QPushButton#encoderControl[selected="true"], QPushButton#joystickControl[selected="true"] { background: #f7dfd7; border: 3px solid #e85b42; }
QPushButton#controlKey[role="agent"][selected="true"] { background: #cfdff2; border: 3px solid #e85b42; }
QPushButton#controlKey:focus, QPushButton#secondaryControl:focus, QPushButton#encoderControl:focus, QPushButton#joystickControl:focus { border: 3px solid #bd3e28; }
QFrame#deviceShell { background: #8a8b86; border: 8px solid #5f615e; border-radius: 24px; }
QFrame#deviceFace { background: #e6e5df; border: 2px solid #252829; border-radius: 16px; }
QFrame#displayControl { background: #171a1b; border: 3px solid #050606; border-radius: 38px; }
QFrame#displayControl QLabel { color: #f4f5f1; }
QPushButton#secondaryControl { background: #c8cbc9; border: 3px solid #717675; border-radius: 36px; }
QPushButton#encoderControl { background: #c8cbc9; border: 4px solid #717675; border-radius: 47px; }
QPushButton#joystickControl { background: #1f2324; border: 2px solid #080909; border-radius: 6px; }
QLabel#joystickKnob { color: #202324; background: #f3f3ee; border: 2px solid #c6c9c6; border-radius: 25px; font-size: 10px; font-weight: 800; }
QLabel#controlId { color: #717674; font-size: 9px; letter-spacing: 0.5px; }
QLabel#controlAction { color: #202324; font-size: 11px; font-weight: 800; }
QPushButton#controlKey[role="agent"] QLabel#controlAction { color: #2f4764; }
QLabel#controlName { color: #777c7a; font-size: 8px; }

QFrame#deviceShell {
    background: #12130f;
    border: none;
    border-radius: 56px;
}
QFrame#deviceFace {
    background: transparent;
    border: none;
    border-radius: 28px;
}
QFrame#displayControl {
    background: #121311;
    border: 2px solid #eeede4;
    border-radius: 38px;
}
QPushButton#controlKey {
    background: #2d2c28;
    border: 1px solid rgba(234, 232, 220, 128);
    border-radius: 8px;
}
QPushButton#controlKey[mapped="true"] { border: 2px solid rgba(234, 232, 220, 160); }
QPushButton#controlKey[role="agent"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34495e, stop:1 #2d4053);
    border: 1px solid #34495e;
}
QPushButton#controlKey[role="agent"][mapped="true"] { border: 2px solid #34495e; }
QPushButton#controlKey:hover,
QPushButton#secondaryControl:hover,
QPushButton#encoderControl:hover,
QPushButton#joystickControl:hover { border: 2px solid #db6b2b; }
QPushButton#controlKey[selected="true"],
QPushButton#secondaryControl[selected="true"],
QPushButton#encoderControl[selected="true"],
QPushButton#joystickControl[selected="true"] {
    background: #34312e;
    border: 3px solid #db6b2b;
}
QPushButton#controlKey[role="agent"][selected="true"] {
    background: #2c4565;
    border: 3px solid #db6b2b;
}
QLabel#controlId { color: rgba(224, 222, 211, 148); }
QLabel#controlAction { color: #f2f1e8; }
QPushButton#controlKey[role="agent"] QLabel#controlAction { color: #a3b5e7; }
QLabel#controlName { color: rgba(224, 222, 211, 128); }
QPushButton#secondaryControl,
QPushButton#encoderControl,
QPushButton#joystickControl {
    background: #171815;
    border-color: rgba(228, 226, 211, 70);
}
QLabel#joystickKnob {
    color: #eceae2;
    background: #1b1c18;
    border: none;
}
QPushButton#joystickControl {
    background: #35352f;
    border-radius: 38px;
}
QPushButton#encoderControl {
    background: #171815;
    border-width: 2px;
    border-radius: 48px;
}
"""


@dataclass
class _SpringValue:
    value: float = 0.0
    velocity: float = 0.0
    target: float = 0.0


def _system_reduces_motion() -> bool:
    """Read the native macOS accessibility preference when available."""

    try:
        from AppKit import NSWorkspace

        return bool(
            NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion()
        )
    except (AttributeError, ImportError):
        return False


class DeviceModelCanvas(QWidget):
    """Public vector schematic with interactive controls; not a live screen mirror."""

    _response_seconds = 0.4
    _settle_epsilon = 0.002

    def __init__(
        self,
        snapshot: DeviceSnapshot | None,
        mappings: dict[str, dict],
        selected_control_id: str | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, objectName="deviceModelCanvas")
        self.setFixedSize(MATRIX12_CANVAS_SIZE, MATRIX12_CANVAS_SIZE)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._source_width, self._source_height = _MODEL_MANIFEST["source_size"]
        scale = min(
            self.width() / self._source_width,
            self.height() / self._source_height,
        )
        width = self._source_width * scale
        height = self._source_height * scale
        self._model_rect = QRectF(
            (self.width() - width) / 2,
            (self.height() - height) / 2,
            width,
            height,
        )
        self._mappings = mappings
        self._platform = str(snapshot.status.get("platform", "")) if snapshot else ""
        self._selected_control_id = selected_control_id
        self._buttons: dict[str, QWidget] = {}
        self._hovered_control_id: str | None = None
        self._screen_enabled = True
        self._reduce_motion = _system_reduces_motion()
        self._springs: dict[str, _SpringValue] = {
            **{f"key.{index}": _SpringValue() for index in range(1, 13)},
            "encoder.press": _SpringValue(),
            "encoder.rotation": _SpringValue(),
            "joystick.x": _SpringValue(),
            "joystick.y": _SpringValue(),
            "joystick.press": _SpringValue(),
        }
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance_springs)
        self._clock = QElapsedTimer()

    def control_rect(self, control_id: str) -> QRectF:
        values = _MODEL_MANIFEST["controls"][control_id]
        return self._map_source_rect(values)

    def screen_rect(self) -> QRectF:
        return self._map_source_rect(_MODEL_MANIFEST["screen_rect"])

    def control_path(self, control_id: str) -> QPainterPath:
        """Recover the tilted key footprint from its projected bounding box."""
        rect = self.screen_rect() if control_id == "display" else self.control_rect(control_id)
        path = QPainterPath()
        if not control_id.startswith("key."):
            path.addEllipse(rect)
            return path
        angle = float(_MODEL_MANIFEST["screen_rotation_degrees"])
        c, s = math.cos(math.radians(angle)), abs(math.sin(math.radians(angle)))
        width = (rect.width() * c - rect.height() * s) / (c * c - s * s)
        height = (rect.height() * c - rect.width() * s) / (c * c - s * s)
        path.addRoundedRect(QRectF(-width / 2, -height / 2, width, height), 3, 3)
        transform = QTransform().translate(rect.center().x(), rect.center().y()).rotate(angle)
        return transform.map(path)

    def update_control_mask(self, control_id: str, widget: QWidget) -> None:
        # QWidget masks route an overlapping corner through to the visible key
        # underneath; hitButton alone would still swallow that mouse event.
        path = self.control_path(control_id).translated(-widget.x(), -widget.y())
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def attach_control(self, control_id: str, widget: QWidget) -> None:
        self._buttons[control_id] = widget
        self.update_control_mask(control_id, widget)
        widget.installEventFilter(self)
        if isinstance(widget, QPushButton):
            widget.pressed.connect(
                lambda value=control_id: self._set_pressed(value, True)
            )
            widget.released.connect(
                lambda value=control_id: self._set_pressed(value, False)
            )
            widget.clicked.connect(
                lambda _checked=False, value=control_id: self.preview_control(
                    self._semantic_control(value)
                )
            )

    def set_selected_control(self, control_id: str | None) -> None:
        self._selected_control_id = control_id
        self.update()

    def set_screen_enabled(self, enabled: bool) -> None:
        self._screen_enabled = bool(enabled)
        self.update()

    def preview_control(self, control_id: str) -> None:
        """Retarget the current presentation instead of restarting a keyframe."""

        if control_id.startswith("key."):
            self._pulse(control_id, 1.0, 180)
            return
        if control_id in {"encoder.cw", "encoder.ccw"}:
            direction = 1 if control_id.endswith(".cw") else -1
            spring = self._springs["encoder.rotation"]
            spring.target += direction * 34.0
            self._start_motion()
            return
        if control_id == "encoder.press":
            self._pulse("encoder.press", 1.0, 180)
            return
        if control_id == "joystick.press":
            self._pulse("joystick.press", 1.0, 180)
            return
        joystick_targets = {
            "joystick.up": (0.0, -1.0),
            "joystick.right": (1.0, 0.0),
            "joystick.down": (0.0, 1.0),
            "joystick.left": (-1.0, 0.0),
        }
        if control_id in joystick_targets:
            x, y = joystick_targets[control_id]
            self._springs["joystick.x"].target = x
            self._springs["joystick.y"].target = y
            self._start_motion()
            self._schedule_release(260, self._release_joystick)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        control_id = watched.property("controlId")
        if isinstance(control_id, str):
            if event.type() == QEvent.Enter:
                self._hovered_control_id = control_id
                self.update()
            elif event.type() == QEvent.Leave and self._hovered_control_id == control_id:
                self._hovered_control_id = None
                self.update()
        return super().eventFilter(watched, event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setPen(QPen(QColor("#77776d"), 2))
        painter.setBrush(QColor("#25251f"))
        painter.drawRoundedRect(self._model_rect.adjusted(8, 16, -8, -16), 28, 28)
        painter.setBrush(QColor("#c9c9bf"))
        for control_id in _MODEL_MANIFEST["controls"]:
            painter.drawPath(self.control_path(control_id))
        self._paint_screen(painter)
        for index in range(1, 13):
            self._paint_key(painter, f"key.{index}")
        self._paint_encoder(painter)
        self._paint_joystick(painter)

    def _paint_screen(self, painter: QPainter) -> None:
        screen = self.screen_rect()
        painter.save()
        painter.translate(screen.center())
        painter.rotate(float(_MODEL_MANIFEST["screen_rotation_degrees"]))
        local = QRectF(
            -screen.width() / 2,
            -screen.height() / 2,
            screen.width(),
            screen.height(),
        )
        clip = QPainterPath()
        clip.addEllipse(local)
        painter.setClipPath(clip)
        painter.fillRect(local, QColor("#050606"))
        if self._screen_enabled:
            painter.setPen(QColor("#eeede4"))
            painter.drawText(local, Qt.AlignCenter, "MIST")
        elif not self._screen_enabled:
            painter.fillRect(local, QColor(0, 0, 0, 190))
        painter.restore()

    def _paint_key(self, painter: QPainter, control_id: str) -> None:
        rect = self.control_rect(control_id)
        pressed = self._springs[control_id].value
        rect.translate(0, pressed * 2.4)
        key_path = self.control_path(control_id).translated(0, pressed * 2.4)
        button = self._buttons.get(control_id)
        selected = self._matches_selected(control_id)
        hovered = self._hovered_control_id == control_id
        role = "agent" if control_id in MATRIX12_AGENT_STATUS_KEYS else "human"

        if button is not None:
            light = button.property("lightingColor")
            if isinstance(light, QColor) and light.alpha() > 0:
                glow = QRadialGradient(rect.center(), rect.width() * 0.72)
                glow.setColorAt(0, light)
                glow.setColorAt(1, QColor(light.red(), light.green(), light.blue(), 0))
                painter.setPen(Qt.NoPen)
                painter.setBrush(glow)
                painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 10, 10)

        emphasis = max(pressed, 0.72 if selected else 0.32 if hovered else 0.0)
        if role == "agent" and emphasis == 0:
            painter.setBrush(QColor(91, 118, 164, 18))
            painter.setPen(Qt.NoPen)
            painter.drawPath(key_path)
        if emphasis > 0:
            painter.setBrush(QColor(219, 107, 43, round(30 + emphasis * 72)))
            painter.setPen(
                QPen(QColor(219, 107, 43, round(100 + emphasis * 135)), 1.5)
            )
            painter.drawPath(key_path)

        mapping = self._mappings.get(control_id)
        text = translate_ui_text(_mapping_action_summary(self._mappings, control_id, self._platform))
        if mapping is None:
            text = ""
        if control_id in {"key.8", "key.9", "key.10"}:
            text = f"BT {int(control_id.split('.')[1]) - 7}  {text}".strip()
        if text:
            painter.save()
            painter.translate(rect.center())
            painter.rotate(float(_MODEL_MANIFEST["screen_rotation_degrees"]))
            label_rect = QRectF(-rect.width() * 0.42, -7, rect.width() * 0.84, 14)
            font = painter.font()
            font.setPixelSize(8)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            text = painter.fontMetrics().elidedText(
                text,
                Qt.ElideRight,
                round(label_rect.width()),
            )
            painter.setPen(QColor("#252724"))
            painter.drawText(label_rect, Qt.AlignCenter, text)
            painter.restore()

    def _paint_encoder(self, painter: QPainter) -> None:
        rect = self.control_rect("encoder")
        press = self._springs["encoder.press"].value
        rect = rect.adjusted(
            9 + press * 2,
            7 + press * 2,
            -9 - press * 2,
            -7 - press * 2,
        )
        angle = self._springs["encoder.rotation"].value
        selected = self._matches_selected("encoder")
        hovered = self._hovered_control_id == "encoder"
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(
            QPen(
                QColor(219, 107, 43, 220 if selected else 120 if hovered else 58),
                2,
            )
        )
        painter.drawArc(rect, 34 * 16, 278 * 16)
        radians = math.radians(angle - 90)
        center = rect.center()
        radius = min(rect.width(), rect.height()) * 0.38
        point = QPointF(
            center.x() + math.cos(radians) * radius,
            center.y() + math.sin(radians) * radius,
        )
        painter.setBrush(QColor("#DB6B2B"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(point, 3.1, 3.1)
        painter.restore()

    def _paint_joystick(self, painter: QPainter) -> None:
        rect = self.control_rect("joystick").adjusted(18, 18, -18, -18)
        press = self._springs["joystick.press"].value
        dx = self._springs["joystick.x"].value * 7
        dy = self._springs["joystick.y"].value * 7 + press * 1.8
        rect.translate(dx, dy)
        selected = self._matches_selected("joystick")
        hovered = self._hovered_control_id == "joystick"
        painter.setBrush(QColor(29, 31, 29, 210))
        painter.setPen(
            QPen(
                QColor(219, 107, 43, 230 if selected else 130 if hovered else 65),
                1.8,
            )
        )
        painter.drawEllipse(rect)

    def _map_source_rect(self, values: list[float]) -> QRectF:
        x1, y1, x2, y2 = values
        scale = self._model_rect.width() / self._source_width
        return QRectF(
            self._model_rect.left() + x1 * scale,
            self._model_rect.top() + y1 * scale,
            (x2 - x1) * scale,
            (y2 - y1) * scale,
        )

    def _semantic_control(self, group_id: str) -> str:
        if (
            isinstance(self._selected_control_id, str)
            and self._selected_control_id.startswith(f"{group_id}.")
        ):
            return self._selected_control_id
        return {"encoder": "encoder.cw", "joystick": "joystick.right"}.get(group_id, group_id)

    def _matches_selected(self, control_id: str) -> bool:
        return self._selected_control_id == control_id or (
            control_id in {"encoder", "joystick"}
            and isinstance(self._selected_control_id, str)
            and self._selected_control_id.startswith(f"{control_id}.")
        )

    def _set_pressed(self, control_id: str, pressed: bool) -> None:
        semantic = self._semantic_control(control_id)
        spring_id = semantic if semantic in self._springs else control_id
        if spring_id in self._springs:
            self._springs[spring_id].target = 1.0 if pressed else 0.0
            self._start_motion()

    def _pulse(self, spring_id: str, target: float, hold_ms: int) -> None:
        self._springs[spring_id].target = target
        self._start_motion()
        self._schedule_release(
            hold_ms, lambda value=spring_id: self._release_spring(value)
        )

    def _schedule_release(self, delay_ms: int, callback: Callable[[], None]) -> None:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(timer.deleteLater)
        timer.start(delay_ms)

    def _release_spring(self, spring_id: str) -> None:
        spring = self._springs.get(spring_id)
        if spring is not None:
            spring.target = 0.0
            self._start_motion()

    def _release_joystick(self) -> None:
        self._springs["joystick.x"].target = 0.0
        self._springs["joystick.y"].target = 0.0
        self._start_motion()

    def _start_motion(self) -> None:
        if self._reduce_motion:
            for spring in self._springs.values():
                spring.value = spring.target
                spring.velocity = 0.0
            self.update()
            return
        if not self._timer.isActive():
            self._clock.restart()
            self._timer.start()

    def _advance_springs(self) -> None:
        elapsed = max(0.001, min(0.034, self._clock.restart() / 1000.0))
        omega = 2 * math.pi / self._response_seconds
        stiffness = omega * omega
        damping = 2 * omega
        active = False
        for spring in self._springs.values():
            acceleration = (
                -stiffness * (spring.value - spring.target)
                - damping * spring.velocity
            )
            spring.velocity += acceleration * elapsed
            spring.value += spring.velocity * elapsed
            if (
                abs(spring.value - spring.target) < self._settle_epsilon
                and abs(spring.velocity) < self._settle_epsilon
            ):
                spring.value = spring.target
                spring.velocity = 0.0
            else:
                active = True
        self.update()
        if not active:
            self._timer.stop()


class KeycapButton(QPushButton):
    """Top-view keycap material shared by mapping, prompt, and lighting views."""

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.property("wireframeOverlay"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.55)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        agent = self.property("role") == "agent"
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor("#F2F1EE" if agent else "#F4F0E7"))
        gradient.setColorAt(1, QColor("#DCDAD5" if agent else "#DDD8CC"))
        painter.setPen(
            QPen(
                QColor(V4_TOKENS["control"])
                if self.property("selected")
                else QColor(255, 255, 255, 30),
                2,
            )
        )
        painter.setBrush(gradient)
        radius = rect.height() * 2.49 / 18
        painter.drawRoundedRect(rect, radius, radius)
        top = rect.adjusted(3, 3, -3, -3)
        dish = QRadialGradient(top.center(), top.width() * 0.65)
        dish.setColorAt(0, QColor(130, 125, 115, 16))
        dish.setColorAt(1, QColor(255, 255, 255, 65))
        painter.setBrush(dish)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        painter.drawRoundedRect(top, max(1, radius - 3), max(1, radius - 3))
        light = self.property("lightingColor")
        if isinstance(light, QColor) and light.alpha() > 0:
            glow = QRadialGradient(top.center(), top.width() * 0.9)
            glow.setColorAt(0, light.lighter(135))
            glow.setColorAt(0.35, light)
            glow.setColorAt(1, QColor(light.red(), light.green(), light.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(top, max(1, radius - 3), max(1, radius - 3))
        slot = self.property("bluetoothSlot")
        if slot is not None:
            painter.save()
            painter.translate(10, 9)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(
                QPen(
                    QColor("#69665E"),
                    1.3,
                    Qt.SolidLine,
                    Qt.RoundCap,
                    Qt.RoundJoin,
                )
            )
            symbol = QPainterPath()
            for points in (
                ((3, 0), (8, 4), (0, 12)),
                ((3, 16), (8, 12), (0, 4)),
                ((3, 0), (3, 16)),
            ):
                symbol.moveTo(*points[0])
                for point in points[1:]:
                    symbol.lineTo(*point)
            painter.drawPath(symbol)
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(13, 0, 12, 16), Qt.AlignLeft | Qt.AlignVCenter, str(slot)
            )
            painter.restore()
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#2A2824"), 1, Qt.DashLine))
            painter.drawRoundedRect(
                top.adjusted(2, 2, -2, -2),
                max(1, radius - 5),
                max(1, radius - 5),
            )


class _KeycapInscription(QLabel):
    """Keep a full action description while fitting its physical keycap."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(
            self.text(), Qt.ElideRight, self.contentsRect().width()
        )
        painter.drawText(self.contentsRect(), Qt.AlignLeft | Qt.AlignVCenter, text)


class _RotaryButton(QPushButton):
    def paintEvent(self, event) -> None:  # noqa: N802
        if self.property("wireframeOverlay"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else 0.55)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        joystick = self.objectName() == "joystickControl"
        material = QRadialGradient(
            rect.center() - rect.topLeft() * 0.15, rect.width() * 0.7
        )
        material.setColorAt(0, QColor("#37372F" if joystick else "#E0DED5"))
        material.setColorAt(1, QColor("#25261F" if joystick else "#ABA9A1"))
        painter.setBrush(material)
        painter.setPen(
            QPen(
                QColor(
                    "#EFEAE0"
                    if self.property("selected") or self.hasFocus()
                    else "#171813"
                ),
                2,
            )
        )
        painter.drawEllipse(rect)
        if not joystick:
            painter.setPen(QPen(QColor("#22231D"), 3, Qt.SolidLine, Qt.RoundCap))
            for x1, y1, x2, y2 in (
                (0.16, 0.3, 0.34, 0.4),
                (0.66, 0.4, 0.84, 0.3),
                (0.5, 0.72, 0.5, 0.91),
            ):
                painter.drawLine(
                    round(self.width() * x1),
                    round(self.height() * y1),
                    round(self.width() * x2),
                    round(self.height() * y2),
                )


class DeviceModelShell(QFrame):
    """Scale the render and its real Qt hit targets in the same coordinate space."""

    def scale_to(self, side: int) -> None:
        if self.width() == side:
            return
        self.setFixedSize(side, side)
        self._face.setGeometry(0, 0, side, side)
        canvas = self._canvas
        canvas.setFixedSize(side, side)
        scale = min(side / canvas._source_width, side / canvas._source_height)
        width, height = canvas._source_width * scale, canvas._source_height * scale
        canvas._model_rect = QRectF((side - width) / 2, (side - height) / 2, width, height)
        for control_id, control in canvas._buttons.items():
            rect = canvas.screen_rect() if control_id == "display" else canvas.control_rect(control_id)
            width, height = max(1, round(rect.width())), max(1, round(rect.height()))
            control.setStyleSheet(
                f"min-width: {width}px; max-width: {width}px; "
                f"min-height: {height}px; max-height: {height}px; "
                "background: transparent; border: none; padding: 0;"
            )
            control.setFixedSize(width, height)
            control.move(round(rect.left()), round(rect.top()))
            canvas.update_control_mask(control_id, control)
        canvas.update()


def create_device_silhouette(
    snapshot: DeviceSnapshot | None,
    *,
    mappings: dict[str, dict] | None = None,
    on_control: Callable[[str], None] | None = None,
    selected_control_id: str | None = None,
) -> QFrame:
    """Create the canonical product silhouette for any application page."""

    resolved_mappings = (snapshot.mappings if snapshot else {}) if mappings is None else mappings
    device_shell = DeviceModelShell(objectName="deviceShell") if snapshot is None or str(snapshot.identity.get("hardware_id", "")) in MATRIX12_HARDWARE_IDS else QFrame(objectName="deviceShell")
    device_shell.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    if snapshot is None or str(snapshot.identity.get("hardware_id", "")) in MATRIX12_HARDWARE_IDS:
        device_shell.setFixedSize(MATRIX12_CANVAS_SIZE, MATRIX12_CANVAS_SIZE)
        device_shell.setProperty("usesControlSchematic", True)
        device_shell.setStyleSheet(
            "QFrame#deviceShell { background: transparent; border: none; }"
        )
        face = QFrame(device_shell, objectName="deviceFace")
        face.setGeometry(0, 0, MATRIX12_CANVAS_SIZE, MATRIX12_CANVAS_SIZE)
        face.setStyleSheet(
            "QFrame#deviceFace { background: transparent; border: none; }"
        )
        canvas = DeviceModelCanvas(
            snapshot,
            resolved_mappings,
            selected_control_id,
            face,
        )
        canvas.setGeometry(0, 0, MATRIX12_CANVAS_SIZE, MATRIX12_CANVAS_SIZE)
        canvas.show()
        holder = QWidget()
        grid = _matrix12_control_layout(
            snapshot,
            resolved_mappings,
            str(snapshot.status.get("platform", "")) if snapshot else "",
            on_control,
            selected_control_id,
        )
        holder.setLayout(grid)
        while grid.count():
            control = grid.takeAt(0).widget()
            control.setParent(face)
            control_id = control.property("controlId")
            if control_id == "display":
                control.setToolTip("屏幕动画为效果示意，不是设备实时画面。")
            rect = (
                canvas.screen_rect()
                if control_id == "display"
                else canvas.control_rect(control_id)
            )
            width = max(24, round(rect.width()))
            height = max(24, round(rect.height()))
            control.setFixedSize(width, height)
            control.move(round(rect.left()), round(rect.top()))
            control.setStyleSheet(
                f"min-width: {width}px; max-width: {width}px; "
                f"min-height: {height}px; max-height: {height}px; "
                "background: transparent; border: none; padding: 0;"
            )
            for label in control.findChildren(QLabel):
                label.hide()
            control.setProperty("wireframeOverlay", True)
            canvas.attach_control(control_id, control)
            control.show()
        holder.deleteLater()
        canvas.lower()
        device_shell._canvas = canvas
        device_shell._face = face
        return device_shell

    shell_layout = QVBoxLayout(device_shell)
    shell_layout.setContentsMargins(12, 12, 12, 12)
    device_face = QFrame(objectName="deviceFace")
    face_layout = QVBoxLayout(device_face)
    face_layout.setContentsMargins(24, 22, 24, 22)
    face_layout.addLayout(
        create_control_layout(
            snapshot,
            mappings=resolved_mappings,
            on_control=on_control,
            selected_control_id=selected_control_id,
        )
    )
    shell_layout.addWidget(device_face)
    return device_shell


def create_prompt_silhouette(
    snapshot: DeviceSnapshot,
    *,
    application_style: str = "",
) -> QFrame:
    """Create the label-free silhouette shown behind four prompt directions."""

    shell = create_device_silhouette(snapshot, mappings=snapshot.mappings)
    shell.setStyleSheet(
        application_style
        + V4_STYLE
        + "QFrame#deviceShell { background: transparent; border: none; }"
    )
    for label in shell.findChildren(QLabel):
        label.hide()
    for key in shell.findChildren(KeycapButton):
        key.setProperty("lightingPreview", True)
        key.setFocusPolicy(Qt.NoFocus)
        key.setToolTip("")
    return shell


def create_lighting_silhouette_preview(
    snapshot: DeviceSnapshot,
    editor,
) -> QWidget:
    """Create the shared silhouette adapted for local lighting configuration."""

    stage = QWidget(objectName="lightingDevicePreview")
    stage.setMinimumWidth(426)
    stage.setMinimumHeight(460)
    layout = QVBoxLayout(stage)
    layout.setContentsMargins(0, 20, 0, 20)
    layout.addStretch(1)
    swatches = {
        button.property("controlId"): button
        for _, button in editor._under_key_colors
    }
    shell = create_device_silhouette(snapshot, mappings=snapshot.mappings)
    canvas = shell.findChild(DeviceModelCanvas, "deviceModelCanvas")
    display = shell.findChild(QFrame, "displayControl")
    if display is not None:
        for label in display.findChildren(QLabel):
            label.hide()
        if canvas is None:
            brand = Boring5RLabel(
                "BORING", scale=0.5, color="#EFEAE0", alignment=Qt.AlignCenter
            )
            display.layout().addWidget(brand, 0, Qt.AlignCenter)

            def update_display(*_args):
                enabled = editor.values()[2].get("brightness", 0) > 0
                brand.setVisible(enabled)
                ring_color = "#A7B6E4" if enabled else "#41413B"
                display.setStyleSheet(
                    "QFrame#displayControl { background: #121311; "
                    f"border: 2px solid {ring_color}; border-radius: 26px; }}"
                )
        else:
            def update_display(*_args):
                canvas.set_screen_enabled(
                    editor.values()[2].get("brightness", 0) > 0
                )

        control = getattr(editor, "_display_brightness", None)
        if isinstance(control, QComboBox):
            control.currentIndexChanged.connect(update_display)
        elif isinstance(control, QSpinBox):
            control.valueChanged.connect(update_display)
        update_display()
    encoder = shell.findChild(QPushButton, "encoderControl")
    if encoder is not None:
        for label in encoder.findChildren(QLabel):
            label.hide()
        encoder.setToolTip(translate_ui_text("旋钮不带可配置灯光"))
    for key in shell.findChildren(KeycapButton):
        control_id = key.property("controlId")
        for inscription in key.findChildren(QLabel):
            inscription.hide()
        key.setAccessibleName(str(control_id))
        if control_id in swatches:
            key.setToolTip(translate_ui_text("点击调整该按键灯颜色"))
            key.setFocusPolicy(Qt.StrongFocus)
            key.clicked.connect(swatches[control_id].click)
        else:
            key.setToolTip(
                translate_ui_text("Agent 灯光由任务状态控制，此处不模拟状态颜色。")
            )
    layout.addWidget(shell, 0, Qt.AlignCenter)
    caption = QLabel("本地效果示意 · 点击白色按键调整灯色", objectName="muted")
    caption.setWordWrap(True)
    caption.setAlignment(Qt.AlignCenter)
    layout.addWidget(caption)
    note = QLabel(
        "Agent 灯光由任务状态控制，此处不模拟状态颜色。",
        objectName="muted",
    )
    note.setWordWrap(True)
    note.setAlignment(Qt.AlignCenter)
    layout.addWidget(note)
    layout.addStretch(1)

    def update_lighting(lighting):
        colors = lighting.get("under_key", [])
        brightness = (
            min(80, lighting.get("brightness", 0)) / 80
            if lighting.get("enabled")
            else 0
        )
        for key in shell.findChildren(KeycapButton):
            control_id = key.property("controlId")
            color = QColor(0, 0, 0, 0)
            if control_id in swatches:
                index = int(control_id.split(".")[-1]) - 1
                if index < len(colors):
                    rgb = colors[index]
                    peak = max(rgb.values())
                    if peak > 0:
                        opacity = round(235 * (brightness * peak / 255) ** 0.45)
                        color = QColor(
                            round(rgb["r"] * 255 / peak),
                            round(rgb["g"] * 255 / peak),
                            round(rgb["b"] * 255 / peak),
                            opacity,
                        )
            key.setProperty("lightingColor", color)
            key.update()
        if canvas is not None:
            canvas.update()

    editor.lighting_changed.connect(update_lighting)
    update_lighting(editor.lighting_value())
    return stage


def create_control_layout(
    snapshot: DeviceSnapshot,
    *,
    mappings: dict[str, dict] | None = None,
    on_control: Callable[[str], None] | None = None,
    selected_control_id: str | None = None,
):
    """Create the control arrangement used inside the product silhouette."""

    resolved_mappings = (snapshot.mappings if snapshot else {}) if mappings is None else mappings
    platform = str(snapshot.status.get("platform", ""))
    hardware_id = str(snapshot.identity.get("hardware_id", ""))
    if hardware_id in MATRIX12_HARDWARE_IDS:
        return _matrix12_control_layout(
            snapshot,
            resolved_mappings,
            platform,
            on_control,
            selected_control_id,
        )
    return _generic_control_layout(
        snapshot,
        resolved_mappings,
        platform,
        on_control,
        selected_control_id,
    )


def _matrix12_control_layout(
    snapshot: DeviceSnapshot | None,
    mappings: dict[str, dict],
    platform: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(MATRIX12_GAP)
    grid.setVerticalSpacing(MATRIX12_GAP)
    grid.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    mode = str(snapshot.status.get("operating_mode", "")) if snapshot else ""

    _add_physical_control(grid, _display_tile(MATRIX12_UNIT), "display", 0, 0)
    positions = {
        "key.1": (0, 1, 1),
        "key.2": (0, 2, 1),
        "key.3": (0, 3, 1),
        "key.4": (1, 0, 1),
        "key.5": (1, 1, 1),
        "key.6": (1, 2, 1),
        "key.7": (1, 3, 1),
        "key.8": (2, 0, 2),
        "key.9": (2, 2, 1),
        "key.10": (2, 3, 1),
        "key.11": (3, 1, 1),
        "key.12": (3, 2, 1),
    }
    for control_id, (row, column, column_span) in positions.items():
        if snapshot is None or control_id in snapshot.controls:
            tile = _control_tile(
                control_id, mappings.get(control_id), platform, mode
            )
            _configure_control_button(
                tile, control_id, on_control, selected_control_id
            )
            if snapshot is None:
                title = _physical_control_name(control_id)
                for label in tile.findChildren(QLabel):
                    label.setText(control_id.replace("key.", "K"))
                    label.setToolTip(title)
                tile.setToolTip(title)
                tile.setAccessibleName(title)
            tile.setFixedSize(
                MATRIX12_UNIT * column_span
                + MATRIX12_GAP * (column_span - 1),
                MATRIX12_UNIT,
            )
            _add_physical_control(
                grid, tile, control_id, row, column, column_span
            )

    joystick = _joystick_tile(
        [
            (
                "↑/↓",
                "/".join(
                    _mapping_action_summary(mappings, control_id, platform)
                    for control_id in ("joystick.up", "joystick.down")
                ),
                "/".join(
                    _mapping_name(mappings, control_id)
                    for control_id in ("joystick.up", "joystick.down")
                ),
            ),
            (
                "←/→",
                "/".join(
                    _mapping_action_summary(mappings, control_id, platform)
                    for control_id in ("joystick.left", "joystick.right")
                ),
                "/".join(
                    _mapping_name(mappings, control_id)
                    for control_id in ("joystick.left", "joystick.right")
                ),
            ),
            (
                "按",
                _mapping_action_summary(mappings, "joystick.press", platform),
                _mapping_name(mappings, "joystick.press"),
            ),
        ]
    )
    encoder = _rotary_tile(
        [
            (
                "逆时针",
                _mapping_action_summary(mappings, "encoder.ccw", platform),
                _mapping_name(mappings, "encoder.ccw"),
            ),
            (
                "顺时针",
                _mapping_action_summary(mappings, "encoder.cw", platform),
                _mapping_name(mappings, "encoder.cw"),
            ),
            (
                "按",
                _mapping_action_summary(mappings, "encoder.press", platform),
                _mapping_name(mappings, "encoder.press"),
            ),
        ]
    )
    joystick.setFixedSize(MATRIX12_UNIT, MATRIX12_UNIT)
    encoder.setFixedSize(128, 128)
    _configure_control_button(encoder, "encoder", on_control, selected_control_id)
    _configure_control_button(joystick, "joystick", on_control, selected_control_id)
    if snapshot is None:
        for control, title in ((encoder, "旋钮"), (joystick, "摇杆")):
            control.setToolTip(title)
            control.setAccessibleName(title)
    _add_physical_control(grid, encoder, "encoder", 3, 0)
    _add_physical_control(grid, joystick, "joystick", 3, 3)
    return grid


def _generic_control_layout(
    snapshot: DeviceSnapshot | None,
    mappings: dict[str, dict],
    platform: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> QHBoxLayout:
    root = QHBoxLayout()
    root.setSpacing(20)
    key_grid = QGridLayout()
    key_grid.setSpacing(8)
    keys = [control for control in snapshot.controls if control.startswith("key.")]
    columns = min(4, max(1, len(keys)))
    mode = str(snapshot.status.get("operating_mode", "")) if snapshot else ""
    for index, control_id in enumerate(keys):
        tile = _control_tile(control_id, mappings.get(control_id), platform, mode)
        _configure_control_button(tile, control_id, on_control, selected_control_id)
        key_grid.addWidget(tile, index // columns, index % columns)
    root.addLayout(key_grid, 3)

    secondary = QVBoxLayout()
    secondary.setSpacing(8)
    for prefix, label in (("encoder.", "旋钮"), ("joystick.", "摇杆")):
        controls = [
            control for control in snapshot.controls if control.startswith(prefix)
        ]
        group = _multi_action_tile(
            label,
            [
                (
                    control_display_name(control_id).removeprefix(label),
                    _mapping_action_summary(mappings, control_id, platform),
                    _mapping_name(mappings, control_id),
                )
                for control_id in controls
            ],
        )
        _configure_control_button(
            group,
            prefix.removesuffix("."),
            on_control,
            selected_control_id,
        )
        secondary.addWidget(group)
    secondary.addStretch(1)
    root.addLayout(secondary, 2)
    return root


def _add_physical_control(
    layout: QGridLayout,
    widget: QWidget,
    control_id: str,
    row: int,
    column: int,
    column_span: int = 1,
) -> None:
    widget.setProperty("controlId", control_id)
    widget.setProperty("gridRow", row)
    widget.setProperty("gridColumn", column)
    widget.setProperty("gridColumnSpan", column_span)
    layout.addWidget(widget, row, column, 1, column_span, Qt.AlignCenter)


def _display_tile(size: int = 76) -> QFrame:
    tile = QFrame(objectName="displayControl")
    tile.setFixedSize(size, size)
    box = QVBoxLayout(tile)
    box.setContentsMargins(12, 10, 12, 10)
    title = QLabel("圆屏")
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("font-weight: 800;")
    subtitle = QLabel("设备状态")
    subtitle.setAlignment(Qt.AlignCenter)
    subtitle.setStyleSheet("font-size: 10px; color: #aaa99f;")
    box.addWidget(title)
    box.addWidget(subtitle)
    return tile


def _multi_action_tile(
    title: str, rows: list[tuple[str, str, str]]
) -> QPushButton:
    tile = QPushButton(objectName="secondaryControl")
    tile.setMinimumSize(76, 64)
    box = QVBoxLayout(tile)
    box.setContentsMargins(6, 5, 6, 6)
    box.setSpacing(3)
    heading = QLabel(title)
    heading.setAlignment(Qt.AlignCenter)
    heading.setStyleSheet("font-weight: 800;")
    box.addWidget(heading)
    for label, action, name in rows:
        text = QLabel(f"{label} · {action}", objectName="controlId")
        text.setAlignment(Qt.AlignCenter)
        text.setStyleSheet("font-size: 8px;")
        text.setToolTip(f"实际动作：{action}\n功能名称：{name}")
        box.addWidget(text)
    return tile


def _rotary_tile(rows: list[tuple[str, str, str]]) -> QPushButton:
    tile = _RotaryButton(objectName="encoderControl")
    tile.setToolTip(
        "\n".join(f"{label} · {action}（{name}）" for label, action, name in rows)
    )
    return tile


def _joystick_tile(rows: list[tuple[str, str, str]]) -> QPushButton:
    tile = _RotaryButton(objectName="joystickControl")
    tile.setToolTip(
        "\n".join(f"{label} · {action}（{name}）" for label, action, name in rows)
    )
    return tile


def _mapping_name(mappings: dict[str, dict], control_id: str) -> str:
    mapping = mappings.get(control_id)
    return str(mapping.get("short_name", "未映射")) if mapping else "未映射"


def _mapping_action_summary(
    mappings: dict[str, dict], control_id: str, platform: str
) -> str:
    mapping = mappings.get(control_id)
    action = mapping.get("action") if isinstance(mapping, dict) else None
    return describe_action(action, platform=platform, compact=True)


def _control_tile(
    control_id: str,
    mapping: dict | None,
    platform: str,
    mode: str,
) -> QPushButton:
    tile = KeycapButton(objectName="controlKey")
    tile.setProperty(
        "bluetoothSlot", {"key.8": 1, "key.9": 2, "key.10": 3}.get(control_id)
    )
    tile.setProperty("mapped", mapping is not None)
    role = "agent" if control_id in MATRIX12_AGENT_STATUS_KEYS else "human"
    tile.setProperty("role", role)
    tile.setMinimumSize(76, 64)
    box = QVBoxLayout(tile)
    box.setContentsMargins(7, 7, 7, 8)
    control_label = _digital_when_supported(
        f"AGENT {control_id.split('.', 1)[1]}" if role == "agent" else control_id,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlId",
    )
    action = mapping.get("action") if mapping else None
    action_summary = describe_action(action, platform=platform, compact=True)
    display_summary = (
        "状态未接入" if role == "agent" and mode == "codex" else action_summary
    )
    action_label = _digital_when_supported(
        display_summary,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlAction",
    )
    action_label.setWordWrap(True)
    custom_name = str(mapping.get("short_name", "")) if mapping else ""
    name_text = (
        f"动作 · {action_summary}"
        if role == "agent" and mode == "codex"
        else (custom_name or "—")
    )
    name_label = _digital_when_supported(
        name_text,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlName",
    )
    if custom_name and not (role == "agent" and mode == "codex"):
        name_label.setProperty("boringI18nSkip_text", True)
    name_label.setToolTip(
        f"功能名称：{custom_name}" if custom_name else "未设置功能名称"
    )
    control_label.setParent(tile)
    control_label.hide()
    name_label.setParent(tile)
    name_label.hide()
    box.addStretch(1)
    if role == "human":
        inscription = _KeycapInscription(
            action_summary, objectName="keycapInscription"
        )
        inscription.setMinimumWidth(0)
        inscription.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        inscription.setToolTip(action_summary)
        inscription.setStyleSheet(
            "color: #2A2824; font-size: 12px; font-weight: 600; "
            "background: transparent;"
        )
        inscription.setAttribute(Qt.WA_TransparentForMouseEvents)
        box.addWidget(inscription, 0, Qt.AlignBottom)
    action_label.setParent(tile)
    action_label.hide()
    return tile


def _configure_control_button(
    button: QPushButton,
    control_id: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> None:
    is_selected = selected_control_id == control_id or (
        control_id in {"encoder", "joystick"}
        and isinstance(selected_control_id, str)
        and selected_control_id.startswith(f"{control_id}.")
    )
    button.setProperty("selected", is_selected)
    button.setAccessibleName(f"设置{_physical_control_name(control_id)}")
    details = button.toolTip()
    button.setToolTip(
        f"点击设置{_physical_control_name(control_id)}"
        + (f"\n{details}" if details else "")
    )
    inscription = button.findChild(QLabel, "keycapInscription")
    if inscription is not None:
        button.setToolTip(f"{button.toolTip()}\n{inscription.text()}")
    for label in button.findChildren(QLabel):
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
    if on_control is None:
        button.setFocusPolicy(Qt.NoFocus)
        return
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(
        lambda _checked=False, value=control_id: on_control(value)
    )


def _digital_when_supported(
    text: str,
    *,
    preferred_scale: float,
    minimum_scale: float,
    maximum_width: float | None = None,
    object_name: str | None = None,
) -> QLabel:
    try:
        unscaled_layout = layout_text(text)
    except ValueError:
        unscaled_layout = None
    if unscaled_layout is not None and text:
        scale = preferred_scale
        if maximum_width is not None and unscaled_layout.width > 0:
            scale = min(scale, maximum_width / unscaled_layout.width)
        if scale >= minimum_scale:
            return Boring5RLabel(text, scale=scale, objectName=object_name)
    return QLabel(text, objectName=object_name)


def _physical_control_name(control_id: str) -> str:
    if control_id == "encoder":
        return "旋钮"
    if control_id == "joystick":
        return "摇杆"
    return control_display_name(control_id)


def control_display_name(control_id: str) -> str:
    if control_id.startswith("key."):
        index = control_id.split(".", 1)[1]
        return (
            f"透明状态键 {index}"
            if control_id in MATRIX12_AGENT_STATUS_KEYS
            else f"白色按键 {index}"
        )
    return {
        "encoder.ccw": "旋钮逆时针",
        "encoder.cw": "旋钮顺时针",
        "encoder.press": "旋钮按下",
        "joystick.up": "摇杆向上",
        "joystick.down": "摇杆向下",
        "joystick.left": "摇杆向左",
        "joystick.right": "摇杆向右",
        "joystick.press": "摇杆按下",
    }.get(control_id, control_id)
