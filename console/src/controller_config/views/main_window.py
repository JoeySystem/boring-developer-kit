from __future__ import annotations

import copy
import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QSettings,
    QSignalBlocker,
    QTimer,
    QVariantAnimation,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QKeySequence,
    QShortcut,
)
from controller_config.views.ui import motion, notice
from controller_config.voice_presets import DEFAULT_VOICE_USAGE
from controller_config.views.name_validator import NameLengthValidator

from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from controller_config.actions import describe_action, is_voice_input_action, mapping_display_name
from controller_config import __version__
from controller_config import desktop_permissions
from controller_config.accessibility import system_reduces_motion
from controller_config.app_release_summary import load_app_release_summary
from controller_config.drafts import reorder_visible
from controller_config.official_controls import (
    MATRIX12_CODEX_KEY_NAMES,
)
from controller_config.appearance import (
    enable_native_window_controls,
    install_macos_vibrancy,
    macos_window_full_screen,
    NATIVE_WINDOW_CONTROLS_CENTER,
    position_native_window_controls,
    refresh_native_window_controls,
    set_macos_window_backdrop,
    title_bar_double_click_action,
    V4_RADIUS,
    V4_STYLE,
    V4_TOKENS,
)
from controller_config.style import tokens
from controller_config.style.sheets import LEGACY_STYLE, application_stylesheet
from controller_config.views.ui import (
    MEDIUM, Banner, BannerStack, DeviceStatusCapsule, Dialog, LanguageKeys, LanguageSwitch, SegmentedControl,
    OrbitalMark, ProgressRing, confirm,
)
from controller_config.views.ui.save_banner import SaveBanner
from controller_config.views.ui.attention_blink import AttentionBlink
from controller_config.views.ui.page_motion import (
    Block, CrossFade, PageArrival, clear_grab, hidden_grab, stagger,
)
from controller_config.views.mode_motion import Link
from controller_config.views.other_action_dialog import OtherActionContext, OtherActionDialog
from controller_config.views.mode_page import (
    BarState, KnobRow, ModeBottomBar, ModeCard, ModeKeyGlyph, ModeKeySwatch, ModeStage, OverviewScreen, PurposeSegment,
    QuotaProvider, QuotaWindow, StageState, StageText,
)
from controller_config.save_banner_state import SaveBannerKind
from controller_config.views.pages.prompts import PromptsPage
from controller_config.views.pages.lighting import (  # noqa: F401 - levels re-exported for tests
    POWER_V2_HAPTIC_LEVELS,
    POWER_V2_LIGHTING_LEVELS,
    LightingPage,
)
from controller_config.views.v4_widgets import V4Card
from controller_config.views.ui.copy_diagnostics import BUTTON_TEXT as COPY_DIAGNOSTICS_TEXT
from controller_config.views.ui.copy_diagnostics import console_diagnostics_block, copy_diagnostics
from controller_config.views.ui.diagnosis_prompt import ENTRY_TEXT as DIAGNOSIS_ENTRY_TEXT, show_diagnosis_prompt
from controller_config.views.ui.status_icon import StatusIcon
from controller_config.views.settings_wall import (
    SECTIONS as SETTINGS_SECTIONS,
    WALL_STYLE,
    ClaudeStatusTile,
    CodexStatusTile,
    DoubaoTile,
    LampsTile,
    RailButton,
    SettingsRail,
    SettingsWall,
    StateLine,
    WallTile,
    note_label,
    pill_button,
    rule as wall_rule,
    show_card_dialog,
    square_tile,
    value_row as wall_value_row,
    wall_scroll,
    wide_tile,
)
from controller_config.views.connection_terminal import ConnectionTerminal
from controller_config.views.connection_progress import ConnectionProgress
from controller_config.codex_agent_focus import AGENT_CLIENT_KEY, installed_agent_clients
from controller_config.background_helper import PromptBackgroundController
from controller_config.build_identity import BuildIdentity, load_build_identity
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.i18n import (
    ENGLISH,
    JAPANESE,
    SIMPLIFIED_CHINESE,
    SKIP_TRANSLATION_PROPERTY,
    SUPPORTED_LANGUAGES,
    LanguageManager,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.connect_timing import timing_summary
from controller_config.models import (
    AppState,
    DeviceSnapshot,
    ScreenModel,
)
from controller_config.prompt_library import (
    PROMPT_SLOT_COUNT,
    QUICK_PROMPT_DIRECTIONS,
    QUICK_PROMPT_IDS,
)
from controller_config.profile_templates import (
    agent_profile_mappings,
    agent_profile_templates_for_platform,
    is_other_platform_profile,
)
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transactions import ConfigTransactionState
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.action_editor import ActionEditor
from controller_config.views.voice_guide import VoiceSetupDialog
from controller_config.views.global_voice import GlobalVoiceDialog
from controller_config.voice_setup import (
    voice_mapping, save_voice_trial, voice_trial, voice_draft_is_scoped,
    voice_trial_state, confirmed_voice_choice, save_confirmed_voice_choice,
    voice_provider_key,
)
from controller_config.views.ai_setup import AISetupDialog
from controller_config.ai_setup import (
    AI_BY_KEY,
    AI_SETUP_STARTED_KEY,
    load_choices, load_device_choices, linked_profile, save_device_choices,
)
from controller_config.views.draft_dialog import confirm_local_draft
from controller_config.views.device_silhouette import (
    DEVICE_SILHOUETTE_STYLE,
    DeviceModelCanvas,
    MATRIX12_AGENT_STATUS_KEYS,
    MATRIX12_HARDWARE_IDS,
    control_display_name,
    )


# Neutral tint over the native material: 40% opacity, not whole-window opacity.
ROOT_MATERIAL_TINT = QColor(*tokens.SHELL_TINT)
from controller_config.views.actions import ActionsPage
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.pages import CalibrationPage, FirmwarePage, SequencesPage, StatePage
from controller_config.views.pages.common import (
    add_technical_details as _add_technical_details,
    card as _card,
    planned_tool_capability as _planned_tool_capability,
)
from controller_config.onboarding import (
    ONBOARDING_COMPLETED_KEY,
    OnboardingDialog,
)
from controller_config.views.preferences_editor import BleNameEditor, PreferencesEditor
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor
from controller_config.views.screen_glyph_editor import GlyphDraft
from controller_config.views.prompt_library_editor import (
    PromptEditingState,
    PromptLibraryEditor,
)


_LANGUAGE_LABELS = {
    SIMPLIFIED_CHINESE: "简体中文",
    ENGLISH: "English",
    JAPANESE: "日本語",
}
_LANGUAGE_GLYPHS = {SIMPLIFIED_CHINESE: "文", ENGLISH: "A", JAPANESE: "あ"}  # 语言大键上的大字（design decision）


def _translated_application_display_name(
    build_identity: BuildIdentity, language_manager: LanguageManager
) -> str:
    name = language_manager.translate("BORING 控制台")
    if not build_identity.allows_official_updates:
        name = "BORING Console Community"
    if build_identity.is_dev_build:
        name = f"{name} {language_manager.translate('开发版')}"
    return name


# The old stylesheet layers live in style/sheets.py now (step 3c). APP_STYLE
# keeps its old meaning (everything before the V4 sheet) for the prompt
# silhouette, which appends V4_STYLE itself.
APP_STYLE = LEGACY_STYLE + DEVICE_SILHOUETTE_STYLE


@dataclass(frozen=True)
class _MappingEditingState:
    device_identity: tuple[str, str]
    control_id: str
    short_name: str
    action: dict
    mode: str = "normal"





# 顶部三个页签的图标：实心圆里挖出图形（四颗按键、输入提示符、齿轮）。
# the designer 2026-11-18 从三组候选里选的 C 组，齿轮和箭头用"稍圆"那一档圆角。
# 画法和候选图见 Console 视觉稿「顶部页签图标-C组-v0.1-20261118」；24×24 的格子，按 evenodd 填充。
NAV_SOLID_ICONS = {
    "nav.mode": "M22 12A10 10 0 1 0 2 12A10 10 0 1 0 22 12ZM8.3 7.2h1.8a1.1 1.1 0 0 1 1.1 1.1v1.8a1.1 1.1 0 0 1 -1.1 1.1h-1.8a1.1 1.1 0 0 1 -1.1 -1.1v-1.8a1.1 1.1 0 0 1 1.1 -1.1ZM13.9 7.2h1.8a1.1 1.1 0 0 1 1.1 1.1v1.8a1.1 1.1 0 0 1 -1.1 1.1h-1.8a1.1 1.1 0 0 1 -1.1 -1.1v-1.8a1.1 1.1 0 0 1 1.1 -1.1ZM8.3 12.8h1.8a1.1 1.1 0 0 1 1.1 1.1v1.8a1.1 1.1 0 0 1 -1.1 1.1h-1.8a1.1 1.1 0 0 1 -1.1 -1.1v-1.8a1.1 1.1 0 0 1 1.1 -1.1ZM13.9 12.8h1.8a1.1 1.1 0 0 1 1.1 1.1v1.8a1.1 1.1 0 0 1 -1.1 1.1h-1.8a1.1 1.1 0 0 1 -1.1 -1.1v-1.8a1.1 1.1 0 0 1 1.1 -1.1Z",
    "nav.prompt": "M22 12A10 10 0 1 0 2 12A10 10 0 1 0 22 12ZM7.62 8.62Q7.30 8.30 7.62 7.98L8.38 7.22Q8.70 6.90 9.02 7.22L13.48 11.68Q13.80 12.00 13.48 12.32L9.02 16.78Q8.70 17.10 8.38 16.78L7.62 16.02Q7.30 15.70 7.62 15.38L10.68 12.32Q11.00 12.00 10.68 11.68ZM14.2 14.6h2.6a1 1 0 0 1 1 1v0a1 1 0 0 1 -1 1h-2.6a1 1 0 0 1 -1 -1v0a1 1 0 0 1 1 -1Z",
    "nav.settings": "M22 12A10 10 0 1 0 2 12A10 10 0 1 0 22 12ZM10.37 7.64Q10.83 7.45 10.87 6.95L10.95 5.99Q11.00 5.37 11.62 5.37L12.38 5.37Q13.00 5.37 13.05 5.99L13.13 6.95Q13.17 7.45 13.63 7.64L13.93 7.76Q14.39 7.95 14.77 7.63L15.51 7.01Q15.98 6.61 16.42 7.05L16.95 7.58Q17.39 8.02 16.99 8.49L16.37 9.23Q16.05 9.61 16.24 10.07L16.36 10.37Q16.55 10.83 17.05 10.87L18.01 10.95Q18.63 11.00 18.63 11.62L18.63 12.38Q18.63 13.00 18.01 13.05L17.05 13.13Q16.55 13.17 16.36 13.63L16.24 13.93Q16.05 14.39 16.37 14.77L16.99 15.51Q17.39 15.98 16.95 16.42L16.42 16.95Q15.98 17.39 15.51 16.99L14.77 16.37Q14.39 16.05 13.93 16.24L13.63 16.36Q13.17 16.55 13.13 17.05L13.05 18.01Q13.00 18.63 12.38 18.63L11.62 18.63Q11.00 18.63 10.95 18.01L10.87 17.05Q10.83 16.55 10.37 16.36L10.07 16.24Q9.61 16.05 9.23 16.37L8.49 16.99Q8.02 17.39 7.58 16.95L7.05 16.42Q6.61 15.98 7.01 15.51L7.63 14.77Q7.95 14.39 7.76 13.93L7.64 13.63Q7.45 13.17 6.95 13.13L5.99 13.05Q5.37 13.00 5.37 12.38L5.37 11.62Q5.37 11.00 5.99 10.95L6.95 10.87Q7.45 10.83 7.64 10.37L7.76 10.07Q7.95 9.61 7.63 9.23L7.01 8.49Q6.61 8.02 7.05 7.58L7.58 7.05Q8.02 6.61 8.49 7.01L9.23 7.63Q9.61 7.95 10.07 7.76ZM13.9 12A1.9 1.9 0 1 0 10.1 12A1.9 1.9 0 1 0 13.9 12Z",
}


# 顶部三个页签的名字。the designer 2026-11-18 定：中文、英文、日文下都直接显示这三个英文。
NAV_LABELS = {"overview": "Mode", "prompts": "Prompt", "settings": "Settings"}
# Longest wait for the found clip's ending (about 1.64 s) before the Mode page takes over.
CONNECTION_FINALE_GUARD_MS = 2500


class _NavigationCapsule(QFrame):
    """Paint a true pill so child widgets can never square off its ends."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedHeight(48)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.setPen(QPen(QColor(*tokens.SHELL_CAPSULE_EDGE), 1))
        painter.setBrush(QColor(*tokens.SHELL_CAPSULE_FILL))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class _TopNavigationButton(QPushButton):
    """Keep every destination in place while the active background changes."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        size = super().sizeHint()
        if self.property("settingsDestination"):
            captions = (NAV_LABELS["settings"], "100%")
            width = max(self.fontMetrics().horizontalAdvance(caption) for caption in captions)
            size.setWidth(width + (24 if self.property("compactNavigation") else 48) + self._ring_room())
        else:
            size.setWidth(max(size.width(), self.fontMetrics().horizontalAdvance(self.text()) + self.iconSize().width() + 28))
        return size

    def set_navigation_icons(self, *, active: QIcon, inactive: QIcon) -> None:
        self._active_navigation_icon = active
        self._inactive_navigation_icon = inactive
        self._apply_navigation_icon()

    def _apply_navigation_icon(self) -> None:
        icon = (
            getattr(self, "_active_navigation_icon", QIcon())
            if self.property("active") is True
            else getattr(self, "_inactive_navigation_icon", QIcon())
        )
        if not icon.isNull():
            self.setIcon(icon)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        if self.property("settingsDestination"):
            self.set_update_notice()
        else:
            self.setText("" if self.property("compactNavigation") else self.accessibleName())
            manager = getattr(self.window(), "_language_manager", None)
            if manager is not None:
                self.setAccessibleDescription(manager.translate("切换界面"))
        self._apply_navigation_icon()
        self.style().unpolish(self)
        self.style().polish(self)
        if self.property("settingsDestination"):
            # Stylesheet polishing reapplies its min/max widths, so restore
            # the reserved navigation width after polishing as well.
            self.setFixedWidth(max(62, self.sizeHint().width()))
        self.updateGeometry()

    def firmware_notice_visible(self) -> bool:
        # Keep the device offer for manual access; suppress only its reminder.
        desktop_priority = self.property("desktopUpdateState") in {
            "available", "downloading", "verifying", "ready", "installing",
        }
        return bool(self.property("firmwareUpdateAvailable")) and not desktop_priority

    def _download_ring(self) -> ProgressRing:
        """进度条点阵样式 v1: the smallest ring before the name while an update downloads."""
        ring = getattr(self, "_ring", None)
        if ring is None:
            ring = self._ring = ProgressRing(NAV_RING_SIZE, self)
            ring.setObjectName("settingsDownloadRing")
            ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            ring.hide()
        return ring

    def _ring_room(self) -> int:
        ring = getattr(self, "_ring", None)
        return NAV_RING_SIZE + NAV_RING_GAP if ring is not None and not ring.isHidden() else 0

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        ring = getattr(self, "_ring", None)
        if ring is not None:
            ring.move(NAV_RING_LEFT, (self.height() - NAV_RING_SIZE) // 2)

    def set_update_notice(self, *, desktop=None, firmware=None, detail=None, progress=None) -> None:
        if desktop is not None:
            self.setProperty("desktopUpdateState", desktop)
            if desktop != "downloading":
                self.setProperty("desktopUpdateProgress", None)
        if firmware is not None:
            self.setProperty("firmwareUpdateAvailable", firmware)
        if detail is not None:
            self._update_detail = detail
        if progress is not None:
            self.setProperty("desktopUpdateProgress", progress)
        if self.property("settingsDestination"):
            downloading = self.property("desktopUpdateState") == "downloading"
            ring = self._download_ring() if downloading or getattr(self, "_ring", None) else None
            if ring is not None:
                value = self.property("desktopUpdateProgress")
                # The number stays in the accessible description: the 18 px ring has no room for it.
                ring.set_value(value if isinstance(value, int) and value >= 0 else None)
                ring.setVisible(downloading)
                ring.move(NAV_RING_LEFT, (self.height() - NAV_RING_SIZE) // 2)
        translate = self.window()._language_manager.translate
        updating = bool(self.property("desktopUpdateState"))
        caption = NAV_LABELS["settings"]
        self.setAccessibleName(caption)
        self.setText(caption if not self.property("compactNavigation") else "")
        detail = getattr(self, "_update_detail", "") if updating else caption
        if self.firmware_notice_visible():
            detail += "\n" + translate("设备固件有更新")
        # No hover box on the tabs (the designer 11-20 真屏验收: "这个方框根本不需要保留");
        # the update detail stays for screen readers.
        self.setAccessibleDescription(detail)
        self._apply_navigation_icon()
        # Qt's native minimumSizeHint also depends on the current text/icon.
        # Reserve the same translated width for both settings and update states.
        self.setFixedWidth(max(62, self.sizeHint().width()))
        self.updateGeometry()
        self.update()

    def _style_option(self) -> QStyleOptionButton:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if self.property("active") is True:
            option.state &= ~QStyle.StateFlag.State_HasFocus
        return option

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt virtual method
        option = self._style_option()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.property("desktopUpdateState"):
            # Downloading shows the ring before the name, not a filled tab (进度条点阵样式 v1).
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(V4_TOKENS["control"]))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        elif self.property("active") is True:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(V4_TOKENS["baseDark"]))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*tokens.FIRMWARE_TRACK))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if (
            self.property("active") is not True
            and option.state & QStyle.StateFlag.State_HasFocus
        ):
            focus_rect = rect.adjusted(1.0, 1.0, -1.0, -1.0)
            painter.setPen(QPen(QColor(*tokens.FIRMWARE_TRACK_EDGE), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                focus_rect,
                focus_rect.height() / 2,
                focus_rect.height() / 2,
            )
        painter.end()
        option.text = option.text.replace("&", "&&")
        option.rect = option.rect.adjusted(self._ring_room(), 0, 0, 0)
        label_painter = QStylePainter(self)
        label_painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)
        if self.firmware_notice_visible() or self.property("desktopUpdateState"):
            label_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            label_painter.setPen(QPen(QColor(V4_TOKENS["baseDark"]), 1.5))
            label_painter.setBrush(QColor(tokens.FIRMWARE_WARNING_DOT))
            label_painter.drawEllipse(QRectF(self.width() - 11, 3, 7, 7))


class _DottedRoot(QWidget):
    """Translucent console field with the restrained engineering dot grid."""

    _RESIZE_MARGIN = 8

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self._corner_radius = float(V4_RADIUS)

    def set_corner_radius(self, radius: float) -> None:
        if radius != self._corner_radius:
            self._corner_radius = radius
            self.update()

    def _resize_edges(self, position) -> Qt.Edge:
        edges = Qt.Edge(0)
        if position.x() <= self._RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - self._RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if position.y() <= self._RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - self._RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def _resize_cursor(edges: Qt.Edge) -> Qt.CursorShape:
        if edges in (
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        ):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
        ):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.setCursor(self._resize_cursor(self._resize_edges(event.position())))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._resize_edges(event.position())
            handle = self.window().windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self.unsetCursor()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), self._corner_radius, self._corner_radius)
        painter.setClipPath(clip)
        native = getattr(self.window(), "_macos_vibrancy_enabled", False)
        painter.fillRect(event.rect(), ROOT_MATERIAL_TINT if native else QColor(V4_TOKENS["baseDark"]))
        ratio = self.devicePixelRatioF()
        if not hasattr(self, "_dot_tile") or self._dot_tile.devicePixelRatioF() != ratio:
            self._dot_tile = QPixmap(round(7 * ratio), round(7 * ratio))
            self._dot_tile.setDevicePixelRatio(ratio)
            self._dot_tile.fill(Qt.transparent)
            dots = QPainter(self._dot_tile)
            dots.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            dots.setPen(QColor(*tokens.SHELL_DOT))
            dots.drawPoint(3, 3)
            dots.end()
        painter.drawTiledPixmap(self.rect(), self._dot_tile, QPoint(3, 3))
        painter.end()




class _WindowChrome(QFrame):
    """Top strip of the console window.

    On macOS the system draws the close/minimize/zoom buttons over this strip,
    so it only reserves their space. Elsewhere the window is frameless and the
    strip draws its own three controls.
    """

    def __init__(self, window: QMainWindow, *, native_controls: bool = False) -> None:
        super().__init__(objectName="windowChrome")
        self._window = window
        self._native_controls = native_controls
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        if native_controls:
            # Keep the title on the same line as the system buttons, which sit
            # lower than the strip's own centre to clear the window corner.
            top = int(2 * NATIVE_WINDOW_CONTROLS_CENTER[1]) - self.height()
            layout.setContentsMargins(10, max(top, 0), 10, 0)
        else:
            layout.setContentsMargins(10, 5, 10, 3)
        layout.setSpacing(0)

        controls = QWidget()
        controls.setFixedWidth(56)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(7)
        drawn_controls = () if native_controls else (
            ("close", "关闭窗口", window.close),
            ("minimize", "最小化窗口", window.showMinimized),
            ("zoom", "缩放窗口", self._toggle_maximized),
        )
        for role, accessible_name, callback in drawn_controls:
            button = QPushButton("", objectName="windowControl")
            button.setProperty("windowRole", role)
            button.setAccessibleName(accessible_name)
            button.setToolTip(accessible_name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(callback)
            controls_layout.addWidget(button)
        layout.addWidget(controls)
        # 顶条 v1 定稿：圆钮这一条保留，上面的“BORING CONSOLE”小字删掉。
        layout.addStretch(1)

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        if event.button() == Qt.MouseButton.LeftButton:
            # With the system title bar, follow the user's macOS setting.
            action = title_bar_double_click_action() if self._native_controls else "zoom"
            if action == "minimize":
                self._window.showMinimized()
            elif action == "zoom":
                self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)



NAV_RING_SIZE = 18    # Settings tab while an update downloads (进度条点阵样式 v1，最小号)
NAV_RING_LEFT = 12
NAV_RING_GAP = 6
BRAND_SCALE = 1.5     # 点阵字原始大小的 1.5 倍：点直径 3、点距 4.5（顶条 v1 定稿）
BRAND_WORD_GAP = 12   # BORING 和 CONSOLE 之间


def _device_status_kind(model: ScreenModel) -> str:
    """Which state the top bar's capsule shows (顶条 v1 定稿; the logic is ours)."""
    snapshot = model.snapshot
    if model.state in {AppState.SCANNING, AppState.CONNECTING}:
        return "connecting"
    if model.state is AppState.AUTHENTICITY_FAILED:
        return "untrusted"
    if model.state is AppState.READ_FAILED:
        return "read_failed"
    if snapshot is None:
        return "no_device"
    # “仅固件维护”在固件更新页顶上说，胶囊只说认证（design ticket 选 B）
    if model.state not in {AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}:
        return "disconnected"
    return "verified" if snapshot.trust.is_authenticated else "dev"


SAVE_BANNER_TOP = 90  # from the window top (the designer, design decision)


class _FollowResize(QObject):
    """Call back when a widget is resized (the save banner follows its host)."""

    def __init__(self, watched: QWidget, callback) -> None:
        super().__init__(watched)
        self._callback = callback
        watched.installEventFilter(self)

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 - Qt virtual method
        if event.type() == QEvent.Type.Resize:
            self._callback()
        return False


# design decision: the smallest window, and the size it opens at.
MINIMUM_WINDOW_WIDTH = 960
MINIMUM_WINDOW_HEIGHT = 640


def _wall_workspace(object_name: str) -> QWidget:
    """A page holding a settings wall: the wall's own stylesheet applies inside it."""
    workspace = QWidget(objectName=object_name)
    workspace.setStyleSheet(WALL_STYLE)
    return workspace

class _CollapseWhenEmpty(QObject):
    """Hides `area` while none of `rows` is shown, and shows it again when one is."""

    def __init__(self, area: QWidget, rows: tuple[QWidget, ...]) -> None:
        super().__init__(area)
        self._area, self._rows = area, rows
        for row in rows:
            row.installEventFilter(self)
        self._sync()

    def eventFilter(self, _watched, event) -> bool:  # noqa: N802 - Qt virtual method
        if event.type() in {QEvent.Type.ShowToParent, QEvent.Type.HideToParent}:
            self._sync()
        return False

    def _sync(self) -> None:
        self._area.setVisible(any(not row.isHidden() for row in self._rows))


