from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from controller_config.automation import LocalScriptAutomation
from controller_config.extensions.bindings import ExtensionActionBinding
from controller_config.extensions.manager import InstalledExtension
from controller_config.host_selection import HostActionError, HostServices, SystemHostServices
from controller_config.workflows import LocalWorkflow, WorkflowStep


class HostActionProvider(StrEnum):
    """Execution path used by one host-side action."""

    OFFICIAL = "official"
    LOCAL_SCRIPT = "local_script"
    EXTENSION = "extension"


@dataclass(frozen=True)
class OfficialActionDefinition:
    action_id: str
    name: str
    description: str
    parameter_label: str = ""


@dataclass(frozen=True)
class HostActionContext:
    text: str = ""


@dataclass(frozen=True)
class HostActionExecution:
    succeeded: bool
    message: str
    context: HostActionContext
    technical: str = ""


class HostActionRegistry:
    """Typed built-in actions executed directly by BORING Console."""

    DEFINITIONS = (
        OfficialActionDefinition(
            "capture_selection",
            "获取当前选中文字",
            "向前台应用发送复制快捷键，并把 Unicode 文字交给下一步。",
        ),
        OfficialActionDefinition(
            "read_clipboard",
            "读取剪贴板文字",
            "读取当前剪贴板中的 Unicode 文字，不发送键盘快捷键。",
        ),
        OfficialActionDefinition(
            "append_text_file",
            "追加到 Markdown / TXT",
            "把当前文字追加到本机文件，不覆盖已有内容。",
            "目标文件",
        ),
        OfficialActionDefinition(
            "open_target",
            "打开应用、文件或网址",
            "通过操作系统打开一个本机目标或 HTTP/HTTPS 网址。",
            "目标",
        ),
        OfficialActionDefinition(
            "show_notification",
            "显示完成通知",
            "通过操作系统显示一条简短的完成提示。",
            "通知内容",
        ),
    )

    def __init__(self, services: HostServices | None = None) -> None:
        self._services = services or SystemHostServices()

    @property
    def definitions(self) -> tuple[OfficialActionDefinition, ...]:
        return self.DEFINITIONS

    def definition(self, action_id: str) -> OfficialActionDefinition:
        for definition in self.DEFINITIONS:
            if definition.action_id == action_id:
                return definition
        raise HostActionError(f"不支持的内置步骤：{action_id}")

    def availability(self, action_id: str) -> tuple[bool, str]:
        self.definition(action_id)
        platform = self._services.platform
        if action_id == "capture_selection" and platform not in {"darwin", "win32"}:
            return False, "当前系统尚未提供全局复制适配"
        if action_id == "show_notification" and platform != "darwin":
            return False, "当前构建尚未提供系统通知适配"
        return True, "可用"

    def execute(
        self, step: WorkflowStep, context: HostActionContext
    ) -> HostActionExecution:
        step.validate()
        available, reason = self.availability(step.action_id)
        if not available:
            return HostActionExecution(False, reason, context, reason)
        try:
            if step.action_id == "capture_selection":
                text = self._services.capture_selection()
                return HostActionExecution(
                    True, "已获取当前选中文字", HostActionContext(text)
                )
            if step.action_id == "read_clipboard":
                text = self._services.read_clipboard()
                return HostActionExecution(
                    True, "已读取剪贴板文字", HostActionContext(text)
                )
            if step.action_id == "append_text_file":
                self._services.append_text_file(
                    str(step.parameters["path"]),
                    context.text,
                    str(step.parameters["separator"]),
                )
                return HostActionExecution(True, "文字已追加到文件", context)
            if step.action_id == "open_target":
                self._services.open_target(str(step.parameters["target"]))
                return HostActionExecution(True, "目标已交给操作系统打开", context)
            if step.action_id == "show_notification":
                self._services.show_notification(str(step.parameters["message"]))
                return HostActionExecution(True, "完成通知已显示", context)
        except HostActionError as exc:
            return HostActionExecution(False, str(exc), context, str(exc))
        raise HostActionError(f"不支持的内置步骤：{step.action_id}")


