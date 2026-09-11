from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from controller_config.automation import (
    AutomationError,
    AutomationHost,
    AutomationRunResult,
    AutomationStore,
    DeviceEvent,
    DeviceEventBus,
    EventDispatchResult,
    LocalScriptAutomation,
    PromptPasteEventHandler,
    QProcessScriptRunner,
)
from controller_config.extension_runner import (
    ExtensionRunnerCommand,
    main as extension_runner_main,
    private_extension_runner_command,
)


class ImmediateRunner:
    def __init__(self, result: AutomationRunResult | None = None) -> None:
        self.result = result or AutomationRunResult(0, "done", "")
        self.launches = []
        self.shutdown_calls = 0

    def launch(self, definition, event, completed) -> None:
        self.launches.append((definition, event))
        completed(self.result)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _event(prompt_id: int = 1) -> DeviceEvent:
    return DeviceEvent(
        kind="prompt.triggered",
        source="usb.prompt",
        device_serial="CP01-AABBCCDDEEFF",
        event_id=7,
        payload={
            "prompt_id": prompt_id,
            "prompt_name": "自动化",
            "prompt_body": "中文正文",
            "body_bytes": 12,
        },
    )


def test_automation_store_round_trips_device_scoped_bindings(tmp_path) -> None:
    script = tmp_path / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    definition = LocalScriptAutomation("one", "本地脚本", 2, str(script), True)
    store = AutomationStore(tmp_path / "store")

    store.save("CP01-AABBCCDDEEFF", (definition,))

    assert store.load("CP01-AABBCCDDEEFF") == (definition,)
    raw = json.loads(
        store.path_for("CP01-AABBCCDDEEFF").read_text(encoding="utf-8")
    )
    assert raw["version"] == 1
    assert raw["automations"][0]["trigger_prompt_id"] == 2


def test_one_prompt_slot_cannot_claim_two_local_automations(tmp_path) -> None:
    store = AutomationStore(tmp_path)
    first = LocalScriptAutomation("one", "一", 1, "/tmp/one.py", False)
    second = LocalScriptAutomation("two", "二", 1, "/tmp/two.py", False)

    with pytest.raises(AutomationError, match="已绑定"):
        store.save("CP01-AABBCCDDEEFF", (first, second))


def test_binding_another_device_clears_runtime_logs(qtbot, tmp_path) -> None:
    script = tmp_path / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    host = AutomationHost(
        AutomationStore(tmp_path / "store"),
        runner=ImmediateRunner(),
    )
    host.bind("CP01-AABBCCDDEEFF")
    host.save_definition(
        automation_id=None,
        name="第一台设备",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=True,
    )
    assert host.logs

    host.bind("CP01-112233445566")

    assert host.logs == ()
    assert host.definitions == ()


def test_event_bus_uses_first_handler_that_claims_event(qtbot) -> None:
    bus = DeviceEventBus()
    calls = []
    bus.register(lambda event: calls.append(("first", event.kind)) or None)
    bus.register(
        lambda event: calls.append(("second", event.kind))
        or EventDispatchResult(True, True, "handled")
    )
    bus.register(
        lambda event: calls.append(("third", event.kind))
        or EventDispatchResult(True, True, "wrong")
    )

    result = bus.dispatch(_event())

    assert result.message == "handled"
    assert calls == [("first", "prompt.triggered"), ("second", "prompt.triggered")]
    assert bus.events == (_event(),)


def test_enabled_binding_claims_prompt_event_and_runs_selected_script(
    qtbot, tmp_path
) -> None:
    script = tmp_path / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    runner = ImmediateRunner()
    host = AutomationHost(AutomationStore(tmp_path / "store"), runner=runner)
    host.bind("CP01-AABBCCDDEEFF")
    definition = host.save_definition(
        automation_id=None,
        name="实体自动化",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=True,
    )

    result = host.handle_event(_event())

    assert result is not None and result.handled and result.succeeded
    assert runner.launches[0][0] == definition
    assert runner.launches[0][1].payload["prompt_body"] == "中文正文"
    assert any(entry.level == "success" for entry in host.logs)


def test_disabled_binding_falls_through_to_existing_prompt_paste(qtbot, tmp_path) -> None:
    script = tmp_path / "run.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    runner = ImmediateRunner()
    host = AutomationHost(AutomationStore(tmp_path / "store"), runner=runner)
    host.bind("CP01-AABBCCDDEEFF")
    host.save_definition(
        automation_id=None,
        name="暂停的自动化",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=False,
    )

    class Helper:
        def handle_entry(self, entry):
            return type("Result", (), {"body_bytes": entry.body_bytes})()

    bus = DeviceEventBus()
    bus.register(host.handle_event)
    bus.register(PromptPasteEventHandler(Helper()))

    result = bus.dispatch(_event())

    assert result.handled and result.succeeded
    assert "UTF-8" in result.message
    assert runner.launches == []


