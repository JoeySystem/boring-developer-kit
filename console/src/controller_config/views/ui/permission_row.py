"""System permissions that recheck themselves when the user comes back (design decision).

The user grants a permission in System Settings, outside Console, and Console
gets no notice of it. So the status is read again when Console comes back to
the front (the application becomes active, or one of its windows is
activated), and for a short while after "去允许" opened System Settings, in
case the user keeps Console visible beside it. No restart, no button to press.

Two parts, used together or alone; pages only call them:

    watcher = PermissionWatcher(permissions, parent=page, kinds=(perm.ACCESSIBILITY,))
    watcher.changed.connect(page.refresh)      # a grant changed
    watcher.status(perm.ACCESSIBILITY)         # last read value (perm.TRUSTED, ...)
    watcher.granted(perm.ACCESSIBILITY)        # allowed, or not needed here
    watcher.open_settings(perm.ACCESSIBILITY)  # "去允许": System Settings, then watch closely
    watcher.recheck()                          # read again now; True when something changed

    row = PermissionRow(watcher, perm.ACCESSIBILITY, ACCESSIBILITY_TEXT)
    # a status shape, the sentence for the current value, and "去允许" while it is missing;
    # hidden on platforms without the grant. It follows the watcher by itself.

`permissions` is a desktop_permissions.SystemPermissions or a double with the
same methods (`is_mac`, `accessibility()`, `bluetooth()`,
`open_accessibility_settings()`, `open_bluetooth_settings()`). Reading never
prompts and opening only shows the System Settings page (desktop_permissions).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from controller_config import desktop_permissions as perm
from controller_config.i18n import set_translatable_text
from controller_config.views.ui.dialog import dialog_button
from controller_config.views.ui.status_icon import StatusIcon

# After "去允许": read every POLL_MS until the grant arrives or WATCH_MS passes.
POLL_MS = 2000
WATCH_MS = 120_000
ALLOW_TEXT = "去允许"


class PermissionWatcher(QObject):
    """The last read value of each permission in `kinds`, kept current."""

    changed = Signal()

    def __init__(self, permissions: perm.SystemPermissions | None = None, parent: QObject | None = None,
                 *, kinds: Sequence[str] = perm.KINDS, poll_ms: int = POLL_MS,
                 watch_ms: int = WATCH_MS) -> None:
        super().__init__(parent)
        unknown = set(kinds) - set(perm.KINDS)
        if unknown:
            raise ValueError(f"Unknown permission: {sorted(unknown)}")
        self.permissions = permissions or perm.SystemPermissions()
        self._kinds = tuple(kinds)
        self._values = {kind: self._read(kind) for kind in self._kinds}
        self._watch = QTimer(self)
        self._watch.setInterval(poll_ms)
        self._watch.timeout.connect(self._poll)
        self._watch_ticks = 0
        self._watch_limit = max(1, watch_ms // max(1, poll_ms))
        if self.is_mac:
            app = QGuiApplication.instance()
            if app is not None:
                app.applicationStateChanged.connect(self._application_state_changed)
                app.focusWindowChanged.connect(self._focus_window_changed)

    @property
    def is_mac(self) -> bool:
        return bool(self.permissions.is_mac)

    @property
    def watching(self) -> bool:
        """True while it reads closely after "去允许"."""
        return self._watch.isActive()

    def status(self, kind: str) -> str:
        return self._values[kind]

    def granted(self, kind: str) -> bool:
        return perm.is_granted(kind, self._values[kind])

    def recheck(self) -> bool:
        """Read every permission again; emit `changed` and return True if one changed."""
        values = {kind: self._read(kind) for kind in self._kinds}
        if values == self._values:
            return False
        self._values = values
        if all(perm.is_granted(kind, value) for kind, value in values.items()):
            self._watch.stop()
        self.changed.emit()
        return True

    def open_settings(self, kind: str) -> bool:
        """Open the System Settings page for `kind`, then read closely for a while."""
        opened = bool(getattr(self.permissions, f"open_{kind}_settings")())
        if self.is_mac:
            self._watch_ticks = 0
            self._watch.start()
        return opened

    def _read(self, kind: str) -> str:
        if not self.is_mac:
            return perm.NOT_NEEDED
        return getattr(self.permissions, kind)()

    def _poll(self) -> None:
        self._watch_ticks += 1
        if self._watch_ticks >= self._watch_limit:
            self._watch.stop()
        self.recheck()

    def _application_state_changed(self, state) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.recheck()

    def _focus_window_changed(self, window) -> None:
        if window is not None:
            self.recheck()


class PermissionRow(QWidget):
    """One permission: status shape, one sentence, "去允许" while it is missing.

    texts: the sentence for each value of this permission (perm.TRUSTED, ...);
    a value without its own sentence uses texts[perm.UNKNOWN].
    """

    def __init__(self, watcher: PermissionWatcher, kind: str, texts: Mapping[str, str], *,
                 allow_text: str = ALLOW_TEXT, object_name: str = "", button_name: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.watcher = watcher
        self.kind = kind
        self._texts = dict(texts)
        self.setObjectName(f"{kind}PermissionRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.icon = StatusIcon("wait")
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel(objectName=object_name or f"{kind}PermissionStatus")
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        self.allow = dialog_button(allow_text, "secondary", object_name=button_name or f"{kind}PermissionAllow")
        row.addWidget(self.allow, 0, Qt.AlignmentFlag.AlignTop)
        self.allow.clicked.connect(lambda: watcher.open_settings(kind))
        watcher.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        value = self.watcher.status(self.kind)
        if value == perm.NOT_NEEDED:  # the platform has no such grant; that never changes
            self.hide()
            return
        granted = self.watcher.granted(self.kind)
        self.icon.set_state("ok" if granted else "wait")
        set_translatable_text(self.label, self._texts.get(value, self._texts[perm.UNKNOWN]))
        self.allow.setVisible(not granted)
