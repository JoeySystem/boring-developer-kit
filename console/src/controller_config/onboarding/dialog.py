"""Device operation guide in the shared dark dialog shell (design decision).

the designer's B v0.4 合成版 3 (使用引导): the one dark dialog shell, with a white
stage that holds the animation; the stage colour equals the GIF background so
no seam shows. "稍后设置" sits bottom-left instead of the round close, the step
dots sit in the middle of the bottom row, the primary button on the right.
The two help popups stay dialogs (the designer 11-19) in the same shell.
"""
from __future__ import annotations

import html

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from controller_config.accessibility import system_reduces_motion
from controller_config.i18n import normalize_language
from controller_config.style import tokens
from controller_config.text_catalog import get_text_catalog
from controller_config.views.ui.button import Button
from controller_config.views.ui.dialog import SMALL, XLARGE, Dialog

from .content import BLUETOOTH_STEP, ONBOARDING_STEPS, cue_at
from .player import GuidePlayer, asset_root


# Sizes not on the spec image are the Agent's picks (cue 12, connection cards side
# by side, selected card = cream ring, not orange). Title 26 as on the spec, second
# line of each card in the secondary grey, outlined buttons as tall as the primary
# one (design ticket 答复第 2 件, Agent 提议, the designer "都按推荐").
STYLE = tokens.stylesheet("""
QFrame#onboardingStage { background: ${ONBOARDING_STAGE}; border: none; border-radius: ${ONBOARDING_STAGE_RADIUS}px; }
QLabel#onboardingBrand { color: ${TEXT_PRIMARY}; font-size: ${FONT_CAPTION}px; font-weight: 600; }
QLabel#onboardingTitle { color: ${TEXT_PRIMARY}; font-size: ${FONT_ONBOARDING_TITLE}px; font-weight: 600; }
QLabel#guideCardTitle { color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; }
QLabel#guideCardDetail { color: ${TEXT_SECONDARY}; font-size: ${FONT_BODY}px; }
QDialog#onboardingDialog QPushButton[boringButton="secondary"][boringButtonSize="large"] {
    min-height: ${CONTROL_HEIGHT_OUTLINED}px; max-height: ${CONTROL_HEIGHT_OUTLINED}px; }
QLabel#onboardingDescription, QLabel#guideConnectionNote { color: ${TEXT_SECONDARY}; font-size: ${FONT_BODY}px; }
QLabel#guideCue { color: ${TEXT_SECONDARY}; font-size: ${FONT_CAPTION}px; }
QPushButton[iconOnly="true"] { background: transparent; border: 1px solid ${BORDER_STRONG}; border-radius: ${RADIUS_CONTROL_SMALL}px; padding: 0; }
QPushButton[iconOnly="true"]:hover { background: ${SURFACE_3}; }
QPushButton[iconOnly="true"]:focus { border-color: ${ACCENT_FOCUS}; }
QPushButton[connection="true"] { background: ${SURFACE_2}; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_LIST_ROW}px; text-align: left; padding: 12px 14px; font-size: ${FONT_BODY}px; }
QPushButton[connection="true"]:hover { background: ${SURFACE_3}; }
QPushButton[connection="true"]:checked { background: ${SURFACE_3}; border-color: ${TEXT_PRIMARY}; }
QPushButton[connection="true"]:focus { border-color: ${ACCENT_FOCUS}; }
QPushButton[progress="true"] { padding: 0; min-height: 0; border: 0; background: transparent; color: ${DOT_ROW_UNLIT}; font-size: ${FONT_EMPHASIS}px; }
QPushButton[progress="true"]:checked { color: ${DOT_ROW_LIT}; }
QPushButton[progress="true"]:focus { border: 1px solid ${ACCENT_FOCUS}; border-radius: ${RADIUS_TAG}px; }
""")

_ICONS: dict[str, QIcon] = {}


