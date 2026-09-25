"""Desktop updates are separate from device firmware maintenance."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import sys

from PySide6.QtCore import QObject, Signal

from controller_config.build_identity import BuildIdentity, load_build_identity


DESKTOP_UPDATE_CHECK_INTERVAL_SECONDS = 60 * 60


@dataclass(frozen=True)
class UpdateStatus:
    state: str = "unconfigured"
    version: str = ""
    received: int = 0
    total: int = 0
    message: str = ""
    retry_action: str = ""


class UnavailableUpdater(QObject):
    changed = Signal(object)
    quit_requested = Signal()

    def __init__(self, message, parent=None, *, state="unconfigured"):
        super().__init__(parent)
        self.status = UpdateStatus(state=state, message=message)

    def start(self):
        self.changed.emit(self.status)

    def check(self):
        self.start()

    def download(self):
        self.start()

    def install(self):
        self.start()

    def retry(self):
        self.check()

    def cancel(self):
        pass

    def shutdown(self):
        pass


def default_desktop_update_source() -> dict:
    """Read the desktop update configuration bundled with this build."""

    payload = json.loads(
        files("controller_config.assets")
        .joinpath("app-update-source.json")
        .read_text(encoding="utf-8-sig")
    )
    if not isinstance(payload, dict):
        raise ValueError("Desktop update configuration must be an object")
    return payload


def create_desktop_updater(
    prepare_restart,
    *,
    parent=None,
    config=None,
    build_identity: BuildIdentity | None = None,
):
    try:
        identity = build_identity or load_build_identity()
        if not identity.allows_official_updates:
            return UnavailableUpdater(
                identity.update_message,
                parent,
                state=identity.update_state,
            )
        if config is None:
            config = default_desktop_update_source()
        platform_key = {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform)
        if platform_key is None:
            return UnavailableUpdater('此系统尚未提供应用内更新。', parent)
        selected = config.get(platform_key, {})
        if not selected.get('feed_url') or not selected.get('public_key'):
            return UnavailableUpdater('此安装包尚未配置在线更新，请使用官方安装包更新。', parent)
        if sys.platform == 'darwin':
            from controller_config.desktop_update_macos import MacDesktopUpdater
            return MacDesktopUpdater(selected, prepare_restart, parent=parent)
        from controller_config.desktop_update_windows import WindowsDesktopUpdater
        return WindowsDesktopUpdater(selected, prepare_restart, parent=parent)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        return UnavailableUpdater(f'应用更新暂不可用：{exc}', parent)
