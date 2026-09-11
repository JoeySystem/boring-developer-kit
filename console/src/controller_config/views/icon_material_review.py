"""Inline material checks shared by home-image and per-glyph editors."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from controller_config.i18n import set_translatable_text
from controller_config.icon_material_check import IconMaterialCheck


REASONS = {
    "format": "素材格式不支持，请换用静态 PNG 或 JPG/JPEG；不支持 GIF、SVG 或视频。",
    "animated": "不支持动图，请导出单张静态 PNG 或 JPG/JPEG 后重新选择。",
    "file_size": "文件超过 10 MiB，请压缩文件或更换较小的图片。",
    "pixels": "图片超过 1600 万像素，请缩小图片尺寸后重新选择。",
    "damaged": "图片不完整或已损坏，请重新导出 PNG 或 JPG/JPEG 后再试。",
    "transparent": "图片完全透明，没有可显示的内容，请更换有可见图案的素材。",
    "read": "无法读取图片，请确认文件已下载到本机且可以打开，再重新选择。",
    "timeout": "素材检查超时，已停止本次检查；请换一张图片或重新选择。",
    "worker": "素材检查未完成，请重新选择图片再试。",
}


class IconMaterialReview(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__(objectName="iconMaterialReview")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel(objectName="iconMaterialMessage")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        self.cancel_button = QPushButton(objectName="iconMaterialCancel")
        set_translatable_text(self.cancel_button, "取消素材检查")
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.cancel_button)
        self.check = IconMaterialCheck(self)
        self.check.busy_changed.connect(self._busy_changed)
        self.check.rejected.connect(self._rejected)
        self.check.accepted.connect(self._accepted)
        self.message.hide()
        self.cancel_button.hide()

    def start(self, path: str) -> None:
        self.check.start(path)
        self._message("正在检查素材… 可随时取消或选择另一张图片。")

    def cancel(self) -> None:
        if self.check.busy:
            self.check.cancel()
            self._message("已取消素材检查，原有内容未改变；可继续选择图片。")

    def _busy_changed(self, busy: bool) -> None:
        self.cancel_button.setVisible(busy)
        self.changed.emit()

    def _message(self, source: str) -> None:
        set_translatable_text(self.message, source)
        self.message.show()

    def _rejected(self, reason: str) -> None:
        self._message(REASONS.get(reason, REASONS["worker"]) + " 原有内容未改变，可继续导入其他图片。")

    def _accepted(self, _path, _image) -> None:
        self._message("素材检查通过。请确认预览效果，再写入设备。")

    def hideEvent(self, event) -> None:
        self.cancel()
        super().hideEvent(event)
