"""灯光与反馈 (route ``lighting``): the lighting, haptic and display wall.

Layout and controls live in :class:`PreferencesEditor` (the designer, 灯光页排法二
v0.4); this page builds it for the current draft, wires the live preview, the
screen icon dialog and the window's save banner, and saves the editor's values
into the draft.

The main window keeps its per-route cache for this route (reusing a built page
while the draft and its values are unchanged), so the page offers build() and
the actions, not Page.update().
"""
from __future__ import annotations

import copy
from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from controller_config.views.ui import notice
from controller_config.i18n import (
    LanguageManager,
    set_translatable_accessible_name,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.lighting_preview import LightingPreviewStatus
from controller_config.models import AppState, DeviceSnapshot
from controller_config.save_banner_state import SaveFlow
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.device_silhouette import MATRIX12_AGENT_STATUS_KEYS, MATRIX12_HARDWARE_IDS
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.screen_glyph_editor import ScreenGlyphEditor
from controller_config.views.screen_icon_editor import ScreenIconDraft
from controller_config.views.settings_wall import wall_scroll
from controller_config.views.ui import SegmentedControl
from controller_config.views.ui.save_banner import SaveBanner

POWER_V2_HARDWARE_ID = "WMP-S3-MATRIX12-POWER-V2"
POWER_V2_LIGHTING_DEVICE_MAX = 80
POWER_V2_LIGHTING_LEVELS = (0, 10, 20, 40, POWER_V2_LIGHTING_DEVICE_MAX)
# Firmware 20261029+: the former 40/50 percent levels were too weak and are gone.
POWER_V2_HAPTIC_LEVELS = (0, 60, 80)

# Preview messages that need no line under 按键灯光.
_QUIET_PREVIEW_MESSAGES = {
    "实时预览已关闭",
    "实时预览已就绪；开启后的效果不会自动保存",
}


class LightingPage:
    """Build 灯光与反馈 and carry out what its controls ask for."""

    route = "lighting"

    def __init__(
        self,
        view_model: MainViewModel,
        language_manager: LanguageManager,
        *,
        window: QWidget,
        save_banner: SaveBanner,
        settings_rail: Callable[[str], QWidget],
        wall_workspace: Callable[[str], QWidget],
        state_page: Callable[[object], QWidget],
        screen_icon_draft_for: Callable[[DeviceSnapshot], ScreenIconDraft],
        screen_glyph_drafts: dict,
        prepare_device_write: Callable[..., None],
    ) -> None:
        self._view_model = view_model
        self._language_manager = language_manager
        self._window = window
        self._save_banner = save_banner
        self._settings_rail = settings_rail
        self._wall_workspace = wall_workspace
        self._state_page = state_page
        self._screen_icon_draft_for = screen_icon_draft_for
        self._screen_glyph_drafts = screen_glyph_drafts
        self._prepare_device_write = prepare_device_write
        self._preview_toggle: QCheckBox | None = None
        self._preview_status: QLabel | None = None

    # -- build ---------------------------------------------------------------

    def build(self, snapshot: DeviceSnapshot, *, editing_values=None) -> QWidget:
        """The wall for the current draft; editing_values restores unsaved edits."""
        draft = self._view_model.draft
        if draft is None:
            return self._state_page(self._view_model.model)
        page = self._wall_workspace("preferencesPage")
        page.setProperty("draftIdentity", id(draft))
        page.editor_features = copy.deepcopy(draft.features)
        page.setProperty("uiLanguage", self._language_manager.language)
        page.config_values = copy.deepcopy(tuple(draft.config[key] for key in ("lighting", "haptic", "display")))
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        editor = self._editor(snapshot, editing_values)
        cards = tuple(card for card in (editor._light_card, editor._haptic_card,
                                        editor._screen_card, editor._icon_card) if card is not None)
        editing_enabled = not self._view_model.write_transaction.blocks_editing
        for card in cards:
            card.setEnabled(editing_enabled)
        self._wire_screen_icons(editor)
        preview_toggle, preview_status = self._preview_controls(editor, editing_enabled)
        editor.set_workspace(preview_toggle, preview_status)

        def refresh_summary(*_args) -> None:
            # Saving is the window's save banner (灯光页 v0.4 第 5 张); this page
            # only tells it where the flow stands. The flow itself is unchanged.
            model = self._view_model.model
            current = model.snapshot
            transaction = self._view_model.write_transaction
            enabled = not transaction.blocks_editing
            for card in cards:
                card.setEnabled(enabled)
            editor.set_device_connected(model.state in {AppState.READY, AppState.READ_ONLY})
            pending = editor.values() != (
                draft.config["lighting"], draft.config["haptic"], draft.config["display"]
            )
            self._save_banner.apply(SaveFlow(
                model_state=model.state,
                transaction_state=transaction.state,
                has_changes=pending or draft.is_dirty,
                base_changed=(current is not None
                              and current.config_result.get("generation") != draft.base_generation),
                writable=current is not None and current.compatibility.get("write") is True,
            ))

        for widget_type, signal_name in ((QCheckBox, "toggled"), (QComboBox, "currentIndexChanged"),
                                         (QSpinBox, "valueChanged"), (QSlider, "valueChanged")):
            for widget in editor.findChildren(widget_type):
                getattr(widget, signal_name).connect(refresh_summary)
        for segmented in editor.findChildren(SegmentedControl):
            segmented.valueChanged.connect(refresh_summary)
        editor.lighting_changed.connect(refresh_summary)
        page.refresh_summary = refresh_summary
        refresh_summary()
        layout.addWidget(wall_scroll(editor, self._settings_rail("appearance")), 1)
        return page

    def _editor(self, snapshot: DeviceSnapshot, editing_values) -> PreferencesEditor:
        draft = self._view_model.draft
        lighting = copy.deepcopy(draft.config["lighting"])
        candidate = self._view_model.lighting_preview.candidate
        if candidate is not None:
            lighting.update(copy.deepcopy(candidate))
        power_v2 = draft.hardware_id == POWER_V2_HARDWARE_ID
        return PreferencesEditor(
            lighting=editing_values[0] if editing_values is not None else lighting,
            screen_icon_draft=self._screen_icon_draft_for(snapshot),
            haptic=editing_values[1] if editing_values is not None else draft.config["haptic"],
            display=editing_values[2] if editing_values is not None else draft.config["display"],
            features=draft.features,
            under_key_control_ids=tuple(
                control_id for control_id in draft.controls if control_id.startswith("key.")
            ),
            agent_status_control_ids=(
                MATRIX12_AGENT_STATUS_KEYS
                if draft.hardware_id in MATRIX12_HARDWARE_IDS
                and draft.features.get("agent_status_under_key_count") == len(MATRIX12_AGENT_STATUS_KEYS)
                else frozenset()
            ),
            rules=draft.editor_rules,
            lighting_levels=POWER_V2_LIGHTING_LEVELS if power_v2 else None,
            haptic_levels=POWER_V2_HAPTIC_LEVELS if power_v2 else None,
        )

    def _preview_controls(self, editor: PreferencesEditor, editing_enabled: bool) -> tuple[QCheckBox, QLabel]:
        preview = self._view_model.lighting_preview
        toggle = QCheckBox(objectName="lightingPreviewEnabled")
        set_translatable_accessible_name(toggle, "设备实时预览")
        toggle.setChecked(preview.requested)
        toggle.setToolTip("预览不等于保存；写入设备仍需明确确认。")
        toggle.setEnabled(
            preview.supported and self._view_model.model.state is AppState.READY and editing_enabled
        )
        status = QLabel(preview.message, objectName="lightingPreviewStatus")
        status.setWordWrap(True)
        status.setProperty("wallState", True)
        status.setProperty("tone", "quiet")
        status.setToolTip(preview.technical)
        status.setVisible(preview.requested or preview.message not in _QUIET_PREVIEW_MESSAGES)
        toggle.toggled.connect(lambda checked: self._toggle_preview(editor, toggle, checked))
        editor.lighting_changed.connect(lambda lighting: self._preview_changed(toggle, lighting))
        self._preview_toggle, self._preview_status = toggle, status
        return toggle, status

    def _wire_screen_icons(self, editor: PreferencesEditor) -> None:
        def configure_icon_editor(icon_editor) -> None:
            icon_editor.upload_requested.connect(self._upload_screen_icon)
            icon_editor.reset_requested.connect(self._reset_screen_icon)
            icon_editor.refresh_requested.connect(self._view_model.refresh_screen_icon)
            icon_editor.cancel_requested.connect(self._view_model.screen_icon.cancel)
            icon_editor.bind_transfer(self._view_model.screen_icon)
            self._language_manager.retranslate_widget_tree(icon_editor)
            # The dialog opens right after this; read the home picture once it shows.
            QTimer.singleShot(0, icon_editor, lambda: self._refresh_home_icon_when_idle(icon_editor))

        def open_screen_glyphs(host_layout) -> None:
            if host_layout is not None:
                glyph_editor = ScreenGlyphEditor(self._screen_glyph_drafts, show_heading=False)
                host_layout.addWidget(glyph_editor)
                glyph_editor.selection_requested.connect(self._view_model.refresh_screen_glyphs)
                glyph_editor.refresh_requested.connect(self._view_model.refresh_screen_glyphs)
                glyph_editor.write_requested.connect(self._write_screen_glyph)
                glyph_editor.reset_requested.connect(lambda: self._write_screen_glyph(None))
                glyph_editor.bind_transfer(self._view_model.screen_glyphs)
                self._language_manager.retranslate_widget_tree(glyph_editor)
                editor.glyph_editor = glyph_editor
            glyph_editor = getattr(editor, "glyph_editor", None)
            if glyph_editor is not None:
                QTimer.singleShot(0, glyph_editor, lambda: self._refresh_screen_glyphs_when_idle(glyph_editor))

        editor.screen_icon_editor_created.connect(configure_icon_editor)
        editor.screen_glyphs_opened.connect(open_screen_glyphs)
        if editor._icon_card is not None:
            editor.bind_home_icon(self._view_model.screen_icon)

    # -- saving --------------------------------------------------------------

    def save(self, editor: PreferencesEditor) -> bool:
        """Put the editor's values into the local draft."""
        lighting, haptic, display = editor.values()
        try:
            self._view_model.set_preferences(lighting=lighting, haptic=haptic, display=display)
        except ValueError as exc:
            notice.alert(self._window, "无法保存设备偏好", str(exc))
            return False
        errors = self._view_model.validate_draft()
        if errors:
            notice.alert(self._window, "偏好已保存，但草稿未通过校验", errors[0])
            return False
        return True

    def save_and_prepare(self, editor: PreferencesEditor) -> None:
        """保存到设备 / 再试一次: the draft, then the device check before ②."""
        if self.save(editor):
            self._prepare_device_write(confirm_after_validation=True)

    # -- live preview --------------------------------------------------------

    def _toggle_preview(self, editor: PreferencesEditor, toggle: QCheckBox, enabled: bool) -> None:
        try:
            self._view_model.set_lighting_preview_enabled(
                enabled, editor.lighting_value() if enabled else None,
            )
        except ValueError as exc:
            blocked = toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(blocked)
            notice.notify(self._window, "无法开启灯光预览", str(exc))

    def _preview_changed(self, toggle: QCheckBox, lighting: dict) -> None:
        if not toggle.isChecked():
            return
        try:
            self._view_model.update_lighting_preview(lighting)
        except ValueError as exc:
            blocked = toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(blocked)
            notice.notify(self._window, "无法更新灯光预览", str(exc))

    def preview_status_changed(self, status: LightingPreviewStatus) -> None:
        """The device reported the live preview's state: show it on the switch."""
        if self._view_model.page != self.route:
            return
        toggle, label = self._preview_toggle, self._preview_status
        if toggle is not None and isValid(toggle):
            blocked = toggle.blockSignals(True)
            toggle.setChecked(status.requested)
            toggle.setEnabled(
                status.supported
                and self._view_model.model.state is AppState.READY
                and not self._view_model.write_transaction.blocks_editing
            )
            toggle.blockSignals(blocked)
        if label is not None and isValid(label):
            set_translatable_text(label, status.message)
            label.setToolTip(translate_ui_text(status.technical))
            label.setVisible(status.requested or status.message not in _QUIET_PREVIEW_MESSAGES)

    # -- screen icons --------------------------------------------------------

    def _upload_screen_icon(self, pixels: bytes) -> None:
        try:
            self._view_model.upload_screen_icon(pixels)
        except ValueError as exc:
            notice.alert(self._window, translate_ui_text("写入图标"), translate_ui_text(str(exc)))

    def _reset_screen_icon(self) -> None:
        try:
            self._view_model.reset_screen_icon()
        except ValueError as exc:
            notice.alert(self._window, translate_ui_text("恢复默认图标"), translate_ui_text(str(exc)))

    def _write_screen_glyph(self, pixels: bytes | None) -> None:
        try:
            self._view_model.write_screen_glyph(pixels)
        except ValueError as exc:
            notice.alert(self._window, translate_ui_text("功能与模式图标"), translate_ui_text(str(exc)))

    def _refresh_home_icon_when_idle(self, shown: QWidget) -> None:
        """Read from the device once the editor is on screen and the other transfer is idle."""
        if not shown.isVisible():
            return
        transfer = self._view_model.screen_icon
        if not transfer.supported or transfer.metadata is not None or transfer.busy:
            return
        if self._view_model.screen_glyphs.busy:
            QTimer.singleShot(50, shown, lambda: self._refresh_home_icon_when_idle(shown))
            return
        self._view_model.refresh_screen_icon()

    def _refresh_screen_glyphs_when_idle(self, shown: QWidget) -> None:
        """Read from the device once the editor is on screen and the other transfer is idle."""
        if not shown.isVisible():
            return
        transfer = self._view_model.screen_glyphs
        if not transfer.supported or transfer.busy:
            return
        if self._view_model.screen_icon.busy:
            QTimer.singleShot(50, shown, lambda: self._refresh_screen_glyphs_when_idle(shown))
            return
        self._view_model.refresh_screen_glyphs()
