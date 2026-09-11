from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.automation import EventDispatchResult
from controller_config.device_events import DeviceEvent
from controller_config.extensions.bindings import ExtensionActionBinding
from controller_config.extensions.contracts import (
    ActionInvocation,
    ActionInvocationResult,
    ExtensionManifest,
    SemanticEvent,
)


@dataclass(frozen=True)
class BoundExtensionAction:
    binding: ExtensionActionBinding
    manifest: ExtensionManifest


class ActionInvoker(Protocol):
    def invoke_action(
        self,
        invocation: ActionInvocation,
        completed: Callable[[ActionInvocationResult], None],
    ) -> bool: ...

    def cancel_invocation(self, invocation_id: str) -> None: ...


@dataclass
class _PendingInvocation:
    invocation_id: str
    extension_id: str
    timer: QTimer
    completed: Callable[[EventDispatchResult], None]
    event: DeviceEvent


class ExtensionActionCoordinator(QObject):
    """Asynchronously arbitrate one explicitly bound extension action."""

    log_added = Signal(str, str, str)

    def __init__(
        self,
        invoker: ActionInvoker,
        *,
        binding_provider: Callable[[str, int], BoundExtensionAction | None],
        extension_is_ready: Callable[[str], bool],
        context_revision: Callable[[], int],
        fallback: Callable[[DeviceEvent], EventDispatchResult],
        maximum_claim_ms: int = 700,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._invoker = invoker
        self._binding_provider = binding_provider
        self._extension_is_ready = extension_is_ready
        self._context_revision = context_revision
        self._fallback = fallback
        self._maximum_claim_ms = maximum_claim_ms
        self._pending: dict[str, _PendingInvocation] = {}

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def dispatch(
        self,
        event: DeviceEvent,
        remaining_budget_ms: int,
        completed: Callable[[EventDispatchResult], None],
    ) -> None:
        prompt_id = event.payload.get("prompt_id")
        if not isinstance(prompt_id, int) or isinstance(prompt_id, bool):
            completed(self._fallback(event))
            return
        bound = self._binding_provider(event.device_serial, prompt_id)
        if bound is None or not self._extension_is_ready(
            bound.binding.extension_id
        ):
            completed(self._fallback(event))
            return
        timeout_ms = min(self._maximum_claim_ms, max(0, remaining_budget_ms))
        if timeout_ms <= 0:
            self.log_added.emit(
                "warning",
                "扩展 action 没有留下可用响应时间，已回退默认动作",
                bound.binding.extension_id,
            )
            completed(self._fallback(event))
            return
        invocation_id = uuid.uuid4().hex
        invocation = ActionInvocation.from_mapping(
            {
                "schema_version": 1,
                "invocation_id": invocation_id,
                "extension_id": bound.binding.extension_id,
                "action_id": bound.binding.action_id,
                "device_serial": event.device_serial,
                "context_revision": self._context_revision(),
                "event": event.as_mapping(),
            },
            manifest=bound.manifest,
        )
        timer = QTimer(self)
        timer.setSingleShot(True)
        pending = _PendingInvocation(
            invocation_id,
            bound.binding.extension_id,
            timer,
            completed,
            event,
        )
        self._pending[invocation_id] = pending
        timer.timeout.connect(lambda: self._timeout(invocation_id))
        launched = self._invoker.invoke_action(
            invocation,
            lambda result: self._result(invocation_id, result),
        )
        if not launched:
            self._pending.pop(invocation_id, None)
            timer.deleteLater()
            completed(self._fallback(event))
            return
        self.log_added.emit(
            "info",
            f"已把实体事件交给扩展 action：{bound.binding.action_id}",
            bound.binding.extension_id,
        )
        timer.start(timeout_ms)

    def cancel_all(self, message: str = "扩展 action 仲裁已取消") -> None:
        for invocation_id, pending in tuple(self._pending.items()):
            self._cancel_pending(invocation_id, pending, message)

    def cancel_extension(
        self, extension_id: str, message: str = "扩展 action 仲裁已取消"
    ) -> None:
        for invocation_id, pending in tuple(self._pending.items()):
            if pending.extension_id == extension_id:
                self._cancel_pending(invocation_id, pending, message)

    def _result(
        self, invocation_id: str, result: ActionInvocationResult
    ) -> None:
        pending = self._pending.pop(invocation_id, None)
        if pending is None:
            return
        pending.timer.stop()
        pending.timer.deleteLater()
        if result.status == "accepted":
            self.log_added.emit("success", result.message, pending.extension_id)
            pending.completed(
                EventDispatchResult(
                    True,
                    True,
                    result.message or "扩展已接受实体动作",
                )
            )
            return
        self._invoker.cancel_invocation(invocation_id)
        self.log_added.emit("warning", result.message, pending.extension_id)
        pending.completed(self._fallback(pending.event))

    def _timeout(self, invocation_id: str) -> None:
        pending = self._pending.pop(invocation_id, None)
        if pending is None:
            return
        self._invoker.cancel_invocation(invocation_id)
        pending.timer.deleteLater()
        self.log_added.emit(
            "warning",
            "扩展未在时限内接受 action，已回退默认动作",
            pending.extension_id,
        )
        pending.completed(self._fallback(pending.event))

    def _cancel_pending(
        self,
        invocation_id: str,
        pending: _PendingInvocation,
        message: str,
    ) -> None:
        self._pending.pop(invocation_id, None)
        pending.timer.stop()
        pending.timer.deleteLater()
        self._invoker.cancel_invocation(invocation_id)
        pending.completed(EventDispatchResult(True, False, message))
