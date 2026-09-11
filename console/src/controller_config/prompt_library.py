from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSaveFile, QStandardPaths


PROMPT_SLOT_COUNT = 12
PROMPT_NAME_MAX_BYTES = 48
PROMPT_BODY_MAX_BYTES = 4096
PROMPT_TOTAL_BODY_MAX_BYTES = PROMPT_SLOT_COUNT * PROMPT_BODY_MAX_BYTES

# The protocol keeps twelve storage slots for compatibility and headroom. The
# product UI exposes the four fixed prompt-palette slots. These directions select
# a slot inside the palette; they are independent of ordinary Profile mappings.
QUICK_PROMPT_DIRECTIONS = (
    ("joystick.up", 1, "↑", "上"),
    ("joystick.right", 2, "→", "右"),
    ("joystick.down", 3, "↓", "下"),
    ("joystick.left", 4, "←", "左"),
)
QUICK_PROMPT_IDS = tuple(item[1] for item in QUICK_PROMPT_DIRECTIONS)


class PromptLibraryError(ValueError):
    """One prompt draft or local cache does not meet the product limits."""


@dataclass(frozen=True)
class PromptEntry:
    prompt_id: int
    name: str
    body: str

    @property
    def name_bytes(self) -> int:
        return len(self.name.encode("utf-8"))

    @property
    def body_bytes(self) -> int:
        return len(self.body.encode("utf-8"))

    def validate(self) -> None:
        if not 1 <= self.prompt_id <= PROMPT_SLOT_COUNT:
            raise PromptLibraryError(
                f"提示词槽位必须在 1–{PROMPT_SLOT_COUNT} 范围内"
            )
        if not self.name:
            raise PromptLibraryError("提示词名称不能为空")
        if self.name_bytes > PROMPT_NAME_MAX_BYTES:
            raise PromptLibraryError(
                f"提示词名称最多 {PROMPT_NAME_MAX_BYTES} 个 UTF-8 字节"
            )
        if self.body_bytes == 0:
            raise PromptLibraryError("提示词正文不能为空")
        if self.body_bytes > PROMPT_BODY_MAX_BYTES:
            raise PromptLibraryError(
                f"提示词正文最多 {PROMPT_BODY_MAX_BYTES} 个 UTF-8 字节"
            )
        _validate_text_controls(self.name, allow_layout_controls=False, label="提示词名称")
        _validate_text_controls(self.body, allow_layout_controls=True, label="提示词正文")

    @classmethod
    def from_mapping(cls, value: object) -> "PromptEntry":
        if not isinstance(value, dict):
            raise PromptLibraryError("提示词记录必须是 object")
        prompt_id = value.get("prompt_id")
        name = value.get("name")
        body = value.get("body")
        if not isinstance(prompt_id, int) or isinstance(prompt_id, bool):
            raise PromptLibraryError("提示词 prompt_id 必须是整数")
        if not isinstance(name, str) or not isinstance(body, str):
            raise PromptLibraryError("提示词名称和正文必须是字符串")
        entry = cls(prompt_id, name, body)
        entry.validate()
        return entry

    def as_mapping(self) -> dict[str, object]:
        return {"prompt_id": self.prompt_id, "name": self.name, "body": self.body}


