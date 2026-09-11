from __future__ import annotations

from collections.abc import Callable
from collections import deque
from dataclasses import dataclass
from enum import Enum
import time

from PySide6.QtCore import QObject, QTimer

from controller_config.automation import EventDispatchResult
from controller_config.device_events import DeviceEvent, prompt_triggered_event
from controller_config.models import DeviceSnapshot
from controller_config.prompt_helper import PromptHelperRuntime, PromptPasteError
from controller_config.prompt_library import (
    PROMPT_BODY_MAX_BYTES,
    PROMPT_NAME_MAX_BYTES,
    PROMPT_SLOT_COUNT,
    PROMPT_TOTAL_BODY_MAX_BYTES,
    PromptEntry,
    PromptLibrary,
    PromptLibraryError,
    PromptLibraryStore,
)
from controller_config.protocol.bootstrap import BootstrapError, Command


PROMPT_COMMANDS = frozenset(
    {
        "GET_PROMPT_LIST",
        "GET_PROMPT",
        "SET_PROMPT",
        "DELETE_PROMPT",
        "GET_PROMPT_EVENT",
    }
)
PROMPT_EVENT_ONLINE_BUDGET_MS = 1500
PROMPT_EVENT_RESUME_RESERVE_MS = 150

EventDispatchCompleted = Callable[[EventDispatchResult], None]
AsyncEventDispatcher = Callable[[DeviceEvent, int, EventDispatchCompleted], None]


