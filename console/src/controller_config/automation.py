from __future__ import annotations

import json
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    QSaveFile,
    QStandardPaths,
    QTimer,
    Signal,
)

from controller_config.device_events import (
    DeviceEvent,
    automation_manual_test_event,
)
from controller_config.prompt_helper import PromptHelperRuntime, PromptPasteError
from controller_config.prompt_library import PROMPT_SLOT_COUNT, PromptEntry
from controller_config.extension_runner import (
    ExtensionRunnerCommand,
    private_extension_runner_command,
)


class AutomationError(ValueError):
    """A local automation definition or execution request is invalid."""


MAX_SCRIPT_OUTPUT_BYTES = 64 * 1024
_TRUNCATED = "[输出已截断，仅保留末尾内容]\n"


def _bounded_output(data: bytes | bytearray, *, truncated: bool = False) -> str:
    truncated = truncated or len(data) > MAX_SCRIPT_OUTPUT_BYTES
    prefix = _TRUNCATED if truncated else ""
    limit = MAX_SCRIPT_OUTPUT_BYTES - len(prefix.encode("utf-8"))
    text = bytes(data[-limit:]).decode("utf-8", errors="replace").strip()
    return prefix + text.encode("utf-8")[-limit:].decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class EventDispatchResult:
    handled: bool
    succeeded: bool
    message: str
    technical: str = ""


EventHandler = Callable[[DeviceEvent], EventDispatchResult | None]


