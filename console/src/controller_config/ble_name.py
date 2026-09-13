"""Independent device preference; never part of a portable configuration draft."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import BootstrapError, Command
from controller_config.protocol.contract import (
    BLE_NAME_GET, BLE_NAME_SET, BLE_NAME_MAX_UTF8_BYTES,
    validate_ble_name, validate_ble_name_response,
)


@dataclass
class _NameDraft:
    text: str = ""
    result: dict | None = None
    expected: str | None = None
    message: str = ""
    technical: str = ""

    @property
    def dirty(self) -> bool:
        return self.expected is not None or self.text != (self.result["saved_name"] if self.result else "")


class BleNameSession(QObject):
    changed = Signal()

    def __init__(self, send: Callable[[Command], None], block_reason: Callable[[], str], parent=None):
        super().__init__(parent)
        self._send = send
        self._block_reason = block_reason
        self._drafts: dict[tuple[str, str], _NameDraft] = {}
        self._key: tuple[str, str] | None = None
        self._current = _NameDraft()
        self._command = ""
        self.connected = False
        self.supported = False
        self.max_bytes = BLE_NAME_MAX_UTF8_BYTES

    @property
    def serial(self) -> str:
        return self._key[0] if self._key else ""

    @property
    def busy(self) -> bool:
        return bool(self._command)

    @property
    def draft(self) -> str:
        return self._current.text

    @property
    def result(self) -> dict | None:
        return self._current.result

    @property
    def message(self) -> str:
        return self._current.message

    @property
    def technical(self) -> str:
        return self._current.technical

    @property
    def dirty(self) -> bool:
        return self._current.dirty

    def pending_drafts(self) -> tuple[tuple[str, str], ...]:
        return tuple((key[0], value.text) for key, value in self._drafts.items() if value.dirty)

    def attach(self, snapshot: DeviceSnapshot) -> None:
        self.detach()
        self._key = (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"]))
        self._current = self._drafts.setdefault(self._key, _NameDraft())
        self.connected = snapshot.trust.is_authenticated
        self.supported = snapshot.capabilities.get("features", {}).get("ble_name") is True
        self.max_bytes = snapshot.capabilities.get("limits", {}).get("ble_name_max_utf8_bytes", BLE_NAME_MAX_UTF8_BYTES)
        if not self.supported:
            self._current.message = "此固件暂不支持修改蓝牙名称"
        elif not self.connected:
            self._current.message = "请连接并认证设备后读取名称"
        self.changed.emit()

    def detach(self) -> None:
        self.connected = False
        self._command = ""
        if self._key:
            self._current.message = (
                "保存结果待确认，请重新读取或重连" if self._current.expected is not None
                else "离线草稿，连接设备后可保存"
            )
        self.changed.emit()

    def edit(self, text: str) -> None:
        # Validation belongs at save and in the UI, not at each edit: never truncate.
        self._current.text = text
        self.changed.emit()

    def read(self) -> None:
        self._require_available()
        self._current.message = "正在读取蓝牙名称…"
        self._current.technical = ""
        self._execute("BLE_NAME_GET", BLE_NAME_GET, {})

    def save(self) -> None:
        self._require_available()
        reason = self._block_reason()
        if reason:
            raise ValueError(reason)
        if self._current.expected is not None:
            raise ValueError("保存结果待确认，请重新读取或重连")
        if self.result is None:
            raise ValueError("请先读取设备名称")
        validate_ble_name(self.draft, self.max_bytes)
        if self.draft == self.result["saved_name"]:
            return
        self._current.expected = self.draft
        self._current.message = "正在保存蓝牙名称…"
        self._current.technical = ""
        self._execute("BLE_NAME_SET", BLE_NAME_SET, {"name": self.draft})

    def restore_default(self) -> None:
        self._require_available()
        if self.result is None:
            raise ValueError("请先读取设备名称")
        # Check before replacing an unsaved input when writing is unavailable.
        reason = self._block_reason()
        if reason:
            raise ValueError(reason)
        if self._current.expected is not None:
            raise ValueError("保存结果待确认，请重新读取或重连")
        self.edit(self.result["default_name"])
        self.save()

    def _require_available(self) -> None:
        if not self.connected:
            raise ValueError("请连接并认证设备后读取名称")
        if not self.supported:
            raise ValueError("此固件暂不支持修改蓝牙名称")
        if self.busy:
            raise ValueError("蓝牙名称正在保存或读回，请稍后操作")

    def _execute(self, name: str, message_type: int, payload: dict) -> None:
        self._command = name
        self.changed.emit()
        self._send(Command(name, message_type, payload))

    def completed(self, command_name: str, payload: dict) -> bool:
        if command_name not in {"BLE_NAME_GET", "BLE_NAME_SET"}:
            return False
        if not self.connected or command_name != self._command:
            return True
        self._command = ""
        try:
            result = validate_ble_name_response(payload, self.max_bytes)
        except ValueError as exc:
            self._current.message = ("保存结果待确认，请重新读取或重连" if self._current.expected is not None
                                     else "名称读取失败，输入已保留")
            self._current.technical = str(exc)
            self.changed.emit()
            return True
        if command_name == "BLE_NAME_SET":
            # ACK is not readback. Only a subsequent GET can confirm persistence.
            self._current.message = "正在读回确认保存结果…"
            self._execute("BLE_NAME_GET", BLE_NAME_GET, {})
            return True
        expected = self._current.expected
        preserve_input = self.dirty
        self._current.result = result
        if not preserve_input:
            self._current.text = result["saved_name"]
        self._current.expected = None
        self._current.technical = ""
        if expected is not None and result["saved_name"] != expected:
            self._current.message = "设备读回名称与待保存名称不一致，输入已保留"
        else:
            self._current.message = (
                "已保存，下次正常重启生效" if result["restart_required"]
                else "已保存，当前名称已生效"
            )
        self.changed.emit()
        return True

    def failed(self, command_name: str, error: BootstrapError) -> bool:
        if command_name not in {"BLE_NAME_GET", "BLE_NAME_SET"}:
            return False
        if command_name != self._command:
            return True
        self._command = ""
        # A device NACK is definitive. A timeout / transport / malformed ACK isn't.
        if command_name == "BLE_NAME_SET" and error.error_name in {
            "VALIDATION_FAILED", "BUSY", "STORAGE_FAILURE", "INTERNAL", "UNSUPPORTED_COMMAND",
        }:
            self._current.expected = None
        self._current.message = (
            "保存结果待确认，请重新读取或重连" if self._current.expected is not None
            else ("名称保存失败，输入已保留" if command_name == "BLE_NAME_SET" else "名称读取失败，输入已保留")
        )
        self._current.technical = error.technical or str(error)
        self.changed.emit()
        return True
