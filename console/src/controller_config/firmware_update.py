from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Any
from zipfile import BadZipFile, ZipFile

from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import Command
from controller_config.protocol.contract import Contract


MANIFEST_FORMAT = "wmp-firmware-update-v1"
ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESC_MAGIC = 0xABCD5432
WMP_DESCRIPTOR_MAGIC = 0x46504D57
WMP_DESCRIPTOR_VERSION = 1
APP_DESC_OFFSET = 24 + 8
CUSTOM_DESC_OFFSET = APP_DESC_OFFSET + 256
WMP_DESCRIPTOR = struct.Struct("<IHH32s32s")
VALIDATION_STATES = frozenset({"built", "sample-verified", "rc", "released"})
FIRMWARE_STATES = frozenset(
    {"IDLE", "RECEIVING", "FINALIZING", "READY_TO_REBOOT", "FAILED"}
)


class FirmwarePackageError(ValueError):
    """The selected update package cannot safely be offered to this device."""


class FirmwareUpdateState(str, Enum):
    IDLE = "idle"
    PACKAGE_READY = "package_ready"
    CHECKING = "checking"
    NEEDS_ABORT = "needs_abort"
    BEGINNING = "beginning"
    TRANSFERRING = "transferring"
    FINALIZING = "finalizing"
    WAITING_RECONNECT = "waiting_reconnect"
    VERIFYING = "verifying"
    ABORTING = "aborting"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class FirmwarePackage:
    source_path: Path
    image_path: Path
    product_id: str
    hardware_id: str
    version: str
    size: int
    sha256: str
    build_id: str = ""
    build_target: str = ""
    validation_state: str = ""
    git_dirty: bool | None = None
    image_data: bytes = field(default=b"", repr=False)
    manifest: dict[str, Any] = field(default_factory=dict, repr=False)
    source_kind: str = "unclassified"

    @classmethod
    def load(cls, package_path: Path, contract: Contract) -> "FirmwarePackage":
        package_path = package_path.expanduser().resolve()
        archive_manifest_name = ""
        if package_path.suffix.lower() == ".bin":
            raise FirmwarePackageError(
                "不能单独导入 .bin；请选择完整 ZIP 维护包或 firmware-manifest.json"
            )
        if package_path.suffix.lower() == ".zip":
            raw, archive_manifest_name = _read_archive_manifest(package_path)
        else:
            try:
                raw = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FirmwarePackageError(f"无法读取固件 manifest：{exc}") from exc
        if not isinstance(raw, dict):
            raise FirmwarePackageError("固件 manifest 根节点必须是 object")

        required = {
            "format": str,
            "product_id": str,
            "hardware_id": str,
            "version": str,
            "image": str,
            "size": int,
            "sha256": str,
        }
        for name, expected in required.items():
            if type(raw.get(name)) is not expected:
                raise FirmwarePackageError(f"固件 manifest 字段 {name} 缺失或类型错误")
        if raw["format"] != MANIFEST_FORMAT:
            raise FirmwarePackageError(f"不支持的固件包格式：{raw['format']}")
        if raw["product_id"] != contract.product_id:
            raise FirmwarePackageError("固件包 product_id 不属于当前 BORING 产品合同")
        if raw["hardware_id"] not in contract.hardware_ids:
            raise FirmwarePackageError("固件包 hardware_id 不在当前 Schema 支持范围内")
        if re.fullmatch(r"[A-Za-z0-9._+-]{1,31}", raw["version"]) is None:
            raise FirmwarePackageError("固件包版本号无效")
        if Path(raw["image"]).name != raw["image"]:
            raise FirmwarePackageError("固件镜像必须与 manifest 位于同一目录")
        if raw["size"] <= 0:
            raise FirmwarePackageError("固件镜像大小必须大于零")
        if re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]) is None:
            raise FirmwarePackageError("固件 manifest sha256 必须是 64 位小写十六进制")

        validation_state = raw.get("validation_state", "")
        if validation_state and validation_state not in VALIDATION_STATES:
            raise FirmwarePackageError("固件 manifest validation_state 无效")
        git_dirty = raw.get("git_dirty")
        if git_dirty is not None and type(git_dirty) is not bool:
            raise FirmwarePackageError("固件 manifest git_dirty 必须是布尔值")

        if archive_manifest_name:
            image_path = Path(raw["image"])
            image_data = _read_archive_image(
                package_path,
                archive_manifest_name,
                raw["image"],
            )
        else:
            image_path = package_path.parent / raw["image"]
            try:
                image_data = image_path.read_bytes()
            except OSError as exc:
                raise FirmwarePackageError(f"无法读取固件镜像：{exc}") from exc
        size = len(image_data)
        if size != raw["size"]:
            raise FirmwarePackageError("固件镜像大小与 manifest 不一致")
        if hashlib.sha256(image_data).hexdigest() != raw["sha256"]:
            raise FirmwarePackageError("固件镜像 SHA-256 与 manifest 不一致")

        image_identity = _read_image_identity(image_data)
        if image_identity != (raw["product_id"], raw["hardware_id"], raw["version"]):
            raise FirmwarePackageError("固件镜像内嵌身份与 manifest 不一致")

        return cls(
            source_path=package_path,
            image_path=image_path,
            product_id=raw["product_id"],
            hardware_id=raw["hardware_id"],
            version=raw["version"],
            size=raw["size"],
            sha256=raw["sha256"],
            build_id=str(raw.get("build_id", "")),
            build_target=str(raw.get("build_target", "")),
            validation_state=str(validation_state),
            git_dirty=git_dirty,
            image_data=image_data,
            manifest=dict(raw),
        )

    def validate_for_device(self, snapshot: DeviceSnapshot) -> int:
        if snapshot.identity.get("product_id") != self.product_id:
            raise FirmwarePackageError("固件包 product_id 与已连接设备不匹配")
        if snapshot.identity.get("hardware_id") != self.hardware_id:
            raise FirmwarePackageError(
                f"固件包目标为 {self.hardware_id}，当前设备为 "
                f"{snapshot.identity.get('hardware_id', 'unknown')}"
            )
        if snapshot.compatibility.get("write") is not True:
            raise FirmwarePackageError("设备当前处于只读兼容状态，不能执行固件维护")
        features = snapshot.capabilities.get("features")
        limits = snapshot.capabilities.get("limits")
        if self.source_kind == "custom" and isinstance(features, dict) and features.get("firmware_signature_required") is True:
            raise FirmwarePackageError("此设备要求设备端签名，当前不支持安装自定义固件；请使用官方固件")
        if not isinstance(features, dict) or features.get("firmware_update") is not True:
            raise FirmwarePackageError("当前设备没有通过 CAPABILITIES 声明固件更新能力")
        if not isinstance(limits, dict):
            raise FirmwarePackageError("设备没有返回固件更新限制")
        chunk_limit = limits.get("firmware_chunk_bytes")
        image_limit = limits.get("firmware_image_bytes")
        if not _is_positive_int(chunk_limit):
            raise FirmwarePackageError("设备没有返回有效的固件分片上限")
        if not _is_positive_int(image_limit) or self.size > image_limit:
            raise FirmwarePackageError("固件镜像超过设备声明的 OTA 镜像上限")
        return min(4096, int(chunk_limit))

    def begin_command(self) -> Command:
        return Command(
            "FW_BEGIN",
            0x40,
            {
                "product_id": self.product_id,
                "hardware_id": self.hardware_id,
                "version": self.version,
                "size": self.size,
                "sha256": self.sha256,
            },
            timeout_ms=5000,
            retries=1,
        )

    def data_command(self, offset: int, chunk_size: int) -> tuple[Command, int]:
        if offset < 0 or offset >= self.size:
            raise FirmwarePackageError("固件分片偏移超出镜像范围")
        chunk = self.image_data[offset : offset + min(chunk_size, self.size - offset)]
        if not chunk:
            raise FirmwarePackageError("固件镜像在 manifest 声明大小之前结束")
        return (
            Command(
                "FW_DATA",
                0x41,
                {"offset": offset, "data": base64.b64encode(chunk).decode("ascii")},
                timeout_ms=5000,
                retries=2,
            ),
            len(chunk),
        )


