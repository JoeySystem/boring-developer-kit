"""The device on the Mode page (Mode页 定稿 v1, 2026-11-19).

The same flat top view as the lighting page (设备图俯视图 正式稿 v0.5), used
the Mode page way:

- Every key can be clicked, the six status-light keys too, and so can the
  knob ("encoder") and the joystick ("joystick").
- Function keys carry what they do, in small text on the cap; a key with
  nothing on it says so in a fainter ink. The Codex screen also writes on the
  status-light keys (Agent 1-6).
- Status-light keys show the Agent state in the colours the draft paints
  (`set_status_colors`); without a device they are plain caps.
- `paint_neighbour` draws a screen either side: the same device at 20%, no
  text, nothing to click, blurred (design ticket).

    view = ModeDeviceView()
    view.set_labels({"key.9": "复制"}, unset={"key.12"})
    view.control_chosen.connect(lambda control_id: ...)
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene, QToolTip, QWidget

from controller_config.i18n import translate_ui_text
from controller_config.style import tokens
from controller_config.views.ui.device_top_view import (
    JOYSTICK_CENTER, JOYSTICK_DIAMETER, KEY_CENTERS, MARGIN, STATUS_KEYS, DeviceTopView, _font,
)

LABEL_MM = 4.0              # label pixel size in millimetres of the drawn device (11 px at body 330)
LABEL_INK = tokens.WALL_TEXT_ON_CREAM
LABEL_UNSET_OPACITY = 0.45  # "未映射"
LABEL_ON_COLOUR = tokens.WALL_TEXT_ON_CREAM
LABELS_FROM = 220           # body width in px; smaller draws no text
CHOOSABLE = (*KEY_CENTERS, "encoder", "joystick")


def _is_wide(character: str) -> bool:
    return ord(character) >= 0x2E80


def _fit_label(text: str, width: float, size: int) -> tuple[str, QFont]:
    """One line if it fits; CJK breaks into two short lines (新建 / 任务); Latin shrinks
    down to 65% and then breaks at a space, never inside a word."""
    font = _font()
    font.setPixelSize(size)
    if QFontMetricsF(font).horizontalAdvance(text) <= width:
        return text, font
    if any(_is_wide(character) for character in text) and " " not in text:
        half = math.ceil(len(text) / 2)
        return f"{text[:half]}\n{text[half:]}", font
    for step in range(1, 8):   # down to 65%
        font.setPixelSize(max(1, round(size * (1 - 0.05 * step))))
        if QFontMetricsF(font).horizontalAdvance(text) <= width:
            return text, font
    if " " in text:
        head, _, tail = text.partition(" ")
        return f"{head}\n{tail}", font
    return text, font


class ModeDeviceView(DeviceTopView):
    """Top view with labels, status colours and every control clickable."""

    control_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, mode="interactive", show_key_numbers=False)
        self.setObjectName("modeDevice")
        self._labels: dict[str, str] = {}
        self._unset: set[str] = set()
        self._status: dict[str, str] = {}
        self._highlight: str | None = None   # "encoder" / "joystick" while chosen
        self._group: frozenset[str] = frozenset()   # all six status-light keys ring together (04)
        self.labels_from = LABELS_FROM   # the 全部方案 thumbnails draw their labels smaller

    # -- what is drawn -------------------------------------------------------------

    def set_labels(self, labels: Mapping[str, str], *, unset: Iterable[str] = ()) -> None:
        self._labels = {key: str(text) for key, text in labels.items() if key in KEY_CENTERS and text}
        self._unset = {key for key in unset if key in KEY_CENTERS}
        self.update()

    def labels(self) -> dict[str, str]:
        return dict(self._labels)

    def set_status_colors(self, colors: Mapping[str, str | None]) -> None:
        self._status = {key: value for key, value in colors.items() if key in STATUS_KEYS and value}
        self.update()

    def set_chosen(self, control_id: str | None) -> None:
        """Mark the chosen control: a ring on a key; the knob and joystick have none yet."""
        self._group = frozenset(STATUS_KEYS) if control_id in STATUS_KEYS else frozenset()
        if control_id in KEY_CENTERS:
            self._highlight = None
            self.set_selected(control_id)
        else:
            self._highlight = control_id if control_id in {"encoder", "joystick"} else None
            self.set_selected(None)

    def ringed(self) -> set[str]:
        """Keys drawn with the orange selection ring."""
        return set(self._group) | ({self.selected()} if self.selected() else set())

    def _paint_key(self, painter: QPainter, control_id: str, scale: float) -> None:
        if control_id in self._group and control_id != self._selected:
            chosen, self._selected = self._selected, control_id   # draw it ringed like the chosen one
            try:
                super()._paint_key(painter, control_id, scale)
            finally:
                self._selected = chosen
            return
        super()._paint_key(painter, control_id, scale)

    def key_fill(self, control_id: str, *, hovered: bool = False) -> QColor:
        if control_id in STATUS_KEYS and self.connected():
            colour = self._status.get(control_id)
            fill = QColor(colour) if colour else QColor(tokens.TOP_VIEW_STATUS_CAP)
            return fill.lighter(106) if hovered else fill
        return super().key_fill(control_id, hovered=hovered)

    # -- pointer -------------------------------------------------------------------

    def control_at(self, position: QPointF) -> str | None:
        found = super().control_at(position)
        if found is not None:
            return found
        centre = self._point(*JOYSTICK_CENTER)
        radius = JOYSTICK_DIAMETER * self._scale() / 2
        if math.hypot(position.x() - centre.x(), position.y() - centre.y()) <= radius:
            return "joystick"
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        control_id = self.control_at(event.position())
        hover = control_id if control_id in KEY_CENTERS else None
        if hover != self._hover:
            self._hover = hover
            self.update()
        if control_id is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        QWidget.mouseMoveEvent(self, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton:
            control_id = self.control_at(event.position())
            if control_id in CHOOSABLE:
                self._keyboard_focus = False
                self.choose(control_id)
                event.accept()
                return
        event.ignore()

    def choose(self, control_id: str) -> None:
        self.set_chosen(control_id)
        self.control_chosen.emit(control_id)

    def _choose(self, control_id: str) -> None:  # keyboard path of the base view
        self.choose(control_id)

    def event(self, event) -> bool:
        if event.type() == event.Type.ToolTip:
            control_id = self.control_at(QPointF(event.pos()))
            text = self._labels.get(control_id or "")
            if text:
                QToolTip.showText(event.globalPos(), translate_ui_text(text), self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)

    # -- painting ------------------------------------------------------------------

    def _page_resizing(self) -> bool:
        """True while the Mode page this view sits on is being dragged to a new size."""
        widget = self.parentWidget()
        while widget is not None:
            resizing = getattr(widget, "resizing", None)
            if callable(resizing):
                return bool(resizing())
            widget = widget.parentWidget()
        return False

    def render_plain(self, target) -> None:
        """Render into `target` as drawn normally (the stage's zoom layer, the picture
        from before disconnecting), never the stretched picture of a drag."""
        self._painting_picture = True
        try:
            self.render(target, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        finally:
            self._painting_picture = False

    def drop_resize_picture(self) -> None:
        self._resize_picture = None

    def hold_for_resize(self) -> None:
        """Keep a picture of the view as it is now, for the drag the page has begun: called
        from the page's resize, before the view gets its new size. Not from paintEvent: a
        render() inside it leaves the paint it is in with no device, and the view blank
        for that frame."""
        if getattr(self, "_resize_picture", None) is not None or not self.isVisible():
            return
        ratio = self.devicePixelRatioF()
        picture = QPixmap(max(1, math.ceil(self.width() * ratio)), max(1, math.ceil(self.height() * ratio)))
        picture.setDevicePixelRatio(ratio)
        picture.fill(Qt.GlobalColor.transparent)
        self.render_plain(picture)
        self._resize_picture = picture

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        picture = getattr(self, "_resize_picture", None)
        if (picture is not None and not getattr(self, "_painting_picture", False)
                and self.isVisible() and self._page_resizing()):
            # While the window is dragged: the view as it was when the drag began, stretched;
            # drawn sharp again when it stops (design decision: 拖窗口每帧不超过 8 毫秒).
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(QRectF(self.rect()), picture, QRectF(picture.rect()))
            painter.end()
            return
        if not getattr(self, "_painting_picture", False) and not self._page_resizing():
            self._resize_picture = None   # drawn as normal again: the next drag takes a new one
        super().paintEvent(event)
        if self.body_width() < self.labels_from:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        base = max(1, round(LABEL_MM * self._scale()))
        for control_id, text in self._labels.items():
            face = self.key_rect(control_id).adjusted(3, 3, -3, -3)
            ink = QColor(LABEL_ON_COLOUR if control_id in STATUS_KEYS else LABEL_INK)
            if control_id in self._unset:
                ink.setAlphaF(LABEL_UNSET_OPACITY)
            painter.setPen(ink)
            shown, font = _fit_label(translate_ui_text(text), face.width(), base)
            painter.setFont(font)
            painter.drawText(face, Qt.AlignmentFlag.AlignCenter, shown)
        painter.end()


_NEIGHBOUR_IMAGES: dict[int, QImage] = {}
NEIGHBOUR_SUPERSAMPLE = 3   # the cached picture is drawn this many times finer than the stage unit


def _neighbour_image(body_px: int) -> QImage:
    """The device on show without labels, numbers or lit keys, drawn once and cached."""
    cached = _NEIGHBOUR_IMAGES.get(body_px)
    if cached is not None:
        return cached
    view = DeviceTopView(mode="interactive", show_key_numbers=False)
    side = math.ceil(body_px * (1 + 2 * MARGIN))
    view.resize(side, side)
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    view.render(image, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    view.deleteLater()
    _NEIGHBOUR_IMAGES[body_px] = image
    return image


def _blurred(image: QImage, radius_px: int) -> tuple[QImage, int]:
    """The picture with a Gaussian blur, padded by `pad` on every side so it can spread."""
    key = (image.cacheKey(), radius_px)
    cached = _BLURRED.get(key)
    if cached is not None:
        return cached
    pad = radius_px * 2
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(image))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius_px)
    effect.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    result = QImage(image.width() + 2 * pad, image.height() + 2 * pad, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    scene.render(painter, QRectF(0, 0, result.width(), result.height()),
                 QRectF(-pad, -pad, result.width(), result.height()))
    painter.end()
    _BLURRED[key] = (result, pad)
    return result, pad


_BLURRED: dict[tuple[int, int], tuple[QImage, int]] = {}


def paint_neighbour(painter: QPainter, centre: QPointF, body_px: float, *, blur: float = 0.0) -> None:
    """A screen either side of the one on show (Mode页 定稿 v1 `nb()`): the same device as the
    one in the middle, no text, keys unlit, drawn as one faint shadow at 20% so it is only a
    little lighter than whatever is behind it (design ticket), blurred by `blur` page
    units (design ticket). Multiplies the painter's opacity, so it still fades out with
    the rest when a knob or the joystick is zoomed."""
    image = _neighbour_image(max(1, round(body_px * NEIGHBOUR_SUPERSAMPLE)))
    side = image.width() / NEIGHBOUR_SUPERSAMPLE
    pad = 0.0
    if blur > 0:
        image, pad_px = _blurred(image, max(1, round(blur * NEIGHBOUR_SUPERSAMPLE)))
        pad = pad_px / NEIGHBOUR_SUPERSAMPLE
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setOpacity(painter.opacity() * tokens.MODE_NEIGHBOUR_OPACITY)
    painter.drawImage(QRectF(centre.x() - side / 2 - pad, centre.y() - side / 2 - pad,
                             side + 2 * pad, side + 2 * pad), image)
    painter.restore()
