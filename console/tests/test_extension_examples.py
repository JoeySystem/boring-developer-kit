"""Run shipped examples against real local files and private extension processes."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer

from controller_config.extensions.contracts import ActionInvocation, SemanticEvent
from controller_config.extensions.runtime import ExtensionRuntimeState
from test_extension_platform import EXAMPLES, _platform


def _host_event():
    return {'schema_version': 1, 'kind': 'host_action.triggered', 'source': 'usb.host_action',
            'device_serial': 'CP01-AABBCCDDEEFF', 'event_id': 4, 'payload': {'action_id': 1, 'task_token': '0123456789abcdef0123456789abcdef'}}


def test_standard_library_script_writes_real_event_file(tmp_path):
    script = EXAMPLES.parent / 'scripts/save_host_task_event.py'
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(_host_event()),
                            text=True, capture_output=True, check=True,
                            env={**os.environ, 'TMPDIR': str(tmp_path), 'TEMP': str(tmp_path), 'TMP': str(tmp_path)})
    output = Path(result.stdout.strip().removeprefix('Saved: '))
    assert output.is_relative_to(tmp_path)
    assert json.loads(output.read_text()) == _host_event()


def test_api11_example_writes_real_file_with_independent_event(qtbot, contract, tmp_path, monkeypatch):
    temporary = tempfile.TemporaryDirectory(prefix='bx-', dir='/tmp')
    output_root = Path(temporary.name)
    monkeypatch.setenv('TMPDIR', str(output_root))
    monkeypatch.setenv('TEMP', str(output_root))
    monkeypatch.setenv('TMP', str(output_root))
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(EXAMPLES / 'save_host_task')
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5000)
        results = []
        platform.api.action_result_received.connect(lambda ext, result: results.append(result))
        invocation = ActionInvocation('example-file', extension_id, 'save_event',
                                      _host_event()['device_serial'], 1, SemanticEvent.from_mapping(_host_event()))
        assert platform.api.invoke_action(invocation, lambda result: None)
        qtbot.waitUntil(lambda: any(result.status == 'completed' for result in results), timeout=5000)
        completed = next(result for result in results if result.status == 'completed')
        output = Path(completed.message.removeprefix('已保存：'))
        assert output.is_relative_to(output_root)
        assert json.loads(output.read_text()) == _host_event()
        assert platform.api.active_action_count == 0
    finally:
        view_model.shutdown()
        temporary.cleanup()


def test_async_disable_keeps_ui_alive_and_waits_for_process_exit(qtbot, contract, tmp_path):
    package = tmp_path / 'unresponsive'
    shutil.copytree(EXAMPLES / 'save_host_task', package)
    (package / 'main.py').write_text('''
import signal
import time
from boring_console_sdk import BoringConsoleClient
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with BoringConsoleClient.from_environment() as client:
    print('ready', flush=True)
    while True:
        time.sleep(0.05)
''')
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    try:
        extension = platform.import_package(package)
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5000)
        process = platform._runtimes[extension_id].process
        completion, ui_ticks = [], []
        platform.disable_async(extension_id, lambda: completion.append(process.state()))
        QTimer.singleShot(20, lambda: ui_ticks.append(True))
        assert completion == []
        qtbot.waitUntil(lambda: bool(ui_ticks), timeout=300)
        assert completion == []
        qtbot.waitUntil(lambda: bool(completion), timeout=3000)
        assert completion == [QProcess.ProcessState.NotRunning]
        assert not platform.manager.get(extension_id).enabled
        assert platform.runtime_state(extension_id) == ExtensionRuntimeState.INSTALLED_DISABLED
    finally:
        view_model.shutdown()


def test_crashed_runner_notifies_host_only_after_process_exit(qtbot, contract, tmp_path, monkeypatch):
    package = tmp_path / 'crash-after-accept'
    shutil.copytree(EXAMPLES / 'save_host_task', package)
    (package / 'main.py').write_text('''
from boring_console_sdk import BoringConsoleClient
with BoringConsoleClient.from_environment() as client:
    invocation = client.next_action(timeout_ms=3000)
    client.respond_action(invocation['invocation_id'], 'accepted')
    raise SystemExit(7)
''')
    _gateway, view_model, platform = _platform(qtbot, contract, tmp_path)
    stopped = []
    try:
        extension = platform.import_package(package)
        extension_id = extension.manifest.extension_id
        platform.enable(extension_id)
        qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5000)
        process = platform._runtimes[extension_id].process
        monkeypatch.setattr(view_model.host_tasks, 'extension_stopped',
                            lambda identity: stopped.append((identity, process.state())))
        invocation = ActionInvocation('crash-event', extension_id, 'save_event',
                                      _host_event()['device_serial'], 1, SemanticEvent.from_mapping(_host_event()))
        assert platform.api.invoke_action(invocation, lambda result: None)
        qtbot.waitUntil(lambda: bool(stopped), timeout=5000)
        assert stopped == [(extension_id, QProcess.ProcessState.NotRunning)]
        qtbot.waitUntil(lambda: platform.api.active_action_count == 0)
    finally:
        view_model.shutdown()
