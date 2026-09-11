from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from controller_config.automation import AutomationError, DeviceEvent
from controller_config.extensions.bindings import (
    ExtensionBindingError,
    ExtensionBindingStore,
)
from controller_config.extensions.manager import ExtensionManager, ExtensionManagerError
from controller_config.extensions.platform import ExtensionPlatformController
from controller_config.extensions.runtime import ExtensionRuntimeState
from controller_config.extensions.registry import ExtensionRegistryError
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.prompt_library import PromptEntry, PromptLibraryStore
from controller_config.viewmodels.main import MainViewModel
from controller_config.workflows import LocalWorkflow, WorkflowStep, WorkflowStore
from test_viewmodel import FakeGateway


EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "extensions"


def _platform(qtbot, contract, tmp_path):
    gateway = FakeGateway()
    view_model = MainViewModel(
        gateway,
        contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    view_model.prompt_device._read_entries = [PromptEntry(2, "Trigger", "Test trigger")]
    view_model.prompt_device._finish_full_read()
    manager = ExtensionManager(tmp_path / "extensions")
    platform = ExtensionPlatformController(
        view_model,
        contract,
        prompt_helper=None,
        manager=manager,
        binding_store=ExtensionBindingStore(tmp_path / "bindings.json"),
        server_name=f"boring-platform-test-{uuid.uuid4().hex}",
    )
    view_model.attach_extension_platform(platform)
    platform.start()
    return gateway, view_model, platform


def _event(prompt_id=2) -> DeviceEvent:
    body = "你好 BORING"
    return DeviceEvent(
        "prompt.triggered",
        "usb.prompt",
        "CP01-AABBCCDDEEFF",
        7,
        {
            "prompt_id": prompt_id,
            "prompt_name": "Prompt",
            "prompt_body": body,
            "body_bytes": len(body.encode("utf-8")),
        },
    )


def test_enabled_extension_waits_for_an_accepted_device_session(
    qtbot, contract, tmp_path
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(
        gateway,
        contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
    )
    platform = ExtensionPlatformController(
        view_model,
        contract,
        prompt_helper=None,
        manager=ExtensionManager(tmp_path / "extensions"),
        binding_store=ExtensionBindingStore(tmp_path / "bindings.json"),
        server_name=f"boring-platform-test-{uuid.uuid4().hex}",
    )
    view_model.attach_extension_platform(platform)
    try:
        extension = platform.import_package(EXAMPLES / "observe_prompt")
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)

        assert not platform.is_started
        assert platform.runtime_state(extension_id) is ExtensionRuntimeState.STOPPED

        gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5_000)
        assert platform.is_started

        gateway.disconnected.emit("port gone")
        assert not platform.is_started
        assert platform.runtime_state(extension_id) is ExtensionRuntimeState.STOPPED
    finally:
        view_model.shutdown()


def test_extension_binding_cannot_replace_enabled_official_action(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        view_model.save_workflow(
            LocalWorkflow(
                "official",
                "官方通知",
                2,
                (WorkflowStep("show_notification", {"message": "完成"}),),
                enabled=True,
                tested=True,
            )
        )
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        platform.enable(extension.manifest.extension_id)
        qtbot.waitUntil(
            lambda: platform.extension_is_ready(extension.manifest.extension_id),
            timeout=5_000,
        )

        with pytest.raises(ValueError, match="内置自动化"):
            platform.bind_action(
                device_serial="CP01-AABBCCDDEEFF",
                prompt_id=2,
                extension_id=extension.manifest.extension_id,
                action_id="use_prompt",
            )
    finally:
        view_model.shutdown()


def test_import_enable_observe_and_disable_extension(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "observe_prompt")
        extension_id = extension.manifest.extension_id
        assert platform.runtime_state(extension_id) is ExtensionRuntimeState.INSTALLED_DISABLED

        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.api.has_session(extension_id), timeout=5_000)
        assert platform.extension_is_ready(extension_id)
        # A connected API session is not yet an event subscription.
        qtbot.waitUntil(
            lambda: any(
                session.extension_id == extension_id
                and "prompt.triggered" in session.subscribed_events
                for session in platform.api._sessions.values()
            ),
            timeout=5_000,
        )

        view_model.event_bus.dispatch(_event())
        qtbot.waitUntil(
            lambda: any("你好 BORING" in item.message for item in platform.logs),
            timeout=5_000,
        )

        platform.disable(extension_id)
        assert platform.runtime_state(extension_id) is ExtensionRuntimeState.INSTALLED_DISABLED
    finally:
        view_model.shutdown()


def test_extension_binding_requires_nonempty_confirmed_device_prompt(qtbot, contract, tmp_path):
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.api.has_session(extension_id), timeout=5_000)
        view_model.prompt_device._read_entries = []
        view_model.prompt_device._finish_full_read()
        with pytest.raises(ValueError, match="尚未写入设备"):
            platform.bind_action(
                device_serial="CP01-AABBCCDDEEFF", prompt_id=2,
                extension_id=extension_id, action_id="use_prompt",
            )
        assert platform.bindings == ()
    finally:
        view_model.shutdown()


