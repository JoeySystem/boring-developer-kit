from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QSaveFile


class ExtensionBindingError(ValueError):
    """A prompt action binding is invalid or cannot be persisted."""


@dataclass(frozen=True)
class ExtensionActionBinding:
    device_serial: str
    prompt_id: int
    extension_id: str
    action_id: str

    def validate(self) -> None:
        if not self.device_serial.strip():
            raise ExtensionBindingError("设备序列号不能为空")
        if not isinstance(self.prompt_id, int) or isinstance(self.prompt_id, bool):
            raise ExtensionBindingError("提示词槽位必须是整数")
        if not 1 <= self.prompt_id <= 12:
            raise ExtensionBindingError("提示词槽位必须位于 1–12")
        if not self.extension_id.strip() or not self.action_id.strip():
            raise ExtensionBindingError("扩展 ID 和 action ID 不能为空")

    @classmethod
    def from_mapping(cls, value: object) -> "ExtensionActionBinding":
        if not isinstance(value, dict) or set(value) != {
            "device_serial",
            "prompt_id",
            "extension_id",
            "action_id",
        }:
            raise ExtensionBindingError("扩展 action 绑定格式无效")
        binding = cls(
            device_serial=value["device_serial"],
            prompt_id=value["prompt_id"],
            extension_id=value["extension_id"],
            action_id=value["action_id"],
        )
        if not all(
            isinstance(item, str)
            for item in (
                binding.device_serial,
                binding.extension_id,
                binding.action_id,
            )
        ):
            raise ExtensionBindingError("扩展 action 绑定文本字段无效")
        binding.validate()
        return binding

    def as_mapping(self) -> dict[str, object]:
        return asdict(self)


class ExtensionBindingStore:
    VERSION = 1

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[ExtensionActionBinding, ...]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExtensionBindingError(f"无法读取扩展 action 绑定：{exc}") from exc
        if not isinstance(value, dict) or set(value) != {"version", "bindings"}:
            raise ExtensionBindingError("扩展 action 绑定文件格式无效")
        if value["version"] != self.VERSION or not isinstance(value["bindings"], list):
            raise ExtensionBindingError("扩展 action 绑定文件版本不受支持")
        bindings = tuple(
            ExtensionActionBinding.from_mapping(item) for item in value["bindings"]
        )
        keys = [(item.device_serial, item.prompt_id) for item in bindings]
        if len(keys) != len(set(keys)):
            raise ExtensionBindingError("同一设备的提示词槽位只能绑定一个扩展 action")
        return bindings

    def save(self, bindings: tuple[ExtensionActionBinding, ...]) -> None:
        for binding in bindings:
            binding.validate()
        keys = [(item.device_serial, item.prompt_id) for item in bindings]
        if len(keys) != len(set(keys)):
            raise ExtensionBindingError("同一设备的提示词槽位只能绑定一个扩展 action")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "bindings": [binding.as_mapping() for binding in bindings],
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        output = QSaveFile(str(self.path))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise ExtensionBindingError(
                f"无法保存扩展 action 绑定：{output.errorString()}"
            )
        if output.write(data) != len(data) or not output.commit():
            raise ExtensionBindingError(
                f"无法保存扩展 action 绑定：{output.errorString()}"
            )
