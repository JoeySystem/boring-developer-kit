from __future__ import annotations

import sys
from importlib.resources import files

from PySide6.QtWidgets import QApplication, QWidget


# UI v4 handoff preview values, deliberately kept in one place until final
# brand colours are supplied. Native materials are not simulated by CSS blur.
V4_TOKENS = {
    "baseDark": "#1C1B19", "information": "#EFEAE0", "muted": "#9A958C",
    "faint": "#7E796F", "control": "#FF6A00", "signal": "#CC5200",
    "agent": "#9FB3EE", "ok": "#8FB98F", "surface1": "#232220",
    "surface2": "#2B2A27", "surface3": "#34322E", "focus": "#221F1B",
}
V4_RADIUS = 25

# Keep QCheckBox's native input and accessibility behavior; only skin its indicator.
TOGGLE_STYLE = """
QCheckBox { spacing: 9px; min-height: 28px; }
QCheckBox:disabled { color: #7E796F; }
QCheckBox::indicator {
    width: 38px; height: 22px;
    border: 2px solid transparent; border-radius: 13px;
    image: url("@ASSETS@/toggle-off.svg");
}
QCheckBox::indicator:checked { image: url("@ASSETS@/toggle-on.svg"); }
QCheckBox::indicator:disabled:unchecked { image: url("@ASSETS@/toggle-off-disabled.svg"); }
QCheckBox::indicator:disabled:checked { image: url("@ASSETS@/toggle-on-disabled.svg"); }
QCheckBox::indicator:focus { border-color: #FF9A52; }
""".replace("@ASSETS@", str(files("controller_config.assets")).replace("\\", "/"))

MACOS_GLASS_MENU_STYLE = """
QMenu {
    background-color: #242320;
    color: #EFEAE0;
    border: none;
    border-radius: 18px;
    padding: 6px;
}
QMenu::item {
    background: transparent;
    color: #EFEAE0;
    border-radius: 12px;
    padding: 9px 24px 9px 14px;
}
QMenu::item:disabled { color: #7E796F; }
QMenu::item:selected {
    background: #3A3833;
    color: #EFEAE0;
}
QMenu::separator {
    background: rgba(239, 234, 224, 26);
    height: 1px;
    margin: 5px 8px;
}
"""


