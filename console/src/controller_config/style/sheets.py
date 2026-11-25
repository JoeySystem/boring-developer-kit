"""The one stylesheet the main window applies, built from style/tokens.py.

`application_stylesheet()` = the remaining rules of the three old APP_STYLE
layers (moved here from views/main_window.py in step 3c of the style
foundation plan, order kept so the cascade is unchanged) + the device
silhouette rules + the V4 stylesheet from appearance.py.

Edit colours in tokens.py. The old rules below still carry their own sizes;
they shrink as the old pages are redrawn with the shared components.
"""
from __future__ import annotations

from controller_config.style.tokens import stylesheet

LEGACY_TEMPLATE = """
QMainWindow { background: transparent; }
QWidget#root { background: transparent; color: ${LEGACY_ROOT_COLOR}; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#windowChrome { background: transparent; border: none; }
QPushButton#windowControl { min-width: 12px; max-width: 12px; min-height: 12px; max-height: 12px; padding: 0; border-radius: 6px; }
QPushButton#windowControl[windowRole="close"] { background: ${LEGACY_WINDOW_CONTROL_CLOSE_BACKGROUND}; border: 1px solid ${LEGACY_WINDOW_CONTROL_CLOSE_BORDER}; }
QPushButton#windowControl[windowRole="minimize"] { background: ${LEGACY_WINDOW_CONTROL_MINIMIZE_BACKGROUND}; border: 1px solid ${LEGACY_WINDOW_CONTROL_MINIMIZE_BORDER}; }
QPushButton#windowControl[windowRole="zoom"] { background: ${LEGACY_WINDOW_CONTROL_ZOOM_BACKGROUND}; border: 1px solid ${LEGACY_WINDOW_CONTROL_ZOOM_BORDER}; }
QPushButton#windowControl:hover { border-color: ${LEGACY_WINDOW_CONTROL_HOVER_BORDER_COLOR}; }
QFrame#topNavigation { border: none; }
QFrame#navigationCapsule { border-radius: 22px; }
QPushButton#navIconButton { background: transparent; }
QPushButton#navIconButton:disabled { background: transparent; border-color: transparent; }
QLabel#shellBrand { color: ${LEGACY_SHELL_BRAND_COLOR}; }
QPushButton:hover { color: ${LEGACY_PUSH_BUTTON_HOVER_COLOR}; }
QFrame#topBar { border-radius: 0; }
QFrame#topMetric { background: transparent; border: none; }
QLabel#eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 1px; }
QLabel#headerValue { font-size: 14px; font-weight: 800; }
QFrame#modeNormal { border: 1px solid ${LEGACY_MODE_NORMAL_BORDER}; border-radius: 6px; }
QFrame#modeCodex { border: 1px solid ${LEGACY_MODE_CODEX_BORDER}; border-radius: 6px; }
QLabel#statusReady, QLabel#promptHelperOnlineState[listenerOnline="true"] { background: transparent; color: ${LEGACY_STATUS_READY_COLOR}; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QLabel#statusWarn, QLabel#promptHelperOnlineState[listenerOnline="false"] { background: transparent; color: ${LEGACY_STATUS_WARN_COLOR}; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QLabel#statusError { background: transparent; padding: 0 4px; border: none; border-radius: 0; font-weight: 650; }
QFrame#actionTabs { border-radius: 9px; }
QScrollArea#mappingEditorBody { background: transparent; border: none; }
QScrollArea#mappingEditorBody QScrollBar:vertical { border: none; }
QScrollArea#mappingEditorBody QScrollBar::add-line:vertical, QScrollArea#mappingEditorBody QScrollBar::sub-line:vertical { height: 0; }
QPushButton#profilePill { border: 1px solid ${LEGACY_PROFILE_PILL_BORDER}; padding: 6px 11px; font-size: 10px; font-weight: 750; }
QPushButton#saveProfileAsButton { padding: 6px 10px; border-radius: 10px; font-size: 10px; }
QLabel#profilePendingState { font-size: 9px; font-weight: 700; }
QLabel#inspectorTitle { font-size: 21px; font-weight: 800; }
QLabel#firmwareMaintenanceTitle { color: ${TEXT_PRIMARY}; font-size: ${FONT_ROW_TITLE}px; font-weight: 700; }
QLabel#appReleaseSummaryTitle { color: ${LEGACY_APP_RELEASE_SUMMARY_TITLE_COLOR}; font-size: 16px; font-weight: 800; }
QLabel#appReleaseHighlight { color: ${LEGACY_APP_RELEASE_HIGHLIGHT_COLOR}; font-size: 13px; }
QLabel#roleHuman { color: ${LEGACY_ROLE_HUMAN_COLOR}; background: ${LEGACY_ROLE_HUMAN_BACKGROUND}; padding: 8px; border-left: 3px solid ${LEGACY_ROLE_HUMAN_BORDER_LEFT}; }
QLabel#roleAgent { color: ${LEGACY_ROLE_AGENT_COLOR}; background: ${LEGACY_ROLE_AGENT_BACKGROUND}; padding: 8px; border-left: 3px solid ${LEGACY_ROLE_AGENT_BORDER_LEFT}; }
QLabel#roleContext { color: ${LEGACY_ROLE_CONTEXT_COLOR}; background: ${LEGACY_ROLE_CONTEXT_BACKGROUND}; padding: 8px; border-left: 3px solid ${LEGACY_ROLE_CONTEXT_BORDER_LEFT}; }
QFrame#roleHuman { border-left: 3px solid ${LEGACY_ROLE_HUMAN_BORDER_LEFT}; }
QFrame#roleAgent { border-left: 3px solid ${LEGACY_ROLE_AGENT_BORDER_LEFT}; }
QFrame#roleContext { border-left: 3px solid ${LEGACY_ROLE_CONTEXT_BORDER_LEFT}; }
QFrame#roleHuman QLabel { background: transparent; }
QFrame#roleAgent QLabel { background: transparent; }
QFrame#roleContext QLabel { background: transparent; }
QPushButton#primary, QPushButton[buttonRole="primary"] { padding: 10px 16px; }
QPushButton#secondary, QPushButton[buttonRole="secondary"] { padding: 9px 14px; font-weight: 650; }
QPushButton#secondary:hover, QPushButton[buttonRole="secondary"]:hover { color: ${LEGACY_PUSH_BUTTON_HOVER_COLOR}; }
QPushButton#promptDirectionCard { color: ${LEGACY_PROMPT_DIRECTION_CARD_COLOR}; }
QComboBox, QLineEdit, QSpinBox, QTextEdit, QPlainTextEdit, QListWidget { selection-color: ${LEGACY_PUSH_BUTTON_HOVER_COLOR}; min-width: 150px; }
QMenu { background: ${LEGACY_MENU_BACKGROUND}; }
QInputDialog QLabel { color: ${LEGACY_INPUT_DIALOG_COLOR}; }
QInputDialog QLineEdit { background: ${LEGACY_INPUT_DIALOG_BACKGROUND}; color: ${LEGACY_INPUT_DIALOG_COLOR}; }
QInputDialog QPushButton { background: ${LEGACY_INPUT_DIALOG_BACKGROUND_2}; color: ${LEGACY_INPUT_DIALOG_COLOR}; border-color: ${LEGACY_INPUT_DIALOG_BORDER_COLOR}; }
QDialog#onboardingDialog { background: transparent; }
QLabel#lightingLevelValue { font-weight: 750; }
QScrollBar:vertical { margin: 0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }

QFrame#navigationCapsule {
    border-color: ${LEGACY_NAVIGATION_CAPSULE_BORDER_COLOR};
}
QLabel#shellEdition { color: ${LEGACY_SHELL_EDITION_COLOR}; }
QFrame#settingsRow { border-bottom: 1px solid ${LEGACY_SETTINGS_ROW_BORDER_BOTTOM}; }
QFrame#topMetric { border-left: 1px solid ${LEGACY_TOP_METRIC_BORDER_LEFT}; }
QLabel#pageTitle,
QLabel#headerValue,
QLabel#inspectorTitle { color: ${LEGACY_INPUT_DIALOG_COLOR}; }
QFrame#modeNormal { background: ${LEGACY_MODE_NORMAL_BACKGROUND}; border-color: ${LEGACY_MODE_NORMAL_BORDER_COLOR}; }
QFrame#modeCodex { background: ${LEGACY_MODE_CODEX_BACKGROUND}; border-color: ${LEGACY_MODE_CODEX_BORDER_COLOR}; }
QLabel#statusReady[compactStatus="true"],
QLabel#statusWarn[compactStatus="true"],
QLabel#statusError[compactStatus="true"] {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 0 4px;
    font-weight: 650;
}
QLabel#statusReady[compactStatus="true"] { color: ${LEGACY_STATUS_READY_TRUE_COLOR}; }
QLabel#statusWarn[compactStatus="true"] { color: ${LEGACY_STATUS_WARN_COLOR}; }
QLabel#statusError[compactStatus="true"] { color: ${LEGACY_STATUS_ERROR_TRUE_COLOR}; }
QLabel#extensionPlatformStatus {
    background: transparent;
    color: ${LEGACY_WINDOW_CHROME_TITLE_COLOR};
    border: none;
    border-radius: 0;
    padding: 0;
    font-weight: 600;
}
QLabel#shortcutRecorderHeading {
    color: ${LEGACY_WINDOW_CHROME_TITLE_COLOR};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#shortcutRecorderPreview {
    color: ${LEGACY_PUSH_BUTTON_HOVER_COLOR};
    font-size: 22px;
    font-weight: 800;
}
QLabel#shortcutRecorderMessage {
    color: ${LEGACY_WINDOW_CHROME_TITLE_COLOR};
    font-size: 11px;
}
QPushButton#shortcutManualToggle {
    text-align: left;
}
QPushButton#secondary,
QPushButton[buttonRole="secondary"],
QPushButton#profilePill {
    color: ${LEGACY_INPUT_DIALOG_COLOR};
}
QPushButton#promptDirectionCard {
    border-color: ${LEGACY_NAVIGATION_CAPSULE_BORDER_COLOR};
}
QLabel#lightingLevelValue { color: ${LEGACY_INPUT_DIALOG_COLOR}; }
QMessageBox {
    background-color: ${LEGACY_MESSAGE_BOX_BACKGROUND_COLOR};
}
QMessageBox QLabel {
    background: transparent;
    color: ${LEGACY_INPUT_DIALOG_COLOR};
}
QMessageBox QPushButton {
    min-width: 76px;
    background: ${LEGACY_MESSAGE_BOX_BACKGROUND};
    color: ${LEGACY_INPUT_DIALOG_COLOR};
    border: 1px solid ${LEGACY_MESSAGE_BOX_BORDER};
}
QMessageBox QPushButton:hover {
    background: ${LEGACY_MESSAGE_BOX_HOVER_BACKGROUND};
    border-color: ${LEGACY_MESSAGE_BOX_HOVER_BORDER_COLOR};
}

QFrame#topNavigation { background: transparent; }
QFrame#navigationCapsule {
    background: transparent;
    border: none;
}
QPushButton#navIconButton {
    font-weight: 600;
    color: ${LEGACY_NAV_ICON_BUTTON_COLOR};
    border-radius: 22px;
}
QPushButton#navIconButton:hover {
    background: transparent;
    border-color: transparent;
}
QPushButton#navIconButton[active="true"] {
    background: transparent;
    border-color: transparent;
    color: ${LEGACY_NAV_ICON_BUTTON_TRUE_COLOR};
}
QFrame#topBar {
    background: transparent;
    border: none;
    border-bottom: 1px solid ${LEGACY_TOP_BAR_BORDER_BOTTOM};
}
QFrame#deviceWorkspace {
    border-radius: 0;
}
QLabel#devicePanelName {
    color: ${LEGACY_DEVICE_PANEL_NAME_COLOR};
    font-size: 28px;
    font-weight: 760;
}
QLabel#devicePanelKey {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QLabel#devicePanelValue {
    color: ${LEGACY_DEVICE_PANEL_VALUE_COLOR};
    font-size: 11px;
    font-weight: 700;
}
QLabel#devicePanelLive {
    font-size: 10px;
    font-weight: 750;
    letter-spacing: 1px;
}
QFrame#deviceStatusRow {
    background: transparent;
    border: none;
    border-top: 1px solid ${LEGACY_DEVICE_STATUS_ROW_BORDER_TOP};
}
QFrame#deviceContextCard QFrame#topMetric {
    background: transparent;
    border: none;
}
QFrame#deviceContextCard QPushButton#profilePill {
    text-align: left;
}
QFrame#deviceContextCard QPushButton#saveProfileAsButton {
    padding: 5px 2px;
    text-align: left;
}
QLabel#deviceStageName {
    color: ${LEGACY_DEVICE_STAGE_NAME_COLOR};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1px;
}
QFrame#roleHuman,
QFrame#roleAgent,
QFrame#roleContext {
    background: transparent;
    border: none;
    border-top: 1px solid ${LEGACY_ROLE_HUMAN_BORDER_TOP};
}
QFrame#roleHuman QLabel,
QFrame#roleAgent QLabel,
QFrame#roleContext QLabel { color: ${LEGACY_ROLE_HUMAN_COLOR_2}; }
QFrame#mappingEditorCard QLineEdit:focus,
QFrame#mappingEditorCard QComboBox:focus,
QFrame#mappingEditorCard QSpinBox:focus {
    border-color: ${ACCENT_FOCUS};
}
QPushButton#shortcutRecordButton:focus { border-color: ${ACCENT}; }
QPushButton#shortcutRecordButton[recording="true"] {
    border-color: ${ACCENT_FOCUS};
}
QPushButton#openOnboardingGuide {
    background: ${LEGACY_OPEN_ONBOARDING_GUIDE_BACKGROUND};
    border: 1px solid ${LEGACY_OPEN_ONBOARDING_GUIDE_BORDER};
    border-radius: 12px;
}
QPushButton#openOnboardingGuide[textEntry="false"] {
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
}
QPushButton#openOnboardingGuide:hover {
    background: ${LEGACY_OPEN_ONBOARDING_GUIDE_HOVER_BACKGROUND};
    border-color: ${LEGACY_OPEN_ONBOARDING_GUIDE_HOVER_BORDER_COLOR};
}
QPushButton#openOnboardingGuide:pressed {
    background: ${LEGACY_OPEN_ONBOARDING_GUIDE_PRESSED_BACKGROUND};
    border-color: ${LEGACY_OPEN_ONBOARDING_GUIDE_PRESSED_BORDER_COLOR};
}
QPushButton#bleSlotsDisclosure {
    background: transparent;
    color: ${LEGACY_BLE_SLOTS_DISCLOSURE_COLOR};
    border: 1px solid transparent;
    border-radius: 5px;
    min-height: 22px;
    padding: 2px 4px;
    font-weight: 400;
}
QPushButton#bleSlotsDisclosure:hover {
    background: ${LEGACY_BLE_SLOTS_DISCLOSURE_HOVER_BACKGROUND};
    color: ${LEGACY_BLE_SLOTS_DISCLOSURE_HOVER_COLOR};
}
QPushButton#bleSlotsDisclosure:focus { border-color: ${ACCENT_FOCUS}; }
QPushButton#primary,
QPushButton[buttonRole="primary"] {
    border-color: ${LEGACY_PRIMARY_BORDER_COLOR};
}
QPushButton#secondary,
QPushButton[buttonRole="secondary"],
QPushButton#profilePill {
    background: ${LEGACY_OPEN_ONBOARDING_GUIDE_BACKGROUND};
    border-color: ${LEGACY_SECONDARY_BORDER_COLOR};
    border-radius: 12px;
}
QFrame#mappingEditorCard QPushButton[buttonRole],
QFrame#syncSummaryCard QPushButton {
    min-height: 24px;
    padding: 8px 14px;
    border-radius: 20px;
}
QFrame#card,
QFrame#actionTabs {
    background: ${LEGACY_CARD_BACKGROUND};
    border: none;
}
QPushButton#configurationModeTab:checked {
    background: ${LEGACY_CONFIGURATION_MODE_TAB_CHECKED_BACKGROUND};
    border-color: ${LEGACY_CONFIGURATION_MODE_TAB_CHECKED_BORDER_COLOR};
    color: ${LEGACY_PUSH_BUTTON_HOVER_COLOR};
}
QGroupBox {
    background: ${LEGACY_GROUP_BOX_BACKGROUND};
    border: none;
    border-radius: 16px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
}
QGroupBox::title {
    color: ${LEGACY_GROUP_BOX_TITLE_COLOR};
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}
QScrollBar::handle:vertical,
QScrollArea#mappingEditorBody QScrollBar::handle:vertical {
    min-height: 32px;
}
"""

