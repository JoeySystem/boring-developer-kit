"""Local workspace handoff across a desktop application update.

This file is not a device backup or an instruction to replay a device write.
Only a freshly authenticated, matching device can receive its recovered local
draft, and only while the confirmed configuration still agrees.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSaveFile, QStandardPaths

from controller_config import __version__
from controller_config.config_files import ConfigPackage
from controller_config.drafts import LocalDraft
from controller_config.models import AppState, DeviceSnapshot

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


class UpdateRecoveryError(ValueError):
    """Recovery data cannot currently be saved or consumed; keep the original."""


class UpdateRecoveryStore:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            root = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            path = Path(root) / "update-workspace.json"
        self.path = Path(path)
        self.last_error = ""

    def save(
        self,
        snapshot: DeviceSnapshot | None,
        draft: LocalDraft | None,
        workspace: dict[str, Any],
    ) -> None:
        identity = None
        if draft is not None:
            identity = {"serial": draft.serial, "hardware_id": draft.hardware_id}
        elif snapshot is not None:
            identity = {
                key: snapshot.identity.get(key) for key in ("serial", "hardware_id")
            }
        saved = {
            "device": identity,
            "draft": None if draft is None else {
                "confirmed_config": draft.confirmed_config,
                "candidate_config": copy.deepcopy(draft.config),
                "base_generation": draft.base_generation,
                "base_digest": draft.base_digest,
            },
            "workspace": copy.deepcopy(workspace),
        }
        _validate_structure(saved)
        try:
            data = (json.dumps(saved, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except (OSError, TypeError, ValueError) as exc:
            raise UpdateRecoveryError(f"无法保存更新前的工作区：{exc}") from exc
        output = QSaveFile(str(self.path))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise UpdateRecoveryError(f"无法保存更新前的工作区：{output.errorString()}")
        if output.write(data) != len(data):
            output.cancelWriting()
            raise UpdateRecoveryError(f"无法保存更新前的工作区：{output.errorString()}")
        if not output.commit():
            raise UpdateRecoveryError(f"无法保存更新前的工作区：{output.errorString()}")

    def load(self) -> dict[str, Any] | None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise UpdateRecoveryError(f"无法读取更新前的工作区，文件已保留：{exc}") from exc
        _validate_structure(saved)
        # Config schema and current hardware capabilities are checked by the
        # existing configuration importer at apply time, after device reconnect.
        return saved

    def apply(self, view_model: MainViewModel, saved: dict[str, Any]) -> bool:
        self.last_error = ""
        _validate_structure(saved)
        identity = saved["device"]
        stored_draft = saved["draft"]
        if identity is None:
            return True
        snapshot = view_model.model.snapshot
        if (
            snapshot is None
            or view_model.model.state not in {AppState.READY, AppState.READ_ONLY}
            or not snapshot.trust.is_authenticated
        ):
            self.last_error = "更新前的工作区已保留，连接并认证原设备后可恢复。"
            return False
        if any(snapshot.identity.get(key) != identity[key] for key in identity):
            self.last_error = "当前连接的不是原设备；更新前的工作区已保留。"
            return False
        if stored_draft is None:
            return True
        draft = view_model.draft
        if draft is None or (draft.serial, draft.hardware_id) != (
            identity["serial"], identity["hardware_id"]
        ):
            self.last_error = "原设备的配置尚未读取完成，工作区已保留。"
            return False
        confirmed = stored_draft["confirmed_config"]
        candidate = stored_draft["candidate_config"]
        if snapshot.config != confirmed or draft.confirmed_config != confirmed:
            self.last_error = "原设备配置已变化，未自动合并旧草稿；恢复文件已保留，需人工核对。"
            return False
        if draft.is_dirty:
            if draft.config == candidate:
                return True  # Workspace restoration may be retried before clear().
            self.last_error = "当前已有新的本地修改，未覆盖；更新前的工作区已保留。"
            return False
        package = ConfigPackage(
            kind="draft",
            hardware_ids=(identity["hardware_id"],),
            schema_version=candidate["schema_version"],
            created_by=f"boring-controller-config/{__version__}",
            base_generation=stored_draft["base_generation"],
            base_digest=stored_draft["base_digest"],
            # The existing in-memory importer validates config, not a file
            # digest. No redundant payload checksum is needed for local handoff.
            payload_digest="",
            config=copy.deepcopy(candidate),
        )
        try:
            view_model.apply_configuration_import(package)
        except ValueError as exc:
            self.last_error = f"更新前的草稿暂时无法恢复，文件已保留：{exc}"
            return False
        return True

    def clear(self) -> None:
        """Call only after BOTH local draft and UI workspace were restored."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise UpdateRecoveryError(f"工作区已恢复，但无法删除恢复文件：{exc}") from exc


def _validate_structure(saved: Any) -> None:
    if not isinstance(saved, dict) or not {"device", "draft", "workspace"} <= saved.keys():
        raise UpdateRecoveryError("更新恢复文件结构无效，文件已保留。")
    if not isinstance(saved["workspace"], dict):
        raise UpdateRecoveryError("更新恢复文件缺少工作区，文件已保留。")
    device = saved["device"]
    if device is not None and (
        not isinstance(device, dict)
        or set(device) != {"serial", "hardware_id"}
        or any(not isinstance(value, str) or not value for value in device.values())
    ):
        raise UpdateRecoveryError("更新恢复文件的设备身份无效，文件已保留。")
    draft = saved["draft"]
    if draft is None:
        return
    if device is None or not isinstance(draft, dict):
        raise UpdateRecoveryError("更新恢复文件的草稿无效，文件已保留。")
    if type(draft.get("base_generation")) is not int or not isinstance(draft.get("base_digest"), str):
        raise UpdateRecoveryError("更新恢复文件缺少草稿来源，文件已保留。")
    for key in ("confirmed_config", "candidate_config"):
        config = draft.get(key)
        if (
            not isinstance(config, dict)
            or type(config.get("schema_version")) is not int
            or config.get("hardware_id") != device["hardware_id"]
        ):
            raise UpdateRecoveryError("更新恢复文件的配置无效，文件已保留。")
