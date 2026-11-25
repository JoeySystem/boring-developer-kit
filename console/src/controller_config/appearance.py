from __future__ import annotations

import sys
from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


from controller_config.style import tokens
from controller_config.style.tokens import stylesheet

# Older names for the shared tokens; painted widgets and tests still use them.
# The values live in style/tokens.py. Native materials are not simulated by CSS blur.
V4_TOKENS = {
    "baseDark": tokens.SURFACE_BASE, "information": tokens.TEXT_PRIMARY, "muted": tokens.TEXT_SECONDARY,
    "faint": tokens.TEXT_TERTIARY, "control": tokens.ACCENT, "signal": tokens.SIGNAL,
    "agent": tokens.AGENT, "ok": tokens.STATUS_OK, "surface1": tokens.SURFACE_1,
    "surface2": tokens.SURFACE_2, "surface3": tokens.SURFACE_3, "focus": tokens.SURFACE_FOCUS,
}
V4_RADIUS = tokens.RADIUS_CARD
# Centre of the macOS close button, in points from the window's top-left corner.
NATIVE_WINDOW_CONTROLS_CENTER = (23.0, 21.0)

# Keep QCheckBox's native input and accessibility behavior; only skin its indicator.
TOGGLE_STYLE = stylesheet("""
QCheckBox { spacing: 9px; min-height: 28px; }
QCheckBox:disabled { color: ${TEXT_TERTIARY}; }
QCheckBox::indicator {
    width: 38px; height: 22px;
    border: 2px solid transparent; border-radius: 13px;
    image: url("@ASSETS@/toggle-off.svg");
}
QCheckBox::indicator:checked { image: url("@ASSETS@/toggle-on.svg"); }
QCheckBox::indicator:disabled:unchecked { image: url("@ASSETS@/toggle-off-disabled.svg"); }
QCheckBox::indicator:disabled:checked { image: url("@ASSETS@/toggle-on-disabled.svg"); }
QCheckBox::indicator:focus { border-color: ${ACCENT_FOCUS}; }
""").replace("@ASSETS@", str(files("controller_config.assets")).replace("\\", "/"))

MACOS_GLASS_MENU_STYLE = stylesheet("""
QMenu {
    background-color: ${SURFACE_WIDGET};
    color: ${TEXT_PRIMARY};
    border: none;
    border-radius: 18px;
    padding: 6px;
}
QMenu::item {
    background: transparent;
    color: ${TEXT_PRIMARY};
    border-radius: 12px;
    padding: 9px 24px 9px 14px;
}
QMenu::item:disabled { color: ${TEXT_TERTIARY}; }
QMenu::item:selected {
    background: ${SURFACE_SELECTED};
    color: ${TEXT_PRIMARY};
}
QMenu::separator {
    background: ${BORDER_CONTROL};
    height: 1px;
    margin: 5px 8px;
}
""")


