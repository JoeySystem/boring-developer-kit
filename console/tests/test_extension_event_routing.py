from __future__ import annotations

from controller_config.automation import DeviceEvent, EventDispatchResult
from controller_config.extensions.action_router import (
    BoundExtensionAction,
    ExtensionActionCoordinator,
)
from controller_config.extensions.bindings import ExtensionActionBinding
from controller_config.extensions.contracts import (
    ActionInvocationResult,
    ExtensionManifest,
)


EXTENSION_ID = "com.example.claim"
SERIAL = "CP01-AABBCCDDEEFF"


class FakeInvoker:
    def __init__(self, launch=True) -> None:
        self.launch = launch
        self.invocations = []
        self.completed = None
        self.cancelled = []

    def invoke_action(self, invocation, completed) -> bool:
        self.invocations.append(invocation)
        self.completed = completed
        return self.launch

    def cancel_invocation(self, invocation_id: str) -> None:
        self.cancelled.append(invocation_id)


def _manifest(extension_id: str = EXTENSION_ID) -> ExtensionManifest:
    return ExtensionManifest.from_mapping(
        {
            "manifest_version": 1,
            "id": extension_id,
            "name": "Claim prompt",
            "version": "1.0.0",
            "api_version": {"major": 1, "minor": 0},
            "entrypoint": "main.py",
            "observer_events": [],
            "actions": [{"id": "run", "name": "Run"}],
        }
    )


def _event(prompt_id: int = 2) -> DeviceEvent:
    body = "你好"
    return DeviceEvent(
        "prompt.triggered",
        "usb.prompt",
        SERIAL,
        9,
        {
            "prompt_id": prompt_id,
            "prompt_name": "Prompt",
            "prompt_body": body,
            "body_bytes": len(body.encode("utf-8")),
        },
    )


def _coordinator(qtbot, invoker, *, ready=True, bound=True, claim_ms=40):
    binding = BoundExtensionAction(
        ExtensionActionBinding(SERIAL, 2, EXTENSION_ID, "run"), _manifest()
    )
    fallbacks = []
    coordinator = ExtensionActionCoordinator(
        invoker,
        binding_provider=lambda _serial, _prompt: binding if bound else None,
        extension_is_ready=lambda _extension_id: ready,
        context_revision=lambda: 4,
        fallback=lambda event: (
            fallbacks.append(event)
            or EventDispatchResult(True, True, "pasted")
        ),
        maximum_claim_ms=claim_ms,
    )
    return coordinator, fallbacks


def test_accepted_extension_action_suppresses_paste(qtbot) -> None:
    invoker = FakeInvoker()
    coordinator, fallbacks = _coordinator(qtbot, invoker)
    results = []

    coordinator.dispatch(_event(), 500, results.append)
    invoker.completed(
        ActionInvocationResult(
            invoker.invocations[0].invocation_id, "accepted", "accepted"
        )
    )

    assert results[0].succeeded
    assert fallbacks == []


def test_rejected_or_unavailable_action_falls_back_once(qtbot) -> None:
    invoker = FakeInvoker()
    coordinator, fallbacks = _coordinator(qtbot, invoker)
    results = []
    coordinator.dispatch(_event(), 500, results.append)

    invoker.completed(
        ActionInvocationResult(
            invoker.invocations[0].invocation_id, "rejected", "no"
        )
    )
    invoker.completed(
        ActionInvocationResult(
            invoker.invocations[0].invocation_id, "accepted", "late"
        )
    )

    assert len(fallbacks) == 1
    assert len(results) == 1


def test_action_timeout_falls_back_and_cancels_late_result(qtbot) -> None:
    invoker = FakeInvoker()
    coordinator, fallbacks = _coordinator(qtbot, invoker, claim_ms=10)
    results = []
    coordinator.dispatch(_event(), 500, results.append)

    qtbot.waitUntil(lambda: len(results) == 1)

    assert len(fallbacks) == 1
    assert len(invoker.cancelled) == 1


def test_no_binding_or_session_uses_default_without_invocation(qtbot) -> None:
    invoker = FakeInvoker()
    coordinator, fallbacks = _coordinator(qtbot, invoker, bound=False)
    results = []

    coordinator.dispatch(_event(), 500, results.append)

    assert len(fallbacks) == 1
    assert invoker.invocations == []


def test_disabling_one_extension_only_cancels_its_pending_action(qtbot) -> None:
    second_extension = "com.example.second"
    invoker = FakeInvoker()
    bindings = {
        2: BoundExtensionAction(
            ExtensionActionBinding(SERIAL, 2, EXTENSION_ID, "run"),
            _manifest(),
        ),
        3: BoundExtensionAction(
            ExtensionActionBinding(SERIAL, 3, second_extension, "run"),
            _manifest(second_extension),
        ),
    }
    coordinator = ExtensionActionCoordinator(
        invoker,
        binding_provider=lambda _serial, prompt_id: bindings.get(prompt_id),
        extension_is_ready=lambda _extension_id: True,
        context_revision=lambda: 4,
        fallback=lambda _event: EventDispatchResult(True, True, "pasted"),
        maximum_claim_ms=500,
    )
    first_results = []
    second_results = []
    coordinator.dispatch(_event(2), 500, first_results.append)
    coordinator.dispatch(_event(3), 500, second_results.append)

    coordinator.cancel_extension(EXTENSION_ID, "first disabled")

    assert len(first_results) == 1
    assert first_results[0].message == "first disabled"
    assert second_results == []
    assert coordinator.pending_count == 1
    assert invoker.cancelled == [invoker.invocations[0].invocation_id]
    coordinator.cancel_all()
