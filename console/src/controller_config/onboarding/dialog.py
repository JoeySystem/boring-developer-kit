from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    Signal,
    Slot,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from controller_config.accessibility import system_reduces_motion
from controller_config.i18n import normalize_language
from controller_config.text_catalog import get_text_catalog

from .content import BLUETOOTH_STEP, ONBOARDING_STEPS, cue_at
from .player import GuidePlayer, asset_root


STYLE = """
QDialog#onboardingDialog { background: transparent; }
QFrame#companionCard { background: #fafbfb; border: 1px solid #d4d8d2; border-radius: 24px; }
QFrame#companionCard QWidget { color: #242625; font-size: 15px; background: transparent; }
QFrame#companionCard QLabel { border: 0; padding: 0; }
QFrame#companionCard QLabel#onboardingBrand { font-size: 17px; font-weight: 650; }
QFrame#companionCard QLabel#onboardingTitle { font-size: 32px; font-weight: 600; }
QFrame#companionCard QLabel#onboardingDescription { font-size: 16px; color: #626762; }
QFrame#companionCard QLabel#guideCue { font-size: 14px; color: #4b514a; }
QFrame#companionCard QPushButton { border: 1px solid #cbd0ca; border-radius: 22px; padding: 10px 17px; color: #525951; background: transparent; min-height: 22px; }
QFrame#companionCard QPushButton:hover { background: #eff1ee; }
QFrame#companionCard QPushButton:pressed { background: #e5eae3; }
QFrame#companionCard QPushButton:focus { border: 2px solid #f47b10; }
QFrame#companionCard QPushButton#onboardingNext { background: #f47b10; border: 0; border-radius: 24px; padding: 12px 24px; color: #241e16; font-weight: 600; min-height: 24px; }
QFrame#companionCard QPushButton#onboardingNext:hover { background: #e97108; }
QFrame#companionCard QPushButton#onboardingNext:focus { border: 2px solid #30251b; }
QFrame#companionCard QPushButton[quiet="true"] { border: 0; }
QFrame#companionCard QPushButton[iconOnly="true"] { min-height: 0; padding: 0; border: 0; border-radius: 18px; }
QFrame#companionCard QPushButton[iconOnly="true"]:focus { border: 2px solid #f47b10; }
QFrame#companionCard QPushButton#guideReplay { min-height: 0; border-radius: 20px; padding: 5px 12px; }
QFrame#companionCard QPushButton[connection="true"] { border-radius: 14px; text-align: left; padding: 12px 16px; min-height: 38px; background: #f4f6f3; }
QFrame#companionCard QPushButton[connection="true"]:checked { border: 2px solid #f47b10; background: #fff5eb; }
QFrame#companionCard QPushButton[progress="true"] { padding: 0; min-height: 0; border: 0; background: transparent; color: #bec6ba; font-size: 15px; }
QFrame#companionCard QPushButton[progress="true"]:checked { color: #f47b10; }
QFrame#companionCard QPushButton[progress="true"]:focus { border: 1px solid #f47b10; border-radius: 12px; }
"""


def icon(name: str) -> QIcon:
    return QIcon(str(asset_root() / "icons" / f"{name}.svg"))


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
        self._transition.setDuration(260)
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
            painter.setPen(QColor("#626762"))
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


