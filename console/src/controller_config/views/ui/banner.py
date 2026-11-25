"""Banners: the one-line notices that replace most message boxes.

the designer, key 8 banner v1.1 (2026-11-18 final) for the look:
    one line, 52 high, fully rounded ends, the same colour as a card;
    below the navigation, on the right, its right edge on the card column;
    width follows the text; a small icon on the left, buttons on the right,
    optionally a round close. It does not dim or block the window.
the designer, "弹窗改横幅盘点 v0.1" (2026-11-18) for several at once:
    they stack downwards, at most three at a time.

Not decided yet, so kept simple and easy to change here: the newest banner
goes on top; a fourth waits until one of the three goes; a banner sent again
with the same `key` replaces the old one in place; ok / reminder banners hide
themselves after AUTO_HIDE_MS (the pointer resting on one keeps it), banners
with buttons or errors stay until answered.

Specs: Console视觉稿/按键8语音手势提示-横幅-v1.1-20261118-图/1-横幅定下来的样子和五种状态.png,
弹窗改横幅盘点-v0.1-20261118-图/2-同时来几条-三种做法和配套规矩.png.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

from controller_config.i18n import translate_ui_text as tr
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.dialog import dialog_button
from controller_config.views.ui.status_icon import StatusIcon

HEIGHT = 52
ICON_SIZE = tokens.CHECK_SIZE_LARGE
CLOSE_SIZE = 32
SIDE_PADDING = 10        # left/right inside the pill; the icon sits in a 32 circle
GAP = 8                  # between banners in the stack
MAX_VISIBLE = 3
AUTO_HIDE_MS = 5000      # ok and reminder banners (Agent value, not tried by hand)
RIGHT_INSET = 26         # same as the page margin, so the right edge meets the cards
TOP_INSET = 8


class _BannerClose(QPushButton):
    """Round close at the right end: dark disc, light cross."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bannerClose")
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tr("关闭"))
        self.setToolTip(tr("关闭"))
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._keyboard_focus = False  # ring for Tab focus only

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = event.reason() in {
            Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
        }
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._keyboard_focus = False
        super().focusOutEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(CLOSE_SIZE, CLOSE_SIZE)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.SURFACE_3 if self.underMouse() else tokens.SURFACE_2))
        painter.drawEllipse(body)
        painter.setPen(QPen(QColor(tokens.TEXT_SECONDARY), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        centre, half = body.center(), 4.5
        painter.drawLine(centre + QPointF(-half, -half), centre + QPointF(half, half))
        painter.drawLine(centre + QPointF(-half, half), centre + QPointF(half, -half))
        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(QColor(tokens.ACCENT_FOCUS), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(body.adjusted(1, 1, -1, -1))
        painter.end()


class Banner(QFrame):
    """One banner. `state` picks the status shape on the left (ok, error,
    busy, wait, off) or pass your own `icon` widget (the key 8 microphone)."""

    dismissed = Signal()
    resized = Signal()  # width changed with its content; the stack re-places it

    def __init__(self, text: str, *, state: str | None = "ok", icon: QWidget | None = None,
                 closable: bool = False, timeout_ms: int | None = None, key: str | None = None,
                 object_name: str = "banner", parent: QWidget | None = None,
                 entrance: str = "slide") -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setProperty("boringBanner", True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFixedHeight(HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.key = key
        self._buttons: list[QPushButton] = []
        self._hover = False

        # Changing length like the Dynamic Island (the designer 11-20): see `begin_change`.
        self._morph = QVariantAnimation(self)
        self._morph.setStartValue(0.0)
        self._morph.setEndValue(1.0)
        self._morph.setDuration(tokens.BANNER_MORPH_MS)
        self._morph.valueChanged.connect(self._morph_step)
        self._morph.finished.connect(self._morph_finished)
        self._morph_from = 0.0        # pill width when the change started
        self._morph_to = 0            # the width the new content needs
        self._morph_c1 = 0.0
        self._extra = 0               # room on the left while the pill is wider than its content
        self._pill = 0.0              # the pill's width now (while changing)
        self._still: QPixmap | None = None   # the old words, fading out inside the pill
        self._still_width = 0
        self._still_opacity = 0.0
        self._content_opacity = 1.0
        self._content_effects: list[QGraphicsOpacityEffect] = []
        self._batch = 0
        self._restart = False

        row = QHBoxLayout(self)
        row.setContentsMargins(SIDE_PADDING, 0, SIDE_PADDING, 0)
        row.setSpacing(10)
        self._icon_slot = QHBoxLayout()
        self._icon_slot.setContentsMargins(6, 0, 0, 0)
        row.addLayout(self._icon_slot)
        self._icon: QWidget | None = None
        if icon is not None:
            self.set_icon(icon)
        elif state is not None:
            self.set_icon(StatusIcon(state, size=ICON_SIZE))
        self._text = QLabel(tr(text), objectName="bannerText")
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._text)
        row.addSpacing(6)
        self._button_row = QHBoxLayout()
        self._button_row.setSpacing(GAP)
        row.addLayout(self._button_row)
        self._close: _BannerClose | None = None
        if closable:
            self._close = _BannerClose(self)
            self._close.clicked.connect(self.dismiss)
            row.addWidget(self._close)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._timeout_ms = timeout_ms
        self.setAccessibleName(tr(text))

        # Sliding in from the right and out to the right (-15 第 3 处, 丙-3).
        self._reduce_motion = motion.reduces_motion()
        self._target = QPoint(0, 0)
        self._slide = QVariantAnimation(self)
        self._slide.setStartValue(0.0)
        self._slide.setEndValue(1.0)
        self._slide.valueChanged.connect(self._slide_step)
        self._slide.finished.connect(self._slide_finished)
        self._slide_kind: str | None = None   # "in" / "out" / "rise"
        self._slide_from = 0.0
        self._slide_c1 = 0.0
        self._slide_then = None
        self._fade: QGraphicsOpacityEffect | None = None
        # "slide" (-15 第 3 处) or "fade": the Doubao failure fades in 短, out 中 (-15 第 2 处).
        self._entrance = entrance
        self._timeout_with_buttons = False

    # -- content --------------------------------------------------------------

    def text(self) -> str:
        return self._text.text()

    def limit_text_width(self, width: int) -> None:
        """Cut a long sentence to `width` with an ellipsis; the whole of it goes in the tooltip."""
        full = self._text.text()
        metrics = self._text.fontMetrics()
        if metrics.horizontalAdvance(full) <= width:
            return
        self._text.setText(metrics.elidedText(full, Qt.TextElideMode.ElideRight, width))
        self._text.setToolTip(full)
        self.setToolTip(full)
        self.adjustSize()
        self.resized.emit()

    def set_text(self, text: str) -> None:
        self._text.setText(tr(text))
        self.setAccessibleName(self._text.text())
        self._refit()

    def icon(self) -> QWidget | None:
        return self._icon

    def set_icon(self, icon: QWidget) -> None:
        if self._icon is not None:
            self._icon_slot.removeWidget(self._icon)
            self._icon.deleteLater()
        self._icon = icon
        self._icon_slot.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._refit()

    def set_state(self, state: str, **kwargs) -> None:
        """Switch the status shape (e.g. busy -> ok after a write)."""
        if isinstance(self._icon, StatusIcon):
            self._icon.set_state(state, **kwargs)
        else:
            self.set_icon(StatusIcon(state, size=ICON_SIZE, **kwargs))

    def add_button(self, text: str, kind: str = "secondary", *, object_name: str = "") -> QPushButton:
        """A small pill button: secondary (outline) or primary (orange)."""
        button = dialog_button(text, kind, object_name=object_name)
        button.setProperty("bannerButton", True)
        self._buttons.append(button)
        self._button_row.addWidget(button)
        self._stop_timer()  # a banner that asks something waits for the answer
        self._refit()
        return button

    def buttons(self) -> list[QPushButton]:
        return list(self._buttons)

    def clear_buttons(self) -> None:
        for button in self._buttons:
            self._button_row.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        self._refit()

    def set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._buttons:
            button.setEnabled(enabled)

    def set_closable(self, closable: bool) -> None:
        if closable and self._close is None:
            self._close = _BannerClose(self)
            self._close.clicked.connect(self.dismiss)
            self.layout().addWidget(self._close)
        elif not closable and self._close is not None:
            self.layout().removeWidget(self._close)
            self._close.deleteLater()
            self._close = None
        self._refit()

    def set_timeout(self, timeout_ms: int | None, *, even_with_buttons: bool = False) -> None:
        """`even_with_buttons`: goes by itself although it has a button (a notice
        whose button only explains, like 〔查看原因〕)."""
        self._timeout_ms = timeout_ms
        self._timeout_with_buttons = even_with_buttons
        self._stop_timer()
        if self.isVisible():
            self._start_timer()

    # -- coming and going (-15 第 3 处: 丙-3, slides in from the right past its place) ----

    def place(self, x: int, y: int) -> None:
        """Where the banner rests; while it slides, the slide ends there instead."""
        self._target = QPoint(x, y)
        if self._slide_kind is None:
            self.move(self._target)
        else:
            self._slide_step(self._slide.currentValue())

    def target(self) -> QPoint:
        return QPoint(self._target)

    def sliding(self) -> str | None:
        """"in", "out" or "rise" while moving, else None."""
        return self._slide_kind

    def _animates(self) -> bool:
        parent = self.parentWidget()
        return not self._reduce_motion and parent is not None and self.window().isVisible()

    def slide_in(self) -> None:
        """From just outside the window's right edge to its place, about 10 past it and back."""
        if not self._animates():
            self._settle()
            return
        self._slide.stop()
        self._set_opacity(None)
        if self._entrance == "fade":
            self.move(self._target)
            self._set_opacity(0.0)
            self._run("fade_in", tokens.MOTION_SHORT_MS)
            return
        start = float(self.parentWidget().width())
        self._slide_from = start
        self._slide_c1 = motion.back_out_for(tokens.BANNER_OVERSHOOT, start - self._target.x())
        self._run("in", tokens.MOTION_MEDIUM_MS)

    def slide_out(self, then=None) -> None:
        """Back out to the right and out of the window, fading as it leaves; then `then()`."""
        if not self._animates() or not self.isVisible():
            self._slide.stop()
            self._slide_kind = None
            if then is not None:
                then()
            return
        self._slide.stop()
        self._slide_from = float(self.x())
        self._slide_then = then
        self._set_opacity(1.0)
        self._run("fade_out" if self._entrance == "fade" else "out", tokens.MOTION_MEDIUM_MS)

    def rise_out(self, then=None) -> None:
        """Fade out while rising a little (保存横幅 "已存进设备" 收起, -15 第 1 处)."""
        if not self._animates() or not self.isVisible():
            self._slide.stop()
            self._slide_kind = None
            if then is not None:
                then()
            return
        self._slide.stop()
        self._slide_from = float(self.y())
        self._slide_then = then
        self._set_opacity(1.0)
        self._run("rise", tokens.MOTION_MEDIUM_MS)

    def _run(self, kind: str, duration: int) -> None:
        self._slide_kind = kind
        self._slide.setDuration(duration)
        self._slide_step(0.0)
        self._slide.start()

    def _slide_step(self, value) -> None:
        p = float(value or 0.0)
        kind = self._slide_kind
        if kind == "in":
            x = self._slide_from + (self._target.x() - self._slide_from) * motion.back_out(p, self._slide_c1)
            self.move(round(x), self._target.y())
        elif kind == "out":
            end = float(self.parentWidget().width()) if self.parentWidget() is not None else self._slide_from
            x = self._slide_from + (end - self._slide_from) * motion.in_cubic(p)
            self.move(round(x), self._target.y())
            self._set_opacity(1.0 - max(0.0, (p - 0.6) / 0.4))   # fades as it nears the edge
        elif kind == "fade_in":
            self._set_opacity(motion.out_cubic(p))
        elif kind == "fade_out":
            self._set_opacity(1.0 - motion.out_cubic(p))
        elif kind == "rise":
            y = self._slide_from - tokens.BANNER_SAVED_RISE * motion.out_cubic(p)
            self.move(self._target.x(), round(y))
            self._set_opacity(1.0 - motion.out_cubic(p))

    def _slide_finished(self) -> None:
        kind, then = self._slide_kind, self._slide_then
        self._slide_kind = None
        self._slide_then = None
        if kind in {"out", "rise", "fade_out"}:
            self.hide()
            self._set_opacity(None)
            self.move(self._target)
            if then is not None:
                then()
        else:
            self._settle()

    def _settle(self) -> None:
        self._slide_kind = None
        self._set_opacity(None)
        self.move(self._target)

    def _set_opacity(self, opacity: float | None) -> None:
        if opacity is None:
            if self._fade is not None:
                self.setGraphicsEffect(None)
                self._fade = None
            return
        if self._fade is None:
            self._fade = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._fade)
        self._fade.setOpacity(max(0.0, min(1.0, opacity)))

    # -- life -----------------------------------------------------------------

    def dismiss(self) -> None:
        """Gone at once for the page (`dismissed` now); only the picture slides away after."""
        self._timer.stop()
        if self._slide_kind in {"out", "fade_out"}:
            return   # already going
        self.slide_out(self.hide)
        self.dismissed.emit()

    def _start_timer(self) -> None:
        if self._timeout_ms and (not self._buttons or self._timeout_with_buttons) and not self._hover:
            self._timer.start(self._timeout_ms)

    def _stop_timer(self) -> None:
        self._timer.stop()

    def _timed_out(self) -> None:
        if not self._hover:
            self.dismiss()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        self._start_timer()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._hover = True
        self._stop_timer()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._hover = False
        self._start_timer()
        super().leaveEvent(event)

    # -- changing length (the designer 11-20: "和 iPhone 里的灵动岛保持一致") ----------

    def begin_change(self) -> None:
        """Call before changing words, icon or buttons, then `end_change()`.

        The pill then stretches or shrinks to the new length with a small bounce
        (its right edge stays), the old words fade out inside it and the new ones
        fade in once there is room. Nothing is grabbed with a background, so no
        box or ghost shows round it. Calls nest.
        """
        self._batch += 1
        if self._batch == 1 and (self.changing() or self._can_change()):
            # Mid-change too: what shows now (half faded) becomes the old picture
            # and the pill carries on from where it is (no jump).
            self._restart = self.changing()
            self.layout().activate()
            self._still, self._still_width = self._content_picture(), self.width()
            self._still_opacity = 1.0

    def end_change(self) -> None:
        if self._batch == 1:
            # Buttons added just now still have their unstyled size; style them first
            # (still inside the batch, so nothing refits half-way) and the pill aims
            # at the right length at once, with no one-frame jump.
            for child in self.findChildren(QWidget):
                child.ensurePolished()
            # A widget added to a showing banner only shows on the next turn of the
            # event loop, and a layout leaves hidden widgets out of its size.
            for child in (self._icon, *self._buttons, self._close):
                if child is not None and not child.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide):
                    child.show()
            self.layout().invalidate()
            self.layout().activate()
        self._batch = max(0, self._batch - 1)
        if self._batch == 0:
            self._refit()

    def changing(self) -> bool:
        return self._morph.state() == QVariantAnimation.State.Running

    def _can_change(self) -> bool:
        return (not self._reduce_motion and self.isVisible() and self._slide_kind is None
                and self.width() > 0 and self.window().isVisible())

    def _content_picture(self) -> QPixmap:
        """Icon disc, icon, words and buttons on a transparent picture (no pill, no box).

        The whole widget, untranslated: a child with an opacity effect (mid-change)
        is clipped wrongly when rendered through a shifted painter.
        """
        ratio = self.devicePixelRatioF()
        picture = QPixmap(round(max(1, self.width()) * ratio), round(HEIGHT * ratio))
        picture.setDevicePixelRatio(ratio)
        picture.fill(Qt.GlobalColor.transparent)
        painter = QPainter(picture)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_icon_disc(painter, self._content_opacity)
        painter.end()
        self.render(picture, QPoint(0, 0), QRegion(self.rect()), QWidget.RenderFlag.DrawChildren)
        return picture

    def _target_width(self) -> int:
        return self.layout().sizeHint().width() - self._extra

    def _refit(self) -> None:
        if self._batch:
            return
        width = self._target_width()
        if self.changing() and self._restart:
            self._restart = False
            self._start_change(self._pill, width)
            return
        if self.changing():
            if width != self._morph_to:
                self._morph_to = width
                self._fit_room(max(self._morph_from, width) + self._overshoot(width))
            self._set_content_opacity(self._content_opacity)   # buttons added just now
            return
        if self._still is not None:
            if self._can_change():
                # Same length too: the words still swap softly in place.
                self._start_change(self.width() - self._extra, width)
                return
            self._still = None
        if width != self.width() or self.minimumWidth() != width:
            self._set_extra(0)
            self.setFixedWidth(width)
            self.resized.emit()

    def _overshoot(self, to: float) -> float:
        distance = abs(to - self._morph_from)
        return min(tokens.BANNER_MORPH_OVERSHOOT, distance / 4)

    def _start_change(self, start: float, end: int) -> None:
        self._morph_from = float(start)
        self._morph_to = end
        self._morph_c1 = motion.back_out_for(self._overshoot(end), abs(end - start))
        self._pill = float(start)
        self._fit_room(max(start, end) + self._overshoot(end))
        self._set_content_opacity(0.0)
        self._morph.stop()
        self._morph.start()

    def _fit_room(self, room: float) -> None:
        """Widget as wide as the pill can get; the content keeps its final place on the right."""
        room = int(room + 0.999)
        self._set_extra(max(0, room - self._morph_to))
        if room != self.width():
            self.setFixedWidth(room)
            self.resized.emit()

    def _set_extra(self, extra: int) -> None:
        if extra == self._extra:
            return
        self._extra = extra
        self.layout().setContentsMargins(SIDE_PADDING + extra, 0, SIDE_PADDING, 0)

    def _morph_step(self, value) -> None:
        p = float(value or 0.0)
        self._pill = self._morph_from + (self._morph_to - self._morph_from) * motion.back_out(p, self._morph_c1)
        self._still_opacity = 1.0 - motion.out_cubic(p / tokens.BANNER_MORPH_OLD_SHARE)
        self._set_content_opacity(motion.out_cubic(
            (p - tokens.BANNER_MORPH_NEW_FROM) / tokens.BANNER_MORPH_NEW_SHARE))
        self.update()

    def _morph_finished(self) -> None:
        self._still = None
        self._set_content_opacity(1.0)
        self._set_extra(0)
        if self.width() != self._morph_to:
            self.setFixedWidth(self._morph_to)
            self.resized.emit()
        self.update()
        self._refit()   # content that changed size during the last frames

    def _set_content_opacity(self, opacity: float) -> None:
        opacity = max(0.0, min(1.0, opacity))
        self._content_opacity = opacity
        children = [w for w in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                    if not w.isWindow()]
        if opacity >= 1.0:
            for widget in children:
                if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect) and widget.graphicsEffect() in self._content_effects:
                    widget.setGraphicsEffect(None)
            self._content_effects.clear()
            return
        for widget in children:
            effect = widget.graphicsEffect()
            if effect is None:
                effect = QGraphicsOpacityEffect(widget)
                widget.setGraphicsEffect(effect)
                self._content_effects.append(effect)
            if effect in self._content_effects:
                effect.setOpacity(opacity)

    def event(self, event) -> bool:
        handled = super().event(event)
        # Buttons and text get their real size only once styled: refit then.
        if event.type() == QEvent.Type.LayoutRequest:
            self._refit()
        return handled

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        return QSize(self.layout().sizeHint().width(), HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pill = self._pill if self.changing() else float(self.width() - self._extra)
        body = QRectF(self.width() - pill, 0, pill, HEIGHT).adjusted(0.5, 0.5, -0.5, -0.5)
        edge = QColor(tokens.TEXT_PRIMARY)
        edge.setAlpha(16)  # the card outline
        painter.setPen(QPen(edge, 1))
        painter.setBrush(QColor(tokens.SURFACE_BASE))
        painter.drawRoundedRect(body, HEIGHT / 2, HEIGHT / 2)
        shape = QPainterPath()
        shape.addRoundedRect(body, HEIGHT / 2, HEIGHT / 2)
        painter.setClipPath(shape)
        if self._still is not None and self._still_opacity > 0:
            painter.setOpacity(self._still_opacity)
            painter.drawPixmap(QPointF(self.width() - self._still_width, 0), self._still)
            painter.setOpacity(1.0)
        self._paint_icon_disc(painter, self._content_opacity)
        painter.end()

    def _paint_icon_disc(self, painter: QPainter, opacity: float) -> None:
        """The icon sits in a darker circle, as in the spec."""
        if self._icon is None or opacity <= 0:
            return
        centre = self._icon.geometry().center()
        painter.save()
        painter.setOpacity(opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.SURFACE_2))
        painter.drawEllipse(QPointF(centre.x() + 0.5, centre.y() + 0.5), 16, 16)
        painter.restore()


class BannerStack(QObject):
    """Places banners over `host`'s top right corner, newest on top, at most
    MAX_VISIBLE at a time. Banners float over the page; they never push it."""

    def __init__(self, host: QWidget, *, right_inset: int = RIGHT_INSET, top_inset: int = TOP_INSET) -> None:
        super().__init__(host)
        self._host = host
        self._right_inset = right_inset
        self._top_inset = top_inset
        self._banners: list[Banner] = []  # newest first
        host.installEventFilter(self)

    def banners(self) -> list[Banner]:
        """Every banner still waiting, newest first (visible ones lead)."""
        return list(self._banners)

    def visible_banners(self) -> list[Banner]:
        return [banner for banner in self._banners if banner.isVisible()]

    def set_insets(self, *, right: int | None = None, top: int | None = None) -> None:
        if right is not None:
            self._right_inset = right
        if top is not None:
            self._top_inset = top
        self._relayout()

    def show(self, banner: Banner) -> Banner:
        """Show `banner`; one with the same key takes the old one's place."""
        old = next((b for b in self._banners if banner.key and b.key == banner.key), None)
        banner.setParent(self._host)
        banner.dismissed.connect(lambda b=banner: self._forget(b))
        banner.resized.connect(self._relayout)
        if old is not None:
            self._banners[self._banners.index(old)] = banner
            old.dismissed.disconnect()
            old.resized.disconnect()
            old.hide()
            old.deleteLater()
        else:
            self._banners.insert(0, banner)
        self._relayout()
        return banner

    def notify(self, text: str, *, state: str = "ok", key: str | None = None,
               timeout_ms: int | None = AUTO_HIDE_MS, closable: bool = True) -> Banner:
        """The common case: one sentence, a status shape, hides by itself."""
        return self.show(Banner(text, state=state, key=key, timeout_ms=timeout_ms, closable=closable))

    def find(self, key: str) -> Banner | None:
        return next((b for b in self._banners if b.key == key), None)

    def clear(self) -> None:
        for banner in list(self._banners):
            banner.dismiss()

    def _forget(self, banner: Banner) -> None:
        if banner in self._banners:
            self._banners.remove(banner)
            if banner.sliding() in {"out", "fade_out"}:
                banner._slide.finished.connect(banner.deleteLater)   # once it has slid away
            else:
                banner.deleteLater()
        self._relayout()

    def _relayout(self) -> None:
        y = self._top_inset
        right = self._host.width() - self._right_inset
        for index, banner in enumerate(self._banners):
            if index >= MAX_VISIBLE:
                banner.hide()
                continue
            banner._refit()
            width = banner.width()
            if banner.sliding() in {"out", "fade_out"}:
                continue   # leaving: the others close up behind it
            arriving = not banner.isVisible() and not banner.property("bannerPlaced")
            banner.resize(width, HEIGHT)
            banner.place(max(0, right - width), y)
            banner.show()
            banner.raise_()
            if arriving:
                banner.setProperty("bannerPlaced", True)
                banner.slide_in()
            y += HEIGHT + GAP

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual method
        if watched is self._host and event.type() in {QEvent.Type.Resize, QEvent.Type.LayoutRequest}:
            self._relayout()
        return False