V4_STYLE = stylesheet("""
QWidget { font-family: ${FONT_FAMILY}; color: ${TEXT_PRIMARY}; font-size: ${FONT_BODY}px; }
QFrame#consoleFrame { background: ${SURFACE_BASE}; border: none; border-radius: ${RADIUS_CARD}px; }
QFrame#consoleFrame[nativeGlass="true"] { background: transparent; }
QFrame#consoleFrame[squareCorners="true"] { border-radius: 0px; }
QFrame#card, QFrame#settingsRow, QFrame#deviceContextCard,
QFrame#selectionInspectorCard, QFrame#mappingEditorCard,
QFrame#syncSummaryCard {
    background: ${SURFACE_BASE}; border: 1px solid ${BORDER_SUBTLE}; border-radius: ${RADIUS_CARD}px;
}
QFrame[v4Role="focus"] { background: ${SURFACE_FOCUS}; border: none; }
QFrame[v4Role="widget"] { background: ${SURFACE_WIDGET}; }
QFrame[cardRole="primary"], QFrame[cardRole="secondary"] { background: ${SURFACE_BASE}; border: 1px solid ${BORDER_SUBTLE}; border-radius: ${RADIUS_CARD}px; }
QFrame[cardRole="focus"] { background: ${SURFACE_FOCUS}; border: none; border-radius: ${RADIUS_CARD}px; }
QFrame[cardRole="widget"] { background: ${SURFACE_WIDGET}; border: 1px solid ${BORDER_SUBTLE}; border-radius: ${RADIUS_CARD}px; }
QFrame#deviceWorkspace, QFrame#deviceStage { background: transparent; border: none; }
QFrame#navigationSeparator { background: ${DIVIDER}; border: none; }
QPushButton#navIconButton { min-width: 42px; max-width: 1000px; min-height: 40px; max-height: 40px; padding: 0 10px; border: none; }
/* Physical sizes belong to the device layout; selection repolish must not reset them. */
QPushButton#controlKey { padding: 0; border: none; }
QPushButton#controlKey[mapped="true"], QPushButton#controlKey[role="agent"], QPushButton#controlKey[role="agent"][mapped="true"], QPushButton#controlKey[selected="true"], QPushButton#controlKey[role="agent"][selected="true"], QPushButton#controlKey:hover, QPushButton#controlKey:focus { border: none; padding: 0; }
QPushButton#encoderControl { padding: 0; border: none; border-radius: 64px; }
QPushButton#joystickControl { padding: 0; border: none; border-radius: 26px; }
QPushButton#secondaryControl:hover, QPushButton#encoderControl:hover, QPushButton#joystickControl:hover,
QPushButton#secondaryControl[selected="true"], QPushButton#encoderControl[selected="true"], QPushButton#joystickControl[selected="true"] { border-color: ${ACCENT}; }
QLabel#syncChangeCount { font-size: ${FONT_EMPHASIS}px; }
QLabel#syncChangeCount[dirty="true"] { color: ${SIGNAL}; }
QLabel#pageTitle { font-family: ${FONT_FAMILY}; font-size: ${FONT_PAGE_TITLE}px; font-weight: 600; color: ${TEXT_PRIMARY}; }
QLabel#muted, QLabel#devicePanelSubtitle { color: ${TEXT_SECONDARY}; }
QLabel#devicePanelKey, QLabel#eyebrow { color: ${TEXT_TERTIARY}; }
QLabel#devicePanelValue[state="ready"], QLabel#devicePanelLive { color: ${STATUS_OK}; }
QPushButton#promptDirectionCard { min-width: 0; min-height: 38px; padding: 6px 10px; border: none; border-radius: 10px; background: transparent; text-align: left; }
QPushButton#promptPreviewDirection { min-width: 0; min-height: 0; padding: 0; border: none; border-radius: 16px; background: ${SURFACE_PROMPT_DIRECTION}; color: ${TEXT_PRIMARY}; }
QPushButton#promptPreviewDirection[selected="true"] { background: ${TEXT_PRIMARY}; color: ${SURFACE_WIDGET}; font-weight: 700; }
QPushButton#promptPreviewDirection:focus { border: 1px solid ${TEXT_PRIMARY}; }
QPlainTextEdit#promptBodyEditor { background: ${SURFACE_PROMPT_EDITOR}; border: none; border-radius: 22px; padding: 14px; }
QPlainTextEdit#promptEventLog { background: ${SURFACE_EVENT_LOG}; border: none; border-radius: 18px; padding: 12px; }
QLabel#promptSlotTitle { background: ${SURFACE_2}; border: none; border-radius: 10px; padding: 8px 12px; }
QPushButton#promptDirectionCard:hover { background: ${SURFACE_3}; border: 1px solid transparent; }
QPushButton#promptDirectionCard[selected="true"] { background: ${SURFACE_3}; border: 1px solid transparent; font-weight: 600; }
QPushButton#promptDirectionCard[selected="true"]:focus { border-color: ${TEXT_PRIMARY}; }
QLabel#profilePendingState, QLabel#statusError { color: ${SIGNAL}; }
QPushButton { background: ${SURFACE_2}; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_CONTROL}px; min-height: 24px; padding: 7px 16px; }
QPushButton:hover { background: ${SURFACE_3}; border-color: ${BORDER_CONTROL_HOVER}; }
QPushButton:focus { border: 1px solid ${TEXT_PRIMARY}; }
QPushButton:disabled { background: ${SURFACE_1}; color: ${TEXT_TERTIARY}; border-color: transparent; }
QPushButton#primary, QPushButton[buttonRole="primary"] { background: ${ACCENT}; color: ${WHITE}; border: 1px solid ${BORDER_ON_ACCENT}; border-radius: ${RADIUS_CONTROL}px; font-weight: 600; }
QPushButton#primary:hover, QPushButton[buttonRole="primary"]:hover { background: ${ACCENT_HOVER}; }
QPushButton#primary:disabled, QPushButton[buttonRole="primary"]:disabled { background: ${SURFACE_ACCENT_DISABLED}; color: ${TEXT_SECONDARY}; border-color: transparent; }
QPushButton#secondary, QPushButton[buttonRole="secondary"] { background: transparent; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_STRONG}; border-radius: ${RADIUS_CONTROL}px; }
QPushButton#secondary:hover, QPushButton[buttonRole="secondary"]:hover, QPushButton#profilePill:hover { border-color: ${BORDER_STRONG}; }
QPushButton[buttonRole="ghost"] { background: transparent; color: ${TEXT_SECONDARY}; border: none; border-radius: ${RADIUS_CONTROL}px; }
QPushButton#shortcutRecordButton, QPushButton#shortcutRecordButton[recording="true"] { background: ${TEXT_PRIMARY}; color: ${SURFACE_BASE}; border: none; border-radius: 14px; min-height: 28px; padding: 0 13px; }
QPushButton#shortcutRecordButton:hover { background: ${WHITE}; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox,
QFrame#mappingEditorCard QLineEdit, QFrame#mappingEditorCard QComboBox, QFrame#mappingEditorCard QSpinBox {
    background: ${SURFACE_2}; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_CONTROL}px; min-height: 38px; padding: 0 16px; selection-background-color: ${ACCENT};
}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: ${TEXT_PRIMARY}; }
QComboBox, QFrame#mappingEditorCard QComboBox { padding-right: 36px; combobox-popup: 0; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 32px; border: none; background: transparent;
    border-top-right-radius: 19px; border-bottom-right-radius: 19px;
}
QComboBox::down-arrow { image: url("@ASSETS@/chevron-down.svg"); width: 12px; height: 8px; }
QComboBox QAbstractItemView {
    background: ${SURFACE_WIDGET}; color: ${TEXT_PRIMARY};
    border: 1px solid ${BORDER_POPUP}; border-radius: 14px; padding: 6px;
    outline: none; selection-background-color: ${SURFACE_SELECTED}; selection-color: ${ACCENT};
}
QComboBox QAbstractItemView::item { min-height: 34px; padding: 0 12px; border-radius: 9px; }
QComboBox QAbstractItemView::item:selected { background: ${SURFACE_SELECTED}; color: ${ACCENT}; }
QComboBox QAbstractItemView::item:disabled { color: ${TEXT_TERTIARY}; }
QTextEdit, QPlainTextEdit, QListWidget { background: ${SURFACE_1}; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_CARD}px; padding: 12px 14px; selection-background-color: ${ACCENT}; }
QFrame#shortcutRecorder { background: ${SURFACE_2}; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_CONTROL}px; }
QFrame#deviceContextCard QPushButton#profilePill { background: ${SURFACE_2}; color: ${TEXT_PRIMARY}; min-height: 38px; padding: 0 16px; border: 1px solid ${BORDER_CONTROL}; border-radius: ${RADIUS_CONTROL}px; }
QFrame#deviceContextCard QPushButton#saveProfileAsButton { background: transparent; color: ${TEXT_SECONDARY}; border: none; }
QDialog#bleSlotsDialog { background: transparent; border: none; }
QFrame#bleComputerRow { background: ${SURFACE_2}; border: none; border-radius: 18px; }
QLabel#bleComputerName { color: ${TEXT_PRIMARY}; font-size: ${FONT_ROW_TITLE}px; font-weight: 600; }
QLabel#bleComputerStatus { color: ${TEXT_SECONDARY_ALT}; font-size: ${FONT_CAPTION}px; }
QPushButton#bleComputerAction, QPushButton#bleComputerMore { min-height: 34px; padding: 0 12px; border-radius: 18px; }
QPushButton#bleComputerMore, QPushButton#bleConnectionHelp { background: transparent; border-color: transparent; color: ${TEXT_SECONDARY_ALT}; }
QPushButton#bleComputerMore:focus, QPushButton#bleConnectionHelp:focus { border-color: ${ACCENT}; }
QLabel#bleConnectionMessage { background: ${SURFACE_2}; border-radius: 16px; padding: 12px; color: ${TEXT_PRIMARY}; }
QPushButton#bleHelpTarget:checked { background: ${TEXT_PRIMARY}; color: ${SURFACE_BASE}; }
QSlider::groove:horizontal { height: 6px; background: ${SURFACE_3}; border: none; border-radius: 3px; }
QSlider::sub-page:horizontal { background: ${TEXT_PRIMARY}; border-radius: 3px; }
QSlider::handle:horizontal { background: ${TEXT_PRIMARY}; border: none; width: 18px; margin: -6px 0; border-radius: 9px; }
QLabel#displayBrightnessValue { background: transparent; color: ${TEXT_PRIMARY}; border: none; min-width: 38px; padding: 0px; font-size: ${FONT_BODY}px; font-weight: 500; }
QLabel#displayModuleTitle { color: ${TEXT_PRIMARY}; font-size: ${FONT_MODULE_TITLE}px; font-weight: 700; }
QScrollBar:vertical, QScrollArea#mappingEditorBody QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical, QScrollArea#mappingEditorBody QScrollBar::handle:vertical { background: ${SCROLLBAR_HANDLE}; border-radius: 4px; }
QToolTip { background: ${SURFACE_BASE}; color: ${TEXT_PRIMARY}; border: 1px solid ${BORDER_CONTROL}; border-radius: 12px; padding: 6px 10px; }
QInputDialog, QMessageBox { background: ${SURFACE_BASE}; color: ${TEXT_PRIMARY}; }
""").replace("@ASSETS@", str(files("controller_config.assets")).replace("\\", "/")) + MACOS_GLASS_MENU_STYLE + TOGGLE_STYLE


