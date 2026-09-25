from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from controller_config.extensions.manager import (
    MANIFEST_FILENAME,
    ExtensionManager,
    ExtensionManagerError,
)
from controller_config.extensions.registry import ExtensionRegistryError


EXTENSION_ID = "com.example.prompt-tools"


def _manifest(*, extension_id: str = EXTENSION_ID) -> dict[str, object]:
    return {
        "manifest_version": 1,
        "id": extension_id,
        "name": "Prompt tools",
        "version": "1.0.0",
        "api_version": {"major": 1, "minor": 0},
        "entrypoint": "src/main.py",
        "observer_events": ["prompt.triggered"],
        "actions": [{"id": "use_prompt", "name": "Use prompt"}],
    }


def _write_package(root: Path, *, include_entrypoint: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / MANIFEST_FILENAME).write_text(
        json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8"
    )
    if include_entrypoint:
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('ready')\n", encoding="utf-8")
    return root


def test_import_directory_registers_disabled_copy_and_survives_reload(
    tmp_path: Path,
) -> None:
    source = _write_package(tmp_path / "source")
    manager = ExtensionManager(tmp_path / "managed")

    installed = manager.import_package(source)

    assert installed.manifest.extension_id == EXTENSION_ID
    assert installed.enabled is False
    assert installed.entrypoint_path.read_text(encoding="utf-8") == "print('ready')\n"
    assert installed.install_path != source
    reloaded = ExtensionManager(tmp_path / "managed").get(EXTENSION_ID)
    assert reloaded.manifest.as_mapping() == _manifest()
    assert reloaded.enabled is False


def test_import_zip_accepts_one_wrapped_package(tmp_path: Path) -> None:
    archive_path = tmp_path / "prompt-tools.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            f"prompt-tools/{MANIFEST_FILENAME}",
            json.dumps(_manifest(), ensure_ascii=False),
        )
        archive.writestr("prompt-tools/src/main.py", "print('zip')\n")

    installed = ExtensionManager(tmp_path / "managed").import_package(archive_path)

    assert installed.entrypoint_path.read_text(encoding="utf-8") == "print('zip')\n"
    assert not (installed.install_path / "prompt-tools").exists()


def test_import_rejects_invalid_manifest_and_missing_entrypoint(tmp_path: Path) -> None:
    invalid = _write_package(tmp_path / "invalid")
    value = _manifest()
    value["api_version"] = {"major": 2, "minor": 0}
    (invalid / MANIFEST_FILENAME).write_text(json.dumps(value), encoding="utf-8")

    manager = ExtensionManager(tmp_path / "managed")
    with pytest.raises(ExtensionManagerError, match="API major 2"):
        manager.import_package(invalid)
    assert (invalid / MANIFEST_FILENAME).is_file()
    assert (invalid / "src" / "main.py").is_file()

    missing = _write_package(tmp_path / "missing", include_entrypoint=False)
    with pytest.raises(ExtensionManagerError, match="入口缺失"):
        manager.import_package(missing)
    assert (missing / MANIFEST_FILENAME).is_file()
    assert manager.extensions == ()


def test_duplicate_id_is_rejected_without_overwriting_installed_files(
    tmp_path: Path,
) -> None:
    first = _write_package(tmp_path / "first")
    second = _write_package(tmp_path / "second")
    (second / "src" / "main.py").write_text("print('second')\n", encoding="utf-8")
    manager = ExtensionManager(tmp_path / "managed")
    installed = manager.import_package(first)

    with pytest.raises(ExtensionManagerError, match="已安装"):
        manager.import_package(second)

    assert installed.entrypoint_path.read_text(encoding="utf-8") == "print('ready')\n"
    assert len(manager.extensions) == 1


def test_enable_disable_persist_and_missing_entrypoint_cannot_enable(
    tmp_path: Path,
) -> None:
    source = _write_package(tmp_path / "source")
    manager = ExtensionManager(tmp_path / "managed")
    installed = manager.import_package(source)

    assert manager.enable(EXTENSION_ID).enabled is True
    assert ExtensionManager(tmp_path / "managed").get(EXTENSION_ID).enabled is True
    assert manager.disable(EXTENSION_ID).enabled is False
    assert ExtensionManager(tmp_path / "managed").get(EXTENSION_ID).enabled is False

    installed.entrypoint_path.unlink()
    with pytest.raises(ExtensionManagerError, match="入口缺失"):
        manager.enable(EXTENSION_ID)


def test_remove_only_deletes_managed_package_and_keeps_extension_data(
    tmp_path: Path,
) -> None:
    source = _write_package(tmp_path / "source")
    managed_root = tmp_path / "managed"
    manager = ExtensionManager(managed_root)
    installed = manager.import_package(source)
    extension_data = managed_root / "data" / EXTENSION_ID / "preferences.json"
    extension_data.parent.mkdir(parents=True)
    extension_data.write_text('{"kept": true}', encoding="utf-8")

    manager.remove(EXTENSION_ID)

    assert not installed.install_path.exists()
    assert extension_data.exists()
    assert manager.extensions == ()
    assert ExtensionManager(managed_root).extensions == ()


def test_remove_restores_package_when_registry_save_fails(
    tmp_path: Path, monkeypatch
) -> None:
    source = _write_package(tmp_path / "source")
    manager = ExtensionManager(tmp_path / "managed")
    installed = manager.import_package(source)

    def fail_save(_records) -> None:
        raise ExtensionRegistryError("registry unavailable")

    monkeypatch.setattr(manager.registry, "save", fail_save)

    with pytest.raises(ExtensionManagerError, match="registry unavailable"):
        manager.remove(EXTENSION_ID)

    assert installed.entrypoint_path.is_file()
    assert manager.get(EXTENSION_ID).manifest.extension_id == EXTENSION_ID


def test_remove_stays_committed_when_temporary_cleanup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    source = _write_package(tmp_path / "source")
    managed_root = tmp_path / "managed"
    manager = ExtensionManager(managed_root)
    installed = manager.import_package(source)

    def fail_cleanup(_path) -> None:
        raise OSError("directory busy")

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)

    with pytest.warns(RuntimeWarning, match="扩展已移除"):
        manager.remove(EXTENSION_ID)

    assert not installed.install_path.exists()
    assert manager.extensions == ()
    assert ExtensionManager(managed_root).extensions == ()


def test_zip_path_escape_is_rejected_without_writing_outside_managed_root(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "escaped.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escaped.txt", "not allowed")
        archive.writestr(MANIFEST_FILENAME, json.dumps(_manifest()))
        archive.writestr("src/main.py", "print('ready')\n")

    with pytest.raises(ExtensionManagerError, match="路径逃逸"):
        ExtensionManager(tmp_path / "managed").import_package(archive_path)

    assert not (tmp_path / "escaped.txt").exists()


def test_invalid_registry_is_reported_instead_of_silently_reset(tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    (managed_root / "registry.json").write_text(
        '{"version": 1, "extensions": [{"manifest": {}, "enabled": false}]}',
        encoding="utf-8",
    )

    with pytest.raises(ExtensionManagerError, match="manifest"):
        ExtensionManager(managed_root)
