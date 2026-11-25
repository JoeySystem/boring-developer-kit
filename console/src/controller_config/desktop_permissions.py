"""Read-only checks of the system permissions the desktop Agent lights need.

* Accessibility (macOS): "press a light to jump to that conversation" drives
  DoubaoWork through the accessibility API, which needs the user's grant.
* Bluetooth (macOS): connecting the device over Bluetooth needs the user's
  grant; USB does not.

Nothing here changes a system setting or triggers a system prompt: the
accessibility check is ``AXIsProcessTrusted`` (no prompt option) and the
Bluetooth check reads the class property ``CBManager.authorization`` without
creating a manager. The only action is opening the matching System Settings
page, where the user decides.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Callable

# Accessibility results
TRUSTED = "trusted"
NOT_TRUSTED = "not_trusted"
NOT_NEEDED = "not_needed"  # Platforms without a per-app accessibility grant
UNKNOWN = "unknown"

# Bluetooth results (CBManagerAuthorization)
BT_ALLOWED = "allowed"
BT_NOT_DETERMINED = "not_determined"
BT_DENIED = "denied"
BT_RESTRICTED = "restricted"

ACCESSIBILITY_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
BLUETOOTH_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth"
)

# The two grants by name, for code that handles either (views/ui/permission_row.py).
# Each name is also a SystemPermissions method (``accessibility()``) with its
# ``open_<name>_settings()`` page, so test doubles only need those methods.
ACCESSIBILITY = "accessibility"
BLUETOOTH = "bluetooth"
KINDS = (ACCESSIBILITY, BLUETOOTH)
_GRANTED = {ACCESSIBILITY: {TRUSTED, NOT_NEEDED}, BLUETOOTH: {BT_ALLOWED, NOT_NEEDED}}


def is_granted(kind: str, status: str) -> bool:
    """True when nothing is missing: allowed, or not needed on this platform."""
    return status in _GRANTED[kind]


_APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"


def accessibility_status(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return NOT_NEEDED
    try:
        services = ctypes.cdll.LoadLibrary(_APPLICATION_SERVICES)
        services.AXIsProcessTrusted.restype = ctypes.c_bool
        services.AXIsProcessTrusted.argtypes = []
        return TRUSTED if services.AXIsProcessTrusted() else NOT_TRUSTED
    except (OSError, AttributeError):
        return UNKNOWN


def bluetooth_status(platform: str | None = None) -> str:
    platform = sys.platform if platform is None else platform
    if platform != "darwin":
        return NOT_NEEDED
    try:
        import CoreBluetooth as CB  # pyobjc-framework-CoreBluetooth (darwin dependency)

        value = CB.CBManager.authorization()
        return {
            CB.CBManagerAuthorizationAllowedAlways: BT_ALLOWED,
            CB.CBManagerAuthorizationNotDetermined: BT_NOT_DETERMINED,
            CB.CBManagerAuthorizationDenied: BT_DENIED,
            CB.CBManagerAuthorizationRestricted: BT_RESTRICTED,
        }.get(value, UNKNOWN)
    except (ImportError, AttributeError):
        return UNKNOWN


class SystemPermissions:
    """What the settings page asks; tests pass doubles for every part."""

    def __init__(self, *, platform: str | None = None,
                 accessibility: Callable[[], str] | None = None,
                 bluetooth: Callable[[], str] | None = None,
                 url_opener: Callable[[str], bool] | None = None):
        self.platform = sys.platform if platform is None else platform
        self._accessibility = accessibility or (lambda: accessibility_status(self.platform))
        self._bluetooth = bluetooth or (lambda: bluetooth_status(self.platform))
        self._url_opener = url_opener or _open_url

    @property
    def is_mac(self) -> bool:
        return self.platform == "darwin"

    def accessibility(self) -> str:
        return self._accessibility()

    def bluetooth(self) -> str:
        return self._bluetooth()

    def open_accessibility_settings(self) -> bool:
        """Open Privacy & Security → Accessibility; change nothing."""
        return bool(self.is_mac and self._url_opener(ACCESSIBILITY_SETTINGS_URL))

    def open_bluetooth_settings(self) -> bool:
        """Open Privacy & Security → Bluetooth; change nothing."""
        return bool(self.is_mac and self._url_opener(BLUETOOTH_SETTINGS_URL))


def _open_url(url: str) -> bool:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return QDesktopServices.openUrl(QUrl(url))