@dataclass(frozen=True)
class FirmwareStatus:
    state: str
    expected_size: int | None = None
    received_size: int = 0
    expected_sha256: str = ""
    expected_version: str = ""
    running_version: str = ""
    running_partition: str = ""
    target_partition: str = ""
    rollback_pending: bool | None = None
    reboot_pending: bool | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "FirmwareStatus":
        result = payload.get("result")
        update = result.get("firmware_update") if isinstance(result, dict) else None
        if not isinstance(update, dict) or update.get("state") not in FIRMWARE_STATES:
            raise FirmwarePackageError("设备返回的 FW_STATUS 结构无效")
        received = update.get("received_size", 0)
        expected = update.get("expected_size")
        if not _is_non_negative_int(received):
            raise FirmwarePackageError("FW_STATUS received_size 无效")
        if expected is not None and not _is_non_negative_int(expected):
            raise FirmwarePackageError("FW_STATUS expected_size 无效")
        return cls(
            state=update["state"],
            expected_size=expected,
            received_size=int(received),
            expected_sha256=str(update.get("expected_sha256", "")),
            expected_version=str(update.get("expected_version", "")),
            running_version=str(update.get("running_version", "")),
            running_partition=str(update.get("running_partition", "")),
            target_partition=str(update.get("target_partition", "")),
            rollback_pending=update.get("rollback_pending")
            if isinstance(update.get("rollback_pending"), bool)
            else None,
            reboot_pending=update.get("reboot_pending")
            if isinstance(update.get("reboot_pending"), bool)
            else None,
        )

    def matches(self, package: FirmwarePackage) -> bool:
        return (
            self.expected_size == package.size
            and self.expected_sha256 == package.sha256
            and self.expected_version == package.version
        )


