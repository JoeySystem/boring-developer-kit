from __future__ import annotations

import pytest

from controller_config.device_events import DeviceEvent
from controller_config.host_actions import HostActionRegistry
from controller_config.automation import (
    AutomationError,
    AutomationHost,
    AutomationRunResult,
    AutomationStore,
    DeviceEventBus,
)
from controller_config.transport.demo import DemoGateway
from controller_config.prompt_library import PromptEntry, PromptLibraryStore
from controller_config.viewmodels.main import MainViewModel
from controller_config.workflow_runtime import WorkflowHost
from controller_config.workflows import (
    LocalWorkflow,
    WorkflowError,
    WorkflowStep,
    WorkflowStore,
)


class FakeHostServices:
    platform = "darwin"

    def __init__(self, *, fail_append: bool = False) -> None:
        self.calls = []
        self.fail_append = fail_append

    def capture_selection(self) -> str:
        self.calls.append("capture")
        return "中文\nEnglish"

    def read_clipboard(self) -> str:
        self.calls.append("clipboard")
        return "clipboard"

    def append_text_file(self, path: str, text: str, separator: str) -> None:
        from controller_config.host_selection import HostActionError

        self.calls.append(("append", path, text, separator))
        if self.fail_append:
            raise HostActionError("文件不可写")

    def open_target(self, target: str) -> None:
        self.calls.append(("open", target))

    def show_notification(self, message: str) -> None:
        self.calls.append(("notify", message))


class ImmediateRunner:
    def __init__(self) -> None:
        self.launches = []

    def launch(self, definition, event, completed) -> None:
        self.launches.append((definition, event))
        completed(AutomationRunResult(0))

    def shutdown(self) -> None:
        pass


def _workflow(*, enabled: bool = False, tested: bool = False) -> LocalWorkflow:
    return LocalWorkflow(
        "save-selection",
        "保存选中文字",
        1,
        (
            WorkflowStep("capture_selection", {}),
            WorkflowStep(
                "append_text_file",
                {"path": "/tmp/notes.md", "separator": "\n\n"},
            ),
            WorkflowStep("show_notification", {"message": "已保存"}),
        ),
        enabled,
        tested,
    )


def _event(prompt_id: int = 1) -> DeviceEvent:
    return DeviceEvent(
        "prompt.triggered",
        "usb.prompt",
        "SERIAL-1",
        9,
        {
            "prompt_id": prompt_id,
            "prompt_name": "保存",
            "prompt_body": "设备提示词",
            "body_bytes": 15,
        },
    )


def test_manual_test_runs_same_executor_marks_tested_without_bus(qtbot, tmp_path) -> None:
    services = FakeHostServices()
    host = WorkflowHost(
        WorkflowStore(tmp_path), registry=HostActionRegistry(services)
    )
    host.bind("SERIAL-1")
    host.save_workflow(_workflow())

    result = host.test_workflow("save-selection")

    assert result.succeeded
    assert host.workflows[0].tested is True
    assert host.workflows[0].enabled is False
    assert services.calls == [
        "capture",
        ("append", "/tmp/notes.md", "中文\nEnglish", "\n\n"),
        ("notify", "已保存"),
    ]


def test_physical_event_runs_enabled_workflow_once(qtbot, tmp_path) -> None:
    services = FakeHostServices()
    host = WorkflowHost(
        WorkflowStore(tmp_path), registry=HostActionRegistry(services)
    )
    host.bind("SERIAL-1")
    host.save_workflow(_workflow(enabled=True, tested=True))

    result = host.handle_event(_event())

    assert result is not None and result.handled and result.succeeded
    assert services.calls.count("capture") == 1
    assert "运行完成" in result.message


def test_disabled_or_other_prompt_falls_through(qtbot, tmp_path) -> None:
    services = FakeHostServices()
    host = WorkflowHost(
        WorkflowStore(tmp_path), registry=HostActionRegistry(services)
    )
    host.bind("SERIAL-1")
    host.save_workflow(_workflow())

    assert host.handle_event(_event()) is None
    assert host.handle_event(_event(2)) is None
    assert services.calls == []


