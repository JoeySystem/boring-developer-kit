"""NORMAL home artwork editor and device readback preview."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QProgressBar, QSlider, QVBoxLayout, QWidget,
)

from controller_config.i18n import set_translatable_accessible_name, set_translatable_text, translate_ui_text
from controller_config.screen_icon import icon_preview, load_icon_image, render_icon
from controller_config.views.icon_material_review import IconMaterialReview


@dataclass
class ScreenIconDraft:
    image: QImage | None = None
    filename: str = ""
    zoom: float = 1.0
    center_x: float = 0.5
    center_y: float = 0.5

    @property
    def is_dirty(self) -> bool:
        return self.image is not None

    def clear(self) -> None:
        self.image = None
        self.filename = ""
        self.zoom = 1.0
        self.center_x = self.center_y = 0.5


class _CropPreview(QLabel):
    moved = Signal(float, float)

    def __init__(self) -> None:
        super().__init__(objectName="screenIconPreview")
        self.setFixedSize(160, 160)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.OpenHandCursor)
        self._last_position = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._last_position = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last_position is not None and event.buttons() & Qt.LeftButton:
            delta = event.position() - self._last_position
            self._last_position = event.position()
            self.moved.emit(delta.x() / self.width(), delta.y() / self.height())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._last_position = None
        self.setCursor(Qt.OpenHandCursor)


class ScreenIconEditor(QWidget):
    upload_requested = Signal(bytes)
    reset_requested = Signal()
    refresh_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, draft: ScreenIconDraft, *, device_supported: bool = False) -> None:
        super().__init__(objectName="screenIconEditor")
        self.draft = draft
        self.payload: bytes | None = None
        self._transfer = None
        self._review_context = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)

        def label(source: str, name: str = "") -> QLabel:
            widget = QLabel(objectName=name)
            widget.setWordWrap(True)
            set_translatable_text(widget, source)
            layout.addWidget(widget)
            return widget

        label("NORMAL 首页图片 · 彩色")
        label("仅替换 NORMAL 模式的空闲首页，不覆盖 CODEX／CC、菜单、番茄钟、提示词盘或系统警告。", "screenIconScope")
        self.device_label = label("设备当前图标：尚未读取")
        self.device_preview = QLabel(objectName="screenIconDevicePreview")
        self.device_preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.device_preview)
        self.preview = _CropPreview()
        layout.addWidget(self.preview, alignment=Qt.AlignHCenter)
        self.filename = label("尚未选择图片")
        self.filename.setObjectName("screenIconFilename")
        self.filename.setTextFormat(Qt.PlainText)
        label("本地预览 · 尚未写入设备")
        label("素材要求", "screenIconRequirementsTitle")
        label("静态 PNG、JPG/JPEG · 最大 10 MiB · 总像素不超过 1600 万", "screenIconFormats")
        label("不支持 GIF、APNG 动图、SVG 或视频。", "screenIconUnsupportedFormats")
        label("支持彩色图片。屏幕面板是方形，可见区域为圆形；圆外内容不显示，重要内容请放在圆内。", "screenIconCropHint")
        label("拖动预览或使用下方滑杆调整裁切；确认效果后再写入设备。", "screenIconPreviewHint")
        self.import_button = QPushButton(objectName="screenIconImport")
        set_translatable_text(self.import_button, "导入图片…")
        self.import_button.clicked.connect(self._choose_image)
        layout.addWidget(self.import_button)
        self.material_review = IconMaterialReview()
        self.material_review.changed.connect(self._sync_transfer)
        self.material_review.check.accepted.connect(self._accept_checked_image)
        layout.addWidget(self.material_review)
        form = QFormLayout()
        layout.addLayout(form)
        self.zoom = self._slider(form, "缩放", "screenIconZoom", 100, 400)
        self.horizontal = self._slider(form, "水平位置", "screenIconHorizontal", 0, 1000)
        self.vertical = self._slider(form, "垂直位置", "screenIconVertical", 0, 1000)
        for slider in (self.zoom, self.horizontal, self.vertical):
            slider.valueChanged.connect(self._adjust)
        self.preview.moved.connect(self._pan)
        self.discard = QPushButton(objectName="screenIconDiscard")
        set_translatable_text(self.discard, "丢弃本地图标")
        self.discard.clicked.connect(self.discard_candidate)
        layout.addWidget(self.discard)
        row = QHBoxLayout()
        self.device_buttons = {}
        for name, source in (("screenIconWrite", "写入首页图片"), ("screenIconReset", "恢复默认首页")):
            button = QPushButton(objectName=name)
            set_translatable_text(button, source)
            button.setEnabled(False)
            row.addWidget(button)
            self.device_buttons[name] = button
        layout.addLayout(row)
        self.device_buttons["screenIconWrite"].clicked.connect(self._confirm_upload)
        self.device_buttons["screenIconReset"].clicked.connect(self._confirm_reset)
        label("恢复默认首页只移除首页自定义图片，不恢复出厂设置，也不改变其他小图标、按键映射或提示词。", "screenIconResetScope")
        self.transfer_status = label("控制台图标协议接入尚未完成，暂不能写入或恢复默认。" if device_supported else
              "当前固件尚未支持自定义图标，可先导入并预览。")
        self.transfer_progress = QProgressBar(objectName="screenIconProgress")
        self.transfer_progress.setRange(0, 100)
        self.transfer_progress.hide()
        layout.addWidget(self.transfer_progress)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(objectName="screenIconRefresh")
        set_translatable_text(self.refresh_button, "读取设备图标")
        self.refresh_button.setEnabled(False)
        self.refresh_button.clicked.connect(self.refresh_requested)
        row.addWidget(self.refresh_button)
        self.cancel_button = QPushButton(objectName="screenIconCancel")
        set_translatable_text(self.cancel_button, "取消上传")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        label("本地图标只在本次运行中保留，不包含在配置方案或配置导出中。")
        self._refresh_controls()

    def bind_transfer(self, transfer) -> None:
        self._transfer = transfer
        transfer.changed.connect(self._sync_transfer)
        self._sync_transfer()

    def _sync_transfer(self) -> None:
        if self.material_review.check.busy and (
            self._review_context != self._import_context() or self._transfer and self._transfer.busy
        ):
            self.material_review.cancel()
        transfer = self._transfer
        if transfer is None:
            return
        if transfer.verified_upload is not None and self.payload == transfer.verified_upload:
            self.draft.clear()
            self._refresh_controls()
        set_translatable_text(self.transfer_status, transfer.status)
        self.transfer_progress.setVisible(transfer.busy)
        self.transfer_progress.setValue(transfer.progress)
        metadata = transfer.metadata
        set_translatable_text(self.device_label, (
            "设备当前图标：内置默认" if metadata and metadata["source"] == "default"
            else "设备当前图标：自定义（已读回）" if transfer.pixels is not None
            else "设备当前图标：尚未读取"
        ))
        if transfer.pixels is not None:
            self.device_preview.setPixmap(QPixmap.fromImage(icon_preview(transfer.pixels)))
        else:
            self.device_preview.clear()
        ready = transfer.supported and transfer.writable and not transfer.busy and not self.material_review.check.busy
        self.device_buttons["screenIconWrite"].setEnabled(ready and self.payload is not None)
        self.device_buttons["screenIconReset"].setEnabled(ready)
        self.refresh_button.setEnabled(transfer.supported and not transfer.busy)
        self.cancel_button.setEnabled(transfer.can_cancel)
        self.import_button.setEnabled(not transfer.busy)
        self.discard.setEnabled(self.draft.is_dirty and not transfer.busy)
        for control in (self.zoom, self.horizontal, self.vertical, self.preview):
            control.setEnabled(self.draft.is_dirty and not transfer.busy)

    def _confirm_upload(self) -> None:
        if self.payload is None:
            return
        pixels = self.payload
        epoch = self._transfer.connection_epoch if self._transfer is not None else None
        if QMessageBox.question(self, translate_ui_text("写入首页图片"), translate_ui_text(
            "将替换当前设备的 NORMAL 首页图标，其他设置不变。是否继续？"
        ), QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            if self._confirm_same_connection(epoch):
                self.upload_requested.emit(pixels)

    def _confirm_reset(self) -> None:
        epoch = self._transfer.connection_epoch if self._transfer is not None else None
        if QMessageBox.question(self, translate_ui_text("恢复默认首页"), translate_ui_text(
            "仅恢复内置首页图标，不恢复出厂设置，也不修改按键和提示词。是否继续？"
        ), QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            if self._confirm_same_connection(epoch):
                self.reset_requested.emit()

    def _confirm_same_connection(self, epoch: int | None) -> bool:
        if self._transfer is None or epoch != self._transfer.connection_epoch:
            QMessageBox.warning(self, translate_ui_text("设备连接已变化"), translate_ui_text(
                "设备已断连或重新连接，请重新确认目标设备后操作。"))
            return False
        return True

    def _slider(self, form: QFormLayout, source: str, name: str, low: int, high: int) -> QSlider:
        slider = QSlider(Qt.Horizontal, objectName=name)
        slider.setRange(low, high)
        set_translatable_accessible_name(slider, source)
        caption = QLabel()
        set_translatable_text(caption, source)
        form.addRow(caption, slider)
        return slider

    def import_image(self, path: str) -> None:
        # Decode before replacing the candidate: a failed import preserves edits.
        self.material_review.cancel()
        image = load_icon_image(path)
        self._apply_image(path, image)

    def _apply_image(self, path: str, image: QImage) -> None:
        self.draft.clear()
        self.draft.image = image
        self.draft.filename = Path(path).name
        self._refresh_controls()

    def _import_context(self):
        return self._transfer, self._transfer.connection_epoch if self._transfer else None

    def review_image(self, path: str) -> None:
        if not self.import_button.isEnabled():
            return
        self._review_context = self._import_context()
        self.material_review.start(path)

    def _accept_checked_image(self, path: str, image: QImage) -> None:
        if self._review_context == self._import_context() and self.import_button.isEnabled():
            self._apply_image(path, image)

    def _choose_image(self) -> None:
        context = self._import_context()
        path, _ = QFileDialog.getOpenFileName(
            self, translate_ui_text("导入图片…"), "", "PNG / JPEG (*.png *.jpg *.jpeg)"
        )
        if path and context == self._import_context():
            self.review_image(path)

    def discard_candidate(self) -> None:
        self.material_review.cancel()
        self.draft.clear()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        for slider, value in ((self.zoom, round(self.draft.zoom * 100)),
                              (self.horizontal, round(self.draft.center_x * 1000)),
                              (self.vertical, round(self.draft.center_y * 1000))):
            slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(False)
            slider.setEnabled(self.draft.is_dirty)
        self.discard.setEnabled(self.draft.is_dirty)
        self.preview.setEnabled(self.draft.is_dirty)
        self.filename.setProperty("boringI18nSkip", self.draft.is_dirty)
        if self.draft.is_dirty:
            self.filename.setText(self.draft.filename)
        else:
            set_translatable_text(self.filename, "尚未选择图片")
        self._render()
        if self._transfer is not None:
            self._sync_transfer()

    def _adjust(self) -> None:
        self.draft.zoom = self.zoom.value() / 100
        self.draft.center_x = self.horizontal.value() / 1000
        self.draft.center_y = self.vertical.value() / 1000
        if self.draft.image is not None:
            side = min(self.draft.image.width(), self.draft.image.height()) / self.draft.zoom
            self.draft.center_x = max(side / self.draft.image.width() / 2, min(
                1 - side / self.draft.image.width() / 2, self.draft.center_x))
            self.draft.center_y = max(side / self.draft.image.height() / 2, min(
                1 - side / self.draft.image.height() / 2, self.draft.center_y))
        self._refresh_controls()

    def _pan(self, dx: float, dy: float) -> None:
        image = self.draft.image
        if image is None:
            return
        side = min(image.width(), image.height()) / self.draft.zoom
        # Clamp the crop center so dragging back from an edge responds immediately.
        self.draft.center_x = max(side / image.width() / 2, min(
            1 - side / image.width() / 2, self.draft.center_x - dx * side / image.width()))
        self.draft.center_y = max(side / image.height() / 2, min(
            1 - side / image.height() / 2, self.draft.center_y - dy * side / image.height()))
        self._refresh_controls()

    def _render(self) -> None:
        if self.draft.image is None:
            self.payload = None
            self.preview.clear()
            return
        self.payload = render_icon(self.draft.image, zoom=self.draft.zoom,
                                   center_x=self.draft.center_x, center_y=self.draft.center_y)
        self.preview.setPixmap(QPixmap.fromImage(icon_preview(self.payload)).scaled(
            160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
