from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Callable

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from controller_config.automation import (
    EventDispatchResult,
    PromptPasteEventHandler,
)
from controller_config.device_events import DeviceEvent
from controller_config.extensions.action_router import (
    BoundExtensionAction,
    ExtensionActionCoordinator,
)
from controller_config.extensions.bindings import (
    ExtensionActionBinding,
    ExtensionBindingError,
    ExtensionBindingStore,
)
from controller_config.extensions.contracts import (
    ActionInvocationResult,
    SemanticEvent,
    SetMappingProposal,
)
from controller_config.extensions.local_api import ExtensionLocalApiServer
from controller_config.extensions.manager import (
    ExtensionManager,
    ExtensionManagerError,
    InstalledExtension,
)
from controller_config.extensions.proposals import ExtensionProposalCoordinator
from controller_config.extensions.runtime import (
    ExtensionRuntime,
    ExtensionRuntimeState,
    shutdown_extension_runtimes,
)
from controller_config.prompt_helper import PromptHelperRuntime
from controller_config.protocol.contract import Contract

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


DEFAULT_EXTENSION_API_SERVER = "com.boring.controller-config.community.extension-api-v1"


@dataclass(frozen=True)
class ExtensionPlatformLog:
    timestamp: str
    level: str
    message: str
    extension_id: str = ""