class DeviceEventBus(QObject):
    """Distribute protocol-backed semantic events inside BORING Console."""

    event_received = Signal(object)

    def __init__(self, *, max_events: int = 100, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._handlers: list[EventHandler] = []
        self._events: deque[DeviceEvent] = deque(maxlen=max_events)

    @property
    def events(self) -> tuple[DeviceEvent, ...]:
        return tuple(self._events)

    def register(self, handler: EventHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def dispatch(self, event: DeviceEvent) -> EventDispatchResult:
        self._events.append(event)
        self.event_received.emit(event)
        for handler in tuple(self._handlers):
            result = handler(event)
            if result is not None and result.handled:
                return result
        return EventDispatchResult(
            handled=False,
            succeeded=False,
            message="当前事件没有绑定主机动作",
        )


@dataclass(frozen=True)
class LocalScriptAutomation:
    automation_id: str
    name: str
    trigger_prompt_id: int
    script_path: str
    enabled: bool

    def validate(self) -> None:
        if not self.automation_id.strip():
            raise AutomationError("本地脚本 ID 不能为空")
        if not self.name.strip():
            raise AutomationError("本地脚本名称不能为空")
        if (
            not isinstance(self.trigger_prompt_id, int)
            or isinstance(self.trigger_prompt_id, bool)
            or not 1 <= self.trigger_prompt_id <= PROMPT_SLOT_COUNT
        ):
            raise AutomationError(
                f"触发提示词槽位必须在 1–{PROMPT_SLOT_COUNT} 范围内"
            )
        if not self.script_path.strip():
            raise AutomationError("请选择本地 Python 脚本")

    @classmethod
    def from_mapping(cls, value: object) -> "LocalScriptAutomation":
        if not isinstance(value, dict):
            raise AutomationError("本地脚本记录必须是 object")
        automation_id = value.get("automation_id")
        name = value.get("name")
        prompt_id = value.get("trigger_prompt_id")
        script_path = value.get("script_path")
        enabled = value.get("enabled")
        if not all(isinstance(item, str) for item in (automation_id, name, script_path)):
            raise AutomationError("本地脚本 ID、名称和脚本路径必须是字符串")
        if not isinstance(enabled, bool):
            raise AutomationError("本地脚本 enabled 必须是布尔值")
        definition = cls(automation_id, name, prompt_id, script_path, enabled)
        definition.validate()
        return definition

    def as_mapping(self) -> dict[str, object]:
        return asdict(self)


class AutomationStore:
    VERSION = 1

    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            root = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            directory = Path(root) / "automations"
        self._directory = directory

    def load(self, serial: str) -> tuple[LocalScriptAutomation, ...]:
        path = self.path_for(serial)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AutomationError(f"无法读取本地脚本：{exc}") from exc
        if not isinstance(value, dict) or value.get("version") != self.VERSION:
            raise AutomationError("本地脚本文件版本不受支持")
        if value.get("serial") != serial:
            raise AutomationError("本地脚本与当前设备序列号不一致")
        items = value.get("automations")
        if not isinstance(items, list):
            raise AutomationError("automations 必须是数组")
        definitions = tuple(LocalScriptAutomation.from_mapping(item) for item in items)
        _validate_unique(definitions)
        return definitions

    def save(self, serial: str, definitions: tuple[LocalScriptAutomation, ...]) -> None:
        _validate_unique(definitions)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "serial": serial,
            "automations": [definition.as_mapping() for definition in definitions],
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        output = QSaveFile(str(self.path_for(serial)))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise AutomationError(f"无法保存本地脚本：{output.errorString()}")
        if output.write(data) != len(data) or not output.commit():
            raise AutomationError(f"无法保存本地脚本：{output.errorString()}")

    def path_for(self, serial: str) -> Path:
        safe_serial = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in serial
        )
        if not safe_serial:
            raise AutomationError("设备序列号不能用于本地脚本路径")
        return self._directory / f"{safe_serial}.json"


@dataclass(frozen=True)
class AutomationRunResult:
    exit_code: int
    standard_output: str = ""
    standard_error: str = ""
    cancelled: bool = False


RunCompleted = Callable[[AutomationRunResult], None]


class ScriptRunner(Protocol):
    def launch(
        self,
        definition: LocalScriptAutomation,
        event: DeviceEvent,
        completed: RunCompleted,
    ) -> None: ...

    def shutdown(self) -> None: ...

    def cancel(self) -> None: ...


class QProcessScriptRunner(QObject):
    """Run one local script through BORING's private extension runner."""

    def __init__(
        self,
        *,
        runner_command: ExtensionRunnerCommand | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner_command = runner_command or private_extension_runner_command()
        self._processes: set[QProcess] = set()
        self._cancelled: set[QProcess] = set()

    @property
    def active_count(self) -> int:
        return len(self._processes)

    def launch(
        self,
        definition: LocalScriptAutomation,
        event: DeviceEvent,
        completed: RunCompleted,
    ) -> None:
        if self._processes:
            raise AutomationError("已有本地脚本正在运行，请先停止或等待完成；不会排队。")
        script = Path(definition.script_path)
        runner_program = Path(self._runner_command.program)
        if not runner_program.is_file():
            raise AutomationError(
                f"BORING 私有扩展运行器不可用：{runner_program}"
            )
        process = QProcess(self)
        self._processes.add(process)
        process.setProgram(str(runner_program))
        process.setArguments(self._runner_command.for_script(script))
        process.setWorkingDirectory(str(script.parent))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("BORING_EVENT_KIND", event.kind)
        environment.insert("BORING_EVENT_SOURCE", event.source)
        environment.insert("BORING_DEVICE_SERIAL", event.device_serial)
        environment.insert(
            "BORING_EVENT_ID",
            "" if event.event_id is None else str(event.event_id),
        )
        prompt_id = event.payload.get("prompt_id")
        environment.insert(
            "BORING_PROMPT_ID",
            str(prompt_id) if isinstance(prompt_id, int) else "",
        )
        process.setProcessEnvironment(environment)
        event_bytes = (json.dumps(event.as_mapping(), ensure_ascii=False) + "\n").encode("utf-8")
        standard_output = bytearray()
        standard_error = bytearray()
        truncated = [False, False]
        finished = False

        def finish(result: AutomationRunResult) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._processes.discard(process)
            self._cancelled.discard(process)
            completed(result)
            process.deleteLater()

        def write_event() -> None:
            process.write(event_bytes)
            process.closeWriteChannel()

        def read_standard_output() -> None:
            standard_output.extend(process.readAllStandardOutput())
            if len(standard_output) > MAX_SCRIPT_OUTPUT_BYTES:
                truncated[0] = True
                del standard_output[:-MAX_SCRIPT_OUTPUT_BYTES]

        def read_standard_error() -> None:
            standard_error.extend(process.readAllStandardError())
            if len(standard_error) > MAX_SCRIPT_OUTPUT_BYTES:
                truncated[1] = True
                del standard_error[:-MAX_SCRIPT_OUTPUT_BYTES]

        def process_finished(exit_code: int, _exit_status: object) -> None:
            read_standard_output()
            read_standard_error()
            finish(
                AutomationRunResult(
                    exit_code=exit_code,
                    standard_output=_bounded_output(standard_output, truncated=truncated[0]),
                    standard_error=_bounded_output(standard_error, truncated=truncated[1]),
                    cancelled=process in self._cancelled,
                )
            )

        def process_error(_error: object) -> None:
            if _error == QProcess.ProcessError.FailedToStart:
                finish(AutomationRunResult(-1, standard_error=process.errorString()))

        process.started.connect(write_event)
        process.readyReadStandardOutput.connect(read_standard_output)
        process.readyReadStandardError.connect(read_standard_error)
        process.finished.connect(process_finished)
        process.errorOccurred.connect(process_error)
        process.start()

    def cancel(self) -> None:
        for process in tuple(self._processes):
            self._cancelled.add(process)
            process.terminate()
            QTimer.singleShot(500, self, lambda p=process: self._kill_if_running(p))

    def _kill_if_running(self, process: QProcess) -> None:
        if process in self._processes and process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def shutdown(self) -> None:
        processes = tuple(self._processes)
        self._cancelled.update(processes)
        running = [
            process
            for process in processes
            if process.state() != QProcess.ProcessState.NotRunning
        ]
        for process in running:
            process.terminate()
        self._wait_for_processes(running, timeout_ms=500)

        survivors = [
            process
            for process in running
            if process.state() != QProcess.ProcessState.NotRunning
        ]
        for process in survivors:
            process.kill()
        self._wait_for_processes(survivors, timeout_ms=500)

        for process in processes:
            self._processes.discard(process)
            self._cancelled.discard(process)
            process.deleteLater()

    @staticmethod
    def _wait_for_processes(processes: list[QProcess], *, timeout_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        for process in processes:
            if process.state() == QProcess.ProcessState.NotRunning:
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            process.waitForFinished(remaining_ms)


@dataclass(frozen=True)
class AutomationLogEntry:
    sequence: int
    timestamp: str
    level: str
    message: str
    technical: str = ""


class AutomationHost(QObject):
    changed = Signal()

    def __init__(
        self,
        store: AutomationStore,
        *,
        runner: ScriptRunner | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._runner = runner or QProcessScriptRunner(parent=self)
        self._serial = ""
        self._definitions: tuple[LocalScriptAutomation, ...] = ()
        self._logs: deque[AutomationLogEntry] = deque(maxlen=100)
        self._sequence = 0
        self._load_error = ""
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def cancel_run(self) -> None:
        if self._running:
            self._runner.cancel()

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def definitions(self) -> tuple[LocalScriptAutomation, ...]:
        return self._definitions

    @property
    def logs(self) -> tuple[AutomationLogEntry, ...]:
        return tuple(self._logs)

    @property
    def load_error(self) -> str:
        return self._load_error

    def bind(self, serial: str) -> None:
        if not serial or serial == self._serial:
            return
        if self._serial:
            self._runner.shutdown()
            self._running = False
        self._serial = serial
        self._logs.clear()
        self._sequence = 0
        try:
            self._definitions = self._store.load(serial)
            self._load_error = ""
        except AutomationError as exc:
            self._definitions = ()
            self._load_error = str(exc)
            self._append_log("error", "本地脚本加载失败", str(exc))
        self.changed.emit()

    def save_definition(
        self,
        *,
        automation_id: str | None,
        name: str,
        trigger_prompt_id: int,
        script_path: str,
        enabled: bool,
    ) -> LocalScriptAutomation:
        self._require_serial()
        path = Path(script_path).expanduser()
        if not path.is_file():
            raise AutomationError(f"脚本文件不存在：{path}")
        definition = LocalScriptAutomation(
            automation_id=automation_id or uuid.uuid4().hex,
            name=name.strip(),
            trigger_prompt_id=trigger_prompt_id,
            script_path=str(path.resolve()),
            enabled=bool(enabled),
        )
        definition.validate()
        candidate = {
            item.automation_id: item
            for item in self._definitions
            if item.automation_id != definition.automation_id
        }
        candidate[definition.automation_id] = definition
        definitions = tuple(candidate[key] for key in sorted(candidate))
        _validate_unique(definitions)
        self._store.save(self._serial, definitions)
        self._definitions = definitions
        self._load_error = ""
        self._append_log("info", f"已保存本地脚本：{definition.name}")
        self.changed.emit()
        return definition

    def delete_definition(self, automation_id: str) -> None:
        self._require_serial()
        definitions = tuple(
            item for item in self._definitions if item.automation_id != automation_id
        )
        if len(definitions) == len(self._definitions):
            raise AutomationError("未找到要删除的本地脚本")
        self._store.save(self._serial, definitions)
        self._definitions = definitions
        self._append_log("info", "已删除本地脚本")
        self.changed.emit()

    def run_test(self, automation_id: str) -> None:
        definition = self._definition(automation_id)
        self._launch(
            definition,
            automation_manual_test_event(
                device_serial=self._serial,
                prompt_id=definition.trigger_prompt_id,
            ),
        )

    def handle_event(self, event: DeviceEvent) -> EventDispatchResult | None:
        if event.kind != "prompt.triggered" or event.device_serial != self._serial:
            return None
        prompt_id = event.payload.get("prompt_id")
        definition = next(
            (
                item
                for item in self._definitions
                if item.enabled and item.trigger_prompt_id == prompt_id
            ),
            None,
        )
        if definition is None:
            return None
        try:
            self._launch(definition, event)
        except AutomationError as exc:
            self._append_log("error", f"本地脚本启动失败：{definition.name}", str(exc))
            self.changed.emit()
            return EventDispatchResult(True, False, str(exc), str(exc))
        return EventDispatchResult(
            handled=True,
            succeeded=True,
            message=f"事件 {event.event_id} 已交给本地脚本：{definition.name}",
        )

    def clear_logs(self) -> None:
        self._logs.clear()
        self.changed.emit()

    def shutdown(self) -> None:
        self._runner.shutdown()

    def _launch(self, definition: LocalScriptAutomation, event: DeviceEvent) -> None:
        if self._running:
            raise AutomationError("已有本地脚本正在运行，请先停止或等待完成；不会排队。")
        if not Path(definition.script_path).is_file():
            raise AutomationError(f"脚本文件不存在：{definition.script_path}")
        serial = self._serial

        def completed(result: AutomationRunResult) -> None:
            if serial != self._serial:
                return
            self._running = False
            if result.cancelled:
                self._append_log("info", f"已停止：{definition.name}", result.standard_output)
            elif result.exit_code == 0:
                self._append_log(
                    "success",
                    f"运行完成：{definition.name}",
                    result.standard_output,
                )
            else:
                self._append_log(
                    "error",
                    f"运行失败：{definition.name} · exit {result.exit_code}",
                    result.standard_error or result.standard_output,
                )
            self.changed.emit()

        self._running = True
        try:
            self._runner.launch(definition, event, completed)
        except Exception:
            self._running = False
            self.changed.emit()
            raise
        if self._running:
            self._append_log("info", f"已启动：{definition.name}")
        self.changed.emit()

    def _definition(self, automation_id: str) -> LocalScriptAutomation:
        for definition in self._definitions:
            if definition.automation_id == automation_id:
                return definition
        raise AutomationError("未找到要运行的本地脚本")

    def _require_serial(self) -> None:
        if not self._serial:
            raise AutomationError("请先连接一台 BORING 设备")

    def _append_log(self, level: str, message: str, technical: str = "") -> None:
        self._sequence += 1
        self._logs.append(
            AutomationLogEntry(
                sequence=self._sequence,
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                level=level,
                message=message,
                technical=_bounded_output(technical.encode("utf-8")),
            )
        )


class PromptPasteEventHandler:
    def __init__(self, helper: PromptHelperRuntime) -> None:
        self._helper = helper

    def __call__(self, event: DeviceEvent) -> EventDispatchResult | None:
        if event.kind != "prompt.triggered":
            return None
        try:
            entry = PromptEntry.from_mapping(
                {
                    "prompt_id": event.payload.get("prompt_id"),
                    "name": event.payload.get("prompt_name"),
                    "body": event.payload.get("prompt_body"),
                }
            )
            pasted = self._helper.handle_entry(entry)
        except (PromptPasteError, ValueError) as exc:
            return EventDispatchResult(True, False, str(exc), str(exc))
        return EventDispatchResult(
            handled=True,
            succeeded=True,
            message=(
                f"已处理事件 {event.event_id} · 提示词 {entry.prompt_id} · "
                f"{pasted.body_bytes} UTF-8 字节"
            ),
        )


def _validate_unique(definitions: tuple[LocalScriptAutomation, ...]) -> None:
    ids: set[str] = set()
    prompt_ids: set[int] = set()
    for definition in definitions:
        definition.validate()
        if definition.automation_id in ids:
            raise AutomationError(f"本地脚本 ID 重复：{definition.automation_id}")
        if definition.trigger_prompt_id in prompt_ids:
            raise AutomationError(
                f"提示词槽位 {definition.trigger_prompt_id} 已绑定其他本地脚本"
            )
        ids.add(definition.automation_id)
        prompt_ids.add(definition.trigger_prompt_id)
