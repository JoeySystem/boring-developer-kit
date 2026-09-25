from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QButtonGroup,
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
        self.identity.hide()
        layout.addWidget(self.identity)
        self.name_input = QLineEdit(objectName="bleNameInput")
        # The protocol limit is UTF-8 bytes. Never truncate QLineEdit to 24 characters.
        self.name_input.setMaxLength(32767)
        self.name_input.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        layout.addWidget(self.name_input)
        self.counter = QLabel(objectName="bleNameByteCount")
        self.counter.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.counter.hide()
        layout.addWidget(self.counter)
        self.validation = QLabel(objectName="bleNameValidation")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        form = QFormLayout()
        self.saved = QLabel(objectName="bleNameSaved")
        self.active = QLabel(objectName="bleNameActive")
        self._name_value_rows: list[tuple[QLabel, QLabel]] = []
        for source, value in (("已保存名称", self.saved), ("本次运行名称", self.active)):
            caption = QLabel()
            set_translatable_text(caption, source)
            value.setWordWrap(True)
            value.setTextFormat(Qt.PlainText)
            value.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            form.addRow(caption, value)
            self._name_value_rows.append((caption, value))
        layout.addLayout(form)
        self.restart = QLabel(objectName="bleNameRestart")
        self.restart.setWordWrap(True)
        layout.addWidget(self.restart)
        self.reason = QLabel(objectName="bleNameBlockReason")
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.message = QLabel(objectName="bleNameMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.name_help_toggle = QPushButton(objectName="bleNameHelpToggle")
        self.name_help_toggle.setProperty("buttonRole", "secondary")
        self.name_help_toggle.setCheckable(True)
        set_translatable_text(self.name_help_toggle, "电脑仍显示旧名称？")
        layout.addWidget(self.name_help_toggle)
        self.name_help = QLabel(objectName="bleNameHelp")
        self.name_help.setWordWrap(True)
        set_translatable_text(self.name_help, "先确认新名称已生效，再重新打开电脑的蓝牙设置。Mac 请退出并重新打开“系统设置”。旧条目不代表设备仍在线，无需为改名删除配对。")
        self.name_help.hide()
        self.name_help_toggle.toggled.connect(self.name_help.setVisible)
        layout.addWidget(self.name_help)
        self.technical = QLabel(objectName="bleNameTechnical")
        self.technical.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self.technical.setTextFormat(Qt.PlainText)
        self.technical.setWordWrap(True)
        self.technical.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.technical_toggle = QPushButton(objectName="bleNameDetailsToggle")
        set_translatable_text(self.technical_toggle, "更多信息")
        self.technical_toggle.setCheckable(True)
        self.technical_toggle.toggled.connect(self.technical.setVisible)
        layout.addWidget(self.technical_toggle)
        layout.addWidget(self.technical)
        actions = QHBoxLayout()
        self.save_button = QPushButton(objectName="saveBleName")
        self.default_button = QPushButton(objectName="restoreBleName")
        self.save_button.setProperty("buttonRole", "primary")
        self.default_button.setProperty("buttonRole", "secondary")
        self.save_button.clicked.connect(self._primary_action)
        self.default_button.clicked.connect(
            lambda _checked=False: self._invoke("restore_default")
        )
        actions.addWidget(self.save_button, 1)
        actions.addWidget(self.default_button)
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

    def _primary_action(self) -> None:
        self._invoke("read" if self._session.result is None else "save")

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
        restart_required = bool(result.get("restart_required"))
        for caption, value in self._name_value_rows:
            caption.setVisible(restart_required)
            value.setVisible(restart_required)
        set_translatable_text(self.restart, (
            "请将设备关机再开机；重新连接后，控制台会自动确认新名称是否生效。"
            if restart_required
            else ""
        ))
        self.restart.setVisible(bool(self.restart.text()))
        reason = self._vm.ble_name_write_block_reason()
        set_translatable_text(self.reason, reason)
        self.reason.setVisible(bool(reason))
        message = self._local_error or session.message
        if message == reason:
            message = ""
        set_translatable_text(self.message, message)
        self.message.setVisible(bool(self.message.text()))
        self.name_help_toggle.setVisible(bool(result) and session.supported)
        self.name_help.setVisible(
            bool(result) and session.supported and self.name_help_toggle.isChecked()
        )
        self.technical.setText(session.technical)
        self.technical_toggle.setVisible(bool(session.technical))
        self.technical.setVisible(bool(session.technical) and self.technical_toggle.isChecked())
        self.name_input.setEnabled(bool(session.serial) and session.supported and not session.busy)
        can_write = not reason and not session.busy and session.connected and session.supported
        can_read = not reason or reason == "设备当前只读，不能保存蓝牙名称"
        if result:
            set_translatable_text(self.save_button, "保存名称")
            self.save_button.setEnabled(can_write and session.dirty and not error)
        else:
            set_translatable_text(self.save_button, "重新读取名称")
            self.save_button.setEnabled(
                session.connected and session.supported and not session.busy and can_read
            )
        default_relevant = bool(result) and (
            result.get("saved_name") != result.get("default_name")
        )
        set_translatable_text(self.default_button, "恢复默认名称")
        self.default_button.setVisible(default_relevant)
        self.default_button.setEnabled(can_write and default_relevant)


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
        # Keep confirmation/cancel visible instead of a detached native color panel.
        dialog.setOption(QColorDialog.DontUseNativeDialog, True)
        dialog.setStyleSheet("QColorDialog { background-color: #242522; }")
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
            "QPushButton#rgbButton { "
            f"background: rgb({red}, {green}, {blue}); color: {text_color}; "
            f"padding: {0 if compact else 10}px; border: none; border-radius: {17 if compact else 14}px;"
            + (" min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; margin: 0;" if compact else "")
            + " }"
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
    screen_icon_editor_created = Signal(object)

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
                selected_level = self._lighting_brightness.value()
                maximum_level = len(self._lighting_levels) - 1
                user_percentage = (
                    round(selected_level / maximum_level * 100)
                    if lighting.get("enabled") and maximum_level > 0
                    else 0
                )
                gauge.set_usage(seven_day=user_percentage, five_hour=None)
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
            self._layout_workspace()
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
            rotation_button = self._display_rotation_group.checkedButton()
            display.update(
                brightness=self._display_brightness.value(),
                rotation=int(rotation_button.property("rotationValue")),
                show_control_hints=self._display_hints.isChecked(),
            )
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
            for index, value in enumerate(under_key_values[:under_key_count]):
                control_id = f"key.{index + 1}"
                if (
                    control_id not in self._under_key_control_ids
                    or control_id in self._agent_status_control_ids
                ):
                    continue
                # The device model is the visible selector. Keep the existing
                # RGB button as the dialog/value controller so the write and
                # readback paths remain unchanged, but do not render a second
                # row of six competing swatches in the side panel.
                button = RgbButton(
                    f"功能键 {index + 1}",
                    value,
                    group,
                    control_id=control_id,
                )
                button.setProperty("underKeyIndex", index)
                button.value_changed.connect(self._emit_lighting_changed)
                self._under_key_colors.append((index, button))
                button.hide()
            if self._agent_status_control_ids:
                layout.addWidget(QLabel("状态灯", objectName="eyebrow"))
                agent_note = QLabel(
                    "状态灯由 Codex 自动控制。",
                    objectName="statusKeyLightingNote",
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
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("屏幕", objectName="displayModuleTitle"))
        form = QFormLayout()
        form.setVerticalSpacing(10)
        layout.addLayout(form)
        self._display_brightness = QSlider(
            Qt.Orientation.Horizontal,
            objectName="displayBrightness",
        )
        brightness_range = self._rules.display_brightness
        self._display_brightness.setRange(brightness_range.minimum, brightness_range.maximum)
        self._display_brightness.setSingleStep(1)
        self._display_brightness.setPageStep(10)
        self._display_brightness.setValue(_integer(self._display.get("brightness"), 0))
        set_translatable_accessible_name(self._display_brightness, "屏幕亮度")
        self._display_brightness_value = QLabel(objectName="displayBrightnessValue")
        self._display_brightness_value.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._display_brightness_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display_brightness_value.setText(f"{self._display_brightness.value()}%")
        self._display_brightness.valueChanged.connect(
            lambda value: self._display_brightness_value.setText(f"{value}%")
        )
        brightness_control = QWidget()
        brightness_control.setObjectName("displayBrightnessControl")
        brightness_layout = QHBoxLayout(brightness_control)
        brightness_layout.setContentsMargins(0, 0, 0, 0)
        brightness_layout.setSpacing(12)
        brightness_layout.addWidget(self._display_brightness, 1)
        brightness_layout.addWidget(self._display_brightness_value)
        form.addRow("亮度", brightness_control)
        rotation_control = QWidget(objectName="displayRotationControl")
        rotation_layout = QHBoxLayout(rotation_control)
        rotation_layout.setContentsMargins(4, 4, 4, 4)
        rotation_layout.setSpacing(2)
        self._display_rotation_group = QButtonGroup(self)
        self._display_rotation_group.setObjectName("displayRotation")
        self._display_rotation_group.setExclusive(True)
        initial_rotation = _integer(
            self._display.get("rotation"),
            self._rules.display_rotations[0],
        )
        for value in self._rules.display_rotations:
            button = QPushButton(f"{value}°", objectName="displayRotationOption")
            button.setCheckable(True)
            button.setProperty("rotationValue", value)
            button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            button.setChecked(value == initial_rotation)
            self._display_rotation_group.addButton(button)
            rotation_layout.addWidget(button, 1)
        if self._display_rotation_group.checkedButton() is None:
            self._display_rotation_group.buttons()[0].setChecked(True)
        form.addRow("画面旋转", rotation_control)
        self._display_hints = QCheckBox("显示控件提示", objectName="displayHints")
        self._display_hints.setChecked(self._display.get("show_control_hints") is True)
        form.addRow("提示", self._display_hints)
        icon_options = QWidget(objectName="screenIconOptions")
        icon_layout = QVBoxLayout(icon_options)
        icon_layout.setContentsMargins(0, 8, 0, 0)
        def ensure_icon_editor(expanded: bool) -> None:
            if expanded and icon_options.findChild(ScreenIconEditor) is None:
                icon_editor = ScreenIconEditor(
                    self._screen_icon_draft,
                    device_supported=self._features.get("custom_home_icon") is True,
                )
                icon_layout.addWidget(icon_editor)
                self.screen_icon_editor_created.emit(icon_editor)
        expand_icons = QPushButton(objectName="expandScreenIcons")
        expand_icons.setCheckable(True)
        expand_icons.setProperty("buttonRole", "secondary")
        set_translatable_text(expand_icons, "自定义屏幕图标 ›")
        expand_icons.toggled.connect(lambda expanded: set_translatable_text(
            expand_icons,
            "自定义屏幕图标 ⌄" if expanded else "自定义屏幕图标 ›",
        ))
        expand_icons.toggled.connect(ensure_icon_editor)
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