def icon(name: str) -> QIcon:
    """A guide icon drawn in the shell's text colour (the SVGs use currentColor)."""
    cached = _ICONS.get(name)
    if cached is None:
        svg = (asset_root() / "icons" / f"{name}.svg").read_text(encoding="utf-8")
        pixmap = QPixmap()
        pixmap.loadFromData(QByteArray(svg.replace("currentColor", tokens.TEXT_PRIMARY).encode()), "SVG")
        cached = _ICONS[name] = QIcon(pixmap)
    return cached


class Scene(QWidget):
    """The camera animation includes the native screen; no inset overlay."""

    transition_finished = Signal()

    def __init__(self, player: GuidePlayer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.player = player
        self.fallback_text = "动画暂不可用，仍可阅读说明并继续。"
        self._previous = QPixmap()
        self._mix = 1.0
        self._transition = QVariantAnimation(self)
        self._transition.setDuration(tokens.MOTION_MEDIUM_MS)
        self._transition.setStartValue(0.0)
        self._transition.setEndValue(1.0)
        self._transition.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._transition.valueChanged.connect(self._set_mix)
        self._transition.finished.connect(self._finish_transition)
        self.setMinimumSize(270, 260)
        self.setAccessibleName("MIST 操作动画")
        player.position_changed.connect(self.update)
        player.error.connect(self.update)

    def _current_frame(self) -> QPixmap:
        if self.player.error_message or not self.player.movie:
            return QPixmap()
        return self.player.movie.currentPixmap()

    def current_visual(self) -> QPixmap:
        current = self._current_frame()
        if current.isNull() or self._previous.isNull() or self._mix >= 1.0:
            return current
        result = QPixmap(current.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setOpacity(1.0 - self._mix)
        painter.drawPixmap(0, 0, self._previous)
        painter.setOpacity(self._mix)
        painter.drawPixmap(0, 0, current)
        painter.end()
        return result

    def transition_from(self, previous: QPixmap, *, reduce_motion: bool) -> bool:
        self._transition.stop()
        self._previous = QPixmap()
        self._mix = 1.0
        current = self._current_frame()
        if reduce_motion or previous.isNull() or current.isNull():
            self.update()
            return False
        self._previous = previous.copy()
        self._mix = 0.0
        self._transition.start()
        self.update()
        return True

    @Slot(object)
    def _set_mix(self, value) -> None:
        self._mix = float(value)
        self.update()

    @Slot()
    def _finish_transition(self) -> None:
        self._previous = QPixmap()
        self._mix = 1.0
        self.update()
        self.transition_finished.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        pixmap = self.current_visual()
        if pixmap.isNull():
            painter.setPen(QColor(tokens.ONBOARDING_STAGE_TEXT))
            painter.drawText(
                self.rect().adjusted(20, 20, -20, -20),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.fallback_text,
            )
            return
        usable = QRectF(8, 8, self.width() - 16, self.height() - 16)
        scale = min(usable.width() / 720, usable.height() / 640)
        rect = QRectF(0, 0, 720 * scale, 640 * scale)
        rect.moveCenter(usable.center())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))


