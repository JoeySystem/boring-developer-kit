from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QProcess

from controller_config import extension_runner
from controller_config.extension_runner import ExtensionRunnerCommand
from controller_config.extensions.contracts import ExtensionManifest
from controller_config.extensions.runtime import (
    ExtensionRuntime,
    ExtensionRuntimeState,
    shutdown_extension_runtimes,
)


def _manifest(*, entrypoint: str = "main.py") -> ExtensionManifest:
    return ExtensionManifest.from_mapping(
        {
            "manifest_version": 1,
            "id": "com.boring.runtime-test",
            "name": "Runtime test",
            "version": "1.0.0",
            "api_version": {"major": 1, "minor": 0},
            "entrypoint": entrypoint,
            "observer_events": [],
            "actions": [],
        }
    )


def _write_resident_script(path: Path, *, before_loop: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "import time\n"
        + before_loop
        + "while True:\n"
        + "    time.sleep(0.05)\n",
        encoding="utf-8",
    )


def test_disabled_runtime_does_not_launch(tmp_path) -> None:
    _write_resident_script(tmp_path / "main.py")
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
    )

    assert runtime.state == ExtensionRuntimeState.INSTALLED_DISABLED
    assert runtime.start() is False
    assert runtime.process is None


def test_runtime_uses_private_runner_and_minimal_environment(
    qtbot, monkeypatch, tmp_path
) -> None:
    output = tmp_path / "environment.json"
    monkeypatch.setenv("BORING_RUNTIME_TEST_SECRET", "must-not-leak")
    _write_resident_script(
        tmp_path / "main.py",
        before_loop=(
            "import json, os\n"
            f"open({str(output)!r}, 'w', encoding='utf-8').write("
            "json.dumps(dict(os.environ), ensure_ascii=False))\n"
        ),
    )
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )

    assert runtime.start() is True
    qtbot.waitUntil(
        lambda: runtime.state == ExtensionRuntimeState.RUNNING
        and output.is_file(),
        timeout=3000,
    )
    environment = json.loads(output.read_text(encoding="utf-8"))
    assert environment["BORING_EXTENSION_API_SERVER"] == "boring-test-api"
    assert environment["BORING_EXTENSION_ID"] == "com.boring.runtime-test"
    assert environment["BORING_EXTENSION_ROOT"] == str(tmp_path.resolve())
    assert "BORING_RUNTIME_TEST_SECRET" not in environment
    assert "PYTHONPATH" not in environment

    runtime.shutdown()
    assert runtime.state == ExtensionRuntimeState.STOPPED
    assert runtime.process is None


def test_missing_entry_has_explicit_state_without_launch(tmp_path) -> None:
    runtime = ExtensionRuntime(
        _manifest(entrypoint="missing.py"),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )

    assert runtime.start() is False
    assert runtime.state == ExtensionRuntimeState.MISSING_ENTRY
    assert "missing.py" in runtime.record.message
    assert runtime.process is None


def test_missing_private_runner_fails_without_python_fallback(tmp_path) -> None:
    _write_resident_script(tmp_path / "main.py")
    missing_runner = tmp_path / "missing-private-runner"
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
        runner_command=ExtensionRunnerCommand(str(missing_runner)),
    )

    assert runtime.start() is False
    assert runtime.state == ExtensionRuntimeState.FAILED
    assert str(missing_runner) in runtime.record.message
    assert runtime.process is None


def test_nuitka_compiled_console_resolves_sibling_private_runner(
    monkeypatch, tmp_path
) -> None:
    console = tmp_path / "BORING Console"
    monkeypatch.setattr(extension_runner.sys, "executable", str(console))
    monkeypatch.setattr(extension_runner.sys, "platform", "darwin")
    monkeypatch.delattr(extension_runner.sys, "frozen", raising=False)
    monkeypatch.setitem(extension_runner.__dict__, "__compiled__", object())

    command = extension_runner.private_extension_runner_command()

    assert command.program == str(tmp_path / "boring-extension-runner")
    assert command.arguments == ()


