from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import tempfile
from typing import Any
import weakref
import zipfile

from .errors import PackageError
from .models import FlashImage, OtaApplication, UsbIdentity


LEGACY_OFFSETS = (0x0, 0x8000, 0x10000)
OTA_DATA_OFFSET = 0x240000
OTA_BOOT_OFFSETS = (*LEGACY_OFFSETS, OTA_DATA_OFFSET)
OTA_MANIFEST_FORMAT = "wmp-firmware-update-v1"
VALIDATION_STATES = {"built", "sample-verified", "rc", "released"}
MAX_ARCHIVE_FILES = 64
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
FLASH_BYTES = 16 * 1024 * 1024
PARTITION_ENTRY_SIZE = 32
PARTITION_MAGIC = 0x50AA
TRUSTED_RELEASE_ARCHIVES = {
    "usb-codex-stable-2026-07-30": (
        "52ee2b927fba55c981dc4d7f55ca429c1a38ee7e0e569dd3bd8082a2d0a760ca"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PackageError(f"release-manifest.json 缺少有效字段 {key}")
    return value


def _usb_id(data: dict[str, Any], key: str) -> str:
    value = _required_string(data, key).upper()
    if not re.fullmatch(r"[0-9A-F]{4}", value):
        raise PackageError(f"runtime_usb.{key} 必须是 4 位十六进制数")
    return value


def _partitions(partition_table: Path) -> list[tuple[int, int, int, int, str]]:
    raw = partition_table.read_bytes()
    partitions: list[tuple[int, int, int, int, str]] = []
    for position in range(0, len(raw) - PARTITION_ENTRY_SIZE + 1, PARTITION_ENTRY_SIZE):
        entry = raw[position : position + PARTITION_ENTRY_SIZE]
        if entry == b"\xFF" * PARTITION_ENTRY_SIZE:
            break
        magic, partition_type, subtype, offset, size, label, _flags = struct.unpack(
            "<HBBLL16sL",
            entry,
        )
        if magic == 0xEBEB:
            break
        if magic != PARTITION_MAGIC:
            raise PackageError(
                f"partition table 在条目 {position // PARTITION_ENTRY_SIZE} 处格式无效"
            )
        if size <= 0 or offset + size > FLASH_BYTES:
            raise PackageError("partition table 包含无效或越界分区")
        partitions.append(
            (
                offset,
                offset + size,
                partition_type,
                subtype,
                label.rstrip(b"\0").decode("ascii", errors="replace"),
            )
        )

    ordered = sorted(partitions)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise PackageError(
                "partition table 分区重叠："
                f"{previous[4] or hex(previous[0])} 与 "
                f"{current[4] or hex(current[0])}"
            )
    return partitions


def _application_partition_limit(
    partitions: list[tuple[int, int, int, int, str]], application_offset: int
) -> int:
    for offset, end, partition_type, _subtype, _label in partitions:
        if partition_type == 0x00 and offset == application_offset:
            return end
    raise PackageError(
        f"partition table 未声明偏移 0x{application_offset:X} 的应用分区"
    )


def _partition_limit(
    partitions: list[tuple[int, int, int, int, str]],
    offset: int,
    *,
    partition_type: int,
    subtype: int,
    label: str,
) -> int:
    for start, end, actual_type, actual_subtype, actual_label in partitions:
        if (
            start == offset
            and actual_type == partition_type
            and actual_subtype == subtype
            and actual_label == label
        ):
            return end
    raise PackageError(
        f"partition table 未声明偏移 0x{offset:X} 的 {label} 分区"
    )


def _safe_extract(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > MAX_ARCHIVE_FILES:
            raise PackageError("release ZIP contains too many files")
        if sum(member.file_size for member in members) > MAX_ARCHIVE_BYTES:
            raise PackageError("release ZIP is too large")
        destination_resolved = destination.resolve()
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise PackageError(f"unsafe path in release ZIP: {member.filename}")
            target = (destination / member_path).resolve()
            if not target.is_relative_to(destination_resolved):
                raise PackageError(f"unsafe path in release ZIP: {member.filename}")
        archive.extractall(destination)


def _snapshot_directory(source: Path, destination: Path) -> Path:
    files = []
    total_bytes = 0
    for path in source.rglob("*"):
        if path.is_symlink():
            raise PackageError(f"release directory contains a symlink: {path}")
        if path.is_file():
            files.append(path)
            total_bytes += path.stat().st_size
    if len(files) > MAX_ARCHIVE_FILES:
        raise PackageError("release directory contains too many files")
    if total_bytes > MAX_ARCHIVE_BYTES:
        raise PackageError("release directory is too large")
    snapshot = destination / "release"
    snapshot.mkdir(parents=True)
    for source_file in files:
        relative = source_file.relative_to(source)
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
    return snapshot


class ReleasePackage(AbstractContextManager["ReleasePackage"]):
    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        flash_images: tuple[FlashImage, ...],
        ota_application: OtaApplication | None = None,
        cleanup: Path | None = None,
        archive_sha256: str | None = None,
        archive_checksum_verified: bool = False,
        trusted_source_verified: bool = False,
    ):
        self.root = root
        self.manifest = manifest
        self.flash_images = flash_images
        self.ota_application = ota_application
        self._cleanup = cleanup
        self._cleanup_finalizer = (
            weakref.finalize(self, shutil.rmtree, cleanup, True)
            if cleanup is not None
            else None
        )
        self.archive_sha256 = archive_sha256
        self.archive_checksum_verified = archive_checksum_verified
        self.trusted_source_verified = trusted_source_verified
        self.release_id = _required_string(manifest, "release_id")
        self.target = _required_string(manifest, "target")
        self.esp_idf = manifest.get("esp_idf")
        self.git_tag = _required_string(manifest, "git_tag")
        self.source_commit = _required_string(manifest, "source_commit")
        self.hardware_id = manifest.get("hardware_id")
        self.build_id = manifest.get("build_id")
        self.validation_state = manifest.get("validation_state")
        usb = manifest["runtime_usb"]
        self.runtime_usb = UsbIdentity(
            product=_required_string(usb, "product"),
            vid=_usb_id(usb, "vid"),
            pid=_usb_id(usb, "pid"),
        )

    @property
    def bootloader(self) -> FlashImage:
        return self.flash_images[0]

    @property
    def partition_table(self) -> FlashImage:
        return self.flash_images[1]

    @property
    def application(self) -> FlashImage:
        return self.flash_images[2]

    @property
    def ota_data(self) -> FlashImage | None:
        return next(
            (image for image in self.flash_images if image.offset == OTA_DATA_OFFSET),
            None,
        )

    @property
    def ota_manifest(self) -> dict[str, Any] | None:
        if self.ota_application is None:
            return None
        return dict(self.ota_application.manifest)

    @classmethod
    def open(cls, source: str | Path) -> "ReleasePackage":
        source_path = Path(source).expanduser().resolve()
        if source_path.is_file() and zipfile.is_zipfile(source_path):
            cleanup = Path(tempfile.mkdtemp(prefix="firmware-release-"))
            try:
                snapshot_archive = cleanup / "release.zip"
                shutil.copyfile(source_path, snapshot_archive)
                archive_sha256 = _sha256(snapshot_archive)
                checksum_verified = cls._verify_archive_sidecar(
                    source_path,
                    archive_sha256,
                )
                extracted = cleanup / "extracted"
                extracted.mkdir()
                _safe_extract(snapshot_archive, extracted)
                root = cls._find_manifest_root(extracted)
                return cls._load(
                    root,
                    cleanup,
                    release_id_hint=source_path.stem,
                    archive_sha256=archive_sha256,
                    archive_checksum_verified=checksum_verified,
                )
            except Exception:
                shutil.rmtree(cleanup, ignore_errors=True)
                raise
        if source_path.is_dir():
            cleanup = Path(tempfile.mkdtemp(prefix="firmware-release-"))
            try:
                snapshot = _snapshot_directory(source_path, cleanup)
                return cls._load(
                    cls._find_manifest_root(snapshot),
                    cleanup,
                    release_id_hint=source_path.name,
                )
            except Exception:
                shutil.rmtree(cleanup, ignore_errors=True)
                raise
        raise PackageError(f"release package not found: {source_path}")

    @staticmethod
    def _verify_archive_sidecar(archive: Path, actual: str) -> bool:
        candidates = (
            Path(str(archive) + ".sha256"),
            archive.with_suffix(".sha256"),
        )
        sidecar = next((path for path in candidates if path.is_file()), None)
        if sidecar is None:
            return False
        try:
            expected = sidecar.read_text(encoding="utf-8").split()[0].lower()
        except (OSError, IndexError) as exc:
            raise PackageError(f"cannot read ZIP SHA-256 sidecar: {sidecar}") from exc
        if expected != actual:
            raise PackageError(
                f"ZIP SHA-256 mismatch: expected {expected}, got {actual}"
            )
        return True

    @staticmethod
    def _find_manifest_root(root: Path) -> Path:
        direct = root / "release-manifest.json"
        if direct.is_file():
            return root
        native = root / "firmware-manifest.json"
        if native.is_file():
            return root
        manifests = list(root.glob("*/release-manifest.json"))
        if len(manifests) == 1:
            return manifests[0].parent
        if len(manifests) > 1:
            raise PackageError("release package contains multiple manifests")
        native_manifests = list(root.glob("*/firmware-manifest.json"))
        if len(native_manifests) == 1:
            return native_manifests[0].parent
        if not native_manifests:
            raise PackageError("release-manifest.json or firmware-manifest.json not found")
        raise PackageError("release package contains multiple manifests")

    @classmethod
    def _load(
        cls,
        root: Path,
        cleanup: Path | None = None,
        *,
        release_id_hint: str | None = None,
        archive_sha256: str | None = None,
        archive_checksum_verified: bool = False,
    ) -> "ReleasePackage":
        release_manifest_path = root / "release-manifest.json"
        try:
            if release_manifest_path.is_file():
                manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
            else:
                manifest = cls._native_release_manifest(root, release_id_hint)
        except (OSError, json.JSONDecodeError) as exc:
            raise PackageError(f"cannot read release manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise PackageError("release-manifest.json must contain an object")

        for key in ("release_id", "target", "git_tag", "source_commit"):
            _required_string(manifest, key)
        if "esp_idf" in manifest:
            _required_string(manifest, "esp_idf")
        for key in ("hardware_id", "build_id", "validation_state"):
            if key in manifest:
                _required_string(manifest, key)
        if (
            "validation_state" in manifest
            and manifest["validation_state"] not in VALIDATION_STATES
        ):
            raise PackageError("release-manifest.json validation_state 无效")
        usb = manifest.get("runtime_usb")
        if not isinstance(usb, dict):
            raise PackageError("release-manifest.json 缺少 runtime_usb")
        _required_string(usb, "product")
        _usb_id(usb, "vid")
        _usb_id(usb, "pid")

        rollback = manifest.get("rollback")
        if not isinstance(rollback, dict):
            raise PackageError("release-manifest.json 缺少 rollback")
        if rollback.get("erase_flash") is not False:
            raise PackageError("unsafe release: rollback.erase_flash must be false")
        if rollback.get("preserves_nvs") is not True:
            raise PackageError("unsafe release: rollback must preserve NVS")

        declared = manifest.get("files")
        if not isinstance(declared, dict):
            raise PackageError("release-manifest.json 缺少 files")
        images_by_offset: dict[int, FlashImage] = {}
        files_by_relative: dict[str, tuple[Path, str, int]] = {}
        root_resolved = root.resolve()
        for relative, metadata in declared.items():
            if not isinstance(relative, str) or not isinstance(metadata, dict):
                raise PackageError("invalid files entry in release manifest")
            path = (root / relative).resolve()
            if not path.is_relative_to(root_resolved):
                raise PackageError(f"unsafe manifest path: {relative}")
            if not path.is_file():
                raise PackageError(f"release file missing: {relative}")
            expected = _required_string(metadata, "sha256").lower()
            actual = _sha256(path)
            if actual != expected:
                raise PackageError(
                    f"SHA-256 mismatch for {relative}: expected {expected}, got {actual}"
                )
            files_by_relative[relative] = (path, expected, path.stat().st_size)
            if "offset" in metadata:
                try:
                    offset = int(str(metadata["offset"]), 0)
                except ValueError as exc:
                    raise PackageError(f"invalid flash offset for {relative}") from exc
                if offset in images_by_offset:
                    raise PackageError(f"duplicate flash offset 0x{offset:X}")
                images_by_offset[offset] = FlashImage(
                    relative_path=relative,
                    path=path,
                    offset=offset,
                    sha256=expected,
                    size=path.stat().st_size,
                )

        ota_manifest_relative = manifest.get("ota_application_manifest")
        has_ota = ota_manifest_relative is not None
        expected_offsets = OTA_BOOT_OFFSETS if has_ota else LEGACY_OFFSETS
        if tuple(sorted(images_by_offset)) != expected_offsets:
            actual = ", ".join(f"0x{offset:X}" for offset in sorted(images_by_offset))
            expected = ", ".join(f"0x{offset:X}" for offset in expected_offsets)
            raise PackageError(
                f"release layout must be {expected}; got {actual or 'none'}"
            )
        images = tuple(images_by_offset[offset] for offset in expected_offsets)
        bootloader, partition_table, application = images[:3]
        if bootloader.size > partition_table.offset:
            raise PackageError("bootloader 超过 0x8000 分区表边界")
        if partition_table.offset + partition_table.size > application.offset:
            raise PackageError("partition table 超过 0x10000 应用边界")
        partitions = _partitions(partition_table.path)
        application_limit = _application_partition_limit(
            partitions,
            application.offset,
        )
        if application.offset + application.size > application_limit:
            raise PackageError(
                "application 超过 partition table 声明的应用分区，拒绝覆盖 NVS"
            )
        if any(image.offset + image.size > FLASH_BYTES for image in images):
            raise PackageError("release image 超过 16MB Flash 边界")

        ota_application = None
        if has_ota:
            if not isinstance(ota_manifest_relative, str) or not ota_manifest_relative:
                raise PackageError("ota_application_manifest 必须是有效路径")
            for key in ("hardware_id", "build_id", "validation_state"):
                _required_string(manifest, key)
            ota_data = images_by_offset[OTA_DATA_OFFSET]
            ota_data_limit = _partition_limit(
                partitions,
                OTA_DATA_OFFSET,
                partition_type=0x01,
                subtype=0x00,
                label="otadata",
            )
            if ota_data.offset + ota_data.size > ota_data_limit:
                raise PackageError("OTA data 超过 partition table 声明的 otadata 分区")
            ota_application = cls._load_ota_application(
                root,
                manifest,
                ota_manifest_relative,
                files_by_relative,
            )
            if application.sha256 != ota_application.sha256:
                raise PackageError("BOOT 恢复与 OTA 更新必须使用同一应用镜像")

        release_id = _required_string(manifest, "release_id")
        trusted_hash = TRUSTED_RELEASE_ARCHIVES.get(release_id)
        trusted_source_verified = False
        if trusted_hash is not None:
            if archive_sha256 != trusted_hash:
                raise PackageError(
                    f"稳定发布 {release_id} 与工具内置可信 SHA-256 不匹配"
                )
            trusted_source_verified = True
        return cls(
            root,
            manifest,
            images,
            ota_application,
            cleanup,
            archive_sha256,
            archive_checksum_verified,
            trusted_source_verified,
        )

    @staticmethod
    def _load_ota_application(
        root: Path,
        release_manifest: dict[str, Any],
        relative_manifest: str,
        files: dict[str, tuple[Path, str, int]],
    ) -> OtaApplication:
        manifest_entry = files.get(relative_manifest)
        if manifest_entry is None:
            raise PackageError("ota_application_manifest 必须在 files 中声明")
        manifest_path = manifest_entry[0]
        try:
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackageError(f"无法读取 OTA application manifest: {exc}") from exc
        if not isinstance(value, dict):
            raise PackageError("OTA application manifest 必须是 JSON object")
        required = {
            "format": str,
            "product_id": str,
            "hardware_id": str,
            "version": str,
            "image": str,
            "size": int,
            "sha256": str,
            "build_id": str,
            "validation_state": str,
        }
        for key, expected_type in required.items():
            if type(value.get(key)) is not expected_type:
                raise PackageError(f"OTA application manifest 字段 {key} 无效")
        if value["format"] != OTA_MANIFEST_FORMAT:
            raise PackageError("OTA application manifest format 无效")
        if value["size"] <= 0:
            raise PackageError("OTA application manifest size 无效")
        if not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
            raise PackageError("OTA application manifest sha256 无效")
        if value["validation_state"] not in VALIDATION_STATES:
            raise PackageError("OTA application manifest validation_state 无效")
        for key in ("hardware_id", "build_id", "validation_state"):
            if value[key] != release_manifest[key]:
                raise PackageError(
                    f"OTA application manifest {key} 与 release-manifest.json 不一致"
                )

        image_name = value["image"]
        if Path(image_name).name != image_name:
            raise PackageError("OTA application image 必须与其 manifest 位于同一目录")
        image_path = (manifest_path.parent / image_name).resolve()
        root_resolved = root.resolve()
        if not image_path.is_relative_to(root_resolved):
            raise PackageError("OTA application image 路径越界")
        relative_image = image_path.relative_to(root_resolved).as_posix()
        image_entry = files.get(relative_image)
        if image_entry is None:
            raise PackageError("OTA application image 必须在 files 中声明")
        _path, digest, size = image_entry
        if value["size"] != size or value["sha256"] != digest:
            raise PackageError("OTA application image 与其 manifest 不一致")
        return OtaApplication(
            relative_manifest_path=relative_manifest,
            manifest_path=manifest_path,
            relative_path=relative_image,
            path=image_path,
            product_id=value["product_id"],
            hardware_id=value["hardware_id"],
            version=value["version"],
            build_id=value["build_id"],
            validation_state=value["validation_state"],
            sha256=value["sha256"],
            size=value["size"],
            manifest=dict(value),
        )

    @staticmethod
    def _native_release_manifest(
        root: Path, release_id_hint: str | None
    ) -> dict[str, Any]:
        manifest_path = root / "firmware-manifest.json"
        try:
            firmware = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackageError(f"cannot read firmware-manifest.json: {exc}") from exc
        if not isinstance(firmware, dict):
            raise PackageError("firmware-manifest.json must contain an object")
        for key in (
            "build_target",
            "hardware_id",
            "build_id",
            "validation_state",
            "git_commit",
            "image",
        ):
            _required_string(firmware, key)
        image_name = firmware["image"]
        if Path(image_name).name != image_name:
            raise PackageError("firmware-manifest.json image must be a filename")

        paths = {
            "bootloader/bootloader.bin": 0x0,
            "partition_table/partition-table.bin": 0x8000,
            image_name: 0x10000,
            "ota_data_initial.bin": OTA_DATA_OFFSET,
        }
        files: dict[str, dict[str, Any]] = {}
        for relative, offset in paths.items():
            path = root / relative
            if not path.is_file():
                raise PackageError(f"release file missing: {relative}")
            files[relative] = {
                "offset": f"0x{offset:X}",
                "sha256": _sha256(path),
            }
        files["firmware-manifest.json"] = {
            "sha256": _sha256(manifest_path),
        }
        return {
            "release_id": release_id_hint or root.name,
            "git_tag": firmware["build_id"],
            "source_commit": firmware["git_commit"],
            "target": firmware["build_target"],
            "hardware_id": firmware["hardware_id"],
            "build_id": firmware["build_id"],
            "validation_state": firmware["validation_state"],
            "runtime_usb": {
                "product": "Codex Micro",
                "vid": "303A",
                "pid": "8360",
            },
            "ota_application_manifest": "firmware-manifest.json",
            "rollback": {
                "recommended_scope": "application-only",
                "application_offset": "0x10000",
                "erase_flash": False,
                "preserves_nvs": True,
            },
            "files": files,
        }

    def close(self) -> None:
        if self._cleanup is not None:
            if self._cleanup_finalizer is not None:
                self._cleanup_finalizer()
            self._cleanup = None

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
