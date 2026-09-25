from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

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


class _BackgroundCall(QRunnable):
    def __init__(self, operation: Callable[[], object], result: Signal) -> None:
        super().__init__()
        self.operation = operation
        self.result = result

    def run(self) -> None:
        try:
            value = self.operation()
        except Exception as exc:
            value = exc
        self.result.emit(value)


class HostActionJob(QObject):
    """One step; only file I/O and OS commands leave the GUI thread."""

    _worker_result = Signal(object)

    def __init__(
        self,
        registry: HostActionRegistry,
        step: WorkflowStep,
        context: HostActionContext,
        completed: Callable[[HostActionExecution], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.step = step
        self.context = context
        self.completed = completed
        self._working = False
        self._cancelled = False
        self._finished = False
        self._after_worker = None
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._poll_selection)
        self._worker_result.connect(self._worker_finished)
        QTimer.singleShot(0, self, self._start)

    def cancel(self) -> None:
        self._cancelled = True
        self._poll.stop()
        # A started file write cannot be safely interrupted. Keep its owner busy
        # until the result arrives; no following step is allowed to start.
        if not self._working:
            self._finish(HostActionExecution(False, "已停止", self.context))

    def _background(self, operation, after=None) -> None:
        self._working = True
        self._after_worker = after
        QThreadPool.globalInstance().start(
            _BackgroundCall(operation, self._worker_result)
        )

    @Slot(object)
    def _worker_finished(self, value) -> None:
        self._working = False
        if self._cancelled:
            self._finish(
                HostActionExecution(False, "已停止；已开始的操作可能已完成", self.context)
            )
        elif isinstance(value, Exception):
            self._fail(value)
        elif self._after_worker is not None:
            self._after_worker(value)
        else:
            self._finish(value)

    def _start(self) -> None:
        if self._finished:
            return
        try:
            self.step.validate()
            available, reason = self.registry.availability(self.step.action_id)
            if not available:
                raise HostActionError(reason)
            services = self.registry._services
            if self.step.action_id == "capture_selection" and isinstance(
                services, SystemHostServices
            ):
                self._before = services.selection_sequence()
                self._attempts = 0
                self._background(services.send_copy_shortcut, self._copy_sent)
            elif self.step.action_id == "open_target" and isinstance(
                services, SystemHostServices
            ):
                target = str(self.step.parameters["target"])
                self._background(
                    lambda: services.prepare_open_target(target), self._open_prepared
                )
            elif self.step.action_id in {
                "append_text_file", "show_notification", "capture_selection"
            }:
                self._background(lambda: self.registry.execute(self.step, self.context))
            else:
                # QClipboard and QDesktopServices must stay on the GUI thread.
                self._finish(self.registry.execute(self.step, self.context))
        except Exception as exc:
            self._fail(exc)

    def _copy_sent(self, _value) -> None:
        self._poll.start(120 if self._before is None else 50)

    def _open_prepared(self, url) -> None:
        try:
            self.registry._services.open_prepared_target(
                url, str(self.step.parameters["target"])
            )
        except Exception as exc:
            self._fail(exc)
            return
        self._finish(HostActionExecution(True, "目标已交给操作系统打开", self.context))

    def _poll_selection(self) -> None:
        try:
            services = self.registry._services
            if self._before is None or services.selection_sequence() != self._before:
                self._finish(
                    HostActionExecution(
                        True, "已获取当前选中文字", HostActionContext(services.read_clipboard())
                    )
                )
                return
            self._attempts += 1
            if self._attempts >= 8:
                raise HostActionError("未检测到新的选中文字。请确认文字选区和辅助功能权限。")
            self._poll.start(50)
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc: Exception) -> None:
        self._finish(HostActionExecution(False, str(exc), self.context, str(exc)))

    def _finish(self, result: HostActionExecution) -> None:
        if self._finished:
            return
        self._finished = True
        self._poll.stop()
        try:
            self.completed(result)
        finally:
            self.deleteLater()


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

    def execute_async(
        self,
        step: WorkflowStep,
        context: HostActionContext,
        completed: Callable[[HostActionExecution], None],
        *,
        parent: QObject,
    ) -> HostActionJob:
        return HostActionJob(self, step, context, completed, parent)


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
            prompt_ids=(workflow.trigger_prompt_id,) if workflow.trigger_prompt_id is not None else (),
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
                prompt_ids=(definition.trigger_prompt_id,) if definition.trigger_prompt_id is not None else (),
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