def uses_native_window_controls() -> bool:
    """macOS keeps the system close/minimize/zoom buttons; others draw their own."""
    application = QApplication.instance()
    return (
        sys.platform == "darwin"
        and isinstance(application, QApplication)
        and application.platformName() == "cocoa"
    )


def enable_native_window_controls(widget: QWidget) -> bool:
    """Keep the system title-bar buttons without losing per-pixel translucency.

    Qt 6.9+ can extend the client area under a title bar that draws no
    background, so the window stays titled (real traffic lights, system
    full screen and tiling) while Qt still owns every pixel. Call before the
    widget is shown; returns False where the drawn chrome must stay.
    """
    if not uses_native_window_controls():
        return False
    widget.setWindowFlags(
        (widget.windowFlags() & ~Qt.WindowType.FramelessWindowHint)
        | Qt.WindowType.ExpandedClientAreaHint
        | Qt.WindowType.NoTitleBarBackgroundHint
    )
    # The console draws its own top strip under the buttons; the title-bar
    # safe area must not push the content down a second time.
    widget.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
    return True


def refresh_native_window_controls(widget: QWidget) -> bool:
    """Finish the titled window natively; True when the system buttons are in place.

    Hides the system title text (the console draws its own) and clears the
    window background: a titled Qt window otherwise keeps an opaque backing
    behind its content, which is what blocked desktop translucency before.
    Qt recreates the native window after a close, so call this on every show.
    """
    if not uses_native_window_controls():
        return False
    try:
        import AppKit
        import objc

        window = objc.objc_object(c_void_p=int(widget.winId())).window()
        if window is None:
            return False
        window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        return all(
            window.standardWindowButton_(kind) is not None
            for kind in (
                AppKit.NSWindowCloseButton,
                AppKit.NSWindowMiniaturizeButton,
                AppKit.NSWindowZoomButton,
            )
        )
    except (AttributeError, ImportError, TypeError, ValueError):
        return False