V4_STYLE = """
QWidget { font-family: "HarmonyOS Sans SC", ".AppleSystemUIFont", "PingFang SC", sans-serif; color: #EFEAE0; font-size: 13px; }
QFrame#consoleFrame { background: #1C1B19; border: none; border-radius: 25px; }
QFrame#consoleFrame[nativeGlass="true"] { background: transparent; }
QFrame#card, QFrame#settingsRow, QFrame#deviceContextCard,
QFrame#bleSlotsCard, QFrame#selectionInspectorCard, QFrame#mappingEditorCard,
QFrame#syncSummaryCard, QFrame#bottomBar {
    background: #1C1B19; border: 1px solid rgba(239,234,224,16); border-radius: 25px;
}
QFrame[v4Role="focus"] { background: #221F1B; border: none; }
QFrame[v4Role="widget"] { background: #242320; }
QFrame[cardRole="primary"], QFrame[cardRole="secondary"] { background: #1C1B19; border: 1px solid rgba(239,234,224,16); border-radius: 25px; }
QFrame[cardRole="focus"] { background: #221F1B; border: none; border-radius: 25px; }
QFrame[cardRole="widget"] { background: #242320; border: 1px solid rgba(239,234,224,16); border-radius: 25px; }
QFrame#deviceWorkspace, QFrame#deviceStage { background: transparent; border: none; }
QFrame#navigationSeparator { background: rgba(239,234,224,41); border: none; }
QPushButton#navIconButton { min-width: 42px; max-width: 1000px; min-height: 40px; max-height: 40px; padding: 0 10px; border: none; }
QPushButton#settingsGroup { text-align: left; border: none; border-radius: 14px; }
QPushButton#settingsGroup[selected="true"] { background: #2B2A27; border-left: 3px solid #FF6A00; }
QPushButton#actionTab { background: transparent; color: #9A958C; border: none; border-radius: 20px; }
QPushButton#actionTab:checked { background: #EFEAE0; color: #1C1B19; border: none; }
/* Physical sizes belong to the device layout; selection repolish must not reset them. */
QPushButton#controlKey { padding: 0; border: none; }
QPushButton#controlKey[mapped="true"], QPushButton#controlKey[role="agent"], QPushButton#controlKey[role="agent"][mapped="true"], QPushButton#controlKey[selected="true"], QPushButton#controlKey[role="agent"][selected="true"], QPushButton#controlKey:hover, QPushButton#controlKey:focus { border: none; padding: 0; }
QPushButton#encoderControl { padding: 0; border: none; border-radius: 64px; }
QPushButton#joystickControl { padding: 0; border: none; border-radius: 26px; }
QPushButton#secondaryControl:hover, QPushButton#encoderControl:hover, QPushButton#joystickControl:hover,
QPushButton#secondaryControl[selected="true"], QPushButton#encoderControl[selected="true"], QPushButton#joystickControl[selected="true"] { border-color: #FF6A00; }
QLabel#syncChangeCount { font-size: 15px; }
QLabel#syncChangeCount[dirty="true"] { color: #CC5200; }
QLabel#pageTitle, QLabel#workspaceHeroTitle { font-family: "HarmonyOS Sans SC", ".AppleSystemUIFont", "PingFang SC", sans-serif; font-size: 22px; font-weight: 600; color: #EFEAE0; }
QLabel#muted, QLabel#bottomMuted, QLabel#devicePanelSubtitle, QLabel#canvasHint { color: #9A958C; }
QLabel#devicePanelKey, QLabel#eyebrow, QLabel#workspaceHeroCaption { color: #7E796F; }
QLabel#deviceAuthSummary[trust="authenticated"], QLabel#devicePanelValue[state="ready"], QLabel#devicePanelLive[connectionState="connected"] { color: #8FB98F; }
QLabel#devicePanelLive[connectionState="disconnected"] { color: #9A958C; }
QLabel#deviceConnectionSummary { color: #EFEAE0; font-size: 13px; font-weight: 700; }
QLabel#deviceAuthSummary[trust="development"] { color: #9A958C; }
QLabel#deviceAuthSummary[trust="untrusted"] { color: #CC5200; }
QPushButton#promptDirectionCard { min-width: 0; min-height: 38px; padding: 6px 10px; border: none; border-radius: 10px; background: transparent; text-align: left; }
QPushButton#promptPreviewDirection { min-width: 0; min-height: 0; padding: 0; border: none; border-radius: 16px; background: #514F46; color: #EFEAE0; }
QPushButton#promptPreviewDirection[selected="true"] { background: #EFEAE0; color: #242320; font-weight: 700; }
QPushButton#promptPreviewDirection:focus { border: 1px solid #EFEAE0; }
QPlainTextEdit#promptBodyEditor { background: #252420; border: none; border-radius: 22px; padding: 14px; }
QPlainTextEdit#promptEventLog { background: #171814; border: none; border-radius: 18px; padding: 12px; }
QLabel#promptSlotTitle { background: #2B2A27; border: none; border-radius: 10px; padding: 8px 12px; }
QPushButton#promptDirectionCard:hover { background: #34322E; border: 1px solid transparent; }
QPushButton#promptDirectionCard[selected="true"] { background: #34322E; border: 1px solid transparent; font-weight: 600; }
QPushButton#promptDirectionCard[selected="true"]:focus { border-color: #EFEAE0; }
QLabel#promptJoystickCenter { background: #232220; border: none; border-radius: 25px; }
QLabel#profilePendingState, QLabel#statusError { color: #CC5200; }
QPushButton { background: #2B2A27; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 20px; min-height: 24px; padding: 7px 16px; }
QPushButton:hover { background: #34322E; border-color: rgba(239,234,224,52); }
QPushButton:focus { border: 1px solid #EFEAE0; }
QPushButton:disabled { background: #232220; color: #7E796F; border-color: transparent; }
QPushButton#primary, QPushButton[buttonRole="primary"] { background: #FF6A00; color: #FFFFFF; border: 1px solid rgba(255,255,255,61); border-radius: 20px; font-weight: 600; }
QPushButton#primary:hover, QPushButton[buttonRole="primary"]:hover { background: #FF7A1A; }
QPushButton#primary:disabled, QPushButton[buttonRole="primary"]:disabled { background: #3B332E; color: #9A958C; border-color: transparent; }
QPushButton#secondary, QPushButton[buttonRole="secondary"], QPushButton#ghostOnDark { background: transparent; color: #EFEAE0; border: 1px solid rgba(239,234,224,71); border-radius: 20px; }
QPushButton#secondary:hover, QPushButton[buttonRole="secondary"]:hover, QPushButton#ghostOnDark:hover, QPushButton#profilePill:hover { border-color: rgba(239,234,224,71); }
QPushButton[buttonRole="ghost"] { background: transparent; color: #9A958C; border: none; border-radius: 20px; }
QPushButton#shortcutRecordButton, QPushButton#shortcutRecordButton[recording="true"] { background: #EFEAE0; color: #1C1B19; border: none; border-radius: 14px; min-height: 28px; padding: 0 13px; }
QPushButton#shortcutRecordButton:hover { background: #FFFFFF; }
QPushButton#settingsLanguageButton { background: #2B2A27; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 20px; min-width: 92px; max-width: 92px; min-height: 38px; max-height: 38px; padding: 0 16px; font-weight: 500; }
QPushButton#settingsLanguageButton:hover { background: #34322E; border-color: rgba(239,234,224,52); }
QPushButton#settingsLanguageButton[active="true"] { background: #EFEAE0; color: #1C1B19; border-color: #EFEAE0; font-weight: 600; }
QPushButton#settingsLanguageButton[active="true"]:hover { background: #FFFFFF; border-color: #FFFFFF; }
QPushButton#settingsLanguageButton:focus { border-color: #FF6A00; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox,
QFrame#mappingEditorCard QLineEdit, QFrame#mappingEditorCard QComboBox, QFrame#mappingEditorCard QSpinBox {
    background: #2B2A27; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 20px; min-height: 38px; padding: 0 16px; selection-background-color: #FF6A00;
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #EFEAE0; }
QComboBox, QFrame#mappingEditorCard QComboBox { padding-right: 36px; combobox-popup: 0; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 32px; border: none; background: transparent;
    border-top-right-radius: 19px; border-bottom-right-radius: 19px;
}
QComboBox::down-arrow { image: url("@ASSETS@/chevron-down.svg"); width: 12px; height: 8px; }
QComboBox QAbstractItemView {
    background: #242320; color: #EFEAE0;
    border: 1px solid #45423B; border-radius: 14px; padding: 6px;
    outline: none; selection-background-color: #3A3833; selection-color: #FF6A00;
}
QComboBox QAbstractItemView::item { min-height: 34px; padding: 0 12px; border-radius: 9px; }
QComboBox QAbstractItemView::item:selected { background: #3A3833; color: #FF6A00; }
QComboBox QAbstractItemView::item:disabled { color: #7E796F; }
QTextEdit, QPlainTextEdit, QListWidget { background: #232220; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 25px; padding: 12px 14px; selection-background-color: #FF6A00; }
QFrame#shortcutRecorder { background: #2B2A27; border: 1px solid rgba(239,234,224,26); border-radius: 20px; }
QFrame#deviceContextCard QPushButton#profilePill { background: #2B2A27; color: #EFEAE0; min-height: 38px; padding: 0 16px; border: 1px solid rgba(239,234,224,26); border-radius: 20px; }
QFrame#deviceContextCard QPushButton#saveProfileAsButton { background: transparent; color: #9A958C; border: none; }
QFrame#deviceContextCard QPushButton#bleSlotsDisclosure { background: transparent; color: #B6B0A5; border: none; min-height: 24px; padding: 4px 0; text-align: left; }
QFrame#deviceContextCard QPushButton#bleSlotsDisclosure:hover, QFrame#deviceContextCard QPushButton#bleSlotsDisclosure:focus { background: transparent; color: #EFEAE0; border: none; }
QDialog#bleSlotsDialog { background: transparent; border: none; }
QFrame#bleSlotsCard { background: #211F1A; border: none; border-radius: 24px; }
QFrame#bleSlotsCard QLabel#inspectorTitle { color: #EFEAE0; font-size: 18px; font-weight: 600; }
QFrame#bleComputerRow { background: #2B2A27; border: none; border-radius: 18px; }
QLabel#bleComputerName { color: #EFEAE0; font-size: 14px; font-weight: 600; }
QLabel#bleComputerStatus { color: #B6B0A5; font-size: 12px; }
QPushButton#bleComputerAction, QPushButton#bleComputerMore { min-height: 34px; padding: 0 12px; border-radius: 18px; }
QPushButton#bleComputerMore, QPushButton#bleConnectionHelp { background: transparent; border-color: transparent; color: #B6B0A5; }
QPushButton#bleComputerMore:focus, QPushButton#bleConnectionHelp:focus { border-color: #FF6A00; }
QLabel#bleConnectionMessage { background: #2B2A27; border-radius: 16px; padding: 12px; color: #EFEAE0; }
QPushButton#bleHelpTarget:checked { background: #EFEAE0; color: #1C1B19; }
QSlider#lightingBrightness::sub-page:horizontal { background: #EFEAE0; }
QSlider#lightingBrightness::handle:horizontal { background: #EFEAE0; border: none; }
QSlider#lightingBrightness::groove:horizontal { background: #34322E; }
QSlider::groove:horizontal { height: 6px; background: #34322E; border: none; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #EFEAE0; border-radius: 3px; }
QSlider::handle:horizontal { background: #EFEAE0; border: none; width: 18px; margin: -6px 0; border-radius: 9px; }
QLabel#displayBrightnessValue { background: #2B2A27; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 15px; min-width: 48px; padding: 7px 10px; font-weight: 700; }
QLabel#displayModuleTitle { color: #EFEAE0; font-size: 18px; font-weight: 700; }
QWidget#displayRotationControl { background: #2B2A27; border: 1px solid rgba(239,234,224,26); border-radius: 18px; }
QPushButton#displayRotationOption { background: transparent; color: #9A958C; border: none; border-radius: 14px; min-height: 30px; min-width: 0; padding: 0 8px; }
QPushButton#displayRotationOption:hover { background: #34322E; color: #EFEAE0; border: none; }
QPushButton#displayRotationOption:checked { background: #EFEAE0; color: #1C1B19; border: none; font-weight: 700; }
QProgressBar { background: #232220; border: none; border-radius: 6px; color: #EFEAE0; text-align: center; }
QProgressBar::chunk { background: #FF6A00; border-radius: 6px; }
QProgressBar[firmwareAction="true"] { min-height: 42px; border-radius: 20px; font-weight: 600; }
QProgressBar[firmwareAction="true"]::chunk { background: #FF6A00; border-radius: 20px; }
QScrollBar:vertical, QScrollArea#mappingEditorBody QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical, QScrollArea#mappingEditorBody QScrollBar::handle:vertical { background: #4A4740; border-radius: 4px; }
QToolTip { background: #1C1B19; color: #EFEAE0; border: 1px solid rgba(239,234,224,26); border-radius: 12px; padding: 6px 10px; }
QInputDialog, QMessageBox { background: #1C1B19; color: #EFEAE0; }
""".replace("@ASSETS@", str(files("controller_config.assets")).replace("\\", "/")) + MACOS_GLASS_MENU_STYLE + TOGGLE_STYLE


