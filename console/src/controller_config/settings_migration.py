"""Import user-owned state from the legacy DIY namespace into an official build."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from controller_config.build_identity import BuildIdentity


MIGRATION_KEY = "migration/official_from_diy_v1"
_USER_SETTING_PREFIXES = ("ui/", "bluetooth/", "prompt_helper/")
_USER_SETTING_KEYS = {"devices/lastBluetoothPort"}
_USER_DATA_DIRECTORIES = (
    "prompt-libraries",
    "automations",
    "workflows",
    "host-tasks",
)


@dataclass(frozen=True)
class SettingsMigrationResult:
    imported_settings: int = 0
    imported_files: int = 0
    retry_needed: bool = False


def migrate_legacy_user_state(
    current: QSettings,
    identity: BuildIdentity,
    *,
    legacy: QSettings | None = None,
    current_data_root: Path | None = None,
    legacy_data_root: Path | None = None,
) -> SettingsMigrationResult:
    """Copy missing user choices without replacing current official state.

    Builds without an official origin keep their isolated storage.  This only
    handles the known namespace used by previously distributed DIY builds.
    """

    if identity.origin != "official" or current.value(MIGRATION_KEY, False, type=bool):
        return SettingsMigrationResult()
    legacy = legacy or QSettings(
        QSettings.Format.NativeFormat,
        QSettings.Scope.UserScope,
        "BORING",
        "BORING Controller Config DIY",
    )
    imported_settings = 0
    for key in legacy.allKeys():
        if not (key.startswith(_USER_SETTING_PREFIXES) or key in _USER_SETTING_KEYS):
            continue
        if current.contains(key):
            continue
        current.setValue(key, legacy.value(key))
        imported_settings += 1

    if current_data_root is None:
        current_data_root = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        ))
    if legacy_data_root is None:
        legacy_data_root = current_data_root.parent / "BORING Controller Config DIY"
    imported_files = 0
    try:
        for directory_name in _USER_DATA_DIRECTORIES:
            source = legacy_data_root / directory_name
            if not source.is_dir():
                continue
            target = current_data_root / directory_name
            for source_file in source.rglob("*"):
                if not source_file.is_file():
                    continue
                target_file = target / source_file.relative_to(source)
                if target_file.exists():
                    continue
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)
                imported_files += 1
    except OSError:
        current.sync()
        return SettingsMigrationResult(imported_settings, imported_files, True)

    current.setValue(MIGRATION_KEY, True)
    current.sync()
    return SettingsMigrationResult(imported_settings, imported_files)
