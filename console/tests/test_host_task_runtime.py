from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import replace

import pytest
from PySide6.QtCore import QThread, QTimer

from controller_config.automation import AutomationError, AutomationHost, AutomationStore, LocalScriptAutomation
from controller_config.host_actions import HostActionRegistry
from controller_config.host_selection import SystemHostServices
from controller_config.workflow_files import export_workflow, import_workflow
from controller_config.workflow_runtime import WorkflowHost
from controller_config.workflows import LocalWorkflow, WorkflowError, WorkflowStep, WorkflowStore


def definition(path, *, enabled=False, tested=False):
    return LocalWorkflow("capture", "收藏文字", None, (
        WorkflowStep("read_clipboard", {}),
        WorkflowStep("append_text_file", {"path": str(path), "separator": "\n"}),
        WorkflowStep("open_target", {"target": "original"}),
    ), enabled=enabled, tested=tested)


class SlowServices:
    platform = "darwin"

    def __init__(self, qapp):
        self.qapp = qapp
        self.started = threading.Event()
        self.release = threading.Event()
        self.writes = 0
        self.opened = []

    def read_clipboard(self):
        assert QThread.currentThread() == self.qapp.thread()
        return "文字"

    def append_text_file(self, path, text, separator):
        assert QThread.currentThread() != self.qapp.thread()
        self.started.set()
        assert self.release.wait(3), "test did not release file operation"
        self.writes += 1
        from pathlib import Path
        with Path(path).open("a", encoding="utf-8") as output:
            output.write(text)

    def open_target(self, target):
        assert QThread.currentThread() == self.qapp.thread()
        self.opened.append(target)


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_slow_file_keeps_gui_alive_and_busy_until_stopped(qtbot, qapp, tmp_path, stop):
    services = SlowServices(qapp)
    host = WorkflowHost(WorkflowStore(tmp_path / "store"), registry=HostActionRegistry(services))
    host.bind("TEST")
    output = tmp_path / "notes.txt"
    task = definition(output)
    results = []
    ticks = []
    ticker = QTimer()
    ticker.timeout.connect(lambda: ticks.append(1))
    ticker.start(5)
    try:
        host.run_definition(task, completed=results.append, timeout_ms=40 if stop == "timeout" else 60_000)
        qtbot.waitUntil(services.started.is_set)
        qtbot.waitUntil(lambda: len(ticks) >= 2)
        if stop == "cancel":
            host.cancel_run()
        else:
            qtbot.waitUntil(lambda: host.status == "timing_out")
        assert host.running
        assert results == []
        with pytest.raises(WorkflowError, match="正在运行"):
            host.run_definition(task)
        services.release.set()
        qtbot.waitUntil(lambda: bool(results))
        assert not host.running
        assert results[0].cancelled == (stop == "cancel")
        assert results[0].timed_out == (stop == "timeout")
        assert services.writes == 1
        assert services.opened == []
        assert output.read_text(encoding="utf-8") == "文字"
        assert host.workflows == ()
    finally:
        services.release.set()
        ticker.stop()
        qtbot.waitUntil(lambda: not host.running)


@pytest.mark.parametrize("enabled", [True, False])
def test_running_workflow_uses_snapshot_and_trial_preserves_enabled(qtbot, qapp, tmp_path, enabled):
    services = SlowServices(qapp)
    host = WorkflowHost(WorkflowStore(tmp_path / "store"), registry=HostActionRegistry(services))
    host.bind("TEST")
    task = definition(tmp_path / "notes.txt", enabled=enabled, tested=enabled)
    host.save_workflow(task)
    results = []
    host.test_workflow(task.workflow_id, results.append)
    try:
        qtbot.waitUntil(services.started.is_set)
        task.steps[-1].parameters["target"] = "edited"
        services.release.set()
        qtbot.waitUntil(lambda: bool(results))
        assert results[0].succeeded
        assert services.opened == ["original"]
        assert host.workflows[0].enabled is enabled
        assert host.workflows[0].tested is enabled
        assert host.workflows[0].steps[-1].parameters["target"] == "edited"
    finally:
        services.release.set()
        qtbot.waitUntil(lambda: not host.running)


