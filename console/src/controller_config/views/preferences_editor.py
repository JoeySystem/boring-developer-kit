from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QScrollArea,
    QFrame,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from controller_config.protocol.contract import ConfigEditorRules, validate_ble_name
from controller_config.i18n import (
    SKIP_TRANSLATION_PROPERTY,
    set_translatable_accessible_name,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.views.v4_widgets import V4Card, UsageRings
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor


class BleNameEditor(QWidget):
    """Edit the connected device's independent preference, not its Profile."""

    def __init__(self, view_model, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bleNameEditor")
        self._vm = view_model
        self._session = view_model.ble_name
        self._local_error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        title = QLabel(objectName="inspectorTitle")
        set_translatable_text(title, "蓝牙名称")
        layout.addWidget(title)
        self.identity = QLabel(objectName="bleNameSerial")
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        layout.addWidget(self.identity)
        self.name_input = QLineEdit(objectName="bleNameInput")
        # The protocol limit is UTF-8 bytes. Never truncate QLineEdit to 24 characters.
        self.name_input.setMaxLength(32767)
        self.name_input.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        layout.addWidget(self.name_input)
        self.counter = QLabel(objectName="bleNameByteCount")
        self.counter.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        help_toggle = QPushButton(objectName="bleNameHelpToggle")
        set_translatable_text(help_toggle, "名称长度说明")
        help_toggle.setCheckable(True)
        layout.addWidget(help_toggle)
        self.counter.hide()
        help_toggle.toggled.connect(self.counter.setVisible)
        layout.addWidget(self.counter)
        rules = QLabel(objectName="muted")
        set_translatable_text(rules, "按 UTF-8 字节计数；中文通常占 3 字节。名称不能为空，不能有首尾空白、换行或控制字符。")
        rules.setWordWrap(True)
        rules.hide()
        help_toggle.toggled.connect(rules.setVisible)
        layout.addWidget(rules)
        self.validation = QLabel(objectName="bleNameValidation")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        form = QFormLayout()
        self.saved = QLabel(objectName="bleNameSaved")
        self.active = QLabel(objectName="bleNameActive")
        for source, value in (("已保存名称", self.saved), ("本次运行名称", self.active)):
            caption = QLabel()
            set_translatable_text(caption, source)
            value.setWordWrap(True)
            value.setTextFormat(Qt.PlainText)
            value.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            form.addRow(caption, value)
        layout.addLayout(form)
        self.restart = QLabel(objectName="bleNameRestart")
        self.restart.setWordWrap(True)
        layout.addWidget(self.restart)
        explanation = QLabel(objectName="muted")
        set_translatable_text(explanation, "保存不会重启或断开设备；新名称在下次正常重启后生效。仅断连重连不保证生效，系统蓝牙列表也可能暂时缓存旧名称。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.reason = QLabel(objectName="bleNameBlockReason")
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.message = QLabel(objectName="bleNameMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.technical = QLabel(objectName="bleNameTechnical")
        self.technical.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.technical.setTextFormat(Qt.PlainText)
        self.technical.setWordWrap(True)
        self.technical.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.technical_toggle = QPushButton(objectName="bleNameDetailsToggle")
        set_translatable_text(self.technical_toggle, "技术详情")
        self.technical_toggle.setCheckable(True)
        self.technical_toggle.toggled.connect(self.technical.setVisible)
        layout.addWidget(self.technical_toggle)
        layout.addWidget(self.technical)
        actions = QHBoxLayout()
        self.save_button = QPushButton(objectName="saveBleName")
        self.default_button = QPushButton(objectName="restoreBleName")
        self.read_button = QPushButton(objectName="readBleName")
        for button, source, operation in (
            (self.save_button, "保存名称", "save"),
            (self.default_button, "恢复默认名称", "restore_default"),
            (self.read_button, "重新读取名称", "read"),
        ):
            set_translatable_text(button, source)
            button.clicked.connect(lambda _checked=False, method=operation: self._invoke(method))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.name_input.textEdited.connect(self._edit)
        self._session.changed.connect(self._session_changed)
        self._vm.changed.connect(self.refresh)
        self.refresh()

    def _session_changed(self) -> None:
        self._local_error = ""
        self.refresh()

    def _edit(self, text: str) -> None:
        self._local_error = ""
        self._session.edit(text)

    def _invoke(self, operation: str) -> None:
        self._local_error = ""
        try:
            getattr(self._session, operation)()
        except ValueError as exc:
            self._local_error = str(exc)
        self.refresh()

    def refresh(self, *_args) -> None:
        session = self._session
        if self.name_input.text() != session.draft:
            self.name_input.setText(session.draft)
        self.identity.setText(session.serial or "—")
        error = ""
        try:
            validate_ble_name(session.draft, session.max_bytes)
            byte_count = len(session.draft.encode("utf-8"))
        except ValueError as exc:
            error = str(exc)
            byte_count = len(session.draft.encode("utf-8", errors="replace"))
        self.counter.setText(f"{byte_count} / {session.max_bytes} UTF-8 bytes")
        set_translatable_text(self.validation, error if session.serial and session.supported
                              and (session.result or session.draft) else "")
        self.validation.setVisible(bool(self.validation.text()))
        result = session.result or {}
        self.saved.setText(str(result.get("saved_name", "—")))
        self.active.setText(str(result.get("active_name", "—")))
        set_translatable_text(self.restart, (
            "已保存，下次正常重启生效" if result.get("restart_required")
            else "已保存名称与本次运行名称一致" if result else "尚未读取蓝牙名称"
        ))
        reason = self._vm.ble_name_write_block_reason()
        set_translatable_text(self.reason, reason)
        self.reason.setVisible(bool(reason))
        set_translatable_text(self.message, self._local_error or session.message)
        self.message.setVisible(bool(self.message.text()))
        self.technical.setText(session.technical)
        self.technical_toggle.setVisible(bool(session.technical))
        self.technical.setVisible(bool(session.technical) and self.technical_toggle.isChecked())
        self.name_input.setEnabled(bool(session.serial) and session.supported and not session.busy)
        can_write = not reason and not session.busy and session.connected and session.supported
        self.save_button.setEnabled(can_write and session.dirty and not error)
        self.default_button.setEnabled(can_write and bool(result) and (
            result.get("saved_name") != result.get("default_name")
            or session.draft != result.get("default_name")
        ))
        can_read = not reason or reason == "设备当前只读，不能保存蓝牙名称"
        self.read_button.setEnabled(session.connected and session.supported and not session.busy and can_read)


class RgbButton(QPushButton):
    value_changed = Signal(object)

    def __init__(
        self,
        label: str,
        value: dict[str, Any],
        parent: QWidget | None = None,
        *,
        control_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._rgb = _rgb_tuple(value)
        self._dialog: QColorDialog | None = None
        self._dialog_original = self._rgb
        self.setObjectName("rgbButton")
        if control_id is not None:
            self.setProperty("controlId", control_id)
        self.clicked.connect(self._choose_color)
        self._refresh()

    def value(self) -> dict[str, int]:
        red, green, blue = self._rgb
        return {"r": red, "g": green, "b": blue}

    def set_value(self, value: dict[str, Any]) -> None:
        self._rgb = _rgb_tuple(value)
        self._refresh()

    def _choose_color(self) -> None:
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._dialog_original = self._rgb
        dialog = QColorDialog(QColor(*self._rgb), self)
        dialog.setWindowTitle(translate_ui_text(self._label))
        dialog.currentColorChanged.connect(self._preview_color)
        dialog.rejected.connect(self._restore_dialog_color)
        dialog.finished.connect(self._finish_color_dialog)
        self._dialog = dialog
        dialog.open()

    def _preview_color(self, color: QColor) -> None:
        if not color.isValid():
            return
        self._set_rgb((color.red(), color.green(), color.blue()), emit=True)

    def _restore_dialog_color(self) -> None:
        self._set_rgb(self._dialog_original, emit=True)

    def _finish_color_dialog(self, _result: int) -> None:
        dialog = self._dialog
        self._dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def _set_rgb(self, value: tuple[int, int, int], *, emit: bool) -> None:
        if value == self._rgb:
            return
        self._rgb = value
        self._refresh()
        if emit:
            self.value_changed.emit(self.value())

    def _refresh(self) -> None:
        red, green, blue = self._rgb
        text_color = "#ffffff" if red * 299 + green * 587 + blue * 114 < 128000 else "#202324"
        compact = self.property("compactSwatch") is True
        set_translatable_text(self, "" if compact else f"{self._label}\n{red}, {green}, {blue}")
        self.setToolTip(f"{translate_ui_text(self._label)} · RGB {red}, {green}, {blue}")
        set_translatable_accessible_name(
            self, f"{self._label}：{red}, {green}, {blue}"
        )
        self.setStyleSheet(
            f"background: rgb({red}, {green}, {blue}); color: {text_color}; "
            f"padding: {0 if compact else 10}px; border: none; border-radius: {17 if compact else 14}px;"
            + (" min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; margin: 0;" if compact else "")
        )


class LightingLevelScale(QWidget):
    """Keep the visible level marks centred under the slider's real snap points."""

    _MARK_WIDTH = 60
    _SLIDER_HEIGHT = 28
    _MARKS_TOP = 31

    def __init__(
        self,
        slider: QSlider,
        level_names: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("lightingLevelScale")
        self.setMinimumWidth(300)
        self.setMaximumWidth(340)
        self.setFixedHeight(64)
        self._slider = slider
        self._slider.setParent(self)
        self._marks: list[QWidget] = []
        for index, name in enumerate(level_names):
            mark = QWidget(self, objectName="lightingLevelMark")
            mark.setProperty("levelValue", index)
            mark.setFixedWidth(self._MARK_WIDTH)
            mark_layout = QVBoxLayout(mark)
            mark_layout.setContentsMargins(0, 0, 0, 0)
            mark_layout.setSpacing(0)
            number = QLabel(str(index), objectName="lightingLevelMarkNumber")
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark_layout.addWidget(number)
            semantic = QLabel(objectName="lightingLevelMarkName")
            semantic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            set_translatable_text(semantic, name)
            mark_layout.addWidget(semantic)
            self._marks.append(mark)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_scale()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._layout_scale()

    def _layout_scale(self) -> None:
        if not self._marks or self.width() <= 0:
            return
        option = QStyleOptionSlider()
        self._slider.initStyleOption(option)
        option.sliderPosition = self._slider.minimum()
        option.sliderValue = self._slider.minimum()
        first_handle = self._slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self._slider,
        )
        side_inset = max(0, self._MARK_WIDTH // 2 - first_handle.center().x())
        self._slider.setGeometry(
            side_inset,
            0,
            max(0, self.width() - side_inset * 2),
            self._SLIDER_HEIGHT,
        )

        for value, mark in enumerate(self._marks):
            option = QStyleOptionSlider()
            self._slider.initStyleOption(option)
            option.sliderPosition = value
            option.sliderValue = value
            handle = self._slider.style().subControlRect(
                QStyle.ComplexControl.CC_Slider,
                option,
                QStyle.SubControl.SC_SliderHandle,
                self._slider,
            )
            center_x = self._slider.x() + handle.center().x()
            mark.setGeometry(
                center_x - self._MARK_WIDTH // 2,
                self._MARKS_TOP,
                self._MARK_WIDTH,
                self.height() - self._MARKS_TOP,
            )


class PreferencesEditor(QWidget):
    lighting_changed = Signal(object)

    def __init__(
        self,
        *,
        lighting: dict[str, Any],
        haptic: dict[str, Any],
        display: dict[str, Any],
        features: dict[str, Any],
        under_key_control_ids: tuple[str, ...],
        agent_status_control_ids: frozenset[str],
        rules: ConfigEditorRules,
        lighting_levels: tuple[int, ...] | None = None,
        haptic_levels: tuple[int, ...] | None = None,
        display_brightness_configurable: bool = True,
        screen_icon_draft: ScreenIconDraft | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lighting = copy.deepcopy(lighting)
        self._haptic = copy.deepcopy(haptic)
        self._display = copy.deepcopy(display)
        self._features = dict(features)
        self._screen_icon_draft = screen_icon_draft if screen_icon_draft is not None else ScreenIconDraft()
        self._under_key_control_ids = frozenset(under_key_control_ids)
        self._agent_status_control_ids = frozenset(agent_status_control_ids)
        self._rules = rules
        self._haptic_levels = haptic_levels
        self._haptic_level_strength = _integer(self._haptic.get("strength"), 0)
        self._display_brightness_configurable = display_brightness_configurable
        lighting_range = rules.lighting_brightness
        if lighting_levels is not None:
            if (
                len(lighting_levels) < 2
                or tuple(sorted(set(lighting_levels))) != lighting_levels
                or lighting_levels[0] != 0
                or lighting_levels[0] < lighting_range.minimum
                or lighting_levels[-1] > lighting_range.maximum
            ):
                raise ValueError("lighting_levels must be ordered unique Schema values")
        self._lighting_levels = lighting_levels
        self._original_lighting_brightness = _integer(
            self._lighting.get("brightness"), lighting_range.minimum
        )
        self._original_lighting_enabled = self._lighting.get("enabled") is True
        self._lighting_level_is_standard = (
            lighting_levels is None
            or not self._original_lighting_enabled
            or self._original_lighting_brightness in lighting_levels[1:]
        )
        self._lighting_level_touched = False
        self._lighting_level_note: QLabel | None = None
        self._lighting_level_value: QLabel | None = None
        self._status_colors: list[RgbButton] = []
        self._under_key_colors: list[tuple[int, RgbButton]] = []
        self._cards_layout = QGridLayout(self)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(12)
        self._light_card = self._lighting_group()
        self._cards_layout.addWidget(self._light_card, 0, 0, 2, 1)
        self._haptic_card = None
        self._screen_card = None
        if features.get("haptic") is True:
            self._haptic_card = self._haptic_group()
            self._cards_layout.addWidget(self._haptic_card, 0, 1)
        if features.get("display") is True:
            self._screen_card = self._display_group()
            self._cards_layout.addWidget(self._screen_card, 1, 1)
        self._cards_layout.setColumnStretch(0, 3)
        self._cards_layout.setColumnStretch(1, 2)
        self._compact_cards = False
        self._workspace_columns = None

    def set_workspace(self, preview: QWidget, preview_controls: QWidget, sync: QWidget) -> None:
        """Keep the same editors alive while arranging the lighting workbench."""
        self._workspace_columns = []
        if self._lighting_levels is not None:
            level_control = self.findChild(QWidget, "lightingLevelControl")
            self.findChild(LightingLevelScale).hide()
            self._lighting_level_value.hide()
            level_row = QHBoxLayout()
            gauge = UsageRings()
            gauge.setObjectName("lightingLevelGauge")
            gauge.setMinimumSize(84, 84)
            gauge.setMaximumSize(92, 92)
            gauge.set_labels("LIGHT", "")
            level_row.addWidget(gauge)
            segments = QHBoxLayout()
            segments.setSpacing(2)
            buttons = []
            for index, name in enumerate(("关", "低", "中", "高", "最高")):
                button = QPushButton(name, objectName="lightingLevelSegment")
                button.setCheckable(True)
                button.setProperty("levelValue", index)
                button.setMinimumWidth(0)
                button.setStyleSheet("QPushButton { padding: 0 4px; min-width: 24px; min-height: 32px; border: none; border-radius: 16px; } QPushButton:checked { background: #EFEAE0; color: #242320; }")
                def select(_checked=False, value=index):
                    previous = self._lighting_brightness.value()
                    self._lighting_brightness.setValue(value)
                    if previous == value:
                        self._use_selected_lighting_level()
                button.clicked.connect(select)
                buttons.append(button)
                segments.addWidget(button)
            level_row.addLayout(segments, 1)
            level_control.layout().insertLayout(0, level_row)
            def refresh(lighting):
                gauge.set_usage(seven_day=lighting.get("brightness", 0) if lighting.get("enabled") else 0,
                                five_hour=None)
                for index, button in enumerate(buttons):
                    button.setChecked(index == self._lighting_brightness.value() and
                                      (self._lighting_level_touched or self._lighting_level_is_standard))
            self.lighting_changed.connect(refresh)
            refresh(self.lighting_value())
        if self._haptic_card is not None:
            help_panel = QWidget()
            help_layout = QVBoxLayout(help_panel)
            help_layout.setContentsMargins(0, 0, 0, 0)
            for note in self._haptic_card.findChildren(QLabel, "hapticHelp"):
                self._haptic_card.layout().removeWidget(note)
                help_layout.addWidget(note)
            help_button = QPushButton("震动说明", objectName="expandHapticHelp")
            help_button.setCheckable(True)
            help_button.toggled.connect(help_panel.setVisible)
            self._haptic_card.layout().addWidget(help_button)
            self._haptic_card.layout().addWidget(help_panel)
            help_panel.hide()
        for card in (self._light_card, self._screen_card, self._haptic_card):
            if card is not None:
                self._cards_layout.removeWidget(card)
        left = QWidget(objectName="lightingLeftColumn")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(16)
        left_layout.addWidget(self._light_card)
        self._light_card.layout().addWidget(preview_controls)
        if self._screen_card is not None:
            left_layout.addWidget(self._screen_card)
        left_layout.addStretch(1)
        right = QWidget(objectName="lightingRightColumn")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(16)
        if self._haptic_card is not None:
            right_layout.addWidget(self._haptic_card)
        right_layout.addWidget(sync)
        right_layout.addStretch(1)
        for content, name in ((left, "lightingLeftScroll"), (right, "lightingRightScroll")):
            scroll = QScrollArea(objectName=name)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(content)
            self._workspace_columns.append(scroll)
        self._workspace_preview = preview
        self._compact_cards = None
        self.setMinimumHeight(600)
        self._layout_workspace()

    def _layout_workspace(self) -> None:
        compact = self.width() < 1080
        if compact == self._compact_cards:
            return
        self._compact_cards = compact
        left, right = self._workspace_columns
        for widget in (left, right, self._workspace_preview):
            self._cards_layout.removeWidget(widget)
        for col in range(3):
            self._cards_layout.setColumnStretch(col, 0)
        for row in range(3):
            self._cards_layout.setRowStretch(row, 0)
        if compact:
            self._cards_layout.addWidget(self._workspace_preview, 0, 0, 1, 2)
            self._cards_layout.addWidget(left, 1, 0)
            self._cards_layout.addWidget(right, 1, 1)
            left.setMinimumHeight(600)
            right.setMinimumHeight(600)
            self._cards_layout.setColumnStretch(0, 1)
            self._cards_layout.setColumnStretch(1, 1)
        else:
            left.setMinimumHeight(0)
            right.setMinimumHeight(0)
            self._cards_layout.addWidget(left, 0, 0)
            self._cards_layout.addWidget(self._workspace_preview, 0, 1)
            self._cards_layout.addWidget(right, 0, 2)
            for col, stretch in enumerate((3, 4, 3)):
                self._cards_layout.setColumnStretch(col, stretch)
            self._cards_layout.setRowStretch(0, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._workspace_columns is not None:
            # Wait for the parent layout to finish assigning this widget's size
            # before moving children and changing their minimum heights.
            QTimer.singleShot(0, self._layout_workspace)
            return
        compact = self.width() < 820
        if compact == self._compact_cards:
            return
        self._compact_cards = compact
        for card in (self._light_card, self._screen_card, self._haptic_card):
            if card is not None:
                self._cards_layout.removeWidget(card)
        self._cards_layout.addWidget(self._light_card, 0, 0, 1 if compact else 2, 1)
        if self._screen_card is not None:
            self._cards_layout.addWidget(self._screen_card, 1, 0 if compact else 1)
        if self._haptic_card is not None:
            if compact:
                self._cards_layout.addWidget(self._haptic_card, 2, 0)
            else:
                self._cards_layout.addWidget(self._haptic_card, 0, 1)
        self._cards_layout.setColumnStretch(1, 0 if compact else 2)

    def values(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        lighting = self.lighting_value()

        haptic = copy.deepcopy(self._haptic)
        if self._features.get("haptic") is True:
            haptic.update(
                enabled=self._haptic_enabled.isChecked(),
                strength=(
                    self._haptic_strength.value() if self._haptic_levels is None
                    else self._haptic_level_strength
                ),
                duration_ms=self._haptic_duration.value(),
                on_press=self._haptic_on_press.isChecked(),
            )
            if self._features.get("haptic_channels") is True:
                for field, checkbox in self._haptic_channels.items():
                    # Omitted optional switches mean true. Opening this page or
                    # editing another setting must not manufacture a draft.
                    if field in haptic or not checkbox.isChecked():
                        haptic[field] = checkbox.isChecked()
            else:
                haptic["on_profile"] = self._haptic_on_profile.isChecked()

        display = copy.deepcopy(self._display)
        if self._features.get("display") is True:
            display.update(
                rotation=int(self._display_rotation.currentData()),
                show_control_hints=self._display_hints.isChecked(),
            )
            if self._display_brightness_configurable:
                display["brightness"] = self._display_brightness.value()
            elif self._display_brightness.currentIndex() != self._initial_display_index:
                display["brightness"] = self._display_brightness.currentData()
        return lighting, haptic, display

    def lighting_value(self) -> dict[str, Any]:
        lighting = copy.deepcopy(self._lighting)
        if self._lighting_levels is None:
            lighting["enabled"] = self._lighting_enabled.isChecked()
            lighting["brightness"] = self._lighting_brightness.value()
        elif self._lighting_level_touched:
            selected_level = self._lighting_brightness.value()
            lighting["enabled"] = selected_level > 0
            if selected_level > 0:
                lighting["brightness"] = self._lighting_levels[selected_level]
        else:
            lighting["enabled"] = self._original_lighting_enabled
            lighting["brightness"] = self._original_lighting_brightness
        status = lighting.get("status")
        if isinstance(status, list):
            for index, button in enumerate(self._status_colors):
                status[index] = button.value()
        under_key = lighting.get("under_key")
        if isinstance(under_key, list):
            for index, button in self._under_key_colors:
                under_key[index] = button.value()
        return lighting

    def _emit_lighting_changed(self, *_args: object) -> None:
        self.lighting_changed.emit(self.lighting_value())

    def _use_selected_lighting_level(self, *_args: object) -> None:
        self._lighting_level_touched = True
        if self._lighting_level_note is not None:
            self._lighting_level_note.hide()
        self._refresh_lighting_level_value()
        self._emit_lighting_changed()

    def _refresh_lighting_level_value(self) -> None:
        if self._lighting_level_value is None:
            return
        if not self._lighting_level_touched and not self._lighting_level_is_standard:
            text = "自定义"
        else:
            text = _lighting_level_names(len(self._lighting_levels or ()))[
                self._lighting_brightness.value()
            ]
        set_translatable_text(self._lighting_level_value, text)

    def _lighting_group(self) -> QWidget:
        group = V4Card(role="primary", dots=True)
        group.setObjectName("lightingFocusCard")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(Boring5RLabel("LIGHT", color="#A5A195", scale=0.9))
        layout.addWidget(QLabel("灯光", objectName="lightingModuleTitle"))
        form = QFormLayout()
        if self._lighting_levels is None:
            self._lighting_enabled = QCheckBox(
                "启用灯光", objectName="lightingEnabled"
            )
            self._lighting_enabled.setChecked(self._original_lighting_enabled)
            self._lighting_enabled.toggled.connect(self._emit_lighting_changed)
            form.addRow("总开关", self._lighting_enabled)
            self._lighting_brightness = QSpinBox(objectName="lightingBrightness")
            brightness_range = self._rules.lighting_brightness
            self._lighting_brightness.setRange(
                brightness_range.minimum, brightness_range.maximum
            )
            self._lighting_brightness.setSuffix(" %")
            self._lighting_brightness.setValue(self._original_lighting_brightness)
            self._lighting_brightness.valueChanged.connect(
                self._emit_lighting_changed
            )
            form.addRow("全局亮度", self._lighting_brightness)
        else:
            level_control = QWidget(objectName="lightingLevelControl")
            level_control.setMinimumWidth(0)
            level_control.setMaximumWidth(410)
            level_layout = QVBoxLayout(level_control)
            level_layout.setContentsMargins(0, 0, 0, 0)
            level_layout.setSpacing(3)

            slider_row = QHBoxLayout()
            self._lighting_brightness = QSlider(
                Qt.Orientation.Horizontal,
                objectName="lightingBrightness",
            )
            self._lighting_brightness.setRange(0, len(self._lighting_levels) - 1)
            self._lighting_brightness.setSingleStep(1)
            self._lighting_brightness.setPageStep(1)
            self._lighting_brightness.setTickInterval(1)
            self._lighting_brightness.setTickPosition(
                QSlider.TickPosition.TicksBelow
            )
            self._lighting_brightness.setMinimumWidth(250)
            self._lighting_brightness.setMaximumWidth(340)
            initial_index = _initial_lighting_level_index(
                self._original_lighting_enabled,
                self._original_lighting_brightness,
                self._lighting_levels,
            )
            self._lighting_brightness.setValue(initial_index)
            set_translatable_accessible_name(
                self._lighting_brightness, "亮度档位"
            )
            level_scale = LightingLevelScale(
                self._lighting_brightness,
                _lighting_level_names(len(self._lighting_levels)),
            )
            level_scale.setMinimumWidth(210)
            slider_row.addWidget(level_scale, 1)
            self._lighting_level_value = QLabel(objectName="lightingLevelValue")
            self._lighting_level_value.setMinimumWidth(42)
            self._lighting_level_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            slider_row.addWidget(self._lighting_level_value)
            slider_row.setAlignment(
                self._lighting_level_value, Qt.AlignmentFlag.AlignTop
            )
            level_layout.addLayout(slider_row)

            if not self._lighting_level_is_standard:
                self._lighting_level_note = QLabel(
                    "当前旧配置值会保持不变；拖动后将使用设备的 0–4 档位。",
                    objectName="muted",
                )
                self._lighting_level_note.setWordWrap(True)
                level_layout.addWidget(self._lighting_level_note)

            self._refresh_lighting_level_value()
            self._lighting_brightness.valueChanged.connect(
                self._use_selected_lighting_level
            )
            form.addRow(QLabel("亮度档位"))
            form.addRow(level_control)
        layout.addLayout(form)

        status_count = _integer(self._features.get("status_rgb_count"), 0)
        under_key_count = _integer(self._features.get("under_key_rgb_count"), 0)
        status_values = self._lighting.get("status")
        if status_count > 0 and isinstance(status_values, list):
            layout.addWidget(QLabel("状态 RGB"))
            status_grid = QGridLayout()
            for index, value in enumerate(status_values[:status_count]):
                button = RgbButton(f"状态灯 {index + 1}", value)
                button.value_changed.connect(self._emit_lighting_changed)
                self._status_colors.append(button)
                status_grid.addWidget(button, index // 4, index % 4)
            layout.addLayout(status_grid)

        under_key_values = self._lighting.get("under_key")
        if under_key_count > 0 and isinstance(under_key_values, list):
            layout.addWidget(QLabel("WHITE KEYS · 白色动作键灯", objectName="eyebrow"))
            note = QLabel("每颗白色按键灯可以单独设置 RGB；设为 0, 0, 0 可关闭该键灯。")
            note.setWordWrap(True)
            layout.addWidget(note)
            colors = QGridLayout()
            for index, value in enumerate(under_key_values[:under_key_count]):
                control_id = f"key.{index + 1}"
                if (
                    control_id not in self._under_key_control_ids
                    or control_id in self._agent_status_control_ids
                ):
                    continue
                button = RgbButton(f"白色动作键 {index + 1}", value, control_id=control_id)
                button.setProperty("underKeyIndex", index)
                button.value_changed.connect(self._emit_lighting_changed)
                self._under_key_colors.append((index, button))
                visible_index = len(self._under_key_colors) - 1
                button.setProperty("compactSwatch", True)
                button.setFixedSize(34, 34)
                button._refresh()
                swatch = QWidget()
                swatch_layout = QVBoxLayout(swatch)
                swatch_layout.setContentsMargins(0, 0, 0, 0)
                swatch_layout.setSpacing(4)
                swatch_layout.addWidget(button, 0, Qt.AlignHCenter)
                number = QLabel(control_id.split(".")[-1], objectName="muted")
                number.setAlignment(Qt.AlignCenter)
                swatch_layout.addWidget(number)
                colors.addWidget(swatch, visible_index // 6, visible_index % 6)
            layout.addLayout(colors)
            if self._agent_status_control_ids:
                layout.addWidget(QLabel("AGENT KEYS · 透明状态键灯", objectName="eyebrow"))
                agent_note = QLabel(
                    "透明键灯由 Agent 状态语义接管，不在这里作为普通 RGB 灯编辑；现有配置值保持不变。"
                )
                agent_note.setWordWrap(True)
                layout.addWidget(agent_note)
        return group

    def _haptic_group(self) -> QWidget:
        group = V4Card(role="secondary")
        group.setObjectName("hapticSecondaryCard")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        layout.addWidget(Boring5RLabel("HAPTIC", color="#A5A195", scale=0.9))
        layout.addWidget(QLabel("震动", objectName="hapticModuleTitle"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        layout.addLayout(form)
        self._haptic_enabled = QCheckBox("启用震动", objectName="hapticEnabled")
        self._haptic_enabled.setChecked(self._haptic.get("enabled") is True)
        form.addRow("总开关", self._haptic_enabled)
        if self._haptic_levels is None:
            self._haptic_strength = QSpinBox(objectName="hapticStrength")
            strength_range = self._rules.haptic_strength
            self._haptic_strength.setRange(strength_range.minimum, strength_range.maximum)
            self._haptic_strength.setSuffix(" %")
            self._haptic_strength.setValue(self._haptic_level_strength)
            form.addRow("强度", self._slider_and_value(self._haptic_strength))
        else:
            self._haptic_strength = QComboBox(objectName="hapticStrength")
            for label, strength in zip(
                ("0 · 关闭震动", "1 · 低", "2 · 中", "3 · 高", "4 · 最高"),
                self._haptic_levels,
            ):
                self._haptic_strength.addItem(label, strength)
            self._haptic_strength.setPlaceholderText("旧配置保留，请选择档位")
            self._sync_haptic_level()
            self._haptic_strength.currentIndexChanged.connect(self._select_haptic_level)
            self._haptic_enabled.toggled.connect(self._sync_haptic_level)
            form.addRow("强度档位", self._haptic_strength)
        self._haptic_duration = QSpinBox(objectName="hapticDuration")
        duration_range = self._rules.haptic_duration_ms
        self._haptic_duration.setRange(duration_range.minimum, duration_range.maximum)
        self._haptic_duration.setSuffix(" ms")
        self._haptic_duration.setValue(
            _integer(self._haptic.get("duration_ms"), duration_range.minimum)
        )
        form.addRow("时长", self._slider_and_value(self._haptic_duration))
        channels_supported = self._features.get("haptic_channels") is True
        self._haptic_on_press = QCheckBox(
            "按键反馈" if channels_supported else "按键触发", objectName="hapticOnPress"
        )
        self._haptic_on_press.setChecked(self._haptic.get("on_press") is True)
        form.addRow(self._haptic_on_press)
        self._haptic_channels: dict[str, QCheckBox] = {}
        if channels_supported:
            for field, label, name in (
                ("on_encoder", "旋钮旋转反馈", "hapticOnEncoder"),
                ("on_joystick", "摇杆方向反馈", "hapticOnJoystick"),
                ("on_task", "任务／计时提醒", "hapticOnTask"),
            ):
                checkbox = QCheckBox(label, objectName=name)
                checkbox.setChecked(self._haptic.get(field, True) is True)
                self._haptic_channels[field] = checkbox
                form.addRow(checkbox)
            notes = (
                "适用于所有模式和配置方案。按下旋钮或摇杆属于按键反馈；任务／计时提醒包括 Codex、CC 和番茄钟。",
                "关闭振动不影响按键动作、灯光和屏幕。保存到设备并完成回读后生效，不是实时预览。",
                "设备自身的马达强度设置页会试振当前档位，即使总开关已关闭；选择 0 档停止。",
            )
        else:
            self._haptic_on_profile = QCheckBox("Profile 切换触发", objectName="hapticOnProfile")
            self._haptic_on_profile.setChecked(self._haptic.get("on_profile") is True)
            form.addRow("Profile", self._haptic_on_profile)
            notes = ("当前固件不支持分类振动开关，升级支持该功能的固件后可分别设置。",)
        for text in notes:
            note = QLabel(text, objectName="hapticHelp")
            note.setWordWrap(True)
            layout.addWidget(note)
        self._haptic_enabled.toggled.connect(self._refresh_haptic_enabled)
        self._refresh_haptic_enabled()
        layout.addStretch(1)
        return group

    def _select_haptic_level(self, index: int) -> None:
        if index < 0:
            return
        strength = self._haptic_strength.itemData(index)
        if strength > 0:
            self._haptic_level_strength = strength
        # As in the device menu, level zero disables output but retains strength.
        self._haptic_enabled.setChecked(strength > 0)

    def _sync_haptic_level(self, *_args: object) -> None:
        value = self._haptic_level_strength if self._haptic_enabled.isChecked() else 0
        was_blocked = self._haptic_strength.blockSignals(True)
        self._haptic_strength.setCurrentIndex(self._haptic_strength.findData(value))
        self._haptic_strength.blockSignals(was_blocked)

    def _refresh_haptic_enabled(self) -> None:
        enabled = self._haptic_enabled.isChecked()
        self._haptic_on_press.setEnabled(enabled)
        for checkbox in self._haptic_channels.values():
            checkbox.setEnabled(enabled)
        if self._features.get("haptic_channels") is not True:
            self._haptic_on_profile.setEnabled(enabled)

    @staticmethod
    def _slider_and_value(value: QSpinBox) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Orientation.Horizontal, objectName=f"{value.objectName()}Slider")
        slider.setRange(value.minimum(), value.maximum())
        slider.setValue(value.value())
        slider.setAccessibleName(value.objectName())
        slider.valueChanged.connect(value.setValue)
        value.valueChanged.connect(slider.setValue)
        value.setMaximumWidth(110)
        layout.addWidget(slider, 1)
        layout.addWidget(value)
        return row

    def _display_group(self) -> QWidget:
        group = V4Card(role="widget")
        group.setObjectName("screenWidgetCard")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(Boring5RLabel("SCREEN", color="#A5A195", scale=0.9))
        layout.addWidget(QLabel("屏幕", objectName="displayModuleTitle"))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        layout.addLayout(form)
        if self._display_brightness_configurable:
            self._display_brightness = QSpinBox(objectName="displayBrightness")
            brightness_range = self._rules.display_brightness
            self._display_brightness.setRange(brightness_range.minimum, brightness_range.maximum)
            self._display_brightness.setSuffix(" %")
            self._display_brightness.setValue(_integer(self._display.get("brightness"), 0))
            form.addRow("亮度", self._display_brightness)
        else:
            self._display_brightness = QComboBox(objectName="displayBrightness")
            self._display_brightness.addItem("关闭背光", 0)
            self._display_brightness.addItem("开启背光（固定亮度）", 100)
            self._initial_display_index = int(_integer(self._display.get("brightness"), 0) > 0)
            self._display_brightness.setCurrentIndex(self._initial_display_index)
            form.addRow("屏幕背光", self._display_brightness)
            note = QLabel("当前固件的屏幕背光为固定亮度，不支持百分比调节。", objectName="muted")
            note.setWordWrap(True)
            form.addRow(note)
        self._display_rotation = QComboBox(objectName="displayRotation")
        for value in self._rules.display_rotations:
            self._display_rotation.addItem(f"{value}°", value)
        self._display_rotation.setCurrentIndex(
            max(0, self._display_rotation.findData(self._display.get("rotation")))
        )
        form.addRow("画面旋转", self._display_rotation)
        self._display_hints = QCheckBox("显示控件提示", objectName="displayHints")
        self._display_hints.setChecked(self._display.get("show_control_hints") is True)
        form.addRow("提示", self._display_hints)
        icon_options = QWidget(objectName="screenIconOptions")
        icon_layout = QVBoxLayout(icon_options)
        icon_layout.setContentsMargins(0, 8, 0, 0)
        icon_layout.addWidget(ScreenIconEditor(
            self._screen_icon_draft,
            device_supported=self._features.get("custom_home_icon") is True,
        ))
        expand_icons = QPushButton("自定义屏幕图标", objectName="expandScreenIcons")
        expand_icons.setCheckable(True)
        expand_icons.toggled.connect(icon_options.setVisible)
        layout.addWidget(expand_icons)
        layout.addWidget(icon_options)
        icon_options.hide()
        return group


def _rgb_tuple(value: dict[str, Any]) -> tuple[int, int, int]:
    return (
        max(0, min(255, _integer(value.get("r"), 0))),
        max(0, min(255, _integer(value.get("g"), 0))),
        max(0, min(255, _integer(value.get("b"), 0))),
    )


def _integer(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _initial_lighting_level_index(
    enabled: bool,
    value: int,
    levels: tuple[int, ...],
) -> int:
    if not enabled or value <= 0:
        return 0
    if value in levels:
        return levels.index(value)
    return min(
        range(1, len(levels)),
        key=lambda index: abs(levels[index] - value),
    )


def _lighting_level_names(count: int) -> tuple[str, ...]:
    if count == 5:
        return ("关闭灯光", "低", "中", "高", "最高")
    return tuple(str(index) for index in range(count))
