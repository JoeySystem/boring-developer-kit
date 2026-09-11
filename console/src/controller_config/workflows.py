from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSaveFile, QStandardPaths

from controller_config.prompt_library import PROMPT_SLOT_COUNT


WORKFLOW_ACTION_IDS = (
    "capture_selection",
    "read_clipboard",
    "append_text_file",
    "open_target",
    "show_notification",
)


class WorkflowError(ValueError):
    """A built-in workflow definition cannot be stored or executed."""


@dataclass(frozen=True)
class WorkflowStep:
    action_id: str
    parameters: dict[str, object]

    def validate(self) -> None:
        if self.action_id not in WORKFLOW_ACTION_IDS:
            raise WorkflowError(f"不支持的内置步骤：{self.action_id}")
        if not isinstance(self.parameters, dict):
            raise WorkflowError("步骤参数必须是 object")
        expected: dict[str, type] = {}
        if self.action_id == "append_text_file":
            expected = {"path": str, "separator": str}
        elif self.action_id == "open_target":
            expected = {"target": str}
        elif self.action_id == "show_notification":
            expected = {"message": str}
        if set(self.parameters) != set(expected):
            raise WorkflowError(f"{self.action_id} 的参数字段不完整或包含未知字段")
        for name, kind in expected.items():
            if not isinstance(self.parameters[name], kind):
                raise WorkflowError(f"{self.action_id}.{name} 参数类型错误")
        if self.action_id == "append_text_file":
            path = str(self.parameters["path"]).strip()
            if not path:
                raise WorkflowError("请选择要追加的 Markdown 或 TXT 文件")
            if Path(path).suffix.lower() not in {".md", ".txt"}:
                raise WorkflowError("追加目标必须是 .md 或 .txt 文件")
        elif self.action_id == "open_target":
            if not str(self.parameters["target"]).strip():
                raise WorkflowError("请选择要打开的应用、文件、目录或网址")
        elif self.action_id == "show_notification":
            message = str(self.parameters["message"]).strip()
            if not message:
                raise WorkflowError("通知内容不能为空")
            if len(message) > 160:
                raise WorkflowError("通知内容不能超过 160 个字符")

    def as_mapping(self) -> dict[str, object]:
        return {"action_id": self.action_id, "parameters": dict(self.parameters)}

    @classmethod
    def from_mapping(cls, value: object) -> "WorkflowStep":
        if not isinstance(value, dict) or set(value) != {"action_id", "parameters"}:
            raise WorkflowError("自动化步骤字段不完整或包含未知字段")
        action_id = value.get("action_id")
        parameters = value.get("parameters")
        if not isinstance(action_id, str) or not isinstance(parameters, dict):
            raise WorkflowError("自动化步骤 action_id 或 parameters 类型错误")
        step = cls(action_id, dict(parameters))
        step.validate()
        return step