def test_selection_os_command_runs_off_thread_but_clipboard_poll_stays_on_gui(qtbot, qapp, tmp_path):
    sequence = [1]
    command_started = threading.Event()
    release = threading.Event()

    class Clipboard:
        def text(self):
            assert QThread.currentThread() == qapp.thread()
            return "selected"

    def get_sequence():
        assert QThread.currentThread() == qapp.thread()
        return sequence[0]

    def run_command(command, **kwargs):
        assert QThread.currentThread() != qapp.thread()
        command_started.set()
        assert release.wait(3)
        sequence[0] = 2
        return subprocess.CompletedProcess(command, 0, "", "")

    services = SystemHostServices(Clipboard(), platform="darwin", runner=run_command, clipboard_sequence=get_sequence)
    host = WorkflowHost(WorkflowStore(tmp_path), registry=HostActionRegistry(services))
    task = LocalWorkflow("selection", "选区", None, (WorkflowStep("capture_selection", {}),))
    results = []
    host.run_definition(task, completed=results.append)
    try:
        qtbot.waitUntil(command_started.is_set)
        assert host.running
        release.set()
        qtbot.waitUntil(lambda: bool(results))
        assert results[0].succeeded
    finally:
        release.set()
        qtbot.waitUntil(lambda: not host.running)


def test_nullable_tasks_roundtrip_without_claiming_same_prompt(tmp_path):
    first = definition(tmp_path / "notes.txt", enabled=True, tested=True)
    second = replace(first, workflow_id="two")
    store = WorkflowStore(tmp_path / "workflows")
    store.save("TEST", (first, second))
    assert store.load("TEST") == (first, second)
    assert json.loads(store.path_for("TEST").read_text())["version"] == 2
    exported = tmp_path / "task.json"
    export_workflow(exported, first)
    assert json.loads(exported.read_text())["version"] == 2
    assert import_workflow(exported).trigger_prompt_id is None
    legacy = replace(first, trigger_prompt_id=2)
    export_workflow(exported, legacy)
    assert json.loads(exported.read_text())["version"] == 1
    assert import_workflow(exported).trigger_prompt_id == 2

    scripts = AutomationStore(tmp_path / "scripts")
    a = LocalScriptAutomation("a", "脚本", None, "/tmp/a.py", False, timeout_ms=120_000)
    b = replace(a, automation_id="b")
    scripts.save("TEST", (a, b))
    assert scripts.load("TEST") == (a, b)
    assert json.loads(scripts.path_for("TEST").read_text())["version"] == 2


def test_open_target_resolves_path_off_thread_then_opens_on_gui(qtbot, qapp, tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("note")
    opened = []

    class Services(SystemHostServices):
        def prepare_open_target(self, value):
            assert QThread.currentThread() != qapp.thread()
            return super().prepare_open_target(value)

    def opener(url):
        assert QThread.currentThread() == qapp.thread()
        opened.append(url.toLocalFile())
        return True

    host = WorkflowHost(WorkflowStore(tmp_path), registry=HostActionRegistry(Services(opener=opener)))
    results = []
    task = LocalWorkflow("open", "打开", None, (WorkflowStep("open_target", {"target": str(target)}),))
    host.run_definition(task, completed=results.append)
    qtbot.waitUntil(lambda: bool(results))
    assert results[0].succeeded
    assert opened == [str(target)]


def test_script_timeout_waits_for_process_exit_and_reports_once(qtbot, tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    task = LocalScriptAutomation("slow", "慢脚本", None, str(script), False, timeout_ms=100)
    host = AutomationHost(AutomationStore(tmp_path / "store"))
    host.bind("TEST")
    results = []
    signals = []
    host.run_completed.connect(signals.append)
    try:
        host.launch_definition(task, completed=results.append)
        with pytest.raises(AutomationError, match="正在运行"):
            host.launch_definition(task)
        qtbot.waitUntil(lambda: bool(results), timeout=3000)
        assert results[0].timed_out
        assert host.status == "timed_out"
        assert not host.running
        assert len(signals) == 1
        assert host.definitions == ()
    finally:
        host.shutdown()


def test_external_busy_blocks_trials_and_old_event_entry(qtbot, tmp_path):
    host = WorkflowHost(WorkflowStore(tmp_path))
    host.external_busy = lambda: True
    with pytest.raises(WorkflowError, match="正在运行"):
        host.run_definition(definition(tmp_path / "notes.txt"))
    script = AutomationHost(AutomationStore(tmp_path))
    script.external_busy = lambda: True
    with pytest.raises(AutomationError, match="正在运行"):
        script.launch_definition(LocalScriptAutomation("a", "脚本", None, "missing.py", False))