def position_native_window_controls(
    widget: QWidget,
    *,
    center_x: float = NATIVE_WINDOW_CONTROLS_CENTER[0],
    center_y: float = NATIVE_WINDOW_CONTROLS_CENTER[1],
) -> bool:
    """Move the system buttons in from the rounded corner; True when placed.

    The system puts them where a square-ish window corner wants them, which
    crowds the console's larger radius. Only the group is shifted: sizes and
    spacing stay the system's. AppKit lays the title bar out again on resize,
    activation and state changes, so call this after each of those. In full
    screen the buttons live in the system's own bar and are left alone.
    """
    if not uses_native_window_controls():
        return False
    try:
        import AppKit
        import objc

        window = objc.objc_object(c_void_p=int(widget.winId())).window()
        if window is None or window.styleMask() & AppKit.NSWindowStyleMaskFullScreen:
            return False
        buttons = [
            window.standardWindowButton_(kind)
            for kind in (
                AppKit.NSWindowCloseButton,
                AppKit.NSWindowMiniaturizeButton,
                AppKit.NSWindowZoomButton,
            )
        ]
        if any(button is None for button in buttons):
            return False
        title_bar = buttons[0].superview()
        container = None if title_bar is None else title_bar.superview()
        if container is None:
            return False
        # Buttons sit vertically centred in the title bar container, so its
        # height sets how far down they are.
        height = 2.0 * center_y
        frame = container.frame()
        frame.size.height = height
        frame.origin.y = window.frame().size.height - height
        container.setFrame_(frame)
        size = buttons[0].frame().size
        shift = (center_x - size.width / 2.0) - buttons[0].frame().origin.x
        top = (height - size.height) / 2.0
        for button in buttons:
            button.setFrameOrigin_((button.frame().origin.x + shift, top))
        return True
    except (AttributeError, ImportError, TypeError, ValueError):
        return False


