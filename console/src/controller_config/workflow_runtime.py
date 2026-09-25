from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.automation import EventDispatchResult
from controller_config.device_events import DeviceEvent
from controller_config.host_actions import (
    HostActionContext,
    HostActionExecution,
    HostActionJob,
    HostActionRegistry,
)
from controller_config.workflows import LocalWorkflow, WorkflowError, WorkflowStore


@dataclass(frozen=True)
class WorkflowRunLog:
    sequence: int
    timestamp: str
    level: str
    workflow_id: str
    workflow_name: str
    message: str
    step_index: int | None = None
    technical: str = ""


@dataclass(frozen=True)
class WorkflowRunResult:
    succeeded: bool
    message: str
    failed_step: int | None = None
    technical: str = ""
    cancelled: bool = False
    timed_out: bool = False


class WorkflowHost(QObject):
    """Persist and execute small, linear, built-in host workflows."""

    changed = Signal()
    run_completed = Signal(object)

    def __init__(
        self,
        store: WorkflowStore,
        *,
        registry: HostActionRegistry | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._registry = registry or HostActionRegistry()
        self._serial = ""
        self._workflows: tuple[LocalWorkflow, ...] = ()
        self._logs: deque[WorkflowRunLog] = deque(maxlen=150)
        self._sequence = 0
        self._load_error = ""
        self.external_busy: Callable[[], bool] = lambda: False
        self.running = False
        self.status = "idle"
        self.current_step: int | None = None
        self._active: LocalWorkflow | None = None
        self._context = HostActionContext()
        self._step_job: HostActionJob | None = None
        self._completed: Callable[[WorkflowRunResult], None] | None = None
        self._cancelled = False
        self._timed_out = False
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.timeout.connect(self._next_step)

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def workflows(self) -> tuple[LocalWorkflow, ...]:
        return self._workflows

    @property
    def logs(self) -> tuple[WorkflowRunLog, ...]:
        return tuple(self._logs)

    @property
    def registry(self) -> HostActionRegistry:
        return self._registry

    @property
    def load_error(self) -> str:
        return self._load_error

    def bind(self, serial: str) -> None:
        if not serial or serial == self._serial:
            return
        self.cancel_run()
        self._serial = serial
        self._logs.clear()
        self._sequence = 0
        try:
            self._workflows = self._store.load(serial)
            self._load_error = ""
        except WorkflowError as exc:
            self._workflows = ()
            self._load_error = str(exc)
            self._append_log(None, "error", "内置自动化加载失败", technical=str(exc))
        self.changed.emit()

    def save_workflow(self, workflow: LocalWorkflow) -> LocalWorkflow:
        self._require_serial()
        workflow.validate()
        candidate = {
            item.workflow_id: item
            for item in self._workflows
            if item.workflow_id != workflow.workflow_id
        }
        candidate[workflow.workflow_id] = workflow
        workflows = tuple(candidate[key] for key in sorted(candidate))
        self._store.save(self._serial, workflows)
        self._workflows = workflows
        self._load_error = ""
        self._append_log(workflow, "info", f"已保存自动化：{workflow.name}")
        self.changed.emit()
        return workflow

    def create_workflow(
        self,
        *,
        name: str,
        trigger_prompt_id: int | None,
        steps,
        review_required: bool = False,
    ) -> LocalWorkflow:
        return self.save_workflow(
            LocalWorkflow(
                uuid.uuid4().hex,
                name.strip(),
                trigger_prompt_id,
                tuple(steps),
                False,
                False,
                review_required,
            )
        )

    def delete_workflow(self, workflow_id: str) -> None:
        self._require_serial()
        workflows = tuple(
            workflow
            for workflow in self._workflows
            if workflow.workflow_id != workflow_id
        )
        if len(workflows) == len(self._workflows):
            raise WorkflowError("未找到要删除的自动化")
        self._store.save(self._serial, workflows)
        self._workflows = workflows
        self._append_log(None, "info", "已删除自动化")
        self.changed.emit()

    def duplicate_workflow(self, workflow_id: str) -> LocalWorkflow:
        duplicated = self._workflow(workflow_id).duplicate()
        return self.save_workflow(duplicated)

    def test_workflow(
        self,
        workflow_id: str,
        completed: Callable[[WorkflowRunResult], None] | None = None,
    ) -> None:
        workflow = deepcopy(self._workflow(workflow_id))
        serial = self._serial

        def tested(result: WorkflowRunResult) -> None:
            # A completed trial must not overwrite edits made while it ran.
            current = next(
                (item for item in self._workflows if item.workflow_id == workflow_id), None
            )
            if result.succeeded and serial == self._serial and current == workflow:
                self._replace_without_log(replace(current, tested=True))
                self.changed.emit()
            if completed is not None:
                completed(result)

        self.run_definition(workflow, completed=tested)

    def handle_event(self, event: DeviceEvent) -> EventDispatchResult | None:
        if event.kind != "prompt.triggered" or event.device_serial != self._serial:
            return None
        prompt_id = event.payload.get("prompt_id")
        workflow = next(
            (
                item
                for item in self._workflows
                if item.enabled and item.trigger_prompt_id == prompt_id
            ),
            None,
        )
        if workflow is None:
            return None
        try:
            self.run_definition(workflow, event)
        except WorkflowError as exc:
            return EventDispatchResult(True, False, str(exc), str(exc))
        return EventDispatchResult(
            handled=True,
            succeeded=True,
            message=f"已开始运行：{workflow.name}",
        )

    def clear_logs(self) -> None:
        self._logs.clear()
        self.changed.emit()

    def run_definition(
        self,
        definition: LocalWorkflow,
        event: DeviceEvent | None = None,
        completed: Callable[[WorkflowRunResult], None] | None = None,
        *,
        timeout_ms: int = 60_000,
    ) -> None:
        if self.running or self.external_busy():
            raise WorkflowError("已有电脑任务正在运行，请先停止或等待完成；不会排队。")
        definition.validate()
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise WorkflowError("运行时限必须是正整数毫秒")
        self._active = deepcopy(definition)
        self._active_serial = self._serial
        self._context = HostActionContext()
        self._completed = completed
        self._cancelled = False
        self._timed_out = False
        self.running = True
        self.status = "running"
        self.current_step = 0
        self._append_log(
            self._active,
            "info",
            "开始手动测试" if event is None else "收到实体事件，开始运行",
        )
        self._deadline.start(timeout_ms)
        self.changed.emit()
        self._advance.start(0)

    def cancel_run(self) -> None:
        if not self.running:
            return
        self._cancelled = True
        self.status = "stopping"
        self._deadline.stop()
        self.changed.emit()
        if self._step_job is not None:
            self._step_job.cancel()
        else:
            self._finish_cancelled()

    def _timeout(self) -> None:
        if not self.running:
            return
        self._timed_out = True
        self.status = "timing_out"
        self.changed.emit()
        if self._step_job is not None:
            self._step_job.cancel()
        else:
            self._finish_cancelled()

    def shutdown(self) -> None:
        self.cancel_run()

    def _next_step(self) -> None:
        if not self.running:
            return
        if self._cancelled or self._timed_out:
            self._finish_cancelled()
            return
        workflow = self._active
        assert workflow is not None and self.current_step is not None
        if self.current_step == len(workflow.steps):
            if all(step.action_id == 'open_target' for step in workflow.steps):
                message = f"已请求打开 {len(workflow.steps)} 项"
            else:
                message = f"任务已完成：{workflow.name}"
            self._finish(WorkflowRunResult(True, message))
            return
        step = workflow.steps[self.current_step]
        self.current_step += 1
        self.changed.emit()
        self._step_job = self._registry.execute_async(
            step, self._context, self._step_finished, parent=self
        )

    def _step_finished(self, execution: HostActionExecution) -> None:
        self._step_job = None
        if self._cancelled or self._timed_out:
            self._finish_cancelled()
            return
        workflow = self._active
        assert workflow is not None and self.current_step is not None
        name = self._registry.definition(
            workflow.steps[self.current_step - 1].action_id
        ).name
        if not execution.succeeded:
            self._finish(
                WorkflowRunResult(
                    False, f"第 {self.current_step} 步失败：{name}",
                    self.current_step, execution.technical or execution.message,
                )
            )
            return
        self._context = execution.context
        self._append_active_log(
            "success", f"第 {self.current_step} 步完成：{name}",
            step_index=self.current_step,
        )
        self._advance.start(0)

    def _finish_cancelled(self) -> None:
        message = "运行超时" if self._timed_out else "已停止"
        self._finish(
            WorkflowRunResult(
                False, f"{message}；已经写入或打开的内容不会撤销", self.current_step,
                cancelled=self._cancelled, timed_out=self._timed_out,
            )
        )

    def _append_active_log(self, level: str, message: str, **kwargs) -> None:
        if self._active_serial == self._serial:
            self._append_log(self._active, level, message, **kwargs)

    def _finish(self, result: WorkflowRunResult) -> None:
        if not self.running:
            return
        self._deadline.stop()
        self._advance.stop()
        self.running = False
        if result.timed_out:
            self.status = "timed_out"
        elif result.cancelled:
            self.status = "cancelled"
        else:
            self.status = "completed" if result.succeeded else "failed"
        self._append_active_log(
            "success" if result.succeeded else "info" if result.cancelled else "error",
            result.message, step_index=result.failed_step, technical=result.technical,
        )
        completed, self._completed = self._completed, None
        self._active = None
        self.changed.emit()
        self.run_completed.emit(result)
        if completed is not None:
            completed(result)

    def _replace_without_log(self, workflow: LocalWorkflow) -> None:
        workflows = tuple(
            workflow if item.workflow_id == workflow.workflow_id else item
            for item in self._workflows
        )
        self._store.save(self._serial, workflows)
        self._workflows = workflows

    def _workflow(self, workflow_id: str) -> LocalWorkflow:
        for workflow in self._workflows:
            if workflow.workflow_id == workflow_id:
                return workflow
        raise WorkflowError("未找到自动化")

    def _require_serial(self) -> None:
        if not self._serial:
            raise WorkflowError("请先连接一台 BORING 设备")

    def _append_log(
        self,
        workflow: LocalWorkflow | None,
        level: str,
        message: str,
        *,
        step_index: int | None = None,
        technical: str = "",
    ) -> None:
        self._sequence += 1
        self._logs.append(
            WorkflowRunLog(
                self._sequence,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                level,
                workflow.workflow_id if workflow is not None else "",
                workflow.name if workflow is not None else "",
                message,
                step_index,
                technical,
            )
        )
