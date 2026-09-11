"""Per-resource glyph operations on the existing device command queue."""
from __future__ import annotations

import base64
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import Command


def _uint(value, maximum=0xFFFFFFFF):
    return type(value) is int and 0 <= value <= maximum


class ScreenGlyphTransfer(QObject):
    changed = Signal()

    def __init__(self, send: Callable[[Command], None], parent=None):
        super().__init__(parent)
        self._send = send
        self.connection_epoch = 0
        self.device_key = None
        self._port = None
        self.supported = self.writable = self.busy = False
        self.catalog = []
        self.selected_id = None
        self.metadata = self.pixels = self.verified_write = None
        self.status = "当前固件未支持逐项图标自定义"
        self._expected = None
        self._operation = "read"
        self._candidate = None
        self._pending = {}
        self._mutation_sent = False
        self._recovering = False
        self._read_error = None

    def attach(self, snapshot: DeviceSnapshot | None, writable=True):
        key = (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"])) if snapshot else None
        port = snapshot.port_name if snapshot else None
        if snapshot is None or (key, port) != (self.device_key, self._port):
            self._remember_pending()
            self.connection_epoch += 1
            self.device_key, self._port = key, port
            self.busy = self._mutation_sent = False
            self.catalog = []
            self.selected_id = self.metadata = self.pixels = self.verified_write = None
            self._expected = None
        cap = snapshot.capabilities.get("screen_glyphs", {}) if snapshot else {}
        self.supported = bool(snapshot and snapshot.capabilities.get("features", {}).get("custom_glyph_icons") is True
            and isinstance(cap, dict) and type(cap.get("version")) is int and cap["version"] == 1
            and cap.get("format") == "alpha8" and _uint(cap.get("count"), 28) and cap["count"] > 0)
        self.writable = bool(snapshot and writable)
        self.status = "请选择要修改的图标" if self.supported else "当前固件未支持逐项图标自定义"
        self.changed.emit()

    def _ready(self):
        return self.supported and self.writable and not self.busy

    def load_catalog(self):
        if not self._ready():
            return
        self.busy = True
        self.status = "正在读取图标列表"
        self._queue("LIST", {})

    def select(self, icon_id: str):
        if not self._ready() or not any(item["id"] == icon_id for item in self.catalog):
            return
        self.selected_id = icon_id
        self.metadata = self.pixels = self.verified_write = None
        self.refresh()

    def refresh(self):
        if not self._ready() or self.selected_id is None:
            return
        pending = self._pending.get((self.device_key, self.selected_id))
        self._operation, self._candidate = pending or ("read", None)
        self._recovering = bool(pending)
        self._read_error = None
        self.verified_write = None
        self.busy = True
        self.status = "正在读取选中图标"
        self._queue("GET", {"id": self.selected_id})

    def write(self, data: bytes):
        self._mutate("write", data)

    def reset(self):
        self._mutate("reset", None)

    def _mutate(self, operation, data):
        if not self._ready() or self.selected_id is None:
            return
        item = next((c for c in self.catalog if c["id"] == self.selected_id), None)
        if not item or not item["editable"]:
            raise ValueError("此图标为内置系统提示，不能修改")
        if (self.device_key, self.selected_id) in self._pending:
            raise ValueError("请先读取该图标，确认上次操作结果")
        if operation == "write" and (not isinstance(data, bytes) or len(data) != item["width"] * item["height"]):
            raise ValueError("图标像素尺寸不匹配")
        self._operation, self._candidate = operation, data
        self._recovering = self._mutation_sent = False
        self._read_error = None
        self.verified_write = None
        self.busy = True
        self.status = "正在确认选中图标的设备版本"
        self._queue("GET", {"id": self.selected_id})

    def _queue(self, suffix, payload):
        name = "SCREEN_GLYPH_" + suffix
        self._expected = name
        epoch = self.connection_epoch
        self.changed.emit()
        def dispatch():
            if epoch != self.connection_epoch or name != self._expected:
                return
            if suffix in {"SET", "RESET"}:
                self._mutation_sent = True
                self._remember_pending()
            try:
                self._send(Command(name, {"LIST": 0x57, "GET": 0x58, "SET": 0x59, "RESET": 0x5A}[suffix],
                                   payload, timeout_ms=5000))
            except Exception as error:
                self.failed(name, error)
        QTimer.singleShot(0, self, dispatch)

    def _decode(self, result):
        item = next((c for c in self.catalog if c["id"] == self.selected_id), None)
        if not item or result.get("id") != self.selected_id or not _uint(result.get("revision")):
            raise ValueError("图标响应与选中项目不一致")
        if (result.get("source") not in {"default", "custom"} or result.get("format") != "alpha8"
                or type(result.get("width")) is not int or result["width"] != item["width"]
                or type(result.get("height")) is not int or result["height"] != item["height"]):
            raise ValueError("图标响应格式不正确")
        raw = result.get("data")
        if not isinstance(raw, str):
            raise ValueError("图标响应缺少像素")
        pixels = base64.b64decode(raw, validate=True)
        if len(pixels) != item["width"] * item["height"] or base64.b64encode(pixels).decode() != raw:
            raise ValueError("图标像素尺寸不匹配")
        return pixels

    def completed(self, name, payload):
        if not name.startswith("SCREEN_GLYPH_"):
            return False
        if name != self._expected:
            return True
        self._expected = None
        try:
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("图标响应缺少结果")
            if name.endswith("_LIST"):
                entries = result.get("icons")
                if result.get("version") != 1 or result.get("format") != "alpha8" or not isinstance(entries, list) or not 1 <= len(entries) <= 28:
                    raise ValueError("图标列表格式不正确")
                ids = set()
                for item in entries:
                    if (not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                            or item["id"] in ids or not _uint(item.get("resource_id"), 27)
                            or not _uint(item.get("width"), 15) or item["width"] == 0
                            or not _uint(item.get("height"), 15) or item["height"] == 0
                            or type(item.get("editable")) is not bool):
                        raise ValueError("图标列表格式不正确")
                    ids.add(item["id"])
                self.catalog = entries
                self.busy = False
                self.select(self.selected_id if self.selected_id in ids else entries[0]["id"])
                return True
            pixels = self._decode(result)
            if name.endswith("_GET"):
                self.metadata, self.pixels = dict(result), pixels
                if self._operation in {"write", "reset"} and not self._recovering:
                    request = {"id": self.selected_id, "base_revision": result["revision"]}
                    if self._operation == "write":
                        request["data"] = base64.b64encode(self._candidate).decode()
                    self.status = "正在保存选中图标"
                    self._queue("SET" if self._operation == "write" else "RESET", request)
                    return True
                self._mutation_sent = False
                self._pending.pop((self.device_key, self.selected_id), None)
                if self._read_error:
                    message = "设备拒绝修改；已读取当前图标，本地图片保留"
                elif self._operation == "write":
                    matches = result["source"] == "custom" and pixels == self._candidate
                    if matches:
                        self.verified_write = (self.selected_id, pixels)
                    message = "此图标已保存，读回一致；其他图标不变" if matches else "读回与待保存图标不一致，本地图片保留"
                elif self._operation == "reset":
                    message = "此图标已恢复默认；其他图标不变" if result["source"] == "default" else "未确认恢复默认，请重新读取"
                else:
                    message = "已读取选中图标"
                self._finish(message)
            else:
                self._recovering = True
                self.status = "保存已响应，正在读回选中图标"
                self._queue("GET", {"id": self.selected_id})
        except (ValueError, TypeError, KeyError) as error:
            self._fail(str(error))
        return True

    def _remember_pending(self):
        if self._mutation_sent and self.device_key and self.selected_id:
            self._pending[(self.device_key, self.selected_id)] = (self._operation, self._candidate)

    def failed(self, name, error):
        if not name.startswith("SCREEN_GLYPH_"):
            return False
        if name == self._expected:
            self._expected = None
            if getattr(error, "error_name", "") in {
                "VALIDATION_FAILED", "GENERATION_CONFLICT", "READ_ONLY", "BUSY", "NOT_FOUND"
            }:
                self._read_error = str(error)
            self._fail(str(error))
        return True

    def _fail(self, reason):
        self._remember_pending()
        if self._mutation_sent and not self._recovering:
            self._recovering = True
            self.status = "操作响应异常，正在读取确认"
            self._queue("GET", {"id": self.selected_id})
        else:
            unknown = (self.device_key, self.selected_id) in self._pending
            self._finish(("结果未知，请重新读取：" if unknown else "图标操作失败：") + reason)

    def _finish(self, message):
        self.busy = False
        self._expected = None
        self.status = message
        self.changed.emit()