def test_bound_extension_action_accepts_then_disabled_extension_falls_back(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.api.has_session(extension_id), timeout=5_000)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )
        results = []

        platform._action_coordinator.dispatch(_event(), 1_000, results.append)
        qtbot.waitUntil(lambda: bool(results), timeout=3_000)

        assert results[0].handled and results[0].succeeded
        platform.disable(extension_id)
        assert len(platform.bindings) == 1
        fallback = []
        platform._action_coordinator.dispatch(_event(), 1_000, fallback.append)
        assert len(fallback) == 1
        assert not fallback[0].handled

        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5_000)
        resumed = []
        platform._action_coordinator.dispatch(_event(), 1_000, resumed.append)
        qtbot.waitUntil(lambda: bool(resumed), timeout=3_000)
        assert resumed[0].handled and resumed[0].succeeded
    finally:
        view_model.shutdown()


def test_enabled_automation_cannot_shadow_existing_extension_binding(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    script = tmp_path / "automation.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.manager.enable(extension_id)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )

        with pytest.raises(AutomationError, match="已绑定扩展"):
            view_model.save_automation(
                automation_id=None,
                name="Conflicting automation",
                trigger_prompt_id=2,
                script_path=str(script),
                enabled=True,
            )

        assert view_model.automation_host.definitions == ()
    finally:
        view_model.shutdown()


def test_runtime_log_append_does_not_trigger_structural_page_refresh(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    structural_changes = []
    log_records = []
    platform.changed.connect(lambda: structural_changes.append(True))
    platform.log_added.connect(log_records.append)
    try:
        platform._append_log("info", "one output chunk", "com.example.chatty")

        assert structural_changes == []
        assert [record.message for record in log_records] == ["one output chunk"]
    finally:
        view_model.shutdown()


def test_manifest_observer_events_are_provided_to_local_api(
    qtbot, contract, tmp_path
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "observe_prompt")

        assert platform._observer_events_for_extension(
            extension.manifest.extension_id
        ) == ("prompt.triggered",)
        assert platform._observer_events_for_extension("com.example.missing") == ()
    finally:
        view_model.shutdown()


def test_binding_memory_changes_only_after_persistent_save(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.manager.enable(extension_id)

        def fail_save(_bindings) -> None:
            raise ExtensionBindingError("disk unavailable")

        monkeypatch.setattr(platform._binding_store, "save", fail_save)
        with pytest.raises(ExtensionBindingError, match="disk unavailable"):
            platform.bind_action(
                device_serial="CP01-AABBCCDDEEFF",
                prompt_id=2,
                extension_id=extension_id,
                action_id="use_prompt",
            )
        assert platform.bindings == ()
    finally:
        view_model.shutdown()


def test_unbind_memory_changes_only_after_persistent_save(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.manager.enable(extension_id)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )
        previous = platform.bindings

        def fail_save(_bindings) -> None:
            raise ExtensionBindingError("disk unavailable")

        monkeypatch.setattr(platform._binding_store, "save", fail_save)
        with pytest.raises(ExtensionBindingError, match="disk unavailable"):
            platform.unbind_action("CP01-AABBCCDDEEFF", 2)
        assert platform.bindings == previous
    finally:
        view_model.shutdown()


def test_remove_restores_binding_when_registry_change_fails(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.manager.enable(extension_id)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )
        previous = platform.bindings
        runtime = platform._runtimes[extension_id]

        def fail_remove(_extension_id: str) -> None:
            raise ExtensionManagerError("registry unavailable")

        monkeypatch.setattr(platform.manager, "remove", fail_remove)
        with pytest.raises(ExtensionManagerError, match="registry unavailable"):
            platform.remove(extension_id)

        assert platform.bindings == previous
        assert ExtensionBindingStore(tmp_path / "bindings.json").load() == previous
        assert platform._runtimes[extension_id] is runtime
    finally:
        view_model.shutdown()


def test_remove_does_not_change_runtime_or_registry_when_binding_save_fails(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.manager.enable(extension_id)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )
        previous = platform.bindings
        runtime = platform._runtimes[extension_id]

        def fail_save(_bindings) -> None:
            raise ExtensionBindingError("disk unavailable")

        monkeypatch.setattr(platform._binding_store, "save", fail_save)
        with pytest.raises(ExtensionBindingError, match="disk unavailable"):
            platform.remove(extension_id)

        assert platform.bindings == previous
        assert platform.manager.get(extension_id).enabled
        assert platform._runtimes[extension_id] is runtime
    finally:
        view_model.shutdown()


def test_disable_registry_failure_leaves_runtime_and_bindings_active(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / "claim_prompt")
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5_000)
        platform.bind_action(
            device_serial="CP01-AABBCCDDEEFF",
            prompt_id=2,
            extension_id=extension_id,
            action_id="use_prompt",
        )
        previous = platform.bindings

        def fail_save(_records) -> None:
            raise ExtensionRegistryError("registry unavailable")

        monkeypatch.setattr(platform.manager.registry, "save", fail_save)
        with pytest.raises(ExtensionManagerError, match="registry unavailable"):
            platform.disable(extension_id)

        assert platform.bindings == previous
        assert platform.manager.get(extension_id).enabled
        assert platform.runtime_state(extension_id) is ExtensionRuntimeState.RUNNING
    finally:
        view_model.shutdown()
