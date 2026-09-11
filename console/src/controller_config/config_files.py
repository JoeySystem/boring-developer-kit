from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from controller_config import __version__
from controller_config.protocol.contract import Contract, ContractError
from controller_config.protocol.framing import canonical_json_bytes


CONFIG_FILE_FORMAT = "boring-controller-config"
CONFIG_FILE_VERSION = 1
MAX_CONFIG_FILE_BYTES = 256 * 1024


class ConfigFileError(ValueError):
    """A user-selected configuration file cannot be safely used."""


@dataclass(frozen=True)
class ConfigPackage:
    kind: str
    hardware_ids: tuple[str, ...]
    schema_version: int
    created_by: str
    base_generation: int
    base_digest: str
    payload_digest: str
    config: dict[str, Any]


def config_payload_digest(config: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(config)).hexdigest()


def export_config_package(
    path: Path,
    *,
    config: dict[str, Any],
    hardware_ids: tuple[str, ...],
    kind: str,
    base_generation: int,
    base_digest: str,
    unresolved_write: dict[str, Any] | None = None,
) -> None:
    if kind not in {"confirmed", "draft"}:
        raise ConfigFileError("配置文件类型必须是 confirmed 或 draft")
    package = {
        "format": CONFIG_FILE_FORMAT,
        "format_version": CONFIG_FILE_VERSION,
        "kind": kind,
        "product_id": config.get("product_id"),
        "hardware_ids": list(hardware_ids),
        "schema_version": config.get("schema_version"),
        "created_by": f"boring-controller-config/{__version__}",
        "base_generation": base_generation,
        "base_digest": base_digest,
        "payload_digest": config_payload_digest(config),
        "config": copy.deepcopy(config),
    }
    if unresolved_write is not None:
        # Informational recovery context, not an instruction to replay a write.
        # The payload remains an ordinary draft supported by the existing importer.
        package["unresolved_write"] = copy.deepcopy(unresolved_write)
    path.write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_config_package(
    path: Path,
    contract: Contract,
    *,
    current_hardware_id: str,
) -> ConfigPackage:
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_CONFIG_FILE_BYTES + 1)
    except OSError as exc:
        raise ConfigFileError(f"无法读取配置文件：{exc}") from exc
    if len(content) > MAX_CONFIG_FILE_BYTES:
        raise ConfigFileError(
            f"配置文件超过 {MAX_CONFIG_FILE_BYTES // 1024} KiB 导入上限"
        )
    try:
        raw = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigFileError(f"无法读取配置文件：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigFileError("配置文件根节点必须是 object")
    if raw.get("format") != CONFIG_FILE_FORMAT or raw.get("format_version") != CONFIG_FILE_VERSION:
        raise ConfigFileError("不支持该配置文件格式或版本")
    if raw.get("kind") not in {"confirmed", "draft"}:
        raise ConfigFileError("配置文件 kind 无效")
    if raw.get("product_id") != contract.product_id:
        raise ConfigFileError("配置文件 product_id 与 BORING 合同不一致")
    if raw.get("schema_version") != contract.schema_version:
        raise ConfigFileError("配置文件 Schema 版本不受支持")
    hardware_ids = raw.get("hardware_ids")
    if not isinstance(hardware_ids, list) or not all(isinstance(value, str) for value in hardware_ids):
        raise ConfigFileError("配置文件 hardware_ids 无效")
    if current_hardware_id not in hardware_ids:
        raise ConfigFileError("配置文件不适用于当前设备硬件")
    config = raw.get("config")
    if not isinstance(config, dict):
        raise ConfigFileError("配置文件缺少完整 config object")
    if config.get("product_id") != raw.get("product_id"):
        raise ConfigFileError("配置内外的 product_id 不一致")
    if config.get("hardware_id") not in hardware_ids:
        raise ConfigFileError("配置内外的 hardware_id 不一致")
    if config.get("schema_version") != raw.get("schema_version"):
        raise ConfigFileError("配置内外的 schema_version 不一致")
    expected_digest = raw.get("payload_digest")
    if not isinstance(expected_digest, str) or expected_digest != config_payload_digest(config):
        raise ConfigFileError("配置 payload 摘要不匹配，文件内容不完整或已变更")
    try:
        contract.validate_config(config)
    except ContractError as exc:
        raise ConfigFileError(str(exc)) from exc
    created_by = raw.get("created_by")
    base_generation = raw.get("base_generation")
    base_digest = raw.get("base_digest")
    if not isinstance(created_by, str) or not created_by:
        raise ConfigFileError("配置文件 created_by 无效")
    if not isinstance(base_generation, int) or isinstance(base_generation, bool):
        raise ConfigFileError("配置文件 base_generation 无效")
    if not isinstance(base_digest, str):
        raise ConfigFileError("配置文件 base_digest 无效")
    return ConfigPackage(
        kind=str(raw["kind"]),
        hardware_ids=tuple(hardware_ids),
        schema_version=int(raw["schema_version"]),
        created_by=created_by,
        base_generation=base_generation,
        base_digest=base_digest,
        payload_digest=expected_digest,
        config=copy.deepcopy(config),
    )