@dataclass(frozen=True)
class LocalWorkflow:
    workflow_id: str
    name: str
    trigger_prompt_id: int
    steps: tuple[WorkflowStep, ...]
    enabled: bool = False
    tested: bool = False
    review_required: bool = False

    def validate(self) -> None:
        if not self.workflow_id.strip():
            raise WorkflowError("自动化 ID 不能为空")
        if not self.name.strip():
            raise WorkflowError("自动化名称不能为空")
        if (
            not isinstance(self.trigger_prompt_id, int)
            or isinstance(self.trigger_prompt_id, bool)
            or not 1 <= self.trigger_prompt_id <= PROMPT_SLOT_COUNT
        ):
            raise WorkflowError(
                f"触发提示词槽位必须在 1–{PROMPT_SLOT_COUNT} 范围内"
            )
        if not 1 <= len(self.steps) <= 5:
            raise WorkflowError("自动化必须包含 1–5 个步骤")
        for step in self.steps:
            if not isinstance(step, WorkflowStep):
                raise WorkflowError("自动化步骤类型错误")
            step.validate()
        if self.enabled and not self.tested:
            raise WorkflowError("请先成功测试自动化，再启用实体触发")
        if self.enabled and self.review_required:
            raise WorkflowError("请先检查本机路径和控件绑定，再启用自动化")

    def as_mapping(self) -> dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "name": self.name,
            "trigger_prompt_id": self.trigger_prompt_id,
            "steps": [step.as_mapping() for step in self.steps],
            "enabled": self.enabled,
            "tested": self.tested,
            "review_required": self.review_required,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "LocalWorkflow":
        expected = {
            "workflow_id",
            "name",
            "trigger_prompt_id",
            "steps",
            "enabled",
            "tested",
            "review_required",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise WorkflowError("自动化字段不完整或包含未知字段")
        workflow_id = value.get("workflow_id")
        name = value.get("name")
        trigger_prompt_id = value.get("trigger_prompt_id")
        steps = value.get("steps")
        enabled = value.get("enabled")
        tested = value.get("tested")
        review_required = value.get("review_required")
        if not isinstance(workflow_id, str) or not isinstance(name, str):
            raise WorkflowError("自动化 ID 和名称必须是字符串")
        if not isinstance(steps, list):
            raise WorkflowError("自动化 steps 必须是数组")
        if not all(isinstance(item, bool) for item in (enabled, tested, review_required)):
            raise WorkflowError("自动化状态字段必须是布尔值")
        workflow = cls(
            workflow_id,
            name,
            trigger_prompt_id,
            tuple(WorkflowStep.from_mapping(item) for item in steps),
            enabled,
            tested,
            review_required,
        )
        workflow.validate()
        return workflow

    def duplicate(self) -> "LocalWorkflow":
        return replace(
            self,
            workflow_id=uuid.uuid4().hex,
            name=f"{self.name} 副本",
            enabled=False,
            tested=False,
        )


class WorkflowStore:
    VERSION = 1

    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            root = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            directory = Path(root) / "workflows"
        self._directory = directory

    def load(self, serial: str) -> tuple[LocalWorkflow, ...]:
        path = self.path_for(serial)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"无法读取自动化：{exc}") from exc
        if not isinstance(value, dict) or set(value) != {
            "version",
            "serial",
            "workflows",
        }:
            raise WorkflowError("自动化文件结构不受支持")
        if value.get("version") != self.VERSION:
            raise WorkflowError("自动化文件版本不受支持")
        if value.get("serial") != serial:
            raise WorkflowError("自动化与当前设备序列号不一致")
        items = value.get("workflows")
        if not isinstance(items, list):
            raise WorkflowError("workflows 必须是数组")
        workflows = tuple(LocalWorkflow.from_mapping(item) for item in items)
        _validate_workflows(workflows)
        return workflows

    def save(self, serial: str, workflows: tuple[LocalWorkflow, ...]) -> None:
        _validate_workflows(workflows)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "serial": serial,
            "workflows": [workflow.as_mapping() for workflow in workflows],
        }
        data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        output = QSaveFile(str(self.path_for(serial)))
        if not output.open(QSaveFile.OpenModeFlag.WriteOnly):
            raise WorkflowError(f"无法保存自动化：{output.errorString()}")
        if output.write(data) != len(data) or not output.commit():
            raise WorkflowError(f"无法保存自动化：{output.errorString()}")

    def path_for(self, serial: str) -> Path:
        safe_serial = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in serial
        )
        if not safe_serial:
            raise WorkflowError("设备序列号不能用于自动化路径")
        return self._directory / f"{safe_serial}.json"


def reference_markdown_workflow(
    *, target: str, trigger_prompt_id: int = 1
) -> LocalWorkflow:
    return LocalWorkflow(
        workflow_id=uuid.uuid4().hex,
        name="保存选中文字到 Markdown",
        trigger_prompt_id=trigger_prompt_id,
        steps=(
            WorkflowStep("capture_selection", {}),
            WorkflowStep(
                "append_text_file", {"path": target, "separator": "\n\n"}
            ),
            WorkflowStep("show_notification", {"message": "内容已保存"}),
        ),
    )


def _validate_workflows(workflows: tuple[LocalWorkflow, ...]) -> None:
    ids: set[str] = set()
    enabled_prompts: set[int] = set()
    for workflow in workflows:
        workflow.validate()
        if workflow.workflow_id in ids:
            raise WorkflowError(f"自动化 ID 重复：{workflow.workflow_id}")
        if workflow.enabled and workflow.trigger_prompt_id in enabled_prompts:
            raise WorkflowError(
                f"提示词槽位 {workflow.trigger_prompt_id} 已绑定其他内置自动化"
            )
        ids.add(workflow.workflow_id)
        if workflow.enabled:
            enabled_prompts.add(workflow.trigger_prompt_id)