def macos_window_full_screen(widget: QWidget) -> bool | None:
    """Whether AppKit has the window in full screen, counting its animations.

    AppKit sets the full-screen style bit as it starts entering and clears it
    as it starts leaving, and resizes the window at that same moment; Qt only
    reports the new state once the animation is over. None where there is no
    Cocoa window to ask (other platforms, offscreen tests, not shown yet).
    """
    if not uses_native_window_controls() or widget.windowHandle() is None:
        return None
    try:
        import AppKit
        import objc

        window = objc.objc_object(c_void_p=int(widget.winId())).window()
        if window is None:
            return None
        return bool(window.styleMask() & AppKit.NSWindowStyleMaskFullScreen)
    except (AttributeError, ImportError, TypeError, ValueError):
        return None


def set_macos_window_backdrop(widget: QWidget, backdrop: str | None) -> bool:
    """Show the desktop glass (None) or back the window with a solid colour.

    Full screen has no desktop behind the window: the glass would only pull
    the wallpaper's colour into the console, and while AppKit animates into
    full screen any pixel Qt has not drawn yet would show as black. A window
    background in the console's own base colour avoids both. (Qt decides
    NSWindow.opaque itself from the translucent surface, so only the colour
    changes.) True when applied.
    """
    if not uses_native_window_controls() or widget.windowHandle() is None:
        return False
    try:
        import AppKit
        import objc

        window = objc.objc_object(c_void_p=int(widget.winId())).window()
        if window is None:
            return False
        if backdrop is None:
            colour = AppKit.NSColor.clearColor()
        else:
            red, green, blue = (int(backdrop[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
            colour = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, 1.0)
        window.setBackgroundColor_(colour)
        effect = getattr(widget, "_boring_macos_effect_view", None)
        if effect is not None:
            effect.setHidden_(backdrop is not None)
        return True
    except (AttributeError, ImportError, TypeError, ValueError):
        return False


def _macos_global_preference(key: str) -> str | None:
    try:
        import AppKit

        value = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_(key)
    except (AttributeError, ImportError, TypeError, ValueError):
        return None
    return None if value is None else str(value)


def title_bar_double_click_action() -> str:
    """What System Settings asks a title-bar double-click to do on macOS."""
    choice = _macos_global_preference("AppleActionOnDoubleClick")
    return {"Minimize": "minimize", "None": "none"}.get(choice, "zoom")


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
            # Full screen has no corners to round; the radius follows the caller.
            installed_layer = installed_effect.layer()
            if installed_layer is not None:
                installed_layer.setCornerRadius_(corner_radius)
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
