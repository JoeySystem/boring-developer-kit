"""A view-only transcript of real connection state; never drives the transport."""
from __future__ import annotations

from time import monotonic

from PySide6.QtCore import QEvent, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QVBoxLayout, QGraphicsOpacityEffect

from controller_config.i18n import SKIP_TRANSLATION_PROPERTY
from controller_config.models import AppState, ScreenModel
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.v4_widgets import V4Card


_BUSY = {AppState.SCANNING, AppState.CONNECTING}
_READY = {AppState.READY, AppState.READ_ONLY}
_COMMANDS = {
    'HELLO': 'reading device identity...',
    'CAPABILITIES': 'reading capabilities...',
    'AUTH_GET_CERTIFICATE': 'reading certificate...',
    'AUTH_CHALLENGE': 'verifying identity...',
    'GET_STATUS': 'reading device state...',
    'GET_CONFIG': 'loading configuration...',
}


class ConnectionTerminal(V4Card):
    def __init__(self, parent=None):
        super().__init__(parent, role='primary', dots=True)
        self.setObjectName('connectionTerminal')
        self.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumHeight(330)
        self._last_state = None
        self._last_attempt = None
        self._compact = False
        self._present = False
        self._busy = False
        self._rows: list[QLabel] = []
        self._box = QVBoxLayout(self)
        self._box.setContentsMargins(20, 20, 20, 18)
        self._box.setSpacing(9)
        self._box.addWidget(Boring5RLabel('LINK / AUTH', scale=0.9, color='#9A958C'))
        self._box.addSpacing(12)
        self._lines = QVBoxLayout()
        self._lines.setSpacing(8)
        self._box.addLayout(self._lines)
        self._box.addStretch(1)
        self._cursor = QLabel('> _')
        self._cursor.setStyleSheet('color: #9A958C; background: transparent;')
        self._cursor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self._box.addWidget(self._cursor)
        self._blink = QTimer(self)
        self._blink.setInterval(550)
        self._blink.timeout.connect(self._blink_cursor)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._fade_out)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1)
        self._opacity.setEnabled(False)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b'opacity', self)
        self._fade.setDuration(180)
        self._fade.finished.connect(self._settle)

    @property
    def present(self):
        return self._present

    @property
    def transcript(self):
        return tuple(row.text() for row in self._rows)

    def observe(self, model: ScreenModel):
        previous = self._last_state
        self._last_state = model.state
        if model.state in _BUSY:
            if previous not in _BUSY or (model.state is AppState.SCANNING and previous is not AppState.SCANNING):
                now = monotonic()
                self._compact = self._last_attempt is not None and now - self._last_attempt < 8
                self._last_attempt = now
                self._clear()
            self._interrupt_finish()
            self._present = self._busy = True
            if model.state is AppState.SCANNING:
                line = 'scanning Bluetooth...' if '蓝牙' in model.message else 'scanning device...'
            elif model.message.startswith('正在读取：'):
                command = model.message.partition('：')[2]
                line = _COMMANDS.get(command, 'reading device...')
            elif '蓝牙' in model.message or 'ble:' in model.message:
                line = 'connecting Bluetooth...'
            elif '正在连接 ' in model.message:
                line = 'connecting USB...'
            else:
                line = 'opening device link...'
            self._append(line)
            self._sync_cursor()
        elif model.state in _READY and model.snapshot is not None:
            if previous in _READY:
                return  # Routine status polling must not replay the animation.
            self._busy = False
            if not self._present:
                return
            self._append('demo transport' if model.snapshot.port_name.startswith('demo:') else
                         'Bluetooth connected' if model.snapshot.connection_kind == 'bluetooth' else 'USB connected')
            authenticated = model.snapshot.trust.is_authenticated
            self._append('identity verified' if authenticated else 'development identity',
                         'success' if authenticated else 'notice')
            self._append('configuration loaded')
            self._append('read-only ready' if model.state is AppState.READ_ONLY else
                         'BORING MIST ready' if authenticated else 'development session ready',
                         'success' if authenticated else 'notice')
            self._sync_cursor()
            if self._compact:
                self._settle()
            else:
                self._hold.start(1100)
        else:
            self._interrupt_finish()
            self._present = True
            self._busy = False
            # A disconnect during the success hold must not keep showing ready.
            for row in list(self._rows):
                if row.property('resultLine'):
                    self._rows.remove(row)
                    self._lines.removeWidget(row)
                    row.deleteLater()
            if model.state is AppState.MULTIPLE_DEVICES:
                self._append('select a device to continue', 'notice')
            else:
                self._append(model.message or 'device disconnected', 'error')
            self._sync_cursor()

    def _append(self, text, tone='normal'):
        value = '> ' + text
        if self._rows and self._rows[-1].text() == value:
            return
        row = QLabel(value)
        row.setTextFormat(Qt.PlainText)
        row.setWordWrap(True)
        row.setFocusPolicy(Qt.NoFocus)
        row.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        row.setProperty('resultLine', tone in {'success', 'notice'} or text == 'configuration loaded')
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(11)
        row.setFont(font)
        color = {'normal': '#D5D0C6', 'success': '#8FB98F', 'notice': '#B6A58B', 'error': '#D4917C'}[tone]
        row.setStyleSheet(f'color: {color}; font-family: "{font.family()}"; font-size: 11pt; '
                         'background: transparent; border: none;')
        self._rows.append(row)
        self._lines.addWidget(row)
        while len(self._rows) > 7:
            old = self._rows.pop(0)
            self._lines.removeWidget(old)
            old.deleteLater()
        if not self._compact and tone == 'normal':
            effect = QGraphicsOpacityEffect(row)
            row.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b'opacity', row)
            animation.setDuration(140)
            animation.setStartValue(0.25)
            animation.setEndValue(1)
            animation.start()
            row._line_animation = animation

    def _clear(self):
        for row in self._rows:
            self._lines.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

    def _interrupt_finish(self):
        self._hold.stop()
        self._fade.stop()
        self._opacity.setOpacity(1)
        self._opacity.setEnabled(False)

    def _fade_out(self):
        # Qt does not reliably paint nested active graphics effects.
        for row in self._rows:
            if row.graphicsEffect() is not None:
                row.graphicsEffect().setEnabled(False)
        self._opacity.setEnabled(True)
        self._fade.setStartValue(1)
        self._fade.setEndValue(0)
        self._fade.start()

    def _settle(self):
        self._present = False
        self._blink.stop()
        self.hide()

    def cover_card(self, card):
        """Keep the device card's layout unchanged during the short success hold."""
        self.setParent(card)
        self.setMinimumWidth(0)
        self.setMaximumWidth(16777215)
        card.installEventFilter(self)
        self.setGeometry(card.rect())
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def _sync_cursor(self):
        if self._busy and self.isVisible() and not self._compact:
            self._blink.start()
        else:
            self._blink.stop()
        self._cursor.setText('> _' if self._busy else '> done' if self._last_state in _READY else '> paused')

    def _blink_cursor(self):
        self._cursor.setText('>   ' if self._cursor.text() == '> _' else '> _')

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_cursor()

    def hideEvent(self, event):
        self._blink.stop()
        super().hideEvent(event)