def test_real_python_runner_receives_utf8_event_json(qtbot, tmp_path) -> None:
    output = tmp_path / "received.json"
    script = tmp_path / "receive.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(output)!r}).write_text(sys.stdin.read(), encoding='utf-8')\n",
        encoding="utf-8",
    )
    host = AutomationHost(AutomationStore(tmp_path / "store"))
    host.bind("CP01-AABBCCDDEEFF")
    definition = host.save_definition(
        automation_id=None,
        name="真实 Python",
        trigger_prompt_id=1,
        script_path=str(script),
        enabled=True,
    )

    result = host.handle_event(_event())

    assert result is not None and result.handled
    qtbot.waitUntil(output.exists, timeout=3000)
    qtbot.waitUntil(
        lambda: any(entry.level == "success" for entry in host.logs),
        timeout=3000,
    )
    received = json.loads(output.read_text(encoding="utf-8"))
    assert received["schema_version"] == 1
    assert received["kind"] == "prompt.triggered"
    assert received["payload"]["prompt_body"] == "中文正文"
    assert definition.script_path == str(script.resolve())
    host.shutdown()


def test_runner_bounds_output_and_rejects_parallel_runs_then_allows_cancel_retry(qtbot, tmp_path):
    from controller_config.automation import MAX_SCRIPT_OUTPUT_BYTES
    script = tmp_path / "noisy.py"
    ready = tmp_path / "started"
    script.write_text(
        "import pathlib, signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdout.write('x' * 300000)\nsys.stdout.flush()\n"
        "sys.stderr.write('中' * 100000)\nsys.stderr.flush()\n"
        f"pathlib.Path({str(ready)!r}).touch()\n"
        "time.sleep(60)\n"
    )
    definition = LocalScriptAutomation("noisy", "noisy", 1, str(script), False)
    runner = QProcessScriptRunner()
    results = []
    try:
        runner.launch(definition, _event(), results.append)
        with pytest.raises(AutomationError, match="运行"):
            runner.launch(definition, _event(), results.append)
        assert runner.active_count == 1
        qtbot.waitUntil(ready.exists, timeout=3000)
        runner.cancel()
        qtbot.waitUntil(lambda: len(results) == 1, timeout=3000)
        assert results[0].cancelled
        assert len(results[0].standard_output.encode()) <= MAX_SCRIPT_OUTPUT_BYTES
        assert "截断" in results[0].standard_output
        assert len(results[0].standard_error.encode()) <= MAX_SCRIPT_OUTPUT_BYTES
        assert "截断" in results[0].standard_error
        assert runner.active_count == 0
        script.write_text("print('again')\n")
        runner.launch(definition, _event(), results.append)
        qtbot.waitUntil(lambda: len(results) == 2, timeout=3000)
        assert results[-1].standard_output == "again"
    finally:
        runner.shutdown()


def test_source_mode_enters_private_runner_module() -> None:
    command = private_extension_runner_command()

    assert command.program == sys.executable
    assert command.arguments == ("-m", "controller_config.extension_runner")
    assert command.for_script(Path("example.py")) == [
        "-m",
        "controller_config.extension_runner",
        "--script",
        "example.py",
    ]


def test_frozen_app_resolves_sibling_private_runner(monkeypatch, tmp_path) -> None:
    import controller_config.extension_runner as runner_module

    console = tmp_path / "BORING.exe"
    monkeypatch.setattr(runner_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner_module.sys, "platform", "win32")
    monkeypatch.setattr(runner_module.sys, "executable", str(console))

    command = runner_module.private_extension_runner_command()

    assert command == ExtensionRunnerCommand(
        str(tmp_path / "boring-extension-runner.exe")
    )


def test_missing_private_runner_fails_without_system_python_fallback(tmp_path) -> None:
    runner = QProcessScriptRunner(
        runner_command=ExtensionRunnerCommand(str(tmp_path / "missing-runner"))
    )
    definition = LocalScriptAutomation(
        "missing-runner",
        "缺少运行器",
        1,
        str(tmp_path / "entry.py"),
        True,
    )

    with pytest.raises(AutomationError, match="私有扩展运行器不可用"):
        runner.launch(definition, _event(), lambda _result: None)


def test_private_runner_reports_missing_entry(capsys, tmp_path) -> None:
    missing = tmp_path / "missing.py"

    assert extension_runner_main(["--script", str(missing)]) == 2
    assert "extension entry does not exist" in capsys.readouterr().err


def test_private_runner_supports_sibling_pure_python_modules(capsys, tmp_path) -> None:
    (tmp_path / "helper.py").write_text(
        "MESSAGE = 'sibling import works'\n",
        encoding="utf-8",
    )
    script = tmp_path / "main.py"
    script.write_text(
        "from helper import MESSAGE\nprint(MESSAGE)\n",
        encoding="utf-8",
    )

    assert extension_runner_main(["--script", str(script)]) == 0
    assert capsys.readouterr().out.strip() == "sibling import works"


def test_private_runner_reports_extension_failure(capsys, tmp_path) -> None:
    script = tmp_path / "broken.py"
    script.write_text("raise RuntimeError('extension failed')\n", encoding="utf-8")

    assert extension_runner_main(["--script", str(script)]) == 1
    assert "RuntimeError: extension failed" in capsys.readouterr().err


def test_python_runner_drains_large_script_output_without_blocking(qtbot, tmp_path) -> None:
    script = tmp_path / "large_output.py"
    script.write_text("print('x' * 200000)\n", encoding="utf-8")
    definition = LocalScriptAutomation(
        "large-output",
        "大输出",
        1,
        str(script),
        True,
    )
    results = []
    runner = QProcessScriptRunner()

    runner.launch(definition, _event(), results.append)

    qtbot.waitUntil(lambda: len(results) == 1, timeout=3000)
    assert results[0].exit_code == 0
    assert len(results[0].standard_output.encode()) <= 64 * 1024
    assert "截断" in results[0].standard_output
    runner.shutdown()