@dataclass(frozen=True)
class HostAction:
    """Console-wide description shared by action listings and bindings.

    The descriptor does not execute anything. AutomationHost and the private
    Extension Runner remain the owners of their respective execution paths.
    """

    action_key: str
    name: str
    provider: HostActionProvider
    provider_name: str
    enabled: bool
    available: bool
    prompt_ids: tuple[int, ...]
    ready_prompt_ids: tuple[int, ...] = ()
    availability_reason: str = ""

    @property
    def physically_bound(self) -> bool:
        return self.enabled and bool(self.ready_prompt_ids)


def collect_host_actions(
    *,
    device_serial: str,
    workflows: tuple[LocalWorkflow, ...] = (),
    automations: tuple[LocalScriptAutomation, ...],
    extensions: tuple[InstalledExtension, ...],
    extension_bindings: tuple[ExtensionActionBinding, ...],
    extension_is_ready: Callable[[str], bool],
    trigger_problem: Callable[[int], str],
) -> tuple[HostAction, ...]:
    """Build one read-only catalog from every supported action runtime."""

    actions = [
        HostAction(
            action_key=f"official:{workflow.workflow_id}",
            name=workflow.name,
            provider=HostActionProvider.OFFICIAL,
            provider_name="BORING 内置自动化",
            enabled=workflow.enabled,
            available=(
                workflow.enabled and workflow.tested and not workflow.review_required
            ),
            prompt_ids=(workflow.trigger_prompt_id,),
        )
        for workflow in workflows
    ]
    actions.extend(
        [
            HostAction(
                action_key=f"local-script:{definition.automation_id}",
                name=definition.name,
                provider=HostActionProvider.LOCAL_SCRIPT,
                provider_name="BORING 本地脚本",
                enabled=definition.enabled,
                available=(
                    definition.enabled and Path(definition.script_path).is_file()
                ),
                prompt_ids=(definition.trigger_prompt_id,),
            )
            for definition in automations
        ]
    )

    bindings_by_action: dict[tuple[str, str], list[int]] = {}
    for binding in extension_bindings:
        if binding.device_serial != device_serial:
            continue
        bindings_by_action.setdefault(
            (binding.extension_id, binding.action_id), []
        ).append(binding.prompt_id)

    for extension in extensions:
        manifest = extension.manifest
        ready = extension.enabled and extension_is_ready(manifest.extension_id)
        for declaration in manifest.actions:
            prompt_ids = tuple(
                sorted(
                    bindings_by_action.get(
                        (manifest.extension_id, declaration.action_id), ()
                    )
                )
            )
            actions.append(
                HostAction(
                    action_key=(
                        f"extension:{manifest.extension_id}:{declaration.action_id}"
                    ),
                    name=declaration.name,
                    provider=HostActionProvider.EXTENSION,
                    provider_name=manifest.name,
                    enabled=extension.enabled,
                    available=ready,
                    prompt_ids=prompt_ids,
                )
            )

    for index, action in enumerate(actions):
        problems = {prompt_id: trigger_problem(prompt_id) for prompt_id in action.prompt_ids}
        ready_ids = tuple(prompt_id for prompt_id, problem in problems.items() if not problem)
        reason = next((problem for problem in problems.values() if problem), "")
        actions[index] = replace(
            action,
            available=action.available and (not action.prompt_ids or bool(ready_ids)),
            ready_prompt_ids=ready_ids,
            availability_reason=reason,
        )

    provider_order = {
        HostActionProvider.OFFICIAL: 0,
        HostActionProvider.LOCAL_SCRIPT: 1,
        HostActionProvider.EXTENSION: 2,
    }
    return tuple(
        sorted(
            actions,
            key=lambda item: (provider_order[item.provider], item.name.lower()),
        )
    )
