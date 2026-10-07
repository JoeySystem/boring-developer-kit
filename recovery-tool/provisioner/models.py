from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from .release import ReleasePackage


class ProvisionMode(str, Enum):
    FIRST_INSTALL = "first-install"
    UPDATE = "update"
    RECOVERY = "recovery"
    CHIP_PRELOAD = "chip-preload"


class RecordKind(str, Enum):
    PRODUCTION = "production"
    VALIDATION = "validation"


@dataclass(frozen=True)
class UsbIdentity:
    product: str
    vid: str
    pid: str

    @property
    def vid_int(self) -> int:
        return int(self.vid, 16)

    @property
    def pid_int(self) -> int:
        return int(self.pid, 16)


@dataclass(frozen=True)
class FlashImage:
    relative_path: str
    path: Path
    offset: int
    sha256: str
    size: int


@dataclass(frozen=True)
class OtaApplication:
    relative_manifest_path: str
    manifest_path: Path
    relative_path: str
    path: Path
    product_id: str
    hardware_id: str
    version: str
    build_id: str
    validation_state: str
    sha256: str
    size: int
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class OtaUpdateResult:
    serial: str
    previous_version: str
    target_partition: str
    received_size: int


@dataclass(frozen=True)
class ChipInfo:
    chip: str
    mac: str
    flash_size: str


@dataclass(frozen=True)
class RuntimeVerification:
    port: str
    product: str
    vid: str
    pid: str
    product_id: str
    hardware_id: str
    build_id: str
    serial: str
    config_generation: int
    running_partition: str = ""
    rollback_pending: bool | None = None


@dataclass(frozen=True)
class WmpVerification:
    product_id: str
    hardware_id: str
    build_id: str
    serial: str
    config_generation: int


@dataclass(frozen=True)
class ProvisionRequest:
    package: "ReleasePackage"
    port: str
    mode: ProvisionMode
    operator: str = ""
    record_kind: RecordKind = RecordKind.VALIDATION
    verify_timeout_s: float = 30.0


@dataclass(frozen=True)
class ProvisionResult:
    release_id: str
    mode: ProvisionMode
    chip: ChipInfo | None
    runtime: RuntimeVerification | None
    receipt_path: Path


@dataclass(frozen=True)
class BatchItemResult:
    item: object
    success: bool
    value: object | None = None
    error: str | None = None
    action_required: bool = False