LEGACY_STYLE = stylesheet(LEGACY_TEMPLATE)

# Shared components in views/ui/ (selectors only match their own properties).
COMPONENT_TEMPLATE = """
QFrame#deviceStatusCapsule QLabel#deviceAuthSummary { background: transparent; color: ${TEXT_SECONDARY};
    font-size: ${FONT_BODY}px; font-weight: 600; }
QFrame#deviceStatusCapsule QLabel#deviceAuthSummary[cream="true"] { color: ${TEXT_PRIMARY}; }
QDialog[boringDialog="true"] { background: transparent; }
QDialog[boringDialog="true"] QScrollArea, QDialog[boringDialog="true"] QWidget#boringDialogBody { background: transparent; border: none; }
QDialog[boringDialog="true"] QLabel { background: transparent; color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; }
QDialog[boringDialog="true"] QLabel#boringDialogTitle { font-size: ${FONT_MODULE_TITLE}px; font-weight: 600; }
QDialog[boringDialog="true"] QLabel#boringDialogStep { color: ${TEXT_SECONDARY}; font-size: ${FONT_CAPTION}px; }
QDialog[boringDialog="true"] QLabel[tone="secondary"] { color: ${TEXT_SECONDARY}; }
QDialog[boringDialog="true"] QLabel[tone="warning"] { color: ${ACCENT}; font-size: ${FONT_CAPTION}px; }
QDialog#otherActionDialog QLabel[tone="secondary"] { font-size: ${FONT_CAPTION}px; }
QPushButton[otherActionChip="true"] { background: transparent; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_STRONG};
    border-radius: ${RADIUS_CHIP}px; min-height: ${CONTROL_HEIGHT_SMALL}px; max-height: ${CONTROL_HEIGHT_SMALL}px;
    padding: 0 14px; font-size: ${FONT_BODY}px; }
QPushButton[otherActionChip="true"]:hover { background: ${SURFACE_3}; }
QPushButton[otherActionChip="true"]:checked { background: ${TEXT_PRIMARY}; color: ${SURFACE_BASE}; border-color: ${TEXT_PRIMARY};
    font-weight: 600; }
QFrame#otherActionNotice, QFrame#voiceWarningBox { background: ${NOTICE_WARN_FILL}; border: none; border-radius: ${RADIUS_NOTICE}px; }
QFrame#voiceStatusRow, QFrame#voiceHelpBox { background: ${SURFACE_2}; border: none; border-radius: ${RADIUS_NOTICE}px; }
QLabel#voiceStatusTitle, QLabel#voiceHelpLead { font-weight: 600; }
QDialog#voiceSetupDialog QLabel#voiceStatusNote, QFrame#voiceHelpBox QLabel[tone="secondary"] { font-size: ${FONT_CAPTION}px; }
QFrame#otherActionPreview { background: ${SURFACE_2}; border: none; border-radius: ${RADIUS_NOTICE}px; }
QLabel#otherActionPreviewKeys, QLabel#otherActionEmptyTitle { font-weight: 600; }
QDialog#otherActionDialog QLineEdit { background: ${SURFACE_2}; color: ${TEXT_PRIMARY}; border: 1px solid transparent;
    border-radius: ${RADIUS_CONTROL}px; min-height: ${CONTROL_HEIGHT}px; padding: 0 14px; }
QDialog#otherActionDialog QLineEdit[invalid="true"] { border: 1px solid ${ACCENT}; }
QPushButton[boringButton] { min-height: ${CONTROL_HEIGHT}px; max-height: ${CONTROL_HEIGHT}px; min-width: 88px; padding: 0 18px;
    border-radius: ${RADIUS_CONTROL}px; font-size: ${FONT_BODY}px; }
QPushButton[boringButtonSize="small"] { min-height: ${CONTROL_HEIGHT_SMALL}px; max-height: ${CONTROL_HEIGHT_SMALL}px;
    min-width: 0; padding: 0 14px; border-radius: ${RADIUS_CONTROL_SMALL}px; }
QPushButton[boringButton="primary"] { background: ${ACCENT}; color: ${WHITE}; border: none; font-weight: 600; }
QPushButton[boringButton="primary"]:hover { background: ${ACCENT_HOVER}; }
QPushButton[boringButton="secondary"] { background: transparent; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_STRONG}; }
QPushButton[boringButton="secondary"]:hover { background: ${SURFACE_3}; }
QPushButton[boringButton="text"] { background: transparent; color: ${TEXT_SECONDARY}; border: none; min-width: 0; padding: 0 6px; }
QPushButton[boringButton="text"]:hover, QPushButton[boringButton="text"]:checked { color: ${TEXT_PRIMARY}; }
QPushButton[boringButton="cream"] { background: ${TEXT_PRIMARY}; color: ${SURFACE_BASE}; border: none; font-weight: 600; }
/* The round arrow on a settings tile paints its own 32 disc: the generic button's
   min-height and padding stretched it to 32 × 40, an oval (lighting page review). */
QPushButton[roundArrow="true"] { min-height: ${ROUND_ARROW_SIZE}px; max-height: ${ROUND_ARROW_SIZE}px;
    min-width: ${ROUND_ARROW_SIZE}px; max-width: ${ROUND_ARROW_SIZE}px; padding: 0px; border: none; }
QPushButton[boringButton]:disabled { background: ${SURFACE_1}; color: ${TEXT_TERTIARY}; border-color: transparent; }
QPushButton[boringButton]:focus { border: 1px solid ${ACCENT_FOCUS}; }
QFrame[boringBanner="true"] QLabel#bannerText, QFrame[boringBanner="true"] QLabel#preferencesSyncSummary { background: transparent; color: ${TEXT_PRIMARY};
    font-size: ${FONT_ROW_TITLE}px; font-weight: 600; }
QPushButton[bannerButton="true"] { min-height: 34px; max-height: 34px; min-width: 0; padding: 0 14px;
    border-radius: 17px; }
QPushButton#bannerClose { min-height: 0; max-height: 32px; min-width: 0; max-width: 32px; padding: 0;
    border: none; background: transparent; }
QFrame#lightingSyncCard QPushButton[bannerButton="true"]:disabled { background: transparent; color: ${TEXT_TERTIARY};
    border: 1px solid ${BORDER_CONTROL}; }
QFrame[segmented="true"] { background: ${SURFACE_2}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_SEGMENT}px; }
QPushButton[segmentOption="true"] { background: transparent; color: ${TEXT_SECONDARY}; border: none;
    border-radius: ${RADIUS_SEGMENT_OPTION}px; min-height: ${SEGMENT_OPTION_HEIGHT}px; min-width: 0; padding: 0 8px; }
QFrame[segmented="true"][compact="true"] { border-radius: ${RADIUS_SEGMENT_COMPACT}px; }
QFrame[segmented="true"][compact="true"] QPushButton[segmentOption="true"] { min-height: ${SEGMENT_OPTION_HEIGHT_COMPACT}px;
    border-radius: ${RADIUS_SEGMENT_OPTION_COMPACT}px; }
QPushButton[segmentOption="true"]:hover { background: ${SURFACE_3}; color: ${TEXT_PRIMARY}; border: none; }
QPushButton[segmentOption="true"]:checked { background: ${TEXT_PRIMARY}; color: ${SURFACE_BASE}; border: none; font-weight: 700; }
QPushButton[segmentOption="true"]:focus { border: 1px solid ${ACCENT_FOCUS}; }
QLabel#boringTag { background: transparent; color: ${TEXT_SECONDARY}; font-size: ${FONT_TAG}px; font-weight: 500;
    border: 1px solid ${WALL_TAG_BORDER}; border-radius: ${RADIUS_TAG}px; padding: 0 6px; }
QLabel#cardTitle { background: transparent; color: ${TEXT_PRIMARY}; font-size: ${FONT_EMPHASIS}px; font-weight: 600; }
QLabel#settingRowTitle { background: transparent; color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; font-weight: 400; }
QLabel#settingRowNote, QLabel#emptyStateText { background: transparent; color: ${TEXT_SECONDARY}; font-size: ${FONT_CAPTION}px; font-weight: 400; }
QLabel#emptyStateText { font-size: ${FONT_BODY}px; }
QWidget[progressRingRow="true"] QLabel#progressRingText { background: transparent; color: ${TEXT_PRIMARY};
    font-size: ${FONT_ROW_TITLE}px; font-weight: 600; }
QLabel#calibrationRawAxis, QLabel#calibrationRawValue { background: transparent; color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; }
QLabel#colorPresetLabel { background: transparent; color: ${WALL_TEXT}; font-size: ${FONT_BODY}px; font-weight: 600; }
QFrame#promptStatusBar { background: ${WALL_TILE}; border: 1px solid ${BORDER_SUBTLE}; border-radius: ${RADIUS_STATUS_BAR}px; }
QFrame#promptStatusBar QLabel { background: transparent; border: none; }
QFrame#promptStatusBar QLabel#promptStatusWord { color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; font-weight: 600; }
QFrame#promptStatusBar QLabel#promptStatusWord[quiet="true"] { color: ${TEXT_SECONDARY}; font-weight: 400; }
QFrame#promptStatusBar QLabel#promptStatusText { color: ${TEXT_SECONDARY}; font-size: ${FONT_BODY}px; }
QFrame#promptStatusBar QFrame#promptStatusDivider { background: ${BORDER_CONTROL}; border: none; }
QFrame#promptStatusBar QPushButton[boringButton] { min-height: ${STATUS_BAR_PILL_INNER}px; max-height: ${STATUS_BAR_PILL_INNER}px;
    min-width: 0; padding: 0 12px; border-radius: ${RADIUS_STATUS_BAR_PILL}px; font-size: ${FONT_BODY}px; }
QFrame#promptStatusBar QPushButton[boringButton="primary"] { border: 1px solid transparent; }
QFrame#promptStatusBar QPushButton[statusBarRound="true"] { min-height: ${STATUS_BAR_ROUND_INNER}px; max-height: ${STATUS_BAR_ROUND_INNER}px;
    min-width: ${STATUS_BAR_ROUND_INNER}px; max-width: ${STATUS_BAR_ROUND_INNER}px; padding: 0; background: transparent;
    border: 1px solid ${BORDER_STRONG}; border-radius: ${RADIUS_STATUS_BAR_ROUND}px; }
QFrame#promptStatusBar QPushButton[statusBarRound="true"]:hover { background: ${SURFACE_3}; }
QFrame#promptStatusBar QPushButton[statusBarRound="true"]:focus { border: 1px solid ${ACCENT_FOCUS}; }
QMenu[boringMenu="true"] { background: transparent; border: none; border-radius: 0; margin: 0;
    padding: ${MENU_PADDING}px; font-size: ${FONT_BODY}px; }
QMenu[boringMenu="true"]::item { background: transparent; border: none; border-radius: 0; margin: 0;
    min-height: ${MENU_ITEM_HEIGHT}px; max-height: ${MENU_ITEM_HEIGHT}px; padding: 0 ${MENU_ITEM_PADDING_RIGHT}px 0 ${MENU_ITEM_PADDING}px; }
QMenu[boringMenu="true"]::item:selected { background: transparent; }
QMenu[boringMenu="true"]::separator { background: transparent; height: 1px; margin: ${MENU_PADDING}px 0; }
QMenu[boringMenu="true"]::right-arrow { image: none; width: 0; }
QWidget#promptDiagnostics QLabel { background: transparent; border: none; color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; }
QWidget#promptDiagnostics QLabel[diagRole="name"] { color: ${TEXT_SECONDARY}; }
QWidget#promptDiagnostics QLabel[diagRole="note"] { color: ${TEXT_SECONDARY}; font-size: ${FONT_CAPTION}px; }
QWidget#promptDiagnostics QLabel[diagRole="heading"] { font-weight: 600; }
QWidget#promptDiagnostics QLabel[diagRole="body"] { color: ${TEXT_SECONDARY}; }
QWidget#promptDiagnostics QFrame#promptDiagnosticsRule { background: ${BORDER_SUBTLE}; border: none; }
QWidget#promptDiagnostics QPlainTextEdit#promptDiagnosticsLog { background: ${SURFACE_EVENT_LOG}; color: ${TEXT_PRIMARY}; border: none;
    border-radius: ${RADIUS_DIAGNOSTICS_LOG}px; padding: 8px 10px; font-family: ${FONT_FAMILY_MONO}; font-size: ${FONT_CAPTION}px; }
"""

COMPONENT_STYLE = stylesheet(COMPONENT_TEMPLATE)


# Appended to the Prompt page's own sheet (after the settings wall's WALL_STYLE):
# a widget's own sheet wins over the main window's, so these cannot live above.
PROMPT_PAGE_TEMPLATE = """
QPushButton#writePromptDevice { min-height: ${PROMPT_SAVE_INNER}px; max-height: ${PROMPT_SAVE_INNER}px; padding: 0 18px;
    border-radius: ${RADIUS_CONTROL}px; }
QPushButton#writePromptDevice:disabled { background: ${PROMPT_SAVE_OFF}; border-color: ${PROMPT_SAVE_OFF};
    color: ${PROMPT_SAVE_OFF_TEXT}; }
"""

PROMPT_PAGE_STYLE = stylesheet(PROMPT_PAGE_TEMPLATE)

def application_stylesheet() -> str:
    """Everything the main window shows, in cascade order."""
    from controller_config.appearance import V4_STYLE
    from controller_config.views.device_silhouette import DEVICE_SILHOUETTE_STYLE
    return LEGACY_STYLE + DEVICE_SILHOUETTE_STYLE + V4_STYLE + COMPONENT_STYLE
