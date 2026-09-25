from __future__ import annotations

from PySide6.QtCore import QSettings

from controller_config.build_identity import BuildIdentity
from controller_config.settings_migration import (
    MIGRATION_KEY,
    migrate_legacy_user_state,
)


def _settings(path):
    return QSettings(str(path), QSettings.Format.IniFormat)


def test_official_imports_missing_user_state_without_overwriting_current(tmp_path):
    current = _settings(tmp_path / "official.ini")
    legacy = _settings(tmp_path / "diy.ini")
    current.setValue("ui/language", "ja_JP")
    legacy.setValue("ui/language", "zh_CN")
    legacy.setValue("ui/voice_input_software", "typeless")
    legacy.setValue("bluetooth/computers/device/serial/1", "办公室电脑")
    legacy.setValue("firmware/official_releases", "stale cache")
    legacy.sync()
    legacy_root = tmp_path / "BORING Controller Config DIY"
    current_root = tmp_path / "BORING Controller Config"
    (legacy_root / "prompt-libraries").mkdir(parents=True)
    (legacy_root / "prompt-libraries/device.json").write_text("old", encoding="utf-8")
    (current_root / "prompt-libraries").mkdir(parents=True)
    (current_root / "prompt-libraries/device.json").write_text("current", encoding="utf-8")
    (legacy_root / "workflows").mkdir()
    (legacy_root / "workflows/device.json").write_text("workflow", encoding="utf-8")

    result = migrate_legacy_user_state(
        current,
        BuildIdentity("official"),
        legacy=legacy,
        current_data_root=current_root,
        legacy_data_root=legacy_root,
    )

    assert result.imported_settings == 2
    assert result.imported_files == 1
    assert not result.retry_needed
    assert current.value("ui/language") == "ja_JP"
    assert current.value("ui/voice_input_software") == "typeless"
    assert current.value("bluetooth/computers/device/serial/1") == "办公室电脑"
    assert not current.contains("firmware/official_releases")
    assert (current_root / "prompt-libraries/device.json").read_text() == "current"
    assert (current_root / "workflows/device.json").read_text() == "workflow"
    assert current.value(MIGRATION_KEY, False, type=bool)


def test_migration_runs_once_and_never_imports_into_custom_build(tmp_path):
    legacy = _settings(tmp_path / "diy.ini")
    legacy.setValue("ui/language", "zh_CN")
    official = _settings(tmp_path / "official.ini")
    migrate_legacy_user_state(
        official,
        BuildIdentity("official"),
        legacy=legacy,
        current_data_root=tmp_path / "official-data",
        legacy_data_root=tmp_path / "legacy-data",
    )
    legacy.setValue("ui/voice_input_software", "typeless")
    second = migrate_legacy_user_state(
        official,
        BuildIdentity("official"),
        legacy=legacy,
        current_data_root=tmp_path / "official-data",
        legacy_data_root=tmp_path / "legacy-data",
    )
    assert second.imported_settings == 0
    assert not official.contains("ui/voice_input_software")

    custom = _settings(tmp_path / "custom.ini")
    result = migrate_legacy_user_state(
        custom,
        BuildIdentity("custom"),
        legacy=legacy,
        current_data_root=tmp_path / "custom-data",
        legacy_data_root=tmp_path / "legacy-data",
    )
    assert result.imported_settings == 0
    assert custom.allKeys() == []
