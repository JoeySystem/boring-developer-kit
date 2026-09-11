from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSaveFile

from controller_config.extensions.contracts import (
    ExtensionContractError,
    ExtensionManifest,
)


class ExtensionRegistryError(ValueError):
    """The local extension registry cannot be read or persisted."""


@dataclass(frozen=True)
class ExtensionRegistryRecord:
    manifest: ExtensionManifest
    enabled: bool = False

    @classmethod
    def from_mapping(cls, value: object) -> "ExtensionRegistryRecord":
        if not isinstance(value, dict):
            raise ExtensionRegistryError("扩展注册记录必须是 object")
        expected = {"manifest", "enabled"}
        missing = expected - set(value)
        unknown = set(value) - expected
        if missing:
            raise ExtensionRegistryError(
                "扩展注册记录缺少字段：" + ", ".join(sorted(missing))
            )
        if unknown:
            raise ExtensionRegistryError(
                "扩展注册记录包含未声明字段："
                + ", ".join(sorted(unknown))
            )
        enabled = value["enabled"]
        if not isinstance(enabled, bool):
            raise ExtensionRegistryError("扩展 enabled 必须是布尔值")
        try:
            manifest = ExtensionManifest.from_mapping(value["manifest"])
        except ExtensionContractError as exc:
            raise ExtensionRegistryError(f"扩展注册表中的 manifest 无效：{exc}") from exc
        return cls(manifest=manifest, enabled=enabled)

    def as_mapping(self) -> dict[str, object]:
        return {
            "manifest": self.manifest.as_mapping(),
            "enabled": self.enabled,
        }


class ExtensionRegistry:
    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[ExtensionRegistryRecord, ...]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExtensionRegistryError(f"无法读取扩展注册表：{exc}") from exc
        if not isinstance(value, dict):
            raise ExtensionRegistryError("扩展注册表必须是 object")
        expected = {"version", "extensions"}
        missing = expected - set(value)
        unknown = set(value) - expected
        if missing:
            raise ExtensionRegistryError(
                "扩展注册表缺少字段：" + ", ".join(sorted(missing))
            )
        if unknown:
            raise ExtensionRegistryError(
                "扩展注册表包含未声明字段："
                + ", ".join(sorted(unknown))
            )
        if value["version"] != self.VERSION:
            raise ExtensionRegistryError("扩展注册表版本不受支持")
        items = value["extensions"]
        if not isinstance(items, list):
            raise ExtensionRegistryError("扩展注册表 extensions 必须是数组")
        records = tuple(ExtensionRegistryRecord.from_mapping(item) for item in items)
        extension_ids = [record.manifest.extension_id for record in records]
        if len(extension_ids) != len(set(extension_ids)):
            raise ExtensionRegistryError("扩展注册表中的扩展 ID 不得重复")
        return records

    def save(self, records: tuple[ExtensionRegistryRecord, ...]) -> None:
        extension_ids = [record.manifest.extension_id for record in records]
        if len(extension_ids) != len(set(extension_ids)):
            raise ExtensionRegistryError("扩展注册表中的扩展 ID 不得重复")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "extensions": [record.as_mapping() for record in records],
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        output = QSaveFile(str(self.path))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise ExtensionRegistryError(
                f"无法保存扩展注册表：{output.errorString()}"
            )
        if output.write(data) != len(data) or not output.commit():
            raise ExtensionRegistryError(
                f"无法保存扩展注册表：{output.errorString()}"
            )