class ExtensionPlatformController(QObject):
    """Application-level owner of extension packages, API and runtimes."""

    changed = Signal()
    log_added = Signal(object)

    def __init__(
        self,
        view_model: MainViewModel,
        contract: Contract,
        *,
        prompt_helper: PromptHelperRuntime | None,
        manager: ExtensionManager | None = None,
        binding_store: ExtensionBindingStore | None = None,
        server_name: str = DEFAULT_EXTENSION_API_SERVER,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._contract = contract
        self._manager = manager or ExtensionManager()
        self._binding_store = binding_store or ExtensionBindingStore(
            self._manager.managed_root / "bindings.json"
        )
        self._bindings = self._binding_store.load()
        self._runtimes: dict[str, ExtensionRuntime] = {}
        self._logs: deque[ExtensionPlatformLog] = deque(maxlen=200)
        self._started = False
        self._api = ExtensionLocalApiServer(
            server_name,
            contract=contract,
            authorize_extension=self._authorize_extension,
            allowed_observer_events=self._observer_events_for_extension,
            context_provider=view_model.extension_context,
            proposal_handler=self._submit_proposal,
            parent=self,
        )
        self._proposals = ExtensionProposalCoordinator(
            view_model,
            contract=contract,
            extension_is_active=self.extension_is_ready,
            extension_name=self.extension_name,
            parent=self,
        )
        paste_handler = (
            PromptPasteEventHandler(prompt_helper)
            if prompt_helper is not None
            else None
        )

        def fallback(event: DeviceEvent) -> EventDispatchResult:
            if paste_handler is None:
                return EventDispatchResult(
                    False,
                    False,
                    "当前系统没有可用的 Unicode 粘贴助手",
                )
            result = paste_handler(event)
            return result or EventDispatchResult(False, False, "")

        self._action_coordinator = ExtensionActionCoordinator(
            self._api,
            binding_provider=self._bound_action,
            extension_is_ready=self.extension_is_ready,
            context_revision=lambda: view_model.extension_context_revision,
            fallback=fallback,
            parent=self,
        )
        self._action_coordinator.log_added.connect(self._append_log)
        self._proposals.changed.connect(self.changed)
        self._api.action_result_received.connect(view_model.host_tasks.extension_result)
        self._api.session_opened.connect(
            lambda extension_id: self._append_log(
                "success", "扩展已连接本地 API", extension_id
            )
        )
        self._api.session_closed.connect(
            lambda extension_id: self._append_log(
                "warning", "扩展已断开本地 API", extension_id
            )
        )
        self._api.session_error.connect(
            lambda extension_id, message: self._append_log(
                "error", message, extension_id
            )
        )
        self._api.action_result_received.connect(self._record_action_result)
        view_model.event_bus.event_received.connect(self._broadcast_event)
        view_model.extension_context_changed.connect(
            lambda _revision: self._proposals.reconcile()
        )

    @property
    def manager(self) -> ExtensionManager:
        return self._manager

    @property
    def api(self) -> ExtensionLocalApiServer:
        return self._api

    @property
    def proposals(self) -> ExtensionProposalCoordinator:
        return self._proposals

    @property
    def extensions(self) -> tuple[InstalledExtension, ...]:
        return self._manager.extensions

    @property
    def bindings(self) -> tuple[ExtensionActionBinding, ...]:
        return self._bindings

    @property
    def logs(self) -> tuple[ExtensionPlatformLog, ...]:
        return tuple(self._logs)

    @property
    def is_started(self) -> bool:
        return self._started

    def runtime_state(self, extension_id: str) -> ExtensionRuntimeState:
        runtime = self._runtimes.get(extension_id)
        if runtime is not None:
            return runtime.state
        extension = self._manager.get(extension_id)
        return (
            ExtensionRuntimeState.STOPPED
            if extension.enabled
            else ExtensionRuntimeState.INSTALLED_DISABLED
        )

    def start(self) -> None:
        if self._started:
            return
        self._api.start()
        self._started = True
        self._view_model.prompt_device.set_async_event_dispatcher(
            self._action_coordinator.dispatch
        )
        for extension in self._manager.extensions:
            runtime = self._ensure_runtime(extension)
            if extension.enabled:
                runtime.start()
        self.changed.emit()

    def import_package(self, source: Path) -> InstalledExtension:
        extension = self._manager.import_package(source)
        self._ensure_runtime(extension)
        self._append_log(
            "success",
            f"已导入扩展 {extension.manifest.name} {extension.manifest.version}",
            extension.manifest.extension_id,
        )
        return extension

    def enable(self, extension_id: str) -> None:
        extension = self._manager.enable(extension_id)
        runtime = self._ensure_runtime(extension)
        runtime.set_enabled(True)
        if self._started:
            runtime.start()
        self.changed.emit()

    def disable(self, extension_id: str) -> None:
        extension = self._manager.disable(extension_id)
        runtime = self._ensure_runtime(extension)
        runtime.set_enabled(False)
        self._action_coordinator.cancel_extension(
            extension_id, "扩展已停用，action 已撤销"
        )
        self.changed.emit()

    def disable_async(self, extension_id: str, completed: Callable[[], None]) -> None:
        """Disable all functions of a package; finish only after its runner exits."""
        self._manager.disable(extension_id)
        runtime = self._runtimes.get(extension_id)
        process = runtime.process if runtime is not None else None
        self._action_coordinator.cancel_extension(extension_id, "扩展正在停用")
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            if runtime is not None:
                runtime.set_enabled(False)
            self.changed.emit()
            completed()
            return

        timer = QTimer(self)
        timer.setSingleShot(True)
        def finished(*_args) -> None:
            timer.stop()
            timer.deleteLater()
            process.finished.disconnect(finished)
            runtime.set_enabled(False)
            self.changed.emit()
            completed()
        def force_stop() -> None:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        process.finished.connect(finished)
        timer.timeout.connect(force_stop)
        process.terminate()
        timer.start(500)
        self.changed.emit()

    def restart(self, extension_id: str) -> bool:
        if not self._started:
            return False
        runtime = self._ensure_runtime(self._manager.get(extension_id))
        result = runtime.restart()
        self.changed.emit()
        return result

    def remove(self, extension_id: str) -> None:
        previous_bindings = self._bindings
        candidate = tuple(
            item for item in previous_bindings if item.extension_id != extension_id
        )
        self._binding_store.save(candidate)
        try:
            self._manager.remove(extension_id)
        except (ExtensionManagerError, OSError) as exc:
            self._restore_bindings(previous_bindings, exc)
            raise
        self._bindings = candidate
        runtime = self._runtimes.pop(extension_id, None)
        if runtime is not None:
            runtime.set_enabled(False)
        self._action_coordinator.cancel_extension(
            extension_id, "扩展已移除，action 已撤销"
        )
        self._append_log("info", "已移除扩展", extension_id)
        self.changed.emit()

    def bind_action(
        self,
        *,
        device_serial: str,
        prompt_id: int,
        extension_id: str,
        action_id: str,
    ) -> None:
        extension = self._manager.get(extension_id)
        if not extension.enabled:
            raise ValueError("请先启用扩展")
        if action_id not in extension.manifest.declared_action_ids:
            raise ValueError(f"action {action_id} 未在扩展 manifest 中声明")
        if problem := self._view_model.prompt_trigger_problem(prompt_id, device_serial):
            raise ValueError(problem)
        if any(
            workflow.enabled and workflow.trigger_prompt_id == prompt_id
            for workflow in self._view_model.workflow_host.workflows
            if self._view_model.workflow_host.serial == device_serial
        ):
            raise ValueError("这个提示词槽位已绑定内置自动化")
        if any(
            item.enabled and item.trigger_prompt_id == prompt_id
            for item in self._view_model.automation_host.definitions
            if self._view_model.automation_host.serial == device_serial
        ):
            raise ValueError("这个提示词槽位已绑定本地脚本")
        binding = ExtensionActionBinding(
            device_serial, prompt_id, extension_id, action_id
        )
        binding.validate()
        candidate = (
            *(
                item
                for item in self._bindings
                if (item.device_serial, item.prompt_id)
                != (device_serial, prompt_id)
            ),
            binding,
        )
        self._binding_store.save(candidate)
        self._bindings = candidate
        self._append_log(
            "success",
            f"已绑定提示词槽位 {prompt_id} → {action_id}",
            extension_id,
        )
        self.changed.emit()

    def unbind_action(self, device_serial: str, prompt_id: int) -> None:
        candidate = tuple(
            item
            for item in self._bindings
            if (item.device_serial, item.prompt_id) != (device_serial, prompt_id)
        )
        if candidate == self._bindings:
            return
        self._binding_store.save(candidate)
        self._bindings = candidate
        self.changed.emit()

    def approve_proposal(self, proposal_id: str) -> None:
        self._proposals.approve(proposal_id)

    def reject_proposal(self, proposal_id: str) -> None:
        self._proposals.reject(proposal_id)

    def remove_proposal_record(self, proposal_id: str) -> None:
        self._proposals.remove_record(proposal_id)

    def extension_is_ready(self, extension_id: str) -> bool:
        runtime = self._runtimes.get(extension_id)
        return bool(
            runtime is not None
            and runtime.state is ExtensionRuntimeState.RUNNING
            and self._api.has_session(extension_id)
            and self._authorize_extension(extension_id)
        )

    def extension_name(self, extension_id: str) -> str:
        try:
            return self._manager.get(extension_id).manifest.name
        except ValueError:
            return extension_id

    def stop_accepting(self) -> None:
        self._api.stop_accepting()

    def shutdown(self) -> None:
        self.suspend("控制台正在退出")

    def suspend(self, reason: str = "设备会话已结束") -> None:
        if not self._started:
            return
        self._started = False
        self._api.stop_accepting()
        self._action_coordinator.cancel_all(reason)
        shutdown_extension_runtimes(self._runtimes.values())
        self._api.close()
        self._view_model.prompt_device.set_async_event_dispatcher(None)
        self.changed.emit()

    def _submit_proposal(self, proposal: SetMappingProposal):
        return self._proposals.submit(proposal)

    def _authorize_extension(self, extension_id: str) -> bool:
        try:
            return self._manager.get(extension_id).enabled
        except ValueError:
            return False

    def _observer_events_for_extension(self, extension_id: str) -> tuple[str, ...]:
        try:
            return self._manager.get(extension_id).manifest.observer_events
        except ValueError:
            return ()

    def _restore_bindings(
        self,
        previous_bindings: tuple[ExtensionActionBinding, ...],
        original_error: Exception,
    ) -> None:
        try:
            self._binding_store.save(previous_bindings)
        except (ExtensionBindingError, OSError) as rollback_error:
            raise ExtensionBindingError(
                "扩展状态未更改，但 action 绑定回滚失败："
                f"{rollback_error}"
            ) from original_error

    def _ensure_runtime(self, extension: InstalledExtension) -> ExtensionRuntime:
        extension_id = extension.manifest.extension_id
        runtime = self._runtimes.get(extension_id)
        if runtime is not None:
            runtime.set_enabled(extension.enabled)
            return runtime
        runtime = ExtensionRuntime(
            extension.manifest,
            extension.install_path,
            api_server_name=self._api.server_name,
            enabled=extension.enabled,
            parent=self,
        )
        runtime.state_changed.connect(self._runtime_state_changed)
        runtime.standard_output.connect(
            lambda text, identity=extension_id: self._append_log(
                "info", text.strip(), identity
            )
        )
        runtime.standard_error.connect(
            lambda text, identity=extension_id: self._append_log(
                "error", text.strip(), identity
            )
        )
        self._runtimes[extension_id] = runtime
        return runtime

    def _runtime_state_changed(self, record) -> None:
        runtime = self._runtimes.get(record.extension_id)
        process = runtime.process if runtime is not None else None
        if (record.state in {ExtensionRuntimeState.STOPPED, ExtensionRuntimeState.FAILED,
                             ExtensionRuntimeState.INSTALLED_DISABLED}
                and process is not None and process.state() == QProcess.ProcessState.NotRunning):
            self._view_model.host_tasks.extension_stopped(record.extension_id)
        self._proposals.reconcile()
        self.changed.emit()

    def _bound_action(
        self, device_serial: str, prompt_id: int
    ) -> BoundExtensionAction | None:
        binding = next(
            (
                item
                for item in self._bindings
                if item.device_serial == device_serial
                and item.prompt_id == prompt_id
            ),
            None,
        )
        if binding is None:
            return None
        try:
            extension = self._manager.get(binding.extension_id)
        except ValueError:
            return None
        return BoundExtensionAction(binding, extension.manifest)

    def _broadcast_event(self, event: DeviceEvent) -> None:
        if event.kind not in {"prompt.triggered", "host_action.triggered"} or not isinstance(event.event_id, int):
            return
        try:
            self._api.broadcast_event(
                SemanticEvent.from_mapping(
                    event.as_mapping()
                )
            )
        except ValueError as exc:
            self._append_log("error", f"无法广播设备语义事件：{exc}")

    def _record_action_result(
        self, extension_id: str, result: ActionInvocationResult
    ) -> None:
        if result.status == "completed":
            self._append_log(
                "success", result.message or "扩展 action 已完成", extension_id
            )
        elif result.status == "failed":
            self._append_log(
                "error", result.message or "扩展 action 执行失败", extension_id
            )

    def _append_log(
        self, level: str, message: str, extension_id: str = ""
    ) -> None:
        if not message:
            return
        self._logs.append(
            record := ExtensionPlatformLog(
                datetime.now().astimezone().isoformat(timespec="seconds"),
                level,
                message,
                extension_id,
            )
        )
        self.log_added.emit(record)
