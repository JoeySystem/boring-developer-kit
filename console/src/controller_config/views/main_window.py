from __future__ import annotations

import copy
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QSettings, QTimer, Qt
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
    QLinearGradient,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSlider,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from controller_config.actions import action_field_choices, describe_action
from controller_config import __version__
from controller_config.appearance import install_macos_vibrancy, V4_STYLE, V4_TOKENS
from controller_config.views.v4_widgets import V4Card, UsageRings, StatusMark
from controller_config.views.connection_terminal import ConnectionTerminal
from controller_config.codex_usage import CodexUsageSnapshot, CodexUsageStatus
from controller_config.background_helper import PromptBackgroundController
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.firmware_release import RemoteFirmwareState
from controller_config.joystick_calibration import (
    CalibrationState,
    CalibrationTransaction,
)
from controller_config.lighting_preview import LightingPreviewStatus
from controller_config.i18n import (
    ENGLISH,
    SIMPLIFIED_CHINESE,
    SKIP_TRANSLATION_PROPERTY,
    SUPPORTED_LANGUAGES,
    LanguageManager,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.models import (
    DEVICE_DISPLAY_NAME,
    AppState,
    DeviceSnapshot,
    ScreenModel,
)
from controller_config.prompt_library import (
    PROMPT_SLOT_COUNT,
    QUICK_PROMPT_DIRECTIONS,
    QUICK_PROMPT_IDS,
)
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transactions import ConfigTransactionState
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.action_editor import ActionEditor


# Neutral tint over the native material: 40% opacity, not whole-window opacity.
ROOT_MATERIAL_TINT = QColor(28, 27, 25, 102)
from controller_config.views.actions import ActionsPage
from controller_config.digital_font import layout_text
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.diagnostics import DiagnosticsPage
from controller_config.views.macro_editor import MacroEditor
from controller_config.views.onboarding import (
    ONBOARDING_COMPLETED_KEY,
    OnboardingDialog,
)
from controller_config.views.preferences_editor import PreferencesEditor
from controller_config.views.screen_icon_editor import ScreenIconDraft, ScreenIconEditor
from controller_config.views.screen_glyph_editor import GlyphDraft, ScreenGlyphEditor
from controller_config.views.prompt_library_editor import (
    PromptEditingState,
    PromptLibraryEditor,
)


_LANGUAGE_LABELS = {
    SIMPLIFIED_CHINESE: "简体中文",
    ENGLISH: "English",
}


APP_STYLE = """
QMainWindow { background: transparent; }
QWidget#root { background: transparent; color: #eee6df; }
QWidget { font-family: "Avenir Next", "PingFang SC", sans-serif; font-size: 13px; color: #eee6df; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#consoleFrame { background: rgba(24, 13, 15, 150); border: none; border-radius: 18px; }
QFrame#windowChrome { background: transparent; border: none; }
QLabel#windowChromeTitle { color: #b49b95; font-size: 10px; font-weight: 750; letter-spacing: 1px; }
QPushButton#windowControl { min-width: 12px; max-width: 12px; min-height: 12px; max-height: 12px; padding: 0; border-radius: 6px; }
QPushButton#windowControl[windowRole="close"] { background: #ff5f57; border: 1px solid #e0443e; }
QPushButton#windowControl[windowRole="minimize"] { background: #febc2e; border: 1px solid #d99b22; }
QPushButton#windowControl[windowRole="zoom"] { background: #28c840; border: 1px solid #20a934; }
QPushButton#windowControl:hover { border-color: rgba(255, 255, 255, 150); }
QFrame#topNavigation { background: transparent; border: none; }
QFrame#navigationCapsule { background: rgba(10, 10, 12, 158); border: 1px solid rgba(255, 255, 255, 36); border-radius: 22px; }
QPushButton#navIconButton { min-width: 40px; max-width: 40px; min-height: 36px; max-height: 36px; padding: 0; background: transparent; border: 1px solid transparent; border-radius: 16px; }
QPushButton#navIconButton:hover { background: rgba(255, 255, 255, 18); border-color: rgba(255, 255, 255, 28); }
QPushButton#navIconButton[active="true"] { background: rgba(255, 255, 255, 32); border-color: rgba(255, 255, 255, 42); }
QPushButton#navIconButton:focus { border: 2px solid rgba(255, 255, 255, 92); }
QPushButton#navIconButton:disabled { background: transparent; border-color: transparent; }
QLabel#shellBrand { color: #fff4eb; }
QLabel#shellEdition { color: #967f7a; }
QLabel#deviceNameSummary { color: #f2e6de; font-size: 11px; font-weight: 750; letter-spacing: 0.6px; }
QLabel#deviceAuthSummary { color: #9f8882; font-size: 10px; font-weight: 650; }
QLabel#deviceAuthSummary[trust="authenticated"] { color: #9ed6b6; }
QLabel#deviceAuthSummary[trust="development"] { color: #ffb29f; }
QPushButton { background: #2a1b1d; color: #e9ded7; border: 1px solid #5d332e; border-radius: 7px; padding: 8px 12px; }
QPushButton:hover { color: #ffffff; border-color: #e85b42; background: #352022; }
QPushButton:disabled { color: #665756; border-color: #392729; background: #211718; }
QPushButton#settingsLanguageButton { min-width: 92px; }
QPushButton#settingsLanguageButton[active="true"] { color: #fff5ec; background: #5c2b25; border-color: #a94231; font-weight: 800; }
QFrame#settingsList { background: transparent; border-top: 1px solid #4f302d; border-bottom: 1px solid #4f302d; }
QFrame#settingsRow { background: transparent; border: none; border-bottom: 1px solid #3d2a29; }
QFrame#topBar { background: transparent; border: none; border-bottom: 1px solid #4d2420; border-radius: 0; }
QFrame#topMetric { background: transparent; border: none; border-left: 1px solid #4b2a28; }
QLabel#eyebrow { color: #9c8580; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#bleShortcutHeading { color: #b6a29c; font-size: 10px; font-weight: 750; }
QLabel#bleShortcutChip { color: #e9ded7; background: rgba(255, 255, 255, 9); border: 1px solid #5d403c; border-radius: 5px; padding: 3px 6px; font-size: 9px; font-weight: 750; }
QLabel#bleShortcutNote { color: #9c8883; font-size: 9px; }
QLabel#pageTitle { color: #fff1e7; font-size: 29px; font-weight: 800; }
QLabel#workspaceHeroTitle { color: #f4e7de; font-family: "Georgia", "Times New Roman", serif; font-size: 42px; font-weight: 400; }
QLabel#workspaceHeroCaption { color: #a88d86; font-size: 10px; font-weight: 750; letter-spacing: 1.5px; }
QLabel#headerValue { color: #f4e9e2; font-size: 14px; font-weight: 800; }
QFrame#modeNormal { background: #312528; border: 1px solid #665154; border-radius: 6px; }
QFrame#modeCodex { background: #233143; border: 1px solid #526d8d; border-radius: 6px; }
QLabel#muted { color: #a5918b; }
QLabel#statusReady, QLabel#promptHelperOnlineState[listenerOnline="true"] { background: transparent; color: #9ed6b6; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QLabel#statusWarn, QLabel#promptHelperOnlineState[listenerOnline="false"] { background: transparent; color: #ffb29f; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QLabel#statusError { background: transparent; color: #ffaaa2; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QFrame#card { background: rgba(33, 23, 25, 214); border: 1px solid #4f302d; border-radius: 11px; }
QFrame#actionTabs { background: rgba(25, 18, 20, 170); border: 1px solid #4f302d; border-radius: 9px; }
QPushButton#actionTab { background: transparent; color: #a9938e; border: none; padding: 8px 16px; font-weight: 700; }
QPushButton#actionTab:checked { background: rgba(94, 71, 70, 190); color: #fff4eb; border: 1px solid #75504a; }
QFrame#deviceWorkspace { background: rgba(24, 14, 16, 96); border: 1px solid #7a3027; border-radius: 13px; }
QFrame#mappingEditorCard { background: rgba(31, 20, 22, 232); border: 1px solid #9b493b; border-radius: 13px; }
QScrollArea#mappingEditorBody { background: transparent; border: none; }
QScrollArea#mappingEditorBody QScrollBar:vertical { background: #1b1113; width: 9px; border: none; }
QScrollArea#mappingEditorBody QScrollBar::handle:vertical { background: #754138; min-height: 34px; border-radius: 4px; }
QScrollArea#mappingEditorBody QScrollBar::add-line:vertical, QScrollArea#mappingEditorBody QScrollBar::sub-line:vertical { height: 0; }
QPushButton#profilePill { background: #201416; color: #f3e5dc; border: 1px solid #67403a; border-radius: 12px; padding: 6px 11px; font-size: 10px; font-weight: 750; }
QPushButton#profilePill:hover { border-color: #e85b42; }
QPushButton#saveProfileAsButton { padding: 6px 10px; border-radius: 10px; font-size: 10px; }
QLabel#profilePendingState { color: #ffb29f; font-size: 9px; font-weight: 700; }
QLabel#canvasHint { color: #9d8983; font-size: 10px; }
QLabel#inspectorTitle { color: #fff0e6; font-size: 21px; font-weight: 800; }
QLabel#roleHuman { color: #efe4dd; background: #2b1e20; padding: 8px; border-left: 3px solid #e85b42; }
QLabel#roleAgent { color: #c8dbf3; background: #1d2a3a; padding: 8px; border-left: 3px solid #7196c4; }
QLabel#roleContext { color: #f7eee8; background: #171113; padding: 8px; border-left: 3px solid #d6c9c1; }
QFrame#roleHuman { background: #2b1e20; border: none; border-left: 3px solid #e85b42; }
QFrame#roleAgent { background: #1d2a3a; border: none; border-left: 3px solid #7196c4; }
QFrame#roleContext { background: #171113; border: none; border-left: 3px solid #d6c9c1; }
QFrame#roleHuman QLabel { color: #efe4dd; background: transparent; }
QFrame#roleAgent QLabel { color: #c8dbf3; background: transparent; }
QFrame#roleContext QLabel { color: #f7eee8; background: transparent; }
QFrame#bottomBar { background: rgba(31, 18, 20, 170); border: 1px solid #552720; border-radius: 9px; }
QFrame#bottomBar QLabel { color: #e9ddd6; }
QLabel#bottomMuted { color: #a18d87; font-size: 11px; }
QPushButton#primary, QPushButton[buttonRole="primary"] { background: #df4f33; color: #fff7f1; padding: 10px 16px; border: 1px solid #ff7656; border-radius: 7px; font-weight: 700; }
QPushButton#primary:hover, QPushButton[buttonRole="primary"]:hover { background: #ef6041; }
QPushButton#secondary, QPushButton[buttonRole="secondary"] { background: #2b1d1f; color: #eaded7; padding: 9px 14px; border: 1px solid #74504a; border-radius: 7px; font-weight: 650; }
QPushButton#secondary:hover, QPushButton[buttonRole="secondary"]:hover { border-color: #e85b42; color: #ffffff; }
QPushButton#promptDirectionCard { background: #271a1c; color: #eee3dc; padding: 12px; border: 1px solid #65413b; border-radius: 9px; text-align: left; min-width: 160px; min-height: 72px; }
QPushButton#promptDirectionCard:hover { border: 2px solid #e85b42; }
QPushButton#promptDirectionCard[selected="true"] { background: #50251f; border: 3px solid #e85b42; font-weight: 700; }
QLabel#promptJoystickCenter { background: #171113; color: #f7eee8; padding: 14px; border: 2px solid #6d3027; border-radius: 34px; font-weight: 800; }
QComboBox, QLineEdit, QSpinBox, QTextEdit, QPlainTextEdit, QListWidget { background: #171113; color: #f1e6df; selection-background-color: #9d3b2b; selection-color: #ffffff; padding: 8px; border: 1px solid #65423d; border-radius: 7px; min-width: 150px; }
QComboBox QAbstractItemView { background: #211719; color: #f1e6df; border: 1px solid #734137; selection-background-color: #6b2d25; }
QMenu { background: #211719; color: #eee3dc; border: 1px solid #5d332e; }
QMenu::item:selected { background: #5a2823; }
QInputDialog { background: rgba(38, 38, 40, 248); color: #f5f5f7; }
QInputDialog QLabel { color: #f5f5f7; }
QInputDialog QLineEdit { background: rgba(12, 12, 14, 220); color: #f5f5f7; }
QInputDialog QPushButton { background: rgba(255, 255, 255, 18); color: #f5f5f7; border-color: rgba(255, 255, 255, 42); }
QDialog#onboardingDialog { background: transparent; }
QFrame#onboardingCard { background: rgba(18, 18, 18, 246); border: none; border-radius: 22px; }
QLabel#onboardingBrand { color: #a99892; font-size: 10px; font-weight: 800; letter-spacing: 1.4px; }
QPushButton#onboardingClose { min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; padding: 0; background: rgba(255, 255, 255, 10); color: #b7aaa4; border: none; border-radius: 17px; font-size: 20px; }
QPushButton#onboardingClose:hover { background: rgba(255, 255, 255, 22); color: #ffffff; border: none; }
QLabel#onboardingProgress { color: #df684d; font-size: 11px; font-weight: 800; letter-spacing: 0.8px; }
QLabel#onboardingTitle { color: #fff7f1; font-size: 30px; font-weight: 800; }
QLabel#onboardingDescription { color: #c4b8b2; font-size: 15px; }
QFrame#onboardingAction { background: rgba(255, 255, 255, 10); border: none; border-radius: 14px; }
QLabel#onboardingStepNumber { color: #df684d; font-size: 24px; font-weight: 800; }
QLabel#onboardingActionText { color: #f1e8e2; font-size: 14px; font-weight: 700; }
QLabel#onboardingDots { color: #b7aaa4; font-size: 12px; }
QPushButton#onboardingSkip { background: transparent; border: none; color: #a99b95; }
QPushButton#onboardingSkip:hover { background: rgba(255, 255, 255, 10); border: none; color: #ffffff; }
QProgressBar { background: #171113; color: #eaded7; border: 1px solid #593b36; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #df4f33; border-radius: 4px; }
QSlider#lightingBrightness { min-width: 250px; min-height: 26px; }
QSlider#lightingBrightness::groove:horizontal { height: 4px; background: #4d3b39; border-radius: 2px; }
QSlider#lightingBrightness::sub-page:horizontal { background: #df4f33; border-radius: 2px; }
QSlider#lightingBrightness::handle:horizontal { width: 16px; margin: -6px 0; background: #f3e7df; border: 2px solid #df4f33; border-radius: 8px; }
QLabel#lightingLevelValue { color: #fff1e7; font-weight: 750; }
QLabel#lightingLevelMarkNumber { color: #e9ded7; font-size: 10px; font-weight: 700; }
QLabel#lightingLevelMarkName { color: #9c8883; font-size: 9px; }
QScrollBar:vertical { background: #1a1113; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #68423c; min-height: 28px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QPushButton#controlKey { background: #f7f7f2; border: 1px solid #aeb3b1; border-radius: 6px; text-align: left; }
QPushButton#controlKey[mapped="true"] { border: 2px solid #343839; }
QPushButton#controlKey[role="agent"] { background: #dfeaf5; border: 1px solid #9bb2c9; }
QPushButton#controlKey[role="agent"][mapped="true"] { border: 2px solid #617b99; }
QPushButton#controlKey:hover, QPushButton#secondaryControl:hover, QPushButton#encoderControl:hover, QPushButton#joystickControl:hover { border: 2px solid #e85b42; }
QPushButton#controlKey[selected="true"], QPushButton#secondaryControl[selected="true"], QPushButton#encoderControl[selected="true"], QPushButton#joystickControl[selected="true"] { background: #f7dfd7; border: 3px solid #e85b42; }
QPushButton#controlKey[role="agent"][selected="true"] { background: #cfdff2; border: 3px solid #e85b42; }
QPushButton#controlKey:focus, QPushButton#secondaryControl:focus, QPushButton#encoderControl:focus, QPushButton#joystickControl:focus { border: 3px solid #bd3e28; }
QFrame#deviceShell { background: #8a8b86; border: 8px solid #5f615e; border-radius: 24px; }
QFrame#deviceFace { background: #e6e5df; border: 2px solid #252829; border-radius: 16px; }
QFrame#displayControl { background: #171a1b; border: 3px solid #050606; border-radius: 38px; }
QFrame#displayControl QLabel { color: #f4f5f1; }
QPushButton#secondaryControl { background: #c8cbc9; border: 3px solid #717675; border-radius: 36px; }
QPushButton#encoderControl { background: #c8cbc9; border: 4px solid #717675; border-radius: 47px; }
QPushButton#joystickControl { background: #1f2324; border: 2px solid #080909; border-radius: 6px; }
QLabel#joystickKnob { color: #202324; background: #f3f3ee; border: 2px solid #c6c9c6; border-radius: 25px; font-size: 10px; font-weight: 800; }
QLabel#controlId { color: #717674; font-size: 9px; letter-spacing: 0.5px; }
QLabel#controlAction { color: #202324; font-size: 11px; font-weight: 800; }
QPushButton#controlKey[role="agent"] QLabel#controlAction { color: #2f4764; }
QLabel#controlName { color: #777c7a; font-size: 8px; }
QPushButton#ghostOnDark { color: #d9cbc5; background: transparent; border: 1px solid #69433d; border-radius: 7px; padding: 6px 10px; }
QPushButton#ghostOnDark:hover { color: #ffffff; border-color: #e85b42; }
QToolTip { color: #f5eae3; background: #241719; border: 1px solid #764237; padding: 5px; }
"""

APP_STYLE += """
QWidget {
    font-family: ".AppleSystemUIFont", "PingFang SC", sans-serif;
    color: #f5f5f7;
}
QFrame#consoleFrame {
    background: rgba(28, 28, 30, 160);
    border: none;
    border-radius: 22px;
}
QLabel#windowChromeTitle { color: rgba(235, 235, 245, 150); }
QFrame#navigationCapsule {
    background: rgba(20, 20, 22, 176);
    border-color: rgba(255, 255, 255, 34);
}
QLabel#shellEdition, QLabel#deviceAuthSummary { color: rgba(235, 235, 245, 138); }
QLabel#deviceNameSummary { color: #f5f5f7; }
QPushButton {
    background: rgba(255, 255, 255, 16);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 32);
    border-radius: 10px;
}
QPushButton:hover {
    background: rgba(255, 255, 255, 28);
    border-color: rgba(255, 255, 255, 62);
}
QPushButton:disabled {
    background: rgba(255, 255, 255, 7);
    color: rgba(235, 235, 245, 72);
    border-color: rgba(255, 255, 255, 16);
}
QPushButton#settingsLanguageButton {
    color: rgba(235, 235, 245, 178);
    border-color: rgba(255, 255, 255, 32);
    border-radius: 9px;
}
QPushButton#settingsLanguageButton[active="true"] {
    background: rgba(255, 255, 255, 28);
    border-color: rgba(255, 255, 255, 54);
}
QFrame#settingsList {
    border-top: 1px solid rgba(255, 255, 255, 30);
    border-bottom: 1px solid rgba(255, 255, 255, 30);
}
QFrame#settingsRow { border-bottom: 1px solid rgba(255, 255, 255, 22); }
QFrame#topBar { border-bottom: 1px solid rgba(255, 255, 255, 26); }
QFrame#topMetric { border-left: 1px solid rgba(255, 255, 255, 26); }
QLabel#eyebrow, QLabel#workspaceHeroCaption, QLabel#muted,
QLabel#canvasHint, QLabel#bottomMuted { color: rgba(235, 235, 245, 142); }
QLabel#bleShortcutHeading { color: rgba(235, 235, 245, 158); }
QLabel#bleShortcutChip {
    color: rgba(245, 245, 247, 220);
    background: rgba(255, 255, 255, 10);
    border-color: rgba(255, 255, 255, 28);
    border-radius: 7px;
}
QLabel#bleShortcutNote { color: rgba(235, 235, 245, 124); }
QLabel#pageTitle, QLabel#workspaceHeroTitle, QLabel#headerValue,
QLabel#inspectorTitle { color: #f5f5f7; }
QFrame#modeNormal { background: rgba(255, 255, 255, 24); border-color: rgba(255, 255, 255, 38); }
QFrame#modeCodex { background: rgba(74, 110, 155, 88); border-color: rgba(133, 174, 222, 92); }
QFrame#card {
    background: rgba(255, 255, 255, 16);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 16px;
}
QLabel#statusReady[compactStatus="true"],
QLabel#statusWarn[compactStatus="true"],
QLabel#statusError[compactStatus="true"] {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0 4px;
    font-weight: 650;
}
QLabel#statusReady[compactStatus="true"] { color: #8fd3a8; }
QLabel#statusWarn[compactStatus="true"] { color: #ffb29f; }
QLabel#statusError[compactStatus="true"] { color: #ffaaa2; }
QLabel#extensionPlatformStatus {
    background: transparent;
    color: rgba(235, 235, 245, 150);
    border: none;
    border-radius: 0;
    padding: 0;
    font-weight: 600;
}
QFrame#deviceWorkspace {
    background: rgba(12, 12, 14, 92);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 18px;
}
QFrame#mappingEditorCard {
    background: rgba(30, 30, 32, 218);
    border: 1px solid rgba(255, 255, 255, 48);
    border-radius: 16px;
}
QFrame#shortcutRecorder {
    background: rgba(255, 255, 255, 13);
    border: 1px solid rgba(255, 255, 255, 38);
    border-radius: 12px;
}
QLabel#shortcutRecorderHeading {
    color: rgba(235, 235, 245, 150);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#shortcutRecorderPreview {
    color: #ffffff;
    font-size: 22px;
    font-weight: 800;
}
QLabel#shortcutRecorderMessage {
    color: rgba(235, 235, 245, 150);
    font-size: 11px;
}
QPushButton#shortcutRecordButton[recording="true"] {
    background: rgba(255, 106, 77, 42);
    color: #ffb19f;
    border-color: #ff6a4d;
}
QPushButton#shortcutManualToggle {
    text-align: left;
}
QFrame#bottomBar {
    background: rgba(20, 20, 22, 142);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 13px;
}
QPushButton#secondary, QPushButton[buttonRole="secondary"],
QPushButton#profilePill, QPushButton#ghostOnDark {
    background: rgba(255, 255, 255, 14);
    color: #f5f5f7;
    border-color: rgba(255, 255, 255, 34);
}
QPushButton#promptDirectionCard {
    background: rgba(255, 255, 255, 14);
    border-color: rgba(255, 255, 255, 34);
    border-radius: 12px;
}
QComboBox, QLineEdit, QSpinBox, QTextEdit, QPlainTextEdit, QListWidget {
    background: rgba(12, 12, 14, 178);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 36);
    border-radius: 10px;
}
QComboBox QAbstractItemView {
    background: rgba(28, 28, 30, 238);
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 36);
    selection-background-color: rgba(255, 255, 255, 28);
}
QProgressBar { background: rgba(8, 8, 10, 150); border-color: rgba(255, 255, 255, 28); }
QSlider#lightingBrightness::groove:horizontal { background: rgba(235, 235, 245, 46); }
QSlider#lightingBrightness::sub-page:horizontal { background: #ff6a4d; }
QSlider#lightingBrightness::handle:horizontal { background: #f5f5f7; border-color: #ff6a4d; }
QLabel#lightingLevelValue, QLabel#lightingLevelMarkNumber { color: #f5f5f7; }
QLabel#lightingLevelMarkName { color: rgba(235, 235, 245, 130); }
QScrollBar:vertical { background: rgba(8, 8, 10, 80); }
QScrollBar::handle:vertical { background: rgba(235, 235, 245, 76); }
QMessageBox {
    background-color: #252527;
    color: #f5f5f7;
}
QMessageBox QLabel {
    background: transparent;
    color: #f5f5f7;
}
QMessageBox QPushButton {
    min-width: 76px;
    background: #3a3a3c;
    color: #f5f5f7;
    border: 1px solid rgba(255, 255, 255, 44);
}
QMessageBox QPushButton:hover {
    background: #48484a;
    border-color: rgba(255, 255, 255, 74);
}
QToolTip {
    color: #f5f5f7;
    background: rgba(28, 28, 30, 232);
    border: 1px solid rgba(255, 255, 255, 42);
    border-radius: 7px;
}
"""

# Product shell aligned with the selected macOS glass reference.  The blue in
# the reference comes from the desktop behind the window, so the application
# adds only a light neutral tint above the native vibrancy material.
APP_STYLE += """
QFrame#consoleFrame {
    background: rgba(20, 20, 20, 40);
    border: none;
    border-radius: 26px;
}
QFrame#topNavigation { background: transparent; }
QFrame#navigationCapsule {
    background: transparent;
    border: none;
}
QPushButton#navIconButton {
    min-width: 42px;
    max-width: 42px;
    min-height: 44px;
    max-height: 44px;
    padding: 0;
    color: rgba(224, 222, 211, 148);
    border-radius: 22px;
}
QPushButton#navIconButton:hover {
    background: transparent;
    border-color: transparent;
}
QPushButton#navIconButton[active="true"] {
    background: transparent;
    border-color: transparent;
    color: rgba(255, 254, 248, 245);
    min-width: 116px;
    max-width: 116px;
    padding: 0 10px;
    font-weight: 750;
}
QLabel#deviceNameSummary { color: rgba(245, 245, 238, 225); }
QLabel#deviceAuthSummary[trust="authenticated"] { color: #79b997; }
QFrame#topBar {
    background: transparent;
    border: none;
    border-bottom: 1px solid rgba(225, 238, 244, 22);
}
QLabel#pageTitle { font-size: 24px; font-weight: 760; }
QFrame#deviceWorkspace {
    background: transparent;
    border: none;
    border-radius: 0;
}
QFrame#settingsList {
    background: transparent;
    border: none;
}
QFrame#settingsRow {
    background: rgba(19, 20, 18, 210);
    border: none;
    border-radius: 16px;
}
QFrame#deviceContextCard,
QFrame#bleSlotsCard,
QFrame#selectionInspectorCard,
QFrame#mappingEditorCard,
QFrame#syncSummaryCard {
    background: rgba(30, 30, 26, 244);
    border: none;
    border-radius: 26px;
}
QLabel#devicePanelName {
    color: #f2f1e8;
    font-size: 28px;
    font-weight: 760;
}
QLabel#devicePanelSubtitle,
QLabel#devicePanelKey {
    color: rgba(220, 219, 207, 118);
}
QLabel#devicePanelKey {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#devicePanelValue {
    color: rgba(244, 243, 234, 220);
    font-size: 11px;
    font-weight: 700;
}
QLabel#devicePanelValue[state="ready"] { color: #79b997; }
QLabel#devicePanelLive {
    color: #79b997;
    font-size: 10px;
    font-weight: 750;
    letter-spacing: 1px;
}
QFrame#deviceStatusRow {
    background: transparent;
    border: none;
    border-top: 1px solid rgba(228, 226, 211, 24);
}
QFrame#deviceContextCard QFrame#topMetric {
    background: transparent;
    border: none;
}
QFrame#deviceContextCard QPushButton#profilePill {
    background: #2b2a26;
    border: none;
    border-radius: 20px;
    min-height: 24px;
    padding: 8px 16px;
    text-align: left;
}
QFrame#deviceContextCard QPushButton#saveProfileAsButton {
    background: transparent;
    border: none;
    color: rgba(224, 222, 211, 150);
    padding: 5px 2px;
    text-align: left;
}
QFrame#bleSlotsCard {
    border-radius: 24px;
}
QFrame#deviceStage {
    background: transparent;
    border: none;
}
QLabel#deviceStageName {
    color: rgba(217, 218, 208, 105);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QFrame#deviceShell {
    background: #12130f;
    border: none;
    border-radius: 56px;
}
QFrame#deviceFace {
    background: transparent;
    border: none;
    border-radius: 28px;
}
QFrame#displayControl {
    background: #121311;
    border: 2px solid #eeede4;
    border-radius: 38px;
}
QPushButton#controlKey {
    background: #2d2c28;
    border: 1px solid rgba(234, 232, 220, 128);
    border-radius: 8px;
}
QPushButton#controlKey[mapped="true"] { border: 2px solid rgba(234, 232, 220, 160); }
QPushButton#controlKey[role="agent"] {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34495e, stop:1 #2d4053);
    border: 1px solid #34495e;
}
QPushButton#controlKey[role="agent"][mapped="true"] { border: 2px solid #34495e; }
QPushButton#controlKey:hover,
QPushButton#secondaryControl:hover,
QPushButton#encoderControl:hover,
QPushButton#joystickControl:hover { border: 2px solid #db6b2b; }
QPushButton#controlKey[selected="true"],
QPushButton#secondaryControl[selected="true"],
QPushButton#encoderControl[selected="true"],
QPushButton#joystickControl[selected="true"] {
    background: #34312e;
    border: 3px solid #db6b2b;
}
QPushButton#controlKey[role="agent"][selected="true"] {
    background: #2c4565;
    border: 3px solid #db6b2b;
}
QLabel#controlId { color: rgba(224, 222, 211, 148); }
QLabel#controlAction { color: #f2f1e8; }
QPushButton#controlKey[role="agent"] QLabel#controlAction { color: #a3b5e7; }
QLabel#controlName { color: rgba(224, 222, 211, 128); }
QPushButton#secondaryControl,
QPushButton#encoderControl,
QPushButton#joystickControl {
    background: #171815;
    border-color: rgba(228, 226, 211, 70);
}
QLabel#joystickKnob {
    color: #eceae2;
    background: #1b1c18;
    border: none;
}
QPushButton#joystickControl {
    background: #35352f;
    border-radius: 38px;
}
QPushButton#encoderControl {
    background: #171815;
    border-width: 2px;
    border-radius: 48px;
}
QFrame#selectionInspectorCard,
QFrame#mappingEditorCard {
    background: rgba(30, 30, 26, 248);
    border: none;
}
QFrame#roleHuman,
QFrame#roleAgent,
QFrame#roleContext {
    background: transparent;
    border: none;
    border-top: 1px solid rgba(228, 226, 211, 26);
}
QFrame#roleHuman QLabel,
QFrame#roleAgent QLabel,
QFrame#roleContext QLabel { color: rgba(236, 235, 226, 190); }
QFrame#mappingEditorCard QLineEdit,
QFrame#mappingEditorCard QComboBox,
QFrame#mappingEditorCard QSpinBox {
    background: #2b2a26;
    color: #eceae2;
    border: 1px solid transparent;
    border-radius: 21px;
    min-height: 24px;
    padding: 8px 14px;
}
QFrame#mappingEditorCard QLineEdit:focus,
QFrame#mappingEditorCard QComboBox:focus,
QFrame#mappingEditorCard QSpinBox:focus {
    border-color: #a3b5e7;
}
QFrame#shortcutRecorder {
    background: #2b2a26;
    border: none;
    border-radius: 24px;
}
QPushButton#shortcutRecordButton {
    background: #ece8de;
    color: #24241f;
    border: 1px solid transparent;
    border-radius: 18px;
    min-height: 22px;
    padding: 6px 14px;
}
QPushButton#shortcutRecordButton:hover { background: #fffaf0; }
QPushButton#shortcutRecordButton:focus { border-color: #5574ed; }
QPushButton#shortcutRecordButton[recording="true"] {
    background: #5574ed;
    color: #ffffff;
    border-color: #a3b5e7;
}
QFrame#bottomBar,
QFrame#syncSummaryCard {
    background: rgba(30, 30, 26, 244);
    border: none;
    border-radius: 26px;
}
QPushButton#primary,
QPushButton[buttonRole="primary"] {
    background: #5574ed;
    border-color: #6f8bff;
    color: #ffffff;
    border-radius: 14px;
}
QPushButton#primary:hover,
QPushButton[buttonRole="primary"]:hover { background: #6481f1; }
QPushButton#secondary,
QPushButton[buttonRole="secondary"],
QPushButton#profilePill,
QPushButton#ghostOnDark {
    background: rgba(245, 244, 236, 10);
    border-color: rgba(245, 244, 236, 36);
    border-radius: 12px;
}
QFrame#mappingEditorCard QPushButton[buttonRole],
QFrame#syncSummaryCard QPushButton {
    min-height: 24px;
    padding: 8px 14px;
    border-radius: 20px;
}
QFrame#bleSlotsCard QPushButton#secondary {
    min-height: 0;
    padding: 0;
    border-radius: 14px;
}
QFrame#card,
QFrame#actionTabs {
    background: rgba(19, 20, 18, 210);
    border: none;
}
QPushButton#actionTab:checked {
    background: rgba(85, 116, 237, 46);
    border-color: rgba(111, 139, 255, 92);
    color: #ffffff;
}
QGroupBox {
    background: rgba(19, 20, 18, 116);
    border: none;
    border-radius: 16px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
}
QGroupBox::title {
    color: rgba(239, 238, 229, 198);
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}
QScrollBar:vertical,
QScrollArea#mappingEditorBody QScrollBar:vertical {
    background: transparent;
    width: 8px;
}
QScrollBar::handle:vertical,
QScrollArea#mappingEditorBody QScrollBar::handle:vertical {
    background: rgba(226, 229, 224, 62);
    min-height: 32px;
    border-radius: 4px;
}
QProgressBar::chunk { background: #5574ed; }
QSlider#lightingBrightness::sub-page:horizontal { background: #5574ed; }
QSlider#lightingBrightness::handle:horizontal { border-color: #5574ed; }
"""


@dataclass(frozen=True)
class _MappingEditingState:
    control_id: str
    short_name: str
    action: dict

MATRIX12_UNIT = 64
MATRIX12_GAP = 3
MATRIX12_HARDWARE_IDS = frozenset(
    {"WMP-S3-MATRIX12-V1", "WMP-S3-MATRIX12-POWER-V2"}
)
MATRIX12_AGENT_STATUS_KEYS = frozenset(
    {"key.1", "key.2", "key.4", "key.5", "key.6", "key.7"}
)
POWER_V2_LIGHTING_DEVICE_MAX = 80
POWER_V2_LIGHTING_LEVELS = (0, 10, 20, 40, POWER_V2_LIGHTING_DEVICE_MAX)
POWER_V2_HAPTIC_LEVELS = (0, 40, 50, 60, 80)


class _InstrumentCard(V4Card):
    """A static, fading dot texture behind the device and sync readouts."""

    def __init__(self, *, objectName: str) -> None:
        super().__init__(role="primary" if objectName == "deviceContextCard" else "secondary", dots=True)
        self.setObjectName(objectName)


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
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.setBrush(QColor(28, 27, 25, 140))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class _TopNavigationButton(QPushButton):
    """Show the active page name while keeping inactive destinations compact."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt virtual method
        size = super().sizeHint()
        if self.property("active") is True:
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
        self.setText(self.accessibleName() if active else "")
        self._apply_navigation_icon()
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

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
        if self.property("active") is True:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(V4_TOKENS["baseDark"]))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(245, 245, 238, 10))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        if (
            self.property("active") is not True
            and option.state & QStyle.StateFlag.State_HasFocus
        ):
            focus_rect = rect.adjusted(1.0, 1.0, -1.0, -1.0)
            painter.setPen(QPen(QColor(245, 245, 238, 92), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                focus_rect,
                focus_rect.height() / 2,
                focus_rect.height() / 2,
            )
        painter.end()
        option.text = option.text.replace("&", "&&")
        label_painter = QStylePainter(self)
        label_painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)


class _DottedRoot(QWidget):
    """Translucent console field with the restrained engineering dot grid."""

    _RESIZE_MARGIN = 8

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)

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
        clip.addRoundedRect(QRectF(self.rect()), 25, 25)
        painter.setClipPath(clip)
        native = getattr(self.window(), "_macos_vibrancy_enabled", False)
        painter.fillRect(event.rect(), ROOT_MATERIAL_TINT if native else QColor(V4_TOKENS["baseDark"]))
        painter.setPen(QColor(239, 234, 224, 5))
        spacing = 7
        left = max(0, event.rect().left() - event.rect().left() % spacing)
        top = max(0, event.rect().top() - event.rect().top() % spacing)
        for x in range(left, event.rect().right() + 1, spacing):
            for y in range(top, event.rect().bottom() + 1, spacing):
                painter.drawPoint(x, y)
        painter.end()


class _ResponsiveMappingWorkspace(QWidget):
    """Keep the device visible beside its inspector, stacking only when needed."""

    # Context rail (260) + board (420) + inspector (300) + two gaps (32).
    _STACK_BREAKPOINT = 1040

    def __init__(self, device: QWidget, inspector: QWidget | None) -> None:
        super().__init__(objectName="mappingWorkspace")
        self._inspector = inspector
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        self._layout.addWidget(device, 3)
        if inspector is not None:
            inspector.setMinimumWidth(300)
            inspector.setMaximumWidth(300)
            self._layout.addWidget(inspector, 0)
        self._update_direction(self.width())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        self._update_direction(event.size().width())
        super().resizeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        QTimer.singleShot(0, lambda: self._update_direction(self.width()))

    def set_inspector(self, inspector: QWidget | None) -> None:
        if self._inspector is not None:
            self._layout.removeWidget(self._inspector)
            self._inspector.hide()
            self._inspector.setParent(None)
            self._inspector.deleteLater()
        self._inspector = inspector
        if inspector is not None:
            inspector.setMinimumWidth(300)
            inspector.setMaximumWidth(300)
            self._layout.addWidget(inspector, 0)
            inspector.show()
        self._update_direction(self.width())
        self._layout.invalidate()
        self._layout.activate()
        self.updateGeometry()

    def _update_direction(self, width: int) -> None:
        stacked = self._inspector is not None and width < self._STACK_BREAKPOINT
        self.setProperty("stacked", stacked)
        self._layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )
        self._layout.setStretch(0, 0 if stacked else 3)
        device = self._layout.itemAt(0).widget()
        if device is not None and isinstance(device.layout(), QBoxLayout):
            device.layout().setDirection(QBoxLayout.BottomToTop if stacked else QBoxLayout.LeftToRight)
            rail = device.findChild(QWidget, "deviceContextRail")
            if rail is not None:
                rail.setMinimumWidth(0 if stacked else 260)
                rail.setMaximumWidth(16777215 if stacked else 300)
        if self._inspector is not None:
            self._inspector.setMinimumHeight(540 if stacked else 0)
            self._inspector.setMaximumWidth(16777215 if stacked else 300)
            self._layout.setAlignment(
                self._inspector,
                Qt.AlignHCenter if stacked else Qt.AlignmentFlag(0),
            )
        self._layout.invalidate()
        self._layout.activate()
        # Measure only after switching the inner layout back to columns.
        # Otherwise its stacked minimum height survives a narrow-to-wide resize.
        if stacked:
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
        else:
            rail = device.findChild(QWidget, "deviceContextRail") if device is not None else None
            rail_height = rail.layout().totalHeightForWidth(rail.width()) if rail is not None else 0
            self.setFixedHeight(max(510, self.window().height() - 140, rail_height,
                                    device.minimumSizeHint().height() if device is not None else 0))


class _SettingsWorkspace(QWidget):
    """Move the settings navigation above its content on narrow windows."""

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._arrange)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._arrange)

    def _arrange(self) -> None:
        row = self.layout()
        if row is None or row.count() < 2:
            return
        compact = self.width() < 1040
        navigation = row.itemAt(0).widget()
        nav = navigation.layout()
        row.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        navigation.setMinimumWidth(0 if compact else 280)
        navigation.setMaximumWidth(16777215 if compact else 280)
        nav.setDirection(QBoxLayout.LeftToRight if compact else QBoxLayout.TopToBottom)
        nav.itemAt(0).widget().setVisible(not compact)
        navigation.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum if compact else QSizePolicy.Preferred)


class _WindowChrome(QFrame):
    """Minimal frameless chrome required for real per-pixel translucency."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(objectName="windowChrome")
        self._window = window
        self.setFixedHeight(28)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 3)
        layout.setSpacing(0)

        controls = QWidget()
        controls.setFixedWidth(56)
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(7)
        for role, accessible_name, callback in (
            ("close", "关闭窗口", window.close),
            ("minimize", "最小化窗口", window.showMinimized),
            ("zoom", "缩放窗口", self._toggle_maximized),
        ):
            button = QPushButton("", objectName="windowControl")
            button.setProperty("windowRole", role)
            button.setAccessibleName(accessible_name)
            button.setToolTip(accessible_name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(callback)
            controls_layout.addWidget(button)
        layout.addWidget(controls)
        layout.addStretch(1)
        layout.addWidget(QLabel("BORING CONSOLE · COMMUNITY", objectName="windowChromeTitle"))
        layout.addStretch(1)
        right_balance = QWidget()
        right_balance.setFixedWidth(56)
        layout.addWidget(right_balance)

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
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        view_model: MainViewModel,
        *,
        language_manager: LanguageManager | None = None,
        background_controller: PromptBackgroundController | None = None,
        onboarding_settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._view_model = view_model
        self._background_controller = background_controller
        self._onboarding_settings = onboarding_settings
        self._onboarding_dialog: OnboardingDialog | None = None
        self._onboarding_prompt_scheduled = False
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("MainWindow requires an active QApplication")
        self._language_manager = language_manager or LanguageManager(
            application,
            initial_language=SIMPLIFIED_CHINESE,
            persist=False,
            parent=self,
        )
        application.setApplicationDisplayName(
            "BORING Console Community"
        )
        self._nav_buttons: dict[str, QPushButton] = {}
        self._language_buttons: dict[str, QPushButton] = {}
        self._selected_control_id: str | None = None
        self._selected_macro_id: int | None = None
        self._settings_section = "general"
        self._screen_icon_drafts: dict[tuple[str, str], ScreenIconDraft] = {}
        self._screen_glyph_drafts: dict[tuple[str, str, str], GlyphDraft] = {}
        self._suppress_mapping_edit_restore = False
        self._pending_mapping_editing_state: _MappingEditingState | None = None
        self._compact_mode = False
        self.setWindowTitle("BORING Console Community")
        self._bounds_screen = None
        self._screen_tracking_connected = False
        self.setMinimumSize(1100, 700)
        self.resize(1280, 800)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet(APP_STYLE + V4_STYLE)
        self._build_application_menu()

        root = _DottedRoot(objectName="root")
        root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root.setAutoFillBackground(False)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        console_frame = QFrame(objectName="consoleFrame")
        console_layout = QVBoxLayout(console_frame)
        console_layout.setContentsMargins(0, 0, 0, 0)
        console_layout.setSpacing(0)
        self._window_chrome = _WindowChrome(self)
        console_layout.addWidget(self._window_chrome)
        self._top_navigation = self._build_top_navigation()
        console_layout.addWidget(self._top_navigation)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(26, 8, 26, 18)
        console_layout.addWidget(self._content, 1)
        root_layout.addWidget(console_frame)
        self.setCentralWidget(root)
        self._macos_vibrancy_enabled = False
        self._connection_terminal = ConnectionTerminal(self)
        self._connection_terminal.hide()

        view_model.changed.connect(self.render)
        view_model.lighting_preview_changed.connect(
            self._lighting_preview_status_changed
        )
        self._language_manager.language_changed.connect(self._language_changed)
        if self._background_controller is not None:
            self._background_controller.state_changed.connect(
                lambda: self.render(self._view_model.model)
            )
            self._background_controller.codex_usage_changed.connect(self._update_home_usage)
        self.render(view_model.model)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            if not self._screen_tracking_connected:
                handle.screenChanged.connect(self._watch_window_screen)
                self._screen_tracking_connected = True
            self._watch_window_screen(handle.screen())
        QTimer.singleShot(0, self._install_macos_vibrancy)
        if not self._onboarding_prompt_scheduled:
            self._onboarding_prompt_scheduled = True
            QTimer.singleShot(0, self._show_first_run_onboarding)

    def _watch_window_screen(self, screen) -> None:
        if self._bounds_screen is not screen:
            if self._bounds_screen is not None:
                self._bounds_screen.availableGeometryChanged.disconnect(self._fit_window_to_available_area)
            self._bounds_screen = screen
            screen.availableGeometryChanged.connect(self._fit_window_to_available_area)
        self._fit_window_to_available_area(screen.availableGeometry())

    def _fit_window_to_available_area(self, area) -> None:
        # Qt screen geometry is in logical pixels and excludes the taskbar/Dock.
        self.setMinimumSize(min(1100, area.width()), min(700, area.height()))
        if self.isMaximized() or self.isFullScreen():
            return
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        self.move(
            max(area.left(), min(self.x(), area.right() - self.width() + 1)),
            max(area.top(), min(self.y(), area.bottom() - self.height() + 1)),
        )

    def _install_macos_vibrancy(self) -> None:
        self._macos_vibrancy_enabled = install_macos_vibrancy(
            self,
            corner_radius=25.0,
        )
        frame = self.findChild(QFrame, "consoleFrame")
        frame.setProperty("nativeGlass", self._macos_vibrancy_enabled)
        frame.style().unpolish(frame)
        frame.style().polish(frame)
        self.centralWidget().update()

    def _show_first_run_onboarding(self) -> None:
        if self._onboarding_settings is None:
            return
        completed = self._onboarding_settings.value(
            ONBOARDING_COMPLETED_KEY,
            False,
            type=bool,
        )
        if not completed:
            self._show_onboarding()

    def _show_onboarding(self) -> None:
        if self._onboarding_dialog is not None:
            self._onboarding_dialog.raise_()
            self._onboarding_dialog.activateWindow()
            return
        dialog = OnboardingDialog(self)
        dialog.page_requested.connect(self._open_onboarding_page)
        self._onboarding_dialog = dialog
        dialog.finished.connect(self._onboarding_finished)
        self._language_manager.retranslate_widget_tree(dialog)
        dialog.open()

    def _open_onboarding_page(self, page: str) -> None:
        model = self._view_model
        if model.firmware_update.blocks_editing or model.calibration.blocks_editing:
            QMessageBox.information(self, translate_ui_text("使用引导"),
                                    translate_ui_text("请先完成当前固件维护或校准，再打开配置页面。"))
            return
        if page == "lighting" and model.draft is None:
            QMessageBox.information(self, translate_ui_text("使用引导"),
                                    translate_ui_text("连接设备并读取配置后，即可调整外观与反馈。"))
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
        if not hasattr(self, "_content"):
            return
        compact = self.width() < 820
        self._apply_compact_mode(compact)

    def _apply_compact_mode(self, compact: bool) -> None:
        if compact == self._compact_mode:
            return
        self._compact_mode = compact
        self._shell_edition.setVisible(not compact)
        self._device_name_summary.setVisible(not compact)
        margins = (16, 8, 16, 12) if compact else (26, 8, 26, 18)
        self._content_layout.setContentsMargins(*margins)

    def _build_application_menu(self) -> None:
        settings_menu = self.menuBar().addMenu("设置")
        settings_menu.setObjectName("settingsMenu")
        quit_action = QAction("退出 BORING 控制台", self)
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
        language_menu = settings_menu.addMenu("语言")
        language_menu.setObjectName("languageMenu")
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
            QMessageBox.warning(self, "无法修改登录启动", str(exc))

    def _language_changed(self, language: str) -> None:
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.setApplicationDisplayName(
                "BORING Console Community"
            )
        for action in self._language_actions.actions():
            action.setChecked(action.data() == language)
        self.render(self._view_model.model)

    def _build_top_navigation(self) -> QWidget:
        navigation = QFrame(objectName="topNavigation")
        navigation.setFixedHeight(62)
        layout = QHBoxLayout(navigation)
        layout.setContentsMargins(26, 4, 26, 4)
        layout.setSpacing(18)

        brand_box = QWidget()
        brand_layout = QVBoxLayout(brand_box)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(0)
        brand = Boring5RLabel(
            "BORING",
            scale=0.95,
            color="#f7f7f2",
            objectName="shellBrand",
        )
        self._shell_edition = Boring5RLabel(
            "CONSOLE",
            scale=0.75,
            color="#7E796F",
            objectName="shellEdition",
        )
        brand_layout.addWidget(brand)
        brand_layout.addWidget(self._shell_edition)
        layout.addWidget(brand_box, 0, Qt.AlignVCenter)
        layout.addStretch(1)

        entries = [
            ("overview", "按键配置", "keyboard", QStyle.StandardPixmap.SP_ComputerIcon, True),
            ("prompts", "快捷提示词", "bubble.left", QStyle.StandardPixmap.SP_MessageBoxInformation, True),
            ("lighting", "外观与反馈", "sun.max", QStyle.StandardPixmap.SP_DriveHDIcon, False),
            ("actions", "Playground", "dial.low", QStyle.StandardPixmap.SP_BrowserReload, True),
            ("settings", "设置", "gearshape", QStyle.StandardPixmap.SP_FileDialogDetailedView, True),
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
            button.setToolTip("外观与反馈 · 灯光、震动、屏幕" if page == "lighting" else label)
            button.setAccessibleName(label)
            button.setAccessibleDescription("切换界面")
            button.set_active(page == "overview")
            button.setEnabled(enabled)
            button.clicked.connect(
                lambda _checked=False, target=page: self._navigate(target)
            )
            self._nav_buttons[page] = button
            nav_layout.addWidget(button)
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index}"), self)
            shortcut.activated.connect(lambda button=button: button.click() if button.isEnabled() else None)
        layout.addWidget(capsule, 0, Qt.AlignCenter)
        layout.addStretch(1)

        device_summary = QWidget()
        device_layout = QVBoxLayout(device_summary)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(1)
        self._device_name_summary = QLabel(
            DEVICE_DISPLAY_NAME, objectName="deviceNameSummary"
        )
        self._device_name_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._device_name_summary.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._device_auth_summary = QLabel(
            "等待设备", objectName="deviceAuthSummary"
        )
        self._device_auth_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        device_layout.addWidget(self._device_name_summary)
        auth_row = QHBoxLayout()
        auth_row.addStretch(1)
        self._device_trust_mark = StatusMark("nolink", dot_size=1.7)
        auth_row.addWidget(self._device_trust_mark)
        auth_row.addWidget(self._device_auth_summary)
        device_layout.addLayout(auth_row)
        layout.addWidget(device_summary, 0, Qt.AlignVCenter)
        self._relink_button = QPushButton("ReLink", objectName="relinkButton")
        self._relink_button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        self._relink_button.setProperty("buttonRole", "secondary")
        self._relink_button.clicked.connect(self._view_model.refresh)
        layout.addWidget(self._relink_button)
        return navigation

    def _refresh_language_buttons(self) -> None:
        for language, button in self._language_buttons.items():
            button.setProperty("active", language == self._language_manager.language)
            button.style().unpolish(button)
            button.style().polish(button)

    def _confirm_leave_page(self) -> bool:
        prompt_editor = self._content.findChild(PromptLibraryEditor)
        if prompt_editor is not None and not prompt_editor.confirm_leave():
            return False
        editor = self._content.findChild(PreferencesEditor)
        draft = self._view_model.draft
        if editor is not None and draft is not None and editor.values() != (
            draft.config["lighting"], draft.config["haptic"], draft.config["display"]
        ):
            choice = QMessageBox.warning(
                self, "设备体验修改尚未保存",
                "灯光、震动或圆屏还有未保存的输入。保存仅保留到本地草稿，不会写入设备。",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice == QMessageBox.Save:
                return self._save_preferences(editor)
            return choice == QMessageBox.Discard
        return True

    def _navigate(self, page: str) -> None:
        if page != self._view_model.page and not self._confirm_leave_page():
            return
        self._view_model.navigate(page)

    def _refresh_device_summary(self, model: ScreenModel) -> None:
        snapshot = model.snapshot
        if model.state is AppState.AUTHENTICITY_FAILED:
            self._device_name_summary.setText(DEVICE_DISPLAY_NAME)
            self._device_name_summary.setToolTip("")
            set_translatable_text(self._device_auth_summary, "无法确认是 BORING 设备 [ UNTRUSTED ]")
            trust = "untrusted"
        elif snapshot is None:
            self._device_name_summary.setText(DEVICE_DISPLAY_NAME)
            self._device_name_summary.setToolTip("")
            set_translatable_text(self._device_auth_summary, "未连接设备 [ NO LINK ]")
            trust = "waiting"
        else:
            self._device_name_summary.setText(DEVICE_DISPLAY_NAME)
            self._device_name_summary.setToolTip(DEVICE_DISPLAY_NAME)
            connected = model.state in {AppState.READY, AppState.READ_ONLY}
            if connected and snapshot.trust.is_authenticated:
                set_translatable_text(self._device_auth_summary, "已认证 [ VERIFIED ]")
                trust = "authenticated"
            elif connected:
                set_translatable_text(self._device_auth_summary, "开发设备，未认证 [ DEV ]")
                trust = "development"
            else:
                set_translatable_text(self._device_auth_summary, "已断开 [ NO LINK ]")
                trust = "waiting"
        self._relink_button.setEnabled(
            not self._view_model.firmware_update.blocks_editing
            and not self._view_model.calibration.blocks_editing
        )
        self._device_auth_summary.setProperty("trust", trust)
        self._device_trust_mark.set_status({"authenticated": "verified", "development": "dev", "untrusted": "untrusted", "waiting": "nolink"}[trust])
        self._device_auth_summary.style().unpolish(self._device_auth_summary)
        self._device_auth_summary.style().polish(self._device_auth_summary)

    def render(self, model: ScreenModel) -> None:
        self._connection_terminal.observe(model)
        firmware_scroll = self._content.findChild(QScrollArea, "firmwareMaintenanceScroll")
        reuse_firmware_scroll = self._view_model.page == "firmware" and firmware_scroll is not None
        if self._view_model.page != "settings":
            self._language_buttons.clear()
        if self._background_controller is not None:
            listener = self._view_model.prompt_device.listener_status
            self._background_controller.set_helper_status(
                online=listener.online,
                state_label=self._language_manager.translate(listener.label),
                message=self._language_manager.translate(listener.message),
            )
        if self._view_model.page == "firmware":
            firmware_inputs = (
                model.state,
                _planned_tool_capability("firmware", model.snapshot),
                _planned_tool_facts("firmware", model.snapshot),
                replace(self._view_model.firmware_update,
                        message="", received_size=0, in_flight_size=0),
                replace(self._view_model.remote_firmware,
                        message="", received_size=0, total_size=0),
                self._view_model.calibration.blocks_editing,
                self._view_model.write_transaction.blocks_editing,
                self._factory_reset_available(model.snapshot),
                self._language_manager.language,
                self._compact_mode,
            )
            if reuse_firmware_scroll and firmware_inputs == self._firmware_render_inputs:
                # Byte counters and status text change on every chunk. Updating
                # those widgets in place avoids detaching the native scroll view.
                self._refresh_firmware_progress(firmware_scroll)
                self._refresh_device_summary(model)
                return
            self._firmware_render_inputs = firmware_inputs
        prompt_editing_state: PromptEditingState | None = None
        current_prompt_editor = self._content.findChild(PromptLibraryEditor)
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

        current_preferences_page = self._content.findChild(
            QScrollArea, "preferencesPage"
        )
        current_draft = self._view_model.draft
        reuse_preferences_page = (
            self._view_model.page == "lighting"
            and current_preferences_page is not None
            and current_draft is not None
            and current_preferences_page.property("draftIdentity")
            == id(current_draft)
            and current_preferences_page.property("editingBlocked")
            == self._view_model.write_transaction.blocks_editing
            and current_preferences_page.property("previewSupported")
            == self._view_model.lighting_preview.supported
            and current_preferences_page.property("modelState") == model.state.value
            and current_preferences_page.property("transactionState")
            == self._view_model.write_transaction.state.value
            and model.snapshot is not None
            and current_preferences_page.property("devicePort")
            == model.snapshot.port_name
        )

        current_diagnostics_page = self._content.findChild(DiagnosticsPage)
        reuse_diagnostics_page = (
            self._view_model.page == "diagnostics"
            and current_diagnostics_page is not None
        )

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

        has_draft = self._view_model.draft is not None
        firmware_blocks = self._view_model.firmware_update.blocks_editing
        calibration_blocks = self._view_model.calibration.blocks_editing
        self._nav_buttons["overview"].setEnabled(not firmware_blocks and not calibration_blocks)
        self._nav_buttons["lighting"].setEnabled(
            has_draft and not firmware_blocks and not calibration_blocks
        )
        self._nav_buttons["prompts"].setEnabled(
            not firmware_blocks and not calibration_blocks
        )
        self._nav_buttons["actions"].setEnabled(
            not firmware_blocks and not calibration_blocks
        )
        self._nav_buttons["settings"].setEnabled(
            not firmware_blocks and not calibration_blocks
        )
        active_nav_page = (
            "settings"
            if self._view_model.page in {"settings", "diagnostics", "firmware", "joystick"}
            else self._view_model.page
        )
        for page, button in self._nav_buttons.items():
            button.set_active(page == active_nav_page)
        if reuse_firmware_scroll:
            # Keep the scroll view under the styled window even when a phase
            # changes. Detaching it drops inherited styles and clamps its range
            # before _firmware_page can save the user's scroll position.
            self._firmware_page(model, scroll=firmware_scroll)
            for button in self._content.findChildren(QPushButton, "settingsGroup"):
                button.setEnabled(not calibration_blocks and
                                  (not firmware_blocks or bool(button.property("selected"))))
            self._refresh_device_summary(model)
            self._language_manager.retranslate_widget_tree(self)
            return
        if reuse_actions_page:
            self._content_layout.removeWidget(current_actions_page)
            current_actions_page.setParent(None)
        if reuse_preferences_page:
            self._content_layout.removeWidget(current_preferences_page)
            current_preferences_page.setParent(None)
        if reuse_diagnostics_page:
            self._content_layout.removeWidget(current_diagnostics_page)
            current_diagnostics_page.setParent(None)
        # Keep the transcript and its timers while the surrounding page changes.
        self._connection_terminal.setParent(self)
        self._connection_terminal.hide()
        _delete_layout(self._content_layout)
        if self._view_model.page not in {"overview", "settings", "diagnostics", "firmware", "joystick", "lighting", "prompts"}:
            self._content_layout.addWidget(self._header(model))
        if self._view_model.page == "prompts":
            prompt_editor = self._prompt_library_page(model)
            self._content_layout.addWidget(prompt_editor, 1)
            if prompt_editing_state is not None:
                prompt_editor.restore_editing_state(prompt_editing_state)
        elif self._view_model.page == "actions":
            actions_page = (
                current_actions_page
                if reuse_actions_page
                else self._actions_page()
            )
            self._content_layout.addWidget(actions_page, 1)
            actions_page.refresh_catalog()
        elif self._view_model.page == "settings":
            self._content_layout.addWidget(self._settings_page(), 1)
        elif self._view_model.page == "firmware":
            firmware_page = self._firmware_page(model)
            self._content_layout.addWidget(self._settings_workspace(firmware_page, "firmware"), 1)
        elif self._view_model.page == "joystick":
            self._content_layout.addWidget(self._joystick_calibration_page(model), 1)
        elif self._view_model.page == "diagnostics":
            diagnostics_page = (
                current_diagnostics_page
                if reuse_diagnostics_page
                else DiagnosticsPage(self._view_model)
            )
            self._content_layout.addWidget(self._settings_workspace(diagnostics_page, "diagnostics"), 1)
            diagnostics_page.refresh()
        elif model.state in {AppState.READY, AppState.READ_ONLY, AppState.DISCONNECTED} and model.snapshot is not None:
            if self._view_model.page == "sequences" and has_draft:
                self._content_layout.addWidget(self._macro_page(model.snapshot), 1)
            elif self._view_model.page == "lighting" and has_draft:
                preferences_page = (
                    current_preferences_page
                    if reuse_preferences_page
                    else self._preferences_page(model.snapshot)
                )
                self._content_layout.addWidget(preferences_page, 1)
                if reuse_preferences_page:
                    self._lighting_preview_status_changed(
                        self._view_model.lighting_preview
                    )
            else:
                self._content_layout.addWidget(
                    self._overview(
                        model.snapshot,
                        model.state,
                        mapping_editing_state=mapping_editing_state,
                    ),
                    1,
                )
        else:
            self._content_layout.addWidget(self._state_page(model), 1)
        if model.snapshot is not None and self._connection_terminal.present:
            context = self._content.findChild(QWidget, "deviceContextCard")
            if context is not None:
                self._connection_terminal.cover_card(context)
        self._refresh_device_summary(model)
        self._language_manager.retranslate_widget_tree(self)

    def _header(self, model: ScreenModel) -> QWidget:
        bar = QFrame(objectName="topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(4, 0, 0, 8)
        row.setSpacing(12)

        page_title = {
            "overview": "Key Mapping",
            "sequences": "Device Key Sequences",
            "prompts": "Quick Prompts",
            "actions": "Playground",
            "lighting": "外观与反馈",
            "joystick": "Calibration",
            "diagnostics": "Diagnostics",
            "firmware": "Firmware",
            "settings": "Settings",
        }.get(self._view_model.page, "Controls")
        hero = QLabel(page_title, objectName="pageTitle")
        set_translatable_text(hero, page_title)
        row.addWidget(hero, 4)

        row.addStretch(1)
        return bar

    def _profile_selector_metric(self, snapshot: DeviceSnapshot) -> QFrame:
        draft = self._view_model.draft
        metric = QFrame(objectName="topMetric")
        layout = QVBoxLayout(metric)
        layout.setContentsMargins(15, 0, 15, 0)
        layout.setSpacing(2)

        active_profile_id = (
            draft.config.get("active_profile") if draft is not None else None
        )
        profile_switch_pending = (
            draft is not None
            and active_profile_id != snapshot.active_profile_id
        )
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(QLabel("配置方案", objectName="eyebrow"))
        if profile_switch_pending:
            heading.addWidget(
                QLabel("尚未应用到设备", objectName="profilePendingState")
            )
        heading.addStretch(1)
        layout.addLayout(heading)

        active_name = snapshot.profile_name
        if (
            draft is not None
            and isinstance(active_profile_id, int)
            and not isinstance(active_profile_id, bool)
        ):
            active_name = str(
                draft.profile(active_profile_id).get("name", active_name)
            )
        prefix = "Profile" if self._language_manager.language == ENGLISH else "配置方案"
        selector = QPushButton(
            f"{prefix}：{active_name}",
            objectName="profilePill",
        )
        selector.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        selector.setAccessibleName(
            self._language_manager.translate("选择或管理配置方案")
        )
        selector.setMenu(self._profile_menu(selector))
        actions = QVBoxLayout()
        actions.setSpacing(4)
        actions.addWidget(selector)
        save_as = QPushButton(
            "另存为新方案…",
            objectName="saveProfileAsButton",
        )
        save_as.setProperty("buttonRole", "secondary")
        save_as.setToolTip("将当前完整按键映射复制并命名为新的配置方案。")
        save_as.setEnabled(
            draft is not None
            and len(draft.profiles) < draft.max_profiles
            and not self._view_model.write_transaction.blocks_editing
        )
        save_as.clicked.connect(self._save_profile_as)
        actions.addWidget(save_as)
        layout.addLayout(actions)
        return metric

    def _settings_page(self) -> QWidget:
        scroll = QScrollArea(objectName="settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        self._language_buttons.clear()
        if self._settings_section in {"device", "about"}:
            scroll.setWidget(self._settings_detail_page())
            return self._settings_workspace(scroll, self._settings_section)
        page = QWidget(objectName="settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 0, 12, 0)
        layout.setSpacing(18)

        intro = QLabel(
            "在这里管理设备检查、固件维护和控制台显示语言。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        settings_list = QFrame(objectName="settingsList")
        list_layout = QVBoxLayout(settings_list)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(12)
        from controller_config.views.claude_status_settings import ClaudeStatusSettings
        list_layout.addWidget(ClaudeStatusSettings(self._view_model.claude_status))

        def add_navigation_row(
            *,
            title: str,
            description: str,
            button_text: str,
            object_name: str,
            page_name: str | None,
            enabled: bool,
        ) -> QPushButton:
            row = QFrame(objectName="settingsRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(16, 17, 16, 17)
            row_layout.setSpacing(18)
            copy_layout = QVBoxLayout()
            copy_layout.setSpacing(4)
            copy_layout.addWidget(QLabel(title, objectName="inspectorTitle"))
            detail = QLabel(description, objectName="muted")
            detail.setWordWrap(True)
            copy_layout.addWidget(detail)
            row_layout.addLayout(copy_layout, 1)
            button = QPushButton(button_text, objectName="secondary")
            button.setObjectName(object_name)
            button.setProperty("buttonRole", "secondary")
            button.setEnabled(enabled)
            if page_name is not None:
                button.clicked.connect(
                    lambda _checked=False, target=page_name: self._view_model.navigate(
                        target
                    )
                )
            row_layout.addWidget(button, 0, Qt.AlignVCenter)
            list_layout.addWidget(row)
            return button

        blocked_by_firmware = self._view_model.firmware_update.blocks_editing
        blocked_by_calibration = self._view_model.calibration.blocks_editing
        onboarding_button = add_navigation_row(
            title="使用引导",
            description="快速了解连接设备、配置实体控件和写入设备的完整流程。",
            button_text="重新查看引导",
            object_name="openOnboardingGuide",
            page_name=None,
            enabled=True,
        )
        onboarding_button.clicked.connect(self._show_onboarding)

        add_navigation_row(
            title="诊断",
            description="检查按键、旋钮和摇杆输入；发现摇杆异常后可进入校准。",
            button_text="打开诊断",
            object_name="openDiagnosticsSettings",
            page_name="diagnostics",
            enabled=not blocked_by_firmware and not blocked_by_calibration,
        )
        add_navigation_row(
            title="固件维护",
            description="检查或安装固件更新，也可在需要时恢复设备出厂设置。",
            button_text="打开固件维护",
            object_name="openFirmwareSettings",
            page_name="firmware",
            enabled=not blocked_by_calibration,
        )

        language_row = QFrame(objectName="settingsRow")
        language_layout = QHBoxLayout(language_row)
        language_layout.setContentsMargins(16, 17, 16, 17)
        language_layout.setSpacing(18)
        language_copy = QVBoxLayout()
        language_copy.setSpacing(4)
        language_copy.addWidget(QLabel("语言", objectName="inspectorTitle"))
        language_description = QLabel(
            "切换 BORING 控制台的界面语言，选择会自动保存。",
            objectName="muted",
        )
        language_description.setWordWrap(True)
        language_copy.addWidget(language_description)
        language_layout.addLayout(language_copy, 1)
        language_buttons = QHBoxLayout()
        language_buttons.setSpacing(8)
        self._language_buttons.clear()
        for language in SUPPORTED_LANGUAGES:
            label = _LANGUAGE_LABELS[language]
            button = QPushButton(label, objectName="settingsLanguageButton")
            button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            button.clicked.connect(
                lambda _checked=False, value=language: self._language_manager.set_language(value)
            )
            self._language_buttons[language] = button
            language_buttons.addWidget(button)
        language_layout.addLayout(language_buttons)
        list_layout.addWidget(language_row)
        self._refresh_language_buttons()

        layout.addWidget(settings_list)
        layout.addStretch(1)
        scroll.setWidget(page)
        return self._settings_workspace(scroll, self._settings_section)

    def _settings_detail_page(self) -> QWidget:
        detail = QWidget()
        box = QVBoxLayout(detail)
        box.setSpacing(14)
        snapshot = self._view_model.model.snapshot
        if self._settings_section == "about":
            box.addWidget(QLabel("BORING Console", objectName="inspectorTitle"))
            box.addWidget(QLabel(__version__, objectName="consoleVersion"))
            description = QLabel("BORING MIST 桌面控制台\n\nHarmonyOS Sans SC / 系统中文字体\nBORING 5R UI Digital Core v1.0")
            description.setWordWrap(True)
            box.addWidget(description)
        elif snapshot is None:
            box.addWidget(QLabel("未连接设备 [ NO LINK ]"))
        else:
            box.addWidget(QLabel("设备", objectName="inspectorTitle"))
            for label, value in (
                ("设备", DEVICE_DISPLAY_NAME),
                ("硬件 ID", snapshot.identity.get("hardware_id", "—")),
                ("序列号", snapshot.identity.get("serial", "—")),
                ("端口", snapshot.port_name),
                ("固件", snapshot.versions.get("firmware", "—")),
                ("信任状态", snapshot.trust.message),
            ):
                line = QLabel(f"{translate_ui_text(label)}  {value}")
                line.setWordWrap(True)
                box.addWidget(line)
            technical = QPushButton("技术详情")
            technical.clicked.connect(lambda: self._show_technical_details(snapshot))
            box.addWidget(technical)
            reset = QPushButton("恢复出厂设置…", objectName="settingsFactoryReset")
            reset.setEnabled(self._factory_reset_available(snapshot))
            reset.clicked.connect(self._confirm_factory_reset)
            box.addWidget(reset)
        box.addStretch(1)
        return detail

    def _settings_workspace(self, content: QWidget, section: str) -> QWidget:
        workspace = _SettingsWorkspace(objectName="settingsWorkspace")
        row = QBoxLayout(QBoxLayout.LeftToRight, workspace)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(20)
        navigation = V4Card(role="primary", dots=True)
        navigation.setFixedWidth(230 if self._compact_mode else 280)
        nav = QBoxLayout(QBoxLayout.TopToBottom, navigation)
        nav.setContentsMargins(20, 18, 20, 18)
        nav.addWidget(_digital_caption("SETTINGS"))
        for key, label in (("general", "通用"), ("device", "设备"), ("diagnostics", "诊断"), ("firmware", "固件维护"), ("about", "关于")):
            button = QPushButton(label, objectName="settingsGroup")
            button.setProperty("selected", key == section)
            button.setEnabled(
                not self._view_model.calibration.blocks_editing
                and (not self._view_model.firmware_update.blocks_editing or key == "firmware")
            )
            button.clicked.connect(lambda _checked=False, key=key: self._select_settings_section(key))
            nav.addWidget(button)
        nav.addStretch(1)
        focus = V4Card(role="focus")
        focus.setObjectName("settingsFocusCard")
        box = QVBoxLayout(focus)
        box.setContentsMargins(20, 18, 20, 18)
        box.addWidget(content)
        row.addWidget(navigation)
        row.addWidget(focus, 1)
        return workspace

    def _select_settings_section(self, section: str) -> None:
        if section in {"diagnostics", "firmware"}:
            self._navigate(section)
            return
        self._settings_section = section
        if self._view_model.page == "settings":
            self.render(self._view_model.model)
        else:
            self._navigate("settings")

    def _back_to_settings_button(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        back = QPushButton("‹ 返回设置", objectName="backToSettings")
        back.setProperty("buttonRole", "secondary")
        back.clicked.connect(
            lambda _checked=False: self._view_model.navigate("settings")
        )
        layout.addWidget(back)
        layout.addStretch(1)
        return bar

    def _profile_menu(self, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        menu.setObjectName("profileMenu")
        draft = self._view_model.draft
        if draft is None:
            return menu

        active_profile_id = draft.config.get("active_profile")
        profile_group = QActionGroup(menu)
        profile_group.setExclusive(True)
        for profile in draft.profiles:
            profile_id = profile.get("id")
            if not isinstance(profile_id, int) or isinstance(profile_id, bool):
                continue
            action = QAction(str(profile.get("name", "Profile")), menu)
            action.setObjectName(f"profileSelectAction_{profile_id}")
            action.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            action.setCheckable(True)
            action.setChecked(profile_id == active_profile_id)
            action.triggered.connect(
                lambda _checked=False, value=profile_id: self._activate_profile(value)
            )
            profile_group.addAction(action)
            menu.addAction(action)

        menu.addSeparator()
        create = menu.addAction("新建空白方案")
        create.setObjectName("createProfileAction")
        create.setEnabled(len(draft.profiles) < draft.max_profiles)
        create.triggered.connect(self._create_profile)
        rename = menu.addAction("重命名当前配置方案…")
        rename.setObjectName("renameProfileAction")
        rename.triggered.connect(self._rename_profile)
        delete = menu.addAction("删除当前配置方案…")
        delete.setObjectName("deleteProfileAction")
        delete.setEnabled(len(draft.profiles) > 1)
        delete.triggered.connect(self._delete_profile)

        menu.addSeparator()
        sequences = menu.addAction("管理设备按键序列…")
        sequences.setObjectName("manageDeviceKeySequencesAction")
        sequences.triggered.connect(
            lambda _checked=False: self._view_model.navigate("sequences")
        )
        changes = menu.addAction("查看本地更改…")
        changes.setObjectName("showDraftChangesAction")
        changes.setEnabled(draft.is_dirty)
        changes.triggered.connect(self._show_draft_changes)
        discard = menu.addAction("放弃全部本地更改…")
        discard.setObjectName("discardDraftAction")
        discard.setEnabled(draft.is_dirty)
        discard.triggered.connect(self._confirm_discard_draft)

        menu.addSeparator()
        import_action = menu.addAction("导入配置文件…")
        import_action.setObjectName("importConfigurationAction")
        import_action.setEnabled(not self._view_model.write_transaction.blocks_editing)
        import_action.triggered.connect(self._import_configuration)
        export_confirmed = menu.addAction("导出设备已确认配置…")
        export_confirmed.setObjectName("exportConfirmedConfigurationAction")
        export_confirmed.triggered.connect(
            lambda _checked=False: self._export_configuration("confirmed")
        )
        export_draft = menu.addAction("导出本地草稿…")
        export_draft.setObjectName("exportDraftConfigurationAction")
        export_draft.setEnabled(draft.is_dirty)
        export_draft.triggered.connect(
            lambda _checked=False: self._export_configuration("draft")
        )
        return menu

    def _prompt_library_page(self, model: ScreenModel) -> PromptLibraryEditor:
        prompt_device = self._view_model.prompt_device
        available = prompt_device.available
        message = self._view_model.prompt_library_error or prompt_device.status.message
        if prompt_device.status.technical:
            message = f"{message}：{prompt_device.status.technical}"
        editor = PromptLibraryEditor(
            self._view_model.prompt_library,
            device_storage_available=available,
            protocol_message=message,
            device_busy=prompt_device.status.is_busy,
            helper_message=prompt_device.helper_message,
            listener_status=prompt_device.listener_status,
            event_log=prompt_device.event_log,
            background_message=(
                self._background_controller.runtime_message
                if self._background_controller is not None
                else "当前运行方式不会在关闭窗口后保留后台助手"
            ),
            save_draft=self._view_model.save_prompt_draft,
            delete_draft=self._view_model.delete_prompt_draft,
            discard_draft=self._view_model.discard_prompt_draft,
            refresh_device=self._view_model.refresh_prompt_library,
            write_device=self._view_model.write_prompt_draft,
            delete_device=self._view_model.delete_prompt_from_device,
            device_preview=(
                _prompt_device_shell(model.snapshot)
                if model.snapshot is not None and self._view_model.prompt_library is not None
                else None
            ),
        )
        editor.direction_selected.connect(self._select_prompt_direction)
        return editor

    def _select_prompt_direction(self, prompt_id: int) -> None:
        # Saving can synchronously rebuild the editor before the slot click returns.
        editor = self._content.findChild(PromptLibraryEditor)
        if editor is not None and editor._selected_prompt_id() != prompt_id:
            editor._load_direction(prompt_id)

    def _actions_page(self) -> ActionsPage:
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
            active_profile = draft.config.get("active_profile")
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
                                _control_display_name(control_id)
                            )
        return ActionsPage(
            self._view_model,
            prompt_names=names,
            prompt_controls={
                prompt_id: tuple(controls)
                for prompt_id, controls in controls_by_prompt.items()
            },
        )

    def _joystick_calibration_page(self, model: ScreenModel) -> QWidget:
        snapshot = model.snapshot
        capability = _planned_tool_capability("joystick", snapshot)
        transaction = self._view_model.calibration

        scroll = QScrollArea(objectName="joystickCalibrationScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        page = QWidget(objectName="joystickCalibrationPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        heading = QVBoxLayout()
        heading.addWidget(_digital_caption("JOYSTICK CALIBRATION"))
        heading.addWidget(QLabel("诊断 / 摇杆校准", objectName="inspectorTitle"))
        intro = QLabel(
            "仅在诊断中发现回中偏移、方向误触或边缘行程不足时使用。先采集松手中立点，再让摇杆走完四周边缘。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        summary_layout.addLayout(heading, 1)
        summary_actions = QVBoxLayout()
        summary_actions.setSpacing(8)
        back = QPushButton("返回诊断", objectName="backToDiagnostics")
        back.setProperty("buttonRole", "secondary")
        back.setEnabled(not transaction.blocks_editing)
        back.clicked.connect(lambda: self._view_model.navigate("diagnostics"))
        summary_actions.addWidget(back)
        badge = QLabel(
            _planned_tool_capability_label("joystick", capability),
            objectName="statusReady" if capability is True else "statusWarn",
        )
        badge.setProperty("capabilityState", capability)
        badge.setAlignment(Qt.AlignCenter)
        summary_actions.addWidget(badge)
        summary_layout.addLayout(summary_actions)
        layout.addWidget(summary)

        current = _card()
        current_layout = QVBoxLayout(current)
        current_layout.setContentsMargins(24, 20, 24, 22)
        current_layout.setSpacing(8)
        current_layout.addWidget(QLabel("CURRENT CALIBRATION", objectName="eyebrow"))
        current_layout.addWidget(QLabel("设备当前值", objectName="inspectorTitle"))
        joystick = snapshot.config.get("joystick") if snapshot is not None else None
        if isinstance(joystick, dict):
            x_axis = joystick.get("x") if isinstance(joystick.get("x"), dict) else {}
            y_axis = joystick.get("y") if isinstance(joystick.get("y"), dict) else {}
            calibrated = "已校准" if joystick.get("calibrated") is True else "尚未校准"
            current_layout.addWidget(QLabel(calibrated, objectName="roleContext"))
            current_layout.addWidget(
                QLabel(
                    f"X  {x_axis.get('minimum', '—')} / {x_axis.get('center', '—')} / {x_axis.get('maximum', '—')}"
                    f"    死区 {x_axis.get('deadzone', '—')}\n"
                    f"Y  {y_axis.get('minimum', '—')} / {y_axis.get('center', '—')} / {y_axis.get('maximum', '—')}"
                    f"    死区 {y_axis.get('deadzone', '—')}",
                    objectName="muted",
                )
            )
        else:
            current_layout.addWidget(QLabel("等待设备读取", objectName="muted"))
        layout.addWidget(current)

        live = _card()
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(24, 20, 24, 22)
        live_layout.setSpacing(10)
        live_layout.addWidget(QLabel("LIVE SAMPLE", objectName="eyebrow"))
        live_layout.addWidget(
            QLabel(_calibration_state_title(transaction.state), objectName="inspectorTitle")
        )
        message = QLabel(transaction.message, objectName="muted")
        message.setWordWrap(True)
        live_layout.addWidget(message)
        if transaction.technical:
            technical = QLabel(transaction.technical, objectName="statusWarn")
            technical.setWordWrap(True)
            live_layout.addWidget(technical)

        for axis, value in (("X", transaction.raw_x), ("Y", transaction.raw_y)):
            bar = QProgressBar(objectName=f"calibrationRaw{axis}")
            bar.setRange(0, 4095)
            bar.setValue(value)
            bar.setFormat(f"{axis}  %v / 4095")
            live_layout.addWidget(bar)
        endpoints = QLabel(
            _calibration_endpoint_text(transaction),
            objectName="roleAgent" if transaction.travel_complete else "muted",
        )
        endpoints.setObjectName("calibrationEndpoints")
        endpoints.setWordWrap(True)
        live_layout.addWidget(endpoints)

        buttons = QHBoxLayout()
        can_start = (
            capability is True
            and model.state is AppState.READY
            and not transaction.blocks_editing
            and (self._view_model.draft is None or not self._view_model.draft.is_dirty)
        )
        if transaction.can_confirm:
            confirm = QPushButton("松手回中后保存校准", objectName="primary")
            confirm.setObjectName("confirmJoystickCalibration")
            confirm.clicked.connect(self._confirm_joystick_calibration)
            buttons.addWidget(confirm)
        if transaction.can_cancel:
            cancel = QPushButton("取消并保留原配置", objectName="secondary")
            cancel.setObjectName("cancelJoystickCalibration")
            cancel.clicked.connect(self._cancel_joystick_calibration)
            buttons.addWidget(cancel)
        if not transaction.blocks_editing:
            start = QPushButton(
                "重新开始校准"
                if transaction.state is not CalibrationState.IDLE
                else "开始摇杆校准",
                objectName="primary",
            )
            start.setObjectName("startJoystickCalibration")
            start.setEnabled(can_start)
            start.clicked.connect(self._start_joystick_calibration)
            buttons.addWidget(start)
        buttons.addStretch(1)
        live_layout.addLayout(buttons)
        layout.addWidget(live)

        boundary = QLabel(
            "取消、USB 断开或会话超时都不会修改原有校准。保存后会等待候选激活，并通过 GET_CONFIG 读回确认。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _start_joystick_calibration(self) -> None:
        choice = QMessageBox.warning(
            self,
            "开始摇杆校准",
            "请先完全松开摇杆。开始后摇杆方向、径向与按压输出会被临时抑制，直到保存、取消、断开或超时。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.start_joystick_calibration()
        except ValueError as exc:
            QMessageBox.warning(self, "无法开始校准", str(exc))

    def _confirm_joystick_calibration(self) -> None:
        choice = QMessageBox.warning(
            self,
            "保存摇杆校准",
            "请确认已松开摇杆并让它回到中心。设备会做最终回中检查，通过后把校准值写入非活动配置槽。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.confirm_joystick_calibration()
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存校准", str(exc))

    def _cancel_joystick_calibration(self) -> None:
        try:
            self._view_model.cancel_joystick_calibration()
        except ValueError as exc:
            QMessageBox.warning(self, "无法取消校准", str(exc))

    def _refresh_firmware_progress(self, scroll: QScrollArea) -> None:
        transaction = self._view_model.firmware_update
        remote = self._view_model.remote_firmware
        for prefix, status, total in (
            ("firmware", transaction, transaction.package.size if transaction.package else None),
            ("remoteFirmware", remote, remote.total_size),
        ):
            message = scroll.findChild(QLabel, prefix + "Message")
            set_translatable_text(message, status.message)
            progress = scroll.findChild(QProgressBar, prefix + "Progress")
            if progress is not None:  # Download progress exists only while downloading.
                progress.setValue(status.progress_percent)
                progress.setFormat(translate_ui_text(
                    f"{status.received_size} / {total} 字节 · %p%" if total is not None else "%p%"))

    def _firmware_page(self, model: ScreenModel, *, scroll: QScrollArea | None = None) -> QWidget:
        snapshot = model.snapshot
        capability = _planned_tool_capability("firmware", snapshot)
        transaction = self._view_model.firmware_update
        package = transaction.package

        scroll = scroll if scroll is not None else QScrollArea(objectName="firmwareMaintenanceScroll")
        scroll_position = scroll.verticalScrollBar().value()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="firmwareMaintenancePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        heading = QVBoxLayout()
        heading.addWidget(_digital_caption("FIRMWARE MAINTENANCE"))
        heading.addWidget(QLabel("固件维护", objectName="inspectorTitle"))
        intro = QLabel(
            "选择自包含维护包，先在电脑端核对镜像身份，再由用户明确确认后通过 USB CDC 更新非活动 OTA 分区。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        summary_layout.addLayout(heading, 1)
        badge = QLabel(
            _planned_tool_capability_label("firmware", capability),
            objectName="statusReady" if capability is True else "statusWarn",
        )
        badge.setProperty("capabilityState", capability)
        badge.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(badge, 0, Qt.AlignTop)
        layout.addWidget(summary)

        device_card = _card()
        device_layout = QVBoxLayout(device_card)
        device_layout.setContentsMargins(24, 20, 24, 22)
        device_layout.setSpacing(8)
        device_layout.addWidget(QLabel("CONNECTED DEVICE", objectName="eyebrow"))
        device_layout.addWidget(QLabel("设备与更新能力", objectName="inspectorTitle"))
        for text in _planned_tool_facts("firmware", snapshot):
            item = QLabel(f"• {text}", objectName="muted")
            item.setWordWrap(True)
            device_layout.addWidget(item)
        layout.addWidget(device_card)

        remote = self._view_model.remote_firmware
        online_card = _card()
        online_layout = QVBoxLayout(online_card)
        online_layout.setContentsMargins(24, 20, 24, 22)
        online_layout.setSpacing(8)
        online_layout.addWidget(QLabel("ONLINE RELEASE", objectName="eyebrow"))
        online_layout.addWidget(QLabel("在线固件发布", objectName="inspectorTitle"))
        online_message = QLabel(remote.message, objectName="muted")
        online_message.setObjectName("remoteFirmwareMessage")
        online_message.setStyleSheet("color: #9A958C;")
        online_message.setWordWrap(True)
        online_layout.addWidget(online_message)
        if remote.release is not None:
            release_details = QLabel(
                (
                    f"版本：{remote.release.version}\n"
                    f"build_id：{remote.release.build_id}\n"
                    f"下载大小：{remote.release.size} 字节\n"
                    f"渠道：{remote.release.manifest.get('channel', 'stable')}\n"
                    f"最低桌面端版本：{remote.release.manifest.get('minimum_app_version', '0.1.0')}\n"
                    f"发布时间：{remote.release.manifest.get('published_at', '未提供')}\n"
                    f"发布说明：{remote.release.manifest.get('release_notes', '未提供')}"
                ),
                objectName="muted",
            )
            release_details.setWordWrap(True)
            release_details.setTextFormat(Qt.TextFormat.PlainText)
            online_layout.addWidget(release_details)
        if remote.technical:
            online_error = QLabel(remote.technical, objectName="statusWarn")
            online_error.setWordWrap(True)
            online_layout.addWidget(online_error)
        if remote.state is RemoteFirmwareState.DOWNLOADING:
            download_progress = QProgressBar(objectName="remoteFirmwareProgress")
            download_progress.setRange(0, 100)
            download_progress.setValue(remote.progress_percent)
            download_progress.setFormat(
                f"{remote.received_size} / {remote.total_size} 字节 · %p%"
            )
            online_layout.addWidget(download_progress)

        online_buttons = QHBoxLayout()
        check_online = QPushButton("检查在线固件", objectName="secondary")
        check_online.setObjectName("checkRemoteFirmware")
        check_online.setEnabled(
            remote.state is not RemoteFirmwareState.UNCONFIGURED
            and remote.state is not RemoteFirmwareState.CHECKING
            and remote.state is not RemoteFirmwareState.DOWNLOADING
            and remote.state is not RemoteFirmwareState.DOWNLOADED
            and snapshot is not None
            and capability is True
            and model.state is AppState.READY
            and not transaction.is_busy
            and not self._view_model.calibration.blocks_editing
            and not self._view_model.write_transaction.blocks_editing
        )
        check_online.clicked.connect(self._check_remote_firmware)
        online_buttons.addWidget(check_online)
        if remote.release is not None and remote.state in {
            RemoteFirmwareState.AVAILABLE,
            RemoteFirmwareState.FAILED,
        }:
            download_online = QPushButton(
                "重新下载新固件"
                if remote.state is RemoteFirmwareState.FAILED
                else "下载新固件",
                objectName="primary",
            )
            download_online.setObjectName("downloadRemoteFirmware")
            download_online.setEnabled(
                snapshot is not None
                and capability is True
                and model.state is AppState.READY
                and not transaction.is_busy
            )
            download_online.clicked.connect(self._download_remote_firmware)
            online_buttons.addWidget(download_online)
        online_buttons.addStretch(1)
        online_layout.addLayout(online_buttons)
        online_boundary = QLabel(
            "stable 渠道只接受正式发布包；sample 渠道用于工程样品联调。检查只读取版本信息，下载并校验完成后，点击“安装新固件”并确认才会写入设备。",
            objectName="roleContext",
        )
        online_boundary.setWordWrap(True)
        online_layout.addWidget(online_boundary)
        layout.addWidget(online_card)

        package_card = _card()
        package_layout = QVBoxLayout(package_card)
        package_layout.setContentsMargins(24, 20, 24, 22)
        package_layout.setSpacing(8)
        package_layout.addWidget(QLabel("UPDATE PACKAGE", objectName="eyebrow"))
        package_layout.addWidget(QLabel("维护包检查", objectName="inspectorTitle"))
        if package is None:
            package_note = QLabel(
                "尚未导入固件维护包。可直接选择下载的 ZIP，或选择解压后的 firmware-manifest.json；导入后只执行检查，不会自动安装。",
                objectName="muted",
            )
            package_note.setWordWrap(True)
            package_layout.addWidget(package_note)
        else:
            details = (
                f"版本：{package.version}\n"
                f"build_id：{package.build_id or '未提供'}\n"
                f"目标硬件：{package.hardware_id}\n"
                f"镜像：{package.image_path.name} · {package.size} 字节\n"
                f"验证状态：{package.validation_state or '未提供'}"
                + (" · git_dirty" if package.git_dirty is True else "")
            )
            package_label = QLabel(details, objectName="muted")
            package_label.setWordWrap(True)
            package_layout.addWidget(package_label)
        choose = QPushButton("导入固件维护包…", objectName="secondary")
        choose.setObjectName("selectFirmwarePackage")
        choose.setEnabled(
            not transaction.is_busy
            and remote.state not in {
                RemoteFirmwareState.CHECKING,
                RemoteFirmwareState.DOWNLOADING,
            }
        )
        choose.clicked.connect(self._select_firmware_package)
        package_layout.addWidget(choose, 0, Qt.AlignLeft)
        layout.addWidget(package_card)

        transaction_card = _card()
        transaction_layout = QVBoxLayout(transaction_card)
        transaction_layout.setContentsMargins(24, 20, 24, 22)
        transaction_layout.setSpacing(10)
        transaction_layout.addWidget(QLabel("UPDATE TRANSACTION", objectName="eyebrow"))
        transaction_layout.addWidget(
            QLabel(_firmware_transaction_title(transaction.state), objectName="inspectorTitle")
        )
        message = QLabel(transaction.message, objectName="muted")
        message.setObjectName("firmwareMessage")
        message.setStyleSheet("color: #9A958C;")
        message.setWordWrap(True)
        transaction_layout.addWidget(message)
        if transaction.technical:
            technical = QLabel(transaction.technical, objectName="muted")
            technical.setWordWrap(True)
            transaction_layout.addWidget(technical)
        progress = QProgressBar(objectName="firmwareProgress")
        progress.setRange(0, 100)
        progress.setValue(transaction.progress_percent)
        progress.setFormat(
            f"{transaction.received_size} / {package.size} 字节 · %p%"
            if package is not None
            else "%p%"
        )
        transaction_layout.addWidget(progress)

        buttons = QHBoxLayout()
        if transaction.state is FirmwareUpdateState.NEEDS_ABORT:
            abort = QPushButton("中止设备中的旧更新", objectName="primary")
            abort.clicked.connect(self._confirm_abort_firmware_update)
            buttons.addWidget(abort)
            dismiss = QPushButton("保留设备旧事务并退出维护", objectName="secondary")
            dismiss.clicked.connect(self._dismiss_firmware_update)
            buttons.addWidget(dismiss)
        elif transaction.can_abort:
            abort = QPushButton("中止本次固件接收", objectName="secondary")
            abort.setEnabled(model.state in {AppState.READY, AppState.READ_ONLY})
            abort.clicked.connect(self._confirm_abort_firmware_update)
            buttons.addWidget(abort)
        elif transaction.state in {
            FirmwareUpdateState.PACKAGE_READY,
            FirmwareUpdateState.FAILED,
            FirmwareUpdateState.COMPLETED,
        }:
            start = QPushButton(
                "安装新固件"
                if remote.state is RemoteFirmwareState.DOWNLOADED
                else "安装所选固件",
                objectName="primary",
            )
            start.setObjectName("startFirmwareUpdate")
            start.setEnabled(
                package is not None and capability is True and model.state is AppState.READY
                and remote.state not in {RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING}
            )
            start.clicked.connect(self._confirm_firmware_update)
            buttons.addWidget(start)
        buttons.addStretch(1)
        transaction_layout.addLayout(buttons)
        layout.addWidget(transaction_card)

        recovery_card = _card()
        recovery_layout = QVBoxLayout(recovery_card)
        recovery_layout.setContentsMargins(24, 20, 24, 22)
        recovery_layout.setSpacing(8)
        recovery_layout.addWidget(QLabel("DEVICE RECOVERY", objectName="eyebrow"))
        recovery_layout.addWidget(QLabel("恢复出厂设置", objectName="inspectorTitle"))
        recovery_note = QLabel(
            "清除设备配置、提示词和全部蓝牙配对信息，然后自动重启。该操作只在 BORING 控制台提供。",
            objectName="muted",
        )
        recovery_note.setWordWrap(True)
        recovery_layout.addWidget(recovery_note)
        factory_reset = QPushButton("恢复出厂设置", objectName="factoryResetDevice")
        factory_reset.setProperty("buttonRole", "secondary")
        factory_reset.setEnabled(self._factory_reset_available(snapshot))
        factory_reset.clicked.connect(self._confirm_factory_reset)
        recovery_layout.addWidget(factory_reset, 0, Qt.AlignLeft)
        layout.addWidget(recovery_card)

        boundary = QLabel(
            "传输会写入非活动 OTA 分区；确认安装后请保持 USB 连接，等待设备重启及版本、运行分区和回滚状态读回确认。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)
        scroll.setWidget(page)
        scroll.verticalScrollBar().setValue(scroll_position)
        return scroll

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
        first = QMessageBox.warning(
            self,
            "恢复出厂设置",
            "这会清除设备配置、提示词和全部蓝牙配对信息。是否继续？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if first != QMessageBox.Yes:
            return
        second = QMessageBox.warning(
            self,
            "再次确认恢复出厂设置",
            "此操作不可撤销，完成后设备会自动重启，并需要重新配对蓝牙设备。确认执行吗？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if second != QMessageBox.Yes:
            return
        try:
            self._view_model.factory_reset_device()
        except ValueError as exc:
            QMessageBox.warning(self, "无法恢复出厂设置", str(exc))

    def _check_remote_firmware(self) -> None:
        try:
            self._view_model.check_remote_firmware()
        except ValueError as exc:
            QMessageBox.warning(self, "无法检查在线固件", str(exc))

    def _download_remote_firmware(self) -> None:
        try:
            self._view_model.download_remote_firmware()
        except ValueError as exc:
            QMessageBox.warning(self, "无法下载在线固件", str(exc))

    def _select_firmware_package(self) -> None:
        file_name, _filter = QFileDialog.getOpenFileName(
            self,
            translate_ui_text("导入 BORING 固件维护包"),
            "",
            translate_ui_text(
                "BORING 固件维护包 (*.zip *.json);;ZIP 压缩包 (*.zip);;firmware-manifest.json (*.json)"
            ),
        )
        if not file_name:
            return
        try:
            self._view_model.load_firmware_package(Path(file_name))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "固件维护包导入失败", str(exc))

    def _confirm_firmware_update(self) -> None:
        package = self._view_model.firmware_update.package
        snapshot = self._view_model.model.snapshot
        if package is None or snapshot is None:
            return
        validation = package.validation_state or "未提供"
        dirty = "是" if package.git_dirty is True else "否/未报告"
        choice = QMessageBox.warning(
            self,
            "确认开始固件维护",
            (
                f"设备序列号：{snapshot.identity.get('serial', '—')}\n"
                f"当前版本：{snapshot.versions.get('firmware', '—')}\n"
                f"目标版本：{package.version}\n"
                f"目标 build_id：{package.build_id or '未提供'}\n"
                f"软件包验证状态：{validation}\n"
                f"git_dirty：{dirty}\n\n"
                "继续后会向设备非活动 OTA 分区传输完整镜像。请保持 USB 连接；"
                "断线后控制台只按 FW_STATUS 对账续传，不会盲目从头重写。"
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.start_firmware_update()
        except ValueError as exc:
            QMessageBox.warning(self, "无法开始固件维护", str(exc))

    def _confirm_abort_firmware_update(self) -> None:
        choice = QMessageBox.warning(
            self,
            "确认中止固件接收",
            "设备将丢弃当前非活动 OTA 分区中的未完成接收事务；当前正在运行的固件不会被替换。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.abort_firmware_update()
        except ValueError as exc:
            QMessageBox.warning(self, "无法中止固件维护", str(exc))

    def _dismiss_firmware_update(self) -> None:
        try:
            self._view_model.dismiss_firmware_update()
            self._view_model.navigate("overview")
        except ValueError as exc:
            QMessageBox.warning(self, "无法退出固件维护", str(exc))

    def _state_page(self, model: ScreenModel) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        self._connection_terminal.setMinimumWidth(260)
        self._connection_terminal.setMaximumWidth(300)
        layout.addWidget(self._connection_terminal, 0, Qt.AlignTop)
        self._connection_terminal.show()

        card = QFrame(objectName="card")
        card.setMinimumWidth(560)
        card.setMaximumWidth(720)
        box = QVBoxLayout(card)
        box.setContentsMargins(38, 34, 38, 36)
        box.setSpacing(12)
        marker = _digital_caption(_state_marker(model.state))
        busy = model.state in {AppState.SCANNING, AppState.CONNECTING}
        title = QLabel("正在连接你的 BORING" if busy else model.message or "正在准备 BORING 控制台")
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        if len(title.text()) > 10:
            title.setMinimumHeight(90)
        detail = QLabel("连接进度显示在左侧，完成身份认证后自动加载设备配置。" if busy else
                        model.technical_message or _state_help(model.state), objectName="muted")
        detail.setWordWrap(True)
        box.addWidget(marker)
        box.addWidget(title)
        box.addWidget(detail)

        if model.state is AppState.MULTIPLE_DEVICES:
            selector = QComboBox()
            for candidate in model.candidates:
                selector.addItem(candidate.display_name, candidate.port_name)
            connect_button = QPushButton("读取所选设备", objectName="primary")
            connect_button.clicked.connect(lambda: self._view_model.connect_candidate(selector.currentData()))
            box.addSpacing(10)
            box.addWidget(selector, 0, Qt.AlignLeft)
            box.addWidget(connect_button, 0, Qt.AlignLeft)
        elif model.state not in {AppState.SCANNING, AppState.CONNECTING}:
            retry = QPushButton("连接 USB 后重新扫描" if model.state is AppState.FIRMWARE_UPDATE_REQUIRED else "重新扫描", objectName="primary")
            retry.clicked.connect(self._view_model.refresh)
            box.addSpacing(10)
            box.addWidget(retry, 0, Qt.AlignLeft)

        layout.addWidget(card, 1, Qt.AlignTop)
        layout.addStretch(1)
        return wrapper

    def _overview(
        self,
        snapshot: DeviceSnapshot,
        state: AppState,
        *,
        mapping_editing_state: _MappingEditingState | None = None,
    ) -> QWidget:
        scroll = QScrollArea(objectName="overviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(6)
        draft = self._view_model.draft
        mode = str(snapshot.status.get("operating_mode", "unknown"))

        controls_card = QFrame(objectName="deviceWorkspace")
        controls_layout = QHBoxLayout(controls_card)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(16)

        left_rail = QWidget(objectName="deviceContextRail")
        left_rail.setMinimumWidth(260)
        left_rail.setMaximumWidth(300)
        left_layout = QVBoxLayout(left_rail)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(20)
        context = self._device_context_card(snapshot, state)
        left_layout.addWidget(context)
        left_layout.addWidget(self._home_usage_card())
        left_layout.addStretch(1)
        controls_layout.addWidget(left_rail, 0)

        mappings = snapshot.mappings
        if draft is not None:
            active_profile_id = draft.config.get("active_profile")
            if isinstance(active_profile_id, int) and not isinstance(active_profile_id, bool):
                mappings = _profile_mappings(draft.profile(active_profile_id))

        device_shell = _device_shell_widget(
            snapshot,
            mappings=mappings,
            on_control=self._select_physical_control if draft is not None else None,
            selected_control_id=self._selected_control_id,
        )
        stage = QFrame(objectName="deviceStage")
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 4, 0, 0)
        stage_layout.setSpacing(9)
        stage_layout.addStretch(1)
        stage_layout.addWidget(device_shell, 0, Qt.AlignHCenter | Qt.AlignVCenter)
        stage_name = QLabel(DEVICE_DISPLAY_NAME, objectName="deviceStageName")
        stage_name.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        stage_name.setAlignment(Qt.AlignCenter)
        stage_layout.addWidget(stage_name)
        stage_layout.addStretch(1)
        controls_layout.addWidget(stage, 1)

        editor = self._mapping_editor(snapshot, mapping_editing_state)
        if editor is None:
            editor = self._selection_inspector(snapshot)
        side_rail = self._mapping_side_rail(snapshot, state, editor)
        layout.addWidget(_ResponsiveMappingWorkspace(controls_card, side_rail), 1)

        if self._view_model.write_transaction.state is not ConfigTransactionState.IDLE:
            current_generation = snapshot.config_result.get("generation")
            base_changed = (
                draft is not None and current_generation != draft.base_generation
            )
            layout.addWidget(
                self._write_transaction_card(
                    snapshot,
                    bool(base_changed),
                    show_prepare=False,
                )
            )

        scroll.setWidget(page)
        return scroll

    def _mapping_side_rail(
        self,
        snapshot: DeviceSnapshot,
        state: AppState,
        editor: QWidget,
    ) -> QWidget:
        rail = QWidget(objectName="mappingInspectorRail")
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(20)
        rail_layout.addWidget(editor, 1)
        rail_layout.addWidget(self._sync_summary_card(snapshot, state), 0)
        return rail

    def _sync_summary_card(
        self,
        snapshot: DeviceSnapshot,
        state: AppState,
    ) -> QFrame:
        draft = self._view_model.draft
        bottom = _InstrumentCard(objectName="syncSummaryCard")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(18, 14, 18, 14)
        bottom_layout.setSpacing(8)
        bottom_layout.addWidget(_digital_caption("SYNC"))
        status_row = QVBoxLayout()
        status_row.setSpacing(8)
        sync = QLabel(snapshot.config_status_label)
        sync.setStyleSheet("font-weight: 800;")
        status_row.addWidget(sync)
        profile_switch_pending = (
            draft is not None
            and draft.config.get("active_profile") != snapshot.active_profile_id
        )
        if profile_switch_pending:
            draft_text = "配置方案尚未应用到设备"
        elif draft is not None and draft.is_dirty:
            draft_text = "未保存变更"
        else:
            draft_text = "没有本地变更"
        status_row.addWidget(QLabel(draft_text, objectName="bottomMuted"))
        status_row.addStretch(1)
        bottom_layout.addLayout(status_row)
        if draft is not None:
            change_count = len(draft.changes)
            count = QLabel(f"{change_count}  处未写入改动" if change_count else "已同步 [ SYNCED ]", objectName="syncChangeCount")
            count.setProperty("dirty", draft.is_dirty)
            bottom_layout.addWidget(count)
        actions_row = QVBoxLayout()
        actions_row.setSpacing(8)
        actions_row.addStretch(1)
        if draft is not None:
            current_generation = snapshot.config_result.get("generation")
            base_changed = current_generation != draft.base_generation
            profile_switch_only = draft.only_active_profile_changed
            save_to_device = QPushButton(
                "切换并应用…" if profile_switch_only else "保存到设备…",
                objectName="saveConfigurationToDevice",
            )
            save_to_device.setProperty("buttonRole", "primary")
            save_to_device.setToolTip(
                "将所选配置方案设为设备当前方案；验证通过后仍需最终确认。"
                if profile_switch_only
                else "验证当前配置方案及其他本地修改；通过后仍需再次确认才会写入设备。"
            )
            save_to_device.setEnabled(
                draft.is_dirty
                and not base_changed
                and not self._view_model.validate_draft()
                and snapshot.compatibility.get("write") is True
                and self._view_model.write_transaction.state
                in {
                    ConfigTransactionState.IDLE,
                    ConfigTransactionState.ACTIVE,
                    ConfigTransactionState.FAILED,
                }
            )
            save_to_device.clicked.connect(self._prepare_device_write)
            actions_row.addWidget(save_to_device)
        technical = QPushButton("技术详情", objectName="ghostOnDark")
        technical.clicked.connect(lambda: self._show_technical_details(snapshot))
        actions_row.addWidget(technical)
        bottom_layout.addLayout(actions_row)
        return bottom

    def _device_context_card(
        self,
        snapshot: DeviceSnapshot,
        state: AppState,
    ) -> QFrame:
        connected = state in {AppState.READY, AppState.READ_ONLY}
        authenticated = connected and snapshot.trust.is_authenticated
        card = _InstrumentCard(objectName="deviceContextCard")
        card.setProperty(
            "connectionState",
            "ready" if connected else "disconnected",
        )
        card.setMinimumHeight(330)
        box = QVBoxLayout(card)
        box.setContentsMargins(18, 12, 18, 12)
        box.setSpacing(6)

        heading = QHBoxLayout()
        heading.addWidget(_digital_caption("DEVICE"))
        heading.addStretch(1)
        live = QLabel("[ LIVE ]" if connected else "[ OFFLINE ]", objectName="devicePanelLive")
        live.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        heading.addWidget(live)
        box.addLayout(heading)

        name = Boring5RLabel(
            DEVICE_DISPLAY_NAME,
            scale=1.35,
            color="#f2f1e8",
            objectName="devicePanelName",
        )
        name.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        box.addWidget(name)
        subtitle = QLabel(
            "BORING 桌面控制台 · 已连接" if connected else "连接已断开 · 断开前的只读快照",
            objectName="devicePanelSubtitle",
        )
        subtitle.setWordWrap(True)
        box.addWidget(subtitle)

        def add_status_row(
            label: str,
            value: str,
            *,
            ready: bool = False,
            digital: bool = False,
        ) -> None:
            row = QFrame(objectName="deviceStatusRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 4, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(QLabel(label, objectName="devicePanelKey"))
            value_label = (
                Boring5RLabel(
                    value,
                    scale=0.8,
                    color="#f2f1e8",
                    objectName="devicePanelValue",
                )
                if digital
                else QLabel(value, objectName="devicePanelValue")
            )
            value_label.setProperty(SKIP_TRANSLATION_PROPERTY, label != "TRUST")
            if not digital:
                value_label.setWordWrap(True)
                value_label.setMinimumWidth(0)
                value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            if label == "TRUST":
                set_translatable_text(value_label, value)
            if ready:
                value_label.setProperty("state", "ready")
            if label == "TRUST":
                value_label.setObjectName("statusReady" if ready else "statusWarn")
                value_label.setProperty("compactStatus", True)
            row_layout.addWidget(value_label, 1)
            box.addWidget(row)

        add_status_row(
            "TRUST",
            "已认证 [ VERIFIED ]" if authenticated else "开发设备，未认证 [ DEV ]" if connected else "已断开 [ NO LINK ]",
            ready=authenticated,
        )
        add_status_row(
            "LINK",
            "BLUETOOTH" if connected and snapshot.connection_kind == "bluetooth" else "USB-C" if connected else "—",
        )
        add_status_row(
            "MODE",
            _mode_label(str(snapshot.status.get("operating_mode", "unknown"))),
            digital=True,
        )

        codex_micro = snapshot.status.get("codex_micro")
        active_slot = codex_micro.get("active_slot") if isinstance(codex_micro, dict) else None
        if isinstance(active_slot, int) and not isinstance(active_slot, bool):
            ble = QPushButton(f"BLE   {active_slot} / 3   ›", objectName="bleSlotsDisclosure")
            ble.setProperty("buttonRole", "secondary")
            ble.clicked.connect(lambda: self._show_ble_slots(snapshot, state))
            box.addWidget(ble)

        if self._view_model.draft is not None:
            box.addSpacing(2)
            box.addWidget(self._profile_selector_metric(snapshot))
        return card

    def _show_ble_slots(self, snapshot: DeviceSnapshot, state: AppState) -> None:
        from PySide6.QtWidgets import QDialog
        slots = self._ble_slots_card(snapshot, state)
        if slots is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("蓝牙设备槽位")
        box = QVBoxLayout(dialog)
        box.addWidget(slots)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        self._language_manager.retranslate_widget_tree(dialog)
        dialog.exec()

    def _home_usage_card(self) -> QWidget:
        card = V4Card(role="widget")
        card.setObjectName("codexHomeCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 18, 20, 18)
        box.addWidget(_digital_caption("CODEX"))
        rings = UsageRings()
        rings.setObjectName("homeUsageRings")
        box.addWidget(rings, 0, Qt.AlignCenter)
        detail = QLabel(objectName="homeUsageDetail")
        detail.setWordWrap(True)
        box.addWidget(detail)
        refresh = QPushButton("更新额度信息", objectName="homeUsageRefresh")
        refresh.setProperty("buttonRole", "secondary")
        refresh.setEnabled(self._background_controller is not None)
        if self._background_controller is not None:
            refresh.clicked.connect(self._background_controller.refresh_usage)
            snapshot = self._background_controller.codex_usage_snapshot
        else:
            snapshot = CodexUsageSnapshot.unavailable()
        box.addWidget(refresh)
        self._populate_home_usage(card, snapshot)
        return card

    def _update_home_usage(self, snapshot: CodexUsageSnapshot) -> None:
        card = self._content.findChild(QWidget, "codexHomeCard")
        if card is not None:
            self._populate_home_usage(card, snapshot)

    @staticmethod
    def _populate_home_usage(card: QWidget, snapshot: CodexUsageSnapshot) -> None:
        windows = {}
        for bucket in snapshot.buckets:
            if bucket.limit_id == "codex":
                windows = {w.window_minutes: w for w in (bucket.primary, bucket.secondary) if w is not None}
                break
        seven, five = windows.get(10080), windows.get(300)
        card.findChild(UsageRings, "homeUsageRings").set_usage(
            seven_day=seven.remaining_percent if seven else None,
            five_hour=five.remaining_percent if five else None,
        )
        text = "7D 外环 · 5H 内环 · 剩余额度"
        if snapshot.status is not CodexUsageStatus.AVAILABLE:
            text += "\n" + snapshot.message
        elif snapshot.updated_at is not None:
            from datetime import datetime
            text += "\n更新于 " + datetime.fromtimestamp(snapshot.updated_at).strftime("%m-%d %H:%M")
        set_translatable_text(card.findChild(QLabel, "homeUsageDetail"), text)

    def _ble_slots_card(
        self, snapshot: DeviceSnapshot, state: AppState
    ) -> QWidget | None:
        features = snapshot.capabilities.get("features")
        codex_micro = snapshot.status.get("codex_micro")
        if (
            not isinstance(features, dict)
            or features.get("ble_host_slots") != 3
            or not isinstance(codex_micro, dict)
        ):
            return None
        slots = codex_micro.get("slots")
        active_slot = codex_micro.get("active_slot")
        if not isinstance(slots, list) or len(slots) != 3:
            return None

        card = QFrame(objectName="bleSlotsCard")
        card.setProperty("bleSlotsCard", True)
        # Keep the physical pairing gestures readable instead of compressing the
        # three shortcut labels into hairlines on the 900 px reference viewport.
        card.setMinimumHeight(248)
        card.setMaximumHeight(258)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(6)

        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 2)
        heading = QLabel("蓝牙设备槽位", objectName="eyebrow")
        heading_row.addWidget(heading)
        heading_row.addStretch(1)
        card_layout.addLayout(heading_row)

        writable = state is AppState.READY
        for slot_number, item in enumerate(slots, start=1):
            if not isinstance(item, dict):
                continue
            connected = item.get("connected") is True
            paired = item.get("paired") is True
            selected = active_slot == slot_number
            state_text = "已连接" if connected else "已配对" if paired else "等待配对"
            marker = "当前" if selected else "槽位"
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(QLabel(f"{marker} {slot_number} · {state_text}", objectName="eyebrow"), 1)
            select_button = QPushButton("选", objectName="secondary")
            select_button.setFixedSize(34, 28)
            select_button.setProperty("bleSlot", slot_number)
            select_button.setProperty("bleAction", "select")
            select_button.setEnabled(writable and not selected)
            select_button.clicked.connect(
                lambda _checked=False, slot=slot_number: self._view_model.select_ble_slot(slot)
            )
            clear_button = QPushButton("清", objectName="secondary")
            clear_button.setFixedSize(34, 28)
            clear_button.setProperty("bleSlot", slot_number)
            clear_button.setProperty("bleAction", "clear")
            clear_button.setEnabled(writable and paired)
            clear_button.clicked.connect(
                lambda _checked=False, slot=slot_number: self._confirm_clear_ble_slot(slot)
            )
            row.addWidget(select_button)
            row.addWidget(clear_button)
            card_layout.addLayout(row)

        shortcut_row = QVBoxLayout()
        shortcut_row.setContentsMargins(0, 3, 0, 0)
        shortcut_row.setSpacing(4)
        shortcut_heading = QLabel("实体快捷操作", objectName="bleShortcutHeading")
        shortcut_row.addWidget(shortcut_heading)
        for target_key, slot_number in ((8, 1), (9, 2), (10, 3)):
            shortcut = QLabel(
                f"KEY 3 + KEY {target_key} → 槽位 {slot_number}",
                objectName="bleShortcutChip",
            )
            shortcut.setProperty("bleSlot", slot_number)
            shortcut_row.addWidget(shortcut)
        shortcut_note = QLabel(
            "短按组合键切换槽位；持续按住约 3 秒，清除该槽位并进入配对。",
            objectName="bleShortcutNote",
        )
        shortcut_note.setWordWrap(True)
        shortcut_row.addWidget(shortcut_note)
        card_layout.addLayout(shortcut_row)
        return card

    def _confirm_clear_ble_slot(self, slot: int) -> None:
        choice = QMessageBox.warning(
            self,
            f"清除蓝牙槽位 {slot}",
            "将忘记该设备并立即进入重新配对。是否继续？",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice == QMessageBox.Yes:
            self._view_model.clear_ble_slot(slot)

    def _show_technical_details(self, snapshot: DeviceSnapshot) -> None:
        QMessageBox.information(
            self,
            "设备技术详情",
            _technical_details_text(snapshot),
        )

    def _selection_inspector(self, snapshot: DeviceSnapshot) -> QWidget:
        card = V4Card(role="secondary")
        card.setObjectName("selectionInspectorCard")
        card.setMinimumWidth(300)
        card.setMaximumWidth(410)
        box = QVBoxLayout(card)
        box.setContentsMargins(22, 19, 22, 22)
        box.setSpacing(12)
        box.addWidget(_digital_caption("SELECTED OBJECT"))
        box.addWidget(QLabel("选择实体控件", objectName="inspectorTitle"))
        guidance = QLabel("点击设备画布中的控件，查看它在当前模式下的真实作用并编辑本地草稿。", objectName="muted")
        guidance.setWordWrap(True)
        box.addWidget(guidance)
        box.addSpacing(6)

        human = _semantic_role_card(
            "HUMAN INPUT",
            "白色键帽与金属控制件 · 发出动作",
            "roleHuman",
        )
        agent = _semantic_role_card(
            "AGENT STATUS",
            "六枚透明键帽 · 显示外部 Agent 状态",
            "roleAgent",
        )
        context = _semantic_role_card(
            "CONTEXT",
            "圆屏 · 模式、任务与下一步",
            "roleContext",
        )
        box.addWidget(human)
        box.addWidget(agent)
        box.addWidget(context)
        box.addSpacing(4)

        mode = str(snapshot.status.get("operating_mode", "unknown"))
        mode_note = QLabel(_mode_explanation(mode), objectName="muted")
        mode_note.setWordWrap(True)
        box.addWidget(mode_note)
        box.addStretch(1)
        return card

    def _mapping_editor(
        self,
        snapshot: DeviceSnapshot,
        editing_state: _MappingEditingState | None = None,
    ) -> QWidget | None:
        draft = self._view_model.draft
        if draft is None or self._selected_control_id not in draft.controls:
            return None
        profile_id = draft.config.get("active_profile")
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return None
        mapping = draft.mapping(profile_id, self._selected_control_id)
        confirmed_mapping = snapshot.mappings.get(self._selected_control_id)
        platform = str(snapshot.status.get("platform", ""))
        current_generation = snapshot.config_result.get("generation")
        base_changed = current_generation != draft.base_generation
        write_state = self._view_model.write_transaction.state

        card = V4Card(role="focus")
        card.setObjectName("mappingEditorCard")
        card.setProperty("controlId", self._selected_control_id)
        card.setMinimumWidth(300)
        card.setMaximumWidth(300)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 18)
        card_layout.setSpacing(10)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel("控件设置", objectName="eyebrow"))
        selected_name = QLabel(_control_display_name(self._selected_control_id))
        selected_name.setObjectName("inspectorTitle")
        selected_name.setWordWrap(True)
        title_box.addWidget(selected_name)
        header.addLayout(title_box, 1)
        close = QPushButton("关闭", objectName="secondary")
        close.setAccessibleName("关闭控件设置")
        close.clicked.connect(self._close_control_editor)
        header.addWidget(close, 0, Qt.AlignTop)
        card_layout.addLayout(header)

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
        body_scroll.setWidget(editor_body)
        card_layout.addWidget(body_scroll, 1)

        role_marker, role_copy = _control_role_copy(self._selected_control_id)
        editor_layout.addWidget(
            _semantic_role_card(
                role_marker,
                role_copy,
                _control_role_style(self._selected_control_id),
                compact=True,
            )
        )
        if write_state is not ConfigTransactionState.IDLE:
            editor_layout.addWidget(
                self._write_transaction_card(
                    snapshot,
                    base_changed,
                    show_prepare=False,
                )
            )

        manage_sequences = QPushButton("管理设备按键序列")
        manage_sequences.setObjectName("manageDeviceKeySequences")
        manage_sequences.setProperty("buttonRole", "secondary")
        manage_sequences.setToolTip(
            "按键序列保存在设备中，可在 BORING 控制台退出后继续执行。"
        )
        manage_sequences.clicked.connect(
            lambda _checked=False: QTimer.singleShot(
                0, lambda: self._view_model.navigate("sequences")
            )
        )

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        related_controls = _related_controls(self._selected_control_id, draft.controls)
        if len(related_controls) > 1:
            trigger_selector = QComboBox(objectName="triggerSelector")
            for control_id in related_controls:
                trigger_selector.addItem(_control_display_name(control_id), control_id)
            trigger_selector.setCurrentIndex(trigger_selector.findData(self._selected_control_id))
            trigger_selector.currentIndexChanged.connect(
                lambda _index, selector=trigger_selector: self._select_control(selector.currentData())
            )
            form.addRow("触发方式", trigger_selector)

        current_action = (
            confirmed_mapping.get("action")
            if isinstance(confirmed_mapping, dict)
            else None
        )
        editing_action = mapping.get("action") if mapping else current_action
        actual_action = QLabel(
            describe_action(current_action, platform=platform),
            objectName="actualActionValue",
        )
        actual_action.setMinimumWidth(0)
        actual_action.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        actual_action.setStyleSheet("font-size: 17px; font-weight: 800;")
        actual_action.setWordWrap(True)
        form.addRow("设备当前", actual_action)

        short_name_editor = QLineEdit(
            str(mapping.get("short_name", "未映射")) if mapping else "未映射"
        )
        short_name_editor.setObjectName("mappingShortNameEditor")
        short_name_editor.setMaxLength(draft.editor_rules.mapping_short_name_max_length)
        short_name_editor.setPlaceholderText("例如：新建任务")
        short_name_editor.setToolTip(
            f"用于辅助识别这个控件，最多 {draft.editor_rules.mapping_short_name_max_length} 个字符"
        )

        preset_selector = QComboBox()
        current_action_key = _canonical_action(editing_action)
        if isinstance(editing_action, dict):
            preset_selector.addItem("当前控件动作", editing_action)
        else:
            preset_selector.addItem("选择模板…", None)
        for preset in draft.action_presets:
            action = preset["action"]
            if _canonical_action(action) == current_action_key:
                continue
            preset_selector.addItem(
                f"{describe_action(action, platform=platform)} · {preset['short_name']}",
                action,
            )

        definitions = tuple(
            definition
            for definition in self._view_model.action_definitions
            if definition.action_type != "macro" or draft.macros
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
        selected_preset = preset_selector.currentData()
        initial_action = (
            selected_preset
            if isinstance(selected_preset, dict)
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
            platform=platform,
            reference_choices=reference_choices,
        )
        if (
            editing_state is not None
            and editing_state.control_id == self._selected_control_id
        ):
            short_name_editor.setText(editing_state.short_name)
            action_editor.set_action(editing_state.action)
        preset_selector.currentIndexChanged.connect(
            lambda _index, selector=preset_selector: action_editor.set_action(
                selector.currentData() if isinstance(selector.currentData(), dict) else {}
            )
        )
        form.addRow(action_editor)
        form.addRow("按键名称（可选）", short_name_editor)
        form.addRow("快捷模板", preset_selector)
        editor_layout.addLayout(form)

        def save_local_draft() -> bool:
            try:
                action = action_editor.action()
            except ValueError as exc:
                QMessageBox.warning(self, "动作参数有误", str(exc))
                return False
            self._suppress_mapping_edit_restore = True
            try:
                self._view_model.set_mapping(
                    profile_id,
                    str(self._selected_control_id),
                    short_name_editor.text(),
                    action,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "当前不能修改草稿", str(exc))
                return False
            else:
                self._pending_mapping_editing_state = None
                return True
            finally:
                self._suppress_mapping_edit_restore = False

        editing_enabled = not self._view_model.write_transaction.blocks_editing
        save_actions = QVBoxLayout()
        apply_to_device = QPushButton(
            "应用到设备…", objectName="applyMappingToDevice"
        )
        apply_to_device.setProperty("buttonRole", "primary")
        apply_to_device.setToolTip(
            "保存当前修改并交给设备验证；验证通过后仍需由你最终确认。"
        )
        apply_to_device.setEnabled(
            editing_enabled
            and not base_changed
            and snapshot.compatibility.get("write") is True
            and write_state
            in {
                ConfigTransactionState.IDLE,
                ConfigTransactionState.ACTIVE,
                ConfigTransactionState.FAILED,
            }
        )

        def save_and_prepare() -> None:
            if save_local_draft():
                self._prepare_device_write()

        apply_to_device.clicked.connect(save_and_prepare)
        save_actions.addWidget(apply_to_device)
        save = QPushButton("仅保存草稿", objectName="saveMappingDraft")
        save.setProperty("buttonRole", "secondary")
        save.clicked.connect(save_local_draft)
        save.setEnabled(editing_enabled)
        save_actions.addWidget(save)
        discard = QPushButton("丢弃本地修改", objectName="secondary")
        discard.setEnabled(draft.is_dirty)

        def discard_local_draft() -> None:
            self._suppress_mapping_edit_restore = True
            self._pending_mapping_editing_state = None
            try:
                self._view_model.discard_draft()
            finally:
                self._suppress_mapping_edit_restore = False

        discard.clicked.connect(discard_local_draft)
        editor_layout.addWidget(discard)
        editor_layout.addWidget(manage_sequences)
        card_layout.addLayout(save_actions)
        return card

    def _macro_page(self, snapshot: DeviceSnapshot) -> QWidget:
        draft = self._view_model.draft
        if draft is None:
            return self._state_page(self._view_model.model)
        scroll = QScrollArea()
        scroll.setObjectName("deviceKeySequencesPage")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(22, 18, 22, 18)
        title = QVBoxLayout()
        title.addWidget(_digital_caption("DEVICE KEY SEQUENCES"))
        title.addWidget(QLabel("按键序列步骤编辑", objectName="inspectorTitle"))
        note = QLabel(
            "保存在设备内，无需后台助手；支持按下、释放、敲击、ASCII 文本和有界延时。",
            objectName="muted",
        )
        note.setWordWrap(True)
        title.addWidget(note)
        summary_layout.addLayout(title, 1)
        total_limit = draft.limits["all_macro_bytes"]
        capacity = QLabel(
            f"{len(draft.macros)}/{draft.editor_rules.max_macros} 条按键序列\n"
            f"{draft.all_macro_encoded_size}/{total_limit} 字节",
            objectName="statusReady" if draft.all_macro_encoded_size <= total_limit else "statusError",
        )
        capacity.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(capacity)
        layout.addWidget(summary)

        macros = draft.macros
        macro_ids = [macro.get("id") for macro in macros if isinstance(macro.get("id"), int)]
        if self._selected_macro_id not in macro_ids:
            self._selected_macro_id = macro_ids[0] if macro_ids else None
        editing_enabled = not self._view_model.write_transaction.blocks_editing

        card = _card()
        editor_layout = QVBoxLayout(card)
        editor_layout.setContentsMargins(24, 20, 24, 24)
        editor_layout.setSpacing(12)
        toolbar = QHBoxLayout()
        selector = QComboBox(objectName="macroSelector")
        selector.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        for macro in macros:
            selector.addItem(str(macro.get("name", "按键序列")), macro.get("id"))
        if self._selected_macro_id is not None:
            selector.setCurrentIndex(selector.findData(self._selected_macro_id))
        selector.currentIndexChanged.connect(
            lambda _index, field=selector: self._select_macro(field.currentData())
        )
        selector.setEnabled(bool(macros))
        toolbar.addWidget(selector, 1)
        create = QPushButton("新建按键序列", objectName="secondary")
        create.setEnabled(editing_enabled and len(macros) < draft.editor_rules.max_macros)
        create.clicked.connect(self._create_macro)
        toolbar.addWidget(create)
        delete = QPushButton("从草稿删除", objectName="secondary")
        delete.setEnabled(editing_enabled and self._selected_macro_id is not None)
        delete.clicked.connect(self._delete_macro)
        toolbar.addWidget(delete)
        editor_layout.addLayout(toolbar)

        if self._selected_macro_id is None:
            empty = QLabel(
                "当前配置没有按键序列。点击“新建按键序列”创建第一条。",
                objectName="muted",
            )
            empty.setWordWrap(True)
            editor_layout.addWidget(empty)
        else:
            macro = draft.macro(self._selected_macro_id)
            form = QFormLayout()
            name = QLineEdit(str(macro.get("name", "")), objectName="macroNameEditor")
            name.setMaxLength(draft.editor_rules.macro_name_max_length)
            name.setEnabled(editing_enabled)
            form.addRow("按键序列名称", name)
            editor_layout.addLayout(form)
            steps = macro.get("steps") if isinstance(macro.get("steps"), list) else []
            macro_editor = MacroEditor(
                steps,
                self._macro_key_choices(),
                byte_limit=draft.limits["macro_bytes"],
                rules=draft.editor_rules,
            )
            macro_editor.setEnabled(editing_enabled)
            editor_layout.addWidget(macro_editor)
            save = QPushButton("保存到本地草稿", objectName="primary")
            save.setEnabled(editing_enabled)
            save.clicked.connect(
                lambda: self._save_macro(
                    int(self._selected_macro_id),
                    name.text(),
                    macro_editor.steps(),
                )
            )
            editor_layout.addWidget(save)
        layout.addWidget(card)
        current_generation = snapshot.config_result.get("generation")
        layout.addWidget(
            self._write_transaction_card(snapshot, current_generation != draft.base_generation)
        )
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    def _preferences_page(self, snapshot: DeviceSnapshot) -> QWidget:
        draft = self._view_model.draft
        if draft is None:
            return self._state_page(self._view_model.model)
        scroll = QScrollArea(objectName="preferencesPage")
        scroll.setProperty("draftIdentity", id(draft))
        scroll.setProperty(
            "editingBlocked", self._view_model.write_transaction.blocks_editing
        )
        scroll.setProperty(
            "previewSupported", self._view_model.lighting_preview.supported
        )
        scroll.setProperty("modelState", self._view_model.model.state.value)
        scroll.setProperty(
            "transactionState", self._view_model.write_transaction.state.value
        )
        scroll.setProperty("devicePort", snapshot.port_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        preview = self._view_model.lighting_preview
        editor_lighting = copy.deepcopy(draft.config["lighting"])
        if preview.candidate is not None:
            editor_lighting.update(copy.deepcopy(preview.candidate))
        editor = PreferencesEditor(
            lighting=editor_lighting,
            screen_icon_draft=self._screen_icon_draft_for(snapshot),
            haptic=draft.config["haptic"],
            display=draft.config["display"],
            features=draft.features,
            under_key_control_ids=tuple(
                control_id
                for control_id in draft.controls
                if control_id.startswith("key.")
            ),
            agent_status_control_ids=(
                MATRIX12_AGENT_STATUS_KEYS
                if draft.hardware_id in MATRIX12_HARDWARE_IDS
                and draft.features.get("agent_status_under_key_count")
                == len(MATRIX12_AGENT_STATUS_KEYS)
                else frozenset()
            ),
            rules=draft.editor_rules,
            lighting_levels=(
                POWER_V2_LIGHTING_LEVELS
                if draft.hardware_id == "WMP-S3-MATRIX12-POWER-V2"
                else None
            ),
            haptic_levels=(
                POWER_V2_HAPTIC_LEVELS
                if draft.hardware_id == "WMP-S3-MATRIX12-POWER-V2"
                else None
            ),
            display_brightness_configurable=draft.hardware_id not in MATRIX12_HARDWARE_IDS,
        )
        editing_enabled = not self._view_model.write_transaction.blocks_editing
        for settings_card in (editor._light_card, editor._haptic_card, editor._screen_card):
            if settings_card is not None:
                settings_card.setEnabled(editing_enabled)
        icon_editor = editor.findChild(ScreenIconEditor)
        if icon_editor is not None:
            icon_editor.upload_requested.connect(self._upload_screen_icon)
            icon_editor.reset_requested.connect(self._reset_screen_icon)
            icon_editor.refresh_requested.connect(self._view_model.refresh_screen_icon)
            icon_editor.cancel_requested.connect(self._view_model.screen_icon.cancel)
            icon_editor.bind_transfer(self._view_model.screen_icon)
            if (self._view_model.screen_icon.supported and not self._view_model.screen_icon.busy
                    and not self._view_model.screen_glyphs.supported):
                QTimer.singleShot(0, self._view_model.refresh_screen_icon)
            glyph_editor = ScreenGlyphEditor(self._screen_glyph_drafts)
            icon_editor.parentWidget().layout().addWidget(glyph_editor)
            glyph_editor.selection_requested.connect(self._view_model.refresh_screen_glyphs)
            glyph_editor.refresh_requested.connect(self._view_model.refresh_screen_glyphs)
            glyph_editor.write_requested.connect(self._write_screen_glyph)
            glyph_editor.reset_requested.connect(lambda: self._write_screen_glyph(None))
            glyph_editor.bind_transfer(self._view_model.screen_glyphs)
            if self._view_model.screen_glyphs.supported:
                QTimer.singleShot(0, self._view_model.refresh_screen_glyphs)
        preview_controls = QWidget(objectName="lightingPreviewControls")
        preview_row = QVBoxLayout(preview_controls)
        preview_row.setContentsMargins(0, 10, 0, 0)
        preview_toggle = QCheckBox(
            "在设备上实时预览",
            objectName="lightingPreviewEnabled",
        )
        preview_toggle.setChecked(preview.requested)
        preview_toggle.setToolTip("预览不等于保存；写入设备仍需明确确认。")
        preview_toggle.setEnabled(
            preview.supported
            and self._view_model.model.state is AppState.READY
            and editing_enabled
        )
        preview_status = QLabel(
            preview.message,
            objectName="lightingPreviewStatus",
        )
        preview_status.setWordWrap(True)
        preview_status.setToolTip(preview.technical)
        preview_row.addWidget(preview_toggle)
        preview_row.addWidget(preview_status, 1)
        preview_toggle.toggled.connect(
            lambda checked: self._toggle_lighting_preview(
                editor,
                checked,
            )
        )
        editor.lighting_changed.connect(
            lambda lighting: self._lighting_editor_changed(
                preview_toggle,
                lighting,
            )
        )
        current_generation = snapshot.config_result.get("generation")
        base_changed = current_generation != draft.base_generation
        sync = V4Card(dots=True)
        sync.setObjectName("lightingSyncCard")
        sync_layout = QVBoxLayout(sync)
        sync_layout.setContentsMargins(18, 20, 18, 20)
        sync_layout.addWidget(_digital_caption("SYNC"))
        sync_summary = QLabel(objectName="preferencesSyncSummary")
        sync_summary.setWordWrap(True)
        sync_layout.addWidget(sync_summary)
        save_actions = QVBoxLayout()
        save_to_device = QPushButton(
            "保存到设备…", objectName="savePreferencesToDevice"
        )
        save_to_device.setProperty("buttonRole", "primary")
        save_to_device.setMinimumHeight(40)
        save_to_device.setToolTip(
            "先保存当前设置并验证，验证通过后仍需由你确认才会写入设备。"
        )
        save_to_device.setEnabled(
            editing_enabled
            and not base_changed
            and snapshot.compatibility.get("write") is True
            and self._view_model.write_transaction.state
            in {
                ConfigTransactionState.IDLE,
                ConfigTransactionState.ACTIVE,
                ConfigTransactionState.FAILED,
            }
        )
        save_to_device.clicked.connect(
            lambda: self._save_preferences_and_prepare(editor)
        )
        save_actions.addWidget(save_to_device)
        save_local = QPushButton(
            "仅保存本地草稿", objectName="savePreferencesDraft"
        )
        save_local.setProperty("buttonRole", "secondary")
        save_local.setMinimumHeight(40)
        save_local.setEnabled(editing_enabled)
        save_local.clicked.connect(lambda: self._save_preferences(editor))
        save_actions.addWidget(save_local)
        sync_layout.addLayout(save_actions)
        transaction_card = self._write_transaction_card(snapshot, base_changed, show_prepare=False)
        if self._view_model.write_transaction.state is not ConfigTransactionState.IDLE:
            transaction_card.layout().setContentsMargins(0, 8, 0, 0)
            for label in transaction_card.findChildren(QLabel):
                label.setWordWrap(True)
            # Retain all real confirmation, error, conflict and reconciliation actions.
            for box in transaction_card.findChildren(QHBoxLayout):
                box.setDirection(QBoxLayout.TopToBottom)
            sync_layout.addWidget(transaction_card)
        else:
            transaction_card.deleteLater()

        def update_summary(*_args):
            state = self._view_model.write_transaction.state
            if state not in {ConfigTransactionState.IDLE, ConfigTransactionState.ACTIVE}:
                set_translatable_text(sync_summary, _transaction_title(state))
                return
            pending = editor.values() != (
                draft.config["lighting"], draft.config["haptic"], draft.config["display"]
            )
            set_translatable_text(sync_summary, "当前设置尚未保存" if pending else
                                  "本地草稿尚未写入设备" if draft.is_dirty else "已同步 · 没有本地变更")

        for widget_type, signal_name in ((QCheckBox, "toggled"), (QComboBox, "currentIndexChanged"),
                                          (QSpinBox, "valueChanged"), (QSlider, "valueChanged")):
            for widget in editor.findChildren(widget_type):
                getattr(widget, signal_name).connect(update_summary)
        editor.lighting_changed.connect(update_summary)
        update_summary()
        device_preview = _lighting_device_preview(snapshot, editor)
        device_preview.setEnabled(editing_enabled)
        editor.set_workspace(device_preview, preview_controls, sync)
        layout.addWidget(editor, 1)
        scroll.setWidget(page)
        return scroll

    def _macro_key_choices(self) -> tuple[tuple[str, int], ...]:
        for definition in self._view_model.action_definitions:
            if definition.action_type != "key":
                continue
            for field in definition.fields:
                if field.name == "usage":
                    return tuple(
                        (label, int(value))
                        for label, value in action_field_choices("key", field, 4)
                    )
        return (("A", 4),)

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
        layout.addWidget(title)
        message = QLabel(transaction.message, objectName="muted")
        message.setWordWrap(True)
        layout.addWidget(message)
        if transaction.candidate_digest:
            layout.addWidget(
                QLabel(
                    f"base generation {transaction.base_generation} · candidate {transaction.candidate_digest[:12]}…",
                    objectName="muted",
                )
            )
        if transaction.technical:
            technical = QLabel(transaction.technical, objectName="muted")
            technical.setWordWrap(True)
            layout.addWidget(technical)

        buttons = QHBoxLayout()
        if transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION:
            confirm = QPushButton("确认写入设备", objectName="primary")
            confirm.clicked.connect(self._confirm_device_write)
            buttons.addWidget(confirm)
            cancel = QPushButton("取消", objectName="secondary")
            cancel.clicked.connect(self._view_model.cancel_device_write_confirmation)
            buttons.addWidget(cancel)
        elif transaction.state is ConfigTransactionState.UNKNOWN:
            reconcile = QPushButton("重新对账", objectName="primary")
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
            prepare = QPushButton("验证并准备写入", objectName="primary")
            errors = self._view_model.validate_draft()
            writable = snapshot.compatibility.get("write") is True
            prepare.setEnabled(
                draft is not None
                and draft.is_dirty
                and not base_changed
                and not errors
                and writable
            )
            prepare.clicked.connect(self._prepare_device_write)
            buttons.addWidget(prepare)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return card

    def _prepare_device_write(self) -> None:
        try:
            self._view_model.prepare_device_write()
        except ValueError as exc:
            QMessageBox.warning(self, "无法验证设备配置", str(exc))

    def _confirm_device_write(self) -> None:
        draft = self._view_model.draft
        transaction = self._view_model.write_transaction
        changes = len(draft.changes) if draft is not None else 0
        choice = QMessageBox.warning(
            self,
            "确认写入 BORING 设备",
            (
                f"设备已验证候选配置。即将写入 {changes} 项本地修改。\n\n"
                f"base generation: {transaction.base_generation}\n"
                f"candidate digest: {transaction.candidate_digest}\n\n"
                "写入后将等待设备激活并读回确认；连接异常时不会自动重试写入。"
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.confirm_device_write()
        except ValueError as exc:
            QMessageBox.warning(self, "无法写入设备", str(exc))

    def _reconcile_device_write(self) -> None:
        try:
            self._view_model.reconcile_device_write()
        except ValueError as exc:
            QMessageBox.warning(self, "无法重新对账", str(exc))

    def _refresh_conflicted_device(self) -> None:
        try:
            self._view_model.refresh_conflicted_device()
        except ValueError as exc:
            QMessageBox.warning(self, "无法重新读取设备", str(exc))

    def _rebase_conflicted_draft(self) -> None:
        draft = self._view_model.draft
        snapshot = self._view_model.model.snapshot
        if draft is None or snapshot is None:
            return
        changes = draft.preview_changes(snapshot.config)
        choice = QMessageBox.warning(
            self,
            "基于设备最新版本重新校验草稿",
            (
                f"当前完整草稿与设备最新配置有 {len(changes)} 项差异。\n\n"
                "继续后不会自动合并或写入；草稿将绑定到设备最新 generation，"
                "你仍需重新验证并再次确认整份配置。"
            ),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.rebase_conflicted_draft()
        except ValueError as exc:
            QMessageBox.warning(self, "无法重新绑定草稿", str(exc))

    def _discard_conflicted_draft(self) -> None:
        choice = QMessageBox.warning(
            self,
            "放弃本地草稿",
            "本地未写入修改将被放弃，并改用设备最新配置。需要保留时请先从按键配置页的配置方案菜单导出草稿。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.discard_conflicted_draft()
        except ValueError as exc:
            QMessageBox.warning(self, "无法使用设备配置", str(exc))

    def _mapping_editor_has_uncommitted_changes(self) -> bool:
        draft = self._view_model.draft
        card = self._current_mapping_card()
        editing_state = self._current_mapping_editing_state()
        if draft is None or card is None:
            return False
        if editing_state is None:
            return True
        control_id = editing_state.control_id
        profile_id = draft.config.get("active_profile")
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return False
        mapping = draft.mapping(profile_id, control_id)
        snapshot = self._view_model.model.snapshot
        confirmed_mapping = (
            snapshot.mappings.get(control_id) if snapshot is not None else None
        )
        saved_short_name = (
            str(mapping.get("short_name", "")) if mapping else "未映射"
        )
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
                control_id=control_id,
                short_name=short_name.text(),
                action=action_editor.action(),
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

    def _confirm_leave_mapping_editor(self) -> bool:
        if not self._mapping_editor_has_uncommitted_changes():
            return True
        choice = QMessageBox.warning(
            self,
            "当前控件修改尚未保存",
            "当前控件编辑器里还有未保存到本地草稿的内容。继续操作会放弃这些内容。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return choice == QMessageBox.Yes

    def _activate_profile(self, profile_id: int) -> None:
        draft = self._view_model.draft
        if draft is None or draft.config.get("active_profile") == profile_id:
            return
        if not self._confirm_leave_mapping_editor():
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            self._view_model.set_active_profile(profile_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法切换配置方案", str(exc))

    def _rename_profile(self) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        profile_id = draft.config.get("active_profile")
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
            QMessageBox.warning(self, "名称不可用", "配置方案名称不能为空。")
            return
        if len(name) > draft.editor_rules.profile_name_max_length:
            QMessageBox.warning(
                self,
                "名称过长",
                f"配置方案名称最多 {draft.editor_rules.profile_name_max_length} 个字符。",
            )
            return
        try:
            self._view_model.rename_profile(profile_id, name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法重命名配置方案", str(exc))

    def _show_draft_changes(self) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        changes = draft.changes
        lines = [
            f"{change.path}\n  {_short_value(change.before)}  →  {_short_value(change.after)}"
            for change in changes[:24]
        ]
        if len(changes) > 24:
            lines.append(f"还有 {len(changes) - 24} 项更改…")
        QMessageBox.information(
            self,
            "本地更改",
            "\n\n".join(lines) if lines else "当前本地草稿与设备已确认配置一致。",
        )

    def _confirm_discard_draft(self) -> None:
        draft = self._view_model.draft
        if draft is None or not draft.is_dirty:
            return
        choice = QMessageBox.warning(
            self,
            "放弃全部本地更改",
            "这会恢复到设备最近一次读回确认的配置，尚未写入设备的修改都会丢失。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        self._view_model.discard_draft()

    def _select_macro(self, macro_id: object) -> None:
        if isinstance(macro_id, int) and macro_id != self._selected_macro_id:
            self._selected_macro_id = macro_id
            self.render(self._view_model.model)

    def _create_macro(self) -> None:
        try:
            self._selected_macro_id = self._view_model.create_macro()
        except ValueError as exc:
            QMessageBox.warning(self, "无法新建按键序列", str(exc))
            return
        self.render(self._view_model.model)

    def _save_macro(self, macro_id: int, name: str, steps: list[dict]) -> None:
        try:
            self._view_model.update_macro(macro_id, name, steps)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存按键序列", str(exc))
            return
        errors = self._view_model.validate_draft()
        if errors:
            QMessageBox.warning(
                self, "按键序列已保存，但草稿未通过校验", errors[0]
            )

    def _delete_macro(self) -> None:
        if self._selected_macro_id is None:
            return
        try:
            self._view_model.delete_macro(self._selected_macro_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法删除按键序列", str(exc))
            return
        draft = self._view_model.draft
        self._selected_macro_id = draft.macros[0].get("id") if draft is not None and draft.macros else None
        self.render(self._view_model.model)

    def _save_preferences(self, editor: PreferencesEditor) -> bool:
        lighting, haptic, display = editor.values()
        try:
            self._view_model.set_preferences(
                lighting=lighting,
                haptic=haptic,
                display=display,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存设备偏好", str(exc))
            return False
        errors = self._view_model.validate_draft()
        if errors:
            QMessageBox.warning(self, "偏好已保存，但草稿未通过校验", errors[0])
            return False
        return True

    def _save_preferences_and_prepare(self, editor: PreferencesEditor) -> None:
        if self._save_preferences(editor):
            self._prepare_device_write()

    def _toggle_lighting_preview(
        self,
        editor: PreferencesEditor,
        enabled: bool,
    ) -> None:
        try:
            self._view_model.set_lighting_preview_enabled(
                enabled,
                editor.lighting_value() if enabled else None,
            )
        except ValueError as exc:
            toggle = self._content.findChild(QCheckBox, "lightingPreviewEnabled")
            if toggle is not None:
                blocked = toggle.blockSignals(True)
                toggle.setChecked(False)
                toggle.blockSignals(blocked)
            QMessageBox.warning(self, "无法开启灯光预览", str(exc))

    def _lighting_editor_changed(
        self,
        preview_toggle: QCheckBox,
        lighting: dict,
    ) -> None:
        if not preview_toggle.isChecked():
            return
        try:
            self._view_model.update_lighting_preview(lighting)
        except ValueError as exc:
            blocked = preview_toggle.blockSignals(True)
            preview_toggle.setChecked(False)
            preview_toggle.blockSignals(blocked)
            QMessageBox.warning(self, "无法更新灯光预览", str(exc))

    def _lighting_preview_status_changed(
        self,
        status: LightingPreviewStatus,
    ) -> None:
        if self._view_model.page != "lighting":
            return
        toggle = self._content.findChild(QCheckBox, "lightingPreviewEnabled")
        label = self._content.findChild(QLabel, "lightingPreviewStatus")
        if toggle is not None:
            blocked = toggle.blockSignals(True)
            toggle.setChecked(status.requested)
            toggle.setEnabled(
                status.supported
                and self._view_model.model.state is AppState.READY
                and not self._view_model.write_transaction.blocks_editing
            )
            toggle.blockSignals(blocked)
        if label is not None:
            set_translatable_text(label, status.message)
            label.setToolTip(translate_ui_text(status.technical))

    def _export_configuration(self, kind: str) -> bool:
        suffix = "draft" if kind == "draft" else "confirmed"
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出 BORING 配置",
            f"BORING-MIST-{suffix}.boring-config.json",
            "BORING 配置 (*.boring-config.json);;JSON (*.json)",
        )
        if not file_name:
            return False
        try:
            self._view_model.export_configuration(Path(file_name), kind=kind)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导出配置失败", str(exc))
            return False
        QMessageBox.information(
            self,
            "配置已导出",
            "本地草稿已导出。" if kind == "draft" else "设备已确认配置已导出。",
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
            QMessageBox.warning(self, "无法导入配置", str(exc))
            return
        draft = self._view_model.draft
        if draft is None:
            return
        changes = draft.preview_changes(package.config)
        paths = "\n".join(f"• {change.path}" for change in changes[:12])
        if len(changes) > 12:
            paths += f"\n• 还有 {len(changes) - 12} 项差异"
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Question)
        prompt.setWindowTitle("确认导入到本地草稿")
        prompt.setText(
            f"文件类型：{package.kind}\n"
            f"创建工具：{package.created_by}\n"
            f"检测到 {len(changes)} 项差异。\n\n"
            f"{paths or '配置内容与当前草稿一致。'}\n\n"
            "导入只替换本地草稿，不会写入设备。"
        )
        if draft.is_dirty:
            export_button = prompt.addButton("导出当前草稿后导入", QMessageBox.AcceptRole)
            replace_button = prompt.addButton("直接替换当前草稿", QMessageBox.DestructiveRole)
        else:
            export_button = None
            replace_button = prompt.addButton("导入到本地草稿", QMessageBox.AcceptRole)
        cancel_button = prompt.addButton("取消", QMessageBox.RejectRole)
        prompt.setDefaultButton(cancel_button)
        prompt.exec()
        role = prompt.buttonRole(prompt.clickedButton())
        if role not in (QMessageBox.AcceptRole, QMessageBox.DestructiveRole):
            return
        if export_button is not None and role == QMessageBox.AcceptRole and not self._export_configuration("draft"):
            return
        if not self._confirm_leave_mapping_editor():
            return
        self._suppress_mapping_edit_restore = True
        try:
            self._view_model.apply_configuration_import(package)
        except ValueError as exc:
            QMessageBox.warning(self, "导入配置与当前设备能力不兼容", str(exc))
            return
        else:
            self._pending_mapping_editing_state = None
        finally:
            self._suppress_mapping_edit_restore = False
        QMessageBox.information(self, "已导入本地草稿", "请检查差异并重新验证；配置尚未写入设备。")

    def _select_control(self, control_id: object) -> None:
        if isinstance(control_id, str) and control_id != self._selected_control_id:
            if not self._confirm_leave_mapping_editor():
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
            self._pending_mapping_editing_state = None
            if not self._refresh_mapping_workspace():
                self.render(self._view_model.model)

    def _select_physical_control(self, control_id: str) -> None:
        draft = self._view_model.draft
        if draft is None:
            return
        related = _related_controls(control_id, draft.controls)
        if related and related[0] != self._selected_control_id:
            if not self._confirm_leave_mapping_editor():
                return
            self._selected_control_id = related[0]
            self._pending_mapping_editing_state = None
            if not self._refresh_mapping_workspace():
                self.render(self._view_model.model)

    def _close_control_editor(self) -> None:
        if not self._confirm_leave_mapping_editor():
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        if not self._refresh_mapping_workspace():
            self.render(self._view_model.model)

    def _refresh_mapping_workspace(self) -> bool:
        snapshot = self._view_model.model.snapshot
        workspace = next(
            (
                candidate
                for candidate in self._content.findChildren(
                    _ResponsiveMappingWorkspace, "mappingWorkspace"
                )
                if candidate.isVisible()
            ),
            None,
        )
        if self._view_model.page != "overview" or snapshot is None or workspace is None:
            return False

        device = workspace.findChild(QFrame, "deviceWorkspace")
        if device is None:
            return False
        for button in device.findChildren(QPushButton):
            control_id = button.property("controlId")
            if not isinstance(control_id, str):
                continue
            selected = control_id == self._selected_control_id or (
                control_id in {"encoder", "joystick"}
                and isinstance(self._selected_control_id, str)
                and self._selected_control_id.startswith(f"{control_id}.")
            )
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

        inspector = self._mapping_editor(snapshot, None)
        if inspector is None:
            inspector = self._selection_inspector(snapshot)
        side_rail = self._mapping_side_rail(
            snapshot,
            self._view_model.model.state,
            inspector,
        )
        workspace.set_inspector(side_rail)
        self._language_manager.retranslate_widget_tree(side_rail)
        return True

    def _create_profile(self) -> None:
        if not self._confirm_leave_mapping_editor():
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            profile_id = self._view_model.create_profile()
            self._view_model.set_active_profile(profile_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法新建配置方案", str(exc))
            return

    def _save_profile_as(self) -> None:
        if not self._confirm_leave_mapping_editor():
            return
        draft = self._view_model.draft
        if draft is None:
            return
        source_profile_id = draft.config.get("active_profile")
        try:
            suggested_name = draft.suggested_profile_copy_name(source_profile_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法另存为配置方案", str(exc))
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
            self._view_model.save_profile_as(source_profile_id, name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法另存为配置方案", str(exc))
            return

    def _delete_profile(self) -> None:
        if not self._confirm_leave_mapping_editor():
            return
        draft = self._view_model.draft
        if draft is None:
            return
        profile_id = draft.config.get("active_profile")
        if not isinstance(profile_id, int) or isinstance(profile_id, bool):
            return
        profile_name = str(draft.profile(profile_id).get("name", ""))
        choice = QMessageBox.warning(
            self,
            "删除配置方案",
            f"确定从本地草稿删除“{profile_name}”吗？删除后仍需保存到设备才会生效。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Yes:
            return
        self._selected_control_id = None
        self._pending_mapping_editing_state = None
        try:
            self._view_model.delete_profile(profile_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法删除配置方案", str(exc))
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

    def _upload_screen_icon(self, pixels: bytes) -> None:
        try:
            self._view_model.upload_screen_icon(pixels)
        except ValueError as exc:
            QMessageBox.warning(self, translate_ui_text("写入图标"), translate_ui_text(str(exc)))

    def _write_screen_glyph(self, pixels: bytes | None) -> None:
        try:
            self._view_model.write_screen_glyph(pixels)
        except ValueError as exc:
            QMessageBox.warning(self, translate_ui_text("功能与模式图标"), translate_ui_text(str(exc)))

    def _reset_screen_icon(self) -> None:
        try:
            self._view_model.reset_screen_icon()
        except ValueError as exc:
            QMessageBox.warning(self, translate_ui_text("恢复默认图标"), translate_ui_text(str(exc)))

    def _screen_icon_draft_for(self, snapshot: DeviceSnapshot) -> ScreenIconDraft:
        key = (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"]))
        return self._screen_icon_drafts.setdefault(key, ScreenIconDraft())

    def _confirm_discard_screen_icons(self) -> bool:
        if not any(draft.is_dirty for draft in (*self._screen_icon_drafts.values(), *self._screen_glyph_drafts.values())):
            return True
        return QMessageBox.question(
            self,
            translate_ui_text("本地图标尚未写入"),
            translate_ui_text("退出将丢弃本次运行中各设备的本地图标编辑；原图片文件不受影响。是否丢弃并退出？"),
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) == QMessageBox.Discard

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._view_model.screen_icon.busy or self._view_model.screen_glyphs.busy:
            QMessageBox.warning(self, translate_ui_text("圆屏图标尚未完成"), translate_ui_text(
                "请等待圆屏图标传输或读回完成；提交之前可以点击取消上传。"
            ))
            event.ignore()
            return
        calibration = self._view_model.calibration
        if calibration.blocks_editing:
            QMessageBox.warning(
                self,
                "摇杆校准尚未结束",
                "BORING 控制台正在采样、取消、等待激活或读回对账。请先回到摇杆校准页完成或取消事务。",
            )
            event.ignore()
            return
        firmware = self._view_model.firmware_update
        if firmware.blocks_editing:
            QMessageBox.warning(
                self,
                "固件维护尚未结束",
                "BORING 控制台正在传输、对账、等待重启，或等待你处理设备中的旧更新。请先回到固件维护页完成或中止事务。",
            )
            event.ignore()
            return
        transaction = self._view_model.write_transaction
        if self._configuration_blocks_close():
            QMessageBox.warning(
                self,
                "设备写入尚未完成",
                "BORING 控制台正在写入、等待激活或处理未知结果。请先让事务进入已激活、失败或冲突状态后再关闭；关闭应用不会取消设备端事务。",
            )
            event.ignore()
            return
        if (
            self._background_controller is not None
            and not self._background_controller.quitting
            and self._background_controller.hide_window()
        ):
            event.ignore()
            return
        if not self._confirm_leave_page():
            event.ignore()
            return
        if not self._confirm_discard_screen_icons():
            event.ignore()
            return
        draft = self._view_model.draft
        unresolved_exit = transaction.state is ConfigTransactionState.UNKNOWN
        if unresolved_exit:
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Warning)
            prompt.setWindowTitle("原设备未连接，写入结果未知")
            prompt.setText(
                "无法确认设备是否已保存。可以导出未决草稿和设备写入信息后退出。\n"
                "退出不会取消设备端写入，也不代表保存成功。重新打开后，请先连接原设备读取配置；不会自动重发写入。"
            )
            export_button = prompt.addButton("导出未决草稿后退出", QMessageBox.AcceptRole)
            cancel_button = prompt.addButton("取消", QMessageBox.RejectRole)
            prompt.setDefaultButton(cancel_button)
            prompt.exec()
            if prompt.buttonRole(prompt.clickedButton()) != QMessageBox.AcceptRole:
                event.ignore()
                return
            # Reconnection can start readback while the modal dialog is open.
            if self._configuration_blocks_close():
                event.ignore()
                return
            if not self._export_configuration("draft"):
                event.ignore()
                return
        elif draft is not None and draft.is_dirty:
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Warning)
            prompt.setWindowTitle("本地草稿尚未导出")
            prompt.setText("当前草稿只保留在本进程中。可以先导出草稿，或丢弃后关闭。")
            export_button = prompt.addButton("导出草稿后关闭", QMessageBox.AcceptRole)
            discard_button = prompt.addButton("丢弃并关闭", QMessageBox.DestructiveRole)
            cancel_button = prompt.addButton("取消", QMessageBox.RejectRole)
            prompt.setDefaultButton(cancel_button)
            prompt.exec()
            # The Show-event translation walk can recreate PySide wrappers.
            # Compare Qt's role, not Python identity of the original button.
            role = prompt.buttonRole(prompt.clickedButton())
            if role not in (QMessageBox.AcceptRole, QMessageBox.DestructiveRole):
                event.ignore()
                return
            if role == QMessageBox.AcceptRole and not self._export_configuration("draft"):
                event.ignore()
                return
            if role == QMessageBox.DestructiveRole:
                self._view_model.discard_draft()
        if unresolved_exit and self._configuration_blocks_close():
            # The save-file and export-result dialogs also run the Qt event loop.
            event.ignore()
            return
        self._view_model.stop_lighting_preview(clear_candidate=True)
        self._view_model.shutdown()
        self._screen_icon_drafts.clear()
        self._screen_glyph_drafts.clear()
        event.accept()


def _card(*, object_name: str = "card") -> QFrame:
    card = QFrame(objectName=object_name)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return card


def _calibration_state_title(state: CalibrationState) -> str:
    return {
        CalibrationState.IDLE: "等待开始",
        CalibrationState.STARTING: "建立校准会话",
        CalibrationState.CENTERING: "采集中立点",
        CalibrationState.SAMPLING: "读取实时采样",
        CalibrationState.CAPTURING: "采集完整行程",
        CalibrationState.READY_TO_CONFIRM: "可保存校准",
        CalibrationState.CONFIRMING: "检查回中并保存",
        CalibrationState.PENDING: "等待候选激活",
        CalibrationState.VERIFYING: "读回校准结果",
        CalibrationState.UNKNOWN: "结果待对账",
        CalibrationState.CANCELLING: "正在取消",
        CalibrationState.CANCELLED: "已取消",
        CalibrationState.COMPLETED: "校准已完成",
        CalibrationState.FAILED: "校准未完成",
        CalibrationState.CONFLICT: "配置冲突",
    }[state]


def _calibration_endpoint_text(transaction: CalibrationTransaction) -> str:
    if transaction.center_x is None or transaction.center_y is None:
        return "端点和中心将在中立点采集完成后显示。"
    status = "四周行程已完整" if transaction.travel_complete else "继续转动摇杆触及四周边缘"
    return (
        f"{status}\n"
        f"X  {transaction.minimum_x} / {transaction.center_x} / {transaction.maximum_x}\n"
        f"Y  {transaction.minimum_y} / {transaction.center_y} / {transaction.maximum_y}"
    )


def _planned_tool_capability(
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


def _planned_tool_capability_label(
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


def _planned_tool_facts(
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


def _digital_caption(text: str) -> Boring5RLabel:
    return Boring5RLabel(
        text,
        scale=0.75,
        color="#a58c86",
        objectName="digitalCaption",
    )


def _digital_when_supported(
    text: str,
    *,
    preferred_scale: float,
    minimum_scale: float,
    maximum_width: float | None = None,
    object_name: str | None = None,
) -> QLabel:
    try:
        unscaled_layout = layout_text(text)
    except ValueError:
        unscaled_layout = None
    if unscaled_layout is not None and text:
        scale = preferred_scale
        if maximum_width is not None:
            unscaled_width = unscaled_layout.width
            if unscaled_width > 0:
                scale = min(scale, maximum_width / unscaled_width)
        if scale >= minimum_scale:
            return Boring5RLabel(
                text,
                scale=scale,
                objectName=object_name,
            )
    return QLabel(text, objectName=object_name)


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
        "keyboard": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><rect x="5.5" y="6.5" width="13" height="10" rx="1.8"/><path d="M7 14 Q12 16 17 14"/>',
        "bubble.left": '<rect x="2" y="13" width="7" height="7" rx="1"/><path d="M12 3 L17 11 H7 Z"/><circle cx="18" cy="17" r="4"/>',
        "dial.low": '<circle cx="12" cy="12" r="9"/><path d="M12 4 V8 M5 16 L9 14 M19 16 L15 14"/>',
        "sun.max": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="6"/>' + ''.join(f'<circle cx="{x}" cy="{y}" r=".6"/>' for x in (9,12,15) for y in (9,12,15)),
        "gearshape": '<path d="M10 2 H14 L15 5 L18 6 L21 6 L22 10 L20 12 L21 15 L19 19 L16 19 L14 22 H10 L9 19 L6 18 L3 18 L2 14 L4 12 L3 9 L5 5 L8 5 Z"/><circle cx="12" cy="12" r="4"/>',
    }
    if symbol_name in shapes:
        colour = "#EFEAE0" if white > .8 else "#9A958C"
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


def _control_display_name(control_id: str) -> str:
    if control_id.startswith("key."):
        index = control_id.split(".", 1)[1]
        return (
            f"透明状态键 {index}"
            if control_id in MATRIX12_AGENT_STATUS_KEYS
            else f"白色按键 {index}"
        )
    return {
        "encoder.ccw": "旋钮逆时针",
        "encoder.cw": "旋钮顺时针",
        "encoder.press": "旋钮按下",
        "joystick.up": "摇杆向上",
        "joystick.down": "摇杆向下",
        "joystick.left": "摇杆向左",
        "joystick.right": "摇杆向右",
        "joystick.press": "摇杆按下",
    }.get(control_id, control_id)


def _control_role_style(control_id: str) -> str:
    return "roleAgent" if control_id in MATRIX12_AGENT_STATUS_KEYS else "roleHuman"


def _control_role_copy(control_id: str) -> tuple[str, str]:
    if control_id in MATRIX12_AGENT_STATUS_KEYS:
        return (
            "AGENT STATUS",
            "透明键帽\nCodex 模式下承担外部 Agent 状态反馈；这里编辑的是设备协议动作，不定义 Agent 状态语义。",
        )
    if control_id.startswith("key."):
        return (
            "HUMAN INPUT",
            "白色键帽\n用于触发用户动作，可编辑真实按键、组合键或设备功能。",
        )
    return (
        "HUMAN INPUT",
        "实体控制\n用于导航、选择、确认或调整；不同触发方式分别保存动作。",
    )


def _short_value(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 120 else f"{text[:117]}…"


def _next_action(snapshot: DeviceSnapshot, state: AppState) -> str:
    if state is AppState.DISCONNECTED:
        return "下一步：重新连接并读取设备；下方保留的是断开前的只读快照。"
    if snapshot.status.get("activation_failed") is True:
        return "下一步：重启设备后再读取；若仍失败，使用设备的物理恢复流程。"
    if snapshot.status.get("pending") is not None:
        return "下一步：等待设备完成激活；当前不要重复写入配置。"
    if not snapshot.config_is_synced:
        return "下一步：重新扫描并完整读取设备；确认对账后再编辑配置。"
    if snapshot.is_read_only:
        return "当前设备仅允许读取。可检查配置与技术详情。"
    return "下一步：点击下方任一按键、旋钮或摇杆，直接调整动作。"


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
        return "透明键阵列显示 Agent 状态；白键与金属控制件保留用户决策入口。"
    if mode == "normal":
        return "白色键帽是主要动作入口；透明键仍保留其状态键帽身份。"
    return "界面只呈现设备已经报告的模式与控件能力。"


def _mode_explanation(mode: str) -> str:
    if mode == "codex":
        return "当前为 Codex 模式。设备负责转发外部 Agent 状态，不表示设备内部运行 AI；当前协议尚未报告 Agent 来源与实时状态，因此界面不虚构这些内容。"
    if mode == "normal":
        return "当前为 Normal 模式。白色键帽用于普通按键和组合动作；透明键不会被解释成普通 RGB 灯位。"
    return "当前模式没有对应的专用语义说明，控制台保留设备报告的原始模式。"


def _technical_details_text(snapshot: DeviceSnapshot) -> str:
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
        )
    )


def _prompt_device_shell(snapshot: DeviceSnapshot) -> QWidget:
    shell = _device_shell_widget(snapshot, mappings=snapshot.mappings,
                                on_control=None, selected_control_id=None)
    shell.setStyleSheet(APP_STYLE + V4_STYLE + "QFrame#deviceShell { background: transparent; border: none; }")
    for label in shell.findChildren(QLabel):
        label.hide()
    for key in shell.findChildren(_KeycapButton):
        key.setProperty("lightingPreview", True)
        key.setFocusPolicy(Qt.NoFocus)
        key.setToolTip("")
    return shell


def _lighting_device_preview(snapshot: DeviceSnapshot, editor: PreferencesEditor) -> QWidget:
    stage = QWidget(objectName="lightingDevicePreview")
    stage.setMinimumWidth(426)
    stage.setMinimumHeight(460)
    layout = QVBoxLayout(stage)
    layout.setContentsMargins(0, 20, 0, 20)
    layout.addStretch(1)
    swatches = {button.property("controlId"): button for _, button in editor._under_key_colors}
    shell = _device_shell_widget(snapshot, mappings=snapshot.mappings,
                                 on_control=None, selected_control_id=None)
    display = shell.findChild(QFrame, "displayControl")
    if display is not None:
        for label in display.findChildren(QLabel):
            label.hide()
        brand = Boring5RLabel("BORING", scale=.5, color="#EFEAE0", alignment=Qt.AlignCenter)
        display.layout().addWidget(brand, 0, Qt.AlignCenter)
        def update_display(*_args):
            enabled = editor.values()[2].get("brightness", 0) > 0
            brand.setVisible(enabled)
            ring_color = "#A7B6E4" if enabled else "#41413B"
            display.setStyleSheet(
                f"QFrame#displayControl {{ background: #121311; border: 2px solid {ring_color}; border-radius: 26px; }}"
            )
        control = getattr(editor, "_display_brightness", None)
        if isinstance(control, QComboBox):
            control.currentIndexChanged.connect(update_display)
        elif isinstance(control, QSpinBox):
            control.valueChanged.connect(update_display)
        update_display()
    encoder = shell.findChild(QPushButton, "encoderControl")
    if encoder is not None:
        # Keep the existing physical control; omit mapping prose on this lighting view.
        for label in encoder.findChildren(QLabel):
            label.hide()
        encoder.setToolTip(translate_ui_text("旋钮不带可配置灯光"))
    for key in shell.findChildren(_KeycapButton):
        control_id = key.property("controlId")
        for inscription in key.findChildren(QLabel):
            inscription.hide()
        key.setAccessibleName(str(control_id))
        if control_id in swatches:
            key.setToolTip(translate_ui_text("点击调整该按键灯颜色"))
            key.setFocusPolicy(Qt.StrongFocus)
            key.clicked.connect(swatches[control_id].click)
        else:
            key.setToolTip(translate_ui_text("Agent 灯光由任务状态控制，此处不模拟状态颜色。"))
    layout.addWidget(shell, 0, Qt.AlignCenter)
    caption = QLabel("本地效果示意 · 点击白色按键调整灯色", objectName="muted")
    caption.setWordWrap(True)
    caption.setAlignment(Qt.AlignCenter)
    layout.addWidget(caption)
    note = QLabel("Agent 灯光由任务状态控制，此处不模拟状态颜色。", objectName="muted")
    note.setWordWrap(True)
    note.setAlignment(Qt.AlignCenter)
    layout.addWidget(note)
    layout.addStretch(1)

    def update_lighting(lighting):
        colors = lighting.get("under_key", [])
        brightness = min(80, lighting.get("brightness", 0)) / 80 if lighting.get("enabled") else 0
        for key in shell.findChildren(_KeycapButton):
            control_id = key.property("controlId")
            color = QColor(0, 0, 0, 0)
            if control_id in swatches:
                index = int(control_id.split(".")[-1]) - 1
                if index < len(colors):
                    rgb = colors[index]
                    peak = max(rgb.values())
                    if peak > 0:
                        # Perceptual boost for the local illustration only. Low RGB
                        # values must remain legible on white keycaps; zero stays off.
                        opacity = round(235 * (brightness * peak / 255) ** .45)
                        color = QColor(round(rgb["r"] * 255 / peak), round(rgb["g"] * 255 / peak),
                                       round(rgb["b"] * 255 / peak), opacity)
            key.setProperty("lightingColor", color)
            key.update()
    editor.lighting_changed.connect(update_lighting)
    update_lighting(editor.lighting_value())
    return stage


def _device_shell_widget(
    snapshot: DeviceSnapshot,
    *,
    mappings: dict[str, dict],
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> QFrame:
    device_shell = QFrame(objectName="deviceShell")
    device_shell.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    if str(snapshot.identity.get("hardware_id", "")) in MATRIX12_HARDWARE_IDS:
        # A fixed, shared physical coordinate system (reference board: 420 × 420).
        # Layout metadata stays on each real button; no bitmap hit-map is involved.
        device_shell.setFixedSize(420, 420)
        device_shell.setStyleSheet("QFrame#deviceShell { background: transparent; border: none; }")
        face = QFrame(device_shell, objectName="deviceFace")
        face.setGeometry(0, 0, 420, 420)
        face.setStyleSheet("QFrame#deviceFace { background: #1C1B19; border: none; border-radius: 64px; }")
        holder = QWidget()
        grid = _matrix12_control_layout(snapshot, mappings,
            str(snapshot.status.get("platform", "")), on_control, selected_control_id)
        holder.setLayout(grid)
        while grid.count():
            control = grid.takeAt(0).widget()
            control.setParent(face)
            control_id = control.property("controlId")
            row, column = control.property("gridRow"), control.property("gridColumn")
            if control_id == "encoder":
                control.setFixedSize(128, 128)
                control.move(0, 292)
            elif control_id in {"display", "joystick"}:
                control.setFixedSize(52, 52)
                control.move(86 + column * 67, 86 + row * 67)
                if control_id == "display":
                    for label in control.findChildren(QLabel):
                        label.hide()
                    brand = Boring5RLabel("BORING", scale=.4, color="#EFEAE0", alignment=Qt.AlignCenter)
                    brand.setObjectName("deviceScreenBrand")
                    control.layout().setContentsMargins(2, 2, 2, 2)
                    control.layout().addWidget(brand, 0, Qt.AlignCenter)
                    control.setStyleSheet("QFrame#displayControl { background: #23241F; border: 2px solid #A7B6E4; border-radius: 26px; }")
            else:
                control.move(80 + column * 67, 80 + row * 67)
            control.show()
        holder.deleteLater()
        return device_shell
    shell_layout = QVBoxLayout(device_shell)
    shell_layout.setContentsMargins(12, 12, 12, 12)
    device_face = QFrame(objectName="deviceFace")
    face_layout = QVBoxLayout(device_face)
    face_layout.setContentsMargins(24, 22, 24, 22)
    face_layout.addLayout(
        _control_layout(
            snapshot,
            mappings=mappings,
            on_control=on_control,
            selected_control_id=selected_control_id,
        )
    )
    shell_layout.addWidget(device_face)
    return device_shell


def _control_layout(
    snapshot: DeviceSnapshot,
    *,
    mappings: dict[str, dict] | None = None,
    on_control: Callable[[str], None] | None = None,
    selected_control_id: str | None = None,
):
    resolved_mappings = snapshot.mappings if mappings is None else mappings
    platform = str(snapshot.status.get("platform", ""))
    hardware_id = str(snapshot.identity.get("hardware_id", ""))
    if hardware_id in MATRIX12_HARDWARE_IDS:
        return _matrix12_control_layout(
            snapshot,
            resolved_mappings,
            platform,
            on_control,
            selected_control_id,
        )

    return _generic_control_layout(
        snapshot,
        resolved_mappings,
        platform,
        on_control,
        selected_control_id,
    )


def _matrix12_control_layout(
    snapshot: DeviceSnapshot,
    mappings: dict[str, dict],
    platform: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(MATRIX12_GAP)
    grid.setVerticalSpacing(MATRIX12_GAP)
    grid.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    mode = str(snapshot.status.get("operating_mode", ""))

    _add_physical_control(grid, _display_tile(MATRIX12_UNIT), "display", 0, 0)
    positions = {
        "key.1": (0, 1, 1),
        "key.2": (0, 2, 1),
        "key.3": (0, 3, 1),
        "key.4": (1, 0, 1),
        "key.5": (1, 1, 1),
        "key.6": (1, 2, 1),
        "key.7": (1, 3, 1),
        "key.8": (2, 0, 2),
        "key.9": (2, 2, 1),
        "key.10": (2, 3, 1),
        "key.11": (3, 1, 1),
        "key.12": (3, 2, 1),
    }
    for control_id, (row, column, column_span) in positions.items():
        if control_id in snapshot.controls:
            tile = _control_tile(control_id, mappings.get(control_id), platform, mode)
            _configure_control_button(tile, control_id, on_control, selected_control_id)
            tile.setFixedSize(
                MATRIX12_UNIT * column_span + MATRIX12_GAP * (column_span - 1),
                MATRIX12_UNIT,
            )
            _add_physical_control(
                grid,
                tile,
                control_id,
                row,
                column,
                column_span,
            )

    joystick = _joystick_tile(
        "摇杆",
        [
            (
                "↑/↓",
                "/".join(
                    _mapping_action_summary(mappings, control_id, platform)
                    for control_id in ("joystick.up", "joystick.down")
                ),
                "/".join(
                    _mapping_name(mappings, control_id)
                    for control_id in ("joystick.up", "joystick.down")
                ),
            ),
            (
                "←/→",
                "/".join(
                    _mapping_action_summary(mappings, control_id, platform)
                    for control_id in ("joystick.left", "joystick.right")
                ),
                "/".join(
                    _mapping_name(mappings, control_id)
                    for control_id in ("joystick.left", "joystick.right")
                ),
            ),
            (
                "按",
                _mapping_action_summary(mappings, "joystick.press", platform),
                _mapping_name(mappings, "joystick.press"),
            ),
        ],
    )
    encoder = _rotary_tile(
        "旋钮",
        [
            (
                "逆时针",
                _mapping_action_summary(mappings, "encoder.ccw", platform),
                _mapping_name(mappings, "encoder.ccw"),
            ),
            (
                "顺时针",
                _mapping_action_summary(mappings, "encoder.cw", platform),
                _mapping_name(mappings, "encoder.cw"),
            ),
            (
                "按",
                _mapping_action_summary(mappings, "encoder.press", platform),
                _mapping_name(mappings, "encoder.press"),
            ),
        ],
    )
    encoder.setObjectName("encoderControl")
    joystick.setFixedSize(MATRIX12_UNIT, MATRIX12_UNIT)
    encoder.setFixedSize(128, 128)
    _configure_control_button(encoder, "encoder", on_control, selected_control_id)
    _configure_control_button(joystick, "joystick", on_control, selected_control_id)
    _add_physical_control(grid, encoder, "encoder", 3, 0)
    _add_physical_control(grid, joystick, "joystick", 3, 3)
    return grid


def _generic_control_layout(
    snapshot: DeviceSnapshot,
    mappings: dict[str, dict],
    platform: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> QHBoxLayout:
    root = QHBoxLayout()
    root.setSpacing(20)
    key_grid = QGridLayout()
    key_grid.setSpacing(8)
    keys = [control for control in snapshot.controls if control.startswith("key.")]
    columns = min(4, max(1, len(keys)))
    mode = str(snapshot.status.get("operating_mode", ""))
    for index, control_id in enumerate(keys):
        tile = _control_tile(control_id, mappings.get(control_id), platform, mode)
        _configure_control_button(tile, control_id, on_control, selected_control_id)
        key_grid.addWidget(tile, index // columns, index % columns)
    root.addLayout(key_grid, 3)

    secondary = QVBoxLayout()
    secondary.setSpacing(8)
    for prefix, label in (("encoder.", "旋钮"), ("joystick.", "摇杆")):
        controls = [control for control in snapshot.controls if control.startswith(prefix)]
        group = _multi_action_tile(
            label,
            [
                (
                    _control_display_name(control_id).removeprefix(f"{label}"),
                    _mapping_action_summary(mappings, control_id, platform),
                    _mapping_name(mappings, control_id),
                )
                for control_id in controls
            ],
        )
        _configure_control_button(group, prefix.removesuffix("."), on_control, selected_control_id)
        secondary.addWidget(group)
    secondary.addStretch(1)
    root.addLayout(secondary, 2)
    return root


def _add_physical_control(
    layout: QGridLayout,
    widget: QWidget,
    control_id: str,
    row: int,
    column: int,
    column_span: int = 1,
) -> None:
    widget.setProperty("controlId", control_id)
    widget.setProperty("gridRow", row)
    widget.setProperty("gridColumn", column)
    widget.setProperty("gridColumnSpan", column_span)
    layout.addWidget(widget, row, column, 1, column_span, Qt.AlignCenter)


def _display_tile(size: int = 76) -> QFrame:
    tile = QFrame(objectName="displayControl")
    tile.setFixedSize(size, size)
    box = QVBoxLayout(tile)
    box.setContentsMargins(12, 10, 12, 10)
    title = QLabel("圆屏")
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("font-weight: 800;")
    subtitle = QLabel("设备状态")
    subtitle.setAlignment(Qt.AlignCenter)
    subtitle.setStyleSheet("font-size: 10px; color: #aaa99f;")
    box.addWidget(title)
    box.addWidget(subtitle)
    return tile


def _multi_action_tile(title: str, rows: list[tuple[str, str, str]]) -> QPushButton:
    tile = QPushButton(objectName="secondaryControl")
    tile.setMinimumSize(76, 64)
    box = QVBoxLayout(tile)
    box.setContentsMargins(6, 5, 6, 6)
    box.setSpacing(3)
    heading = QLabel(title)
    heading.setAlignment(Qt.AlignCenter)
    heading.setStyleSheet("font-weight: 800;")
    box.addWidget(heading)
    for label, action, name in rows:
        text = QLabel(f"{label} · {action}", objectName="controlId")
        text.setAlignment(Qt.AlignCenter)
        text.setStyleSheet("font-size: 8px;")
        text.setToolTip(f"实际动作：{action}\n功能名称：{name}")
        box.addWidget(text)
    return tile


class _RotaryButton(QPushButton):
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else .55)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        joystick = self.objectName() == "joystickControl"
        material = QRadialGradient(rect.center() - rect.topLeft() * .15, rect.width() * .7)
        material.setColorAt(0, QColor("#37372F" if joystick else "#E0DED5"))
        material.setColorAt(1, QColor("#25261F" if joystick else "#ABA9A1"))
        painter.setBrush(material)
        painter.setPen(QPen(QColor("#EFEAE0" if self.property("selected") or self.hasFocus() else "#171813"), 2))
        painter.drawEllipse(rect)
        if not joystick:
            painter.setPen(QPen(QColor("#22231D"), 3, Qt.SolidLine, Qt.RoundCap))
            for x1, y1, x2, y2 in ((.16,.3,.34,.4), (.66,.4,.84,.3), (.5,.72,.5,.91)):
                painter.drawLine(round(self.width()*x1), round(self.height()*y1),
                                 round(self.width()*x2), round(self.height()*y2))


def _rotary_tile(title: str, rows: list[tuple[str, str, str]]) -> QPushButton:
    tile = _RotaryButton(objectName="encoderControl")
    tile.setToolTip("\n".join(f"{label} · {action}（{name}）" for label, action, name in rows))
    return tile


def _joystick_tile(title: str, rows: list[tuple[str, str, str]]) -> QPushButton:
    tile = _RotaryButton(objectName="joystickControl")
    tile.setToolTip(
        "\n".join(
            f"{label} · {action}（{name}）"
            for label, action, name in rows
        )
    )
    return tile


def _mapping_name(mappings: dict[str, dict], control_id: str) -> str:
    mapping = mappings.get(control_id)
    return str(mapping.get("short_name", "未映射")) if mapping else "未映射"


def _mapping_action_summary(
    mappings: dict[str, dict],
    control_id: str,
    platform: str,
) -> str:
    mapping = mappings.get(control_id)
    action = mapping.get("action") if isinstance(mapping, dict) else None
    return describe_action(action, platform=platform, compact=True)


class _KeycapButton(QPushButton):
    """LCK dimensions expressed as a top-view material, not the missing CAD asset."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.55)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        agent = self.property("role") == "agent"
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, QColor("#F2F1EE" if agent else "#F4F0E7"))
        gradient.setColorAt(1, QColor("#DCDAD5" if agent else "#DDD8CC"))
        painter.setPen(QPen(QColor(V4_TOKENS["control"]) if self.property("selected") else QColor(255, 255, 255, 30), 2))
        painter.setBrush(gradient)
        radius = rect.height() * 2.49 / 18
        painter.drawRoundedRect(rect, radius, radius)
        top = rect.adjusted(3, 3, -3, -3)
        dish = QRadialGradient(top.center(), top.width() * .65)
        dish.setColorAt(0, QColor(130, 125, 115, 16))
        dish.setColorAt(1, QColor(255, 255, 255, 65))
        painter.setBrush(dish)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        painter.drawRoundedRect(top, max(1, radius - 3), max(1, radius - 3))
        light = self.property("lightingColor")
        if isinstance(light, QColor) and light.alpha() > 0:
            glow = QRadialGradient(top.center(), top.width() * .9)
            glow.setColorAt(0, light.lighter(135))
            glow.setColorAt(.35, light)
            glow.setColorAt(1, QColor(light.red(), light.green(), light.blue(), 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawRoundedRect(top, max(1, radius - 3), max(1, radius - 3))
        slot = self.property("bluetoothSlot")
        if slot is not None:
            # Secondary key legend, above the action text. Paint it rather than
            # using a font glyph so the Bluetooth symbol is identical on both OSes.
            painter.save()
            painter.translate(10, 9)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#69665E"), 1.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            symbol = QPainterPath()
            for points in (((3, 0), (8, 4), (0, 12)), ((3, 16), (8, 12), (0, 4)), ((3, 0), (3, 16))):
                symbol.moveTo(*points[0])
                for point in points[1:]:
                    symbol.lineTo(*point)
            painter.drawPath(symbol)
            font = painter.font()
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(13, 0, 12, 16), Qt.AlignLeft | Qt.AlignVCenter, str(slot))
            painter.restore()
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#2A2824"), 1, Qt.DashLine))
            painter.drawRoundedRect(top.adjusted(2, 2, -2, -2), max(1, radius - 5), max(1, radius - 5))


class _KeycapInscription(QLabel):
    """Keep a full action description while fitting its small physical keycap."""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(
            self.text(), Qt.ElideRight, self.contentsRect().width()
        )
        painter.drawText(self.contentsRect(), Qt.AlignLeft | Qt.AlignVCenter, text)


def _control_tile(
    control_id: str,
    mapping: dict | None,
    platform: str,
    mode: str,
) -> QPushButton:
    tile = _KeycapButton(objectName="controlKey")
    tile.setProperty("bluetoothSlot", {"key.8": 1, "key.9": 2, "key.10": 3}.get(control_id))
    tile.setProperty("mapped", mapping is not None)
    role = "agent" if control_id in MATRIX12_AGENT_STATUS_KEYS else "human"
    tile.setProperty("role", role)
    tile.setMinimumSize(76, 64)
    box = QVBoxLayout(tile)
    box.setContentsMargins(7, 7, 7, 8)
    control_label = _digital_when_supported(
        f"AGENT {control_id.split('.', 1)[1]}" if role == "agent" else control_id,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlId",
    )
    action = mapping.get("action") if mapping else None
    action_summary = describe_action(action, platform=platform, compact=True)
    display_summary = "状态未接入" if role == "agent" and mode == "codex" else action_summary
    action_label = _digital_when_supported(
        display_summary,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlAction",
    )
    action_label.setWordWrap(True)
    custom_name = str(mapping.get("short_name", "")) if mapping else ""
    name_text = f"动作 · {action_summary}" if role == "agent" and mode == "codex" else (custom_name or "—")
    name_label = _digital_when_supported(
        name_text,
        preferred_scale=0.75,
        minimum_scale=0.75,
        maximum_width=62,
        object_name="controlName",
    )
    if custom_name and not (role == "agent" and mode == "codex"):
        name_label.setProperty("boringI18nSkip_text", True)
    name_label.setToolTip(f"功能名称：{custom_name}" if custom_name else "未设置功能名称")
    # IDs and custom names remain available to assistive technology / tooltip,
    # not printed as three rows on a keycap. Agent light state isn't in WMP.
    control_label.setParent(tile)
    control_label.hide()
    name_label.setParent(tile)
    name_label.hide()
    box.addStretch(1)
    if role == "human":
        inscription = _KeycapInscription(action_summary, objectName="keycapInscription")
        inscription.setMinimumWidth(0)
        inscription.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        inscription.setToolTip(action_summary)
        inscription.setStyleSheet("color: #2A2824; font-size: 12px; font-weight: 600; background: transparent;")
        inscription.setAttribute(Qt.WA_TransparentForMouseEvents)
        box.addWidget(inscription, 0, Qt.AlignBottom)
    action_label.setParent(tile)
    action_label.hide()
    return tile


def _configure_control_button(
    button: QPushButton,
    control_id: str,
    on_control: Callable[[str], None] | None,
    selected_control_id: str | None,
) -> None:
    is_selected = selected_control_id == control_id or (
        control_id in {"encoder", "joystick"}
        and isinstance(selected_control_id, str)
        and selected_control_id.startswith(f"{control_id}.")
    )
    button.setProperty("selected", is_selected)
    button.setAccessibleName(f"设置{_physical_control_name(control_id)}")
    details = button.toolTip()
    button.setToolTip(f"点击设置{_physical_control_name(control_id)}" + (f"\n{details}" if details else ""))
    inscription = button.findChild(QLabel, "keycapInscription")
    if inscription is not None:
        button.setToolTip(f"{button.toolTip()}\n{inscription.text()}")
    for label in button.findChildren(QLabel):
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
    if on_control is None:
        button.setFocusPolicy(Qt.NoFocus)
        return
    button.setCursor(Qt.PointingHandCursor)
    button.clicked.connect(lambda _checked=False, value=control_id: on_control(value))


def _physical_control_name(control_id: str) -> str:
    if control_id == "encoder":
        return "旋钮"
    if control_id == "joystick":
        return "摇杆"
    return _control_display_name(control_id)


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


def _badge_text(state: AppState) -> str:
    return {
        AppState.SCANNING: "扫描中",
        AppState.NO_DEVICE: "无设备",
        AppState.MULTIPLE_DEVICES: "请选择设备",
        AppState.CONNECTING: "连接中",
        AppState.READY: "已连接",
        AppState.READ_ONLY: "只读",
        AppState.FIRMWARE_UPDATE_REQUIRED: "需要更新固件",
        AppState.INCOMPATIBLE: "不兼容",
        AppState.AUTHENTICITY_FAILED: "真实性失败",
        AppState.READ_FAILED: "读取失败",
        AppState.DISCONNECTED: "已断开",
    }[state]


def _badge_style(state: AppState) -> str:
    if state is AppState.READY:
        return "statusReady"
    if state in {AppState.SCANNING, AppState.CONNECTING, AppState.READ_ONLY, AppState.MULTIPLE_DEVICES}:
        return "statusWarn"
    return "statusError"


def _connection_badge_text(model: ScreenModel) -> str:
    if (
        model.state in {AppState.READY, AppState.READ_ONLY}
        and model.snapshot is not None
    ):
        return "已认证" if model.snapshot.trust.is_authenticated else "开发设备"
    return _badge_text(model.state)


def _connection_badge_style(model: ScreenModel) -> str:
    if (
        model.state in {AppState.READY, AppState.READ_ONLY}
        and model.snapshot is not None
        and not model.snapshot.trust.is_authenticated
    ):
        return "statusWarn"
    return _badge_style(model.state)


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


def _firmware_transaction_title(state: FirmwareUpdateState) -> str:
    return {
        FirmwareUpdateState.IDLE: "尚未选择维护包",
        FirmwareUpdateState.PACKAGE_READY: "维护包已通过本地检查",
        FirmwareUpdateState.CHECKING: "正在读取设备更新状态",
        FirmwareUpdateState.NEEDS_ABORT: "发现另一份未完成更新",
        FirmwareUpdateState.BEGINNING: "正在建立设备接收事务",
        FirmwareUpdateState.TRANSFERRING: "正在传输固件镜像",
        FirmwareUpdateState.FINALIZING: "设备正在校验镜像",
        FirmwareUpdateState.WAITING_RECONNECT: "等待设备重启并重新连接",
        FirmwareUpdateState.VERIFYING: "正在核对新固件身份",
        FirmwareUpdateState.ABORTING: "正在中止接收事务",
        FirmwareUpdateState.PAUSED: "连接中断，等待对账续传",
        FirmwareUpdateState.COMPLETED: "固件维护已读回确认",
        FirmwareUpdateState.FAILED: "固件维护未完成",
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
        AppState.READ_FAILED: "设备身份已进入读取流程，但状态或配置没有完整返回。可重新扫描重试。",
        AppState.DISCONNECTED: "只读界面可保留最后一次快照；重新连接后需重新读取，不会自动写入。",
        AppState.READY: "设备和配置已同步。",
        AppState.READ_ONLY: "设备允许读取，但当前不允许写入。",
    }[state]