class PromptDeviceState(str, Enum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    READING = "reading"
    WRITING = "writing"
    DELETING = "deleting"
    ERROR = "error"


@dataclass(frozen=True)
class PromptDeviceStatus:
    state: PromptDeviceState = PromptDeviceState.UNAVAILABLE
    message: str = "当前设备没有声明提示词存储能力"
    technical: str = ""

    @property
    def is_busy(self) -> bool:
        return self.state in {
            PromptDeviceState.READING,
            PromptDeviceState.WRITING,
            PromptDeviceState.DELETING,
        }


class PromptListenerState(str, Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    READY = "ready"
    PAUSED = "paused"
    RETRYING = "retrying"


@dataclass(frozen=True)
class PromptListenerStatus:
    state: PromptListenerState = PromptListenerState.OFFLINE
    message: str = "助手离线；等待设备连接"
    technical: str = ""

    @property
    def online(self) -> bool:
        return self.state is PromptListenerState.READY

    @property
    def label(self) -> str:
        return {
            PromptListenerState.OFFLINE: "助手离线",
            PromptListenerState.STARTING: "正在连接",
            PromptListenerState.READY: "助手在线",
            PromptListenerState.PAUSED: "暂时暂停",
            PromptListenerState.RETRYING: "正在重试",
        }[self.state]


@dataclass(frozen=True)
class PromptEventLogEntry:
    sequence: int
    level: str
    message: str
    technical: str = ""
    event_id: int | None = None
    prompt_id: int | None = None


def get_prompt_list_command() -> Command:
    return Command("GET_PROMPT_LIST", 0x17, {})


def get_prompt_command(prompt_id: int) -> Command:
    return Command("GET_PROMPT", 0x18, {"prompt_id": prompt_id})


def set_prompt_command(entry: PromptEntry) -> Command:
    entry.validate()
    return Command(
        "SET_PROMPT",
        0x19,
        {"prompt_id": entry.prompt_id, "name": entry.name, "body": entry.body},
    )


def delete_prompt_command(prompt_id: int) -> Command:
    return Command("DELETE_PROMPT", 0x1A, {"prompt_id": prompt_id})


def get_prompt_event_command() -> Command:
    return Command("GET_PROMPT_EVENT", 0x1B, {})


class PromptDeviceSession(QObject):
    def __init__(
        self,
        execute: Callable[[Command], None],
        store: PromptLibraryStore,
        *,
        helper: PromptHelperRuntime | None = None,
        event_dispatcher: Callable[[DeviceEvent], EventDispatchResult] | None = None,
        async_event_dispatcher: AsyncEventDispatcher | None = None,
        auto_refresh: bool = True,
        auto_poll: bool = False,
        changed: Callable[[], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._execute = execute
        self._store = store
        self._helper = helper
        self._event_dispatcher = event_dispatcher
        self._async_event_dispatcher = async_event_dispatcher
        self._auto_refresh = auto_refresh
        self._auto_poll = auto_poll
        self._changed = changed or (lambda: None)
        self._library: PromptLibrary | None = None
        self._snapshot: DeviceSnapshot | None = None
        self._status = PromptDeviceStatus()
        self._listener_status = PromptListenerStatus()
        self._trigger_supported = False
        self._polling_paused = False
        self._last_poll_error = ""
        self._event_sequence = 0
        self._event_log: deque[PromptEventLogEntry] = deque(maxlen=30)
        self._read_queue: list[int] = []
        self._read_entries: list[PromptEntry] = []
        self._read_prompt_ids: set[int] = set()
        self._write_prompt_id: int | None = None
        self._delete_prompt_id: int | None = None
        self._pending_event_id: int | None = None
        self._pending_event_prompt_id: int | None = None
        self._pending_event_deadline = 0.0
        self._dispatch_sequence = 0
        self._event_poll_in_flight = False
        self._discard_event_prompt_response = False
        self._event_timer = QTimer(self)
        self._event_timer.setSingleShot(True)
        self._event_timer.timeout.connect(self._poll_event)
        self._helper_message = "尚无实体提示词触发记录"

    @property
    def status(self) -> PromptDeviceStatus:
        return self._status

    @property
    def helper_message(self) -> str:
        return self._helper_message

    @property
    def listener_status(self) -> PromptListenerStatus:
        return self._listener_status

    @property
    def event_log(self) -> tuple[PromptEventLogEntry, ...]:
        return tuple(self._event_log)

    @property
    def available(self) -> bool:
        return self._status.state is not PromptDeviceState.UNAVAILABLE

    def prompt_was_read(self, prompt_id: int) -> bool:
        return prompt_id in self._read_prompt_ids

    def set_async_event_dispatcher(
        self, dispatcher: AsyncEventDispatcher | None
    ) -> None:
        self._async_event_dispatcher = dispatcher

    def bind(self, library: PromptLibrary, snapshot: DeviceSnapshot) -> None:
        already_bound = self._library is library and self._snapshot is not None
        self._event_timer.stop()
        if not already_bound:
            self._read_prompt_ids.clear()
            self._event_poll_in_flight = False
            self._discard_event_prompt_response = False
            self._pending_event_id = None
            self._pending_event_prompt_id = None
            self._pending_event_deadline = 0.0
            self._dispatch_sequence += 1
        self._library = library
        self._snapshot = snapshot
        available, reason = _prompt_capability(snapshot)
        features = snapshot.capabilities.get("features")
        self._trigger_supported = bool(
            available
            and (self._event_dispatcher is not None or self._helper is not None)
            and isinstance(features, dict)
            and features.get("prompt_trigger_usb") is True
        )
        if not available:
            self._set_status(PromptDeviceState.UNAVAILABLE, reason)
            self._set_listener(PromptListenerState.OFFLINE, f"助手离线；{reason}")
            return
        if already_bound:
            self._schedule_event(100)
            return
        self._set_status(PromptDeviceState.IDLE, reason)
        if self._trigger_supported and self._auto_poll:
            self._set_listener(
                PromptListenerState.STARTING,
                "正在建立设备事件监听；首次轮询成功后才会显示在线",
            )
            self._append_event_log("info", "正在建立 USB 提示词事件监听")
        elif self._helper is None and self._event_dispatcher is None:
            self._set_listener(
                PromptListenerState.OFFLINE,
                "助手离线；当前平台没有可用的 Unicode 粘贴助手",
            )
        elif not isinstance(features, dict) or features.get("prompt_trigger_usb") is not True:
            self._set_listener(
                PromptListenerState.OFFLINE,
                "助手离线；设备没有声明 USB 提示词触发能力",
            )
        else:
            self._set_listener(PromptListenerState.OFFLINE, "助手离线；后台轮询未启用")
        if self._auto_refresh:
            self.refresh()
        elif self._auto_poll:
            self._schedule_event(50)

    def unbind(self) -> None:
        self._read_prompt_ids.clear()
        self._event_timer.stop()
        self._snapshot = None
        self._event_poll_in_flight = False
        self._discard_event_prompt_response = False
        self._dispatch_sequence += 1
        self._reset_operation_context()
        self._last_poll_error = ""
        if self._listener_status.state is not PromptListenerState.OFFLINE:
            self._append_event_log("warning", "设备连接断开，提示词助手已离线")
        self._set_listener(PromptListenerState.OFFLINE, "助手离线；等待设备重新连接")
        if self._library is not None:
            self._set_status(
                PromptDeviceState.UNAVAILABLE,
                "设备已断开；保留最后一次读回内容和本地草稿",
            )

    def set_polling_paused(self, paused: bool) -> None:
        next_paused = bool(paused)
        if next_paused == self._polling_paused:
            return
        self._polling_paused = next_paused
        if self._polling_paused:
            self._event_timer.stop()
            self._dispatch_sequence += 1
            self._discard_pending_event_read()
            if self._trigger_supported:
                self._set_listener(
                    PromptListenerState.PAUSED,
                    "助手暂时暂停；设备事务完成后会自动恢复监听",
                )
        else:
            if self._trigger_supported:
                self._set_listener(
                    PromptListenerState.STARTING,
                    "正在恢复设备事件监听；首次轮询成功后才会显示在线",
                )
            self._schedule_event(100)

    def refresh(self) -> None:
        if self._library is None or self._snapshot is None:
            raise PromptLibraryError("请先连接支持提示词存储的设备")
        available, reason = _prompt_capability(self._snapshot)
        if not available:
            raise PromptLibraryError(reason)
        if self._status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成")
        self._event_timer.stop()
        self._reset_operation_context()
        self._pause_listener_for_device_operation("正在读取提示词库")
        self._read_prompt_ids.clear()
        self._set_status(PromptDeviceState.READING, "正在读取设备提示词列表")
        self._execute(get_prompt_list_command())

    def write_draft(self, prompt_id: int) -> None:
        library, _snapshot = self._writable_context()
        if self._status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成")
        entry = library.draft_entry(prompt_id)
        if entry is None:
            raise PromptLibraryError("当前槽位没有可写入的本地草稿")
        self._event_timer.stop()
        self._write_prompt_id = prompt_id
        self._read_prompt_ids.discard(prompt_id)
        self._pause_listener_for_device_operation("正在写入提示词")
        self._set_status(
            PromptDeviceState.WRITING,
            f"正在写入提示词槽位 {prompt_id}；完成后会立即读回确认",
        )
        self._execute(set_prompt_command(entry))

    def delete_confirmed(self, prompt_id: int) -> None:
        library, _snapshot = self._writable_context()
        if self._status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成")
        if library.confirmed_entry(prompt_id) is None:
            raise PromptLibraryError("当前槽位没有已从设备读回的提示词")
        self._event_timer.stop()
        self._delete_prompt_id = prompt_id
        self._read_prompt_ids.discard(prompt_id)
        self._pause_listener_for_device_operation("正在删除提示词")
        self._set_status(
            PromptDeviceState.DELETING,
            f"正在删除设备提示词槽位 {prompt_id}；完成后会重新读取列表确认",
        )
        self._execute(delete_prompt_command(prompt_id))

    def handle_completed(self, command_name: str, payload: dict) -> bool:
        if command_name not in PROMPT_COMMANDS:
            return False
        if command_name == "GET_PROMPT_EVENT":
            self._event_poll_in_flight = False
            if self._polling_paused or self._status.is_busy:
                self._schedule_event(100)
                return True
        if command_name == "GET_PROMPT" and self._discard_event_prompt_response:
            self._discard_event_prompt_response = False
            self._schedule_event(100)
            return True
        try:
            if command_name == "GET_PROMPT_LIST":
                self._handle_prompt_list(payload)
            elif command_name == "GET_PROMPT":
                self._handle_prompt(payload)
            elif command_name == "SET_PROMPT":
                self._handle_set_prompt(payload)
            elif command_name == "DELETE_PROMPT":
                self._handle_delete_prompt(payload)
            else:
                self._handle_prompt_event(payload)
        except PromptLibraryError as exc:
            if command_name == "GET_PROMPT_EVENT":
                self._handle_poll_failure(
                    "设备事件响应无效，正在重试",
                    str(exc),
                    delay_ms=1000,
                )
                return True
            self._reset_operation_context()
            self._set_status(PromptDeviceState.ERROR, "提示词设备响应无效", str(exc))
        return True

    def handle_failed(self, command_name: str, error: BootstrapError) -> bool:
        if command_name not in PROMPT_COMMANDS:
            return False
        if command_name == "GET_PROMPT_EVENT":
            self._event_poll_in_flight = False
            if self._polling_paused or self._status.is_busy:
                self._schedule_event(100)
                return True
            delay_ms = 200 if error.error_name == "CLIENT_BUSY" else 500
            self._handle_poll_failure(
                "设备事件监听暂时不可用，正在重试",
                error.technical or str(error),
                delay_ms=delay_ms,
            )
            return True
        if command_name == "GET_PROMPT" and self._discard_event_prompt_response:
            self._discard_event_prompt_response = False
            self._schedule_event(100)
            return True
        if command_name == "GET_PROMPT" and self._pending_event_id is not None:
            event_id = self._pending_event_id
            prompt_id = self._pending_event_prompt_id
            self._event_timer.stop()
            self._reset_operation_context()
            self._helper_message = f"事件 {event_id} 的提示词读取失败，本次未粘贴"
            self._append_event_log(
                "error",
                self._helper_message,
                technical=error.technical or str(error),
                event_id=event_id,
                prompt_id=prompt_id,
            )
            self._set_status(
                PromptDeviceState.IDLE,
                "设备提示词存储可用",
                error.technical or str(error),
            )
            self._mark_listener_starting("正在恢复设备事件监听")
            self._schedule_event(100)
            return True
        self._event_timer.stop()
        self._reset_operation_context()
        self._set_status(
            PromptDeviceState.ERROR,
            "提示词设备操作失败",
            error.technical or str(error),
        )
        self._mark_listener_starting("提示词设备操作失败后正在恢复事件监听")
        self._schedule_event(500)
        return True

    def _handle_prompt_list(self, payload: dict) -> None:
        result = _result(payload, "GET_PROMPT_LIST")
        prompts = result.get("prompts")
        if not isinstance(prompts, list):
            raise PromptLibraryError("GET_PROMPT_LIST result.prompts 必须是数组")
        summaries: list[tuple[int, str, int]] = []
        seen: set[int] = set()
        for value in prompts:
            if not isinstance(value, dict):
                raise PromptLibraryError("GET_PROMPT_LIST 条目必须是 object")
            prompt_id = value.get("prompt_id")
            name = value.get("name")
            body_bytes = value.get("body_bytes")
            if (
                not isinstance(prompt_id, int)
                or isinstance(prompt_id, bool)
                or not 1 <= prompt_id <= PROMPT_SLOT_COUNT
                or prompt_id in seen
            ):
                raise PromptLibraryError("GET_PROMPT_LIST prompt_id 无效或重复")
            if not isinstance(name, str) or not 0 < len(name.encode("utf-8")) <= PROMPT_NAME_MAX_BYTES:
                raise PromptLibraryError("GET_PROMPT_LIST name 超出协议限制")
            if (
                not isinstance(body_bytes, int)
                or isinstance(body_bytes, bool)
                or not 0 < body_bytes <= PROMPT_BODY_MAX_BYTES
            ):
                raise PromptLibraryError("GET_PROMPT_LIST body_bytes 超出协议限制")
            seen.add(prompt_id)
            summaries.append((prompt_id, name, body_bytes))
        if self._delete_prompt_id is not None:
            deleted = self._delete_prompt_id
            if deleted in seen:
                raise PromptLibraryError("DELETE_PROMPT 后读回列表仍包含目标槽位")
            assert self._library is not None
            self._library.confirm_deleted(deleted)
            self._store.save(self._library)
            self._delete_prompt_id = None
            self._set_status(PromptDeviceState.IDLE, "设备删除已读回确认")
            self._mark_listener_starting("正在恢复设备事件监听")
            self._schedule_event(100)
            return
        self._read_queue = [prompt_id for prompt_id, _name, _bytes in summaries]
        self._read_entries = []
        if self._read_queue:
            self._execute(get_prompt_command(self._read_queue.pop(0)))
        else:
            self._finish_full_read()

    def _handle_prompt(self, payload: dict) -> None:
        entry = _prompt_entry(payload)
        if self._pending_event_id is not None:
            event_id = self._pending_event_id
            expected_prompt_id = self._pending_event_prompt_id
            self._pending_event_id = None
            self._pending_event_prompt_id = None
            event_deadline = self._pending_event_deadline
            self._pending_event_deadline = 0.0
            if entry.prompt_id != expected_prompt_id:
                raise PromptLibraryError("提示词事件读回了错误的 prompt_id")
            event = self._prompt_event(event_id, entry)
            dispatch_result = self._dispatch_event_bus(event)
            if dispatch_result.handled:
                self._complete_prompt_event(event_id, entry, dispatch_result)
                return
            if self._async_event_dispatcher is not None:
                remaining_ms = max(
                    0, int((event_deadline - time.monotonic()) * 1000)
                )
                self._dispatch_sequence += 1
                dispatch_sequence = self._dispatch_sequence

                def completed(result: EventDispatchResult) -> None:
                    if dispatch_sequence != self._dispatch_sequence:
                        return
                    self._dispatch_sequence += 1
                    self._complete_prompt_event(event_id, entry, result)

                self._async_event_dispatcher(event, remaining_ms, completed)
                return
            self._complete_prompt_event(
                event_id, entry, self._paste_fallback(event_id, entry)
            )
            return
        if self._write_prompt_id is not None:
            assert self._library is not None
            expected = self._library.draft_entry(self._write_prompt_id)
            if expected is None or entry != expected:
                raise PromptLibraryError("SET_PROMPT 后读回内容与本地草稿不一致")
            self._library.confirm_entry(entry)
            self._store.save(self._library)
            self._read_prompt_ids.add(entry.prompt_id)
            self._write_prompt_id = None
            self._set_status(PromptDeviceState.IDLE, "提示词已写入并逐字节读回确认")
            self._mark_listener_starting("正在恢复设备事件监听")
            self._schedule_event(100)
            return
        self._read_entries.append(entry)
        if self._read_queue:
            self._execute(get_prompt_command(self._read_queue.pop(0)))
        else:
            self._finish_full_read()

    def _handle_set_prompt(self, payload: dict) -> None:
        if self._write_prompt_id is None:
            raise PromptLibraryError("收到无对应本地写入的 SET_PROMPT 响应")
        result = _result(payload, "SET_PROMPT")
        if result.get("prompt_id") != self._write_prompt_id:
            raise PromptLibraryError("SET_PROMPT 返回了错误的 prompt_id")
        self._execute(get_prompt_command(self._write_prompt_id))

    def _handle_delete_prompt(self, payload: dict) -> None:
        if self._delete_prompt_id is None:
            raise PromptLibraryError("收到无对应本地删除的 DELETE_PROMPT 响应")
        result = _result(payload, "DELETE_PROMPT")
        if result.get("prompt_id") != self._delete_prompt_id or result.get("deleted") is not True:
            raise PromptLibraryError("DELETE_PROMPT 响应没有确认目标槽位")
        self._execute(get_prompt_list_command())

    def _handle_prompt_event(self, payload: dict) -> None:
        result = _result(payload, "GET_PROMPT_EVENT")
        poll_after = result.get("poll_after_ms")
        if not isinstance(poll_after, int) or isinstance(poll_after, bool) or poll_after <= 0:
            raise PromptLibraryError("GET_PROMPT_EVENT poll_after_ms 必须是正整数")
        event = result.get("event")
        self._mark_listener_ready()
        if event is not None:
            if not isinstance(event, dict):
                raise PromptLibraryError("GET_PROMPT_EVENT event 必须是 object 或 null")
            event_id = event.get("event_id")
            prompt_id = event.get("prompt_id")
            if (
                not isinstance(event_id, int)
                or isinstance(event_id, bool)
                or event_id <= 0
                or not isinstance(prompt_id, int)
                or isinstance(prompt_id, bool)
                or not 1 <= prompt_id <= PROMPT_SLOT_COUNT
            ):
                raise PromptLibraryError("GET_PROMPT_EVENT event 字段无效")
            self._pending_event_id = event_id
            self._pending_event_prompt_id = prompt_id
            self._pending_event_deadline = (
                time.monotonic()
                + (
                    PROMPT_EVENT_ONLINE_BUDGET_MS
                    - PROMPT_EVENT_RESUME_RESERVE_MS
                )
                / 1000
            )
            self._append_event_log(
                "info",
                f"收到实体事件 {event_id}，正在读取提示词 {prompt_id}",
                event_id=event_id,
                prompt_id=prompt_id,
            )
            self._execute(get_prompt_command(prompt_id))
            return
        self._schedule_event(poll_after)

    def _finish_full_read(self) -> None:
        assert self._library is not None
        self._library.replace_confirmed(tuple(self._read_entries))
        self._store.save(self._library)
        self._read_entries.clear()
        self._read_prompt_ids = set(range(1, PROMPT_SLOT_COUNT + 1))
        self._set_status(PromptDeviceState.IDLE, "设备提示词已完整读取")
        self._mark_listener_starting("正在恢复设备事件监听")
        self._schedule_event(100)

    def _prompt_event(self, event_id: int, entry: PromptEntry) -> DeviceEvent:
        serial = self._library.serial if self._library is not None else ""
        return prompt_triggered_event(
            device_serial=serial,
            event_id=event_id,
            prompt_id=entry.prompt_id,
            prompt_name=entry.name,
            prompt_body=entry.body,
        )

    def _dispatch_event_bus(self, event: DeviceEvent) -> EventDispatchResult:
        if self._event_dispatcher is None:
            return EventDispatchResult(False, False, "")
        return self._event_dispatcher(event)

    def _paste_fallback(
        self, event_id: int, entry: PromptEntry
    ) -> EventDispatchResult:
        if self._helper is None:
            return EventDispatchResult(
                False, False, "收到设备触发，但当前事件没有绑定主机动作"
            )
        try:
            pasted = self._helper.handle_entry(entry)
        except PromptPasteError as exc:
            return EventDispatchResult(
                True,
                False,
                str(exc),
                str(exc),
            )
        return EventDispatchResult(
            True,
            True,
            f"已处理事件 {event_id} · 提示词 {entry.prompt_id} · "
            f"{pasted.body_bytes} UTF-8 字节",
        )

    def _complete_prompt_event(
        self,
        event_id: int,
        entry: PromptEntry,
        result: EventDispatchResult,
    ) -> None:
        self._helper_message = result.message
        if result.handled:
            level = "success" if result.succeeded else "error"
        else:
            level = "warning"
        self._append_event_log(
            level,
            result.message,
            technical=result.technical,
            event_id=event_id,
            prompt_id=entry.prompt_id,
        )
        self._mark_listener_ready()
        self._schedule_event(100)

    def _writable_context(self) -> tuple[PromptLibrary, DeviceSnapshot]:
        if self._library is None or self._snapshot is None:
            raise PromptLibraryError("请先连接支持提示词存储的设备")
        available, reason = _prompt_capability(self._snapshot)
        if not available:
            raise PromptLibraryError(reason)
        if self._snapshot.compatibility.get("write") is not True:
            raise PromptLibraryError("当前设备为只读状态，不能修改提示词")
        return self._library, self._snapshot

    def _poll_event(self) -> None:
        if (
            not self._trigger_supported
            or self._snapshot is None
            or self._polling_paused
            or self._event_poll_in_flight
            or self._status.state not in {PromptDeviceState.IDLE, PromptDeviceState.ERROR}
        ):
            return
        self._event_poll_in_flight = True
        self._execute(get_prompt_event_command())

    def _schedule_event(self, delay_ms: int) -> None:
        if (
            self._trigger_supported
            and self._auto_poll
            and not self._polling_paused
            and not self._event_poll_in_flight
            and not self._discard_event_prompt_response
            and self._pending_event_id is None
            and self._status.state in {PromptDeviceState.IDLE, PromptDeviceState.ERROR}
        ):
            self._event_timer.start(max(50, delay_ms))

    def _pause_listener_for_device_operation(self, operation: str) -> None:
        self._discard_pending_event_read()
        if self._trigger_supported:
            self._set_listener(
                PromptListenerState.PAUSED,
                f"助手暂时暂停；{operation}完成后会自动恢复监听",
            )

    def _mark_listener_starting(self, message: str) -> None:
        if not self._trigger_supported or self._polling_paused or not self._auto_poll:
            return
        self._set_listener(PromptListenerState.STARTING, message)

    def _mark_listener_ready(self) -> None:
        if not self._trigger_supported:
            return
        was_ready = self._listener_status.state is PromptListenerState.READY
        self._last_poll_error = ""
        self._set_listener(
            PromptListenerState.READY,
            "助手在线；正在监听实体提示词按键",
        )
        if not was_ready:
            self._append_event_log("success", "USB 提示词事件监听已就绪")

    def _handle_poll_failure(
        self, message: str, technical: str, *, delay_ms: int
    ) -> None:
        detail = technical.strip() or message
        should_log = (
            self._listener_status.state is not PromptListenerState.RETRYING
            or detail != self._last_poll_error
        )
        self._last_poll_error = detail
        self._set_listener(PromptListenerState.RETRYING, message, detail)
        if should_log:
            self._append_event_log(
                "warning",
                "设备事件轮询失败；助手将自动重试",
                technical=detail,
            )
        self._schedule_event(delay_ms)

    def _set_listener(
        self, state: PromptListenerState, message: str, technical: str = ""
    ) -> None:
        next_status = PromptListenerStatus(state, message, technical)
        if next_status == self._listener_status:
            return
        self._listener_status = next_status
        self._changed()

    def _append_event_log(
        self,
        level: str,
        message: str,
        *,
        technical: str = "",
        event_id: int | None = None,
        prompt_id: int | None = None,
    ) -> None:
        self._event_sequence += 1
        self._event_log.append(
            PromptEventLogEntry(
                sequence=self._event_sequence,
                level=level,
                message=message,
                technical=technical,
                event_id=event_id,
                prompt_id=prompt_id,
            )
        )
        self._changed()

    def _set_status(
        self, state: PromptDeviceState, message: str, technical: str = ""
    ) -> None:
        next_status = PromptDeviceStatus(state, message, technical)
        if next_status == self._status:
            return
        self._status = next_status
        self._changed()

    def _reset_operation_context(self) -> None:
        self._read_queue.clear()
        self._read_entries.clear()
        self._write_prompt_id = None
        self._delete_prompt_id = None
        self._pending_event_id = None
        self._pending_event_prompt_id = None
        self._pending_event_deadline = 0.0
        self._dispatch_sequence += 1

    def _discard_pending_event_read(self) -> None:
        if self._pending_event_id is None:
            return
        self._pending_event_id = None
        self._pending_event_prompt_id = None
        self._pending_event_deadline = 0.0
        self._discard_event_prompt_response = True


def _prompt_capability(snapshot: DeviceSnapshot) -> tuple[bool, str]:
    features = snapshot.capabilities.get("features")
    limits = snapshot.capabilities.get("limits")
    actions = snapshot.capabilities.get("actions")
    if not isinstance(features, dict) or features.get("prompt_storage") is not True:
        return False, "设备没有通过 CAPABILITIES 声明 prompt_storage"
    expected_limits = {
        "prompt_slots": PROMPT_SLOT_COUNT,
        "prompt_name_bytes": PROMPT_NAME_MAX_BYTES,
        "prompt_body_bytes": PROMPT_BODY_MAX_BYTES,
        "all_prompt_body_bytes": PROMPT_TOTAL_BODY_MAX_BYTES,
    }
    if not isinstance(limits, dict) or any(
        limits.get(name) != value for name, value in expected_limits.items()
    ):
        return False, "设备提示词容量与当前 BORING 控制台支持范围不一致"
    if not isinstance(actions, list) or "prompt" not in actions:
        return False, "设备没有声明 prompt 配置动作"
    return True, "设备已声明 UTF-8 提示词存储能力"


def _result(payload: dict, command_name: str) -> dict:
    if not isinstance(payload, dict) or payload.get("command") != command_name:
        raise PromptLibraryError(f"{command_name} 响应 command 不匹配")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise PromptLibraryError(f"{command_name} result 必须是 object")
    return result


def _prompt_entry(payload: dict) -> PromptEntry:
    result = _result(payload, "GET_PROMPT")
    entry = PromptEntry.from_mapping(result)
    if result.get("body_bytes") != entry.body_bytes:
        raise PromptLibraryError("GET_PROMPT body_bytes 与 UTF-8 正文不一致")
    return entry
