from __future__ import annotations

import copy
from typing import Any

import shiboken6
from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QGraphicsDropShadowEffect

from controller_config.protocol.contract import ConfigEditorRules, validate_ble_name
from controller_config.i18n import (
    SKIP_TRANSLATION_PROPERTY,
    set_translatable_accessible_name,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor, icon_preview
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.page_motion import clear_grab
from controller_config.views.ui.tile_morph import TileMorph
from controller_config.views.settings_wall import (
    RADIUS as WALL_RADIUS,
    HOME_ICON_PREVIEW,
    RoundArrowButton,
    SettingsWall,
    TileIcon,
    WallTile,
    title_label,
)
from controller_config.views.ui import (
    LARGE,
    MEDIUM,
    ColorPresetRow,
    DeviceTopView,
    Dialog,
    DotMatrixSlider,
    SegmentedControl,
)


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


HAPTIC_HELP = (
    "适用于所有模式和配置方案。按下旋钮或摇杆属于按键反馈；任务／计时提醒包括 Codex、CC 和番茄钟。",
    "关闭振动不影响按键动作、灯光和屏幕。保存到设备并完成回读后生效，不是实时预览。",
    "设备自身的马达强度设置页会试振当前档位，即使总开关已关闭；选择 0 档停止。",
)
LEGACY_HAPTIC_HELP = ("当前固件不支持分类振动开关，升级支持该功能的固件后可分别设置。",)


def _row(*items: object, spacing: int = 8) -> QHBoxLayout:
    """One line of a tile: widgets, ints for fixed gaps, None for a stretch."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for item in items:
        if item is None:
            row.addStretch(1)
        elif isinstance(item, int):
            row.addSpacing(item)
        else:
            # Add first, then show: a widget shown before it has a parent
            # would open as its own window and hide again when re-parented.
            row.addWidget(item, 0, Qt.AlignmentFlag.AlignVCenter)
            item.show()
    return row


def _clear(layout) -> None:
    """Empty a tile layout without deleting its widgets (they are re-placed)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide()
        elif item.layout() is not None:
            _clear(item.layout())
            item.layout().setParent(None)


def _name(text: str, object_name: str = "lightingRowName") -> QLabel:
    label = QLabel(objectName=object_name)
    set_translatable_text(label, text)
    return label


def _tile_picture(tile: "FoldingTile") -> QPixmap:
    """A still of the tile with its controls. Its shadow effect would leave the controls
    out of the picture, so it comes off for the moment of the still."""
    if tile.graphicsEffect() is None:
        return clear_grab(tile, own_background=True)
    tile.setGraphicsEffect(None)
    try:
        return clear_grab(tile, own_background=True)
    finally:
        tile.restore_effect()


class FoldingTile(WallTile):
    """A one-cell tile that opens two cells wide over its neighbour (灯光页 v0.2).

    Its controls are created once as children of the tile; arrange(layout,
    expanded) puts the ones each look needs into the emptied layout.
    """

    def __init__(self, object_name: str) -> None:
        super().__init__(1, 1)
        self.setObjectName(object_name)
        self._arrange = None
        self.expanded = False

    def set_arrange(self, arrange) -> None:
        self._arrange = arrange
        self.rearrange()

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.span = (1, 2 if expanded else 1)
        self.setProperty("raised", expanded)
        self.restore_effect()
        self.rearrange()
        self.update()

    def restore_effect(self) -> None:
        """Open: a faint shadow under it (-08 答复第 3 件); closed: none."""
        if not self.expanded:
            self.setGraphicsEffect(None)
            return
        r, g, b, a = tokens.TILE_SHADOW
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(tokens.TILE_SHADOW_BLUR)
        shadow.setOffset(0, tokens.TILE_SHADOW_OFFSET)
        shadow.setColor(QColor(r, g, b, a))
        self.setGraphicsEffect(shadow)

    def rearrange(self) -> None:
        _clear(self.box)
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child.hide()
        self._arrange(self.box, self.expanded)


class PreferencesEditor(SettingsWall):
    """「灯光与反馈」: a 4 × 2 wall (the designer, 灯光页排法二 v0.4).

    Left 2 × 2: 按键灯光 (device picture, colours, brightness, live preview).
    Top right: 屏幕 and 震动, one cell each; each opens two cells wide over the
    other, one at a time. Bottom right 2 × 1: 屏幕图标, opening a dialog.
    The editor owns the controls and values(); the main window supplies the
    device picture, the live-preview switch and the save banner.
    """

    lighting_changed = Signal(object)
    screen_icon_editor_created = Signal(object)
    screen_glyphs_opened = Signal(object)

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
        super().__init__("appearance", parent)
        self.setObjectName("lightingWall")
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
        self._screen_icon_dialog = None
        self._open_tile: FoldingTile | None = None

        self._light_card = self._lighting_tile()
        self.place(self._light_card, 0, 0)
        self._show_lighting(self.lighting_value())
        if self._light_buttons:
            self._select_light_key(next(iter(self._light_buttons)))
        self._screen_card: FoldingTile | None = None
        self._haptic_card: FoldingTile | None = None
        self._icon_card: WallTile | None = None
        if features.get("display") is True:
            self._screen_card = FoldingTile("screenWidgetCard")
            self._build_display_controls(self._screen_card)
            self._screen_card.set_arrange(self._arrange_display)
            self.place(self._screen_card, 0, 2)
            self._icon_card = self._screen_icon_tile()
            self.place(self._icon_card, 1, 2)
        if features.get("haptic") is True:
            self._haptic_card = FoldingTile("hapticSecondaryCard")
            self._build_haptic_controls(self._haptic_card)
            self._haptic_card.set_arrange(self._arrange_haptic)
            self.place(self._haptic_card, 0, 3)

    # -- the wall ------------------------------------------------------------

    def set_workspace(self, preview_toggle: QWidget, preview_status: QWidget) -> None:
        """Put the main window's live-preview switch and its status line in 按键灯光."""
        self._preview_title.show()
        self._preview_toggle_slot.addWidget(preview_toggle)
        self._preview_status_slot.addWidget(preview_status)

    def set_device_connected(self, connected: bool) -> None:
        """Without a device the function keys show one dimmed colour (设备图 v0.5)."""
        self._device_view.set_connected(connected)

    def open_tile(self, tile: FoldingTile | None) -> None:
        """Open 屏幕 or 震动 over the other one, or close the open one (None).

        Opening widens it with a bounce and closing shrinks it back (-08 答复第 3 件).
        """
        if tile is self._open_tile:
            return
        running = getattr(self, "_tile_morph", None)
        if running is not None and shiboken6.isValid(running):
            running.finish()   # a click while one plays: the old overlay goes first, never two
        moving = tile if tile is not None else self._open_tile
        before = self._tile_before(moving, opening=tile is not None) if moving is not None else None
        self._open_tile_now(tile)
        if before is not None:
            self._morph_tile(moving, before, opening=tile is not None)

    # -- 方块变宽 (-08 答复第 3 件): a still before, a still after, an overlay between ---

    def _morph_span(self, host: QWidget) -> QRect:
        """The two cells 屏幕 and 震动 share, with room for the bounce and the shadow."""
        rects = [QRect(card.mapTo(host, QPoint(0, 0)), card.size())
                 for card in (self._screen_card, self._haptic_card) if card is not None and card.isVisible()]
        span = rects[0]
        for rect in rects[1:]:
            span = span.united(rect)
        margin = tokens.TILE_OPEN_OVERSHOOT + 30
        return span.adjusted(-margin, -20, margin, 40).intersected(host.rect())

    def _tile_before(self, tile: FoldingTile, *, opening: bool):
        if motion.reduces_motion() or not self.isVisible() or not tile.isVisible():
            return None
        host = self.window()
        rect = QRectF(QRect(tile.mapTo(host, QPoint(0, 0)), tile.size()))
        span = self._morph_span(host)
        # Nothing is grabbed under the overlay: on the glass window such a still would
        # carry the window's tint and darken the cells while it plays (真屏 11-22).
        return rect, _tile_picture(tile), span, None

    def _morph_tile(self, tile: FoldingTile, before, *, opening: bool) -> None:
        host = self.window()
        start, old, span, base = before
        # The controls the new look needs were just put into the tile: lay them out first.
        # Only layout events: flushing every posted event here would paint the opened
        # tile for one frame before the overlay exists (the flash at the start).
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        if tile.layout() is not None:
            tile.layout().activate()
        end = QRectF(QRect(tile.mapTo(host, QPoint(0, 0)), tile.size()))
        new = _tile_picture(tile)
        # Opening: the tile it covers stays shown under the overlay until covered.
        covered = None
        if opening:
            covered = self._haptic_card if tile is self._screen_card else self._screen_card
        self._tile_morph = TileMorph(host, base=base, base_at=span, start=start, end=end, old=old, new=new,
                                     opening=opening, radius=float(WALL_RADIUS), veiled=tile, reveal=covered)

    def _open_tile_now(self, tile: FoldingTile | None) -> None:
        previous = self._open_tile
        if previous is not None:
            previous.set_expanded(False)
            self.move(previous, 0, 2 if previous is self._screen_card else 3)
        for other in (self._screen_card, self._haptic_card):
            if other is not None:
                other.show()
        self._open_tile = tile
        app = QApplication.instance()
        if tile is None:
            app.removeEventFilter(self)
            return
        covered = self._haptic_card if tile is self._screen_card else self._screen_card
        if covered is not None:
            covered.hide()
        tile.set_expanded(True)
        self.move(tile, 0, 2)
        tile.raise_()
        if previous is None:
            app.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt virtual method
        tile = self._open_tile
        if tile is not None and tile.isVisible():
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                if (isinstance(watched, QWidget) and watched.window() is self.window()
                        and QApplication.activeModalWidget() is None):
                    self.open_tile(None)
                    return True
            elif event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                inside = watched is tile or tile.isAncestorOf(watched)
                popup = QApplication.activePopupWidget() is not None
                if not inside and not popup and watched.window() is self.window():
                    self.open_tile(None)
        return False

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.open_tile(None)
        super().hideEvent(event)

    # -- values --------------------------------------------------------------

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
                brightness=self._display_brightness.value(),
                rotation=int(self._display_rotation.value()),
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

    def _select_light_key(self, control_id: str) -> None:
        """A function key picked on the device picture: show its colour row."""
        button = self._light_buttons.get(control_id)
        if button is None:
            return
        self._device_view.set_selected(control_id)
        number = control_id.split(".")[-1]
        self._color_row.set_key(f"{translate_ui_text('键')} {number}", _rgb_tuple(button.value()))

    def _choose_light_color(self, rgb) -> None:
        """A preset (or 不亮, stored as black) for the picked key; shows at once."""
        button = self._light_buttons.get(self._device_view.selected())
        if button is not None:
            button._set_rgb(tuple(rgb) if rgb is not None else (0, 0, 0), emit=True)

    def _show_lighting(self, lighting: dict[str, Any]) -> None:
        """Paint the keys in their light colours at the chosen brightness."""
        colors = lighting.get("under_key", [])
        self._device_view.set_key_colors({
            control_id: _rgb_tuple(colors[int(control_id.split(".")[-1]) - 1])
            for control_id in self._light_buttons
            if int(control_id.split(".")[-1]) - 1 < len(colors)
        })
        self._device_view.set_brightness(self._brightness_fraction(lighting))

    def _brightness_fraction(self, lighting: dict[str, Any]) -> float:
        if not lighting.get("enabled"):
            return 0.0
        brightness = _integer(lighting.get("brightness"), 0)
        if self._lighting_levels is not None:
            return _lighting_level_fraction(brightness, self._lighting_levels)
        maximum = self._rules.lighting_brightness.maximum
        return brightness / maximum if maximum else 0.0

    # -- 按键灯光 (2 × 2) -------------------------------------------------------

    def _lighting_tile(self) -> WallTile:
        tile = WallTile(2, 2)
        tile.setObjectName("lightingFocusCard")
        box = tile.box
        box.setSpacing(6)
        self._preview_title = _name("设备实时预览", "wallTileNote")
        self._preview_title.hide()
        self._preview_toggle_slot = QHBoxLayout()
        self._preview_toggle_slot.setContentsMargins(0, 0, 0, 0)
        header = _row(TileIcon("sun"), title_label("按键灯光"), None, self._preview_title, spacing=9)
        header.addLayout(self._preview_toggle_slot)
        self._preview_title.hide()  # shown once the main window supplies the switch
        box.addLayout(header)
        self._preview_status_slot = QHBoxLayout()
        self._preview_status_slot.setContentsMargins(37, 0, 0, 0)
        box.addLayout(self._preview_status_slot)
        # The device picture is the key picker; the row under it gives the
        # chosen key one of the preset colours (灯光页 v0.2 第 3 张).
        self._device_view = DeviceTopView()
        self._device_view.setObjectName("lightingDevicePreview")
        self._device_view.key_selected.connect(self._select_light_key)
        box.addWidget(self._device_view, 1)
        self._color_row = ColorPresetRow()
        self._color_row.setObjectName("lightingColorRow")
        self._color_row.color_chosen.connect(self._choose_light_color)
        box.addWidget(self._color_row)

        status_count = _integer(self._features.get("status_rgb_count"), 0)
        status_values = self._lighting.get("status")
        if status_count > 0 and isinstance(status_values, list):
            status_grid = QGridLayout()
            for index, value in enumerate(status_values[:status_count]):
                button = RgbButton(f"状态灯 {index + 1}", value)
                button.value_changed.connect(self._emit_lighting_changed)
                self._status_colors.append(button)
                status_grid.addWidget(button, index // 4, index % 4)
            box.addLayout(status_grid)

        under_key_count = _integer(self._features.get("under_key_rgb_count"), 0)
        under_key_values = self._lighting.get("under_key")
        if under_key_count > 0 and isinstance(under_key_values, list):
            for index, value in enumerate(under_key_values[:under_key_count]):
                control_id = f"key.{index + 1}"
                if (
                    control_id not in self._under_key_control_ids
                    or control_id in self._agent_status_control_ids
                ):
                    continue
                # The device picture is the visible selector; this button keeps
                # the value and the colour dialog so writes stay unchanged.
                button = RgbButton(f"功能键 {index + 1}", value, tile, control_id=control_id)
                button.setProperty("underKeyIndex", index)
                button.value_changed.connect(self._emit_lighting_changed)
                self._under_key_colors.append((index, button))
                button.hide()
        self._light_buttons = {button.property("controlId"): button for _index, button in self._under_key_colors}
        self._color_row.setVisible(bool(self._light_buttons))
        self.lighting_changed.connect(self._show_lighting)
        if self._agent_status_control_ids:
            note = _name("不带键号的 6 颗是状态灯，由 Codex 自动控制。", "wallTileNote")
            note.setWordWrap(True)
            box.addWidget(note)

        if self._lighting_levels is None:
            self._lighting_enabled = QCheckBox(objectName="lightingEnabled")
            set_translatable_accessible_name(self._lighting_enabled, "启用灯光")
            self._lighting_enabled.setChecked(self._original_lighting_enabled)
            self._lighting_enabled.toggled.connect(self._emit_lighting_changed)
            self._lighting_brightness = QSpinBox(objectName="lightingBrightness")
            brightness_range = self._rules.lighting_brightness
            self._lighting_brightness.setRange(brightness_range.minimum, brightness_range.maximum)
            self._lighting_brightness.setSuffix(" %")
            self._lighting_brightness.setValue(self._original_lighting_brightness)
            self._lighting_brightness.valueChanged.connect(self._emit_lighting_changed)
            box.addLayout(_row(_name("亮度"), self._slider_and_value(self._lighting_brightness),
                               self._lighting_enabled, spacing=12))
            return tile

        if not self._lighting_level_is_standard:
            # Takes the status line's place above the bar until the bar is moved.
            self._lighting_level_note = _name(
                "当前旧配置值会保持不变；拖动后将使用设备的 0–4 档位。", "wallTileNote")
            self._lighting_level_note.setWordWrap(True)
            box.addWidget(self._lighting_level_note)
        # One continuous dot bar that snaps to the five levels on release
        # (the designer, lighting page v0.4); the label on the right names the level.
        self._lighting_brightness = DotMatrixSlider(
            Qt.Orientation.Horizontal, objectName="lightingBrightness", snap=True,
        )
        self._lighting_brightness.setRange(0, len(self._lighting_levels) - 1)
        self._lighting_brightness.setSingleStep(1)
        self._lighting_brightness.setPageStep(1)
        self._lighting_brightness.setValue(_initial_lighting_level_index(
            self._original_lighting_enabled, self._original_lighting_brightness, self._lighting_levels,
        ))
        if not self._lighting_level_is_standard:
            self._lighting_brightness.set_between(_lighting_level_fraction(
                self._original_lighting_brightness, self._lighting_levels
            ))
        set_translatable_accessible_name(self._lighting_brightness, "亮度档位")
        self._lighting_level_value = QLabel(objectName="lightingLevelValue")
        self._lighting_level_value.setMinimumWidth(42)
        self._lighting_level_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._refresh_lighting_level_value()
        self._lighting_brightness.valueChanged.connect(self._use_selected_lighting_level)
        # Landing on the stop it already had still counts as choosing it
        # (a saved in-between value is then replaced by that level).
        self._lighting_brightness.sliderReleased.connect(self._use_selected_lighting_level)
        level_row = _row(_name("亮度"), spacing=12)
        level_row.addWidget(self._lighting_brightness, 1)
        level_row.addWidget(self._lighting_level_value)
        box.addLayout(level_row)
        return tile

    # -- 屏幕 (1 cell, opens to the right over 震动) ----------------------------

    def _build_display_controls(self, tile: QWidget) -> None:
        self._display_icon = TileIcon("screen")
        self._display_title = title_label("屏幕")
        self._display_open = RoundArrowButton("expandScreenTile", "屏幕的更多设置")
        self._display_open.clicked.connect(lambda: self.open_tile(
            None if self._open_tile is self._screen_card else self._screen_card))
        self._display_brightness = DotMatrixSlider(Qt.Orientation.Horizontal, objectName="displayBrightness")
        brightness_range = self._rules.display_brightness
        self._display_brightness.setRange(brightness_range.minimum, brightness_range.maximum)
        self._display_brightness.setSingleStep(1)
        self._display_brightness.setPageStep(10)
        self._display_brightness.setValue(_integer(self._display.get("brightness"), 0))
        set_translatable_accessible_name(self._display_brightness, "屏幕亮度")
        self._display_brightness_value = QLabel(objectName="displayBrightnessValue")
        self._display_brightness_value.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._display_brightness_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._display_brightness_value.setMinimumWidth(38)
        self._display_brightness_value.setText(f"{self._display_brightness.value()}%")
        self._display_brightness.valueChanged.connect(
            lambda value: self._display_brightness_value.setText(f"{value}%")
        )
        rotations = self._rules.display_rotations
        initial_rotation = _integer(self._display.get("rotation"), rotations[0])
        self._display_rotation = SegmentedControl(
            [(value, f"{value}°") for value in rotations], initial_rotation,
            object_name="displayRotationControl",
        )
        set_translatable_accessible_name(self._display_rotation, "画面旋转")
        for value, button in zip(rotations, self._display_rotation.buttons()):
            # Names kept for the lighting page's change summary and tests.
            button.setObjectName("displayRotationOption")
            button.setProperty("rotationValue", value)
            button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._display_hints = QCheckBox(objectName="displayHints")
        set_translatable_accessible_name(self._display_hints, "显示控件提示")
        self._display_hints.setChecked(self._display.get("show_control_hints") is True)
        self._display_names = {
            key: _name(text) for key, text in (
                ("brightness", "亮度"), ("rotation", "画面旋转"), ("hints", "提示"),
            )
        }
        self._display_hints_label = _name("显示控件提示", "lightingRowValue")
        for widget in (self._display_icon, self._display_title, self._display_open,
                       self._display_brightness, self._display_brightness_value, self._display_rotation,
                       self._display_hints, self._display_hints_label, *self._display_names.values()):
            widget.setParent(tile)

    def _arrange_display(self, box: QVBoxLayout, expanded: bool) -> None:
        self._display_open.set_direction("left" if expanded else "right")
        set_translatable_accessible_name(
            self._display_open, "收起屏幕设置" if expanded else "屏幕的更多设置")
        if not expanded:
            box.addLayout(_row(self._display_icon, None, self._display_open))
            box.addStretch(1)
            box.addLayout(_row(self._display_title))
            line = _row(spacing=10)
            line.addWidget(self._display_brightness, 1)
            line.addWidget(self._display_brightness_value)
            box.addLayout(line)
            self._display_brightness.show()
            self._display_brightness_value.show()
            return
        box.addLayout(_row(self._display_icon, self._display_title, None, self._display_open, spacing=9))
        box.addSpacing(6)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        names = self._display_names
        grid.addWidget(names["brightness"], 0, 0)
        grid.addWidget(self._display_brightness, 0, 1, 1, 2)
        grid.addWidget(self._display_brightness_value, 0, 3)
        grid.addWidget(names["rotation"], 1, 0)
        grid.addWidget(self._display_rotation, 1, 1, 1, 3)
        grid.addWidget(names["hints"], 2, 0)
        grid.addWidget(self._display_hints_label, 2, 1)
        grid.addWidget(self._display_hints, 2, 3, Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        box.addLayout(grid)
        box.addStretch(1)
        for widget in (*names.values(), self._display_brightness, self._display_brightness_value,
                       self._display_rotation, self._display_hints_label, self._display_hints):
            widget.show()

    # -- 震动 (1 cell, opens to the left over 屏幕) ------------------------------

    def _build_haptic_controls(self, tile: QWidget) -> None:
        self._haptic_icon = TileIcon("haptic")
        self._haptic_title = title_label("震动")
        self._haptic_enabled = QCheckBox(objectName="hapticEnabled")
        set_translatable_accessible_name(self._haptic_enabled, "启用震动")
        self._haptic_enabled.setChecked(self._haptic.get("enabled") is True)
        self._haptic_more = QPushButton(objectName="expandHapticTile")
        self._haptic_more.setProperty("wallRole", "link")
        self._haptic_more.setCursor(Qt.CursorShape.PointingHandCursor)
        set_translatable_text(self._haptic_more, "时长和分类 ›")
        self._haptic_more.clicked.connect(lambda: self.open_tile(self._haptic_card))
        self._haptic_close = RoundArrowButton("collapseHapticTile", "收起震动设置")
        self._haptic_close.clicked.connect(lambda: self.open_tile(None))
        self._haptic_help_button = QPushButton(objectName="expandHapticHelp")
        self._haptic_help_button.setProperty("wallRole", "link")
        self._haptic_help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        set_translatable_text(self._haptic_help_button, "说明 ›")
        self._haptic_help_button.clicked.connect(self._show_haptic_help)

        if self._haptic_levels is None:
            self._haptic_strength = QSpinBox(objectName="hapticStrength")
            strength_range = self._rules.haptic_strength
            self._haptic_strength.setRange(strength_range.minimum, strength_range.maximum)
            self._haptic_strength.setSuffix(" %")
            self._haptic_strength.setValue(self._haptic_level_strength)
            self._haptic_strength_control = self._slider_and_value(self._haptic_strength)
        else:
            # 标准 / 强. Turning vibration off is the switch's job (the designer,
            # 灯光页 v0.2 dropped "0 · 关闭震动"); an old strength outside the
            # levels shows no choice and is kept until one is picked.
            levels = tuple(level for level in self._haptic_levels if level > 0)
            names = ("标准", "强") if len(levels) == 2 else tuple(str(i + 1) for i in range(len(levels)))
            self._haptic_strength = SegmentedControl(
                list(zip(levels, names)),
                self._haptic_level_strength if self._haptic_level_strength in levels else None,
                object_name="hapticStrength",
            )
            set_translatable_accessible_name(self._haptic_strength, "震动强度")
            if self._haptic_level_strength not in levels:
                group = self._haptic_strength.buttons()[0].group()
                group.setExclusive(False)
                for button in self._haptic_strength.buttons():
                    button.setChecked(False)
                group.setExclusive(True)
            self._haptic_strength.valueChanged.connect(self._select_haptic_level)
            self._haptic_strength_control = self._haptic_strength

        self._haptic_duration = QSpinBox(objectName="hapticDuration")
        duration_range = self._rules.haptic_duration_ms
        self._haptic_duration.setRange(duration_range.minimum, duration_range.maximum)
        self._haptic_duration.setSuffix(" ms")
        self._haptic_duration.setValue(_integer(self._haptic.get("duration_ms"), duration_range.minimum))
        self._haptic_duration_control = self._slider_and_value(self._haptic_duration)

        channels_supported = self._features.get("haptic_channels") is True
        self._haptic_on_press = QCheckBox(objectName="hapticOnPress")
        self._haptic_on_press.setChecked(self._haptic.get("on_press") is True)
        switches = [("按键反馈" if channels_supported else "按键触发", self._haptic_on_press)]
        self._haptic_channels: dict[str, QCheckBox] = {}
        if channels_supported:
            for field, label, name in (
                ("on_encoder", "旋钮反馈", "hapticOnEncoder"),
                ("on_joystick", "摇杆反馈", "hapticOnJoystick"),
                ("on_task", "任务与计时提醒", "hapticOnTask"),
            ):
                checkbox = QCheckBox(objectName=name)
                checkbox.setChecked(self._haptic.get(field, True) is True)
                self._haptic_channels[field] = checkbox
                switches.append((label, checkbox))
            self._haptic_help = HAPTIC_HELP
        else:
            self._haptic_on_profile = QCheckBox(objectName="hapticOnProfile")
            self._haptic_on_profile.setChecked(self._haptic.get("on_profile") is True)
            switches.append(("Profile 切换触发", self._haptic_on_profile))
            self._haptic_help = LEGACY_HAPTIC_HELP
        self._haptic_switches = []
        for label, checkbox in switches:
            set_translatable_accessible_name(checkbox, label)
            self._haptic_switches.append((_name(label, "lightingRowValue"), checkbox))
        self._haptic_names = {key: _name(text) for key, text in (("strength", "强度"), ("duration", "时长"))}
        parts = [self._haptic_icon, self._haptic_title, self._haptic_enabled, self._haptic_more,
                 self._haptic_close, self._haptic_help_button, self._haptic_strength_control,
                 self._haptic_duration_control, *self._haptic_names.values()]
        for name, checkbox in self._haptic_switches:
            parts += [name, checkbox]
        for widget in parts:
            widget.setParent(tile)
        self._haptic_enabled.toggled.connect(self._refresh_haptic_enabled)
        self._refresh_haptic_enabled()

    def _arrange_haptic(self, box: QVBoxLayout, expanded: bool) -> None:
        box.setSpacing(2 if expanded else 4)
        strength = self._haptic_strength_control
        if strength.property("segmented"):
            # The opened tile holds five lines in one cell's height.
            strength.setProperty("compact", expanded)
            strength.style().unpolish(strength)
            strength.style().polish(strength)
            for button in strength.findChildren(QPushButton):
                button.style().unpolish(button)
                button.style().polish(button)
        if not expanded:
            box.addLayout(_row(self._haptic_icon, None, self._haptic_enabled))
            box.addStretch(1)
            box.addLayout(_row(self._haptic_title))
            box.addWidget(self._haptic_strength_control)
            self._haptic_strength_control.show()
            box.addLayout(_row(self._haptic_more, None))
            return
        box.addLayout(_row(self._haptic_icon, self._haptic_title, None, self._haptic_help_button,
                           self._haptic_enabled, self._haptic_close, spacing=9))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.addWidget(self._haptic_names["strength"], 0, 0)
        grid.addWidget(self._haptic_strength_control, 0, 1)
        grid.addWidget(self._haptic_names["duration"], 1, 0)
        grid.addWidget(self._haptic_duration_control, 1, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        switches = QGridLayout()
        switches.setContentsMargins(0, 0, 0, 0)
        switches.setHorizontalSpacing(10)
        switches.setVerticalSpacing(2)
        for index, (name, checkbox) in enumerate(self._haptic_switches):
            row, column = index // 2, 2 * (index % 2)
            switches.addWidget(name, row, column)
            switches.addWidget(checkbox, row, column + 1, Qt.AlignmentFlag.AlignRight)
        # Share the width by how long the names are: Japanese ones run long.
        for column in (0, 2):
            widths = [name.sizeHint().width() for index, (name, _box) in enumerate(self._haptic_switches)
                      if 2 * (index % 2) == column]
            switches.setColumnStretch(column, max(widths, default=1))
        box.addLayout(switches)
        box.addStretch(1)
        shown = [*self._haptic_names.values(), self._haptic_strength_control, self._haptic_duration_control]
        for name, checkbox in self._haptic_switches:
            shown += [name, checkbox]
        for widget in shown:
            widget.show()

    def _show_haptic_help(self) -> None:
        dialog = Dialog(self.window(), "震动说明", width=MEDIUM, object_name="hapticHelpDialog")
        for text in self._haptic_help:
            label = dialog.add_text(text, secondary=True)
            label.setObjectName("hapticHelp")
        dialog.open()

    def _select_haptic_level(self, strength: int) -> None:
        self._haptic_level_strength = strength

    def _refresh_haptic_enabled(self) -> None:
        enabled = self._haptic_enabled.isChecked()
        for _name_label, checkbox in self._haptic_switches:
            checkbox.setEnabled(enabled)

    @staticmethod
    def _slider_and_value(value: QSpinBox) -> QWidget:
        """A dot bar with its value written beside it; the spin box stays as the model."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        slider = DotMatrixSlider(Qt.Orientation.Horizontal, objectName=f"{value.objectName()}Slider")
        slider.setRange(value.minimum(), value.maximum())
        slider.setValue(value.value())
        slider.setAccessibleName(value.objectName())
        slider.valueChanged.connect(value.setValue)
        value.valueChanged.connect(slider.setValue)
        shown = QLabel(objectName=f"{value.objectName()}Value")
        shown.setProperty("valuePill", True)
        shown.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        shown.setAlignment(Qt.AlignmentFlag.AlignCenter)

        def show_value(number: int) -> None:
            shown.setText(f"{number}{value.suffix()}")

        value.valueChanged.connect(show_value)
        show_value(value.value())
        layout.addWidget(slider, 1)
        layout.addWidget(shown)
        layout.addWidget(value)
        value.hide()
        return row

    # -- 屏幕图标 (2 × 1, opens a dialog) ---------------------------------------

    def _screen_icon_tile(self) -> WallTile:
        tile = WallTile(2, 1)
        tile.setObjectName("screenIconTile")
        open_dialog = RoundArrowButton("expandScreenIcons", "屏幕图标")
        open_dialog.clicked.connect(self.open_screen_icons)
        tile.box.addLayout(_row(TileIcon("image"), title_label("屏幕图标"), None, open_dialog, spacing=9))
        tile.box.addStretch(1)
        self._home_icon_preview = QLabel(objectName="homeIconPreview")
        self._home_icon_preview.setFixedSize(HOME_ICON_PREVIEW, HOME_ICON_PREVIEW)
        self._home_icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._home_icon_preview.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._home_icon_preview.setText("BORING")
        self._home_icon_state = _name("首页图片：尚未读取", "lightingRowValue")
        hint = _name("换设备待机首页的图片，或单独换各个小图标。", "wallTileNote")
        hint.setWordWrap(True)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addWidget(self._home_icon_state)
        text.addWidget(hint)
        bottom = _row(self._home_icon_preview, spacing=14)
        bottom.addLayout(text, 1)
        tile.box.addLayout(bottom)
        return tile

    def bind_home_icon(self, transfer) -> None:
        """Show on the tile what the device's home picture is now."""
        self._home_transfer = transfer
        # A bound method of this editor: Qt drops the connection with the page.
        transfer.changed.connect(self._sync_home_icon)
        self._sync_home_icon()

    def _sync_home_icon(self, *_args: object) -> None:
        transfer = self._home_transfer
        metadata = transfer.metadata
        state = (
            "首页图片：默认" if metadata and metadata["source"] == "default"
            else "首页图片：自定义" if transfer.pixels is not None
            else "首页图片：尚未读取"
        )
        set_translatable_text(self._home_icon_state, state)
        if transfer.pixels is not None:
            image = icon_preview(transfer.pixels)
            self._home_icon_preview.setPixmap(QPixmap.fromImage(image).scaled(
                self._home_icon_preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def open_screen_icons(self) -> None:
        """屏幕图标 dialog: 首页图片 and 其他图标（高级） (灯光页 v0.2 第 6 张)."""
        dialog = self._screen_icon_dialog
        if dialog is None:
            dialog = Dialog(self.window(), "屏幕图标", width=LARGE, object_name="screenIconDialog")
            pages = SegmentedControl(
                [("home", "首页图片"), ("glyphs", "其他图标（高级）")], "home",
                object_name="screenIconPages",
            )
            _home_button, glyph_button = pages.buttons()
            glyph_button.setObjectName("expandScreenGlyphs")
            pages.setMinimumWidth(300)
            row = QHBoxLayout()
            row.addWidget(pages)
            row.addStretch(1)
            dialog.body_layout().addLayout(row)
            home = QWidget(objectName="screenIconOptions")
            home_layout = QVBoxLayout(home)
            home_layout.setContentsMargins(0, 0, 0, 0)
            icon_editor = ScreenIconEditor(
                self._screen_icon_draft,
                device_supported=self._features.get("custom_home_icon") is True,
            )
            home_layout.addWidget(icon_editor)
            glyphs = QWidget(objectName="screenGlyphOptions")
            glyph_layout = QVBoxLayout(glyphs)
            glyph_layout.setContentsMargins(0, 0, 0, 0)
            glyphs.hide()
            dialog.body_layout().addWidget(home)
            dialog.body_layout().addWidget(glyphs)
            opened = []

            def show_page(page: str) -> None:
                home.setVisible(page == "home")
                glyphs.setVisible(page == "glyphs")
                if page == "glyphs" and not opened:
                    opened.append(True)
                    self.screen_glyphs_opened.emit(glyph_layout)
                elif page == "glyphs":
                    self.screen_glyphs_opened.emit(None)
                dialog.fit()

            pages.valueChanged.connect(show_page)
            self._screen_icon_dialog = dialog
            # The dialog belongs to the window; it goes when this page goes.
            self.destroyed.connect(dialog.deleteLater)
            self.screen_icon_editor_created.emit(icon_editor)
        dialog.open()


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


def _lighting_level_fraction(value: int, levels: tuple[int, ...]) -> float:
    """Where a saved brightness sits along the level bar, 0..1, read between
    the two levels around it (levels are not evenly spaced)."""
    last = len(levels) - 1
    if value <= levels[0]:
        return 0.0
    for index in range(1, len(levels)):
        if value <= levels[index]:
            low, high = levels[index - 1], levels[index]
            return (index - 1 + (value - low) / (high - low)) / last
    return 1.0


def _lighting_level_names(count: int) -> tuple[str, ...]:
    if count == 5:
        return ("关闭灯光", "低", "中", "高", "最高")
    return tuple(str(index) for index in range(count))