def install_macos_vibrancy(
    widget: QWidget,
    *,
    corner_radius: float,
) -> bool:
    """Place native glass behind Qt without replacing its window content view."""
    application = QApplication.instance()
    if (
        sys.platform != "darwin"
        or not isinstance(application, QApplication)
        or application.platformName() != "cocoa"
    ):
        return False

    try:
        import AppKit
        import objc

        if AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency():
            return False

        content_view = objc.objc_object(c_void_p=int(widget.winId()))
        window = content_view.window()
        container = content_view.superview()
        if window is None or container is None:
            return False
        installed_view = getattr(widget, "_boring_macos_content_view", None)
        installed_window = getattr(widget, "_boring_macos_window", None)
        installed_effect = getattr(widget, "_boring_macos_effect_view", None)
        if (
            installed_view == content_view
            and installed_window == window
            and installed_effect is not None
            and window.contentView() == content_view
            and installed_effect.superview() == container
        ):
            return True

        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            content_view.frame()
        )
        effect.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        effect.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setAppearance_(
            AppKit.NSAppearance.appearanceNamed_(
                AppKit.NSAppearanceNameVibrantDark
            )
        )
        effect.setWantsLayer_(True)
        layer = effect.layer()
        if layer is not None:
            layer.setCornerRadius_(corner_radius)
            layer.setMasksToBounds_(True)

        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(True)
        # Qt must remain NSWindow.contentView: otherwise Cocoa treats its
        # hide/show as a child-view operation and leaves an empty window behind.
        container.addSubview_positioned_relativeTo_(
            effect, AppKit.NSWindowBelow, content_view
        )
        widget._boring_macos_effect_view = effect
        widget._boring_macos_content_view = content_view
        widget._boring_macos_window = window
        return True
    except (AttributeError, ImportError, TypeError, ValueError):
        return False