class MainWindow(QMainWindow):
    def __init__(
        self,
        view_model: MainViewModel,
        *,
        language_manager: LanguageManager | None = None,
        background_controller: PromptBackgroundController | None = None,
        onboarding_settings: QSettings | None = None,
        build_identity: BuildIdentity | None = None,
    ) -> None:
        super().__init__()
        self._desktop_update_ui = None
        self._update_workspace_restore = {}
        self._view_model = view_model
        self._background_controller = background_controller
        self._onboarding_settings = onboarding_settings
        self._onboarding_dialog: OnboardingDialog | None = None
        self._ai_setup_dialog = None
        self._shortcut_task_requested = False
        self._primary_voice_intent = None
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("MainWindow requires an active QApplication")
        self._build_identity = build_identity or load_build_identity()
        self._language_manager = language_manager or LanguageManager(
            application,
            initial_language=SIMPLIFIED_CHINESE,
            persist=False,
            parent=self,
        )
        application.setApplicationDisplayName(
            _translated_application_display_name(
                self._build_identity, self._language_manager
            )
        )
        self._nav_buttons: dict[str, QPushButton] = {}
        # 叠丙 page change (design decision): an overlay over the page area, made on first use.
        self._page_arrival: PageArrival | None = None
        self._veiled: QWidget | None = None   # the page kept unseen while the change plays
        self._page_arrival_direction = 1
        self._page_arrival_pending = False   # between the old page's still and the new page's start
        self._attention_blink = AttentionBlink(self, tokens.MODE_AGENT_ATTENTION)
        self._connection_transition_model: ScreenModel | None = None
        self._last_link_ready = False
        self._connection_fade_target: QWidget | None = None
        self._connection_fade_effect: QGraphicsOpacityEffect | None = None
        self._connection_fade = QVariantAnimation(self)
        self._connection_fade.valueChanged.connect(self._set_connection_opacity)
        self._connection_fade.finished.connect(self._advance_connection_transition)
        # Connected: the found clip's ending plays out before the Mode page (design ticket
        # 第 2 条). The guard hands over anyway if the clip never reports its end (stalled
        # decoder, window minimised mid-ending) so the connection page can never stick.
        self._connection_finale_waiting = False
        self._connection_finale_guard = QTimer(self)
        self._connection_finale_guard.setSingleShot(True)
        self._connection_finale_guard.timeout.connect(self._connection_finale_done)
        self._reduce_motion = system_reduces_motion()
        self._language_buttons: dict[str, QPushButton] = {}
        self._language_switch: LanguageSwitch | None = None  # the last change, so rebuilt keys can finish its animation
        self._voice_setup_pending = None
        self._voice_setup_dialog = None
        self._selected_control_id: str | None = None
        self._editing_profile_id: int | None = None
        self._mode_saved_at = -1e9          # when the last write was read back ("已保存" for a few seconds)
        self._mode_seen_connected: bool | None = None   # the Mode page's last known connection
        self._mode_link: Link | None = None  # the connect / disconnect animation now running (06)
        self._mode_overview_open = False    # 全部方案 stays open across page rebuilds
        self._mode_keep_timer = QTimer(self)  # keeps an offline key edit once typing pauses
        self._mode_keep_timer.setSingleShot(True)
        self._mode_keep_timer.setInterval(MODE_OFFLINE_KEEP_MS)
        self._mode_keep_timer.timeout.connect(self._mode_keep_offline_edit)
        self._mode_last_write_state = None
        self._configuration_mode: str | None = None
        self._configuration_identity: tuple[str, str] | None = None
        self._mode_control_selection: dict[str, str | None] = {}
        # 进「设置与维护」先落在第一组；有更新提醒把人带过来时才落在「更新」。
        self._settings_section = "device"
        self._system_permissions = desktop_permissions.SystemPermissions()
        self._screen_icon_drafts: dict[tuple[str, str], ScreenIconDraft] = {}
        self._screen_glyph_drafts: dict[tuple[str, str, str], GlyphDraft] = {}
        self._suppress_mapping_edit_restore = False
        self._pending_mapping_editing_state: _MappingEditingState | None = None
        # Keep the most expensive editor across navigation. Its inputs are
        # compared on re-entry so device/library/status changes still refresh it.
        self._prompt_editor: PromptLibraryEditor | None = None
        self._prompt_editor_inputs: tuple | None = None
        self._inactive_prompt_host = QWidget(self)
        self._inactive_prompt_host.hide()
        self._cached_preferences_page = None
        self._inactive_preferences_host = QWidget(self)
        self._inactive_preferences_host.hide()
        self._compact_mode = False
        self.setWindowTitle(
            _translated_application_display_name(
                self._build_identity, self._language_manager
            )
        )
        self._bounds_screen = None
        self._screen_tracking_connected = False
        # design decision: 最小和默认打开都是 960×640（design decision 按这个尺寸量每一页）。
        self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
        self.resize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
        # macOS keeps the system title-bar buttons; other platforms need a
        # frameless window for per-pixel translucency and draw their own.
        self._native_window_controls = enable_native_window_controls(self)
        if not self._native_window_controls:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(application_stylesheet())
        self._build_application_menu()

        root = _DottedRoot(objectName="root")
        root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if self._native_window_controls:
            root.setAttribute(
                Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False
            )
        root.setAutoFillBackground(False)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        console_frame = QFrame(objectName="consoleFrame")
        console_layout = QVBoxLayout(console_frame)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(0)
        self._window_chrome = _WindowChrome(
            self, native_controls=self._native_window_controls
        )
        console_layout.addWidget(self._window_chrome)
        self._top_navigation = self._build_top_navigation()
        console_layout.addWidget(self._top_navigation)

        self._agent_focus_notice = QFrame(objectName="agentFocusNotice")
        focus_notice_row = QHBoxLayout(self._agent_focus_notice)
        focus_notice_row.setContentsMargins(26, 4, 26, 4)
        self._agent_focus_message = QLabel(objectName="agentFocusMessage")
        self._agent_focus_message.setWordWrap(True)
        focus_notice_row.addWidget(self._agent_focus_message, 1)
        focus_settings = QPushButton("查看状态键设置", objectName="agentFocusSettings")
        focus_settings.clicked.connect(self._open_agent_focus_settings)
        focus_notice_row.addWidget(focus_settings)
        dismiss_focus = QPushButton("关闭提示", objectName="agentFocusDismiss")
        dismiss_focus.clicked.connect(self._agent_focus_notice.hide)
        focus_notice_row.addWidget(dismiss_focus)
        self._agent_focus_notice.hide()
        console_layout.addWidget(self._agent_focus_notice)

        self._connection_area = QWidget(objectName="connectionArea")
        connection_layout = QVBoxLayout(self._connection_area)
        connection_layout.setContentsMargins(26, 0, 26, 4)
        connection_layout.setSpacing(4)
        connection_row = QHBoxLayout()
        self._connection_message = QLabel(objectName="connectionMessage")
        self._connection_message.setWordWrap(True)
        connection_row.addWidget(self._connection_message, 1)
        self._connection_candidates = QComboBox(objectName="connectionCandidates")
        connection_row.addWidget(self._connection_candidates)
        self._connect_selected = QPushButton("读取所选设备", objectName="secondary")
        self._connect_selected.clicked.connect(
            lambda: self._view_model.connect_candidate(self._connection_candidates.currentData())
        )
        connection_row.addWidget(self._connect_selected)
        self._connection_retry = QPushButton("重新连接", objectName="connectionRetry")
        self._connection_retry.setProperty("buttonRole", "primary")
        self._connection_retry.clicked.connect(self._view_model.refresh)
        connection_row.addWidget(self._connection_retry)
        self._connection_details_toggle = QPushButton("连接详情", objectName="connectionDetailsToggle")
        self._connection_details_toggle.setCheckable(True)
        connection_row.addWidget(self._connection_details_toggle, 0, Qt.AlignRight)
        connection_layout.addLayout(connection_row)
        self._connection_details = QPlainTextEdit(objectName="connectionDetails")
        self._connection_details.setReadOnly(True)
        self._connection_details.setMaximumHeight(110)
        self._connection_details.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._connection_details.hide()
        self._connection_details_toggle.toggled.connect(self._connection_details.setVisible)
        connection_layout.addWidget(self._connection_details)
        console_layout.addWidget(self._connection_area)

        self._page_area = QWidget()
        self._page_layout = QVBoxLayout(self._page_area)
        self._page_layout.setContentsMargins(0, 0, 0, 0)
        self._page_hosts: dict[str, QWidget] = {}
        self._content_page_key = None
        self._content = QWidget(self._page_area)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(26, 8, 26, 18)
        self._content.hide()
        console_layout.addWidget(self._page_area, 1)
        self._calibration_page = CalibrationPage(
            self._view_model, self._language_manager, dialog_parent=self,
            settings_rail=self._settings_rail,
            wall_workspace=_wall_workspace,
        )
        self._state_view = StatePage(self._view_model, home_button=self._onboarding_home_button)
        self._sequences_page = SequencesPage(
            self._view_model, self._language_manager, dialog_parent=self,
            rerender=lambda: self.render(self._view_model.model),
            write_transaction_card=self._write_transaction_card,
            state_page=self._state_view.build,
        )
        self._firmware_page = FirmwarePage(
            self._view_model, self._language_manager, window=self,
            build_identity=self._build_identity,
            reveal_update_notice=lambda: self._firmware_update_ui.reveal(),
            quick_viewport=lambda: getattr(self, "_boring_mist_quick_viewport", None),
            factory_reset_available=self._factory_reset_available,
            settings_rail=self._settings_rail,
            wall_workspace=_wall_workspace,
        )
        # One-line notices float over the page's top right corner (views/ui/banner.py).
        self._banners = BannerStack(self._page_area)
        # 灯光与反馈's save banner: one for the window, under the tabs on the
        # right, kept while switching Settings groups (灯光页 v0.4 第 5 张).
        self._save_banner = SaveBanner(self._page_area)
        self._save_banner.hide()
        self._save_banner.save_requested.connect(
            lambda: self._with_preferences_editor(self._lighting_page.save_and_prepare))
        self._save_banner.retry_requested.connect(
            lambda: self._with_preferences_editor(self._lighting_page.save_and_prepare))
        self._save_banner.save_draft_requested.connect(
            lambda: self._with_preferences_editor(self._lighting_page.save))
        self._save_banner.confirm_requested.connect(self._confirm_banner_write)
        self._save_banner.cancel_requested.connect(self._view_model.cancel_device_write_confirmation)
        self._save_banner.details_requested.connect(self._show_save_details)
        self._save_banner.resized.connect(self._place_save_banner)
        self._save_banner.shown_changed.connect(self._place_save_banner)
        self._save_banner_follower = _FollowResize(self._page_area, self._place_save_banner_soon)
        self._prompts_page = PromptsPage(
            self._view_model,
            content=lambda: self._content,
            permissions=lambda: self._system_permissions,
            background_message=lambda: (self._background_controller.runtime_message
                                        if self._background_controller is not None else None),
        )
        self._lighting_page = LightingPage(
            self._view_model, self._language_manager, window=self,
            save_banner=self._save_banner,
            settings_rail=self._settings_rail,
            wall_workspace=_wall_workspace,
            state_page=self._state_page,
            screen_icon_draft_for=self._screen_icon_draft_for,
            screen_glyph_drafts=self._screen_glyph_drafts,
            prepare_device_write=self._prepare_device_write,
        )
        self._connection_progress = None
        self._connection_progress_layout = console_layout
        self._update_notice_area = QWidget(objectName="updateNoticeArea")
        self._update_notice_area.setMinimumHeight(44)
        self._update_notice_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._update_notice_area.setStyleSheet("#updateNoticeArea QPushButton { min-height: 24px; max-height: 28px; padding: 2px 8px; }")
        notice_layout = QHBoxLayout(self._update_notice_area)
        notice_layout.setContentsMargins(26, 2, 26, 6)
        notice_layout.setSpacing(16)
        self._desktop_update_footer = QWidget(objectName="desktopUpdateFooter")
        self._desktop_update_footer.hide()
        notice_layout.addWidget(self._desktop_update_footer, 1)
        self._firmware_update_footer = QWidget(objectName="firmwareUpdateFooter")
        self._firmware_update_footer.hide()
        notice_layout.addWidget(self._firmware_update_footer, 1)
        console_layout.addWidget(self._update_notice_area)
        # With no notice the strip takes no height at all (design decision: at 960×640 the page
        # gets 524 instead of 480); a notice brings it back as before.
        self._update_notice_watch = _CollapseWhenEmpty(
            self._update_notice_area, (self._desktop_update_footer, self._firmware_update_footer))
        root_layout.addWidget(console_frame)
        self.setCentralWidget(root)
        self._macos_vibrancy_enabled = False
        self._showing_full_screen_look = False
        self._connection_terminal = ConnectionTerminal(self, reduce_motion=self._reduce_motion)
        self._connection_terminal.hide()

        view_model.changed.connect(self.render)
        view_model.normal_agent.changed.connect(self._refresh_normal_agent_controls)
        view_model.changed.connect(self._refresh_normal_agent_controls)
        view_model.diagnostics_changed.connect(self._refresh_battery_summary)
        view_model.connection_steps_changed.connect(self._refresh_mode_steps)
        view_model.lighting_preview_changed.connect(
            self._lighting_page.preview_status_changed
        )
        self._language_manager.language_changed.connect(self._language_changed)
        if self._background_controller is not None:
            self._background_controller.state_changed.connect(
                self._background_state_changed
            )
            self._background_controller.codex_usage_changed.connect(self._refresh_mode_quota)
            self._background_controller.claude_usage_changed.connect(self._refresh_mode_quota)
        self.render(view_model.model)
        from controller_config.views.firmware_reminder import FirmwareReminderUi
        self._firmware_update_ui = FirmwareReminderUi(self)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            if not self._screen_tracking_connected:
                handle.screenChanged.connect(self._watch_window_screen)
                self._screen_tracking_connected = True
            self._watch_window_screen(handle.screen())
        if self._native_window_controls:
            refresh_native_window_controls(self)
            position_native_window_controls(self)
        QTimer.singleShot(0, self._install_macos_vibrancy)

    def _watch_window_screen(self, screen) -> None:
        if self._bounds_screen is not screen:
            if self._bounds_screen is not None:
                self._bounds_screen.availableGeometryChanged.disconnect(self._fit_window_to_available_area)
            self._bounds_screen = screen
            screen.availableGeometryChanged.connect(self._fit_window_to_available_area)
        self._fit_window_to_available_area(screen.availableGeometry())

    def _fit_window_to_available_area(self, area) -> None:
        # Qt screen geometry is in logical pixels and excludes the taskbar/Dock.
        self.setMinimumSize(min(MINIMUM_WINDOW_WIDTH, area.width()), min(MINIMUM_WINDOW_HEIGHT, area.height()))
        # Entering full screen, Qt still says "normal" while AppKit already has
        # the window at screen size; moving it then fights the animation.
        if self.isMaximized() or self.isFullScreen() or macos_window_full_screen(self):
            return
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(
            max(area.left(), min(self.x(), area.right() - self.width() + 1)),
            max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
        )

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and hasattr(self, "_macos_vibrancy_enabled")
        ):
            # Entering or leaving the system full screen changes the corners.
            self._install_macos_vibrancy()
        elif event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowTitleChange,
        ):
            self._place_native_window_controls()

    def _place_native_window_controls(self) -> None:
        # The system puts its buttons back whenever it lays the title bar out.
        if getattr(self, "_native_window_controls", False):
            position_native_window_controls(self)

    def _full_screen_look(self) -> bool:
        # AppKit's own flag turns as each full-screen animation starts; Qt's
        # isFullScreen() only once it has ended.
        native = macos_window_full_screen(self)
        return self.isFullScreen() if native is None else native

    def _follow_full_screen_animation(self) -> None:
        """Called on resize: AppKit resizes the window as it starts entering or
        leaving full screen, shows the frame Qt draws for that resize and no
        later one until the animation ends. So the new look and every layout
        still waiting in the queue must land in this same frame."""
        if not hasattr(self, "_macos_vibrancy_enabled"):
            return
        native = macos_window_full_screen(self)
        if native is None:
            return
        if native != self._showing_full_screen_look:
            self._install_macos_vibrancy()
        if native != self.isFullScreen():
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
            # The shared 3D device view follows its canvas on a 50 ms timer
            # (kept for live resize); here that would leave it at the old
            # size and place for the whole animation.
            canvas = getattr(self, "_boring_mist_model_owner", None)
            sync = getattr(canvas, "_sync_viewport_geometry", None)
            if sync is not None and isValid(canvas):
                sync()

    def _install_macos_vibrancy(self) -> None:
        if self._native_window_controls:
            refresh_native_window_controls(self)
            position_native_window_controls(self)
        # A full-screen window has no desktop behind its corners, so the
        # rounded window shape would only leave four black wedges.
        full_screen = self._full_screen_look()
        radius = 0.0 if full_screen else float(V4_RADIUS)
        installed = install_macos_vibrancy(
            self,
            corner_radius=radius,
        )
        # Nor any desktop to show through the glass: the console's own base
        # colour fills the screen instead of a wallpaper-tinted one.
        set_macos_window_backdrop(self, tokens.SURFACE_BASE if full_screen else None)
        self._macos_vibrancy_enabled = installed and not full_screen
        self._showing_full_screen_look = full_screen
        self.centralWidget().set_corner_radius(radius)
        frame = self.findChild(QFrame, "consoleFrame")
        frame.setProperty("nativeGlass", self._macos_vibrancy_enabled)
        frame.setProperty("squareCorners", radius == 0)
        frame.style().unpolish(frame)
        frame.style().polish(frame)
        self.centralWidget().update()

    def _show_ai_setup(self) -> None:
        if self._ai_setup_dialog is not None:
            self._ai_setup_dialog.show()
            self._ai_setup_dialog.raise_()
            self._ai_setup_dialog.activateWindow()
            return
        if not self._confirm_leave_mapping_editor():
            return
        if self._onboarding_settings is not None:
            self._onboarding_settings.setValue(AI_SETUP_STARTED_KEY, True)
        dialog = AISetupDialog(
            self._view_model, self._onboarding_settings, self,
            open_guide=self._show_onboarding,
        )
        self._ai_setup_dialog = dialog
        def finished(_result):
            if not dialog._applying:
                self._ai_setup_dialog = None
            self.render(self._view_model.model)
        dialog.finished.connect(finished)
        dialog.open()

    def _show_onboarding(self) -> None:
        if self._onboarding_dialog is not None:
            self._onboarding_dialog.raise_()
            self._onboarding_dialog.activateWindow()
            return
        dialog = OnboardingDialog(self)
        dialog.page_requested.connect(self._open_onboarding_page)
        self._language_manager.language_changed.connect(dialog.set_language)
        self._onboarding_dialog = dialog
        dialog.finished.connect(self._onboarding_finished)
        dialog.open()

    def _onboarding_home_button(self, *, text_entry: bool = False) -> QPushButton:
        label = self._language_manager.translate("开机与操作说明" if text_entry else "设备操作演示")
        button = QPushButton(label if text_entry else "", objectName="openOnboardingGuide")
        button.setProperty("textEntry", text_entry)
        button.setAccessibleName(label)
        button.setToolTip(label)
        button.setIcon(
            _navigation_icon(
                "book.closed",
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
                white=0.9,
            )
        )
        button.setIconSize(QSize(16, 16))
        if text_entry:
            button.setProperty("buttonRole", "ghost")
        else:
            button.setFixedSize(24, 24)
        button.clicked.connect(self._show_onboarding)
        return button

    def _open_onboarding_page(self, page: str) -> None:
        model = self._view_model
        if model.firmware_update.blocks_editing or model.calibration.blocks_editing:
            notice.notify(self, translate_ui_text("使用引导"),
                                    translate_ui_text("请先完成当前固件维护或校准，再打开配置页面。"), state="wait")
            return
        if page == "lighting" and model.draft is None:
            notice.notify(self, translate_ui_text("使用引导"),
                                    translate_ui_text("连接设备并读取配置后，即可调整外观与反馈。"), state="wait")
            return
        self._navigate(page)

    def _onboarding_finished(self, _result: int) -> None:
        dialog = self._onboarding_dialog
        if self._onboarding_settings is not None:
            self._onboarding_settings.setValue(ONBOARDING_COMPLETED_KEY, True)
            self._onboarding_settings.sync()
        self._onboarding_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._place_native_window_controls()
        if not hasattr(self, "_content"):
            return
        switched = False
        for button in self._nav_buttons.values():
            if button.property("compactNavigation") != (self.width() < 1100):
                button.setProperty("compactNavigation", self.width() < 1100)
                button.set_active(button.property("active") is True)
                switched = True
        if switched:
            # The window now opens below 1100 (design decision), so growing it is common:
            # lay the tabs out at once instead of a few frames later, half out of the capsule.
            self._nav_buttons["overview"].parentWidget().layout().activate()
            self._top_navigation.layout().activate()
        compact = self.width() < 820
        self._apply_compact_mode(compact)
        self._follow_full_screen_animation()

    def _apply_compact_mode(self, compact: bool) -> None:
        if compact == self._compact_mode:
            return
        self._compact_mode = compact
        self._shell_edition.setVisible(not compact)
        margins = (16, 8, 16, 12) if compact else (26, 8, 26, 18)
        for host in self._page_hosts.values():
            host.layout().setContentsMargins(*margins)
        self._banners.set_insets(right=margins[2])
        self._place_save_banner()

    # -- 灯光与反馈 save banner ------------------------------------------------

    def _save_banner_right(self) -> int:
        """Where the banner's right edge goes, in page-area coordinates.

        Lined up with the wall's right edge (-08 答复第 4 件, Agent's reading: the
        top-right block it used to line up with is gone with top bar v1). Pages
        without a wall (Mode) keep the page margin.
        """
        page = self._content
        if page is not None and isValid(page) and page.isVisible():
            walls = [wall for wall in page.findChildren(SettingsWall)
                     if wall.isVisible() and wall.width() > 0
                     and not any(isinstance(parent, SettingsWall) for parent in self._widget_ancestors(wall, stop=page))]
            if walls:
                return max(wall.mapTo(self._page_area, QPoint(wall.width(), 0)).x() for wall in walls)
        return self._page_area.width() - (16 if self._compact_mode else 26)

    def _place_save_banner_soon(self, *_args) -> None:
        """Once the page has laid itself out (the wall moves after the window does).

        The timer belongs to the window, so it never fires after the window is gone.
        """
        QTimer.singleShot(0, self, self._place_save_banner)

    def _place_save_banner(self, *_args) -> None:
        """Top 90 from the window top (design decision), right edge on the wall's right edge."""
        banner = self._save_banner
        right = self._save_banner_right()
        top = max(0, SAVE_BANNER_TOP - self._page_area.mapTo(self, QPoint(0, 0)).y())
        banner.place(max(0, right - banner.width()), top)
        banner.raise_()
        # Other notices queue under it while it shows.
        # Every banner on the right starts 90 from the window top (design decision item 7).
        self._banners.set_insets(top=top + banner.height() + 8 if banner.is_shown() else top)

    def _sync_save_banner(self) -> None:
        """Unsaved lighting keeps its banner on every tab, Mode and Prompt
        included (the designer: 页签下面那一条要保留, design decision item 6)."""
        page = self._cached_preferences_page
        if page is not None and hasattr(page, "refresh_summary"):
            page.refresh_summary()
        else:
            self._save_banner.apply(SaveBannerKind.HIDDEN)
        self._place_save_banner_soon()   # another page, another wall

    def _with_preferences_editor(self, action) -> None:
        page = self._cached_preferences_page
        editor = page.findChild(PreferencesEditor) if page is not None else None
        if editor is not None:
            action(editor)

    def _confirm_banner_write(self) -> None:
        """The banner's ② already asks 确认写入设备？, so no second question here."""
        try:
            self._view_model.confirm_device_write()
        except ValueError as exc:
            notice.alert(self, "无法写入设备", str(exc))

    def _show_save_details(self) -> None:
        """查看原因: the reason and the actions of the write transaction card."""
        snapshot = self._view_model.model.snapshot
        draft = self._view_model.draft
        if snapshot is None or draft is None:
            return
        base_changed = snapshot.config_result.get("generation") != draft.base_generation
        dialog = Dialog(self, "保存到设备", width=MEDIUM, object_name="saveDetailsDialog")
        card = self._write_transaction_card(snapshot, base_changed, show_prepare=False)
        card.layout().setContentsMargins(0, 0, 0, 0)
        for label in card.findChildren(QLabel):
            label.setWordWrap(True)
        dialog.body_layout().addWidget(card)
        self._add_diagnosis_entry(dialog)
        self._language_manager.retranslate_widget_tree(card)
        self._view_model.changed.connect(dialog.reject)  # the state moved on: the card is stale
        dialog.open()

    def _build_application_menu(self) -> None:
        settings_menu = self.menuBar().addMenu(
            self._language_manager.translate("设置")
        )
        settings_menu.setObjectName("settingsMenu")
        self._settings_menu = settings_menu
        quit_action = QAction(
            self._language_manager.translate("退出 BORING 控制台"), self
        )
        quit_action.setObjectName("quitApplicationAction")
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        # Replace Qt's native macOS Quit action, which otherwise bypasses our
        # closeEvent draft/transaction checks and only emits aboutToQuit.
        quit_action.triggered.connect(
            self._background_controller.quit_application
            if self._background_controller is not None
            else self.close
        )
        settings_menu.addAction(quit_action)
        self._quit_action = quit_action
        language_menu = settings_menu.addMenu(
            self._language_manager.translate("语言")
        )
        language_menu.setObjectName("languageMenu")
        self._language_menu = language_menu
        self._language_actions = QActionGroup(self)
        self._language_actions.setExclusive(True)
        for language in SUPPORTED_LANGUAGES:
            label = _LANGUAGE_LABELS[language]
            action = QAction(label, self)
            action.setObjectName(f"languageAction_{language}")
            action.setCheckable(True)
            action.setChecked(language == self._language_manager.language)
            action.setData(language)
            action.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            action.triggered.connect(
                lambda _checked=False, value=language: self._language_manager.set_language(value)
            )
            self._language_actions.addAction(action)
            language_menu.addAction(action)
        helper_menu = settings_menu.addMenu("主机自动化助手")
        helper_menu.setObjectName("promptHelperMenu")
        background_action = QAction("关闭窗口后在菜单栏运行", self)
        background_action.setObjectName("promptHelperBackgroundAction")
        background_action.setCheckable(True)
        background_action.setEnabled(self._background_controller is not None)
        background_action.setChecked(
            self._background_controller.background_enabled
            if self._background_controller is not None
            else False
        )
        if self._background_controller is not None:
            background_action.toggled.connect(
                self._background_controller.set_background_enabled
            )
        helper_menu.addAction(background_action)
        login_action = QAction("登录后自动启动", self)
        login_action.setObjectName("promptHelperLoginAction")
        login_action.setCheckable(True)
        login_action.setEnabled(self._background_controller is not None)
        login_action.setChecked(
            self._background_controller.login_enabled
            if self._background_controller is not None
            else False
        )
        if self._background_controller is not None:
            login_action.toggled.connect(self._set_prompt_helper_login)
        helper_menu.addAction(login_action)

    def _set_prompt_helper_login(self, enabled: bool) -> None:
        if self._background_controller is None:
            return
        try:
            self._background_controller.set_login_enabled(enabled)
        except (OSError, ValueError) as exc:
            action = self.findChild(QAction, "promptHelperLoginAction")
            if action is not None:
                with QSignalBlocker(action):
                    action.setChecked(self._background_controller.login_enabled)
            notice.notify(
                self,
                self._language_manager.translate("无法修改登录启动"),
                self._language_manager.translate(str(exc)),
            )

    def _language_changed(self, language: str) -> None:
        application = QApplication.instance()
        if isinstance(application, QApplication):
            display_name = _translated_application_display_name(
                self._build_identity, self._language_manager
            )
            application.setApplicationDisplayName(display_name)
            self.setWindowTitle(display_name)
        for action in self._language_actions.actions():
            action.setChecked(action.data() == language)
        self._settings_menu.setTitle(self._language_manager.translate("设置"))
        self._language_menu.setTitle(self._language_manager.translate("语言"))
        self._quit_action.setText(
            self._language_manager.translate("退出 BORING 控制台")
        )
        self.render(self._view_model.model)
        # Retranslation changes each caption's size hint. Apply those widths
        # before an update offer changes the settings caption, otherwise the
        # whole navigation can jump when Qt finally lays out the translated
        # buttons during that unrelated state change.
        for button in self._nav_buttons.values():
            button.set_active(button.property("active") is True)

    def _build_top_navigation(self) -> QWidget:
        navigation = QFrame(objectName="topNavigation")
        navigation.setFixedHeight(62)
        layout = QHBoxLayout(navigation)
        layout.setContentsMargins(26, 4, 26, 4)
        layout.setSpacing(18)

        # 顶条 v1 定稿：BORING CONSOLE 排成一行，点放大到原始的 1.5 倍（直径 3、点距 4.5）。
        brand_box = QWidget()
        brand_layout = QHBoxLayout(brand_box)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(BRAND_WORD_GAP)
        brand = Boring5RLabel("BORING", scale=BRAND_SCALE, color=tokens.BRAND_WORD,
                              objectName="shellBrand")
        self._shell_edition = Boring5RLabel("CONSOLE", scale=BRAND_SCALE, color=tokens.BRAND_EDITION,
                                            objectName="shellEdition")
        brand_layout.addWidget(brand)
        brand_layout.addWidget(self._shell_edition)
        # 左右两侧各占一半剩余宽度，页签那一条才会正好在窗口正中，
        # 不随右上角设备信息的字数（换语言时会变）左右移动。
        left_side = QWidget(objectName="topNavigationLeft")
        left_side.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left_layout = QHBoxLayout(left_side)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(brand_box, 0, Qt.AlignVCenter)
        left_layout.addStretch(1)
        layout.addWidget(left_side, 1)

        entries = [
            ("overview", NAV_LABELS["overview"], "nav.mode", QStyle.StandardPixmap.SP_ComputerIcon, True),
            ("prompts", NAV_LABELS["prompts"], "nav.prompt", QStyle.StandardPixmap.SP_MessageBoxInformation, True),
            ("settings", NAV_LABELS["settings"], "nav.settings", QStyle.StandardPixmap.SP_FileDialogDetailedView, True),
        ]
        capsule = _NavigationCapsule(objectName="navigationCapsule")
        nav_layout = QHBoxLayout(capsule)
        nav_layout.setContentsMargins(4, 4, 4, 4)
        nav_layout.setSpacing(2)
        for index, (page, label, symbol_name, fallback_icon, enabled) in enumerate(entries, start=1):
            if page == "settings":
                separator = QFrame(objectName="navigationSeparator")
                separator.setFixedSize(1, 20)
                nav_layout.addSpacing(6)
                nav_layout.addWidget(separator)
                nav_layout.addSpacing(6)
            button = _TopNavigationButton("", objectName="navIconButton")
            fallback = self.style().standardIcon(fallback_icon)
            button.set_navigation_icons(
                active=_navigation_icon(symbol_name, fallback, white=0.97),
                inactive=_navigation_icon(symbol_name, fallback, white=0.58),
            )
            button.setIconSize(QSize(20, 20))
            # 三个页签在中文、英文、日文下都显示同一个英文名，不跟着语言变。
            button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            button.setAccessibleName(label)
            button.setAccessibleDescription(self._language_manager.translate("切换界面"))
            button.set_active(page == "overview")
            button.setEnabled(enabled)
            if page == "settings":
                button.setProperty("settingsDestination", True)
                button.clicked.connect(self._activate_settings_navigation)
                button.setContextMenuPolicy(Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(self._settings_navigation_menu)
            else:
                button.clicked.connect(
                    lambda _checked=False, target=page: self._navigate(target)
                )
            self._nav_buttons[page] = button
            nav_layout.addWidget(button)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            if page == "settings":
                shortcut.activated.connect(lambda: self._navigate("settings") if self._nav_buttons["settings"].isEnabled() else None)
            else:
                shortcut.activated.connect(lambda button=button: button.click() if button.isEnabled() else None)
        layout.addWidget(capsule, 0, Qt.AlignCenter)

        # 顶条 v1 定稿：右边只剩设备状态，装在一个小胶囊里（BORING MIST 和“当前模式”一行删掉）。
        self._device_status = DeviceStatusCapsule()
        self._device_auth_summary = self._device_status.label
        device_summary = self._device_status
        right_side = QWidget(objectName="topNavigationRight")
        right_side.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        right_layout = QHBoxLayout(right_side)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch(1)
        right_layout.addWidget(device_summary, 0, Qt.AlignVCenter)
        layout.addWidget(right_side, 1)
        return navigation

    def _refresh_language_buttons(self) -> None:
        for language, button in self._language_buttons.items():
            button.setProperty("active", language == self._language_manager.language)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _choose_language(self, language: str, origin: QPointF) -> None:
        """A press on a language key: remember it for the animation, then switch."""
        current = self._language_manager.language
        if language == current:
            return
        self._language_switch = LanguageSwitch(current, language, origin, time.monotonic())
        self._language_manager.set_language(language)

    def _confirm_leave_page(self, *, for_exit: bool = False) -> bool:
        prompt_editor = self._content.findChild(PromptLibraryEditor)
        if prompt_editor is not None:
            if for_exit and not prompt_editor.confirm_leave():
                return False
            if not for_exit and prompt_editor.has_unsaved_fields() and not prompt_editor._save():
                return False
        if (not for_exit and self._view_model.page == "overview"
                and self._mapping_editor_has_uncommitted_changes()):
            if not self._save_mapping_editor_draft():
                return False
        editor = self._content.findChild(PreferencesEditor)
        draft = self._view_model.draft
        if editor is not None and draft is not None and editor.values() != (
            draft.config["lighting"], draft.config["haptic"], draft.config["display"]
        ):
            if for_exit:
                choice = confirm_local_draft(
                    self, "外观修改尚未应用",
                    "保留草稿可在本次使用中继续编辑，尚未应用到设备。退出控制台前需导出草稿。",
                )
                if choice == QMessageBox.Discard:
                    self._cached_preferences_page.setProperty("discardEdits", True)
                    # Show the draft at once. If a later check stops the exit, the page
                    # must not keep the discarded edits on screen, where leaving the page
                    # would put them back into the draft.
                    self.render(self._view_model.model)
                    return True
                if choice != QMessageBox.Save:
                    return False
            return self._lighting_page.save(editor)
        return True

    def _activate_settings_navigation(self) -> None:
        button = self._nav_buttons["settings"]
        updater_ui = getattr(self, "_desktop_update_ui", None)
        if button.property("desktopUpdateState") and updater_ui is not None:
            updater_ui.activate_navigation()
        else:
            self._select_settings_section(self._settings_landing_section())

    def _settings_landing_section(self) -> str:
        """点一级导航进 Settings（设置与维护）时落在哪一组。

        有固件更新提醒、或设备在固件维护里（这时只有「更新」能点）才落在「更新」；
        其余时候落在第一组，和左边图标的顺序对上。电脑软件的更新提醒走 activate_navigation。
        """
        button = self._nav_buttons["settings"]
        if (button.property("firmwareUpdateAvailable")
                or self._view_model.model.state is AppState.FIRMWARE_MAINTENANCE):
            return "updates"
        return "device"

    def _settings_navigation_menu(self, point) -> None:
        button = self._nav_buttons["settings"]
        menu = QMenu(button)
        menu.addAction(
            self._language_manager.translate("打开 Settings"),
            lambda: self._select_settings_section(self._settings_landing_section()),
        )
        if button.property("firmwareUpdateAvailable"):
            menu.addAction(self._language_manager.translate("查看固件更新"), self._firmware_update_ui.open_update)
        updater_ui = getattr(self, "_desktop_update_ui", None)
        if button.property("desktopUpdateState") and updater_ui is not None:
            if updater_ui.status.state == "available":
                menu.addAction(self._language_manager.translate("稍后提醒"), updater_ui.snooze_update)
            elif updater_ui.status.state == "downloading":
                menu.addAction(self._language_manager.translate("取消下载"), updater_ui.updater.cancel)
        menu.exec(button.mapToGlobal(point))
        menu.deleteLater()

    def _navigate(self, page: str) -> None:
        if page == self._view_model.page:
            return
        if not self._confirm_leave_page():
            return
        self._cover_page_transition(page)
        self._set_active_navigation(page)
        self._top_navigation.repaint()
        try:
            self._view_model.navigate(page)
        except ValueError:
            self._clear_page_transition()
            self._set_active_navigation(self._view_model.page)
            raise
        if self._view_model.page == page:
            self._animate_page_transition()
        else:
            self._clear_page_transition()
            self._set_active_navigation(self._view_model.page)

    def _set_active_navigation(self, page: str) -> None:
        active_page = page
        if page in {"settings", "firmware", "joystick", "actions", "lighting"}:
            active_page = "settings"
        elif page == "sequences":
            active_page = "overview"
        for name, button in self._nav_buttons.items():
            button.set_active(name == active_page)

    # -- 叠丙 page change (design decision; -12 答复第 3 件补) ---------------------------

    _TAB_ORDER = {"overview": 0, "sequences": 0, "prompts": 1}
    _PAINTED_BLOCKS = frozenset({"modeDevice", "modeQuota"})   # no card: no fill, no shadow

    def _tab_index(self, page: str) -> int:
        return self._TAB_ORDER.get(page, 2)   # every other page sits under Settings

    def _cover_page_transition(self, page: str) -> None:
        """Before the new page is built: hold a still of the page on screen now."""
        self._page_arrival_pending = False
        if (self._reduce_motion or not self.isVisible() or self._content is None
                or not self._content.isVisible()):
            self._clear_page_transition()
            return
        arrival = self._page_arrival
        if arrival is None:
            arrival = self._page_arrival = PageArrival(self._page_area)
            arrival.finished.connect(self._unveil_content)
        if arrival.running():
            old, at = arrival.snapshot(), QPointF(0, 0)   # 7: interrupted, the frame on screen now
        else:
            old = clear_grab(self._content)
            at = QPointF(self._content.mapTo(self._page_area, QPoint(0, 0)))
        tab_before = clear_grab(self._top_navigation)
        direction = 1 if self._tab_index(page) >= self._tab_index(self._view_model.page) else -1
        self._page_arrival_direction = direction
        arrival.cover(old, at, None, direction)
        self._raise_over_page_arrival()
        self._page_arrival_pending = True
        CrossFade(self._top_navigation, tab_before, tokens.MOTION_INSTANT_MS)   # 6: tabs, 瞬时

    def _veil_content(self, page: QWidget) -> None:
        """The real new page stays in place but unseen while the overlay draws the
        change, so it never shows through as a ghost (the designer 11-20 真屏验收)."""
        if self._veiled is not None and self._veiled is not page:
            self._unveil_content()
        if page.graphicsEffect() is None:
            veil = QGraphicsOpacityEffect(page)
            veil.setOpacity(0.0)
            page.setGraphicsEffect(veil)
            self._veiled = page

    def _unveil_content(self) -> None:
        page, self._veiled = self._veiled, None
        if page is not None and isValid(page) and isinstance(page.graphicsEffect(), QGraphicsOpacityEffect):
            page.setGraphicsEffect(None)

    def _raise_over_page_arrival(self) -> None:
        """Banners stay on top of the change; only the page moves."""
        if self._save_banner.isVisible():
            self._save_banner.raise_()
        for banner in self._banners.banners() if hasattr(self._banners, "banners") else ():
            banner.raise_()

    def _animate_page_transition(self) -> None:
        if not self._page_arrival_pending:
            return
        # The new page is laid out on the next turn of the event loop.
        QTimer.singleShot(0, self, self._start_page_arrival)   # owned by the window

    def _start_page_arrival(self) -> None:
        arrival = self._page_arrival
        if arrival is None or not self._page_arrival_pending:
            return
        self._page_arrival_pending = False
        page = self._content
        if page is None or not page.isVisible() or page.findChild(DeviceModelCanvas) is not None:
            arrival.finish()   # the 3D viewport lives outside the page: no motion there
            return
        layout = self._page_area.layout()
        if layout is not None:
            layout.activate()
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        blocks, riders = self._page_arrival_blocks(page)
        veil = page.graphicsEffect() if page is self._veiled else None
        if veil is not None:
            veil.setEnabled(False)   # pictures of the real page, not of its veil
        try:
            rest = hidden_grab(page, [*blocks, *riders])
            origin = QPointF(page.mapTo(self._page_area, QPoint(0, 0)))
            pieces = []
            for widget in blocks:
                rect = QRect(widget.mapTo(self._page_area, QPoint(0, 0)), widget.size())
                own = widget.objectName() not in self._PAINTED_BLOCKS   # these draw themselves, no fill
                pieces.append(Block(rect, clear_grab(widget, own_background=own)))
            for rider in riders:   # a title comes in with the card nearest to it
                rect = QRect(rider.mapTo(self._page_area, QPoint(0, 0)), rider.size())
                nearest = min(pieces, key=lambda piece: (QPointF(piece.rect.center())
                                                         - QPointF(rect.center())).manhattanLength(),
                              default=None)
                if nearest is not None:
                    nearest.riders.append((rect, clear_grab(rider)))
        finally:
            if veil is not None:
                veil.setEnabled(True)
        starts = stagger([(piece.rect.center().x(), piece.rect.top()) for piece in pieces],
                         self._page_arrival_direction)
        for piece, start in zip(pieces, starts):
            piece.start_ms = start
        arrival.play(rest, origin, pieces)
        self._raise_over_page_arrival()

    @staticmethod
    def _page_arrival_blocks(page: QWidget) -> tuple[list[QWidget], list[QWidget]]:
        """The pieces that fall (叠丙 spec item 5) and the titles that ride on them.

        Mode: AI 额度, the device, the bottom bar (the battery and the words beside
        the device are painted on the stage, so they come in with the page);
        Prompt: the status bar and the two tiles; Settings: the rail and each tile.
        Other pages: their top-level cards, or the page as one piece.
        """
        def shown(widget: QWidget) -> bool:
            return widget.isVisible() and widget.width() > 0 and widget.height() > 0

        named = ("modeQuota", "modeDevice", "modeBottomBar", "promptStatusBar", "settingsNavigation")
        blocks = [widget for name in named for widget in page.findChildren(QWidget, name) if shown(widget)]
        tiles = [tile for tile in page.findChildren(WallTile) if shown(tile)]
        blocks += [tile for tile in tiles
                   if not any(isinstance(parent, WallTile) for parent in MainWindow._widget_ancestors(tile, stop=page))]
        if not blocks:
            blocks = [card for card in page.findChildren(V4Card) if shown(card)
                      and not any(isinstance(parent, V4Card) for parent in MainWindow._widget_ancestors(card, stop=page))]
        riders = [label for label in page.findChildren(QLabel, "settingsWallTitle") if shown(label)]
        return blocks, riders

    @staticmethod
    def _widget_ancestors(
        widget: QWidget, *, stop: QWidget
    ) -> Iterator[QWidget]:
        parent = widget.parentWidget()
        while parent is not None and parent is not stop:
            yield parent
            parent = parent.parentWidget()

    def _clear_page_transition(self) -> None:
        if self._page_arrival_pending:
            return   # the page is being rebuilt under the still; it starts in a moment
        if self._page_arrival is not None:
            self._page_arrival.finish()

    def _add_content_page(self, page: QWidget, stretch: int = 0) -> None:
        """Install one fully built page without painting intermediate layouts."""

        updates_enabled = self._content.updatesEnabled()
        if updates_enabled:
            self._content.setUpdatesEnabled(False)
        try:
            self._content_layout.addWidget(page, stretch)
        finally:
            if updates_enabled:
                self._content.setUpdatesEnabled(True)
                self._content.update()

    def _refresh_device_summary(self, model: ScreenModel) -> None:
        self._refresh_battery_summary()
        self._device_status.set_status(_device_status_kind(model))

    def _refresh_battery_summary(self) -> None:
        """The battery on the Mode page follows the diagnostics without a rebuild."""
        stage = self._visible_mode_stage()
        model = self._view_model.model
        if stage is not None and model.snapshot is not None:
            connected = model.state in {AppState.READY, AppState.READ_ONLY}
            stage.set_state(replace(stage.state(), battery=model.snapshot.battery_percent if connected else None))

    def render(self, model: ScreenModel) -> None:
        self._render(model)
        self._sync_save_banner()

    def _render(self, model: ScreenModel) -> None:
        if self._view_model.page != "overview":
            # Away from the Mode page a connection change is just noted: coming back plays nothing.
            self._mode_seen_connected = model.state in {AppState.READY, AppState.READ_ONLY}
            self._mode_link = None
        link_ready = model.state in {AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}
        if link_ready:
            self._last_link_ready = True
        elif model.state in {AppState.DISCONNECTED, AppState.READ_FAILED}:
            if (self._last_link_ready and not self.isActiveWindow()
                    and self._background_controller is not None):
                self._background_controller.notify_failure(
                    "BORING 设备已断开", "设备后台联动已暂停；请重新连接并读取配置。",
                )
            self._last_link_ready = False
        # Keep the last device-confirmed custom mapping even when the next
        # transaction switches back to official dictation. Never retain drafts.
        snapshot = model.snapshot
        if snapshot is not None:
            identity = (snapshot.identity.get("serial"), snapshot.identity.get("hardware_id"))
            if identity != self._configuration_identity:
                self._configuration_identity = identity
                self._editing_profile_id = snapshot.active_profile_id
                reported_mode = snapshot.status.get("operating_mode", "normal")
                self._configuration_mode = (reported_mode if reported_mode in {"normal", "codex", "claude_code"}
                                            and self._configuration_mode_supported(snapshot, reported_mode) else "normal")
                self._mode_control_selection.clear()
        if model.state is AppState.READY and snapshot is not None:
            action = (snapshot.active_profile or {}).get("codex_voice", {})
            if action.get("type") == "key_gesture":
                serial = snapshot.identity.get("serial")
                saved = confirmed_voice_choice(self._onboarding_settings, serial, snapshot.active_profile_id)
                if saved is None or _canonical_action(saved["action"]) != _canonical_action(action):
                    # Reading a key cannot identify the software that uses it.
                    # The guide pairs the provider only after confirmation.
                    save_confirmed_voice_choice(self._onboarding_settings, serial,
                                                snapshot.active_profile_id, action, "")
        if self._defer_connection_completion(model):
            return
        key = self._view_model.page
        if key == "settings":
            key += ":" + self._settings_section
        if key != self._content_page_key:
            if self._content_page_key == "overview":
                self._pending_mapping_editing_state = self._current_mapping_editing_state()
            self._clear_page_transition()
            self._page_layout.removeWidget(self._content)
            self._content.hide()
            if key not in self._page_hosts:
                host = QWidget(self._page_area)
                layout = QVBoxLayout(host)
                layout.setContentsMargins(*( (16, 8, 16, 12) if self._compact_mode else (26, 8, 26, 18) ))
                self._page_hosts[key] = host
            self._content = self._page_hosts[key]
            self._content_layout = self._content.layout()
            self._content_page_key = key
            if self._page_arrival_pending:
                self._veil_content(self._content)   # before it ever paints
            self._page_layout.addWidget(self._content, 1)
            self._content.show()
        updates_enabled = self._content.updatesEnabled()
        if updates_enabled:
            self._content.setUpdatesEnabled(False)
        try:
            self._render_content(model)
        finally:
            if updates_enabled:
                self._content.setUpdatesEnabled(True)
                self._content.update()
        self._maybe_open_voice_setup(model)

    def _defer_connection_completion(self, model: ScreenModel) -> bool:
        if model.state not in {AppState.READY, AppState.READ_ONLY}:
            # Errors and a new connection attempt must never wait behind success.
            self._cancel_connection_transition()
            return False
        if self._connection_transition_model is not None:
            self._connection_transition_model = model
            return True
        progress = self._connection_progress
        if (progress is None or not progress.isVisible() or not self.isVisible()
                or self._reduce_motion or model.snapshot is None):
            return False
        self._clear_page_transition()
        self._connection_transition_model = model
        self._refresh_device_summary(model)
        set_translatable_text(progress.title, "已连接")
        progress.hint.clear()
        if progress.finish_found():
            self._connection_finale_waiting = True
            self._connection_finale_guard.start(CONNECTION_FINALE_GUARD_MS)
        else:
            self._connection_finale_done()
        return True

    def _connection_finale_done(self) -> None:
        """The found clip ended (or the guard ran out): fade the connection page out."""
        self._connection_finale_guard.stop()
        self._connection_finale_waiting = False
        if self._connection_transition_model is None:
            return  # cancelled meanwhile (failure, new attempt, window closed)
        # 中 0.26 s: the settled top view fades into the Mode page's device (动效四档).
        self._run_connection_fade(self._connection_progress, 1.0, 0.0, tokens.MOTION_MEDIUM_MS)

    def _connection_clip_settled(self) -> None:
        if self._connection_finale_waiting:
            self._connection_finale_done()

    def _run_connection_fade(self, target: QWidget, start: float, end: float, duration: int) -> None:
        if self._connection_fade_target is not None:
            self._connection_fade_target.setGraphicsEffect(None)
        self._connection_fade_target = target
        self._connection_fade_effect = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(self._connection_fade_effect)
        self._connection_fade.setDuration(duration)
        self._connection_fade.setStartValue(start)
        self._connection_fade.setEndValue(end)
        self._connection_fade.setEasingCurve(motion.curve_swap())
        self._set_connection_opacity(start)
        self._connection_fade.start()

    def _set_connection_opacity(self, opacity: float) -> None:
        if self._connection_fade_effect is not None:
            self._connection_fade_effect.setOpacity(opacity)
        # The shared Quick viewport is a window-level sibling, outside the page
        # effect. Fade its QML root too, without hiding/recreating its renderer.
        viewport = getattr(self, "_boring_mist_quick_viewport", None)
        if viewport is not None and isValid(viewport):
            root = viewport.rootObject()
            if root is not None:
                root.setProperty("opacity", opacity)

    def _advance_connection_transition(self) -> None:
        model = self._connection_transition_model
        if model is None:
            self._cancel_connection_transition()
            return
        self._connection_transition_model = None
        self._connection_progress.hide()
        self._run_connection_fade(self._page_area, 0.0, 1.0, tokens.MOTION_INSTANT_MS)  # 瞬时 (-12 顺带查到的; was 0.18)
        # Render the latest confirmed data once, using the retained page/editor.
        self.render(model)

    def _cancel_connection_transition(self) -> None:
        self._connection_transition_model = None
        self._connection_finale_waiting = False
        self._connection_finale_guard.stop()
        self._connection_fade.stop()
        if self._connection_fade_target is not None:
            self._set_connection_opacity(1.0)
            self._connection_fade_target.setGraphicsEffect(None)
        self._connection_fade_effect = None
        self._connection_fade_target = None

    def _preview_voice_key(self, control_id: str) -> None:
        canvas = self._content.findChild(DeviceModelCanvas)
        if canvas is not None:
            canvas.preview_key_press(control_id)

    def _open_voice_setup(self, control_id: str, *, mode="normal", candidate_action=None, short_name=None) -> None:
        if self._voice_setup_dialog is not None:
            self._voice_setup_dialog.raise_()
            return
        snapshot = self._view_model.model.snapshot
        if snapshot is None:
            return
        if self._view_model.draft is not None and self._view_model.draft.config.get("active_profile") != snapshot.active_profile_id:
            notice.notify(self, translate_ui_text("设置语音键"),
                                    translate_ui_text("请先保存正在编辑的配置方案，再设置语音输入。"), state="wait")
            return
        action = voice_mapping(snapshot, control_id, mode).get("action", {})
        if (candidate_action or action).get("type") != "key_gesture":
            return
        editor_action = copy.deepcopy(candidate_action or action)
        serial = snapshot.identity.get("serial")
        profile_id = snapshot.active_profile_id
        tried = voice_trial(self._onboarding_settings, serial, profile_id, control_id, action, mode=mode)
        entries = load_device_choices(self._onboarding_settings, serial)
        target = next((key for key, entry in entries.items()
                       if key in AI_BY_KEY and entry.get("profile_id") == profile_id), None)
        if mode == "codex":
            target = "codex"
            saved = confirmed_voice_choice(self._onboarding_settings, serial, profile_id)
            if (action.get("type") != "key_gesture" and saved is not None
                    and _canonical_action(editor_action) == _canonical_action(saved["action"])):
                self._onboarding_settings.setValue(voice_provider_key(serial, profile_id, mode), saved["provider"])
        dialog = VoiceSetupDialog(self, action=action, settings=self._onboarding_settings,
                                  target_key=tried["target"] if tried else target,
                                  view_model=self._view_model, control_id=control_id, mode=mode,
                                  candidate_action=candidate_action, short_name=short_name,
                                  trial_record=tried,
                                  open_device_voice=(self._open_global_voice_setup
                                                     if self._global_voice_available() else None))
        self._voice_setup_dialog = dialog
        dialog.demo.key_pressed.connect(lambda: self._preview_voice_key(control_id))
        def context_matches():
            model = self._view_model.model
            current = model.snapshot
            return (model.state is AppState.READY and current is not None
                    and current.identity.get("serial") == serial
                    and current.active_profile_id == profile_id
                    and _canonical_action(voice_mapping(current, control_id, mode).get("action", {}))
                    == _canonical_action(dialog.action)
                    and not self._view_model.write_transaction.blocks_editing
                    and not self._view_model.firmware_update.blocks_editing
                    and not self._view_model.calibration.blocks_editing)
        def refresh_context(*_):
            dialog.set_context_ready(context_matches())
        def finished(result):
            self._view_model.changed.disconnect(refresh_context)
            if (result == QDialog.Accepted and context_matches()
                    and self._view_model.model.snapshot.status.get("operating_mode", "normal") == mode):
                dialog.commit_provider_choice()
                save_voice_trial(self._onboarding_settings, serial, profile_id, control_id,
                                 dialog.action, dialog.provider.currentData(), dialog.target_key, mode=mode)
                save_confirmed_voice_choice(self._onboarding_settings, serial, profile_id,
                                            dialog.action, dialog.provider.currentData(), mode=mode)
            self._voice_setup_dialog = None
            card = self._current_mapping_card()
            editor = card.findChild(ActionEditor) if card is not None else None
            if editor is not None:
                editor.action_changed.emit()
        def adapted(applied_action):
            nonlocal action, editor_action
            save_confirmed_voice_choice(self._onboarding_settings, serial, profile_id,
                                        applied_action, dialog.provider.currentData(), mode=mode)
            # The retained inspector may still contain the pre-guide shortcut.
            # Update that value after readback without replacing new user edits.
            card = self._current_mapping_card()
            editor = card.findChild(ActionEditor) if card is not None else None
            if (editor is not None and card.property("controlId") == control_id
                    and (card.property("editingMode") or "normal") == mode
                    and _canonical_action(editor.action()) == _canonical_action(editor_action)):
                editor.set_action(applied_action)
            pending = self._pending_mapping_editing_state
            if (pending is not None and pending.control_id == control_id and pending.mode == mode
                    and _canonical_action(pending.action) == _canonical_action(editor_action)):
                self._pending_mapping_editing_state = replace(pending, action=copy.deepcopy(applied_action))
            action = copy.deepcopy(applied_action)
            editor_action = copy.deepcopy(applied_action)
            if mode == "normal" and target is not None and control_id == "key.8":
                current_entries = load_device_choices(self._onboarding_settings, serial)
                entry = current_entries.get(target)
                if entry is not None and entry.get("profile_id") == profile_id:
                    entry["voice_action"] = copy.deepcopy(applied_action)
                    entry["tried"] = False
                    save_device_choices(self._onboarding_settings, serial, current_entries)
            refresh_context()
        self._view_model.changed.connect(refresh_context)
        dialog.action_applied.connect(adapted)
        dialog.finished.connect(finished)
        refresh_context()
        dialog.show()

    def _maybe_open_voice_setup(self, model: ScreenModel) -> None:
        pending = self._voice_setup_pending
        if pending is None:
            return
        state = self._view_model.write_transaction.state
        if state in {ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}:
            self._voice_setup_pending = None
        elif state is ConfigTransactionState.ACTIVE:
            # ACTIVE is emitted just before the model receives its readback.
            # Wait for that confirmed snapshot as well.
            serial, control, expected, mode = pending
            snapshot = model.snapshot
            if (snapshot is not None and model.state is AppState.READY
                    and snapshot.identity.get("serial") == serial
                    and _canonical_action(voice_mapping(snapshot, control, mode).get("action"))
                    == _canonical_action(expected)):
                self._voice_setup_pending = None
                QTimer.singleShot(0, self, lambda: self._open_voice_setup(control, mode=mode))

    def _render_content(self, model: ScreenModel) -> None:
        self._connection_terminal.observe(model)
        self._refresh_connection_area(model)
        # Connecting to a device seen before keeps the page (the Mode page greys and its bar
        # shows the steps, Prompt and Settings only the capsule; design decision). Looking for a
        # device, or a first connection with nothing to show yet, keeps the 3D page as before.
        connecting = (model.state is AppState.SCANNING
                      or (model.state is AppState.CONNECTING and model.snapshot is None))
        if connecting:
            if self._connection_progress is None:
                self._connection_progress = ConnectionProgress(self.centralWidget())
                self._connection_progress.settled.connect(self._connection_clip_settled)
                index = self._connection_progress_layout.indexOf(self._page_area)
                self._connection_progress_layout.insertWidget(index, self._connection_progress, 1)
            self._page_area.hide()
            self._connection_progress.observe(model.state)
            self._connection_progress.show()
            self._refresh_device_summary(model)
            return  # Keep editors and scroll positions intact during the handshake.
        if self._connection_progress is not None:
            self._connection_progress.hide()
        self._page_area.show()
        if model.state is AppState.CONNECTING:
            # The page stays as it is (editors, scroll positions, unsaved words): only the
            # Mode page greys and its bar follows the steps; elsewhere just the capsule.
            for stage in self._content.findChildren(ModeStage):
                self._fill_mode_stage(stage, model.snapshot, model.state, labels=False)
            self._refresh_device_summary(model)
            return
        self._firmware_page.maybe_schedule_remote_install_confirmation(model)
        has_draft = self._view_model.draft is not None
        firmware_blocks = self._view_model.firmware_update.blocks_editing
        calibration_blocks = (self._view_model.calibration.blocks_editing
                              and model.state is not AppState.FIRMWARE_MAINTENANCE)
        self._nav_buttons["overview"].setEnabled(not firmware_blocks and not calibration_blocks
            and model.state is not AppState.FIRMWARE_MAINTENANCE)
        self._nav_buttons["prompts"].setEnabled(
            not firmware_blocks and not calibration_blocks
            and model.state is not AppState.FIRMWARE_MAINTENANCE
        )
        self._nav_buttons["settings"].setEnabled(
            not firmware_blocks and not calibration_blocks
        )
        self._set_active_navigation(self._view_model.page)
        if self._background_controller is not None:
            listener = self._view_model.prompt_device.listener_status
            self._background_controller.set_helper_status(
                online=listener.online,
                state_label=self._language_manager.translate(listener.label),
                message=self._language_manager.translate(listener.message),
            )
        if self._view_model.page == "firmware" and self._firmware_page.update(model):
            # Byte counters and status text change on every chunk. Updating
            # those widgets in place avoids detaching the native scroll view.
            self._refresh_device_summary(model)
            return
        prompt_editing_state: PromptEditingState | None = None
        current_prompt_editor = self._prompt_editor
        if (
            self._view_model.page == "prompts"
            and current_prompt_editor is not None
        ):
            prompt_editing_state = current_prompt_editor.editing_state()

        current_actions_page = self._content.findChild(ActionsPage)
        reuse_actions_page = (
            self._view_model.page == "actions"
            and current_actions_page is not None
            and current_actions_page.device_serial
            == self._view_model.automation_host.serial
        )

        current_preferences_page = self._cached_preferences_page
        current_draft = self._view_model.draft
        macro_values = self._sequences_page.editing_state()
        preferences_values = None
        if (current_preferences_page is not None and current_draft is not None
                and current_preferences_page.property("draftIdentity") == id(current_draft)
                and not current_preferences_page.property("discardEdits")):
            current_editor = current_preferences_page.findChild(PreferencesEditor)
            if current_editor is not None:
                preferences_values = current_editor.values()
                # A local save moves these exact values into the draft. It does
                # not require replacing the editor the user just worked in.
                draft_values = tuple(current_draft.config[key] for key in ("lighting", "haptic", "display"))
                if (preferences_values == draft_values
                        and current_preferences_page.config_values != draft_values):
                    current_preferences_page.config_values = copy.deepcopy(draft_values)
                elif current_preferences_page.config_values != draft_values:
                    preferences_values = tuple(
                        edited if edited != previous else updated
                        for edited, previous, updated in zip(
                            preferences_values, current_preferences_page.config_values, draft_values
                        )
                    )
        restored = self._update_workspace_restore
        if restored:
            self._update_workspace_restore = {}
            if restored.get("mapping"):
                saved_mapping = dict(restored["mapping"])
                saved_mapping["device_identity"] = tuple(saved_mapping["device_identity"])
                mapping_editing_state = _MappingEditingState(**saved_mapping)
            if restored.get("preferences") is not None:
                preferences_values = tuple(restored["preferences"])
            if restored.get("macro") is not None:
                macro_values = restored["macro"]
            if restored.get("prompt"):
                prompt_editing_state = PromptEditingState(**restored["prompt"])
        reuse_preferences_page = (
            not restored
            and not (current_preferences_page is not None and current_preferences_page.property("discardEdits"))
            and self._view_model.page == "lighting"
            and current_preferences_page is not None
            and current_draft is not None
            and current_preferences_page.property("draftIdentity")
            == id(current_draft)
            and model.snapshot is not None
            and current_preferences_page.editor_features == current_draft.features
            and current_preferences_page.config_values == tuple(
                current_draft.config[key] for key in ("lighting", "haptic", "display")
            )
            and current_preferences_page.property("uiLanguage") == self._language_manager.language
        )

        if not restored.get("mapping"):
            mapping_editing_state = (
                None
                if self._suppress_mapping_edit_restore
                else self._current_mapping_editing_state()
            )
        if mapping_editing_state is not None:
            self._pending_mapping_editing_state = mapping_editing_state
        elif (
            not self._suppress_mapping_edit_restore
            and self._view_model.page == "overview"
            and self._pending_mapping_editing_state is not None
            and self._pending_mapping_editing_state.control_id
            == self._selected_control_id
        ):
            mapping_editing_state = self._pending_mapping_editing_state

        page = self._view_model.page
        language = self._language_manager.language
        snapshot = model.snapshot
        simple_inputs = None
        if page == "overview" and snapshot is None:
            simple_inputs = (model.state, language)
        elif page == "settings":
            if self._settings_section in {"system", "ai"}:
                simple_inputs = (self._settings_section, language, self._desktop_update_ui,
                                 firmware_blocks, calibration_blocks)
            else:
                simple_inputs = (self._settings_section, language, model.state,
                                 snapshot.identity if snapshot else None,
                                 (self._view_model.ble_name.result or {}).get("saved_name"),
                                 snapshot.versions if snapshot else None, snapshot.port_name if snapshot else None,
                                 self._factory_reset_available(snapshot), firmware_blocks, calibration_blocks,
                                 self._view_model.remote_firmware, self._view_model.firmware_update,
                                 _planned_tool_capability("joystick", snapshot), self._desktop_update_ui)
        elif page == "sequences":
            # Without a draft this route shows the Mode or state page instead,
            # so it keeps the host's input cache rather than Page.update().
            simple_inputs = self._sequences_page.render_inputs(model)
        if page == "joystick" and not restored and self._calibration_page.update(model):
            self._refresh_device_summary(model)
            return
        if (not restored and simple_inputs is not None and self._content_layout.count()
                and simple_inputs == getattr(self._content, "render_inputs", None)):
            if page == "settings" and self._settings_section == "system":
                self._refresh_language_buttons()
            self._refresh_device_summary(model)
            return
        if simple_inputs is not None:
            self._content.render_inputs = (simple_inputs if page == "settings"
                                           else copy.deepcopy(simple_inputs))
        if reuse_actions_page and not restored:
            current_actions_page.update_prompt_context(*self._action_prompt_context())
            current_actions_page.refresh_catalog()
            self._refresh_device_summary(model)
            return
        if reuse_preferences_page and current_preferences_page.parentWidget() is self._content:
            current_preferences_page.refresh_summary()
            self._lighting_page.preview_status_changed(self._view_model.lighting_preview)
            canvas = current_preferences_page.findChild(DeviceModelCanvas)
            if canvas is not None:
                canvas.set_agent_states(canvas._snapshot_agent_states(model.snapshot))
            self._refresh_device_summary(model)
            return
        if current_preferences_page is not None:
            keep_preferences = (
                current_draft is not None
                and current_preferences_page.property("draftIdentity") == id(current_draft)
                and not current_preferences_page.property("discardEdits")
            )
            if keep_preferences and (self._view_model.page != "lighting" or reuse_preferences_page):
                if current_preferences_page.parentWidget() is self._content:
                    self._content_layout.removeWidget(current_preferences_page)
                    current_preferences_page.hide()
                    current_preferences_page.setParent(self._inactive_preferences_host)
            else:
                self._content_layout.removeWidget(current_preferences_page)
                current_preferences_page.hide()
                current_preferences_page.setParent(None)
                current_preferences_page.deleteLater()
                self._cached_preferences_page = None
        overview_scroll = self._content.findChild(QScrollArea, "overviewScroll")
        if (self._view_model.page == "overview" and overview_scroll is not None
                and model.snapshot is not None):
            self._overview(model.snapshot, model.state,
                           mapping_editing_state=mapping_editing_state, scroll=overview_scroll)
            self._refresh_device_summary(model)
            if overview_scroll.content_changed:
                self._language_manager.retranslate_widget_tree(self._content)
            return
        if self._view_model.page == "firmware" and self._firmware_page.rebuild_in_place(model):
            for button in self._content.findChildren(QPushButton, "settingsGroup"):
                button.setEnabled(not calibration_blocks and
                                  (not firmware_blocks or bool(button.property("selected")))
                                  and (model.state is not AppState.FIRMWARE_MAINTENANCE
                                       or button.property("settingsSection") in {"updates", "firmware"}))
            self._refresh_device_summary(model)
            self._language_manager.retranslate_widget_tree(self._content)
            return
        prompt_inputs = (
            self._prompt_page_inputs(model)
            if self._view_model.page == "prompts" else None
        )
        reuse_prompt_editor = (
            not restored
            and prompt_inputs is not None
            and current_prompt_editor is not None
            and prompt_inputs == self._prompt_editor_inputs
        )
        if reuse_prompt_editor and current_prompt_editor.parentWidget() is self._content:
            self._prompts_page.refresh(current_prompt_editor)
            self._refresh_device_summary(model)
            return
        if current_prompt_editor is not None:
            if self._view_model.page != "prompts" or reuse_prompt_editor:
                if current_prompt_editor.parentWidget() is self._content:
                    self._content_layout.removeWidget(current_prompt_editor)
                    current_prompt_editor.hide()
                    current_prompt_editor.setParent(self._inactive_prompt_host)
            else:
                self._content_layout.removeWidget(current_prompt_editor)
                current_prompt_editor.hide()
                current_prompt_editor.setParent(None)
                current_prompt_editor.deleteLater()
                self._prompt_editor = None
        if reuse_actions_page:
            self._content_layout.removeWidget(current_actions_page)
            current_actions_page.setParent(None)
        # Keep the transcript and its timers while the surrounding page changes.
        self._connection_terminal.setParent(self)
        self._connection_terminal.hide()
        _delete_layout(self._content_layout)
        if self._view_model.page not in {"overview", "settings", "firmware", "joystick", "lighting", "prompts"}:
            self._content_layout.addWidget(self._header(model))
        if self._view_model.page == "prompts":
            prompt_editor = (
                current_prompt_editor
                if reuse_prompt_editor
                else self._prompts_page.build(model)
            )
            self._prompt_editor = prompt_editor
            self._prompt_editor_inputs = prompt_inputs
            self._add_content_page(prompt_editor, 1)
            prompt_editor.show()
            if prompt_editing_state is not None and not reuse_prompt_editor:
                prompt_editor.restore_editing_state(prompt_editing_state)
        elif self._view_model.page == "actions":
            actions_page = (
                current_actions_page
                if reuse_actions_page
                else self._actions_page()
            )
            self._add_content_page(actions_page, 1)
            actions_page.refresh_catalog()
            if restored.get("automation"):
                from controller_config.views.automation import AutomationEditingState

                actions_page.show_developer_lane(0)
                script = actions_page._developer._automation_page
                script.restore_editing_state(AutomationEditingState(**restored["automation"]))
                script._refresh_controls(restored.get("automation_control"))
            if restored.get("workflow"):
                from controller_config.views.workflows import WorkflowEditingState

                actions_page._workflow_page.restore_editing_state(WorkflowEditingState(**restored["workflow"]))
            if "actions_section" in restored:
                if "developer_lane" in restored:
                    actions_page.show_developer_lane(restored["developer_lane"])
                actions_page.show_section(restored["actions_section"])
        elif self._view_model.page == "settings":
            self._add_content_page(self._settings_page(), 1)
        elif self._view_model.page == "firmware":
            self._add_content_page(self._firmware_page.build(model), 1)
        elif self._view_model.page == "joystick":
            self._add_content_page(self._calibration_page.build(model), 1)
        elif model.snapshot is not None:
            if self._view_model.page == "sequences" and has_draft:
                self._add_content_page(
                    self._sequences_page.build(model, editing=macro_values), 1
                )
            elif self._view_model.page == "lighting" and has_draft:
                preferences_page = (
                    current_preferences_page
                    if reuse_preferences_page
                    else self._lighting_page.build(model.snapshot, editing_values=preferences_values)
                )
                self._cached_preferences_page = preferences_page
                self._add_content_page(preferences_page, 1)
                preferences_page.show()
                if reuse_preferences_page:
                    preferences_page.refresh_summary()
                    canvas = preferences_page.findChild(DeviceModelCanvas)
                    if canvas is not None:
                        canvas.set_agent_states(canvas._snapshot_agent_states(model.snapshot))
                    self._lighting_page.preview_status_changed(
                        self._view_model.lighting_preview
                    )
            else:
                self._add_content_page(
                    self._overview(
                        model.snapshot,
                        model.state,
                        mapping_editing_state=mapping_editing_state,
                    ),
                    1,
                )
        else:
            self._add_content_page(self._state_view.build(model), 1)
        if page == "sequences":
            # Building may pick another sequence when the selected one is gone.
            self._content.render_inputs = copy.deepcopy(self._sequences_page.render_inputs(model))
        self._refresh_device_summary(model)
        if not reuse_preferences_page:
            self._language_manager.retranslate_widget_tree(self._content)

    def _header(self, model: ScreenModel) -> QWidget:
        bar = QFrame(objectName="topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 0, 0, 8)
        row.setSpacing(12)

        page_title = {
            "overview": "Mode",
            "sequences": "Device Key Sequences",
            "prompts": "Quick Prompts",
            "actions": "高级自定义",
            "lighting": "外观与反馈",
            "joystick": "Calibration",
            "firmware": "Firmware",
            "settings": "Settings",
        }.get(self._view_model.page, "Controls")
        hero = QLabel(page_title, objectName="pageTitle")
        set_translatable_text(hero, page_title)
        row.addWidget(hero, 4)
        if self._view_model.page in {"actions", "sequences"}:
            is_actions = self._view_model.page == "actions"
            back = QPushButton("返回 Settings" if is_actions else "返回 Mode", objectName="backFromAdvanced")
            back.setProperty("buttonRole", "secondary")
            if is_actions:
                back.clicked.connect(lambda _checked=False: self._select_settings_section("system"))
            else:
                back.clicked.connect(lambda _checked=False: self._navigate("overview"))
            row.addWidget(back)

        row.addStretch(1)
        return bar

    def _settings_page(self) -> QWidget:
        section = self._settings_section
        if section not in {"device", "ai", "system", "updates"}:
            section = self._settings_section = "device"
        wall = {
            "device": self._device_wall,
            "ai": self._ai_wall,
            "system": self._system_wall,
            "updates": self._updates_wall,
        }[section]()
        # 图标栏紧挨在墙的左边，和墙一起居中；窗口拉大以后两样不会分开。
        workspace = _wall_workspace("settingsWorkspace")
        layout = QHBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(wall_scroll(wall, self._settings_rail(section)), 1)
        return workspace

    def _device_wall(self) -> SettingsWall:
        """设备与连接：设备信息（2×2）、摇杆校准、蓝牙名称、恢复出厂设置、操作说明。"""
        wall = SettingsWall("device")
        model = self._view_model.model
        snapshot = model.snapshot
        connected = snapshot is not None and model.state in {
            AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}
        over_ble = snapshot is not None and snapshot.port_name.startswith("ble:")

        link = StateLine()
        link.label.setWordWrap(False)
        link.label.setObjectName("deviceLinkState")
        if snapshot is None:
            link.set_state("off", translate_ui_text("未连接设备"))
        elif connected:
            link.set_state("ok", translate_ui_text("蓝牙已连接" if over_ble else "USB 已连接"))
        else:
            link.set_state("wait", translate_ui_text("已断开"))
        info = wide_tile("device", "设备信息", control=link, rows=2)
        mark_row = QHBoxLayout()  # 底下一行：左“更多信息”，右下角几何图标暗纹（design decision 第 1 条）
        mark_row.setContentsMargins(0, 0, 0, 0)
        if snapshot is None:
            info.box.addSpacing(6)
            info.box.addWidget(note_label("连接设备后可以查看序列号和固件版本。"))
        else:
            info.box.addSpacing(8)
            for name, value, translated in (
                ("序列号", str(snapshot.identity.get("serial", "—")), False),
                ("连接方式", "蓝牙" if over_ble else "USB", True),
                ("固件", str(snapshot.versions.get("firmware", "—")), False),
            ):
                info.box.addWidget(wall_rule())
                info.box.addWidget(wall_value_row(name, value, translate_value=translated))
            info.box.addWidget(wall_rule())
            if connected and over_ble and self._system_permissions.is_mac:
                # Console 用蓝牙连设备要的系统权限：只在用蓝牙连接时显示（design decision）。只读，不改连接。
                from controller_config.views.desktop_agent_settings import BLUETOOTH_TEXT

                granted = self._system_permissions.bluetooth()
                info.box.addSpacing(6)
                info.box.addWidget(note_label(
                    BLUETOOTH_TEXT.get(granted, BLUETOOTH_TEXT[desktop_permissions.UNKNOWN]),
                    "deviceBluetoothPermission"))
            technical = pill_button("更多信息", "deviceDetailsToggle")
            technical.clicked.connect(lambda: self._show_technical_details(snapshot))
            mark_row.addWidget(technical, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
            if self._ble_slots_available(snapshot):
                # The same Bluetooth dialog as the Mode page's Bluetooth chip; 「如何用设备切换」
                # is at its bottom (the designer 11-21 选甲; 方案调度台裁定). Nothing new about Bluetooth.
                connections = pill_button("蓝牙连接…", "openBleConnections")
                connections.clicked.connect(lambda: self._show_ble_slots(snapshot, model.state))
                mark_row.addSpacing(8)
                mark_row.addWidget(connections, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        info.spacer()
        # 带分步耗时，内容能直接贴进自助诊断提示词（design decision）。没连设备也能复制：应用状态和
        # 最近的连接事件照样有用。自己一行，英日文的「更多信息」「蓝牙连接…」那一行放不下第三个
        # 按钮（design decision：不截字）。和下面两颗一样是带边框的按钮：只有一句字看不出能点
        # （the designer 11-23）。
        copy_row = QHBoxLayout()
        copy_row.setContentsMargins(0, 0, 0, 8)
        copy_button = pill_button(COPY_DIAGNOSTICS_TEXT, "copyDeviceDiagnostics")
        copy_button.clicked.connect(lambda: copy_diagnostics(copy_button, self._console_diagnostics_text()))
        copy_row.addWidget(copy_button)
        copy_row.addStretch(1)
        info.box.addLayout(copy_row)
        mark_row.addWidget(OrbitalMark(), 1)
        info.box.addLayout(mark_row)
        wall.place(info, 0, 0)

        calibration = pill_button("开始校准", "openJoystickCalibration")
        capability = _planned_tool_capability("joystick", snapshot)
        can_calibrate = (
            model.state is AppState.READY and capability is True
            and snapshot is not None and snapshot.compatibility.get("write") is True
            and not self._view_model.firmware_update.blocks_editing
            and not self._view_model.write_transaction.blocks_editing
            and not self._view_model.calibration.blocks_editing
        )
        calibration.setEnabled(can_calibrate)
        calibration.clicked.connect(lambda: self._navigate("joystick"))
        wall.place(square_tile(
            "joystick", "摇杆校准",
            note_label(
                "用于检查回中与方向行程；不能代替硬件维修。"
                if can_calibrate else "连接支持校准的可写设备后可用；当前固件或连接状态暂不支持。",
                "joystickCalibrationAvailability",
            ),
            control=calibration,
        ), 0, 2)

        rename = pill_button("改名", "openBleNameEditor")
        rename.setEnabled(snapshot is not None)
        rename.clicked.connect(self._open_ble_name_editor)
        saved_name = str((self._view_model.ble_name.result or {}).get("saved_name") or "")
        name_note = note_label("" if saved_name else "设备在电脑蓝牙列表里显示的名字。")
        if saved_name:
            name_note.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            name_note.setTextFormat(Qt.TextFormat.PlainText)
            name_note.setText(saved_name)
        wall.place(square_tile("name", "蓝牙名称", name_note, control=rename), 0, 3)

        reset = pill_button("恢复出厂…", "factoryResetDevice")
        reset.setAccessibleName("恢复出厂设置…")
        reset.setProperty("buttonRole", "secondary")
        reset.setEnabled(self._factory_reset_available(snapshot))
        reset.clicked.connect(self._confirm_factory_reset)
        wall.place(square_tile(
            "reset", "恢复出厂设置",
            "恢复出厂设置会清除设备配置、提示词和蓝牙配对信息。",
            control=reset,
        ), 1, 2)

        guide = pill_button("打开", "openOnboardingGuide")
        guide.setProperty("textEntry", True)
        guide.setAccessibleName(self._language_manager.translate("开机与操作说明"))
        guide.clicked.connect(self._show_onboarding)
        wall.place(square_tile(
            "help", "开机与操作说明",
            "用动画演示怎么开机、连接和操作设备。",
            control=guide,
        ), 1, 3)
        return wall

    def _open_ble_name_editor(self) -> None:
        show_card_dialog(self, "蓝牙名称", BleNameEditor(self._view_model))
        if self._view_model.page == "settings" and self._settings_section == "device":
            self._content.render_inputs = None
            self.render(self._view_model.model)

    def _codex_micro_status(self) -> dict | None:
        """设备 GET_STATUS 里的 codex_micro；没连设备时是 None（给 Codex 那一块用）。"""
        snapshot = self._view_model.model.snapshot
        if snapshot is None or self._view_model.model.state not in {AppState.READY, AppState.READ_ONLY}:
            return None
        micro = snapshot.status.get("codex_micro")
        return micro if isinstance(micro, dict) else None

    def _ai_wall(self) -> SettingsWall:
        """AI 状态灯：Claude、豆包（各一个开关）、Codex／ChatGPT（只读）、6 颗灯的编号和分灯规则。"""
        wall = SettingsWall("ai")
        wall.place(ClaudeStatusTile(self._view_model.claude_status), 0, 0)
        wall.place(DoubaoTile(self._view_model.desktop_agents,
                              open_updates=lambda: self._select_settings_section("updates")), 0, 2)

        background = self._background_controller
        signals = [self._view_model.diagnostics_changed]
        if background is not None:
            signals.append(background.codex_usage_changed)
        wall.place(CodexStatusTile(
            codex_micro=self._codex_micro_status,
            usage=lambda: background.codex_usage_snapshot if background is not None else None,
            signals=signals,
        ), 1, 0)

        lamps = LampsTile(self._view_model.claude_status, self._view_model.desktop_agents)
        wall.place(lamps, 1, 2)
        return wall

    def _system_wall(self) -> SettingsWall:
        """通用：语言、高级自定义。"""
        wall = SettingsWall("system")
        # 语言：横两格的三个大键，换的时候“点阵长满”（the designer 11-19，design decision 第 2、4 条）
        language = wide_tile("globe", "语言")
        language.box.addSpacing(12)
        keys = LanguageKeys(
            [(code, _LANGUAGE_GLYPHS[code], _LANGUAGE_LABELS[code]) for code in SUPPORTED_LANGUAGES],
            on_choose=self._choose_language,
        )
        self._language_keys = keys
        self._language_buttons.clear()
        self._language_buttons.update(keys.keys)
        language.box.addWidget(keys, 1)
        wall.place(language, 0, 0)
        self._refresh_language_buttons()
        if self._language_switch is not None:
            keys.play(self._language_switch)

        open_advanced = pill_button("打开", "openAdvancedCustomization")
        open_advanced.setAccessibleName("打开高级自定义")
        open_advanced.setEnabled(not self._view_model.firmware_update.blocks_editing
                                 and not self._view_model.calibration.blocks_editing)
        open_advanced.clicked.connect(lambda _checked=False: self._navigate("actions"))
        # 高级自定义缩成一格（随“方案丙”定，design decision 第 3 条）
        wall.place(square_tile("sliders", "高级自定义", "组合电脑操作，或接入自己的脚本与扩展。",
                               control=open_advanced), 0, 2)

        # 首页的 AI 额度默认显示 (Mode页 定稿 v1, 08). Which group and how it looks is for
        # the Settings draft; the AI wall is full (4×2), so it waits here on 通用's free row.
        default = SegmentedControl([("5h", "5 小时"), ("week", "一周")], self._mode_quota_default(),
                                   object_name="modeQuotaDefault")
        default.valueChanged.connect(self._set_mode_quota_default)
        default.setMinimumWidth(140)   # "5 小时" and "一周" in full, English and Japanese too
        quota = wide_tile("info", "首页的 AI 额度默认显示", control=default)
        quota.box.addStretch(1)
        quota.box.addWidget(note_label("某一家没有 5 小时限制时，那一家自动显示一周。"))
        wall.place(quota, 1, 0)

        # 遇到问题：让用户电脑上的 AI 先帮忙查（自助诊断提示词，design ticket，the designer 选「通用」这一格）。
        diagnosis = pill_button("打开", "openDiagnosisPrompt")
        diagnosis.setAccessibleName("打开遇到问题")
        diagnosis.clicked.connect(lambda _checked=False: self._show_diagnosis_prompt())
        wall.place(square_tile("question", "遇到问题", "让你电脑上的 AI 助手先查一遍，写一份报告。",
                               control=diagnosis), 1, 2)
        return wall

    def _updates_wall(self) -> SettingsWall:
        """更新：电脑软件（2×2）、设备固件（2×2）。"""
        wall = SettingsWall("updates")
        wall.setObjectName("updatesOverviewPage")
        identity = self._build_identity
        update_ui = self._desktop_update_ui

        action: QPushButton | None = None
        if identity.is_dev_build:
            origin_text = "内部开发版 · 不对外"
        elif identity.allows_official_updates:
            origin_text = "官方版本"
            action = pill_button("检查应用更新", "checkDesktopUpdate")
            action.setEnabled(update_ui is not None)
            if update_ui is not None:
                action.clicked.connect(update_ui.check)
        else:
            origin_text = "自定义版本" if identity.origin == "custom" else "构建信息不可用"
            action = pill_button("切换到官方版本", "switchOfficialVersion")
            action.setEnabled(update_ui is not None)
            if update_ui is not None:
                action.clicked.connect(update_ui.switch_to_official)
        software = wide_tile("computer", "电脑软件", control=action, rows=2)
        software.box.addSpacing(10)
        version = QLabel(identity.display_version(__version__), objectName="consoleVersion")
        version.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        version.setStyleSheet("font-size: 28px; font-weight: 600;")
        software.box.addWidget(version)
        software.box.addWidget(note_label("BORING Console"))
        software.box.addSpacing(8)
        origin = QLabel(origin_text, objectName="consoleBuildOrigin")
        origin.setStyleSheet("font-size: 13px; font-weight: 500;")
        software.box.addWidget(origin)
        if identity.is_dev_build:
            software.box.addWidget(note_label("开发版不检查、不下载软件更新。"))
        elif identity.allows_official_updates:
            inline_update = note_label("", "desktopUpdateInlineMessage")
            inline_update.setTextFormat(Qt.TextFormat.PlainText)
            # 进度条点阵样式 v1: the small ring on the left while it downloads, the words on the right.
            inline_ring = ProgressRing(36)
            inline_ring.setObjectName("desktopUpdateInlineProgress")
            inline_row = QHBoxLayout()
            inline_row.setSpacing(10)
            inline_row.addWidget(inline_ring, 0, Qt.AlignmentFlag.AlignVCenter)
            inline_row.addWidget(inline_update, 1)
            if update_ui is not None:
                update_ui.populate_inline_status(inline_update)
                update_ui.populate_inline_progress(inline_ring)
            else:
                inline_update.hide()
                inline_ring.hide()
            software.box.addLayout(inline_row)
        software.box.addSpacing(10)
        software.box.addWidget(wall_rule())
        software.box.addSpacing(8)
        self._add_release_highlights(software.box)
        software.spacer()
        wall.place(software, 0, 0)

        open_firmware = pill_button("固件更新…", "openFirmwareUpdates")
        open_firmware.setAccessibleName("查看设备固件与更新")
        open_firmware.clicked.connect(lambda: self._select_settings_section("firmware"))
        firmware = wide_tile("chip", "设备固件", control=open_firmware, rows=2)
        firmware.setObjectName("firmwareOverviewCard")
        firmware.box.addSpacing(10)
        snapshot = self._view_model.model.snapshot
        firmware_version = QLabel(
            str(snapshot.versions.get("firmware", "—")) if snapshot else "—",
            objectName="firmwareOverviewVersion",
        )
        firmware_version.setTextFormat(Qt.TextFormat.PlainText)
        firmware_version.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        firmware_version.setStyleSheet("font-size: 28px; font-weight: 600;")
        firmware.box.addWidget(firmware_version)
        firmware.box.addWidget(note_label("Boring MIST"))
        firmware.box.addSpacing(8)
        connected = self._view_model.model.state in {
            AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}
        if self._view_model.firmware_update.state is not FirmwareUpdateState.IDLE:
            message = self._view_model.firmware_update.message
        elif snapshot is None:
            message = "连接设备后可以查看固件版本；固件安装需要 USB。"
        elif not connected:
            message = "上次读取的固件版本，当前设备已断开。重新连接后确认版本；固件安装需要 USB。"
        else:
            message = self._view_model.firmware_online_message
        firmware.box.addWidget(note_label(message, "firmwareOverviewMessage"))
        firmware.spacer()
        wall.place(firmware, 0, 2)
        return wall

    def _add_release_highlights(self, layout: QVBoxLayout) -> None:
        """「本版本更新」直接排在电脑软件那一块里，不再另套一层卡片。"""
        try:
            summary = load_app_release_summary(self._language_manager.language)
        except (OSError, UnicodeError, ValueError, TypeError):
            return
        block = QWidget(objectName="appReleaseSummary")
        box = QVBoxLayout(block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(5)
        title = QLabel(
            self._language_manager.translate("本版本更新"),
            objectName="appReleaseSummaryTitle",
        )
        title.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        box.addWidget(title)
        for text in summary.highlights:
            item = QLabel(f"•  {text}", objectName="appReleaseHighlight")
            item.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            item.setTextFormat(Qt.TextFormat.PlainText)
            item.setWordWrap(True)
            box.addWidget(item)
        layout.addWidget(block)

    def _settings_rail(self, section: str) -> SettingsRail:
        """五个分组图标。"""
        rail = SettingsRail()
        # 固件更新页是「更新」分组里点进去的一页，所以图标仍然亮在「更新」上；
        # 摇杆校准页从「设备与连接」点进去，图标亮在「设备与连接」上。
        selected_section = {"firmware": "updates", "joystick": "device"}.get(section, section)
        maintenance = self._view_model.model.state is AppState.FIRMWARE_MAINTENANCE
        for key, label, icon in SETTINGS_SECTIONS:
            button = RailButton(label, icon)
            button.setProperty("selected", key == selected_section)
            button.setProperty("settingsSection", key)
            button.setEnabled(
                (maintenance or not self._view_model.calibration.blocks_editing)
                and (not self._view_model.firmware_update.blocks_editing or key == selected_section)
                and (not maintenance or key == "updates")
            )
            button.clicked.connect(lambda _checked=False, key=key: self._select_settings_section(key))
            rail.add(button)
        return rail

    def _select_settings_section(self, section: str) -> None:
        if section == "appearance":
            self._navigate("lighting")
            return
        if section == "firmware":
            self._navigate(section)
            return
        self._settings_section = section
        if self._view_model.page == "settings":
            self.render(self._view_model.model)
        else:
            self._navigate("settings")
        update_ui = getattr(self, "_desktop_update_ui", None)
        if update_ui is not None:
            update_ui._refresh_footer_location()

    def _editing_profile(self, draft) -> int:
        profile_ids = {profile["id"] for profile in draft.profiles}
        if self._editing_profile_id not in profile_ids:
            self._editing_profile_id = draft.config["active_profile"]
        return self._editing_profile_id

    def _prompt_page_inputs(self, model: ScreenModel) -> tuple:
        library = self._view_model.prompt_library
        device = self._view_model.prompt_device
        return (
            (model.snapshot.identity, model.snapshot.capabilities) if model.snapshot else None,
            library,
            library.draft if library is not None else (),
            library.confirmed if library is not None else (),
            self._language_manager.language,
        )

    def _action_prompt_context(self) -> tuple[dict, dict]:
        names: dict[int, str] = {}
        controls_by_prompt: dict[int, list[str]] = {}
        library = self._view_model.prompt_library
        if library is not None:
            for prompt_id in range(1, PROMPT_SLOT_COUNT + 1):
                entry = library.draft_entry(prompt_id)
                if entry is not None:
                    names[prompt_id] = entry.name
        draft = self._view_model.draft
        if draft is not None:
            active_profile = self._editing_profile(draft)
            if isinstance(active_profile, int) and not isinstance(
                active_profile, bool
            ):
                profile = draft.profile(active_profile)
                mappings = profile.get("mappings")
                if isinstance(mappings, list):
                    for mapping in mappings:
                        action = (
                            mapping.get("action")
                            if isinstance(mapping, dict)
                            else None
                        )
                        prompt_id = (
                            action.get("prompt_id")
                            if isinstance(action, dict)
                            and action.get("type") == "prompt"
                            else None
                        )
                        control_id = (
                            mapping.get("control_id")
                            if isinstance(mapping, dict)
                            else None
                        )
                        if (
                            isinstance(prompt_id, int)
                            and not isinstance(prompt_id, bool)
                            and isinstance(control_id, str)
                        ):
                            controls_by_prompt.setdefault(prompt_id, []).append(
                                control_display_name(control_id)
                            )
        return names, {prompt_id: tuple(controls) for prompt_id, controls in controls_by_prompt.items()}

    def _actions_page(self) -> ActionsPage:
        names, controls = self._action_prompt_context()
        page = ActionsPage(self._view_model, prompt_names=names, prompt_controls=controls)
        page.section_created.connect(self._language_manager.retranslate_widget_tree)
        page.macros_requested.connect(lambda: self._navigate("sequences"))
        return page

    def _factory_reset_available(self, snapshot: DeviceSnapshot | None) -> bool:
        return (
            snapshot is not None
            and self._view_model.model.state is AppState.READY
            and snapshot.compatibility.get("write") is True
            and not self._view_model.firmware_update.is_busy
            and not self._view_model.calibration.blocks_editing
            and not self._view_model.write_transaction.blocks_editing
            and not self._view_model.prompt_device.status.is_busy
        )

    def _confirm_factory_reset(self) -> None:
        # Two steps on purpose (D7: the danger flow stays as it was); only the
        # look follows the shared danger confirmation (B direction v0.4).
        if not confirm(self, "恢复出厂设置",
                       "这会清除设备配置、提示词和全部蓝牙配对信息。是否继续？",
                       "恢复出厂设置…", danger=True, object_name="factoryResetConfirm"):
            return
        if not confirm(self, "再次确认恢复出厂设置",
                       "此操作不可撤销，完成后设备会自动重启，并需要重新配对蓝牙设备。确认执行吗？",
                       "恢复出厂设置", danger=True, object_name="factoryResetConfirmAgain"):
            return
        try:
            self._view_model.factory_reset_device()
        except ValueError as exc:
            notice.alert(self, "无法恢复出厂设置", str(exc))

    def _refresh_connection_area(self, model: ScreenModel) -> None:
        connected = model.state in {AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}
        self._connection_area.setVisible(not connected)
        message = model.message or (
            "设备未连接；已保留上次配置。"
            if model.snapshot is not None
            else ""
        )
        self._connection_message.setText(self._language_manager.translate(message))
        self._connection_message.setVisible(model.state not in {AppState.SCANNING, AppState.CONNECTING})
        self._connection_message.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        multiple = model.state is AppState.MULTIPLE_DEVICES
        self._connection_candidates.setVisible(multiple)
        self._connect_selected.setVisible(multiple)
        self._connection_retry.setVisible(model.snapshot is not None and model.state in {
            AppState.READ_FAILED, AppState.DISCONNECTED, AppState.AUTHENTICITY_FAILED,
            AppState.FIRMWARE_UPDATE_REQUIRED,
        })
        candidates = [(item.display_name, item.port_name) for item in model.candidates]
        current = [(self._connection_candidates.itemText(index), self._connection_candidates.itemData(index))
                   for index in range(self._connection_candidates.count())]
        if candidates != current:
            selected = self._connection_candidates.currentData()
            self._connection_candidates.clear()
            for name, port in candidates:
                self._connection_candidates.addItem(name, port)
            index = self._connection_candidates.findData(selected)
            if index >= 0:
                self._connection_candidates.setCurrentIndex(index)
        details = "\n".join(self._connection_terminal.transcript)
        if model.technical_message:
            details += "\n" + model.technical_message
        if self._connection_details.toPlainText() != details:
            bar = self._connection_details.verticalScrollBar()
            position = bar.value()
            follow_latest = position == bar.maximum()
            self._connection_details.setPlainText(details)
            bar.setValue(bar.maximum() if follow_latest else position)

    def _state_page(self, model: ScreenModel) -> QWidget:
        # Kept for the lighting page, which is being redrawn (规划 11.3).
        return self._state_view.build(model)

    def _overview(
        self,
        snapshot: DeviceSnapshot,
        state: AppState,
        *,
        mapping_editing_state: _MappingEditingState | None = None,
        scroll: QScrollArea | None = None,
    ) -> QWidget:
        """The Mode page (Mode页 定稿 v1): see views/mode_page.py for what is drawn."""
        scroll = scroll if scroll is not None else QScrollArea(objectName="overviewScroll")
        draft = self._view_model.draft
        status_inputs = {key: snapshot.status.get(key) for key in
                         ("operating_mode", "platform", "active", "pending", "activation_failed")}
        micro = snapshot.status.get("codex_micro")
        status_inputs["has_ble_slots"] = isinstance(micro, dict) and isinstance(micro.get("active_slot"), int)
        inputs = (replace(snapshot, status=status_inputs), state, id(draft),
                  (draft.config, self._editing_profile(draft)) if draft else None,
                  draft.base_generation if draft else None,
                  self._view_model.write_transaction,
                  self._language_manager.language,
                  self._configuration_mode)
        previous_inputs = getattr(scroll, "render_inputs", None)
        scroll.content_changed = (
            inputs != previous_inputs
            or self._selected_control_id != getattr(scroll, "selected_control_id", None)
        )
        stage = scroll.findChild(ModeStage)
        if not scroll.content_changed and stage is not None:
            # Only the live parts: Agent colours, battery, the bar's sentence.
            self._fill_mode_stage(stage, snapshot, state, labels=False)
            return scroll
        if (stage is not None and previous_inputs is not None
                and inputs[:3] == previous_inputs[:3]
                and inputs[4:-1] == previous_inputs[4:-1]
                and self._refresh_mode_workspace(snapshot, scroll, mapping_editing_state)):
            scroll.render_inputs = copy.deepcopy(inputs)
            scroll.selected_control_id = self._selected_control_id
            self._content.setProperty("discardMappingEdits", False)
            scroll.content_changed = False
            return scroll
        scroll.render_inputs = copy.deepcopy(inputs)
        scroll.selected_control_id = self._selected_control_id
        self._content.setProperty("discardMappingEdits", False)
        old_focus = QApplication.focusWidget()
        focus_name = old_focus.objectName() if old_focus is not None and scroll.isAncestorOf(old_focus) else ""
        cursor_position = old_focus.cursorPosition() if isinstance(old_focus, QLineEdit) else None
        # The connection transcript is shared across pages and must outlive the old card.
        self._connection_terminal.setParent(self)
        self._connection_terminal.hide()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        stage = ModeStage()
        stage.device.control_chosen.connect(self._choose_mode_control)
        stage.flip_requested.connect(self._flip_mode_screen)
        stage.screen_requested.connect(self._show_mode_screen)
        stage.blank_clicked.connect(self._mode_blank_clicked)
        stage.zoom_part_chosen.connect(self._select_control)   # 逆时针 / 顺时针 / 按下, 向上 … on the zoomed part
        bar = stage.bar
        bar.set_steps_source(self._view_model.connection_steps)
        bar.back_clicked.connect(self._close_control_editor)
        bar.profile_clicked.connect(lambda: self._mode_show_overview(True))
        overview = stage.overview
        overview.screen_chosen.connect(self._mode_overview_choose)
        overview.enable_requested.connect(self._mode_overview_enable)
        overview.menu_requested.connect(self._mode_screen_menu)
        overview.new_requested.connect(self._mode_new_screen_menu)
        overview.import_requested.connect(self._import_configuration)
        overview.export_requested.connect(self._mode_export_menu)
        overview.discard_requested.connect(self._confirm_discard_draft)
        overview.order_changed.connect(self._mode_overview_reorder)
        overview.done_requested.connect(lambda: self._mode_show_overview(False))
        bar.ble_clicked.connect(lambda: self._show_ble_slots(None, None))
        bar.bind_clicked.connect(self._begin_shortcut_binding)
        bar.discard_clicked.connect(self._mode_discard_key)
        bar.save_clicked.connect(self._mode_save)
        bar.enable_clicked.connect(self._mode_enable_screen)
        bar.pending_clicked.connect(lambda: self._show_draft_changes())
        self._fill_mode_stage(stage, snapshot, state)
        self._refresh_mode_quota(stage=stage)
        stage.set_card(self._mode_card(snapshot, self._mapping_editor(snapshot, mapping_editing_state)))
        scroll.setWidget(stage)
        if self._mode_overview_open:   # 全部方案 was open: keep it open over the rebuilt page
            stage.show_overview(True)
            self._refresh_mode_overview(stage)
        if focus_name and not self._view_model.write_transaction.blocks_editing:
            new_focus = stage.findChild(QWidget, focus_name)
            if new_focus is not None:
                new_focus.setFocus(Qt.OtherFocusReason)
                if isinstance(new_focus, QLineEdit) and cursor_position is not None:
                    new_focus.setCursorPosition(cursor_position)
        return scroll

    # -- Mode page: screens -------------------------------------------------------------

    def _mode_screens(self) -> list[tuple[str, int | None]]:
        """One screen per set of mappings in normal mode, then Codex (when the device has it)."""
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        screens: list[tuple[str, int | None]] = []
        if draft is not None:
            for profile in draft.profiles:
                profile_id = profile.get("id")
                if (isinstance(profile_id, int) and not isinstance(profile_id, bool)
                        and not is_other_platform_profile(str(profile.get("name", "")), platform=sys.platform)):
                    screens.append(("normal", profile_id))
        if not screens:
            screens.append(("normal", None))
        if snapshot is not None and self._configuration_mode_supported(snapshot, "codex"):
            screens.append(("codex", None))
        return screens

    def _viewed_mode_screen(self) -> int:
        screens = self._mode_screens()
        if self._configuration_mode == "codex" and ("codex", None) in screens:
            return screens.index(("codex", None))
        draft = self._view_model.draft
        if draft is not None:
            profile_id = self._editing_profile(draft)
            if ("normal", profile_id) in screens:
                return screens.index(("normal", profile_id))
        return 0

    def _active_mode_screen(self, snapshot: DeviceSnapshot) -> int | None:
        """The screen the device is using: green ring on its page dot."""
        screens = self._mode_screens()
        mode = snapshot.status.get("operating_mode")
        if mode == "codex":
            return screens.index(("codex", None)) if ("codex", None) in screens else None
        if mode not in {"normal", None}:
            return None
        target = ("normal", snapshot.active_profile_id)
        return screens.index(target) if target in screens else None

    def _show_mode_screen(self, index: int) -> None:
        screens = self._mode_screens()
        if not 0 <= index < len(screens) or index == self._viewed_mode_screen():
            return
        mode, profile_id = screens[index]
        if mode == "codex":
            self._select_configuration_mode("codex")
            return
        if self._configuration_mode != "normal":
            draft = self._view_model.draft
            if draft is not None and profile_id is not None:
                self._editing_profile_id = profile_id
            self._select_configuration_mode("normal")
            return
        if profile_id is not None:
            self._activate_profile(profile_id)

    def _flip_mode_screen(self, step: int) -> None:
        self._show_mode_screen(self._viewed_mode_screen() + step)

    # -- Mode page: what the stage shows ------------------------------------------------

    def _mode_quota_default(self) -> str:
        """Settings › 首页的 AI 额度默认显示: "5h" (default) or "week"."""
        value = (self._onboarding_settings.value(MODE_QUOTA_DEFAULT_KEY, "5h", type=str)
                 if self._onboarding_settings is not None else getattr(self, "_mode_quota_choice", "5h"))
        return value if value in {"5h", "week"} else "5h"

    def _set_mode_quota_default(self, value: str) -> None:
        if value not in {"5h", "week"}:
            return
        self._mode_quota_choice = value   # kept for this run even without a settings file
        if self._onboarding_settings is not None:
            self._onboarding_settings.setValue(MODE_QUOTA_DEFAULT_KEY, value)
            self._onboarding_settings.sync()
        self._refresh_mode_quota()

    def _mode_quota_providers(self) -> tuple[tuple[QuotaProvider, ...], str]:
        background = self._background_controller
        sources = (("Codex", "CODEX", "codex", background.codex_usage_snapshot if background else None),
                   ("Claude", "CLAUDE", "claude", background.claude_usage_snapshot if background else None))
        default = self._mode_quota_default()
        providers, updated = [], []
        for name, word, limit_id, usage in sources:
            bucket = next((b for b in (usage.buckets if usage is not None else ()) if b.limit_id == limit_id), None)
            if usage is not None and usage.updated_at is not None:
                updated.append(usage.updated_at)
            if bucket is None:
                # Not logged in or not read yet: not drawn in the draft (15); a ring with "—".
                providers.append(QuotaProvider(name, word, windows=(QuotaWindow("5h", None),)))
                continue
            windows = []
            if bucket.primary is not None:
                windows.append(QuotaWindow("5h", bucket.primary.remaining_percent, _quota_reset(bucket.primary.resets_at)))
            if bucket.secondary is not None:
                windows.append(QuotaWindow("week", bucket.secondary.remaining_percent,
                                           _quota_reset(bucket.secondary.resets_at)))
            if not windows:
                windows.append(QuotaWindow("5h", None))
            # No 5-hour limit (e.g. Claude Ultra): that app shows its week (08, 二).
            shown = next((i for i, w in enumerate(windows) if w.label == default), len(windows) - 1)
            providers.append(QuotaProvider(name, word, (bucket.plan_type or "").upper(), tuple(windows), shown))
        stamp = ""
        if updated:
            from datetime import datetime
            stamp = translate_ui_text("更新于 {time}").format(time=datetime.fromtimestamp(max(updated)).strftime("%H:%M"))
        return tuple(providers), stamp

    def _refresh_mode_quota(self, *_args, stage: ModeStage | None = None) -> None:
        stage = stage or self._visible_mode_stage()
        if stage is not None:
            stage.quota.set_data(*self._mode_quota_providers())

    def _visible_mode_stage(self) -> ModeStage | None:
        return next((stage for stage in self._content.findChildren(ModeStage) if stage.isVisible()), None)

    def _fill_mode_stage(self, stage: ModeStage, snapshot: DeviceSnapshot, state: AppState,
                         *, labels: bool = True) -> None:
        connected = state in {AppState.READY, AppState.READ_ONLY}
        codex = self._configuration_mode == "codex"
        screens = self._mode_screens()
        stage.set_state(StageState(
            connected=connected,
            word="CODEX" if codex else "NORMAL",
            screens=len(screens),
            viewed=self._viewed_mode_screen(),
            active=self._active_mode_screen(snapshot),
            battery=snapshot.battery_percent if connected else None,
            usb=connected and snapshot.connection_kind != "bluetooth",
            battery_tip="设备报告的电量；USB 连接不代表电池正在充电。",
            text=self._mode_stage_text(snapshot) if labels else stage.state().text,
        ))
        # A light turning 待处理 blinks twice first (-15 第 7 处; Console only).
        self._attention_blink.apply(self._mode_agent_colours(snapshot), stage.device.set_status_colors)
        stage.play_link(self._mode_link_for(connected))
        if labels and stage.overview_shown():
            self._refresh_mode_overview(stage)
        if labels:
            key_labels, unset = self._mode_key_labels(snapshot)
            stage.device.set_labels(key_labels, unset=unset)
            stage.device.set_chosen(self._mode_chosen_control())
            stage.set_zoom(self._mode_zoom_target(), self._selected_control_id, animate=False)
        self._refresh_mode_bar(stage)

    def _mode_link_for(self, connected: bool) -> Link | None:
        """Start the connect / disconnect animation when the connection changes; a page
        rebuilt while it runs carries on from the same start (06)."""
        seen, self._mode_seen_connected = self._mode_seen_connected, connected
        if seen is not None and seen != connected:
            self._mode_link = Link("in" if connected else "out", time.monotonic())
        if self._mode_link is not None and self._mode_link.done():
            self._mode_link = None
        return self._mode_link

    def _mode_zoom_target(self) -> str | None:
        """The knob ("encoder") or the joystick when one of its parts is open (11)."""
        chosen = self._mode_chosen_control()
        return chosen if chosen in {"encoder", "joystick"} else None

    def _mode_chosen_control(self) -> str | None:
        control_id = self._selected_control_id
        if not isinstance(control_id, str):
            return None
        if control_id.startswith("encoder."):
            return "encoder"
        if control_id.startswith("joystick."):
            return "joystick"
        return control_id

    def _mode_mappings(self, snapshot: DeviceSnapshot) -> dict[str, dict]:
        draft = self._view_model.draft
        if draft is not None:
            profile_id = self._editing_profile(draft)
            if isinstance(profile_id, int) and not isinstance(profile_id, bool):
                return _profile_mappings(draft.profile(profile_id))
        return snapshot.mappings

    def _mode_control_text(self, mappings: dict[str, dict], control_id: str, platform: str) -> str:
        mapping = mappings.get(control_id)
        action = mapping.get("action") if isinstance(mapping, dict) else None
        if not isinstance(action, dict) or action.get("type") in {None, "none"}:
            return "未映射"
        name = str(mapping.get("short_name") or "").strip()
        return name or describe_action(action, platform=platform, compact=True, control_id=control_id)

    def _mode_key_labels(self, snapshot: DeviceSnapshot, *,
                         profile_id: int | None = None) -> tuple[dict[str, str], set[str]]:
        if profile_id is None and self._configuration_mode == "codex":
            return {key: translate_ui_text(name) for key, name in MATRIX12_CODEX_KEY_NAMES.items()}, set()
        draft = self._view_model.draft
        mappings = (_profile_mappings(draft.profile(profile_id)) if profile_id is not None and draft is not None
                    else self._mode_mappings(snapshot))
        platform = "macos" if sys.platform == "darwin" else "windows"
        labels, unset = {}, set()
        for number in range(1, 13):
            control_id = f"key.{number}"
            if control_id in MATRIX12_AGENT_STATUS_KEYS:
                continue
            text = self._mode_control_text(mappings, control_id, platform)
            labels[control_id] = text
            if text == "未映射":
                unset.add(control_id)
        return labels, unset

    def _mode_stage_text(self, snapshot: DeviceSnapshot) -> StageText:
        if self._configuration_mode == "codex":
            return StageText(
                knob=tuple(KnobRow(icon, verb, "Codex 旋钮")
                           for icon, verb in (("↺", "逆时针"), ("↻", "顺时针"), ("●", "按下"))),
                status_line="显示 Agent 状态",
                joystick_note=("Codex 导航", "按下同普通模式"),
                fixed_note=("按键功能固定", "要调整，请到 Codex 里改"),
            )
        mappings = self._mode_mappings(snapshot)
        platform = "macos" if sys.platform == "darwin" else "windows"
        knob = tuple(
            KnobRow(icon, verb, self._mode_control_text(mappings, control_id, platform))
            for icon, verb, control_id in (("↺", "逆时针", "encoder.ccw"), ("↻", "顺时针", "encoder.cw"),
                                           ("●", "按下", "encoder.press"))
        )
        joystick = {direction: self._mode_control_text(mappings, f"joystick.{direction}", platform)
                    for direction in ("up", "down", "left", "right", "press")}
        return StageText(knob=knob, status_line="按下打开对应对话", joystick=joystick)

    @staticmethod
    def _mode_agent_colours(snapshot: DeviceSnapshot) -> dict[str, str | None]:
        colours = {"working": tokens.MODE_AGENT_WORKING, "completed": tokens.MODE_AGENT_COMPLETED,
                   "approval": tokens.MODE_AGENT_ATTENTION, "reply": tokens.MODE_AGENT_ATTENTION,
                   "error": tokens.MODE_AGENT_ERROR}
        states = DeviceModelCanvas._snapshot_agent_states(snapshot)
        keys = [key for key in (f"key.{number}" for number in range(1, 13)) if key in MATRIX12_AGENT_STATUS_KEYS]
        return {key: colours.get(state) for key, state in zip(keys, states)}

    # -- Mode page: the bottom bar ------------------------------------------------------

    def _refresh_mode_steps(self) -> None:
        """A connecting step came in: the Mode bar's right cell follows (design decision)."""
        for bar in self.findChildren(ModeBottomBar):
            bar.refresh_steps()

    def _mode_bar_state(self) -> BarState:
        model = self._view_model.model
        snapshot, state = model.snapshot, model.state
        draft = self._view_model.draft
        connected = state in {AppState.READY, AppState.READ_ONLY}
        screens = self._mode_screens()
        viewed = self._viewed_mode_screen()
        mode, profile_id = screens[viewed]
        if mode == "codex":
            name = "Codex"
        elif draft is not None and profile_id is not None:
            name = str(draft.profile(profile_id).get("name", "Profile"))
        else:
            name = snapshot.profile_name if snapshot is not None else ""
        active = snapshot is not None and viewed == self._active_mode_screen(snapshot)
        micro = snapshot.status.get("codex_micro") if snapshot is not None else None
        ble = isinstance(micro, dict) and isinstance(micro.get("active_slot"), int)
        transaction = self._view_model.write_transaction
        status_card = self._mode_status_card_open()
        agent = self._view_model.normal_agent
        status_dirty = status_card and agent.dirty
        editing = self._mapping_editor_has_uncommitted_changes() or status_dirty
        busy = state in {AppState.SCANNING, AppState.CONNECTING}
        # "蓝牙连接" written out while not connected; round while connecting (D90 定稿).
        common = dict(profile_name=name, profile_active=active, ble_button=not connected and not busy, ble=ble,
                      editing_key=editing, can_save=self._mode_can_save(),
                      edit_text="状态灯键有改动" if status_dirty else "这颗键有改动",
                      zoomed=self._mode_zoom_target() is not None)
        if status_dirty:
            # The six status-light keys save on their own command, not with the key mappings.
            if agent.busy:
                return BarState(**{**common, "editing_key": False}, right="writing", dimmed=True)
            reason = self._view_model.normal_agent_write_block_reason()
            can = (agent.connected and agent.loaded and not agent.uncertain and not reason)
            return BarState(**{**common, "can_save": can},
                            right="pending" if connected else "offline",
                            text="" if connected else "没连上设备，连上以后再保存")
        if transaction.is_busy:
            return BarState(**{**common, "editing_key": False}, right="writing", dimmed=True)
        if not connected:
            dirty = editing or (draft is not None and draft.is_dirty)
            # While a key is changed the bar still says why it cannot be saved (05).
            # Connecting: the left of the bar greys like the page (D90 定稿).
            return BarState(**common, right="connecting" if busy else "offline", dimmed=busy,
                            text="" if busy else "没连上设备，连上以后再保存" if dirty else "")
        if state is AppState.READ_ONLY or snapshot.compatibility.get("write") is not True:
            return BarState(**common, right="readonly")
        if transaction.state in {ConfigTransactionState.FAILED, ConfigTransactionState.UNKNOWN,
                                 ConfigTransactionState.CONFLICT}:
            reason = {ConfigTransactionState.FAILED: "没保存上，可以再试一次",
                      ConfigTransactionState.UNKNOWN: "不确定有没有保存上",
                      ConfigTransactionState.CONFLICT: "设备上的设置变了，先重新读取"}[transaction.state]
            return BarState(**common, right="failed", text=reason)
        if editing:
            return BarState(**common, right="pending")
        count = len(draft.changes) if draft is not None and draft.is_dirty else 0
        if not active and mode == "normal" and profile_id is not None and profile_id != snapshot.active_profile_id:
            # 启用到设备 also saves what is not saved yet; its confirmation lists it (09).
            return BarState(**{**common, "can_save": self._mode_can_enable()}, right="enable", count=count)
        if count:
            return BarState(**common, right="pending", count=count)
        if time.monotonic() - self._mode_saved_at < MODE_SAVED_NOTE_SECONDS:
            return BarState(**common, right="saved")
        if snapshot.status.get("operating_mode") == "claude_code":
            # Console no longer sets Claude Code mode up; say how to leave it (design decision) without
            # naming the mode, which Console never shows (boring-P13, 单 design ticket 第 7 处).
            return BarState(**common, right="codex", text="双击设备上的旋钮，切回普通模式")
        if mode == "codex":
            return BarState(**common, right="synced" if active else "codex")
        bind = draft is not None and profile_id is not None and not _profile_mappings(draft.profile(profile_id))
        if (not active and profile_id == snapshot.active_profile_id
                and snapshot.status.get("operating_mode") == "codex"):
            # The device keeps this set but is in Codex mode: only the knob switches modes.
            return BarState(**common, right="codex", text="双击设备上的旋钮，切回普通模式", bind=bind)
        if not active:
            return BarState(**{**common, "can_save": self._mode_can_enable()}, right="enable", bind=bind)
        return BarState(**common, right="synced", bind=bind)

    def _refresh_mode_bar(self, stage: ModeStage | None = None) -> None:
        stage = stage or self._visible_mode_stage()
        if stage is None:
            return
        transaction_state = self._view_model.write_transaction.state
        if (self._mode_last_write_state in {ConfigTransactionState.WRITING, ConfigTransactionState.PENDING,
                                            ConfigTransactionState.VERIFYING}
                and transaction_state is ConfigTransactionState.ACTIVE):
            self._mode_saved_at = time.monotonic()
            QTimer.singleShot(int(MODE_SAVED_NOTE_SECONDS * 1000) + 50, self, self._refresh_mode_bar)
        self._mode_last_write_state = transaction_state
        if (self._view_model.model.state not in {AppState.READY, AppState.READ_ONLY}
                and not self._mode_keep_timer.isActive() and self._mapping_editor_has_uncommitted_changes()):
            self._mode_keep_timer.start()  # the device went away mid-edit: keep what was typed
        stage.bar.set_state(self._mode_bar_state())
        self._language_manager.retranslate_widget_tree(stage.bar)

    def _mode_can_save(self) -> bool:
        model = self._view_model.model
        snapshot, draft = model.snapshot, self._view_model.draft
        if snapshot is None or draft is None or model.state is not AppState.READY:
            return False
        return (snapshot.config_result.get("generation") == draft.base_generation
                and snapshot.compatibility.get("write") is True
                and not self._view_model.write_transaction.blocks_editing
                and self._view_model.write_transaction.state in {
                    ConfigTransactionState.IDLE, ConfigTransactionState.ACTIVE, ConfigTransactionState.FAILED})

    def _mode_can_enable(self) -> bool:
        return self._mode_can_save() and not self._view_model.validate_draft()

    def _mode_status_card_open(self) -> bool:
        card = self._current_mapping_card()
        return card is not None and card.findChild(QComboBox, "normalAgentBehavior") is not None

    def _mode_save(self) -> None:
        if self._mode_status_card_open() and self._view_model.normal_agent.dirty:
            self._normal_agent_action("save")
            return
        card = self._current_mapping_card()
        apply = card.findChild(QPushButton, "applyMappingToDevice") if card is not None else None
        if apply is not None and self._mapping_editor_has_uncommitted_changes():
            apply.click()  # the editor's own path: keep the edit, then write everything
            return
        self._prepare_device_write(confirm_after_validation=True)

    def _mode_discard_key(self) -> None:
        """× on the bar: drop what was typed into the open key; nothing else changes."""
        agent = self._view_model.normal_agent
        if self._mode_status_card_open() and agent.dirty and agent.value is not None and not agent.busy:
            agent.edit(agent.value)
            self._refresh_normal_agent_controls()
            return
        self._pending_mapping_editing_state = None
        self._suppress_mapping_edit_restore = True
        try:
            if not self._refresh_mapping_workspace():
                self.render(self._view_model.model)
        finally:
            self._suppress_mapping_edit_restore = False

    def _mode_enable_screen(self) -> None:
        screens = self._mode_screens()
        mode, profile_id = screens[self._viewed_mode_screen()]
        if mode == "normal" and profile_id is not None:
            self._enable_profile(profile_id)

    # -- 全部方案 (Mode页 定稿 v1, 10) -------------------------------------------------------

    def _mode_show_overview(self, shown: bool) -> None:
        stage = self._visible_mode_stage()
        if stage is None:
            return
        if shown and self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        self._mode_overview_open = shown
        if shown:
            self._refresh_mode_overview(stage)
        stage.show_overview(shown)

    def _refresh_mode_overview(self, stage: ModeStage | None = None) -> None:
        stage = stage or self._visible_mode_stage()
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        if stage is None or draft is None or snapshot is None:
            return
        connected = self._view_model.model.state in {AppState.READY, AppState.READ_ONLY}
        active = self._active_mode_screen(snapshot)
        screens = []
        for index, (mode, profile_id) in enumerate(self._mode_screens()):
            is_active = index == active
            if mode == "codex":
                labels = {key: translate_ui_text(name) for key, name in MATRIX12_CODEX_KEY_NAMES.items()}
                screens.append(OverviewScreen(("codex", None), "Codex",
                                              "设备正在用这一套" if is_active else "在设备上双击旋钮切换",
                                              active=is_active, codex=True, labels=labels))
                continue
            if profile_id is None:
                continue
            labels, unset = self._mode_key_labels(snapshot, profile_id=profile_id)
            screens.append(OverviewScreen(
                ("normal", profile_id), str(draft.profile(profile_id).get("name", "Profile")),
                "设备正在用这一套" if is_active else "设备没在用", active=is_active,
                can_enable=(not is_active and connected and profile_id != snapshot.active_profile_id
                            and not self._view_model.write_transaction.blocks_editing),
                labels=labels, unset=frozenset(unset)))
        can_add = len(draft.profiles) < draft.max_profiles and not self._view_model.write_transaction.blocks_editing
        stage.overview.set_screens(screens, can_add=can_add, connected=connected, dirty=draft.is_dirty,
                                   can_reorder=not self._view_model.write_transaction.blocks_editing)
        # As the old 导入配置文件… was: not while a write is going on or its result is unknown.
        stage.overview.import_button.setEnabled(not self._view_model.write_transaction.blocks_editing)
        self._language_manager.retranslate_widget_tree(stage.overview)

    def _mode_overview_choose(self, key: tuple) -> None:
        screens = self._mode_screens()
        self._mode_show_overview(False)
        if key in screens:
            self._show_mode_screen(screens.index(key))

    def _mode_overview_reorder(self, keys: list) -> None:
        """全部方案: a card was dragged to a new place (design decision). One unsaved change;
        保存到设备 writes it and reads it back like any other."""
        draft = self._view_model.draft
        if draft is None:
            return
        shown = [profile_id for mode, profile_id in keys if mode == "normal" and profile_id is not None]
        try:
            order = reorder_visible([p["id"] for p in draft.profiles], shown)
            self._view_model.reorder_profiles(order)
        except ValueError as exc:
            notice.alert(self, "无法调整方案顺序", str(exc))
        self._refresh_mode_overview()

    def _mode_overview_enable(self, key: tuple) -> None:
        mode, profile_id = key
        if mode == "normal" and profile_id is not None:
            self._enable_profile(profile_id)
            self._refresh_mode_overview()

    def _mode_overview_target(self, profile_id: int) -> None:
        """Rename / save as / delete act on the edited set; point it at this screen first."""
        draft = self._view_model.draft
        if draft is not None and self._editing_profile(draft) != profile_id:
            self._leave_codex_screen()
            self._editing_profile_id = profile_id

    def _mode_screen_menu(self, key: tuple, anchor: QWidget) -> None:
        draft = self._view_model.draft
        mode, profile_id = key
        if draft is None or mode != "normal" or profile_id is None:
            return
        menu = QMenu(anchor)
        menu.setObjectName("modeScreenMenu")

        def on_this(action) -> None:
            self._mode_overview_target(profile_id)
            action()
            self._refresh_mode_overview()

        rename = menu.addAction(translate_ui_text("重命名"))
        rename.setObjectName("renameProfileAction")
        rename.triggered.connect(lambda: on_this(self._rename_profile))
        save_as = menu.addAction(translate_ui_text("另存为…"))
        save_as.setObjectName("saveProfileAsAction")
        save_as.setEnabled(len(draft.profiles) < draft.max_profiles
                           and not self._view_model.write_transaction.blocks_editing)
        save_as.triggered.connect(lambda: on_this(self._save_profile_as))
        delete = menu.addAction(translate_ui_text("删除…"))
        delete.setObjectName("deleteProfileAction")
        delete.setEnabled(len(draft.profiles) > 1)
        delete.triggered.connect(lambda: on_this(self._delete_profile))
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() + 4)))

    def _mode_new_screen_menu(self, anchor: QWidget) -> None:
        """新建一屏: 空白, 另存当前, or an AI template (设置整套 AI 方案 joined it, 10)."""
        draft = self._view_model.draft
        if draft is None:
            return
        menu = QMenu(anchor)
        menu.setObjectName("modeNewScreenMenu")
        blocked = self._view_model.write_transaction.blocks_editing
        room = len(draft.profiles) < draft.max_profiles

        def then_refresh(action) -> None:
            action()
            self._refresh_mode_overview()

        create = menu.addAction(translate_ui_text("空白"))
        create.setObjectName("createProfileAction")
        create.setEnabled(room)
        create.triggered.connect(lambda: then_refresh(self._create_profile))
        save_as = menu.addAction(translate_ui_text("另存当前"))
        save_as.setObjectName("saveProfileAsAction")
        save_as.setEnabled(room and not blocked)
        save_as.triggered.connect(lambda: then_refresh(self._save_profile_as))
        if draft.hardware_id == "WMP-S3-MATRIX12-POWER-V2":
            existing = {str(profile.get("name", "")) for profile in draft.profiles}
            templates = [template for template in agent_profile_templates_for_platform(sys.platform)
                         if template.profile_name not in existing]
            ai = menu.addMenu(translate_ui_text("AI 模板"))
            ai.setObjectName("modeAiTemplateMenu")
            for template in templates:
                action = ai.addAction(template.menu_label)
                action.setObjectName(f"agentProfileTemplateAction_{template.key}")
                action.setProperty(SKIP_TRANSLATION_PROPERTY, True)
                action.setEnabled(room and not blocked)
                action.triggered.connect(lambda _checked=False, value=template: then_refresh(
                    lambda: self._create_agent_profile(value)))
            self._add_common_ai_menu(ai)
        menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height() // 2)))

    def _mode_export_menu(self) -> None:
        draft = self._view_model.draft
        stage = self._visible_mode_stage()
        if draft is None or stage is None:
            return
        if not draft.is_dirty:
            self._export_configuration("confirmed")
            return
        anchor = stage.overview.export_button
        menu = QMenu(anchor)
        menu.setObjectName("modeExportMenu")
        confirmed = menu.addAction(translate_ui_text("设备上已确认的配置…"))
        confirmed.setObjectName("exportConfirmedConfigurationAction")
        confirmed.triggered.connect(lambda: self._export_configuration("confirmed"))
        local = menu.addAction(translate_ui_text("连同没保存的改动…"))
        local.setObjectName("exportDraftConfigurationAction")
        local.triggered.connect(lambda: self._export_configuration("draft"))
        menu.exec(anchor.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def _add_common_ai_menu(self, menu: QMenu) -> None:
        """切换常用 AI and 设置整套 AI 方案…, under 新建一屏 › AI 模板."""
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        if draft is None or snapshot is None:
            return
        saved = load_device_choices(self._onboarding_settings, draft.serial)
        favorites = [(key, linked_profile(draft, saved.get(key))) for key in load_choices(self._onboarding_settings)]
        favorites = [(key, profile) for key, profile in favorites if profile is not None
                     and any(p.get("id") == profile["id"] and p.get("name") == profile["name"]
                             for p in snapshot.config.get("profiles", []))]
        menu.addSeparator()
        if not favorites:
            setup = menu.addAction(translate_ui_text("设置整套 AI 方案…"))
            setup.setObjectName("chooseCommonAI")
            setup.triggered.connect(self._show_ai_setup)
            return
        ai_menu = menu.addMenu(translate_ui_text("切换常用 AI"))
        for key, profile in favorites:
            label = AI_BY_KEY[key].name
            if not saved[key].get("tried"):
                label += " · " + self._language_manager.translate("待试用")
            action = ai_menu.addAction(label)
            action.setObjectName(f"commonAI_{key}")
            action.setCheckable(True)
            action.setChecked(profile["id"] == snapshot.active_profile_id)
            action.setEnabled(not self._view_model.write_transaction.blocks_editing)
            action.triggered.connect(lambda _checked=False, value=profile["id"]: self._activate_common_ai(value))
        ai_menu.addSeparator()
        manage = ai_menu.addAction(translate_ui_text("设置整套 AI 方案…"))
        manage.triggered.connect(self._show_ai_setup)

    def _choose_mode_control(self, control_id: str) -> None:
        if self._view_model.draft is None or self._view_model.write_transaction.blocks_editing:
            return
        if self._mode_chosen_control() == control_id:
            self._close_control_editor()
            return
        self._select_physical_control(control_id)
        if self._selected_control_id is not None:
            self._end_shortcut_task_prompt()  # the task goes on in the card

    def _other_action_context(self, action_editor: ActionEditor) -> OtherActionContext | None:
        """What 选别的动作… shows for the open key (Mode页 两个弹窗 v0, 03–05)."""
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        control_id = self._selected_control_id
        if draft is None or snapshot is None or not isinstance(control_id, str):
            return None
        supported = {d.action_type: d for d in self._view_model.action_definitions if d.action_type in draft.actions}
        part = MODE_PART_TITLES.get(control_id)
        if part is not None:
            title = part[0]
        elif control_id.startswith("key."):
            number = control_id.split(".")[1]
            kind = ("状态灯键" if control_id in MATRIX12_AGENT_STATUS_KEYS
                    else "语音键" if control_id == "key.8" else "功能键")
            title = f"{translate_ui_text(kind)} {number}"
        else:
            title = control_display_name(control_id)
        hidden = {kind for kind in ("consumer", "mouse", "macro", "key_gesture", "prompt", "profile", "device",
                                    "key", "none") if kind not in supported}
        greyed, notices = {}, {}
        if not control_id.startswith("key."):
            hidden |= {"key_gesture", "host_action"}    # the knob and the joystick never take these (03)
        elif "host_action" not in draft.actions:
            notices["host_action"] = "这台设备的固件还不能放电脑任务。固件更新以后再来。"
        elif control_id not in {"key.8", "key.9", "key.10", "key.11", "key.12"}:
            greyed["host_action"] = "只有功能键 8～12 能用"
        elif snapshot.connection_kind == "bluetooth":
            greyed["host_action"] = "要用 USB 线连着设备"
        platform = "darwin" if sys.platform == "darwin" else "win32"
        presets = {mapping["control_id"]: mapping for mapping in agent_profile_mappings("basic", platform=platform)}
        common = tuple((presets[control]["short_name"], presets[control]["action"])
                       for control in ("key.11", "key.12", "key.9", "key.10") if control in presets) \
            if "key" in supported else ()
        profile_id = self._editing_profile(draft)
        tasks = tuple((binding.name, {"type": "host_action", "action_id": binding.action_id,
                                      "task_token": binding.task_token})
                      for binding in self._view_model.host_tasks.bindings
                      if binding.applied and binding.control_id == control_id and binding.profile_id == profile_id)
        prompts = tuple(action_editor._reference_choices.get(("prompt", "prompt_id"), ()))
        return OtherActionContext(
            control_title=title,
            current=action_editor.action(),
            definitions=tuple(supported.values()),
            platform="macos" if sys.platform == "darwin" else "windows",
            common=common,
            hidden=frozenset(hidden),
            greyed=greyed,
            notices=notices,
            macros=tuple((int(macro.get("id", 0)), str(macro.get("name", "")),
                          len(macro.get("steps") or [])) for macro in draft.macros),
            max_macros=draft.editor_rules.max_macros,
            tasks=tasks,
            prompts=prompts,
            profiles=tuple((str(profile.get("name", "")), profile.get("id")) for profile in draft.profiles),
            active_profile=profile_id if isinstance(profile_id, int) else None,
        )

    def _open_other_action_dialog(self, action_editor: ActionEditor) -> None:
        context = self._other_action_context(action_editor)
        if context is None:
            return
        control_id = self._selected_control_id
        dialog = OtherActionDialog(self, context)
        accepted = dialog.exec()
        follow_up, chosen = dialog.follow_up, dialog.chosen
        dialog.deleteLater()
        if not accepted:
            return
        if follow_up == "sequences":
            self._navigate("sequences")
        elif follow_up == "tasks":
            self._view_model.host_tasks.preferred_control = control_id
            self._navigate("actions")
        elif chosen is not None:
            try:
                action_editor.set_action(chosen)   # the card changes; the bar says 这颗键有改动 (04)
            except ValueError as exc:
                notice.notify(self, "这个动作现在放不进这颗键", str(exc), state="wait")

    def _mode_blank_clicked(self) -> None:
        if self._selected_control_id is not None:
            self._close_control_editor()
        elif self._shortcut_task_requested:
            self._end_shortcut_task_prompt()  # closing it cancels the task

    def _mode_card(self, snapshot: DeviceSnapshot, editor: QWidget | None) -> QWidget | None:
        """The editor in the cream card, wired so the bottom bar follows every keystroke."""
        if editor is None:
            return None
        action_editor = editor.findChild(ActionEditor)
        short_name = editor.findChild(QLineEdit, "mappingShortNameEditor")
        if action_editor is not None:
            action_editor.action_changed.connect(lambda *_: self._mode_card_changed())
        if short_name is not None:
            short_name.textChanged.connect(lambda *_: self._mode_card_changed())
        return editor

    def _mode_card_changed(self) -> None:
        self._refresh_mode_bar()
        if self._view_model.model.state not in {AppState.READY, AppState.READ_ONLY}:
            # Without a device the change is kept by itself (Mode页 定稿 v1: no 仅保存草稿 button).
            self._mode_keep_timer.start()

    def _mode_keep_offline_edit(self) -> None:
        if (self._view_model.model.state not in {AppState.READY, AppState.READ_ONLY}
                and self._view_model.page == "overview"
                and self._mapping_editor_has_uncommitted_changes()):
            self._save_mapping_editor_draft()

    def _refresh_mode_workspace(
        self, snapshot: DeviceSnapshot, scroll: QScrollArea,
        editing_state: _MappingEditingState | None,
    ) -> bool:
        stage = scroll.findChild(ModeStage)
        if stage is None or not stage.isVisible():
            return False
        self._fill_mode_stage(stage, snapshot, self._view_model.model.state)
        return self._refresh_mapping_workspace(editing_state=editing_state)

    def _refresh_mapping_workspace(
        self, *, editing_state: _MappingEditingState | None = None, reuse_sync: bool = False,
    ) -> bool:
        """Rebuild only the card and the bar after a key is chosen, closed or edited."""
        snapshot = self._view_model.model.snapshot
        stage = self._visible_mode_stage()
        if self._view_model.page != "overview" or snapshot is None or stage is None:
            return False
        stage.device.set_chosen(self._mode_chosen_control())
        stage.set_zoom(self._mode_zoom_target(), self._selected_control_id)
        focus = QApplication.focusWidget()
        focus_name = focus.objectName() if focus is not None and stage.isAncestorOf(focus) else ""
        cursor = focus.cursorPosition() if isinstance(focus, QLineEdit) else None
        card = self._mode_card(snapshot, self._mapping_editor(snapshot, editing_state))
        stage.set_card(card)
        if card is not None and focus_name:
            # A kept edit rebuilds the card; typing carries on where it was.
            again = card.findChild(QWidget, focus_name)
            if again is not None:
                again.setFocus(Qt.OtherFocusReason)
                if isinstance(again, QLineEdit) and cursor is not None:
                    again.setCursorPosition(cursor)
        # This local edit already updated the selection. Reconnection must not
        # rebuild the same card because its cached selection was stale.
        scroll = self._content.findChild(QScrollArea, "overviewScroll")
        if scroll is not None:
            scroll.selected_control_id = self._selected_control_id
        if card is not None:
            self._language_manager.retranslate_widget_tree(card)
        self._refresh_mode_bar(stage)
        return True

    @staticmethod
    def _ble_slots_available(snapshot: DeviceSnapshot | None) -> bool:
        """Whether the device has the three Bluetooth computer slots the dialog shows."""
        if snapshot is None:
            return False
        features = snapshot.capabilities.get("features", {})
        slots = snapshot.status.get("codex_micro", {}).get("slots", [])
        return features.get("ble_host_slots") == 3 and len(slots) == 3

    def _show_ble_slots(self, snapshot: DeviceSnapshot, state: AppState) -> None:
        from controller_config.views.ble_connections import BleConnectionsDialog
        model = self._view_model.model
        snapshot, state = model.snapshot, model.state
        if not self._ble_slots_available(snapshot):
            return
        dialog = BleConnectionsDialog(self._view_model, snapshot, state, self)
        dialog.exec()
        dialog.deleteLater()

    def _background_state_changed(self, *_args) -> None:
        self.render(self._view_model.model)

    def _console_diagnostics_text(self) -> str:
        """设备信息卡「复制诊断信息」: the block the self-help prompt takes as is."""
        model = self._view_model.model
        snapshot = model.snapshot
        connected = snapshot is not None and model.state in {
            AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE}
        link = "none"
        device_lines: tuple[str, ...] = ()
        if connected:
            link = "ble" if snapshot.port_name.startswith("ble:") else "usb"
            device_lines = tuple(_technical_details_text(
                snapshot, self._view_model.diagnostics.connect_timing).splitlines())
        return console_diagnostics_block(
            link=link,
            build_origin=self._build_identity.runtime_origin,
            device_lines=device_lines,
            report_text=self._view_model.diagnostic_report_text(),
        )

    def _show_technical_details(self, snapshot: DeviceSnapshot) -> None:
        notice.alert(
            self,
            "设备技术详情",
            _technical_details_text(snapshot, self._view_model.diagnostics.connect_timing),
            kind="info",
        )

    def _begin_shortcut_binding(self) -> None:
        # The three-step guide is not drawn yet (Mode页 定稿 v1, 15): until it is,
        # the first step is said in a banner and the card follows the key chosen.
        self._shortcut_task_requested = True
        # Stays until a key is chosen; closing it before then cancels the task (the old 取消).
        banner = self._banners.notify(translate_ui_text("点一下设备上要设置的键"), state="wait",
                                      key="shortcutTask", timeout_ms=None, closable=True)
        banner.dismissed.connect(self._cancel_shortcut_task_if_waiting)
        self._refresh_mapping_workspace()

    def _cancel_shortcut_task_if_waiting(self) -> None:
        if self._shortcut_task_requested and self._selected_control_id is None:
            self._shortcut_task_requested = False
            self._refresh_mode_bar()

    def _end_shortcut_task_prompt(self) -> None:
        banner = self._banners.find("shortcutTask")
        if banner is not None:
            banner.dismiss()

    def _global_voice_available(self) -> bool:
        snapshot = self._view_model.model.snapshot
        return (sys.platform in {"darwin", "win32"} and snapshot is not None
                and snapshot.identity.get("hardware_id") == "WMP-S3-MATRIX12-POWER-V2"
                and snapshot.capabilities.get("features", {}).get("global_voice") is True)

    def _global_voice_card_text(self, draft, snapshot) -> tuple[str, bool]:
        """B-02: card copy for Key 8 when the device supports a shared voice key.

        Returns (text, offer_profile_voice). ``profile_action`` is computed up
        front so no branch depends on short-circuit evaluation.
        """
        from controller_config.voice_presets import describe_platform
        platform = describe_platform()
        action = draft.config.get("voice_action")
        profile_action = voice_mapping(snapshot, "key.8", self._configuration_mode).get("action") or {}
        if isinstance(action, dict):
            status = describe_action(action, platform=platform, control_id="key.8")
            if action != snapshot.config.get("voice_action"):
                status += " · 本地待保存"
            return f"Normal／Codex 共用 · 所有 Profile：{status}", False
        if action is not None:
            return ("设备级语音设置无法识别，未作修改；请重新设置设备级语音键。", False)
        status = (describe_action(profile_action, platform=platform, control_id="key.8")
                  if isinstance(profile_action, dict) and profile_action else "尚未设置")
        return (f"当前 Profile · {self._configuration_mode.upper()}：{status}；没有设备级语音覆盖。",
                isinstance(profile_action, dict) and profile_action.get("type") == "key_gesture")

    def _open_global_voice_setup(self) -> None:
        if not self._global_voice_available():
            return
        dialog = getattr(self, "_global_voice_dialog", None)
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = GlobalVoiceDialog(self._view_model, self, settings=self._onboarding_settings)
        self._global_voice_dialog = dialog
        dialog.finished.connect(lambda *_: setattr(self, "_global_voice_dialog", None))
        dialog.show()

    def show_agent_focus_error(self, message: str) -> None:
        if self._selected_control_id in MATRIX12_AGENT_STATUS_KEYS:
            self._refresh_mapping_workspace()
        self._agent_focus_message.setText(translate_ui_text("未能打开对话应用：") + translate_ui_text(message))
        self._agent_focus_notice.show()
        if not self.isActiveWindow() and self._background_controller is not None:
            self._background_controller.notify_failure("未能打开对话应用", translate_ui_text(message))

    def show_agent_open_failed(self, app_id: str, message: str) -> None:
        """A Doubao light press that did not open (-15 第 2 处, 乙: only the banner part).

        A banner slides in from the right like the others (the designer 11-20 真屏验收:
        "就改成同样滑进来就行"): orange triangle, "豆包没有响应",
        〔查看原因〕, and goes by itself after 4 s; no dialog, no focus, no page
        change. The ring round the key is not drawn: the failure signal does not
        say which key was pressed (needs the firmware owner). Other apps keep the old notice.
        """
        if app_id != "doubao":
            return
        self._agent_focus_notice.hide()   # the banner says it instead of the strip
        banner = Banner("豆包没有响应", state="error", key="doubao-open-failed",
                        object_name="doubaoOpenFailed")
        details = banner.add_button("查看原因", object_name="doubaoOpenFailedDetails")
        reason = translate_ui_text(message)
        details.clicked.connect(lambda: self._show_reason_with_diagnosis("豆包没有响应", reason))
        banner.set_timeout(tokens.BANNER_FAILED_HIDE_MS, even_with_buttons=True)
        self._banners.show(banner)

    def _show_diagnosis_prompt(self) -> None:
        """「让你的 AI 先帮忙查」: the problem kinds, one copy button each (design ticket)."""
        show_diagnosis_prompt(self, self._console_diagnostics_text)

    def _add_diagnosis_entry(self, dialog: Dialog) -> None:
        """「让 AI 帮忙查…」 at the bottom right of a 查看原因 dialog: closes it, opens the prompt."""
        entry = dialog.add_button(DIAGNOSIS_ENTRY_TEXT, "secondary", object_name="openDiagnosisPromptFromReason")

        def open_prompt(_checked=False) -> None:
            dialog.reject()
            self._show_diagnosis_prompt()

        entry.clicked.connect(open_prompt)

    def _show_reason_with_diagnosis(self, title: str, reason: str) -> None:
        """A failure's 查看原因: the reason as notice.alert shows it, plus 「让 AI 帮忙查…」."""
        dialog = Dialog(self, title, width=notice._width_for(reason), object_name="boringAlert")
        dialog.body_layout().addWidget(notice._message_row(reason, StatusIcon("error", size=20)))
        self._add_diagnosis_entry(dialog)
        if dialog.close_button is not None:
            dialog.close_button.setDefault(True)
            dialog.close_button.setFocus()
        notice._run(dialog)

    def _open_agent_focus_settings(self) -> None:
        self._navigate("overview")
        if self._selected_control_id not in MATRIX12_AGENT_STATUS_KEYS:
            self._select_physical_control(sorted(MATRIX12_AGENT_STATUS_KEYS)[0])

    def _add_agent_client_selector(self, layout: QVBoxLayout, *, compact: bool = False) -> None:
        clients = installed_agent_clients()
        selected = (self._onboarding_settings.value(AGENT_CLIENT_KEY, "", type=str)
                    if self._onboarding_settings is not None else "")
        if len(clients) <= 1 and not (selected and selected not in clients):
            return
        label = QLabel("接收的桌面应用" if compact else "接收设备操作的桌面应用")
        label.setProperty("muted", compact)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        (row if compact else layout).addWidget(label)
        selector = QComboBox(objectName="agentDesktopClient")
        selector.setAccessibleName("接收设备操作的桌面应用")
        label.setBuddy(selector)
        selector.addItem("选择桌面应用", "")
        for name in clients:
            selector.addItem(name, name)
        selector.setCurrentIndex(max(0, selector.findData(selected)))

        def save_client(_index):
            if self._onboarding_settings is not None:
                self._onboarding_settings.setValue(AGENT_CLIENT_KEY, selector.currentData())
                self._onboarding_settings.sync()

        selector.currentIndexChanged.connect(save_client)
        if compact:
            row.addWidget(selector, 1)
            layout.addLayout(row)
            selector.setToolTip(translate_ui_text("选择已连接此设备的应用；仅保存在这台电脑上。"))
            return
        layout.addWidget(selector)
        note = QLabel("选择已连接此设备的应用；仅保存在这台电脑上。")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _add_normal_agent_settings(self, card: QFrame, layout: QVBoxLayout) -> None:
        """状态灯键的卡 (Mode页 定稿 v1, 04): no buttons of its own; saving goes through the bar."""
        title = QLabel("按下状态灯键时", objectName="officialControlDefinitionTitle")
        title.setProperty("muted", True)
        layout.addWidget(title)
        selector = QComboBox(objectName="normalAgentBehavior")
        selector.setAccessibleName("按下状态灯键时")
        title.setBuddy(selector)
        selector.addItem("请选择按下后的操作", None)
        selector.addItem("打开对应对话", "open_conversation")
        selector.addItem("仅显示状态", "status_only")
        layout.addWidget(selector)
        selector.hide()   # shown as the two segments below; it keeps the logic
        segments = QFrame(objectName="modeCardPurposes")
        row = QHBoxLayout(segments)
        row.setContentsMargins(0, 0, 0, 0)   # the sheet pads 3 (scaled)
        row.setSpacing(2)
        for value, label in (("open_conversation", "打开对应对话"), ("status_only", "仅显示状态")):
            button = PurposeSegment(translate_ui_text(label))
            button.setObjectName("modeCardPurpose")
            button.setProperty("behavior", value)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, choice=value: selector.setCurrentIndex(selector.findData(choice)))
            row.addWidget(button, 1)
        layout.addWidget(segments)
        shared = QFrame(objectName="shortcutRecorder")   # the card's grey well
        shared_box = QVBoxLayout(shared)
        shared_box.setContentsMargins(12, 10, 12, 10)
        shared_box.setSpacing(4)
        shared_title = QLabel("6 颗状态灯键共用这一项", objectName="normalAgentSharedTitle")
        shared_box.addWidget(shared_title)
        explanation = QLabel("设备图上圈出来的 6 颗，改一颗等于全改；灯的颜色由 AI 软件决定，这里不能改。")
        explanation.setProperty("muted", True)
        explanation.setWordWrap(True)
        shared_box.addWidget(explanation)
        layout.addWidget(shared)
        description = QLabel(objectName="normalAgentBehaviorDescription")
        description.setWordWrap(True)
        description.hide()   # the segment says it; kept for the old tooltip text
        layout.addWidget(description)
        client_options = QWidget(objectName="normalAgentClientOptions")
        client_layout = QVBoxLayout(client_options)
        client_layout.setContentsMargins(0, 0, 0, 0)
        if sys.platform == "darwin":
            self._add_agent_client_selector(client_layout, compact=True)
        layout.addWidget(client_options)
        status = QLabel(objectName="normalAgentStatus")
        status.setProperty("muted", True)
        status.setWordWrap(True)
        layout.addWidget(status)
        # Only when the device's setting could not be read (04, 右): the line and 重新读取.
        unread = QFrame(objectName="normalAgentUnread")
        unread_row = QHBoxLayout(unread)
        unread_row.setContentsMargins(12, 8, 8, 8)
        unread_text = QLabel("没读到设备里现在的设置")
        unread_text.setWordWrap(True)
        unread_row.addWidget(unread_text, 1)
        retry = QPushButton("重新读取", objectName="normalAgentRead")
        retry.clicked.connect(lambda: self._normal_agent_action("read"))
        unread_row.addWidget(retry)
        layout.addWidget(unread)
        layout.addStretch(1)
        apply = QPushButton("应用到设备", objectName="normalAgentApply")
        apply.clicked.connect(lambda: self._normal_agent_action("save"))
        layout.addWidget(apply)
        apply.hide()   # merged into the bar's 保存到设备 (the designer 11-19); kept for its click path
        selector.currentIndexChanged.connect(
            lambda _index: self._normal_agent_action("edit", selector.currentData())
        )
        self._refresh_normal_agent_card(card)

    def _normal_agent_action(self, action: str, value: str | None = None) -> None:
        try:
            session = self._view_model.normal_agent
            if action == "edit":
                if value is not None:
                    session.edit(value)
            elif action == "save":
                session.save()
            else:
                session.read()
        except ValueError as exc:
            notice.alert(self, translate_ui_text("无法应用状态灯按键设置"),
                                translate_ui_text(str(exc)))
        self._refresh_normal_agent_controls()

    def _refresh_normal_agent_controls(self, *_args) -> None:
        for card in self.findChildren(QFrame, "mappingEditorCard"):
            if card.findChild(QComboBox, "normalAgentBehavior") is not None:
                self._refresh_normal_agent_card(card)

    def _refresh_normal_agent_card(self, card: QFrame) -> None:
        session = self._view_model.normal_agent
        selector = card.findChild(QComboBox, "normalAgentBehavior")
        snapshot = self._view_model.model.snapshot
        same_device = (snapshot is not None and tuple(card.property("deviceIdentity")) ==
                       (snapshot.identity["serial"], snapshot.identity["hardware_id"]))
        if not same_device:
            card.setEnabled(False)
            return
        card.setEnabled(True)
        with QSignalBlocker(selector):
            selector.setCurrentIndex(max(0, selector.findData(session.draft)))
        selector.setEnabled(session.connected and session.loaded and not session.busy and not session.uncertain)
        description = card.findChild(QLabel, "normalAgentBehaviorDescription")
        text = {"open_conversation": "按下后切到这颗灯对应的对话，并将应用带到前台。",
                "status_only": "灯光继续显示状态，按下不会切换对话。"}.get(session.draft, "")
        set_translatable_text(description, text)
        description.setToolTip(translate_ui_text(text))
        card.findChild(QWidget, "normalAgentClientOptions").setVisible(session.draft == "open_conversation")
        reason = self._view_model.normal_agent_write_block_reason()
        status = card.findChild(QLabel, "normalAgentStatus")
        set_translatable_text(status, (reason if session.dirty and not session.busy else "") or session.message)
        status.setToolTip(session.technical)
        apply = card.findChild(QPushButton, "normalAgentApply")
        apply.setEnabled(session.connected and session.loaded and session.dirty
                         and not session.busy and not session.uncertain and not reason)
        set_translatable_text(apply, "正在应用…" if session.busy and session.dirty else
                              "已应用" if session.loaded and session.value is not None and not session.dirty
                              else "应用到设备")
        apply.setToolTip(translate_ui_text(reason))
        retry = card.findChild(QPushButton, "normalAgentRead")
        retry.setEnabled(session.connected and not session.busy)
        current_row = card.findChild(QWidget, "modeCardCurrentRow")
        if current_row is not None:
            # 设备当前 · 打开对应对话 under the title (04); 上次读取 when it is not known now.
            caption = current_row.findChild(QLabel, "modeCardCurrent")
            value = current_row.findChild(QLabel, "normalAgentCurrent")
            if caption is None:
                caption = QLabel(objectName="modeCardCurrent")
                value = QLabel(objectName="normalAgentCurrent")
                value.setProperty("muted", True)
                current_row.layout().addWidget(caption)
                current_row.layout().addWidget(value, 1)
            names = {"open_conversation": "打开对应对话", "status_only": "仅显示状态"}
            set_translatable_text(caption, "设备当前" if session.loaded and not session.uncertain else "上次读取")
            set_translatable_text(value, names.get(session.value, "—"))
            current_row.setVisible(session.value in names)
        unread = card.findChild(QFrame, "normalAgentUnread")
        if unread is not None:
            unread.setVisible(session.connected and (not session.loaded or session.uncertain) and not session.busy)
        for button in card.findChildren(QPushButton, "modeCardPurpose"):
            if button.property("behavior") is not None:
                button.setChecked(button.property("behavior") == session.draft)
                button.setEnabled(selector.isEnabled())
        self._refresh_mode_bar()

    def _mapping_editor(
        self,
        snapshot: DeviceSnapshot,
        editing_state: _MappingEditingState | None = None,
    ) -> QWidget | None:
        draft = self._view_model.draft
        if draft is None or self._selected_control_id not in draft.controls:
            return None
        profile_id = self._editing_profile(draft)
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return None
        codex_voice = (draft.hardware_id in MATRIX12_HARDWARE_IDS
                       and self._configuration_mode == "codex"
                       and self._selected_control_id == "key.8")
        editing_mode = "codex" if codex_voice else "normal"
        mapping = ({"short_name": "", "action": draft.profile(profile_id).get("codex_voice", {"type": "none"})}
                   if codex_voice else draft.mapping(profile_id, self._selected_control_id))
        confirmed_profile = next(
            (profile for profile in snapshot.config.get("profiles", []) if profile.get("id") == profile_id),
            None,
        )
        if confirmed_profile is None:
            confirmed_mapping = {}
        elif codex_voice:
            confirmed_mapping = {"action": confirmed_profile.get("codex_voice", {"type": "none"})}
        else:
            confirmed_mapping = next(
                (entry for entry in confirmed_profile.get("mappings", [])
                 if entry.get("control_id") == self._selected_control_id),
                {},
            )
        # The editor and its readback use the host's names for the same HID usages.
        platform = "macos" if sys.platform == "darwin" else "windows"
        current_generation = snapshot.config_result.get("generation")
        base_changed = current_generation != draft.base_generation
        write_state = self._view_model.write_transaction.state

        card = ModeCard()
        card.setObjectName("mappingEditorCard")
        card.setProperty("controlId", self._selected_control_id)
        card.setProperty("editingMode", editing_mode)
        card.setProperty("profileId", profile_id)
        card.setProperty("deviceIdentity", (draft.serial, draft.hardware_id))
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        # Header (Mode页 定稿 v1, 01): the key number in dots, the key, what the device has now.
        header = QHBoxLayout()
        header.setSpacing(10)
        number = self._selected_control_id.split(".")[1] if self._selected_control_id.startswith("key.") else ""
        part = MODE_PART_TITLES.get(self._selected_control_id)   # a part of the zoomed knob / joystick (11)
        if number or part:
            header.addWidget(ModeKeyGlyph(number or part[1]), 0, Qt.AlignVCenter)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        if number or part:
            title_row.addWidget(ModeKeySwatch(), 0, Qt.AlignVCenter)
        kind = ("状态灯键" if self._selected_control_id in MATRIX12_AGENT_STATUS_KEYS
                else "语音键" if self._selected_control_id == "key.8"
                else "功能键" if number else part[0] if part else control_display_name(self._selected_control_id))
        selected_name = QLabel(kind)
        selected_name.setObjectName("inspectorTitle")
        selected_name.setToolTip(control_display_name(self._selected_control_id))
        title_row.addWidget(selected_name, 1)
        title_box.addLayout(title_row)
        current_row = QWidget(objectName="modeCardCurrentRow")
        current_layout = QHBoxLayout(current_row)
        current_layout.setContentsMargins(0, 0, 0, 0)
        current_layout.setSpacing(10)
        title_box.addWidget(current_row)
        if part:
            # A part of the knob / joystick: its title ("旋钮 · 逆时针", "Knob · Clockwise") gets a
            # line of its own under the dot letters, so it is never cut (design decision, the designer 11-22).
            header.addStretch(1)
            card_layout.addLayout(header)
            card_layout.addLayout(title_box)
        else:
            header.addLayout(title_box, 1)
        # No close button in the draft: a click beside the device closes the card.
        close = QPushButton("关闭", objectName="secondary")
        close.setAccessibleName("关闭控件设置")
        close.clicked.connect(self._close_control_editor)
        close.hide()
        header.addWidget(close, 0, Qt.AlignTop)
        if not part:
            card_layout.addLayout(header)

        if (self._global_voice_available() and self._selected_control_id == "key.8"
                and self._configuration_mode in {"normal", "codex"}):
            card.setProperty("officialReadOnly", True)
            description, profile_scoped = self._global_voice_card_text(draft, snapshot)
            description = QLabel(description, objectName="globalVoiceCardStatus")
            description.setWordWrap(True)
            card_layout.addWidget(description)
            if profile_scoped:
                scoped = QPushButton("设置当前 Profile 的语音键", objectName="configureProfileVoice")
                scoped.clicked.connect(lambda: self._open_voice_setup(
                    "key.8", mode=self._configuration_mode))
                card_layout.addWidget(scoped)
            button = QPushButton("设置设备级语音键", objectName="configureGlobalVoice")
            button.clicked.connect(self._open_global_voice_setup)
            card_layout.addWidget(button)
            card_layout.addWidget(QLabel("Claude Code 保持空格键。长按听写前请在输入区使用 /voice hold。"))
            card_layout.addStretch(1)
            return card

        if self._shortcut_task_requested:
            task = QLabel("2 · 设置操作 → 3 · 保存并在目标软件试用", objectName="mappingTaskProgress")
            task.setWordWrap(True)
            card_layout.addWidget(task)

        mode = self._configuration_mode or "normal"
        uses_dedicated_actions = (
            draft.hardware_id in MATRIX12_HARDWARE_IDS
            and mode in {"codex", "claude_code"}
        )
        official_status_key = (
            draft.hardware_id in MATRIX12_HARDWARE_IDS
            and self._selected_control_id in MATRIX12_AGENT_STATUS_KEYS
        )
        if uses_dedicated_actions and not codex_voice:
            card.setProperty("officialReadOnly", True)
            card_layout.addWidget(QLabel("固定功能", objectName="officialControlDefinitionTitle"))
            action_name = _dedicated_control_name(mode, self._selected_control_id)
            card_layout.addWidget(QLabel(action_name, objectName="officialControlAction"))
            note = QLabel("此功能由固件或目标应用处理，普通模式的自定义映射不会改写它。",
                          objectName="officialControlDefinitionNote")
            note.setWordWrap(True)
            card_layout.addWidget(note)
            if mode == "claude_code" and self._selected_control_id == "key.8":
                voice_note = QLabel("固定输出 Space；能否启动语音取决于 Claude Code 的语音设置。这里不提供第三方语音改键。")
                voice_note.setWordWrap(True)
                card_layout.addWidget(voice_note)
            if mode == "claude_code" and official_status_key:
                status_note = QLabel(
                    "此键显示 Claude Code 任务状态；当前尚不能按键进入对应会话。"
                    "状态灯的启用与故障处理在 Settings。",
                    objectName="claudeStatusCapability",
                )
                status_note.setWordWrap(True)
                card_layout.addWidget(status_note)
                status_settings = QPushButton("查看状态灯设置", objectName="claudeStatusSettingsLink")
                status_settings.setProperty("buttonRole", "secondary")
                status_settings.clicked.connect(lambda: self._select_settings_section("ai"))
                card_layout.addWidget(status_settings)
            if mode == "codex" and self._selected_control_id == "joystick.press":
                shared = QLabel("摇杆按下沿用普通模式的映射，请在普通模式配置。")
                shared.setWordWrap(True)
                card_layout.addWidget(shared)
                normal = QPushButton("前往普通模式配置", objectName="configureSharedNormalControl")
                normal.clicked.connect(lambda: self._select_configuration_mode("normal"))
                card_layout.addWidget(normal)
            if mode == "codex" and official_status_key and sys.platform == "darwin":
                self._add_agent_client_selector(card_layout)
            card_layout.addStretch(1)
            return card
        if official_status_key:
            card.setProperty("officialReadOnly", True)
            if mode == "normal" and self._view_model.normal_agent.supported:
                self._add_normal_agent_settings(card, card_layout)
                return card
            if mode == "normal":
                # Older firmware: say what pressing does now; the Mode page has no 查看固件更新 (04).
                title = QLabel("按下状态灯键时", objectName="officialControlDefinitionTitle")
                title.setProperty("muted", True)
                card_layout.addWidget(title)
                # Kept short: the card is narrow (design ticket 答复第 3 件).
                note = QLabel("现在按下只选中对应对话，不会把应用带到前台。",
                              objectName="officialControlDefinitionNote")
                note.setWordWrap(True)
                card_layout.addWidget(note)
                help_text = QLabel("更新固件后可选“打开对应对话”或“仅显示状态”（Settings › 更新），6 颗键共用。",
                                   objectName="normalAgentFirmwareNotice")
                help_text.setProperty("muted", True)
                help_text.setWordWrap(True)
                card_layout.addWidget(help_text)
                card_layout.addStretch(1)
                return card

        if codex_voice and draft.features.get("codex_voice") is not True:
            card.setProperty("officialReadOnly", True)
            card_layout.addWidget(QLabel("Codex 自带语音", objectName="officialControlDefinitionTitle"))
            notice = QLabel("此设备固件尚不支持 CODEX 模式第三方语音。", objectName="voiceFirmwareNotice")
            notice.setWordWrap(True)
            card_layout.addWidget(notice)
            update = QPushButton("查看固件更新", objectName="codexVoiceFirmwareUpdate")
            update.setProperty("buttonRole", "secondary")
            update.clicked.connect(lambda: self._select_settings_section("firmware"))
            card_layout.addWidget(update)
            card_layout.addStretch(1)
            return card

        body_scroll = QScrollArea(objectName="mappingEditorBody")
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setMinimumHeight(0)
        body_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        editor_body = QWidget()
        editor_layout = QVBoxLayout(editor_body)
        editor_layout.setContentsMargins(0, 0, 4, 0)
        editor_layout.setSpacing(10)
        capture_platform = platform
        body_scroll.setWidget(editor_body)
        card_layout.addWidget(body_scroll, 1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        related_controls = _related_controls(self._selected_control_id, draft.controls)
        if len(related_controls) > 1:
            trigger_selector = QComboBox(objectName="triggerSelector")
            for control_id in related_controls:
                trigger_selector.addItem(control_display_name(control_id), control_id)
            trigger_selector.setCurrentIndex(trigger_selector.findData(self._selected_control_id))
            trigger_selector.currentIndexChanged.connect(
                lambda _index, selector=trigger_selector: QTimer.singleShot(
                    0, self, lambda value=selector.currentData(): self._select_control(value)
                )
            )
            form.addRow("触发方式", trigger_selector)
            # Mode页 定稿 v1 (11): the parts are chosen on the zoomed knob / joystick, not here.
            form.setRowVisible(trigger_selector, False)

        current_action = (
            confirmed_mapping.get("action")
            if isinstance(confirmed_mapping, dict)
            else None
        )
        editing_action = mapping.get("action") if mapping else current_action
        actual_action = QLabel(
            ("此方案尚未保存到设备" if confirmed_profile is None else
             describe_action(current_action, platform=platform, control_id=self._selected_control_id)),
            objectName="actualActionValue",
        )
        actual_action.setMinimumWidth(0)
        actual_action.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        actual_action.setProperty("muted", True)
        actual_action.setWordWrap(True)   # a long English or Japanese action takes a second line
        current_caption = QLabel(
            "设备中此方案" if profile_id != snapshot.active_profile_id else
            "设备当前" if self._view_model.model.state in {AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE} else "上次读取",
            objectName="modeCardCurrent",
        )
        current_layout.addWidget(current_caption)
        current_layout.addWidget(actual_action, 1)

        short_name_editor = QLineEdit(_editor_name(self._selected_control_id, mapping))
        short_name_editor.setObjectName("mappingShortNameEditor")
        short_name_editor.setStyleSheet("QLineEdit#mappingShortNameEditor { min-width: 0; }")
        short_name_editor.setValidator(NameLengthValidator(draft.editor_rules.mapping_short_name_max_length, short_name_editor))
        short_name_editor.setPlaceholderText("例如：新建任务")
        short_name_editor.setToolTip(
            f"用于辅助识别这个控件，最多 {draft.editor_rules.mapping_short_name_max_length} 个字符"
        )

        definitions = tuple(
            definition
            for definition in self._view_model.action_definitions
            if (definition.action_type != "macro" or draft.macros)
            and (definition.action_type != "host_action" or (isinstance(editing_action, dict) and editing_action.get("type") == "host_action"))
        )
        prompt_library = self._view_model.prompt_library
        visible_prompt_ids = list(QUICK_PROMPT_IDS)
        current_prompt_id = (
            editing_action.get("prompt_id")
            if isinstance(editing_action, dict)
            else None
        )
        if (
            isinstance(editing_action, dict)
            and editing_action.get("type") == "prompt"
            and isinstance(current_prompt_id, int)
            and not isinstance(current_prompt_id, bool)
            and current_prompt_id not in visible_prompt_ids
        ):
            visible_prompt_ids.append(current_prompt_id)
        direction_by_prompt_id = {
            prompt_id: (arrow, label)
            for _control_id, prompt_id, arrow, label in QUICK_PROMPT_DIRECTIONS
        }
        reference_choices = {
            ("host_action", "action_id"): tuple((b.name, b.action_id) for b in self._view_model.host_tasks.bindings if b.applied),
            ("macro", "macro_id"): tuple(
                (
                    str(macro.get("name", f"按键序列 {macro.get('id')}")),
                    macro.get("id"),
                )
                for macro in draft.macros
            ),
            ("profile", "profile_id"): tuple(
                (str(profile.get("name", f"Profile {profile.get('id')}")), profile.get("id"))
                for profile in draft.profiles
            ),
            ("prompt", "prompt_id"): tuple(
                (
                    (
                        (
                            f"{direction_by_prompt_id[prompt_id][0]} "
                            f"{direction_by_prompt_id[prompt_id][1]} · {entry.name}"
                            if prompt_id in direction_by_prompt_id
                            else f"当前高级提示词 {prompt_id:02d} · {entry.name}"
                        )
                        if (entry := prompt_library.draft_entry(prompt_id)) is not None
                        else (
                            f"{direction_by_prompt_id[prompt_id][0]} "
                            f"{direction_by_prompt_id[prompt_id][1]} · 未设置"
                            if prompt_id in direction_by_prompt_id
                            else f"当前高级提示词 {prompt_id:02d}"
                        )
                    ),
                    prompt_id,
                )
                for prompt_id in visible_prompt_ids
            )
            if prompt_library is not None
            else tuple(
                (
                    f"{direction_by_prompt_id[prompt_id][0]} "
                    f"{direction_by_prompt_id[prompt_id][1]}",
                    prompt_id,
                )
                for prompt_id in QUICK_PROMPT_IDS
            ),
        }
        initial_action = (
            editing_action
            if isinstance(editing_action, dict)
            else next(
                (
                    definition
                    for definition in definitions
                    if definition.action_type == "none"
                ),
                definitions[0],
            ).default_action()
        )
        action_editor = ActionEditor(
            definitions,
            initial_action,
            platform=capture_platform,
            capture_platform=capture_platform,
            show_manual_controls=not codex_voice,
            codex_voice=codex_voice,
            allow_voice_preset=(
                (codex_voice or sys.platform == "darwin")
                and draft.hardware_id in MATRIX12_HARDWARE_IDS
                and self._selected_control_id in {"key.8", "key.9", "key.10", "key.11", "key.12"}
            ),
            reference_choices=reference_choices,
        )
        if codex_voice and initial_action.get("type") not in {"key", "key_gesture"}:
            saved = confirmed_voice_choice(self._onboarding_settings, draft.serial, profile_id)
            if saved is not None:
                action_editor.set_remembered_voice_action(saved["action"])
        if not codex_voice:
            action_editor.voice_preset_selected.connect(
                lambda: short_name_editor.setText(translate_ui_text("语音输入"))
            )
        voice_control = self._selected_control_id
        action_editor.voice_demo_pressed.connect(lambda: self._preview_voice_key(voice_control))
        action_editor.voice_setup_requested.connect(lambda: self._open_voice_setup(
            voice_control, mode=editing_mode, candidate_action=action_editor.action(),
            short_name=(_stored_name(short_name_editor.text()) if short_name_editor.text() !=
                        _editor_name(voice_control, mapping) else None)))
        if (
            editing_state is not None
            and editing_state.mode == editing_mode
            and editing_state.control_id == self._selected_control_id
            and editing_state.device_identity == (draft.serial, draft.hardware_id)
        ):
            short_name_editor.setText(editing_state.short_name)
            action_editor.set_action(editing_state.action)
        voice_identity = (snapshot.identity.get("serial"), profile_id,
                          self._configuration_mode, self._selected_control_id)
        if self._primary_voice_intent == voice_identity:
            action_editor.set_purpose("native")
        if action_editor._purpose is not None:
            def remember_purpose(_index):
                self._primary_voice_intent = (voice_identity
                    if action_editor._purpose.currentData() == "native" else None)
            action_editor._purpose.currentIndexChanged.connect(remember_purpose)
        _mode_purpose_segments(action_editor, codex_voice=codex_voice)
        form.addRow(action_editor)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_label = QLabel("名称", objectName="modeCardNameLabel")
        name_label.setProperty("muted", True)
        name_label.setBuddy(short_name_editor)
        short_name_editor.setAccessibleName("快捷键名称（可选）")
        name_row.addWidget(name_label)
        name_row.addWidget(short_name_editor, 1)
        name_widget = QWidget()
        name_widget.setLayout(name_row)
        name_row.setContentsMargins(0, 0, 0, 0)
        form.addRow(name_widget)
        if codex_voice:
            short_name_editor.clear()
            form.setRowVisible(name_widget, False)
        editor_layout.addLayout(form)
        if not codex_voice and self._selected_control_id in {"key.8", "key.9", "key.10", "key.11", "key.12"} and draft.hardware_id in MATRIX12_HARDWARE_IDS:
            tasks = QPushButton("配置电脑任务…", objectName="configureComputerTask")
            tasks.setProperty("buttonRole", "secondary")
            control_id = self._selected_control_id
            def open_tasks():
                self._view_model.host_tasks.preferred_control = control_id
                self._navigate("actions")
            tasks.clicked.connect(open_tasks)
            advanced = QPushButton("高级自定义", objectName="mappingAdvancedToggle")
            advanced.setProperty("buttonRole", "ghost")
            advanced.setCheckable(True)
            advanced.toggled.connect(tasks.setVisible)
            editor_layout.addWidget(advanced)
            advanced.hide()
            tasks.hide()
            editor_layout.addWidget(tasks)
        sequences = QPushButton("按键序列 / 连续动作…", objectName="mappingKeySequences")
        sequences.setProperty("buttonRole", "ghost")
        sequences.clicked.connect(lambda: self._navigate("sequences"))
        editor_layout.addWidget(sequences)
        sequences.hide()
        if self._selected_control_id.startswith("joystick."):
            calibration = QPushButton("摇杆校准…", objectName="mappingJoystickCalibration")
            calibration.setProperty("buttonRole", "ghost")
            calibration.setEnabled(
                _planned_tool_capability("joystick", snapshot) is True
                and self._view_model.model.state is AppState.READY
                and snapshot.compatibility.get("write") is True
                and not self._view_model.write_transaction.blocks_editing
                and not self._view_model.firmware_update.blocks_editing
            )
            calibration.clicked.connect(lambda: self._navigate("joystick"))
            editor_layout.addWidget(calibration)
            calibration.hide()
        editor_layout.addStretch(1)

        editing_enabled = not self._view_model.write_transaction.blocks_editing
        save_actions = QBoxLayout(QBoxLayout.LeftToRight)
        # The footer exists before its buttons: setVisible(True) on a widget with no
        # parent shows it as a window of its own. update_apply_state() and the
        # draft button below set visibility before the layout adopts them, which
        # flashed a window and took focus from the main window (a recording on the
        # card then cancelled itself on the first click).
        footer = QWidget(objectName="mappingEditorFooter")
        apply_to_device = QPushButton(
            "保存全部更改到设备",
            footer,
            objectName="applyMappingToDevice",
        )
        apply_to_device.setProperty("buttonRole", "primary")
        apply_to_device.setToolTip(
            "保存所有待保存配置，不仅是当前控件；设备读回确认后才显示已保存。"
        )
        can_apply = (
            editing_enabled
            and self._view_model.model.state is AppState.READY
            and not base_changed
            and snapshot.compatibility.get("write") is True
            and write_state
            in {
                ConfigTransactionState.IDLE,
                ConfigTransactionState.ACTIVE,
                ConfigTransactionState.FAILED,
            }
        )
        scope = QLabel(objectName="mappingSaveScope")
        scope.setWordWrap(True)

        def update_apply_state() -> None:
            try:
                # A matching voice preset is already explained by the gesture
                # rows. Keep readback visible when the pending action differs.
                current_row.setVisible(not codex_voice and not (
                    is_voice_input_action(current_action)
                    and is_voice_input_action(action_editor.action())
                ))
                baseline_action = (
                    editing_action
                    if isinstance(editing_action, dict)
                    else {"type": "none"}
                )
                pending = (short_name_editor.text() != _editor_name(self._selected_control_id, mapping)
                           or _canonical_action(action_editor.action()) != _canonical_action(baseline_action))
                supported = action_editor.action().get("type") in draft.actions
                if action_editor._purpose is not None and action_editor._purpose.currentData() == "native":
                    supported = supported and action_editor.action().get("type") == "key"
            except ValueError:
                pending = False
                supported = False
            connect_voice = action_editor.findChild(QPushButton, "voiceConnectApp")
            if connect_voice is not None:
                ready_for_setup = can_apply and supported and (
                    not draft.is_dirty or voice_draft_is_scoped(draft.config, snapshot, voice_control, editing_mode))
                connect_voice.setEnabled(ready_for_setup)
                connect_voice.setToolTip("" if ready_for_setup else translate_ui_text("需要设备连接正常，并先处理其他未应用的修改。"))
                committed_action = current_action if isinstance(current_action, dict) else action_editor.action()
                trial_state = voice_trial_state(
                    self._onboarding_settings, draft.serial, profile_id,
                    voice_control, committed_action, mode=editing_mode,
                )
                tried = voice_trial(self._onboarding_settings, draft.serial, profile_id,
                                    voice_control, committed_action, mode=editing_mode)
                set_translatable_text(connect_voice, "语音输入设置" if tried else "设置语音输入")
                status = action_editor.findChild(QLabel, "voiceSetupStatus")
                if status is not None:
                    if tried and not pending:
                        target_key = tried.get("target")
                        target_name = AI_BY_KEY[target_key].name if target_key in AI_BY_KEY else "目标应用"
                        set_translatable_text(status, f"上次已在 {target_name} 验证")
                    elif tried and pending:
                        set_translatable_text(status, "当前修改尚未应用；上次验证仍对应设备中的设置。")
                    elif trial_state == "stale":
                        set_translatable_text(status, "设置已更改，需要重新试用。")
                    else:
                        set_translatable_text(status, "选择你使用的语音输入软件，跟着步骤完成设置和试用。")
                # Voice setup owns choosing the shortcut and applying it. The
                # regular Apply action remains for restoring official dictation.
                apply_to_device.setVisible(action_editor.action().get("type") != "key_gesture"
                                           or (draft.is_dirty and not ready_for_setup))
            apply_to_device.setEnabled(can_apply and supported and (pending or draft.is_dirty))
            transaction = self._view_model.write_transaction
            applied = (can_apply and supported and not pending and not draft.is_dirty
                       and isinstance(current_action, dict))
            if transaction.is_busy:
                apply_label = "正在保存…"
            elif transaction.state is ConfigTransactionState.UNKNOWN:
                apply_label = "等待连接确认"
            elif transaction.state is ConfigTransactionState.FAILED:
                apply_label = "重试保存全部更改"
            elif applied:
                apply_label = "设备配置已保存"
            else:
                apply_label = "保存全部更改到设备"
            apply_to_device.setText(translate_ui_text(apply_label))
            apply_to_device.setIcon(_navigation_icon("checkmark", QIcon(), white=.9) if applied else QIcon())
            if applied and profile_id != snapshot.active_profile_id:
                scope.setProperty(SKIP_TRANSLATION_PROPERTY, False)
                scope.setToolTip("")
                set_translatable_text(scope, "此方案已保存到设备；请明确启用后再在目标软件试用。")
            elif applied and snapshot.status.get("operating_mode") != mode:
                scope.setProperty(SKIP_TRANSLATION_PROPERTY, True)
                scope.setText(translate_ui_text("已保存。切到 {mode} 后试用。").format(mode=_configuration_mode_label(mode)))
                scope.setToolTip(translate_ui_text("配置已保存。双击旋钮切到 {mode} 后，在目标软件中试用。").format(mode=_configuration_mode_label(mode)))
            else:
                scope.setProperty(SKIP_TRANSLATION_PROPERTY, False)
                scope.setToolTip("")
                set_translatable_text(scope, "已保存。在目标软件中试用此控件。"
                                      if applied else "保存会包含当前控件及其他所有待保存配置。")

        action_editor.action_changed.connect(update_apply_state)
        short_name_editor.textChanged.connect(update_apply_state)
        update_apply_state()

        def save_and_apply() -> None:
            action = action_editor.action()
            previous = current_action if isinstance(current_action, dict) else {}
            needs_setup = action.get("type") == "key_gesture" and (
                _canonical_action(previous) != _canonical_action(action)
            )
            if self._save_mapping_editor_draft():
                if needs_setup:
                    self._voice_setup_pending = (draft.serial, voice_control, copy.deepcopy(action), editing_mode)
                self._prepare_device_write(confirm_after_validation=True)

        apply_to_device.clicked.connect(save_and_apply)
        save_actions.addWidget(apply_to_device, 1)
        if self._view_model.model.state not in {AppState.READY, AppState.READ_ONLY}:
            apply_to_device.setToolTip("需要连接并读取设备；当前修改可先保存为本地草稿。")
        save = QPushButton("仅保存草稿", footer, objectName="saveMappingDraft")
        save.setProperty("buttonRole", "secondary")
        save.clicked.connect(self._save_mapping_editor_draft)
        save.setEnabled(editing_enabled)
        # Keep the healthy connected path to one obvious action.  Local draft
        # saving remains available when the device cannot currently accept a
        # write (offline, read-only, conflict, or another blocking operation).
        save.setVisible(
            self._view_model.model.state is not AppState.READY
            or snapshot.compatibility.get("write") is not True
            or base_changed
        )
        if not save.isHidden():
            save_actions.setDirection(QBoxLayout.TopToBottom)
        save_actions.addWidget(save)
        # Saving moved to the bottom bar (保存到设备); these stay for its click path.
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.addWidget(scope)
        footer_layout.addLayout(save_actions)
        footer.hide()
        card_layout.addWidget(footer)
        _mode_other_actions(card, editor_layout, action_editor,
                            lambda: self._open_other_action_dialog(action_editor))
        _mode_voice_panel(action_editor, self._open_global_voice_setup if self._global_voice_available() else None)
        return card

    def _write_transaction_card(
        self,
        snapshot: DeviceSnapshot,
        base_changed: bool,
        *,
        show_prepare: bool = True,
    ) -> QWidget:
        draft = self._view_model.draft
        transaction = self._view_model.write_transaction
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(10)
        layout.addWidget(QLabel("写入设备", objectName="eyebrow"))
        title = QLabel(_transaction_title(transaction.state), objectName="inspectorTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        message = QLabel(transaction.message, objectName="muted")
        message.setWordWrap(True)
        layout.addWidget(message)
        if transaction.technical and transaction.state in {
            ConfigTransactionState.FAILED, ConfigTransactionState.UNKNOWN,
            ConfigTransactionState.CONFLICT,
        }:
            _add_technical_details(layout, "configurationWrite", transaction.technical)
        buttons = QHBoxLayout() if show_prepare else QVBoxLayout()
        if transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION:
            confirm = QPushButton("确认写入设备", objectName="confirmConfigurationWrite")
            confirm.setProperty("buttonRole", "primary")
            confirm.setEnabled(self._view_model.model.state is AppState.READY)
            confirm.clicked.connect(self._confirm_device_write)
            buttons.addWidget(confirm)
            cancel = QPushButton("取消", objectName="secondary")
            cancel.clicked.connect(self._view_model.cancel_device_write_confirmation)
            buttons.addWidget(cancel)
        elif transaction.state is ConfigTransactionState.UNKNOWN:
            reconcile = QPushButton("确认设备保存结果", objectName="primary")
            reconcile.clicked.connect(self._reconcile_device_write)
            buttons.addWidget(reconcile)
        elif transaction.state is ConfigTransactionState.CONFLICT:
            if base_changed:
                rebase = QPushButton("基于设备最新版本重新校验草稿", objectName="primary")
                rebase.clicked.connect(self._rebase_conflicted_draft)
                buttons.addWidget(rebase)
                use_device = QPushButton("放弃草稿并使用设备配置", objectName="secondary")
                use_device.clicked.connect(self._discard_conflicted_draft)
                buttons.addWidget(use_device)
            else:
                refresh = QPushButton("重新读取设备最新配置", objectName="primary")
                refresh.clicked.connect(self._refresh_conflicted_device)
                buttons.addWidget(refresh)
        elif not transaction.is_busy and show_prepare:
            prepare = QPushButton("写入设备", objectName="primary")
            errors = self._view_model.validate_draft()
            writable = snapshot.compatibility.get("write") is True
            prepare.setEnabled(
                draft is not None
                and draft.is_dirty
                and not base_changed
                and not errors
                and writable
                and self._view_model.model.state is AppState.READY
            )
            prepare.clicked.connect(
                lambda: self._prepare_device_write(confirm_after_validation=True)
            )
            buttons.addWidget(prepare)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    def _prepare_device_write(self, *, confirm_after_validation: bool = True) -> None:
        try:
            self._view_model.prepare_device_write(
                confirm_after_validation=confirm_after_validation
            )
        except ValueError as exc:
            self._voice_setup_pending = None
            notice.alert(self, "无法验证设备配置", str(exc))

    def _confirm_device_write(self) -> None:
        draft = self._view_model.draft
        transaction = self._view_model.write_transaction
        changes = len(draft.changes) if draft is not None else 0
        choice = notice.ask(
            self,
            translate_ui_text("确认保存到设备"),
            translate_ui_text("将 {changes} 项修改保存到设备 {serial}？\n\n请保持连接，等待控制台确认保存结果。连接中断时不会自动重复保存。").format(
                changes=changes, serial=transaction.device_serial,
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Yes: "保存到设备"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.confirm_device_write()
        except ValueError as exc:
            notice.alert(self, "无法写入设备", str(exc))

    def _reconcile_device_write(self) -> None:
        try:
            self._view_model.reconcile_device_write()
        except ValueError as exc:
            notice.alert(self, "无法重新对账", str(exc))

    def _refresh_conflicted_device(self) -> None:
        try:
            self._view_model.refresh_conflicted_device()
        except ValueError as exc:
            notice.alert(self, "无法重新读取设备", str(exc))

    def _rebase_conflicted_draft(self) -> None:
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        if draft is None or snapshot is None:
            return
        changes = draft.preview_changes(snapshot.config)
        choice = notice.ask(
            self,
            "基于设备最新版本重新校验草稿",
            (
                f"当前完整草稿与设备最新配置有 {len(changes)} 项差异。\n\n"
                "继续后不会自动合并或写入；草稿将绑定到设备最新 generation，"
                "你仍需重新验证并再次确认整份配置。"
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Yes: "重新校验"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.rebase_conflicted_draft()
        except ValueError as exc:
            notice.alert(self, "无法重新绑定草稿", str(exc))

    def _discard_conflicted_draft(self) -> None:
        choice = notice.ask(
            self,
            "放弃本地草稿",
            "本地未写入修改将被放弃，并改用设备最新配置。需要保留时请先从 Mode 的配置方案菜单导出草稿。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            danger=True,
            labels={QMessageBox.Yes: "放弃草稿"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.discard_conflicted_draft()
        except ValueError as exc:
            notice.alert(self, "无法使用设备配置", str(exc))

    def _mapping_editor_has_uncommitted_changes(self) -> bool:
        draft = self._view_model.draft
        card = self._current_mapping_card()
        editing_state = self._current_mapping_editing_state()
        if draft is None:
            return False
        if card is None or card.property("officialReadOnly") is True:
            editing_state = self._pending_mapping_editing_state
            if editing_state is None or editing_state.control_id != self._selected_control_id:
                return False
        if editing_state is None:
            return True
        if editing_state.device_identity != (draft.serial, draft.hardware_id):
            return False
        control_id = editing_state.control_id
        profile_id = self._editing_profile(draft)
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return False
        mapping = ({"short_name": "", "action": draft.profile(profile_id).get("codex_voice", {"type": "none"})}
                   if editing_state.mode == "codex" else draft.mapping(profile_id, control_id))
        snapshot = self._view_model.model.snapshot
        confirmed_mapping = (
            snapshot.mappings.get(control_id) if snapshot is not None else None
        )
        saved_short_name = _editor_name(control_id, mapping)
        saved_action = (
            mapping.get("action")
            if mapping
            else (
                confirmed_mapping.get("action")
                if isinstance(confirmed_mapping, dict)
                else {"type": "none"}
            )
        )
        return (
            editing_state.short_name != saved_short_name
            or _canonical_action(editing_state.action) != _canonical_action(saved_action)
        )

    def _current_mapping_editing_state(self) -> _MappingEditingState | None:
        if self._content.property("discardMappingEdits"):
            return None
        card = self._current_mapping_card()
        if card is None:
            return None
        control_id = card.property("controlId")
        if not isinstance(control_id, str):
            return None
        short_name = card.findChild(QLineEdit, "mappingShortNameEditor")
        action_editor = card.findChild(ActionEditor)
        if short_name is None or action_editor is None:
            return None
        try:
            return _MappingEditingState(
                device_identity=tuple(card.property("deviceIdentity")),
                control_id=control_id,
                short_name=short_name.text(),
                action=action_editor.action(),
                mode=card.property("editingMode") or "normal",
            )
        except ValueError:
            return None

    def _current_mapping_card(self) -> QFrame | None:
        cards = self._content.findChildren(QFrame, "mappingEditorCard")
        return next(
            (
                candidate
                for candidate in reversed(cards)
                if candidate.property("controlId") == self._selected_control_id
            ),
            None,
        )

    def _save_mapping_editor_draft(self) -> bool:
        draft = self._view_model.draft
        card = self._current_mapping_card()
        if draft is None:
            return False
        if card is not None and card.property("officialReadOnly") is not True:
            short_name = card.findChild(QLineEdit, "mappingShortNameEditor")
            editor = card.findChild(ActionEditor)
            if short_name is None or editor is None:
                return False
            try:
                editing_state = _MappingEditingState(
                    device_identity=tuple(card.property("deviceIdentity")),
                    control_id=str(card.property("controlId")),
                    short_name=short_name.text(),
                    action=editor.action(),
                    mode=card.property("editingMode") or "normal",
                )
            except ValueError as exc:
                notice.notify(self, "动作参数有误", str(exc))
                return False
        else:
            editing_state = self._pending_mapping_editing_state
        if (editing_state is None or editing_state.control_id != self._selected_control_id
                or editing_state.device_identity != (draft.serial, draft.hardware_id)):
            return False
        action = editing_state.action
        profile_id = self._editing_profile(draft)
        control_id = editing_state.control_id
        saved = ({"short_name": "", "action": draft.profile(profile_id).get("codex_voice", {"type": "none"})}
                 if editing_state.mode == "codex" else draft.mapping(profile_id, control_id))
        if saved is not None and _canonical_action(action) == _canonical_action(saved.get("action")):
            action = saved["action"]
            if editing_state.short_name == _editor_name(control_id, saved):
                return True
        self._suppress_mapping_edit_restore = True
        try:
            if editing_state.mode == "codex":
                self._view_model.set_codex_voice(profile_id, action)
            else:
                self._view_model.set_mapping(profile_id, control_id, _stored_name(editing_state.short_name), action)
        except ValueError as exc:
            notice.notify(self, "当前不能修改草稿", str(exc), state="wait")
            return False
        else:
            self._pending_mapping_editing_state = None
            return True
        finally:
            self._suppress_mapping_edit_restore = False

    def _confirm_leave_mapping_editor(self) -> bool:
        if not self._mapping_editor_has_uncommitted_changes():
            return True
        choice = confirm_local_draft(
            self,
            "按键修改尚未应用",
            "保留草稿可在本次使用中继续编辑，尚未应用到设备。退出控制台前需导出草稿。",
        )
        if choice == QMessageBox.Save:
            return self._save_mapping_editor_draft()
        if choice == QMessageBox.Discard:
            self._pending_mapping_editing_state = None
            overview_host = self._page_hosts.get("overview")
            if overview_host is not None:
                overview_host.setProperty("discardMappingEdits", True)
                scroll = overview_host.findChild(QScrollArea, "overviewScroll")
                if scroll is not None:
                    scroll.render_inputs = None
            self._suppress_mapping_edit_restore = True
            try:
                self.render(self._view_model.model)
            finally:
                self._suppress_mapping_edit_restore = False
            return True
        return False

    def _leave_codex_screen(self) -> None:
        """A set chosen or made from the set menu is shown on its own screen, also from Codex."""
        if self._configuration_mode == "codex":
            self._mode_control_selection["codex"] = self._selected_control_id
            self._configuration_mode = "normal"

    def _activate_profile(self, profile_id: int) -> None:
        draft = self._view_model.draft
        if draft is None or (self._editing_profile(draft) == profile_id and self._configuration_mode != "codex"):
            return
        if self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        draft.profile(profile_id)
        self._leave_codex_screen()
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        self._editing_profile_id = profile_id
        self._suppress_mapping_edit_restore = True
        try:
            self.render(self._view_model.model)
        finally:
            self._suppress_mapping_edit_restore = False

    def _enable_profile(self, profile_id: int) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        if self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        if profile_id == draft.config["active_profile"] and not draft.is_dirty:
            return
        profile_name = str(draft.profile(profile_id).get("name", "Profile"))
        snapshot = self._view_model.model.snapshot
        current_name = snapshot.profile_name if snapshot is not None else ""
        # 09-点了启用到设备以后的四步: the six strings of the button and this box are the designer's.
        text = translate_ui_text(
            "设备现在用的是“{current}”。确认后会把“{name}”写进设备，再读回来核对一遍，核对无误才算换好。"
        ).format(current=current_name, name=profile_name)
        changes = list(self._draft_change_summary())
        if changes:
            text += "\n\n" + translate_ui_text("同时保存 {count} 处还没保存的修改").format(count=len(changes))
            text += "\n" + "\n".join(changes[:12])
            if len(changes) > 12:
                text += "\n" + translate_ui_text("另有 {count} 项修改，可从待保存修改查看。").format(count=len(changes) - 12)
        choice = notice.ask(
            self,
            translate_ui_text("让设备改用“{name}”这一套？").format(name=profile_name),
            text,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Yes: "启用到设备"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            if profile_id != draft.config["active_profile"]:
                self._view_model.set_active_profile(profile_id)
            # Confirmed above: once the device validates it, write and read back without asking again.
            self._prepare_device_write(confirm_after_validation=True)
        except ValueError as exc:
            notice.alert(self, "无法启用配置方案", str(exc))

    def _activate_common_ai(self, profile_id: int) -> None:
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        if draft is None or snapshot is None:
            return
        if (self._mapping_editor_has_uncommitted_changes()
                and not self._save_mapping_editor_draft()):
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        self._editing_profile_id = profile_id
        if snapshot.active_profile_id != profile_id or draft.is_dirty:
            self._enable_profile(profile_id)

    def _rename_profile(self) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        profile_id = self._editing_profile(draft)
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return
        profile = draft.profile(profile_id)
        current_name = str(profile.get("name", ""))
        name, accepted = QInputDialog.getText(
            self,
            "重命名配置方案",
            "配置方案名称",
            text=current_name,
        )
        if not accepted:
            return
        name = name.strip()
        if not name:
            notice.notify(self, "名称不可用", "配置方案名称不能为空。")
            return
        if len(name) > draft.editor_rules.profile_name_max_length:
            notice.notify(
                self,
                "名称过长",
                f"配置方案名称最多 {draft.editor_rules.profile_name_max_length} 个字符。",
            )
            return
        try:
            self._view_model.rename_profile(profile_id, name)
        except ValueError as exc:
            notice.notify(self, "无法重命名配置方案", str(exc))

    def _show_draft_changes(self, candidate: _MappingEditingState | None = None) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        changes = draft.changes
        lines = list(self._draft_change_summary(candidate))
        technical = [f"{change.path}\n  {_short_value(change.before)}  →  {_short_value(change.after)}"
                     for change in changes[:24]]
        if len(changes) > 24:
            technical.append(f"还有 {len(changes) - 24} 项更改…")
        text = "\n\n".join(lines) if lines else translate_ui_text("当前本地草稿与设备已确认配置一致。")
        if technical:
            text += "\n\n" + translate_ui_text("保存会包含以下全部修改，不只是当前控件。")
            text += "\n\n" + translate_ui_text("以下技术明细仅包含已保留草稿；当前编辑值见上方摘要。")
            text += "\n\n" + "\n\n".join(technical)
        notice.alert(self, "全部待保存修改", text, kind="info", object_name="pendingChangesList")

    def _draft_change_summary(self, candidate: _MappingEditingState | None = None) -> tuple[str, ...]:
        draft = self._view_model.draft
        if draft is None:
            return ()
        before, after = draft.confirmed_config, draft.config
        lines = []
        card = self._current_mapping_card()
        editable = card is not None and card.property("officialReadOnly") is not True
        editing = candidate or (self._current_mapping_editing_state() if editable else self._pending_mapping_editing_state)
        if candidate is None and editable and editing is None:
            lines.append(translate_ui_text("当前控件的输入尚未完成，请先修正；它还没有加入草稿。"))
        elif (editing is not None and editing.device_identity == (draft.serial, draft.hardware_id)
              and (candidate is not None or (editing.control_id == self._selected_control_id
                                            and self._mapping_editor_has_uncommitted_changes()))):
            # Preview the same candidate Save will collect, without changing the
            # real draft, emitting VM signals, or issuing a device command.
            projected = copy.copy(draft)
            projected.config = copy.deepcopy(draft.config)
            profile_id = self._editing_profile(draft)
            saved = ({"action": draft.profile(profile_id).get("codex_voice", {"type": "none"})}
                     if editing.mode == "codex" else draft.mapping(profile_id, editing.control_id))
            action = editing.action
            if saved is not None and _canonical_action(action) == _canonical_action(saved.get("action")):
                action = saved["action"]
            try:
                if editing.mode == "codex":
                    projected.set_codex_voice(profile_id, action)
                else:
                    projected.set_mapping(profile_id, editing.control_id, editing.short_name, action)
                after = projected.config
            except ValueError as exc:
                lines.append(translate_ui_text("当前控件不能加入草稿：{message}").format(message=str(exc)))
        profiles_before = {p["id"]: p for p in before["profiles"]}
        profiles_after = {p["id"]: p for p in after["profiles"]}
        if before["active_profile"] != after["active_profile"]:
            lines.append(translate_ui_text("启用配置方案：{name}").format(
                name=profiles_after[after["active_profile"]]["name"]))
        if before.get("voice_action") != after.get("voice_action"):
            action = after.get("voice_action")
            lines.append(translate_ui_text("设备级语音键：{action}").format(
                action=describe_action(action, platform=draft.platform, control_id="key.8")
                if action is not None else "未设置"))
        for profile_id, profile in profiles_after.items():
            previous = profiles_before.get(profile_id)
            if previous is None:
                lines.append(translate_ui_text("新增配置方案：{name}").format(name=profile["name"]))
                continue
            if previous["name"] != profile["name"]:
                lines.append(translate_ui_text("配置方案改名：{before} → {after}").format(
                    before=previous["name"], after=profile["name"]))
            mappings_before = {m["control_id"]: m for m in previous["mappings"]}
            for mapping in profile["mappings"]:
                control_id = mapping["control_id"]
                if mapping != mappings_before.get(control_id):
                    lines.append(translate_ui_text("{profile} · 普通 · {control}：{action}").format(
                        profile=profile["name"], control=translate_ui_text(control_display_name(control_id)),
                        action=describe_action(mapping["action"], platform=draft.platform, control_id=control_id)))
                    if mapping.get("short_name", "") != (mappings_before.get(control_id) or {}).get("short_name", ""):
                        lines.append(translate_ui_text("按键名称：{name}").format(name=mapping.get("short_name", "")))
            if profile.get("codex_voice", {"type": "none"}) != previous.get("codex_voice", {"type": "none"}):
                action = profile.get("codex_voice", {"type": "none"})
                lines.append(translate_ui_text("{profile} · Codex · 8 号语音键：{action}").format(
                    profile=profile["name"], action=translate_ui_text("Codex 自带语音") if action.get("type") == "none"
                    else describe_action(action, platform=draft.platform, control_id="key.8")))
        for profile_id, profile in profiles_before.items():
            if profile_id not in profiles_after:
                lines.append(translate_ui_text("删除配置方案：{name}").format(name=profile["name"]))
        order_before = [p["id"] for p in before["profiles"]]
        order_after = [p["id"] for p in after["profiles"]]
        if order_before != order_after and sorted(order_before) == sorted(order_after):
            lines.append(translate_ui_text("调整方案顺序：{order}").format(
                order=" → ".join(str(p["name"]) for p in after["profiles"])))
        for key, title in (("lighting", "灯光设置"), ("haptic", "振动设置"),
                           ("display", "显示设置"), ("macros", "按键序列"), ("joystick", "摇杆校准")):
            if before.get(key) != after.get(key):
                lines.append(translate_ui_text(title))
        return tuple(lines)

    def _confirm_discard_draft(self) -> None:
        draft = self._view_model.draft
        if draft is None or not draft.is_dirty:
            return
        choice = notice.ask(
            self,
            "放弃全部本地更改",
            "这会恢复到设备最近一次读回确认的配置，尚未写入设备的修改都会丢失。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            danger=True,
            labels={QMessageBox.Yes: "放弃全部更改"},
        )
        if choice != QMessageBox.Yes:
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        self._view_model.discard_draft()

    def _export_configuration(self, kind: str, *, draft=None) -> bool:
        suffix = "draft" if kind == "draft" else "confirmed"
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出 BORING 配置",
            f"BORING-MIST-{draft.serial + '-' if draft else ''}{suffix}.boring-config.json",
            "BORING 配置 (*.boring-config.json);;JSON (*.json)",
        )
        if not file_name:
            return False
        try:
            if draft is None:
                self._view_model.export_configuration(Path(file_name), kind=kind)
            else:
                self._view_model.export_configuration(Path(file_name), kind=kind, draft=draft)
        except (OSError, ValueError) as exc:
            notice.alert(self, "导出配置失败", str(exc))
            return False
        self._banners.notify(
            "本地草稿已导出。" if kind == "draft" else "设备已确认配置已导出。",
            key="configuration-export",
        )
        return True

    def _import_configuration(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入 BORING 配置",
            "",
            "BORING 配置 (*.boring-config.json *.json);;JSON (*.json)",
        )
        if not file_name:
            return
        try:
            package = self._view_model.read_configuration_file(Path(file_name))
        except (OSError, ValueError) as exc:
            notice.notify(self, "无法导入配置", str(exc))
            return
        draft = self._view_model.draft
        if draft is None:
            return
        changes = draft.preview_changes(package.config)
        paths = "\n".join(f"• {change.path}" for change in changes[:12])
        if len(changes) > 12:
            paths += f"\n• 还有 {len(changes) - 12} 项差异"
        text = (
            f"文件类型：{package.kind}\n"
            f"创建工具：{package.created_by}\n"
            f"检测到 {len(changes)} 项差异。\n\n"
            f"{paths or '配置内容与当前草稿一致。'}\n\n"
            "导入只替换本地草稿，不会写入设备。"
        )
        options = ([("cancel", "取消"), ("replace", "直接替换当前草稿"), ("export", "导出当前草稿后导入")]
                   if draft.is_dirty else [("cancel", "取消"), ("replace", "导入到本地草稿")])
        picked = notice.choose(self, "确认导入到本地草稿", text, options, default="cancel",
                               escape="cancel", object_name="configurationImportConfirm")
        if picked == "cancel":
            return
        if picked == "export" and not self._export_configuration("draft"):
            return
        if not self._confirm_leave_mapping_editor():
            return
        self._suppress_mapping_edit_restore = True
        try:
            self._view_model.apply_configuration_import(package)
        except ValueError as exc:
            notice.notify(self, "导入配置与当前设备能力不兼容", str(exc))
            return
        else:
            self._pending_mapping_editing_state = None
        finally:
            self._suppress_mapping_edit_restore = False
        notice.notify(self, "已导入本地草稿", "请检查差异并重新验证；配置尚未写入设备。", state="ok")

    def _select_control(self, control_id: object) -> None:
        if isinstance(control_id, str) and control_id != self._selected_control_id:
            if (self._mapping_editor_has_uncommitted_changes()
                    and not self._save_mapping_editor_draft()):
                card = self._current_mapping_card()
                selector = (
                    card.findChild(QComboBox, "triggerSelector")
                    if card is not None
                    else None
                )
                if selector is not None:
                    blocked = selector.blockSignals(True)
                    selector.setCurrentIndex(
                        selector.findData(self._selected_control_id)
                    )
                    selector.blockSignals(blocked)
                return
            self._selected_control_id = control_id
            self._primary_voice_intent = None
            self._pending_mapping_editing_state = None
            if not self._refresh_mapping_workspace():
                self.render(self._view_model.model)

    def _select_physical_control(self, control_id: str) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        related = _related_controls(control_id, draft.controls)
        selected_control_id = (
            control_id if control_id in related else related[0] if related else None
        )
        if (
            selected_control_id is not None
            and selected_control_id != self._selected_control_id
        ):
            if (self._mapping_editor_has_uncommitted_changes()
                    and not self._save_mapping_editor_draft()):
                return
            self._selected_control_id = selected_control_id
            self._primary_voice_intent = None
            self._pending_mapping_editing_state = None
            if not self._refresh_mapping_workspace():
                self.render(self._view_model.model)

    def _close_control_editor(self) -> None:
        if (self._mapping_editor_has_uncommitted_changes()
                and not self._save_mapping_editor_draft()):
            return
        self._selected_control_id = None
        self._primary_voice_intent = None
        self._shortcut_task_requested = False
        self._pending_mapping_editing_state = None
        if not self._refresh_mapping_workspace():
            self.render(self._view_model.model)

    @staticmethod
    def _configuration_mode_supported(snapshot: DeviceSnapshot, mode: str) -> bool:
        if mode == "normal":
            return True
        if mode == "claude_code":
            # design decision: Claude desktop's Code sessions use the normal-mode
            # lights; Console no longer configures the Claude Code mode.
            return False
        if snapshot.identity.get("hardware_id") not in MATRIX12_HARDWARE_IDS:
            return False
        return mode == "codex" or snapshot.capabilities.get("features", {}).get("claude_code_mode") is True

    def _select_configuration_mode(self, mode: str) -> None:
        snapshot = self._view_model.model.snapshot
        if (snapshot is None or mode == self._configuration_mode
                or not self._configuration_mode_supported(snapshot, mode)
                or self._view_model.write_transaction.blocks_editing
                or self._view_model.firmware_update.blocks_editing
                or self._view_model.calibration.blocks_editing):
            return
        if self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        self._mode_control_selection[self._configuration_mode or "normal"] = self._selected_control_id
        self._configuration_mode = mode
        self._selected_control_id = self._mode_control_selection.get(mode)
        self._primary_voice_intent = None
        self._pending_mapping_editing_state = None
        self._shortcut_task_requested = False
        self._suppress_mapping_edit_restore = True
        try:
            self.render(self._view_model.model)
        finally:
            self._suppress_mapping_edit_restore = False

    def _create_profile(self) -> None:
        if self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            profile_id = self._view_model.create_profile()
            self._leave_codex_screen()
            self._editing_profile_id = profile_id
            self.render(self._view_model.model)
        except ValueError as exc:
            notice.notify(self, "无法新建配置方案", str(exc))
            return

    def _create_agent_profile(self, template) -> None:
        draft = self._view_model.draft
        if draft is None or (self._mapping_editor_has_uncommitted_changes()
                              and not self._save_mapping_editor_draft()):
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            profile_id = self._view_model.create_profile_from_template(
                template.profile_name,
                agent_profile_mappings(template.key, platform=sys.platform, hardware_id=draft.hardware_id),
            )
            self._leave_codex_screen()
            self._editing_profile_id = profile_id
            self.render(self._view_model.model)
        except ValueError as exc:
            notice.notify(self, "无法添加 Agent 方案", str(exc))
            return

    def _save_profile_as(self) -> None:
        if self._mapping_editor_has_uncommitted_changes() and not self._save_mapping_editor_draft():
            return
        draft = self._view_model.draft
        if draft is None:
            return
        source_profile_id = self._editing_profile(draft)
        try:
            suggested_name = draft.suggested_profile_copy_name(source_profile_id)
        except ValueError as exc:
            notice.notify(self, "无法另存为配置方案", str(exc))
            return
        name, accepted = QInputDialog.getText(
            self,
            "另存为新方案",
            "新配置方案名称",
            text=suggested_name,
        )
        if not accepted:
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            profile_id = self._view_model.save_profile_as(source_profile_id, name)
            self._leave_codex_screen()
            self._editing_profile_id = profile_id
            self.render(self._view_model.model)
        except ValueError as exc:
            notice.notify(self, "无法另存为配置方案", str(exc))
            return

    def _delete_profile(self) -> None:
        if not self._confirm_leave_mapping_editor():
            return
        draft = self._view_model.draft
        if draft is None:
            return
        profile_id = self._editing_profile(draft)
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return
        profile_name = str(draft.profile(profile_id).get("name", ""))
        choice = notice.ask(
            self,
            "删除配置方案",
            f"确定从本地草稿删除“{profile_name}”吗？删除后仍需保存到设备才会生效。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            danger=True,
            labels={QMessageBox.Yes: "删除"},
        )
        if choice != QMessageBox.Yes:
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            self._view_model.delete_profile(profile_id)
        except ValueError as exc:
            notice.notify(self, "无法删除配置方案", str(exc))
            return

    def _configuration_blocks_close(self) -> bool:
        transaction = self._view_model.write_transaction
        snapshot = self._view_model.model.snapshot
        original_device_connected = (
            self._view_model.model.state in {AppState.READY, AppState.READ_ONLY}
            and snapshot is not None
            and snapshot.identity.get("serial") == transaction.device_serial
        )
        return transaction.is_busy or (
            transaction.state is ConfigTransactionState.UNKNOWN and original_device_connected
        )

    def _screen_icon_draft_for(self, snapshot: DeviceSnapshot) -> ScreenIconDraft:
        key = (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"]))
        return self._screen_icon_drafts.setdefault(key, ScreenIconDraft())

    def _confirm_discard_screen_icons(self) -> bool:
        if not any(draft.is_dirty for draft in (*self._screen_icon_drafts.values(), *self._screen_glyph_drafts.values())):
            return True
        return notice.ask(
            self,
            translate_ui_text("本地图标尚未写入"),
            translate_ui_text("退出将丢弃本次运行中各设备的本地图标编辑；原图片文件不受影响。是否丢弃并退出？"),
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Discard: "丢弃并退出"},
        ) == QMessageBox.Discard

    def _confirm_discard_ble_names(self) -> bool:
        drafts = self._view_model.ble_name.pending_drafts()
        if not drafts:
            return True
        text = translate_ui_text(
            "退出将丢弃以下设备的本地名称输入或待确认记录；不会撤销设备已执行的保存。重新打开后请连接原设备读取名称，确认实际结果。是否退出？"
        ) + "\n\n" + "\n".join(f"{serial}  {draft}" for serial, draft in drafts)
        return notice.ask(
            self, "蓝牙名称仍有本地记录", text,
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel, danger=True,
            labels={QMessageBox.Discard: "丢弃并退出"},
        ) == QMessageBox.Discard

    def _confirm_discard_normal_agent_choices(self) -> bool:
        if not self._view_model.normal_agent.pending_drafts():
            return True
        return notice.ask(
            self, translate_ui_text("状态灯按键设置尚未确认"),
            translate_ui_text("退出将丢弃未应用的选择；不会撤销设备已保存的设置。若应用结果待确认，请连接原设备重新读取。是否退出？"),
            QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel,
            labels={QMessageBox.Discard: "丢弃并退出"},
        ) == QMessageBox.Discard

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        model = self._connection_transition_model
        self._cancel_connection_transition()
        if model is not None:
            self._connection_progress.hide()
            self.render(model)
        if self._view_model.normal_agent.busy:
            notice.alert(self, translate_ui_text("状态灯按键操作尚未完成"), translate_ui_text(
                "请等待状态灯按键设置保存或读回完成。"
            ))
            event.ignore()
            return
        if self._view_model.ble_name.busy:
            notice.alert(self, translate_ui_text("蓝牙名称操作尚未完成"), translate_ui_text(
                "请等待蓝牙名称保存或读回完成；关闭应用不会取消设备端操作。"
            ))
            event.ignore()
            return
        if self._view_model.screen_icon.busy or self._view_model.screen_glyphs.busy:
            notice.alert(self, translate_ui_text("圆屏图标尚未完成"), translate_ui_text(
                "请等待圆屏图标传输或读回完成；提交之前可以点击取消上传。"
            ))
            event.ignore()
            return
        calibration = self._view_model.calibration
        if (calibration.blocks_editing
                and self._view_model.model.state is not AppState.FIRMWARE_MAINTENANCE):
            notice.alert(
                self,
                "摇杆校准尚未结束",
                "BORING 控制台正在采样、取消、等待激活或读回对账。请先回到摇杆校准页完成或取消事务。",
            )
            event.ignore()
            return
        firmware = self._view_model.firmware_update
        if self._view_model.can_stop_firmware_wait:
            if not self._firmware_page.confirm_stop_wait():
                event.ignore()
                return
        elif firmware.blocks_editing:
            notice.alert(
                self,
                "固件维护尚未结束",
                "BORING 控制台正在传输、对账、等待重启，或等待你处理设备中的旧更新。请先回到固件维护页完成或中止事务。",
            )
            event.ignore()
            return
        transaction = self._view_model.write_transaction
        if self._configuration_blocks_close():
            notice.alert(
                self,
                "设备写入尚未完成",
                "BORING 控制台正在写入、等待激活或处理未知结果。请先让事务进入已激活、失败或冲突状态后再关闭；关闭应用不会取消设备端事务。",
            )
            event.ignore()
            return
        if self._desktop_update_ui is not None and self._desktop_update_ui.restart_prepared:
            self._view_model.shutdown()
            event.accept()
            return
        if (
            self._background_controller is not None
            and not self._background_controller.quitting
            and self._background_controller.hide_window()
        ):
            event.ignore()
            return
        if (
            self._desktop_update_ui is not None
            and not self._desktop_update_ui.handle_close_request()
        ):
            event.ignore()
            return
        if not self._confirm_leave_page(for_exit=True):
            event.ignore()
            return
        if not self._confirm_leave_mapping_editor():
            event.ignore()
            return
        if not self._confirm_discard_screen_icons():
            event.ignore()
            return
        if not self._confirm_discard_normal_agent_choices() or self._view_model.normal_agent.busy:
            event.ignore()
            return
        if not self._confirm_discard_ble_names() or self._view_model.ble_name.busy:
            event.ignore()
            return
        draft = self._view_model.draft
        unresolved_exit = transaction.state is ConfigTransactionState.UNKNOWN
        if unresolved_exit:
            if notice.choose(
                self, "原设备未连接，写入结果未知",
                "无法确认设备是否已保存。可以导出未决草稿和设备写入信息后退出。\n"
                "退出不会取消设备端写入，也不代表保存成功。重新打开后，请先连接原设备读取配置；不会自动重发写入。",
                [("cancel", "取消"), ("export", "导出未决草稿后退出")],
                default="cancel", escape="cancel", object_name="unresolvedWriteExit",
            ) != "export":
                event.ignore()
                return
            # Reconnection can start readback while the modal dialog is open.
            if self._configuration_blocks_close():
                event.ignore()
                return
            if not self._export_configuration("draft"):
                event.ignore()
                return
        for pending_draft in self._view_model.pending_dirty_workspaces():
            if (unresolved_exit and pending_draft.serial == transaction.device_serial
                    and pending_draft.config == transaction.candidate_config):
                # This exact draft was included in the unresolved-write export.
                continue
            picked = notice.choose(
                self, "本地草稿尚未导出",
                f"设备 {pending_draft.serial} 的草稿只保留在本进程中。可以先导出草稿，或丢弃后关闭。",
                [("cancel", "取消"), ("discard", "丢弃并关闭"), ("export", "导出草稿后关闭")],
                default="cancel", escape="cancel", object_name="pendingDraftExit",
            )
            if picked == "cancel":
                event.ignore()
                return
            if picked == "export":
                exported = (self._export_configuration("draft") if pending_draft is draft
                            else self._export_configuration("draft", draft=pending_draft))
                if not exported:
                    event.ignore()
                    return
            if picked == "discard":
                if pending_draft is draft:
                    self._view_model.discard_draft()
                else:
                    pending_draft.discard()
        if unresolved_exit and self._configuration_blocks_close():
            # The save-file and export-result dialogs also run the Qt event loop.
            event.ignore()
            return
        if self._view_model.ble_name.busy or self._view_model.normal_agent.busy:
            event.ignore()
            return
        self._view_model.stop_lighting_preview(clear_candidate=True)
        self._view_model.shutdown()
        self._screen_icon_drafts.clear()
        self._screen_glyph_drafts.clear()
        event.accept()


def _semantic_role_card(
    marker: str,
    body: str,
    object_name: str,
    *,
    compact: bool = False,
) -> QFrame:
    card = QFrame(objectName=object_name)
    card.setAccessibleName(f"{marker} {body}")
    box = QVBoxLayout(card)
    box.setContentsMargins(10, 5, 10, 5)
    box.setSpacing(2)
    marker_color = {
        "roleAgent": "#c8dbf3",
        "roleContext": "#f7f7f2",
    }.get(object_name, "#efe4dd")
    marker_label = Boring5RLabel(
        marker,
        scale=0.75,
        color=marker_color,
        objectName="roleMarker",
    )
    body_text = body
    if compact and "\n" in body:
        descriptor, body_text = body.split("\n", 1)
        header = QVBoxLayout()
        header.setSpacing(4)
        header.addWidget(marker_label)
        descriptor_label = QLabel(descriptor, objectName="roleBody")
        descriptor_label.setWordWrap(True)
        header.addWidget(descriptor_label)
        box.addLayout(header)
    else:
        box.addWidget(marker_label)
    body_label = QLabel(body_text, objectName="roleBody")
    body_label.setWordWrap(True)
    box.addWidget(body_label)
    return card


def _top_metric(
    label: str,
    value: str,
    *,
    digital: bool = False,
    translate_value: bool = True,
) -> QFrame:
    metric = QFrame(objectName="topMetric")
    box = QVBoxLayout(metric)
    box.setContentsMargins(15, 0, 15, 0)
    box.setSpacing(2)
    box.addWidget(QLabel(label, objectName="eyebrow"))
    value_widget = (
        Boring5RLabel(
            value,
            scale=1.0,
            color="#f4e9e2",
            objectName="headerValue",
        )
        if digital
        else QLabel(value, objectName="headerValue")
    )
    if not translate_value:
        value_widget.setProperty("boringI18nSkip_text", True)
    box.addWidget(value_widget)
    return metric


def _tinted_navigation_fallback(icon: QIcon, white: float) -> QIcon:
    pixmap = icon.pixmap(QSize(20, 20))
    if pixmap.isNull():
        return icon
    tinted = QPixmap(pixmap.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(
        tinted.rect(),
        QColor.fromRgbF(white, white, white, 1.0),
    )
    painter.end()
    return QIcon(tinted)


def _navigation_icon(symbol_name: str, fallback: QIcon, *, white: float) -> QIcon:
    """Candidate line icons derived from v4 descriptions, pending original SVGs."""

    from PySide6.QtSvg import QSvgRenderer
    shapes = {
        "folder": '<path d="M3 7 V5 A2 2 0 0 1 5 3 H9 L12 6 H19 A2 2 0 0 1 21 8 V18 A2 2 0 0 1 19 20 H5 A2 2 0 0 1 3 18 Z"/><path d="M3 9 H21"/>',
        "curlybraces": '<path d="M9 3 H7 Q5 3 5 6 V9 Q5 12 3 12 Q5 12 5 15 V18 Q5 21 7 21 H9 M15 3 H17 Q19 3 19 6 V9 Q19 12 21 12 Q19 12 19 15 V18 Q19 21 17 21 H15"/>',
        "keyboard": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><rect x="5.5" y="6.5" width="13" height="10" rx="1.8"/><path d="M7 14 Q12 16 17 14"/>',
        "bubble.left": '<rect x="2" y="13" width="7" height="7" rx="1"/><path d="M12 3 L17 11 H7 Z"/><circle cx="18" cy="17" r="4"/>',
        "dial.low": '<circle cx="12" cy="12" r="9"/><path d="M12 4 V8 M5 16 L9 14 M19 16 L15 14"/>',
        "sun.max": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="6"/>' + ''.join(f'<circle cx="{x}" cy="{y}" r=".6"/>' for x in (9,12,15) for y in (9,12,15)),
        "gearshape": '<path d="M10 2 H14 L15 5 L18 6 L21 6 L22 10 L20 12 L21 15 L19 19 L16 19 L14 22 H10 L9 19 L6 18 L3 18 L2 14 L4 12 L3 9 L5 5 L8 5 Z"/><circle cx="12" cy="12" r="4"/>',
        "book.closed": '<path d="M4 4.5 C7 3.5 9.5 4 12 6 V20 C9.5 18 7 17.5 4 18.5 Z"/><path d="M20 4.5 C17 3.5 14.5 4 12 6 V20 C14.5 18 17 17.5 20 18.5 Z"/><path d="M12 6 V20"/>',
    }
    if symbol_name in NAV_SOLID_ICONS or symbol_name in shapes:
        colour = tokens.TEXT_PRIMARY if white > .8 else tokens.TEXT_SECONDARY
        if symbol_name in NAV_SOLID_ICONS:
            source = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="{colour}" fill-rule="evenodd" d="{NAV_SOLID_ICONS[symbol_name]}"/></svg>'
        else:
            source = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{colour}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{shapes[symbol_name]}</g></svg>'
        pixmap = QPixmap(48, 48)
        pixmap.fill(Qt.transparent)
        renderer = QSvgRenderer(source.encode())
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return QIcon(pixmap)

    application = QApplication.instance()
    if (
        sys.platform != "darwin"
        or application is None
        or application.platformName() != "cocoa"
    ):
        return _tinted_navigation_fallback(fallback, white)
    try:
        from AppKit import (
            NSBitmapImageFileTypePNG,
            NSBitmapImageRep,
            NSColor,
            NSImage,
            NSImageSymbolConfiguration,
        )

        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol_name,
            None,
        )
        if image is None:
            return fallback
        configuration = (
            NSImageSymbolConfiguration.configurationWithHierarchicalColor_(
                NSColor.colorWithWhite_alpha_(white, 1.0)
            )
        )
        configured = image.imageWithSymbolConfiguration_(configuration)
        if configured is not None:
            image = configured
        data = image.TIFFRepresentation()
        representation = (
            NSBitmapImageRep.imageRepWithData_(data) if data is not None else None
        )
        png_data = (
            representation.representationUsingType_properties_(
                NSBitmapImageFileTypePNG,
                {},
            )
            if representation is not None
            else None
        )
        pixmap = QPixmap()
        if png_data is not None and pixmap.loadFromData(bytes(png_data), "PNG"):
            return QIcon(pixmap)
    except (AttributeError, ImportError, TypeError, ValueError):
        pass
    return _tinted_navigation_fallback(fallback, white)


def _mode_metric(mode: str) -> QFrame:
    metric = QFrame(objectName="topMetric")
    box = QVBoxLayout(metric)
    box.setContentsMargins(15, 0, 15, 0)
    box.setSpacing(2)
    box.addWidget(QLabel("当前模式", objectName="eyebrow"))
    badge = QFrame(objectName="modeCodex" if mode == "codex" else "modeNormal")
    badge_layout = QHBoxLayout(badge)
    badge_layout.setContentsMargins(9, 6, 9, 6)
    label = Boring5RLabel(
        _mode_label(mode),
        scale=1.0,
        color="#c9dcf4" if mode == "codex" else "#f0e7e1",
        objectName="modeDigitalValue",
    )
    badge_layout.addWidget(label)
    box.addWidget(badge, 0, Qt.AlignLeft)
    return metric


def _canonical_action(action: object) -> bytes:
    if not isinstance(action, dict):
        return b""
    normalized = {
        key: value
        for key, value in action.items()
        if key == "type" or value not in (None, [], "")
    }
    return canonical_json_bytes(normalized)


def _control_role_style(control_id: str) -> str:
    return "roleAgent" if control_id in MATRIX12_AGENT_STATUS_KEYS else "roleHuman"


def _control_role_copy(control_id: str) -> tuple[str, str]:
    if control_id in MATRIX12_AGENT_STATUS_KEYS:
        return (
            "AGENT STATUS",
            "状态灯键\n用于 Agent 状态与选择，由官方功能接管，不提供自定义。",
        )
    if control_id.startswith("key."):
        return (
            "HUMAN INPUT",
            "功能键\n用于触发用户动作，可编辑真实按键、组合键或设备功能。",
        )
    return (
        "HUMAN INPUT",
        "实体控制\n用于导航、选择、确认或调整；不同触发方式分别保存动作。",
    )


def _short_value(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 120 else f"{text[:117]}…"


def _next_action(snapshot: DeviceSnapshot, state: AppState) -> str:
    if state not in {AppState.READY, AppState.READ_ONLY}:
        return "离线草稿可继续编辑；重新连接并读取设备后，由你确认应用。"
    if snapshot.status.get("activation_failed") is True:
        return "下一步：重启设备后再读取；若仍失败，使用设备的物理恢复流程。"
    if snapshot.status.get("pending") is not None:
        return "下一步：等待设备完成激活；当前不要重复写入配置。"
    if not snapshot.config_is_synced:
        return "下一步：重新扫描并完整读取设备；确认对账后再编辑配置。"
    if snapshot.is_read_only:
        return "当前设备仅允许读取。可检查配置与技术详情。"
    return "下一步：点击下方任一按键、旋钮或摇杆，直接调整动作。"


def _shortcut_display_platform(snapshot: DeviceSnapshot) -> str:
    # The protocol has one device platform, not a platform field per profile.
    # Capture uses host Qt semantics separately; display never changes HID usages.
    return str(snapshot.status.get("platform", ""))


# Mode tabs Console offers (design decision: no Claude Code tab).
CONFIGURABLE_MODES = ("normal", "codex")
MODE_OFFLINE_KEEP_MS = 800       # an offline key edit goes into the draft this long after the last change
MODE_SAVED_NOTE_SECONDS = 4.0
MODE_QUOTA_DEFAULT_KEY = "mode/quota_default"   # "5h" or "week" (Mode页 定稿 v1, 08)   # "已保存，去目标软件里试试" stays this long (Mode页 定稿 v1, 03)
# The card's title and the dots beside it for each part of the knob and the joystick (11).
MODE_PART_TITLES = {
    "encoder.ccw": ("旋钮 · 逆时针", "CCW"), "encoder.cw": ("旋钮 · 顺时针", "CW"),
    "encoder.press": ("旋钮 · 按下", "OK"),
    "joystick.up": ("摇杆 · 向上", "UP"), "joystick.down": ("摇杆 · 向下", "DN"),
    "joystick.left": ("摇杆 · 向左", "LT"), "joystick.right": ("摇杆 · 向右", "RT"),
    "joystick.press": ("摇杆 · 按下", "OK"),
}
CLAUDE_CODE_MODE_HIDDEN_NOTE = ("设备正处于 Claude Code 模式，Console 不再提供这个模式的设置。"
                                "Claude 的状态灯在普通模式下显示。")


def _configuration_mode_label(mode: str) -> str:
    return translate_ui_text({"normal": "普通", "codex": "Codex", "claude_code": "Claude Code"}.get(mode, _mode_label(mode)))


def _dedicated_control_name(mode: str, control_id: str) -> str:
    if mode == "claude_code":
        names = {
            "key.1": "提交 · Enter", "key.2": "中断 · Escape", "key.3": "取消 · Ctrl+C",
            "key.4": "切换权限 · Shift+Tab", "key.5": "查看记录 · Ctrl+O", "key.6": "搜索历史 · Ctrl+R",
            "key.7": "重绘 · Ctrl+L", "key.8": "语音 · Space", "key.9": "任务 · Ctrl+T",
            "key.10": "外部编辑器 · Ctrl+G", "key.11": "切换模型 · Alt+P", "key.12": "粘贴 · Command/Ctrl+V",
            "joystick.up": "历史上一条 · ↑", "joystick.down": "历史下一条 · ↓",
            "joystick.left": "光标向左 · ←", "joystick.right": "光标向右 · →", "joystick.press": "补全 · Tab",
            "encoder.ccw": "向上滚动", "encoder.cw": "向下滚动", "encoder.press": "提交 · Enter",
        }
    else:
        names = {**MATRIX12_CODEX_KEY_NAMES,
                 "encoder.ccw": "Codex 旋钮逆时针", "encoder.cw": "Codex 旋钮顺时针",
                 "encoder.press": "Codex 旋钮按下", "joystick.press": "沿用普通模式映射"}
        names.update({f"joystick.{direction}": "Codex 摇杆导航" for direction in ("up", "down", "left", "right")})
    return translate_ui_text(names.get(control_id, "设备专用动作"))


def _mode_label(mode: str) -> str:
    return {
        "normal": "NORMAL",
        "codex": "CODEX",
        "eda": "EDA",
        "claude_code": "CC",
    }.get(mode, mode.upper())


def _canvas_title(mode: str) -> str:
    if mode == "codex":
        return "外部 Agent 工作台"
    if mode == "normal":
        return "实体动作工作台"
    return "实体控件工作台"


def _canvas_context(mode: str) -> str:
    if mode == "codex":
        return "状态灯键显示 Agent 状态；功能键与金属控制件用于操作。"
    if mode == "normal":
        return "功能键用于触发动作；状态灯键用于显示状态。"
    return "界面只呈现设备已经报告的模式与控件能力。"


def _mode_explanation(mode: str) -> str:
    if mode == "codex":
        return "当前为 Codex 模式。设备负责转发外部 Agent 状态，不表示设备内部运行 AI；当前协议尚未报告 Agent 来源与实时状态，因此界面不虚构这些内容。"
    if mode == "normal":
        return "当前为 Normal 模式。功能键用于普通按键和组合动作；状态灯键不作为普通 RGB 灯位。"
    return "当前模式没有对应的专用语义说明，控制台保留设备报告的原始模式。"


def _technical_details_text(snapshot: DeviceSnapshot, connect_timing: tuple = ()) -> str:
    protocol = (
        f"{snapshot.versions.get('protocol_major', '—')}."
        f"{snapshot.versions.get('protocol_minor', '—')}"
    )
    usb_id = (
        f"{int(snapshot.identity.get('usb_vid', 0)):04X}:"
        f"{int(snapshot.identity.get('usb_pid', 0)):04X}"
    )
    return "\n".join(
        (
            f"Hardware ID    {snapshot.identity.get('hardware_id', '—')}",
            f"序列号         {snapshot.identity.get('serial', '—')}",
            f"固件           {snapshot.versions.get('firmware', '—')}",
            f"协议 / Schema  {protocol} / {snapshot.versions.get('schema_version', '—')}",
            f"USB ID         {usb_id}",
            f"设备真实性     {snapshot.trust.message}",
            f"认证签发方     {snapshot.trust.issuer_key_id or '—'}",
            f"端口           {snapshot.port_name}",
            f"Generation     {snapshot.config_result.get('generation', '—')}",
            f"配置摘要       {snapshot.config_result.get('digest', '—')}",
            # SH-005: where the last connection spent its time, per step.
            *((f"连接耗时       {timing_summary(connect_timing)}",) if connect_timing else ()),
        )
    )



def _quota_reset(epoch: int | None) -> str:
    """Short reset time under a quota ring: "00:20" today, "11-21" on a later day (07)."""
    if epoch is None:
        return ""
    from datetime import datetime
    moment = datetime.fromtimestamp(epoch)
    return moment.strftime("%H:%M") if moment.date() == datetime.now().date() else moment.strftime("%m-%d")


def _mode_purpose_segments(action_editor: ActionEditor, *, codex_voice: bool) -> None:
    """用途三格 (Mode页 定稿 v1): the purpose list shown as three segments.

    The list itself stays (hidden) and keeps all the logic; a segment only sets it.
    """
    combo = action_editor._purpose
    if combo is None:
        return
    # Short names so three fit the card in every language; the full name stays in the tooltip.
    short = ({"shortcut": "Codex 语音", "voice": "其他语音软件"} if codex_voice
             else {"shortcut": "快捷键", "voice": "语音输入", "native": "AI 语音"})
    frame = QFrame(objectName="modeCardPurposes")
    row = QHBoxLayout(frame)
    row.setContentsMargins(0, 0, 0, 0)   # the sheet pads 3 (scaled)
    row.setSpacing(2)
    buttons = []
    for index in range(combo.count()):
        data = combo.itemData(index)
        button = PurposeSegment(translate_ui_text(short.get(data, combo.itemText(index))))
        button.setProperty("purpose", data)
        button.setCheckable(True)
        button.setToolTip(combo.itemText(index))
        button.setEnabled(combo.model().item(index).isEnabled())
        tip = combo.itemData(index, Qt.ToolTipRole)
        if tip:
            button.setToolTip(f"{combo.itemText(index)}\n{tip}")
        button.clicked.connect(lambda _checked=False, value=index: combo.setCurrentIndex(value))
        row.addWidget(button, 1)
        buttons.append(button)

    def follow(current: int) -> None:
        for index, button in enumerate(buttons):
            button.setChecked(index == current)

    combo.currentIndexChanged.connect(follow)
    follow(combo.currentIndex())
    layout = action_editor.layout()
    layout.insertWidget(layout.indexOf(combo), frame)
    combo.hide()


def _mode_voice_panel(action_editor: ActionEditor, open_device_voice) -> None:
    """语音键的卡 (Mode页 定稿 v1, 04): one sentence, 设置语音输入…, and two small links.

    The demo, the help and the status line move into the 设置语音输入 window later
    (its draft is not drawn yet); they stay in the editor, hidden.
    """
    panel = action_editor.findChild(QWidget, "voiceInputSetup")
    if panel is None:
        # No 语音输入 on this firmware; AI 语音 still drops 选别的动作… and 名称.
        _mode_plain_rows(action_editor, lambda: False, None)
        return
    panel.setAttribute(Qt.WA_StyledBackground, True)
    box = panel.layout()
    box.setContentsMargins(12, 12, 12, 12)
    summary = next((label for label in panel.findChildren(QLabel) if label.parent() is panel
                    and label.objectName() == ""), None)
    if summary is not None:
        summary.setObjectName("modeVoiceSummary")
        set_translatable_text(summary, "单击说话，双击发送")
    for name in ("voiceSetupStatus", "voiceDemoToggle", "voiceSetupToggle"):
        widget = panel.findChild(QWidget, name)
        if widget is not None:
            widget.hide()
    connect = panel.findChild(QPushButton, "voiceConnectApp")
    if connect is not None:
        set_translatable_text(connect, "设置语音输入…")   # it opens a dialog now (design ticket 答复第 4 件)
    instructions = panel.findChild(QWidget, "voiceSetupInstructions")
    details_toggle = panel.findChild(QPushButton, "voiceShortcutDetailsToggle")
    details = panel.findChild(QWidget, "voiceShortcutDetails")
    links = QWidget(objectName="modeVoiceLinks")
    row = QHBoxLayout(links)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(16)
    layout = action_editor.layout()
    after = layout.indexOf(panel) + 1
    if details_toggle is not None and details is not None and instructions is not None:
        instructions.layout().removeWidget(details_toggle)
        instructions.layout().removeWidget(details)
        details_toggle.setObjectName("voiceShortcutDetailsToggle")
        set_translatable_text(details_toggle, "高级快捷键 ›")
        details_toggle.setProperty("modeLink", True)
        row.addWidget(details_toggle)
    if open_device_voice is not None:
        device = QPushButton("设备级语音键 ›", objectName="modeDeviceVoiceLink")
        device.setProperty("modeLink", True)
        device.clicked.connect(open_device_voice)
        row.addWidget(device)
    row.addStretch(1)
    layout.insertWidget(after, links)
    if details is not None:
        layout.insertWidget(after + 1, details)

    _mode_plain_rows(action_editor, lambda: panel.isVisibleTo(action_editor), links)


def _mode_plain_rows(action_editor: ActionEditor, voice_shown, links: QWidget | None) -> None:
    """选别的动作… and 名称 only on a plain key: not with 语音输入 (04), and not with AI 语音
    either (单 design ticket 第 2 处); another action means 快捷键 first."""
    other = action_editor.findChild(QPushButton, "modeCardOtherActions")
    card = action_editor.parentWidget()
    while card is not None and card.objectName() != "mappingEditorCard":
        card = card.parentWidget()
    name = card.findChild(QLineEdit, "mappingShortNameEditor") if card is not None else None
    name_row = name.parentWidget() if name is not None else None
    name_allowed = name_row is not None and not name_row.isHidden()   # Codex voice already hides it

    def follow(*_args) -> None:
        voice = voice_shown()
        if links is not None:
            links.setVisible(voice)
        purpose = action_editor._purpose.currentData() if action_editor._purpose is not None else None
        plain = not voice and purpose != "native"
        if other is not None:
            other.setVisible(plain)
        if name_row is not None:
            name_row.setVisible(name_allowed and plain)

    action_editor.action_changed.connect(follow)
    if action_editor._purpose is not None:
        action_editor._purpose.currentIndexChanged.connect(follow)
    follow()


def _mode_other_actions(card: QFrame, editor_layout: QVBoxLayout, action_editor: ActionEditor,
                        open_dialog) -> None:
    """"选别的动作…" opens its own dialog (Mode页 两个弹窗 v0): common shortcuts and ten kinds.

    The card keeps no form for the other kinds: the box above the black button says
    what the key does now ("现在的动作"), whatever kind it is.
    """
    recorder = action_editor.findChild(QWidget, "shortcutRecorder")
    if recorder is None:
        return
    # The recorder as drawn: the keys large, the black button full width under them.
    heading = recorder.findChild(QLabel, "shortcutRecorderHeading")
    if heading is not None:
        heading.hide()
    capture = recorder.findChild(QPushButton, "shortcutRecordButton")
    preview = recorder.findChild(QLabel, "shortcutRecorderPreview")
    if capture is not None and recorder.layout() is not None:
        box = recorder.layout()
        index = next((index for index in range(box.count())
                      if box.itemAt(index).layout() is not None
                      and box.itemAt(index).layout().indexOf(capture) >= 0), None)
        if index is not None:
            row = box.itemAt(index).layout()
            row.removeWidget(capture)
            box.insertWidget(index + 1, capture)
            capture.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            caption = QLabel("现在的动作", objectName="modeCardActionCaption")
            caption.setProperty("muted", True)
            row.addWidget(caption, 0, Qt.AlignRight | Qt.AlignTop)
    recorder.describe_all = True      # media, mouse, sequences … are written out too (11)
    if preview is not None:
        recorder.set_action(action_editor.action())
    manual_panel = action_editor.findChild(QWidget, "manualActionEditor")
    for name in ("commonShortcutMenu", "shortcutManualToggle"):
        button = action_editor.findChild(QPushButton, name)
        if button is not None:
            button.hide()
    if manual_panel is not None:
        manual_panel.hide()
        action_editor.action_changed.connect(manual_panel.hide)   # its kinds live in the dialog now
    other = QPushButton("选别的动作…", objectName="modeCardOtherActions")
    other.clicked.connect(open_dialog)
    layout = action_editor.layout()
    layout.insertWidget(layout.indexOf(recorder) + 1, other)


def _editor_name(control_id: str, mapping: dict | None) -> str:
    """What the 名称 field shows: the key's own name, or "未映射" in the UI language when
    it has none (a stand-in, never the name itself)."""
    name = mapping_display_name(control_id, mapping)
    return translate_ui_text(name) if name == "未映射" else name


def _stored_name(text: str) -> str:
    """The name to save: the "未映射" stand-in (in any language) saves as no name."""
    return "" if text.strip() in {"未映射", translate_ui_text("未映射")} else text


def _related_controls(control_id: str, controls: tuple[str, ...]) -> tuple[str, ...]:
    if control_id == "encoder" or control_id.startswith("encoder."):
        return tuple(value for value in controls if value.startswith("encoder."))
    if control_id == "joystick" or control_id.startswith("joystick."):
        return tuple(value for value in controls if value.startswith("joystick."))
    return (control_id,) if control_id in controls else ()


def _profile_mappings(profile: dict) -> dict[str, dict]:
    mappings = profile.get("mappings")
    if not isinstance(mappings, list):
        return {}
    return {
        mapping["control_id"]: mapping
        for mapping in mappings
        if isinstance(mapping, dict) and isinstance(mapping.get("control_id"), str)
    }


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        child_layout = item.layout()
        widget = item.widget()
        if child_layout is not None:
            _delete_layout(child_layout)
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()


def _state_marker(state: AppState) -> str:
    return {
        AppState.SCANNING: "DEVICE DISCOVERY",
        AppState.NO_DEVICE: "NO DEVICE",
        AppState.MULTIPLE_DEVICES: "SELECT DEVICE",
        AppState.CONNECTING: "READING DEVICE",
        AppState.FIRMWARE_UPDATE_REQUIRED: "USB UPDATE REQUIRED",
        AppState.INCOMPATIBLE: "INCOMPATIBLE",
        AppState.AUTHENTICITY_FAILED: "AUTHENTICITY FAILED",
        AppState.READ_FAILED: "READ FAILED",
        AppState.DISCONNECTED: "DISCONNECTED",
        AppState.READY: "READY",
        AppState.READ_ONLY: "READ ONLY",
        AppState.FIRMWARE_MAINTENANCE: "FIRMWARE MAINTENANCE",
    }[state]


def _transaction_title(state: ConfigTransactionState) -> str:
    return {
        ConfigTransactionState.IDLE: "本地草稿尚未写入",
        ConfigTransactionState.VALIDATING: "正在验证候选配置",
        ConfigTransactionState.AWAITING_CONFIRMATION: "等待你的最终确认",
        ConfigTransactionState.WRITING: "正在写入候选槽",
        ConfigTransactionState.PENDING: "等待设备激活",
        ConfigTransactionState.VERIFYING: "正在读回确认",
        ConfigTransactionState.ACTIVE: "写入并读回确认完成",
        ConfigTransactionState.FAILED: "写入未完成",
        ConfigTransactionState.UNKNOWN: "设备结果未知",
        ConfigTransactionState.CONFLICT: "配置版本冲突",
    }[state]


def _state_help(state: AppState) -> str:
    return {
        AppState.SCANNING: "正在按共享 USB 身份筛选候选端口。",
        AppState.CONNECTING: "依次执行 HELLO、GET_STATUS、GET_CONFIG，并读取能力用于布局。",
        AppState.NO_DEVICE: "连接设备后重新扫描；如设备处于 ROM 下载模式，请先恢复应用固件。",
        AppState.MULTIPLE_DEVICES: "选择后会通过 HELLO 再次确认产品、硬件与序列号。",
        AppState.FIRMWARE_UPDATE_REQUIRED: "请连接 USB，认证后进入设置中的固件维护更新。",
        AppState.INCOMPATIBLE: "请查看技术原因并使用兼容固件或 BORING 控制台版本。",
        AppState.AUTHENTICITY_FAILED: "控制台未能验证设备证书、挑战签名或身份一致性。普通键盘输入不受影响；请重新扫描或查看技术详情。",
        AppState.READ_FAILED: "控制台未能完成配置通信。请重新连接，或通过 USB 重试；已保留你的编辑。",
        AppState.DISCONNECTED: "只读界面可保留最后一次快照；重新连接后需重新读取，不会自动写入。",
        AppState.READY: "设备和配置已同步。",
        AppState.READ_ONLY: "设备允许读取，但当前不允许写入。",
        AppState.FIRMWARE_MAINTENANCE: "设备已认证；配置未能读取，可通过 USB 安装官方签名固件。",
    }[state]
