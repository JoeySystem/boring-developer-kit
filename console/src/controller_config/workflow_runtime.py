from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from controller_config.automation import EventDispatchResult
from controller_config.device_events import DeviceEvent
from controller_config.host_actions import HostActionContext, HostActionRegistry
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


class WorkflowHost(QObject):
    """Persist and execute small, linear, built-in host workflows."""

    changed = Signal()

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
        trigger_prompt_id: int,
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

    def test_workflow(self, workflow_id: str) -> WorkflowRunResult:
        workflow = self._workflow(workflow_id)
        result = self._execute(workflow, source="manual")
        if result.succeeded:
            tested = replace(workflow, tested=True, enabled=False)
            self._replace_without_log(tested)
            self._append_log(tested, "success", "手动步骤测试通过；实体触发仍需配置设备提示词槽位")
        self.changed.emit()
        return result

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
        result = self._execute(workflow, source="device")
        self.changed.emit()
        return EventDispatchResult(
            handled=True,
            succeeded=result.succeeded,
            message=result.message,
            technical=result.technical,
        )

    def clear_logs(self) -> None:
        self._logs.clear()
        self.changed.emit()

    def _execute(self, workflow: LocalWorkflow, *, source: str) -> WorkflowRunResult:
        self._append_log(
            workflow,
            "info",
            "开始手动测试" if source == "manual" else "收到实体事件，开始运行",
        )
        context = HostActionContext()
        for index, step in enumerate(workflow.steps, start=1):
            definition = self._registry.definition(step.action_id)
            execution = self._registry.execute(step, context)
            if not execution.succeeded:
                message = f"第 {index} 步失败：{definition.name}"
                self._append_log(
                    workflow,
                    "error",
                    message,
                    step_index=index,
                    technical=execution.technical or execution.message,
                )
                return WorkflowRunResult(
                    False,
                    message,
                    failed_step=index,
                    technical=execution.technical or execution.message,
                )
            context = execution.context
            self._append_log(
                workflow,
                "success",
                f"第 {index} 步完成：{definition.name}",
                step_index=index,
            )
        message = f"自动化运行完成：{workflow.name}"
        self._append_log(workflow, "success", message)
        return WorkflowRunResult(True, message)

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