class OnboardingDialog(Dialog):
    """Drop-in App dialog. Emits navigation only; cannot access the SDK or device.

    The host owns first-run persistence and the connection-trigger policy.
    Pass reduce_motion=True to respect the host's accessibility preference.
    """

    page_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        autoplay: bool = True,
        reduce_motion: bool | None = None,
        assets=None,
        language: str | None = None,
    ) -> None:
        super().__init__(parent, width=XLARGE, close="later", full_height=True,
                         object_name="onboardingDialog")
        self.setWindowTitle("MIST · Companion guide")
        # Appended: without a parent window the shell carries the app sheet itself.
        self.setStyleSheet(self.styleSheet() + STYLE)
        self._step_index = 0
        self._bluetooth = False
        self._reduce_motion = (
            system_reduces_motion() if reduce_motion is None else reduce_motion
        )
        self._autoplay = autoplay and not self._reduce_motion
        self._play_after_transition = False
        self._last_cue: tuple[str, str, str] | None = None
        self._language = normalize_language(language or str(
            QApplication.instance().property("boringUiLanguage") or "zh_CN"
        )) or "zh_CN"
        self._popups: list[QDialog] = []
        self.player = GuidePlayer(self, assets=assets)

        layout = self.body_layout()
        layout.setSpacing(0)
        layout.addWidget(QLabel("MIST", objectName="onboardingBrand"))
        layout.addSpacing(6)
        self._title = QLabel(objectName="onboardingTitle")
        self._title.setWordWrap(True)
        layout.addWidget(self._title)
        layout.addSpacing(6)
        self._description = QLabel(objectName="onboardingDescription")
        self._description.setWordWrap(True)
        layout.addWidget(self._description)
        layout.addSpacing(12)

        self._connections = QWidget()
        choices = QHBoxLayout(self._connections)
        choices.setContentsMargins(0, 0, 0, 8)
        choices.setSpacing(8)
        group = QButtonGroup(self)
        self._usb = QPushButton(objectName="guideUSB")
        self._ble = QPushButton(objectName="guideBluetooth")
        self._card_lines: dict[QPushButton, tuple[QLabel, QLabel]] = {}
        for button, name in ((self._usb, "usb"), (self._ble, "bluetooth")):
            button.setCheckable(True)
            button.setProperty("connection", True)
            button.setAutoDefault(False)
            # Two lines in two colours: what it is, then (secondary grey) what it is for.
            inner = QHBoxLayout(button)
            inner.setContentsMargins(14, 12, 14, 12)
            inner.setSpacing(10)
            glyph = QLabel()
            glyph.setPixmap(icon(name).pixmap(22, 22))
            glyph.setFixedSize(22, 22)
            lines = QVBoxLayout()
            lines.setContentsMargins(0, 0, 0, 0)
            lines.setSpacing(2)
            title = QLabel(objectName="guideCardTitle")
            detail = QLabel(objectName="guideCardDetail")
            for label in (glyph, title, detail):
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lines.addWidget(title)
            lines.addWidget(detail)
            inner.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
            inner.addLayout(lines, 1)
            self._card_lines[button] = (title, detail)
            group.addButton(button)
            choices.addWidget(button, 1)
        self._usb.clicked.connect(lambda: self.set_connection_mode(False))
        self._ble.clicked.connect(lambda: self.set_connection_mode(True))
        layout.addWidget(self._connections)
        self._connection_note = QLabel(objectName="guideConnectionNote")
        self._connection_note.setWordWrap(True)
        # "稍后配对" is a link at the end of this line, not a fourth button at the bottom,
        # so every language fits three buttons and the dots (design ticket 答复第 4 件).
        self._connection_note.setTextFormat(Qt.TextFormat.RichText)
        self._connection_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self._connection_note.setOpenExternalLinks(False)
        self._connection_note.linkActivated.connect(lambda _link: self.next_step())
        self._connection_note.setContentsMargins(0, 0, 0, 8)
        layout.addWidget(self._connection_note)
        self._help = Button("", "secondary", size="small", object_name="guideHelp")
        self._help.setIcon(icon("question"))
        self._help.clicked.connect(self.open_help)
        layout.addWidget(self._help, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addSpacing(16)

        stage = QFrame(objectName="onboardingStage")
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.scene = Scene(self.player)
        self.scene.setMinimumSize(270, 180)
        stage_layout.addWidget(self.scene)
        layout.addWidget(stage, 1)
        self._stage = stage
        layout.addSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._cue_icon = QLabel()
        self._cue_icon.setFixedSize(24, 24)
        self._cue = QLabel(objectName="guideCue")
        self._cue.setWordWrap(True)
        row.addWidget(self._cue_icon)
        row.addWidget(self._cue, 1)
        self._pause = self._icon_button("pause", "guidePause")
        self._pause.clicked.connect(self.toggle_playback)
        self._replay = Button("", "secondary", size="small", object_name="guideReplay")
        self._replay.setIcon(icon("arrow-counter-clockwise"))
        self._replay.setIconSize(QSize(16, 16))
        self._replay.clicked.connect(self.player.replay)
        row.addWidget(self._pause)
        row.addWidget(self._replay)
        layout.addLayout(row)

        # Bottom row: "稍后设置" (the shell's close) | dots in the middle of the row | 返回 · primary.
        self._skip = self.close_button  # keeps the shell's "dialogLater" name and look
        footer = self.button_row()
        self._footer = footer
        dots = QWidget(footer.parentWidget())
        dots_layout = QHBoxLayout(dots)
        dots_layout.setContentsMargins(0, 0, 0, 0)
        dots_layout.setSpacing(0)
        self._dots = []
        for i in range(7):
            button = QPushButton("●", objectName=f"guideStep{i}")
            button.setProperty("progress", True)
            button.setFixedSize(24, 28)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.clicked.connect(
                lambda _checked=False, index=i: self.goto_step(index)
            )
            dots_layout.addWidget(button)
            self._dots.append(button)
        dots.setFixedSize(24 * len(self._dots), 28)
        self._dots_bar = dots   # placed by _place_dots(): centred on the whole row, not the gap
        self._back = self.add_button("", "secondary", object_name="onboardingBack")
        self._back.clicked.connect(self.previous_step)
        self._next = self.add_button("", "primary", object_name="onboardingNext")
        self._next.clicked.connect(self.next_step)

        self.player.position_changed.connect(self._sync_frame)
        self.player.playing_changed.connect(self._sync_playback)
        self.player.error.connect(self._asset_error)
        self.scene.transition_finished.connect(self._start_after_transition)
        self.finished.connect(self._finish)
        for widget in self.findChildren(QWidget):
            widget.setProperty("boringI18nSkip", True)
        self._render_step()

    def _icon_button(self, name: str, object_name: str) -> QPushButton:
        button = QPushButton(objectName=object_name)
        button.setProperty("iconOnly", True)
        button.setFixedSize(tokens.CONTROL_HEIGHT_SMALL, tokens.CONTROL_HEIGHT_SMALL)
        button.setIcon(icon(name))
        button.setIconSize(QSize(16, 16))
        button.setAutoDefault(False)
        return button

    def tr_pair(self, pair: tuple[str, str]) -> str:
        return get_text_catalog().translate(pair[0], self._language)

    @property
    def step_index(self) -> int:
        return self._step_index

    @property
    def active_step(self):
        return (
            BLUETOOTH_STEP
            if self._step_index == 1 and self._bluetooth
            else ONBOARDING_STEPS[self._step_index]
        )

    @Slot(str)
    def set_language(self, language: str) -> None:
        self._language = normalize_language(language) or "zh_CN"
        self._render_text()

    def goto_step(self, index: int) -> None:
        self._step_index = max(0, min(6, index))
        self._render_step()

    def next_step(self) -> None:
        if self._step_index == 6:
            self.open_step_page()
        else:
            self.goto_step(self._step_index + 1)

    def previous_step(self) -> None:
        self.goto_step(self._step_index - 1)

    def open_step_page(self) -> None:
        self.accept()
        self.page_requested.emit(ONBOARDING_STEPS[self._step_index].page)

    def set_connection_mode(self, bluetooth: bool) -> None:
        self._bluetooth = bluetooth
        self._render_step()

    def _render_step(self) -> None:
        previous = self.scene.current_visual()
        self._play_after_transition = False
        loaded = self.player.load(self.active_step.clip)
        should_play = loaded and self._autoplay and self.isVisible()
        transitioning = loaded and self.scene.transition_from(
            previous,
            reduce_motion=self._reduce_motion,
        )
        if should_play and transitioning:
            self._play_after_transition = True
        elif should_play:
            self.player.play()
        self._render_text()

    @Slot()
    def _start_after_transition(self) -> None:
        if self._play_after_transition and self.isVisible():
            self._play_after_transition = False
            self.player.play()

    def _fit_footer(self) -> None:
        """The dots sit in the middle of the whole row (design ticket 答复第 2 件). Button
        words come first: where long words (Japanese step 2, English last step) leave no room
        there, the dots move to the middle of the gap between the buttons, and only step aside
        when even the gap is too narrow."""
        self._place_dots()
        QTimer.singleShot(0, self, self._place_dots)  # again once the row has re-laid out

    def _place_dots(self) -> None:
        row = self._footer.geometry()
        if not row.isValid():
            return
        bar = self._dots_bar
        left_end = self._skip.geometry().right() + 8
        right = [b.geometry().left() for b in (self._back, self._next) if not b.isHidden()]
        right_start = min(right, default=row.right()) - 8
        x = row.center().x() - bar.width() / 2
        if x < left_end or x + bar.width() > right_start:
            x = (left_end + right_start) / 2 - bar.width() / 2
        bar.move(round(x), round(row.center().y() - bar.height() / 2))
        bar.setVisible(right_start - left_end >= bar.width())
        bar.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_dots()

    def _render_text(self) -> None:
        step = self.active_step
        self.setWindowTitle(self.tr_pair(("MIST · 设备操作动画", "MIST · Device operation guide")))
        self._title.setText(self.tr_pair(step.title))
        self._description.setText(self.tr_pair(step.description))
        self._help.setText(self.tr_pair(step.help))
        self._next.setText(self.tr_pair(step.cta))
        self._skip.setText(self.tr_pair(("稍后设置", "Set up later")))
        self._back.setText(self.tr_pair(("返回", "Back")))
        self._back.setVisible(self._step_index > 0)
        self._connections.setVisible(self._step_index == 1)
        for button, pair in (
            (self._usb, ("使用数据线\n连接应用，修改配置", "Use a USB cable\nConnect to configure")),
            (self._ble, ("设置蓝牙\n无线使用，可稍后设置", "Set up Bluetooth\nOptional wireless use")),
        ):
            title, _, detail = self.tr_pair(pair).partition("\n")
            title_label, detail_label = self._card_lines[button]
            title_label.setText(title)
            detail_label.setText(detail)
            button.setAccessibleName(f"{title} {detail}".strip())
            button.setMinimumHeight(button.layout().sizeHint().height())
        self._usb.setChecked(not self._bluetooth)
        self._ble.setChecked(self._bluetooth)
        self._connection_note.setVisible(self._step_index == 1 and self._bluetooth)
        note = self.tr_pair((
            "支持通过 USB 或蓝牙连接控制台并修改配置；固件升级需要 USB。",
            "Configure through USB or Bluetooth; firmware updates require USB.",
        ))
        lead = self.tr_pair(("暂时不用蓝牙？", "Not using Bluetooth yet?"))
        later = self.tr_pair(("稍后配对", "Pair later"))
        self._connection_note.setText(
            f"{html.escape(note)} {html.escape(lead)}"
            f'<a href="pair-later" style="color: {tokens.TEXT_PRIMARY};">{html.escape(later)}</a>'
        )
        self.scene.fallback_text = self.tr_pair((
            "动画暂不可用，仍可阅读说明并继续。",
            "Animation unavailable. You can still read the guide.",
        ))
        self._replay.setText(self.tr_pair(("重播", "Replay")))
        self._replay.setAccessibleName(self.tr_pair(("重播动画", "Replay animation")))
        self._replay.setToolTip(self._replay.accessibleName())
        self.scene.setAccessibleName(
            self.tr_pair(("MIST 操作动画", "MIST operation demo"))
        )
        for i, dot in enumerate(self._dots):
            dot.setChecked(i == self._step_index)
            dot.setAccessibleName(
                self.tr_pair(("第 {v1} 页：{v2}", "Step {v1}: {v2}")).format(
                    v1=i + 1, v2=self.tr_pair(ONBOARDING_STEPS[i].title)
                )
            )
        self._fit_footer()
        self._sync_playback(self.player.playing)
        self._sync_frame(self.player.position)
        self.scene.update()

    def _sync_frame(self, position: int) -> None:
        if self.player.error_message:
            self._asset_error(self.player.error_message)
            return
        name, caption = cue_at(self.player.clip, position)
        cue_key = (name, caption, self._language)
        if cue_key == self._last_cue:
            return
        self._last_cue = cue_key
        self._cue.setText(get_text_catalog().translate(caption, self._language))
        self._cue_icon.setPixmap(icon(name).pixmap(22, 22))
        for widget in (self._pause, self._replay):
            widget.setEnabled(True)

    def _asset_error(self, _message: str) -> None:
        self._last_cue = None
        self._cue.setText(
            self.tr_pair(
                (
                    "社区源码版提供文字引导，不附官方演示动画。",
                    "Community source includes text instructions without official animation assets.",
                )
            )
        )
        for widget in (self._pause, self._replay):
            widget.setEnabled(False)
        self.scene.update()

    def _sync_playback(self, playing: bool) -> None:
        self._pause.setIcon(icon("pause" if playing else "play"))
        self._pause.setAccessibleName(
            self.tr_pair(
                ("暂停动画", "Pause animation")
                if playing
                else ("播放动画", "Play animation")
            )
        )
        self._pause.setToolTip(self._pause.accessibleName())

    def toggle_playback(self) -> None:
        self.player.pause() if self.player.playing else self.player.play()

    def _popup(self, title: str) -> Dialog:
        """A help popup: the shared shell at the small width, round close."""
        dialog = Dialog(self, title, width=SMALL, object_name="onboardingHelp")
        dialog.setWindowTitle(title)
        dialog.title_label.setText(title)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._popups.append(dialog)
        dialog.finished.connect(
            lambda _result: (
                self._popups.remove(dialog) if dialog in self._popups else None
            )
        )
        return dialog

    def open_help(self) -> None:
        self._play_after_transition = False
        self.player.pause()
        dialog = self._popup(self.tr_pair(self.active_step.help))
        body = QLabel(self.tr_pair(self.active_step.answer))
        body.setWordWrap(True)
        dialog.body_layout().addWidget(body)
        if self._step_index == 3:
            demos = QVBoxLayout()
            demos.setSpacing(8)
            for slug, caption in (
                ("knob-clockwise", ("顺时针示范", "Clockwise")),
                ("knob-counterclockwise", ("逆时针示范", "Counterclockwise")),
                ("knob-press", ("按压示范", "Press")),
            ):
                button = Button(self.tr_pair(caption), "secondary", size="small")
                button.clicked.connect(
                    lambda _checked=False, clip=slug: (
                        dialog.accept(),
                        self.player.load(clip, autoplay=True),
                    )
                )
                demos.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            dialog.body_layout().addLayout(demos)
        replay = dialog.add_button(
            self.tr_pair(
                ("从头再看", "Start over")
                if self._step_index == 6
                else ("重播示范", "Replay demo")
            ),
            "secondary",
        )
        replay.clicked.connect(
            lambda: (
                dialog.accept(),
                self.goto_step(0)
                if self._step_index == 6
                else self.player.load(self.active_step.clip, autoplay=True),
            )
        )
        for widget in dialog.findChildren(QWidget):
            widget.setProperty("boringI18nSkip", True)
        dialog.open()

    def _finish(self, _result: int) -> None:
        self._play_after_transition = False
        self.scene.transition_from(QPixmap(), reduce_motion=True)
        for popup in list(self._popups):
            popup.reject()
        self.player.close()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self, self._place_dots)
        if self.player.movie is None:
            self.player.load(self.active_step.clip)
        if self._autoplay:
            self.player.play()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._play_after_transition = False
        self.player.pause()
        super().hideEvent(event)