class OnboardingDialog(QDialog):
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
        super().__init__(
            parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setObjectName("onboardingDialog")
        self.setWindowTitle("MIST · Companion guide")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(620, 560)
        screen = self.screen().availableGeometry()
        self.resize(min(1100, screen.width()), min(680, screen.height()))
        self.setStyleSheet(STYLE)
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        card = QFrame(objectName="companionCard")
        outer.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(30, 18, 30, 24)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(QLabel("MIST", objectName="onboardingBrand"))
        header.addStretch()
        self._skip = QPushButton(objectName="onboardingSkip")
        self._skip.setProperty("quiet", True)
        self._skip.clicked.connect(self.reject)
        header.addWidget(self._skip)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(26)
        scroll.setWidget(self._body)
        layout.addWidget(scroll, 1)
        visual = QWidget()
        visual_layout = QVBoxLayout(visual)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setSpacing(3)
        self.scene = Scene(self.player)
        visual_layout.addWidget(self.scene, 1)
        controls = QWidget()
        controls.setFixedWidth(390)
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(8, 0, 8, 0)
        controls_layout.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(12)
        self._cue_icon = QLabel()
        self._cue_icon.setFixedSize(24, 24)
        self._cue = QLabel(objectName="guideCue")
        self._cue.setWordWrap(True)
        row.addWidget(self._cue_icon)
        row.addWidget(self._cue, 1)
        self._pause = self._icon_button("pause", "guidePause")
        self._pause.clicked.connect(self.toggle_playback)
        self._replay = QPushButton(
            icon("arrow-counter-clockwise"), "重播", objectName="guideReplay"
        )
        self._replay.setFixedHeight(40)
        self._replay.setIconSize(QSize(18, 18))
        self._replay.clicked.connect(self.player.replay)
        row.addWidget(self._pause)
        row.addWidget(self._replay)
        controls_layout.addLayout(row)
        visual_layout.addWidget(controls, 0, Qt.AlignmentFlag.AlignHCenter)
        self._body_layout.addWidget(visual, 57)
        self._visual = visual

        copy = QWidget()
        copy.setMaximumWidth(390)
        text = QVBoxLayout(copy)
        text.setContentsMargins(0, 16, 0, 16)
        text.setSpacing(20)
        text.addStretch()
        self._title = QLabel(objectName="onboardingTitle")
        self._title.setWordWrap(True)
        self._description = QLabel(objectName="onboardingDescription")
        self._description.setWordWrap(True)
        text.addWidget(self._title)
        text.addWidget(self._description)
        self._connections = QWidget()
        choices = QVBoxLayout(self._connections)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(10)
        group = QButtonGroup(self)
        self._usb = QPushButton(objectName="guideUSB")
        self._ble = QPushButton(objectName="guideBluetooth")
        for button, name in ((self._usb, "usb"), (self._ble, "bluetooth")):
            button.setCheckable(True)
            button.setProperty("connection", True)
            button.setIcon(icon(name))
            button.setIconSize(QSize(22, 22))
            group.addButton(button)
            choices.addWidget(button)
        self._usb.clicked.connect(lambda: self.set_connection_mode(False))
        self._ble.clicked.connect(lambda: self.set_connection_mode(True))
        text.addWidget(self._connections)
        self._connection_note = QLabel()
        self._connection_note.setWordWrap(True)
        text.addWidget(self._connection_note)
        self._help = QPushButton(objectName="guideHelp")
        self._help.setIcon(icon("question"))
        self._help.clicked.connect(self.open_help)
        text.addWidget(self._help, 0, Qt.AlignmentFlag.AlignLeft)
        text.addStretch()
        self._body_layout.addWidget(copy, 43)
        self._copy = copy

        self._footer = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._footer.setSpacing(20)
        dots = QWidget()
        dots_layout = QHBoxLayout(dots)
        dots_layout.setContentsMargins(0, 0, 0, 0)
        dots_layout.setSpacing(0)
        self._dots = []
        for i in range(7):
            button = QPushButton("●", objectName=f"guideStep{i}")
            button.setProperty("progress", True)
            button.setFixedSize(27, 28)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, index=i: self.goto_step(index)
            )
            dots_layout.addWidget(button)
            self._dots.append(button)
        self._footer.addWidget(dots, 1, Qt.AlignmentFlag.AlignLeft)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(8)
        self._back = QPushButton(objectName="onboardingBack")
        self._back.setProperty("quiet", True)
        self._back.clicked.connect(self.previous_step)
        self._defer = QPushButton()
        self._defer.setProperty("quiet", True)
        self._defer.clicked.connect(self.next_step)
        self._next = QPushButton(objectName="onboardingNext")
        self._next.setMinimumWidth(180)
        self._next.clicked.connect(self.next_step)
        for button in (self._back, self._defer, self._next):
            button.setAutoDefault(False)
            actions_layout.addWidget(button)
        self._footer.addWidget(actions, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(self._footer)
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
        button.setFixedSize(36, 36)
        button.setIcon(icon(name))
        button.setIconSize(QSize(18, 18))
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
        self._defer.setText(self.tr_pair(("稍后配对", "Pair later")))
        self._defer.setVisible(self._step_index == 1 and self._bluetooth)
        self._connections.setVisible(self._step_index == 1)
        self._usb.setText(
            self.tr_pair(
                (
                    "使用数据线\n连接应用，修改配置",
                    "Use a USB cable\nConnect to configure",
                )
            )
        )
        self._ble.setText(
            self.tr_pair(
                (
                    "设置蓝牙\n无线使用，可稍后设置",
                    "Set up Bluetooth\nOptional wireless use",
                )
            )
        )
        self._usb.setChecked(not self._bluetooth)
        self._ble.setChecked(self._bluetooth)
        self._connection_note.setVisible(self._step_index == 1 and self._bluetooth)
        self._connection_note.setText(
            self.tr_pair(
                (
                    "支持通过 USB 或蓝牙连接控制台并修改配置；固件升级需要 USB。",
                    "Configure through USB or Bluetooth; firmware updates require USB.",
                )
            )
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

    def _popup(self, title: str) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setStyleSheet(
            "QDialog{background:#fafbfb} QLabel{color:#242625;font-size:15px} QPushButton{color:#242625;background:#eef1ed;border:0;border-radius:20px;padding:11px 18px}"
        )
        dialog.resize(420, 300)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setStyleSheet("font-size:23px;font-weight:600")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for w in dialog.findChildren(QWidget):
            w.setProperty("boringI18nSkip", True)
        self._popups.append(dialog)
        dialog.finished.connect(
            lambda _result: (
                self._popups.remove(dialog) if dialog in self._popups else None
            )
        )
        return dialog, layout

    def open_help(self) -> None:
        self._play_after_transition = False
        self.player.pause()
        dialog, layout = self._popup(self.tr_pair(self.active_step.help))
        body = QLabel(self.tr_pair(self.active_step.answer))
        body.setProperty("boringI18nSkip", True)
        body.setWordWrap(True)
        layout.addWidget(body)
        if self._step_index == 3:
            for slug, caption in (
                ("knob-clockwise", ("顺时针示范", "Clockwise")),
                ("knob-counterclockwise", ("逆时针示范", "Counterclockwise")),
                ("knob-press", ("按压示范", "Press")),
            ):
                button = QPushButton(self.tr_pair(caption))
                button.setProperty("boringI18nSkip", True)
                button.clicked.connect(
                    lambda _checked=False, clip=slug: (
                        dialog.accept(),
                        self.player.load(clip, autoplay=True),
                    )
                )
                layout.addWidget(button)
        row = QHBoxLayout()
        close = QPushButton(self.tr_pair(("关闭", "Close")))
        close.clicked.connect(dialog.accept)
        replay = QPushButton(
            self.tr_pair(
                ("从头再看", "Start over")
                if self._step_index == 6
                else ("重播示范", "Replay demo")
            )
        )
        replay.clicked.connect(
            lambda: (
                dialog.accept(),
                self.goto_step(0)
                if self._step_index == 6
                else self.player.load(self.active_step.clip, autoplay=True),
            )
        )
        for button in (close, replay):
            button.setProperty("boringI18nSkip", True)
            row.addWidget(button)
        layout.addLayout(row)
        dialog.open()

    def _finish(self, _result: int) -> None:
        self._play_after_transition = False
        self.scene.transition_from(QPixmap(), reduce_motion=True)
        for popup in list(self._popups):
            popup.reject()
        self.player.close()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.player.movie is None:
            self.player.load(self.active_step.clip)
        if self._autoplay:
            self.player.play()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._play_after_transition = False
        self.player.pause()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "_body_layout"):
            return
        narrow = self.width() < 850
        first = self._copy if narrow else self._visual
        if self._body_layout.indexOf(first) != 0:
            self._body_layout.removeWidget(first)
            self._body_layout.insertWidget(0, first)
        self._body_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        self._footer.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        self._body_layout.setStretch(0, 0 if narrow else 57)
        self._body_layout.setStretch(1, 0 if narrow else 43)
        self.scene.setMinimumHeight(240 if narrow else 260)
        self.scene.setMaximumHeight(290 if narrow else 16777215)
        self._copy.setMaximumWidth(16777215 if narrow else 390)
