"""未连接／读取失败等状态页: what the window shows while there is no
device configuration to edit (no device, read failed, firmware update
required, authenticity failed, incompatible).

Several routes fall back to it, so it is a page body without a route of its
own and without an in-place update: the route that shows it decides reuse.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from controller_config.models import AppState, ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.device_silhouette import create_device_silhouette
from controller_config.views.v4_widgets import V4Card


# The official-recovery note is about firmware that cannot authenticate or cannot be
# maintained. A plain read failure (timeout, CRC, port busy) is fixed by reconnecting,
# so the note would only alarm (design decision: 只在固件认证失败或固件维护有问题时显示).
RECOVERY_HINT_STATES = frozenset({AppState.AUTHENTICITY_FAILED, AppState.INCOMPATIBLE})


class StatePage:
    def __init__(self, view_model: MainViewModel, *, home_button: Callable[[], QWidget]) -> None:
        self._view_model = view_model
        self._home_button = home_button

    def build(self, model: ScreenModel) -> QWidget:
        scroll = QScrollArea(objectName="devicePreviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        page = QWidget(objectName="devicePreviewPage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(24)
        stage = QVBoxLayout()
        stage.addStretch(1)
        stage.addWidget(create_device_silhouette(
            None, mappings={}, on_control=None, selected_control_id=None,
        ), 0, Qt.AlignHCenter)
        stage.addStretch(1)
        layout.addLayout(stage, 1)
        card = V4Card(role="focus")
        card.setMinimumWidth(320)
        card.setMaximumWidth(480)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(16)
        title_row = QHBoxLayout()
        title = QLabel("配置未能读取" if model.state is AppState.READ_FAILED else "连接设备",
                       objectName="inspectorTitle")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        title_row.addWidget(self._home_button(), 0, Qt.AlignTop)
        box.addLayout(title_row)
        instruction = QLabel(
            model.message if model.state is AppState.READ_FAILED
            else "请使用 USB 连接设备。" if model.state is AppState.FIRMWARE_UPDATE_REQUIRED
            else "插入 USB，或先在系统蓝牙设置中连接设备。",
            objectName="muted",
        )
        instruction.setWordWrap(True)
        box.addWidget(instruction)
        retry = QPushButton(
            "连接 USB 后重新扫描"
            if model.state is AppState.FIRMWARE_UPDATE_REQUIRED
            else "重新连接并读取配置" if model.state is AppState.READ_FAILED
            else "重新连接" if model.state is AppState.AUTHENTICITY_FAILED
            else "连接设备",
            objectName="connectDeviceButton",
        )
        retry.setProperty("buttonRole", "primary")
        retry.setEnabled(model.state not in {AppState.SCANNING, AppState.CONNECTING})
        retry.clicked.connect(self._view_model.refresh)
        box.addWidget(retry)
        if model.state in RECOVERY_HINT_STATES:
            recovery_hint = QLabel(
                "旧固件无法认证或不支持固件维护时，需要官方 USB 恢复工具或售后协助。请记录设备序列号与技术详情；重复连接不会补齐这些能力。",
                objectName="firmwareRecoveryGuidance",
            )
            recovery_hint.setWordWrap(True)
            box.addWidget(recovery_hint)
        box.addStretch(1)
        layout.addWidget(card, 1, Qt.AlignVCenter)
        scroll.setWidget(page)
        return scroll