def test_development_console_uses_current_python_module_runner(monkeypatch) -> None:
    monkeypatch.delattr(extension_runner.sys, "frozen", raising=False)
    monkeypatch.delitem(extension_runner.__dict__, "__compiled__", raising=False)

    command = extension_runner.private_extension_runner_command()

    assert command.program == sys.executable
    assert command.arguments == ("-m", "controller_config.extension_runner")


def test_runtime_reports_nonzero_extension_exit(qtbot, tmp_path) -> None:
    (tmp_path / "main.py").write_text(
        "raise RuntimeError('broken extension')\n",
        encoding="utf-8",
    )
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )
    errors: list[str] = []
    runtime.standard_error.connect(errors.append)

    assert runtime.start() is True
    qtbot.waitUntil(
        lambda: runtime.state == ExtensionRuntimeState.FAILED,
        timeout=3000,
    )
    assert "exit_code=1" in runtime.record.message
    assert any("RuntimeError: broken extension" in value for value in errors)
    assert runtime.process is None


def test_restart_relaunches_enabled_extension(qtbot, tmp_path) -> None:
    counter = tmp_path / "starts.txt"
    _write_resident_script(
        tmp_path / "main.py",
        before_loop=(
            f"with open({str(counter)!r}, 'a', encoding='utf-8') as stream:\n"
            "    stream.write('started\\n')\n"
            "    stream.flush()\n"
        ),
    )
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )

    assert runtime.start() is True
    qtbot.waitUntil(
        lambda: counter.is_file()
        and len(counter.read_text(encoding="utf-8").splitlines()) == 1,
        timeout=3000,
    )
    process_object = runtime.process
    assert runtime.restart() is True
    qtbot.waitUntil(
        lambda: len(counter.read_text(encoding="utf-8").splitlines()) == 2,
        timeout=3000,
    )
    assert runtime.state == ExtensionRuntimeState.RUNNING
    assert runtime.process is process_object

    runtime.shutdown()


def test_disabling_runtime_stops_process_and_is_idempotent(qtbot, tmp_path) -> None:
    _write_resident_script(tmp_path / "main.py")
    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )
    runtime.start()
    qtbot.waitUntil(
        lambda: runtime.state == ExtensionRuntimeState.RUNNING,
        timeout=3000,
    )

    runtime.set_enabled(False)
    runtime.set_enabled(False)
    runtime.shutdown()

    assert runtime.state == ExtensionRuntimeState.INSTALLED_DISABLED
    assert runtime.process is None


def test_kill_timeout_keeps_process_and_reports_failure(tmp_path) -> None:
    class StubbornProcess:
        def state(self):
            return QProcess.ProcessState.Running

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def waitForFinished(self, _timeout_ms: int) -> bool:
            return False

    runtime = ExtensionRuntime(
        _manifest(),
        tmp_path,
        api_server_name="boring-test-api",
        enabled=True,
    )
    process = StubbornProcess()
    runtime._process = process

    runtime.set_enabled(False, stop_timeout_ms=0)

    assert runtime.state is ExtensionRuntimeState.FAILED
    assert "未能在停止时限内退出" in runtime.record.message
    assert runtime.process is process


def test_shutdown_uses_one_deadline_for_all_runtimes(qtbot, tmp_path) -> None:
    runtimes = []
    for index in range(2):
        root = tmp_path / str(index)
        _write_resident_script(root / "main.py")
        runtime = ExtensionRuntime(
            _manifest(),
            root,
            api_server_name="boring-test-api",
            enabled=True,
        )
        runtime.start()
        runtimes.append(runtime)
    qtbot.waitUntil(
        lambda: all(
            runtime.state == ExtensionRuntimeState.RUNNING
            for runtime in runtimes
        ),
        timeout=3000,
    )

    started = time.monotonic()
    shutdown_extension_runtimes(runtimes, timeout_ms=500)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert all(runtime.process is None for runtime in runtimes)
    assert all(runtime.state == ExtensionRuntimeState.STOPPED for runtime in runtimes)