def test_failure_stops_later_steps_and_names_failed_step(qtbot, tmp_path) -> None:
    services = FakeHostServices(fail_append=True)
    host = WorkflowHost(
        WorkflowStore(tmp_path), registry=HostActionRegistry(services)
    )
    host.bind("SERIAL-1")
    host.save_workflow(_workflow(enabled=True, tested=True))

    result = host.handle_event(_event())

    assert result is not None and result.handled and not result.succeeded
    assert "第 2 步失败" in result.message
    assert all(call != ("notify", "已保存") for call in services.calls)
    assert any(log.level == "error" and log.step_index == 2 for log in host.logs)


def test_event_bus_stops_after_official_workflow_claims_event(qtbot, tmp_path) -> None:
    workflow_host = WorkflowHost(
        WorkflowStore(tmp_path / "workflows"),
        registry=HostActionRegistry(FakeHostServices()),
    )
    workflow_host.bind("SERIAL-1")
    workflow_host.save_workflow(_workflow(enabled=True, tested=True))
    script = tmp_path / "also.py"
    script.write_text("print('wrong')\n", encoding="utf-8")
    runner = ImmediateRunner()
    automation_host = AutomationHost(
        AutomationStore(tmp_path / "automations"), runner=runner
    )
    automation_host.bind("SERIAL-1")
    automation_host.save_definition(
        automation_id=None,
        name="不应执行",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=True,
    )
    bus = DeviceEventBus()
    bus.register(workflow_host.handle_event)
    bus.register(automation_host.handle_event)

    result = bus.dispatch(_event())

    assert result.handled and result.succeeded
    assert runner.launches == []


def test_device_switch_loads_its_own_workflows_and_clears_logs(qtbot, tmp_path) -> None:
    host = WorkflowHost(
        WorkflowStore(tmp_path), registry=HostActionRegistry(FakeHostServices())
    )
    host.bind("SERIAL-1")
    host.save_workflow(_workflow())
    assert host.logs

    host.bind("SERIAL-2")

    assert host.workflows == ()
    assert host.logs == ()


def test_view_model_prevents_official_and_local_script_from_sharing_trigger(
    qtbot, contract, tmp_path
) -> None:
    services = FakeHostServices()
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        host_action_registry=HostActionRegistry(services),
        automation_store=AutomationStore(tmp_path / "automations"),
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=3_000)
    view_model.prompt_device._read_entries = [PromptEntry(1, "Trigger", "Configured on device")]
    view_model.prompt_device._finish_full_read()
    view_model.save_workflow(_workflow(enabled=True, tested=True))
    script = tmp_path / "action.py"
    script.write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(AutomationError, match="内置自动化"):
        view_model.save_automation(
            automation_id=None,
            name="冲突脚本",
            trigger_prompt_id=1,
            script_path=str(script),
            enabled=True,
        )
    view_model.shutdown()


def test_official_workflow_cannot_replace_enabled_local_script(
    qtbot, contract, tmp_path
) -> None:
    view_model = MainViewModel(
        DemoGateway(contract, "ready"),
        contract,
        workflow_store=WorkflowStore(tmp_path / "workflows"),
        host_action_registry=HostActionRegistry(FakeHostServices()),
        automation_store=AutomationStore(tmp_path / "automations"),
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=3_000)
    view_model.prompt_device._read_entries = [PromptEntry(1, "Trigger", "Configured on device")]
    view_model.prompt_device._finish_full_read()
    script = tmp_path / "action.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    view_model.save_automation(
        automation_id=None,
        name="已有脚本",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=True,
    )

    with pytest.raises(WorkflowError, match="本地脚本"):
        view_model.save_workflow(_workflow(enabled=True, tested=True))
    view_model.shutdown()
