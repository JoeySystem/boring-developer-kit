"""Shared Console UI components. Build pages from these instead of styling widgets by hand."""
from controller_config.views.ui.banner import Banner, BannerStack
from controller_config.views.ui.button import Button
from controller_config.views.ui.card import Card, CardHeader
from controller_config.views.ui.color_presets import ColorPresetRow
from controller_config.views.ui.device_status_capsule import DeviceStatusCapsule
from controller_config.views.ui.device_top_view import DeviceTopView
from controller_config.views.ui.dialog import (
    LARGE, MEDIUM, SMALL, XLARGE, Dialog, RoundCloseButton, confirm, dialog_button,
)
from controller_config.views.ui.direction_cross import DirectionCross, DirectionGlyph, direction_of
from controller_config.views.ui.disclosure import Disclosure
from controller_config.views.ui.dot_row import DotRow
from controller_config.views.ui.dot_slider import DotMatrixSlider
from controller_config.views.ui.empty_state import EmptyState
from controller_config.views.ui import notice
from controller_config.views.ui.menu import Menu
from controller_config.views.ui.language_keys import LanguageKey, LanguageKeys, LanguageSwitch
from controller_config.views.ui.orbital_mark import OrbitalMark
from controller_config.views.ui.progress_ring import ProgressRing, ProgressRingRow
from controller_config.views.ui.prompt_diagnostics import PromptDiagnostics, prompt_diagnostics_dialog
from controller_config.views.ui.prompt_status_bar import PromptStatusBar
from controller_config.views.ui.save_banner import SaveBanner
from controller_config.views.ui.segmented import SegmentedControl
from controller_config.views.ui.setting_row import SettingRow
from controller_config.views.ui.status_icon import StatusIcon
from controller_config.views.ui.step_number import StepNumber
from controller_config.views.ui.tag import Tag

__all__ = [
    "Banner", "BannerStack", "Button", "Card", "CardHeader", "ColorPresetRow", "DeviceStatusCapsule",
    "DeviceTopView", "Dialog", "DirectionCross", "DirectionGlyph", "Disclosure", "DotMatrixSlider", "DotRow",
    "EmptyState", "LARGE", "LanguageKey", "LanguageKeys", "LanguageSwitch", "MEDIUM", "Menu", "OrbitalMark", "ProgressRing", "ProgressRingRow", "PromptDiagnostics", "PromptStatusBar",
    "RoundCloseButton", "SMALL", "SaveBanner", "SegmentedControl", "SettingRow", "StatusIcon", "StepNumber", "Tag", "XLARGE",
    "confirm", "dialog_button", "notice", "direction_of", "prompt_diagnostics_dialog",
]
