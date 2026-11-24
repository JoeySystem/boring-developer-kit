"""Small builders shared by the main window and the page classes.

Moved unchanged out of ``main_window.py`` so pages can use them without
importing the main window; ``main_window.py`` imports them under the old
underscore names.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from controller_config.models import DeviceSnapshot
from controller_config.views.digital_label import Boring5RLabel


def card(*, object_name: str = "card") -> QFrame:
    card = QFrame(objectName=object_name)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return card


def planned_tool_capability(
    page_name: str, snapshot: DeviceSnapshot | None
) -> bool | None:
    if snapshot is None:
        return None
    features = snapshot.capabilities.get("features")
    if not isinstance(features, dict):
        return False
    feature_name = {
        "joystick": "joystick_calibration",
        "firmware": "firmware_update",
    }[page_name]
    return features.get(feature_name) is True


def planned_tool_capability_label(
    page_name: str,
    capability: bool | None,
) -> str:
    if capability is None:
        return "等待设备读取"
    if capability is not True:
        return "当前设备未声明支持"
    return {
        "joystick": "设备已声明支持校准",
        "firmware": "设备已声明支持更新",
    }[page_name]


def digital_caption(text: str) -> Boring5RLabel:
    return Boring5RLabel(
        text,
        scale=0.75,
        color="#a58c86",
        objectName="digitalCaption",
    )


def planned_tool_facts(
    page_name: str, snapshot: DeviceSnapshot | None
) -> tuple[str, ...]:
    if snapshot is None:
        return (
            "连接 BORING 设备后，这里会读取真实身份、状态与 CAPABILITIES。",
            "页面入口可提前查看，但不会把“尚未连接”误写成“设备不支持”。",
        )

    if page_name == "joystick":
        joystick = snapshot.config.get("joystick")
        calibrated = joystick.get("calibrated") if isinstance(joystick, dict) else None
        calibration_label = {True: "已校准", False: "尚未校准"}.get(
            calibrated, "未报告"
        )
        return (
            f"当前配置的摇杆状态：{calibration_label}。",
            "权威协议已定义 CALIBRATION_START、SAMPLE、CONFIRM 与 CANCEL。",
            "取消校准必须保留设备原有校准值。",
        )

    features = snapshot.capabilities.get("features")
    signature_required = (
        features.get("firmware_signature_required") if isinstance(features, dict) else None
    )
    signature_label = {
        True: "要求签名",
        False: "当前构建未要求签名",
    }.get(signature_required, "未报告")
    return (
        f"当前固件版本：{snapshot.versions.get('firmware', '—')}。",
        f"当前构建：{snapshot.versions.get('build_id', '未提供')}。",
        f"固件签名能力：{signature_label}。",
        "更新前必须核对 product_id、hardware_id、版本、大小与软件包 manifest。",
    )


def add_technical_details(layout: QVBoxLayout, name: str, text: str) -> None:
    toggle = QPushButton("更多信息", objectName=f"{name}DetailsToggle")
    toggle.setCheckable(True)
    details = QLabel(text, objectName=f"{name}Technical")
    details.setTextFormat(Qt.TextFormat.PlainText)
    details.setWordWrap(True)
    details.setTextInteractionFlags(Qt.TextSelectableByMouse)
    details.hide()
    toggle.toggled.connect(details.setVisible)
    layout.addWidget(toggle, 0, Qt.AlignLeft)
    layout.addWidget(details)
