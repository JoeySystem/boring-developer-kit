"""Per-device, per-resource editor for the screen's monochrome glyphs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from controller_config.i18n import set_translatable_text, translate_ui_text
from controller_config.screen_icon import load_icon_image
from controller_config.views.icon_material_review import IconMaterialReview


GLYPH_NAMES = {
    "timer": "番茄钟", "settings": "设置", "macos": "macOS", "windows_linux": "Windows / Linux",
    "back": "返回", "confirm": "确认", "power": "电源", "warning": "警告",
    "play": "播放", "pause": "暂停", "cancel": "取消", "error": "错误", "config": "配置",
    "lighting": "按键灯光", "haptic": "振动", "standby": "待机", "exit": "退出",
    "normal_small": "NORMAL 小标志", "codex_small": "CODEX 小标志", "ble_link": "蓝牙连接",
    "restart": "重启", "system": "操作系统", "normal_mode": "NORMAL 模式",
    "codex_mode": "CODEX 模式", "claude_code_mode": "CC 模式", "usb": "USB",
    "bluetooth": "蓝牙", "battery": "电量",
}


@dataclass
class GlyphDraft:
    image: QImage | None = None
    filename: str = ""
    invert: bool = False

    @property
    def is_dirty(self) -> bool:
        return self.image is not None

    def clear(self) -> None:
        self.image = None
        self.filename = ""
        self.invert = False


def render_glyph(image: QImage, width: int, height: int, *, invert: bool = False) -> bytes:
    """Fit without cropping; luminance times alpha becomes one glyph cell."""
    if image.isNull() or width <= 0 or height <= 0:
        raise ValueError("A glyph needs an image and positive dimensions.")
    fitted = image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    output = bytearray(width * height)
    left, top = (width - fitted.width()) // 2, (height - fitted.height()) // 2
    for y in range(fitted.height()):
        for x in range(fitted.width()):
            color = fitted.pixelColor(x, y)
            luminance = (299 * color.red() + 587 * color.green() + 114 * color.blue() + 500) // 1000
            if invert:
                luminance = 255 - luminance
            output[(top + y) * width + left + x] = (luminance * color.alpha() + 127) // 255
    return bytes(output)


def glyph_preview(data: bytes, width: int, height: int) -> QImage:
    """Show each alpha cell separately instead of pretending it is a photograph."""
    if width <= 0 or height <= 0 or len(data) != width * height:
        raise ValueError("Glyph dimensions do not match its cells.")
    pitch = max(2, min(12, 160 // max(width, height)))
    image = QImage(width * pitch, height * pitch, QImage.Format_RGB32)
    image.fill(QColor("#151612"))
    painter = QPainter(image)
    for index, alpha in enumerate(data):
        painter.fillRect((index % width) * pitch, (index // width) * pitch,
                         pitch - 1, pitch - 1, QColor(236, 234, 226, alpha))
    painter.end()
    return image


class ScreenGlyphEditor(QWidget):
    selection_requested = Signal(str)
    refresh_requested = Signal()
    write_requested = Signal(bytes)
    reset_requested = Signal()

    def __init__(self, drafts: dict[tuple[str, str, str], GlyphDraft]) -> None:
        super().__init__(objectName="screenGlyphEditor")
        self.drafts = drafts
        self.payload: bytes | None = None
        self._transfer = None
        self._review_context = None
        self._last_verified_write = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)

        def label(source: str, name: str = "") -> QLabel:
            widget = QLabel(objectName=name)
            widget.setWordWrap(True)
            widget.setTextFormat(Qt.PlainText)
            set_translatable_text(widget, source)
            layout.addWidget(widget)
            return widget

        label("功能／模式小图标 · 单色点阵")
        label("选择一个图标单独修改；未修改的图标继续保留，每个图标都可单独恢复默认。")
        self.selector = QComboBox(objectName="screenGlyphSelector")
        self.selector.setAccessibleName(translate_ui_text("选择图标"))
        self.selector.currentIndexChanged.connect(self._select)
        layout.addWidget(self.selector)
        self.readonly_notice = label("该图标用于系统警告或错误反馈，仅可查看，不能修改。")
        self.readonly_notice.hide()
        self.home_notice = label("如首页已设置图片，需先恢复首页默认图标，才能看到此模式标志。")
        self.home_notice.hide()
        label("当前图标的实际点阵尺寸（宽 × 高）")
        self.dimensions = label("连接设备并选择图标后显示", "screenGlyphDimensions")
        self.device_label = label("设备当前图标：尚未读取")
        self.device_preview = QLabel(objectName="screenGlyphDevicePreview")
        self.device_preview.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.device_preview)
        label("转换后预览 · 尚未写入设备")
        self.preview = QLabel(objectName="screenGlyphPreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        self.filename = label("尚未选择图片", "screenGlyphFilename")
        label("素材要求", "screenGlyphRequirementsTitle")
        label("静态 PNG、JPG/JPEG · 最大 10 MiB · 总像素不超过 1600 万", "screenGlyphFormats")
        label("不支持 GIF、APNG 动图、SVG 或视频。", "screenGlyphUnsupportedFormats")
        label("推荐透明背景、轮廓清晰的简单图形。照片、细小文字和复杂渐变缩小后可能难以辨认。", "screenGlyphRecommendation")
        label("等比例缩放为单色点阵，不保留原图颜色；保留设备原有位置、文字和状态颜色。", "screenGlyphConversion")
        label("白色显示，黑色或透明区域隐藏；黑色图案可勾选下方“反色”。", "screenGlyphContrast")
        self.import_button = QPushButton(objectName="screenGlyphImport")
        set_translatable_text(self.import_button, "导入图片…")
        self.import_button.clicked.connect(self._choose_image)
        layout.addWidget(self.import_button)
        self.material_review = IconMaterialReview()
        self.material_review.changed.connect(self._sync_transfer)
        self.material_review.check.accepted.connect(self._accept_checked_image)
        layout.addWidget(self.material_review)
        self.empty_notice = label("转换后没有可见图案，请尝试“反色”或更换素材。", "screenGlyphEmptyNotice")
        self.empty_notice.hide()
        self.invert = QCheckBox(objectName="screenGlyphInvert")
        set_translatable_text(self.invert, "反色（将黑色图形转为亮色）")
        self.invert.toggled.connect(self._invert)
        layout.addWidget(self.invert)
        self.discard = QPushButton(objectName="screenGlyphDiscard")
        self.write_button = QPushButton(objectName="screenGlyphWrite")
        self.reset_button = QPushButton(objectName="screenGlyphReset")
        self.refresh_button = QPushButton(objectName="screenGlyphRefresh")
        for index, (widget, text, action) in enumerate((
            (self.discard, "丢弃本地图标", self.discard_candidate),
            (self.refresh_button, "读取设备图标", self.refresh_requested.emit),
            (self.write_button, "写入当前图标", self._confirm_write),
            (self.reset_button, "本项恢复默认", self._confirm_reset),
        )):
            if index % 2 == 0:
                row = QHBoxLayout()
                layout.addLayout(row)
            set_translatable_text(widget, text)
            widget.clicked.connect(action)
            row.addWidget(widget)
        label("写入或恢复默认只影响当前图标，不改变其他图标、按键映射或提示词。", "screenGlyphScope")
        self.status = label("请连接支持单项图标自定义的设备。", "screenGlyphStatus")
        label("本地图标只在本次运行中保留，不包含在配置方案或配置导出中。")
        self._sync_transfer()

    @property
    def draft_key(self) -> tuple[str, str, str] | None:
        t = self._transfer
        if t is None or t.device_key is None or t.selected_id is None:
            return None
        return (*t.device_key, t.selected_id)

    @property
    def draft(self) -> GlyphDraft | None:
        key = self.draft_key
        return self.drafts.get(key) if key else None

    def bind_transfer(self, transfer) -> None:
        if self._transfer is not None:
            self._transfer.changed.disconnect(self._sync_transfer)
        self._transfer = transfer
        # A completed operation from before this widget existed must not clear
        # a freshly imported matching image when the page is rebuilt.
        self._last_verified_write = transfer.verified_write
        transfer.changed.connect(self._sync_transfer)
        self._sync_transfer()

    def _entry(self) -> dict | None:
        t = self._transfer
        return next((entry for entry in t.catalog if entry["id"] == t.selected_id), None) if t else None

    def _sync_transfer(self) -> None:
        if self.material_review.check.busy and (
            self._review_context != self._import_context() or self._transfer and self._transfer.busy
        ):
            self.material_review.cancel()
        t = self._transfer
        catalog = t.catalog if t else []
        self.selector.blockSignals(True)
        self.selector.clear()
        for entry in catalog:
            self.selector.addItem(translate_ui_text(GLYPH_NAMES.get(entry["id"], entry["id"])), entry["id"])
        self.selector.setCurrentIndex(self.selector.findData(t.selected_id) if t else -1)
        self.selector.blockSignals(False)
        entry, draft = self._entry(), self.draft
        set_translatable_text(self.dimensions,
            f'{entry["width"]} × {entry["height"]}'
            if entry and t.supported and t.device_key else "连接设备并选择图标后显示")
        self.payload = (render_glyph(draft.image, entry["width"], entry["height"], invert=draft.invert)
                        if draft and draft.is_dirty and entry else None)
        verified = t.verified_write if t else None
        if verified is not None and verified != self._last_verified_write and self.draft_key:
            saved = self.drafts.get((*t.device_key, verified[0]))
            saved_entry = next((item for item in catalog if item["id"] == verified[0]), None)
            if saved and saved.is_dirty and saved_entry and render_glyph(
                saved.image, saved_entry["width"], saved_entry["height"], invert=saved.invert
            ) == verified[1]:
                saved.clear()
                if saved is draft:
                    self.payload = None
        self._last_verified_write = verified
        self.invert.blockSignals(True)
        self.invert.setChecked(bool(draft and draft.invert))
        self.invert.blockSignals(False)
        self.filename.setText(draft.filename if draft and draft.is_dirty else translate_ui_text("尚未选择图片"))
        self.preview.clear()
        if self.payload is not None:
            self.preview.setPixmap(QPixmap.fromImage(glyph_preview(self.payload, entry["width"], entry["height"])))
        else:
            set_translatable_text(self.preview, "导入后在这里查看单色点阵效果")
        self.device_preview.clear()
        metadata = t.metadata if t else None
        if entry and metadata and metadata.get("id") == entry["id"] and t.pixels is not None:
            self.device_preview.setPixmap(QPixmap.fromImage(glyph_preview(t.pixels, entry["width"], entry["height"])))
        set_translatable_text(self.device_label, "设备当前图标：内置默认" if metadata and metadata.get("source") == "default"
                              else "设备当前图标：自定义（已读回）" if metadata else "设备当前图标：尚未读取")
        supported = bool(t and t.supported and t.device_key)
        busy = bool(t and t.busy)
        editable = bool(entry and entry.get("editable"))
        ready = supported and not busy and editable and t.writable
        self.selector.setEnabled(supported and not busy and bool(catalog))
        self.readonly_notice.setVisible(bool(entry and not editable))
        self.home_notice.setVisible(bool(entry and entry["id"] == "normal_mode"))
        self.import_button.setEnabled(ready)
        self.invert.setEnabled(ready and self.payload is not None)
        self.discard.setEnabled(bool(draft and draft.is_dirty) and not busy)
        empty = self.payload is not None and not any(self.payload)
        self.empty_notice.setVisible(empty)
        self.write_button.setEnabled(ready and self.payload is not None and not empty and not self.material_review.check.busy)
        self.reset_button.setEnabled(ready and not self.material_review.check.busy)
        self.refresh_button.setEnabled(supported and not busy)
        set_translatable_text(self.status, t.status if t else "请连接支持单项图标自定义的设备。")

    def _select(self, index: int) -> None:
        icon_id = self.selector.itemData(index)
        if icon_id is not None:
            self.selection_requested.emit(icon_id)

    def import_image(self, path: str) -> None:
        if self.draft_key is None or self._entry() is None:
            raise ValueError("Select a device glyph before importing.")
        if not self.import_button.isEnabled():
            raise ValueError("This glyph is not currently editable.")
        self.material_review.cancel()
        image = load_icon_image(path)
        self._apply_image(path, image)

    def _apply_image(self, path: str, image: QImage) -> None:
        self.drafts[self.draft_key] = GlyphDraft(image, Path(path).name)
        self._sync_transfer()

    def _import_context(self):
        return self.draft_key, self._transfer.connection_epoch if self._transfer else None

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
        path, _ = QFileDialog.getOpenFileName(self, translate_ui_text("导入图片…"), "", "PNG / JPEG (*.png *.jpg *.jpeg)")
        if path and context == self._import_context():
            self.review_image(path)

    def _invert(self, enabled: bool) -> None:
        if self.draft:
            self.draft.invert = enabled
            self._sync_transfer()

    def discard_candidate(self) -> None:
        self.material_review.cancel()
        if self.draft:
            self.draft.clear()
            self._sync_transfer()

    def _confirm(self, title: str, message: str) -> bool:
        t = self._transfer
        if t is None or self.draft_key is None:
            return False
        key, epoch = self.draft_key, t.connection_epoch
        result = QMessageBox.question(self, translate_ui_text(title), translate_ui_text(message),
                                      QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if result != QMessageBox.Yes:
            return False
        if key != self.draft_key or epoch != t.connection_epoch:
            QMessageBox.warning(self, translate_ui_text("设备连接已变化"), translate_ui_text(
                "设备或选中的图标已变化，请重新确认后操作。"))
            return False
        entry = self._entry()
        return bool(t.supported and t.writable and not t.busy and entry and entry["editable"])

    def _confirm_write(self) -> None:
        pixels = self.payload
        if pixels is not None and self.write_button.isEnabled() and self._confirm(
            "写入当前图标", "只替换当前选中的图标，其他图标和设置保持不变。是否继续？"
        ):
            self.write_requested.emit(pixels)

    def _confirm_reset(self) -> None:
        if self.reset_button.isEnabled() and self._confirm(
            "本项恢复默认", "只恢复当前选中的内置图标，其他自定义图标和设置保持不变。是否继续？"
        ):
            self.reset_requested.emit()
