from __future__ import annotations

import json
import shutil
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QStandardPaths

from controller_config.extensions.contracts import (
    ExtensionContractError,
    ExtensionManifest,
)
from controller_config.extensions.registry import (
    ExtensionRegistry,
    ExtensionRegistryError,
    ExtensionRegistryRecord,
)


MANIFEST_FILENAME = "boring-extension.json"


class ExtensionManagerError(ValueError):
    """An extension package cannot be imported or managed safely."""


@dataclass(frozen=True)
class InstalledExtension:
    manifest: ExtensionManifest
    install_path: Path
    enabled: bool

    @property
    def entrypoint_path(self) -> Path:
        return self.install_path.joinpath(*PurePosixPath(self.manifest.entrypoint).parts)

    @property
    def entrypoint_exists(self) -> bool:
        return self.entrypoint_path.is_file()


class ExtensionManager:
    """Import and register local extension packages under one managed root."""

    def __init__(self, managed_root: Path | None = None) -> None:
        if managed_root is None:
            application_data = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            managed_root = Path(application_data) / "extensions"
        self.managed_root = Path(managed_root)
        self.packages_root = self.managed_root / "packages"
        self.registry = ExtensionRegistry(self.managed_root / "registry.json")
        self._records = self._load_records()

    @property
    def extensions(self) -> tuple[InstalledExtension, ...]:
        return tuple(
            InstalledExtension(
                manifest=record.manifest,
                install_path=self._install_path(record.manifest.extension_id),
                enabled=record.enabled,
            )
            for record in self._records
        )

    def get(self, extension_id: str) -> InstalledExtension:
        for extension in self.extensions:
            if extension.manifest.extension_id == extension_id:
                return extension
        raise ExtensionManagerError(f"未找到扩展 {extension_id}")

    def reload(self) -> tuple[InstalledExtension, ...]:
        self._records = self._load_records()
        return self.extensions

    def import_package(self, source: Path) -> InstalledExtension:
        source = Path(source)
        if not source.exists():
            raise ExtensionManagerError(f"扩展包不存在：{source}")

        self.packages_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".import-", dir=self.packages_root
        ) as temporary_directory:
            staging_root = Path(temporary_directory)
            package_root = self._prepare_package(source, staging_root)
            manifest = self._read_manifest(package_root / MANIFEST_FILENAME)
            self._require_entrypoint(package_root, manifest)
            extension_id = manifest.extension_id
            if any(
                record.manifest.extension_id == extension_id
                for record in self._records
            ):
                raise ExtensionManagerError(f"扩展 ID {extension_id} 已安装")
            target = self._install_path(extension_id)
            if target.exists() or target.is_symlink():
                raise ExtensionManagerError(
                    f"受管安装目录已存在，不会覆盖：{target}"
                )

            staged_package = staging_root / ".ready"
            shutil.copytree(package_root, staged_package)
            self._require_entrypoint(staged_package, manifest)
            staged_package.rename(target)

            record = ExtensionRegistryRecord(manifest=manifest, enabled=False)
            candidate = (*self._records, record)
            try:
                self.registry.save(candidate)
            except ExtensionRegistryError as exc:
                self._remove_install_path(target)
                raise ExtensionManagerError(str(exc)) from exc
            self._records = candidate
            return InstalledExtension(manifest, target, enabled=False)

    def enable(self, extension_id: str) -> InstalledExtension:
        extension = self.get(extension_id)
        if not extension.entrypoint_exists:
            raise ExtensionManagerError(
                f"扩展入口缺失：{extension.manifest.entrypoint}"
            )
        return self._set_enabled(extension_id, True)

    def disable(self, extension_id: str) -> InstalledExtension:
        return self._set_enabled(extension_id, False)

    def remove(self, extension_id: str) -> None:
        self.get(extension_id)
        target = self._install_path(extension_id)
        candidate = tuple(
            record
            for record in self._records
            if record.manifest.extension_id != extension_id
        )
        holding_root: Path | None = None
        held_package: Path | None = None
        if target.exists() or target.is_symlink():
            holding_root = Path(
                tempfile.mkdtemp(prefix=".remove-", dir=self.packages_root)
            )
            held_package = holding_root / "package"
            target.rename(held_package)
        try:
            self.registry.save(candidate)
        except ExtensionRegistryError as exc:
            if held_package is not None and holding_root is not None:
                held_package.rename(target)
                holding_root.rmdir()
            raise ExtensionManagerError(str(exc)) from exc
        self._records = candidate
        if holding_root is not None:
            try:
                shutil.rmtree(holding_root)
            except OSError as exc:
                warnings.warn(
                    f"扩展已移除，但无法清理临时目录 {holding_root}：{exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )

    def _set_enabled(self, extension_id: str, enabled: bool) -> InstalledExtension:
        self.get(extension_id)
        candidate = tuple(
            ExtensionRegistryRecord(record.manifest, enabled)
            if record.manifest.extension_id == extension_id
            else record
            for record in self._records
        )
        try:
            self.registry.save(candidate)
        except ExtensionRegistryError as exc:
            raise ExtensionManagerError(str(exc)) from exc
        self._records = candidate
        return self.get(extension_id)

    def _load_records(self) -> tuple[ExtensionRegistryRecord, ...]:
        try:
            return self.registry.load()
        except ExtensionRegistryError as exc:
            raise ExtensionManagerError(str(exc)) from exc

    def _prepare_package(self, source: Path, staging_root: Path) -> Path:
        if source.is_dir():
            manifest_path = source / MANIFEST_FILENAME
            if not manifest_path.is_file():
                raise ExtensionManagerError(
                    f"扩展目录根部缺少 {MANIFEST_FILENAME}"
                )
            return source
        if not source.is_file() or source.suffix.lower() != ".zip":
            raise ExtensionManagerError("只支持扩展目录或 .zip 扩展包")
        extract_root = staging_root / ".extracted"
        extract_root.mkdir()
        try:
            with zipfile.ZipFile(source) as archive:
                self._extract_zip(archive, extract_root)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ExtensionManagerError(f"无法读取扩展包：{exc}") from exc
        manifests = tuple(extract_root.rglob(MANIFEST_FILENAME))
        if len(manifests) != 1:
            raise ExtensionManagerError(
                f".zip 扩展包必须仅包含一个 {MANIFEST_FILENAME}"
            )
        return manifests[0].parent

    @staticmethod
    def _extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
        for member in archive.infolist():
            name = member.filename
            if "\\" in name:
                raise ExtensionManagerError(".zip 扩展包路径必须使用 POSIX 分隔符")
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ExtensionManagerError(".zip 扩展包包含路径逃逸")
            if not path.parts:
                continue
            target = destination.joinpath(*path.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)

    @staticmethod
    def _read_manifest(path: Path) -> ExtensionManifest:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ExtensionManagerError(f"扩展包缺少 {MANIFEST_FILENAME}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExtensionManagerError(f"无法读取扩展 manifest：{exc}") from exc
        try:
            return ExtensionManifest.from_mapping(value)
        except ExtensionContractError as exc:
            raise ExtensionManagerError(f"扩展 manifest 无效：{exc}") from exc

    @staticmethod
    def _require_entrypoint(
        package_root: Path, manifest: ExtensionManifest
    ) -> None:
        entrypoint = package_root.joinpath(*PurePosixPath(manifest.entrypoint).parts)
        if not entrypoint.is_file():
            raise ExtensionManagerError(f"扩展入口缺失：{manifest.entrypoint}")

    def _install_path(self, extension_id: str) -> Path:
        return self.packages_root / extension_id

    def _remove_install_path(self, target: Path) -> None:
        expected_parent = self.packages_root.resolve()
        if target.parent.resolve() != expected_parent:
            raise ExtensionManagerError("拒绝移除受管扩展目录以外的路径")
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
