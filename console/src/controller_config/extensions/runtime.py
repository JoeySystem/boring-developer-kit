from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from controller_config.extension_runner import (
    ExtensionRunnerCommand,
    private_extension_runner_command,
)
from controller_config.extensions.contracts import ExtensionManifest


_PLATFORM_ENVIRONMENT_NAMES = (
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
)


class ExtensionRuntimeState(str, Enum):
    INSTALLED_DISABLED = "installed-disabled"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    MISSING_ENTRY = "missing-entry"


@dataclass(frozen=True)
class ExtensionRuntimeRecord:
    extension_id: str
    state: ExtensionRuntimeState
    message: str = ""


class ExtensionRuntime(QObject):
    """Own one long-lived extension process launched by BORING's private runner."""

    state_changed = Signal(object)
    standard_output = Signal(str)
    standard_error = Signal(str)

    def __init__(
        self,
        manifest: ExtensionManifest,
        extension_root: Path,
        *,
        api_server_name: str,
        enabled: bool = False,
        runner_command: ExtensionRunnerCommand | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._extension_root = extension_root.expanduser().resolve()
        self._api_server_name = api_server_name
        self._runner_command = runner_command or private_extension_runner_command()
        self._enabled = enabled
        self._process: QProcess | None = None
        self._process_object: QProcess | None = None
        self._stopping = False
        initial_state = (
            ExtensionRuntimeState.STOPPED
            if enabled
            else ExtensionRuntimeState.INSTALLED_DISABLED
        )
        self._record = ExtensionRuntimeRecord(manifest.extension_id, initial_state)

    @property
    def manifest(self) -> ExtensionManifest:
        return self._manifest

    @property
    def extension_root(self) -> Path:
        return self._extension_root

    @property
    def entrypoint(self) -> Path:
        relative = PurePosixPath(self._manifest.entrypoint)
        return self._extension_root.joinpath(*relative.parts)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def record(self) -> ExtensionRuntimeRecord:
        return self._record

    @property
    def state(self) -> ExtensionRuntimeState:
        return self._record.state

    @property
    def process(self) -> QProcess | None:
        return self._process

    def set_enabled(self, enabled: bool, *, stop_timeout_ms: int = 1000) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if not enabled:
            self.stop(timeout_ms=stop_timeout_ms)
            if not self._is_process_active():
                self._set_state(ExtensionRuntimeState.INSTALLED_DISABLED)
        elif self.state == ExtensionRuntimeState.INSTALLED_DISABLED:
            self._set_state(ExtensionRuntimeState.STOPPED)

    def start(self) -> bool:
        if not self._enabled:
            self._set_state(ExtensionRuntimeState.INSTALLED_DISABLED)
            return False
        if self._is_process_active():
            return True
        self._dispose_process(self._process)

        entrypoint = self.entrypoint
        if not entrypoint.is_file():
            self._set_state(
                ExtensionRuntimeState.MISSING_ENTRY,
                f"扩展入口不存在：{entrypoint}",
            )
            return False

        runner = Path(self._runner_command.program)
        if not runner.is_file():
            self._set_state(
                ExtensionRuntimeState.FAILED,
                f"BORING 私有扩展运行器不可用：{runner}",
            )
            return False

        process = self._obtain_process()
        self._process = process
        self._stopping = False
        process.setProgram(str(runner))
        process.setArguments(self._runner_command.for_script(entrypoint))
        process.setWorkingDirectory(str(self._extension_root))
        process.setProcessEnvironment(self._build_environment())
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._set_state(ExtensionRuntimeState.STARTING)
        process.start()
        return True

    def _obtain_process(self) -> QProcess:
        process = self._process_object
        if process is not None:
            return process
        process = QProcess(self)
        process.started.connect(lambda: self._process_started(process))
        process.readyReadStandardOutput.connect(
            lambda: self._read_standard_output(process)
        )
        process.readyReadStandardError.connect(
            lambda: self._read_standard_error(process)
        )
        process.finished.connect(
            lambda exit_code, exit_status: self._process_finished(
                process, exit_code, exit_status
            )
        )
        process.errorOccurred.connect(
            lambda error: self._process_error(process, error)
        )
        self._process_object = process
        return process

    def stop(self, *, timeout_ms: int = 1000) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            self._dispose_process(process)
            if self._enabled:
                self._set_state(ExtensionRuntimeState.STOPPED)
            else:
                self._set_state(ExtensionRuntimeState.INSTALLED_DISABLED)
            return

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        self._request_stop()
        self._wait_until_deadline(deadline)
        self._kill_if_running()
        self._dispose_stopped_process()

    def restart(self, *, stop_timeout_ms: int = 1000) -> bool:
        if not self._enabled:
            self._set_state(ExtensionRuntimeState.INSTALLED_DISABLED)
            return False
        self.stop(timeout_ms=stop_timeout_ms)
        if self._is_process_active():
            return False
        return self.start()

    def shutdown(self, *, timeout_ms: int = 1000) -> None:
        self.stop(timeout_ms=timeout_ms)

    def _build_environment(self) -> QProcessEnvironment:
        source = QProcessEnvironment.systemEnvironment()
        environment = QProcessEnvironment()
        for name in _PLATFORM_ENVIRONMENT_NAMES:
            if source.contains(name):
                environment.insert(name, source.value(name))
        environment.insert("BORING_EXTENSION_API_SERVER", self._api_server_name)
        environment.insert("BORING_EXTENSION_ID", self._manifest.extension_id)
        environment.insert("BORING_EXTENSION_ROOT", str(self._extension_root))
        return environment

    def _is_process_active(self) -> bool:
        return (
            self._process is not None
            and self._process.state() != QProcess.ProcessState.NotRunning
        )

    def _set_state(self, state: ExtensionRuntimeState, message: str = "") -> None:
        record = ExtensionRuntimeRecord(self._manifest.extension_id, state, message)
        if record == self._record:
            return
        self._record = record
        self.state_changed.emit(record)

    def _process_started(self, process: QProcess) -> None:
        if self._process is process and not self._stopping:
            self._set_state(ExtensionRuntimeState.RUNNING)

    def _read_standard_output(self, process: QProcess) -> None:
        value = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if value:
            self.standard_output.emit(value)

    def _read_standard_error(self, process: QProcess) -> None:
        value = bytes(process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        if value:
            self.standard_error.emit(value)

    def _process_finished(
        self, process: QProcess, exit_code: int, _exit_status: object
    ) -> None:
        self._drain_process_output(process)
        if self._process is not process:
            self._dispose_process(process)
            return
        if self._stopping:
            state = (
                ExtensionRuntimeState.STOPPED
                if self._enabled
                else ExtensionRuntimeState.INSTALLED_DISABLED
            )
            self._set_state(state)
        elif exit_code == 0:
            self._set_state(ExtensionRuntimeState.STOPPED)
        else:
            self._set_state(
                ExtensionRuntimeState.FAILED,
                f"扩展进程退出，exit_code={exit_code}",
            )
        self._dispose_process(process)

    def _process_error(self, process: QProcess, _error: object) -> None:
        if self._process is not process:
            self._dispose_process(process)
            return
        if process.state() != QProcess.ProcessState.NotRunning:
            return
        if not self._stopping:
            self._set_state(ExtensionRuntimeState.FAILED, process.errorString())
        self._dispose_process(process)

    def _request_stop(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._stopping = True
        process.terminate()

    def _wait_until_deadline(self, deadline: float) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if remaining_ms:
            process.waitForFinished(remaining_ms)

    def _kill_if_running(self) -> bool:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return True
        process.kill()
        process.waitForFinished(100)
        return process.state() == QProcess.ProcessState.NotRunning

    def _dispose_stopped_process(self) -> bool:
        process = self._process
        if process is not None and process.state() != QProcess.ProcessState.NotRunning:
            self._set_state(
                ExtensionRuntimeState.FAILED,
                "扩展进程未能在停止时限内退出",
            )
            return False
        if process is not None:
            self._drain_process_output(process)
            self._dispose_process(process)
        state = (
            ExtensionRuntimeState.STOPPED
            if self._enabled
            else ExtensionRuntimeState.INSTALLED_DISABLED
        )
        self._set_state(state)
        return True

    def _drain_process_output(self, process: QProcess | None) -> None:
        if process is None:
            return
        output = bytes(process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        error = bytes(process.readAllStandardError()).decode(
            "utf-8", errors="replace"
        )
        if output:
            self.standard_output.emit(output)
        if error:
            self.standard_error.emit(error)

    def _dispose_process(self, process: QProcess | None) -> None:
        if process is None:
            return
        if self._process is process:
            self._process = None
        # The stopped process object is reused on the next start, so repeated
        # restarts do not accumulate child QProcess objects.


def shutdown_extension_runtimes(
    runtimes: Iterable[ExtensionRuntime], *, timeout_ms: int = 1500
) -> None:
    """Stop all runtimes against one shared grace deadline, then kill survivors."""

    materialized = tuple(runtimes)
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    for runtime in materialized:
        runtime._request_stop()
    for runtime in materialized:
        runtime._wait_until_deadline(deadline)
    for runtime in materialized:
        runtime._kill_if_running()
    for runtime in materialized:
        runtime._dispose_stopped_process()