class PromptLibrary:
    """Device-confirmed prompts plus independently editable local drafts.

    Confirmed entries are the last full device readback shown by the editor.
    Physical trigger events still fetch their exact body from the device before
    paste, so an unsent draft or stale cache is never executed.
    """

    def __init__(
        self,
        serial: str,
        *,
        confirmed: tuple[PromptEntry, ...] = (),
        draft: tuple[PromptEntry, ...] | None = None,
    ) -> None:
        if not serial:
            raise PromptLibraryError("提示词库必须绑定设备序列号")
        self.serial = serial
        self._confirmed = _entries_by_id(confirmed)
        self._draft = _entries_by_id(confirmed if draft is None else draft)
        self._validate_total(self._confirmed)
        self._validate_total(self._draft)

    @property
    def confirmed(self) -> tuple[PromptEntry, ...]:
        return tuple(self._confirmed[key] for key in sorted(self._confirmed))

    @property
    def draft(self) -> tuple[PromptEntry, ...]:
        return tuple(self._draft[key] for key in sorted(self._draft))

    @property
    def dirty_prompt_ids(self) -> tuple[int, ...]:
        return tuple(
            prompt_id
            for prompt_id in range(1, PROMPT_SLOT_COUNT + 1)
            if self._draft.get(prompt_id) != self._confirmed.get(prompt_id)
        )

    @property
    def is_dirty(self) -> bool:
        return bool(self.dirty_prompt_ids)

    @property
    def total_draft_body_bytes(self) -> int:
        return sum(entry.body_bytes for entry in self._draft.values())

    def draft_entry(self, prompt_id: int) -> PromptEntry | None:
        _validate_prompt_id(prompt_id)
        return self._draft.get(prompt_id)

    def confirmed_entry(self, prompt_id: int) -> PromptEntry | None:
        _validate_prompt_id(prompt_id)
        return self._confirmed.get(prompt_id)

    def set_draft(self, prompt_id: int, name: str, body: str) -> PromptEntry:
        entry = PromptEntry(prompt_id, name, body)
        entry.validate()
        candidate = dict(self._draft)
        candidate[prompt_id] = entry
        self._validate_total(candidate)
        self._draft = candidate
        return entry

    def delete_draft(self, prompt_id: int) -> None:
        _validate_prompt_id(prompt_id)
        self._draft.pop(prompt_id, None)

    def discard_draft(self, prompt_id: int) -> None:
        _validate_prompt_id(prompt_id)
        confirmed = self._confirmed.get(prompt_id)
        if confirmed is None:
            self._draft.pop(prompt_id, None)
        else:
            self._draft[prompt_id] = confirmed

    def confirm_entry(self, entry: PromptEntry) -> None:
        entry.validate()
        confirmed = dict(self._confirmed)
        confirmed[entry.prompt_id] = entry
        self._validate_total(confirmed)
        self._confirmed = confirmed
        self._draft[entry.prompt_id] = entry

    def confirm_deleted(self, prompt_id: int) -> None:
        _validate_prompt_id(prompt_id)
        self._confirmed.pop(prompt_id, None)
        self._draft.pop(prompt_id, None)

    def replace_confirmed(self, entries: tuple[PromptEntry, ...]) -> None:
        confirmed = _entries_by_id(entries)
        self._validate_total(confirmed)
        dirty = {
            prompt_id: entry
            for prompt_id, entry in self._draft.items()
            if entry != self._confirmed.get(prompt_id)
        }
        deleted = {
            prompt_id
            for prompt_id in self._confirmed
            if prompt_id not in self._draft
        }
        draft = dict(confirmed)
        draft.update(dirty)
        for prompt_id in deleted:
            draft.pop(prompt_id, None)
        self._validate_total(draft)
        self._confirmed = confirmed
        self._draft = draft

    @staticmethod
    def _validate_total(entries: dict[int, PromptEntry]) -> None:
        total = sum(entry.body_bytes for entry in entries.values())
        if total > PROMPT_TOTAL_BODY_MAX_BYTES:
            raise PromptLibraryError(
                f"全部提示词正文合计最多 {PROMPT_TOTAL_BODY_MAX_BYTES} 个 UTF-8 字节"
            )


class PromptLibraryStore:
    VERSION = 1

    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            root = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            directory = Path(root) / "prompt-libraries"
        self._directory = directory

    def load(self, serial: str) -> PromptLibrary:
        path = self.path_for(serial)
        if not path.exists():
            return PromptLibrary(serial)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PromptLibraryError(f"无法读取本地提示词草稿：{exc}") from exc
        if not isinstance(value, dict) or value.get("version") != self.VERSION:
            raise PromptLibraryError("本地提示词草稿版本不受支持")
        if value.get("serial") != serial:
            raise PromptLibraryError("本地提示词草稿与当前设备序列号不一致")
        confirmed = _parse_entries(value.get("confirmed"), "confirmed")
        draft = _parse_entries(value.get("draft"), "draft")
        return PromptLibrary(serial, confirmed=confirmed, draft=draft)

    def save(self, library: PromptLibrary) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "serial": library.serial,
            "confirmed": [entry.as_mapping() for entry in library.confirmed],
            "draft": [entry.as_mapping() for entry in library.draft],
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        target = self.path_for(library.serial)
        output = QSaveFile(str(target))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise PromptLibraryError(f"无法保存本地提示词草稿：{output.errorString()}")
        if output.write(data) != len(data) or not output.commit():
            raise PromptLibraryError(f"无法保存本地提示词草稿：{output.errorString()}")

    def path_for(self, serial: str) -> Path:
        safe_serial = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in serial
        )
        if not safe_serial:
            raise PromptLibraryError("设备序列号不能用于本地提示词草稿路径")
        return self._directory / f"{safe_serial}.json"


def _entries_by_id(entries: tuple[PromptEntry, ...]) -> dict[int, PromptEntry]:
    result: dict[int, PromptEntry] = {}
    for entry in entries:
        entry.validate()
        if entry.prompt_id in result:
            raise PromptLibraryError(f"提示词槽位 {entry.prompt_id} 重复")
        result[entry.prompt_id] = entry
    return result


def _parse_entries(value: Any, label: str) -> tuple[PromptEntry, ...]:
    if not isinstance(value, list):
        raise PromptLibraryError(f"本地提示词草稿 {label} 必须是数组")
    return tuple(PromptEntry.from_mapping(item) for item in value)


def _validate_prompt_id(prompt_id: int) -> None:
    if not isinstance(prompt_id, int) or isinstance(prompt_id, bool):
        raise PromptLibraryError("提示词槽位必须是整数")
    if not 1 <= prompt_id <= PROMPT_SLOT_COUNT:
        raise PromptLibraryError(
            f"提示词槽位必须在 1–{PROMPT_SLOT_COUNT} 范围内"
        )


def _validate_text_controls(
    value: str, *, allow_layout_controls: bool, label: str
) -> None:
    allowed = {"\t", "\n", "\r"} if allow_layout_controls else set()
    for character in value:
        codepoint = ord(character)
        if (codepoint < 0x20 and character not in allowed) or codepoint == 0x7F:
            raise PromptLibraryError(f"{label}包含不支持的控制字符")