@dataclass(frozen=True)
class FirmwareUpdateTransaction:
    state: FirmwareUpdateState = FirmwareUpdateState.IDLE
    message: str = "尚未选择固件维护包"
    technical: str = ""
    package: FirmwarePackage | None = None
    serial: str = ""
    chunk_size: int = 0
    received_size: int = 0
    in_flight_size: int = 0
    target_partition: str = ""
    abort_requested: bool = False
    restart_failed: bool = False  # Only the first status check of an explicit Start/Retry.

    @property
    def progress_percent(self) -> int:
        if self.package is None or self.package.size <= 0:
            return 0
        return min(100, int(self.received_size * 100 / self.package.size))

    @property
    def is_busy(self) -> bool:
        return self.state in {
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
            FirmwareUpdateState.FINALIZING,
            FirmwareUpdateState.WAITING_RECONNECT,
            FirmwareUpdateState.VERIFYING,
            FirmwareUpdateState.ABORTING,
            FirmwareUpdateState.PAUSED,
        }

    @property
    def blocks_editing(self) -> bool:
        return self.is_busy or self.state is FirmwareUpdateState.NEEDS_ABORT

    @property
    def can_abort(self) -> bool:
        return self.state in {
            FirmwareUpdateState.NEEDS_ABORT,
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
            FirmwareUpdateState.PAUSED,
        }


def firmware_status_command() -> Command:
    return Command("FW_STATUS", 0x42, {}, timeout_ms=4000, retries=1)


def firmware_end_command() -> Command:
    return Command("FW_END", 0x43, {}, timeout_ms=8000, retries=3)


def firmware_abort_command() -> Command:
    return Command("FW_ABORT", 0x44, {}, timeout_ms=5000, retries=1)


def _read_archive_manifest(archive_path: Path) -> tuple[Any, str]:
    try:
        with ZipFile(archive_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and PurePosixPath(info.filename).name == "firmware-manifest.json"
            ]
            if not matches:
                raise FirmwarePackageError(
                    "ZIP 固件维护包中找不到 firmware-manifest.json"
                )
            if len(matches) != 1:
                raise FirmwarePackageError(
                    "ZIP 固件维护包只能包含一份 firmware-manifest.json"
                )
            manifest = matches[0]
            raw = json.loads(archive.read(manifest))
            return raw, manifest.filename
    except FirmwarePackageError:
        raise
    except (
        BadZipFile,
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise FirmwarePackageError(f"无法读取 ZIP 固件维护包：{exc}") from exc


def _read_archive_image(
    archive_path: Path,
    manifest_name: str,
    image_name: str,
) -> bytes:
    image_member = str(PurePosixPath(manifest_name).parent / image_name)
    try:
        with ZipFile(archive_path) as archive:
            matches = [
                info
                for info in archive.infolist()
                if not info.is_dir() and info.filename == image_member
            ]
            if len(matches) != 1:
                raise FirmwarePackageError(
                    "ZIP 固件维护包中找不到固件镜像，或镜像存在重复项"
                )
            return archive.read(matches[0])
    except FirmwarePackageError:
        raise
    except (BadZipFile, OSError, RuntimeError) as exc:
        raise FirmwarePackageError(f"无法读取 ZIP 固件镜像：{exc}") from exc


def _read_image_identity(image_data: bytes) -> tuple[str, str, str]:
    prefix = image_data[: CUSTOM_DESC_OFFSET + WMP_DESCRIPTOR.size]
    if len(prefix) < CUSTOM_DESC_OFFSET + WMP_DESCRIPTOR.size:
        raise FirmwarePackageError("固件镜像过小，缺少 ESP/WMP 描述符")
    if prefix[0] != ESP_IMAGE_MAGIC:
        raise FirmwarePackageError("固件镜像不是有效的 ESP 应用镜像")
    if struct.unpack_from("<I", prefix, APP_DESC_OFFSET)[0] != ESP_APP_DESC_MAGIC:
        raise FirmwarePackageError("固件镜像缺少有效的 ESP app descriptor")
    version = _descriptor_string(prefix[APP_DESC_OFFSET + 16 : APP_DESC_OFFSET + 48])
    project = _descriptor_string(prefix[APP_DESC_OFFSET + 48 : APP_DESC_OFFSET + 80])
    magic, descriptor_version, descriptor_size, product, hardware = WMP_DESCRIPTOR.unpack_from(
        prefix, CUSTOM_DESC_OFFSET
    )
    if (
        magic != WMP_DESCRIPTOR_MAGIC
        or descriptor_version != WMP_DESCRIPTOR_VERSION
        or descriptor_size != WMP_DESCRIPTOR.size
    ):
        raise FirmwarePackageError("固件镜像缺少有效的 WMP 身份描述符")
    if project != "wired_macro_pad":
        raise FirmwarePackageError("固件镜像项目名称不是 wired_macro_pad")
    return _descriptor_string(product), _descriptor_string(hardware), version


def _descriptor_string(raw: bytes) -> str:
    value, separator, _tail = raw.partition(b"\0")
    if not separator:
        raise FirmwarePackageError("固件镜像身份字符串没有正确结束")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FirmwarePackageError("固件镜像身份字符串不是 ASCII") from exc


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
