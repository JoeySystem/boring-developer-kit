"""Bounded, yielding screen-icon commands on the existing authenticated connection."""
from __future__ import annotations

import base64
import binascii
from typing import Callable, Any

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import Command
from controller_config.protocol.contract import Contract


TOTAL = 32768
# U1 WMP screen-icon v1 command types, documented in software/protocol/protocol.md.
MESSAGE_TYPES = {name: 0x50 + index for index, name in enumerate(
    ("GET", "READ", "BEGIN", "DATA", "COMMIT", "ABORT", "RESET"))}


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not _integer(value.get("revision"), 0, 0xFFFFFFFF):
        raise ValueError("图标版本无效")
    if (value.get("source") not in ("default", "custom")
            or value.get("target") != "normal_home"
            or value.get("format") != "rgb565_le"
            or type(value.get("width")) is not int or value["width"] != 128
            or type(value.get("height")) is not int or value["height"] != 128
            or type(value.get("total_bytes")) is not int
            or value["total_bytes"] != (TOTAL if value["source"] == "custom" else 0)):
        raise ValueError("设备图标格式无效")
    return dict(value)


class ScreenIconTransfer(QObject):
    changed = Signal()

    @property
    def connection_epoch(self) -> int:
        return self._epoch

    def __init__(self, contract: Contract | None, send: Callable[[Command], None], parent=None):
        super().__init__(parent)
        self._contract, self._send = contract, send
        self.busy = False
        self.supported = False
        self.writable = False
        self.status = "尚未读取设备图标"
        self.state = "idle"
        self.metadata: dict[str, Any] | None = None
        self.pixels: bytes | None = None
        self.verified_upload: bytes | None = None
        self.progress = 0
        self.can_cancel = False
        self._device: tuple[str, str] | None = None
        self._port: str | None = None
        self._epoch = 0
        self._expected: str | None = None
        self._sent = False
        self._pending: dict[tuple[str, str], tuple[str, bytes | None]] = {}
        self._operation = "refresh"
        self._candidate: bytes | None = None
        self._upload_id: int | None = None
        self._offset = 0
        self._chunk = 512
        self._read = bytearray()
        self._cancel_requested = False
        self._mutation_sent = False
        self._base_revision: int | None = None
        self._last_length = 0
        self._recovering = False
        self._abort_error: str | None = None
        self._refresh_error: str | None = None
        self._refresh_after_abort = False

    def attach(self, snapshot: DeviceSnapshot | None, writable: bool = True) -> None:
        device = ((snapshot.identity.get("serial", ""), snapshot.identity.get("hardware_id", ""))
                  if snapshot else None)
        port = snapshot.port_name if snapshot else None
        if (device, port) != (self._device, self._port) or snapshot is None:
            self._remember_unknown()
            self._epoch += 1
            self._device, self._port = device, port
            self._expected = None
            self._mutation_sent = False
            self._upload_id = None
            self.busy = self.can_cancel = False
            self.metadata = self.pixels = self.verified_upload = None
            self.state = "unknown" if device in self._pending else "idle"
            self.status = "写入结果待重新读取确认" if self.state == "unknown" else "尚未读取设备图标"
        cap = snapshot.capabilities.get("screen_icon", {}) if snapshot else {}
        features = snapshot.capabilities.get("features", {}) if snapshot else {}
        self.supported = bool(
            features.get("custom_home_icon") is True and isinstance(cap, dict)
            and type(cap.get("version")) is int and cap["version"] == 1
            and cap.get("target") == "normal_home" and cap.get("format") == "rgb565_le"
            and type(cap.get("width")) is int and cap["width"] == 128
            and type(cap.get("height")) is int and cap["height"] == 128
            and type(cap.get("total_bytes")) is int and cap["total_bytes"] == TOTAL
            and _integer(cap.get("max_chunk_bytes"), 1, 1024)
            and _integer(cap.get("session_timeout_ms"), 1, 0xFFFFFFFF))
        self.writable = bool(snapshot and writable)
        if self.supported:
            self._chunk = min(512, cap["max_chunk_bytes"])
        self.changed.emit()

    def _ready(self) -> bool:
        if self.busy:
            return False
        if not self.supported or not self.writable or self._contract is None:
            self.status = "当前设备未支持图标写入或连接尚未授权"
            self.changed.emit()
            return False
        return True

    def refresh(self) -> None:
        if not self._ready():
            return
        pending = self._pending.get(self._device)
        self._start(*(pending or ("refresh", None)), recovering=bool(pending))

    def upload(self, data: bytes) -> None:
        if not self._ready():
            return
        if not isinstance(data, bytes) or len(data) != TOTAL:
            self.status = "图标必须为 32768 字节 RGB565 数据"
            self.changed.emit()
            return
        if self._device in self._pending:
            self.status = "请先重新读取，确认上次写入结果"
            self.changed.emit()
            return
        self._start("upload", bytes(data))

    def reset(self) -> None:
        if not self._ready():
            return
        if self._device in self._pending:
            self.status = "请先重新读取，确认上次写入结果"
            self.changed.emit()
            return
        self._start("reset", None)

    def _start(self, operation: str, candidate: bytes | None, recovering: bool = False) -> None:
        self._operation, self._candidate, self._recovering = operation, candidate, recovering
        self.verified_upload = None
        self._cancel_requested = self._mutation_sent = False
        self._abort_error = None
        self._refresh_error = None
        self._refresh_after_abort = False
        self._upload_id = None
        self._base_revision = None
        self.progress = 0
        self.busy = True
        self.can_cancel = not recovering
        self.status = "正在读取设备图标"
        self._queue("GET", {})

    def _queue(self, suffix: str, payload: dict[str, Any]) -> None:
        name = "SCREEN_ICON_" + suffix
        self.state = suffix.lower()
        self._expected, self._sent = name, False
        epoch = self._epoch
        command = Command(name, MESSAGE_TYPES[suffix], payload,
                          timeout_ms=10000 if suffix in ("COMMIT", "RESET") else 2500)
        self.changed.emit()

        def dispatch() -> None:
            if epoch != self._epoch or self._expected != name or self._sent:
                return
            if not self.writable or not self.supported:
                self._error("设备连接不可用")
                return
            self._sent = True
            if suffix in ("COMMIT", "RESET"):
                self._mutation_sent = True
                self.can_cancel = False
            try:
                self._send(command)
            except Exception as exc:
                self.failed(name, exc)

        QTimer.singleShot(0, self, dispatch)

    def completed(self, command_name: str, payload: dict[str, Any]) -> bool:
        if not command_name.startswith("SCREEN_ICON_"):
            return False
        if command_name != self._expected or not self._sent:
            return True
        self._expected = None
        try:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("设备图标响应缺少 result")
            suffix = command_name.removeprefix("SCREEN_ICON_")
            if suffix == "GET":
                self.metadata = _metadata(result)
                self.pixels = None
                if self._cancel_requested:
                    self._finish("已取消，设备图标未修改")
                elif self._operation != "refresh" and not self._recovering:
                    self._base_revision = self.metadata["revision"]
                    if self._operation == "reset":
                        self._queue("RESET", {"base_revision": self._base_revision})
                    else:
                        self._queue("BEGIN", {"base_revision": self._base_revision,
                            "target": "normal_home", "format": "rgb565_le",
                            "width": 128, "height": 128, "total_bytes": TOTAL})
                else:
                    self._read_or_finish()
            elif suffix == "BEGIN":
                if not _integer(result.get("upload_id"), 1, 0xFFFFFFFF):
                    raise ValueError("图标上传会话无效")
                self._upload_id = result["upload_id"]
                if type(result.get("next_offset")) is not int or result["next_offset"] != 0:
                    raise ValueError("图标上传起始偏移无效")
                if not _integer(result.get("max_chunk_bytes"), 1, 1024):
                    raise ValueError("图标分块上限无效")
                self._chunk = min(self._chunk, result["max_chunk_bytes"])
                self._offset = 0
                self._next_data()
            elif suffix == "DATA":
                expected = self._offset + self._last_length
                if type(result.get("next_offset")) is not int or result["next_offset"] != expected:
                    raise ValueError("图标上传偏移不一致")
                self._offset = expected
                self.progress = self._offset * 70 // TOTAL
                self._next_data()
            elif suffix in ("COMMIT", "RESET"):
                meta = _metadata(result)
                if meta["revision"] != self._base_revision + 1:
                    raise ValueError("图标写入后版本不一致")
                if meta["source"] != ("custom" if suffix == "COMMIT" else "default"):
                    raise ValueError("图标写入后来源不一致")
                self.metadata = meta
                self._upload_id = None
                self._recovering = True
                self.status = "写入已响应，正在回读确认"
                self._queue("GET", {})
            elif suffix == "READ":
                if (type(result.get("revision")) is not int
                        or result["revision"] != self.metadata["revision"]
                        or type(result.get("offset")) is not int or result["offset"] != len(self._read)):
                    raise ValueError("图标回读版本或偏移不一致")
                raw = result.get("data")
                if not isinstance(raw, str):
                    raise ValueError("图标回读数据无效")
                chunk = base64.b64decode(raw, validate=True)
                if len(chunk) != self._last_length or base64.b64encode(chunk).decode("ascii") != raw:
                    raise ValueError("图标回读数据长度或编码无效")
                self._read.extend(chunk)
                self.progress = 70 + len(self._read) * 30 // TOTAL if self._operation == "upload" else len(self._read) * 100 // TOTAL
                if self._cancel_requested and not self._mutation_sent:
                    self._finish("已取消读取，设备图标未修改")
                elif len(self._read) == TOTAL:
                    self.pixels = bytes(self._read)
                    self._verify()
                else:
                    self._next_read()
            elif suffix == "ABORT":
                if result.get("aborted") is not True:
                    raise ValueError("设备未确认取消图标上传")
                self._upload_id = None
                if self._refresh_after_abort:
                    self._refresh_after_abort = False
                    self._queue("GET", {})
                else:
                    self._finish(self._abort_error or "已取消，设备图标未修改", error=bool(self._abort_error))
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            self._error(str(exc))
        return True

    def _next_data(self) -> None:
        if self._cancel_requested:
            self._queue("ABORT", {"upload_id": self._upload_id})
        elif self._offset == TOTAL:
            self._queue("COMMIT", {"upload_id": self._upload_id})
        else:
            data = self._candidate[self._offset:self._offset + self._chunk]
            self._last_length = len(data)
            self.status = "正在上传图标"
            self._queue("DATA", {"upload_id": self._upload_id, "offset": self._offset,
                                 "data": base64.b64encode(data).decode("ascii")})

    def _read_or_finish(self) -> None:
        if self.metadata["source"] == "default":
            self._verify()
        else:
            self._read = bytearray()
            self._next_read()

    def _next_read(self) -> None:
        self._last_length = min(self._chunk, TOTAL - len(self._read))
        self._queue("READ", {"revision": self.metadata["revision"],
                             "offset": len(self._read), "length": self._last_length})

    def _verify(self) -> None:
        self._mutation_sent = False
        self._pending.pop(self._device, None)
        if self._refresh_error:
            self._finish(self._refresh_error + "；已读取设备当前图标，本地图片保留", error=True)
        elif self._operation == "upload":
            if self.metadata["source"] == "custom" and self.pixels == self._candidate:
                self.verified_upload = self._candidate
                self._finish("图标已保存，完整回读一致")
            else:
                self._finish("设备当前图标与待上传图片不一致；保留本地图片，可重新上传", error=True)
        elif self._operation == "reset":
            self._finish("已恢复默认图标" if self.metadata["source"] == "default" else "设备仍为自定义图标，未确认恢复默认",
                         error=self.metadata["source"] != "default")
        else:
            self._finish("已读取设备自定义图标" if self.pixels is not None else "设备正在使用默认图标")

    def cancel(self) -> None:
        if not self.busy or not self.can_cancel:
            return
        self._cancel_requested = True
        if self._sent and self._expected:
            return  # Finish the in-flight command before aborting its session.
        self._epoch += 1
        self._expected = None
        if self._upload_id is not None:
            self._queue("ABORT", {"upload_id": self._upload_id})
        else:
            self._finish("已取消，设备图标未修改")

    def _remember_unknown(self) -> None:
        if self._mutation_sent and self._device is not None:
            self._pending[self._device] = (self._operation, self._candidate)

    def failed(self, command_name: str, error: object) -> bool:
        if not command_name.startswith("SCREEN_ICON_"):
            return False
        if command_name != self._expected:
            return True
        self._expected = None
        if getattr(error, "error_name", "") == "GENERATION_CONFLICT" and self._refresh_error is None:
            self._refresh_error = "设备图标已被其他操作修改"
            self._mutation_sent = False  # Explicit conflict NACK means this mutation did not commit.
            self._operation, self._recovering = "refresh", True
            self.can_cancel = False
            self.metadata = self.pixels = None
            if self._upload_id is not None:
                self._refresh_after_abort = True
                self._queue("ABORT", {"upload_id": self._upload_id})
            else:
                self._queue("GET", {})
            return True
        self._error(str(error))
        return True

    def _error(self, reason: str) -> None:
        self._remember_unknown()
        unknown = self._device in self._pending
        if self._upload_id is not None and not self._mutation_sent and self.state != "abort" and self.writable:
            self._abort_error = "图标操作失败，上传已取消：" + reason
            self.can_cancel = False
            self._queue("ABORT", {"upload_id": self._upload_id})
            return
        self.busy = self.can_cancel = False
        self._expected = None
        self._epoch += 1
        self.state = "unknown" if unknown else "error"
        self.status = ("写入结果未知，请重新读取确认：" if unknown else "图标操作失败：") + reason
        self.changed.emit()
        if unknown and not self._recovering and self.writable and self.supported:
            self._recovering = True
            self.busy = True
            self._queue("GET", {})

    def _finish(self, message: str, error: bool = False) -> None:
        self.busy = self.can_cancel = False
        self.state = "error" if error else "idle"
        self.status = message
        self.progress = 100 if not error else self.progress
        self._expected = None
        self.changed.emit()
