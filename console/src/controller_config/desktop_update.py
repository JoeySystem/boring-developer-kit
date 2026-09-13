"""Desktop updates are separate from device firmware maintenance."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import sys

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class UpdateStatus:
    state: str = "unconfigured"
    version: str = ""
    received: int = 0
    total: int = 0
    message: str = ""


class UnavailableUpdater(QObject):
    changed = Signal(object)
    quit_requested = Signal()

    def __init__(self, message, parent=None):
        super().__init__(parent)
        self.status = UpdateStatus(message=message)

    def start(self):
        self.changed.emit(self.status)

    def check(self):
        self.start()

    def download(self):
        self.start()

    def install(self):
        self.start()

    def cancel(self):
        pass

    def shutdown(self):
        pass


def create_desktop_updater(prepare_restart, *, parent=None, config=None):
    try:
        if config is None:
            config = json.loads(files('controller_config.assets').joinpath('app-update-source.json').read_text())
        platform_key = {'darwin': 'macos', 'win32': 'windows'}.get(sys.platform)
        if platform_key is None:
            return UnavailableUpdater('此系统尚未提供应用内更新。', parent)
        selected = config.get(platform_key, {})
        if not selected.get('feed_url') or not selected.get('public_key'):
            return UnavailableUpdater('此源码构建未配置应用更新，请从源码仓库获取更新。', parent)
        if sys.platform == 'darwin':
            from controller_config.desktop_update_macos import MacDesktopUpdater
            return MacDesktopUpdater(selected, prepare_restart, parent=parent)
        from controller_config.desktop_update_windows import WindowsDesktopUpdater
        return WindowsDesktopUpdater(selected, prepare_restart, parent=parent)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        return UnavailableUpdater(f'应用更新暂不可用：{exc}', parent)
