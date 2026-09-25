"""Visible connection feedback; observes transport state without driving it."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.models import AppState
from controller_config.views.device_silhouette import DeviceModelCanvas


class ConnectionProgress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connectionProgress")
        self.setFocusPolicy(Qt.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 24)
        layout.setSpacing(10)
        layout.addStretch(1)
        self.canvas = DeviceModelCanvas(None, {}, None, self)
        layout.addWidget(self.canvas, 0, Qt.AlignHCenter)
        self.title = QLabel("连接中", objectName="connectionProgressTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("color: #FF6A00; font-size: 30px; font-weight: 700; background: transparent;")
        layout.addWidget(self.title)
        self.hint = QLabel(objectName="connectionProgressHint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def observe(self, state):
        set_translatable_text(self.title, "连接中")
        set_translatable_text(self.hint, "正在寻找设备" if state is AppState.SCANNING
                              else "连接完成后自动进入")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        side = max(140, min(460, self.width() - 48, self.height() - 138))
        if self.canvas.width() != side:
            self.canvas.setFixedSize(side, side)
