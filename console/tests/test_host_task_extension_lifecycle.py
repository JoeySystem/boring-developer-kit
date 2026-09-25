from __future__ import annotations

from types import SimpleNamespace
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from controller_config.device_events import automation_manual_test_event
from controller_config.extensions.contracts import ActionInvocationResult
from controller_config.extensions.platform import ExtensionPlatformController
from controller_config.host_tasks import HostTasks


class FakeApi:
    def __init__(self):
        self.actions = {}
        self.cancel_requests = []
        self.claim = None

    @property
    def active_action_count(self):
        return len(self.actions)

    @property
    def active_extension_ids(self):
        return tuple(set(self.actions.values()))

    def request_cancel_invocation(self, invocation_id):
        self.cancel_requests.append(invocation_id)
        return True

    def cancel_invocation(self, invocation_id):
        self.actions.pop(invocation_id, None)

    def invoke_action(self, invocation, completed):
        self.actions[invocation.invocation_id] = invocation.extension_id
        self.claim = completed
        self.invocation = invocation
        return True


class FakePlatform:
    def __init__(self):
        self.api = FakeApi()
        self.disables = []
        self.exit_callbacks = {}

    def disable_async(self, extension_id, completed):
        # Process exit is deliberately separate from socket/terminal messages.
        self.disables.append(extension_id)
        self.exit_callbacks[extension_id] = completed

    def process_exited(self, extension_id):
        self.exit_callbacks.pop(extension_id)()


def setup_host(tmp_path):
    platform = FakePlatform()
    runner = SimpleNamespace(running=False, cancel_run=lambda: None)
    vm = SimpleNamespace(
        extension_platform=platform,
        workflow_host=runner,
        automation_host=runner,
        extension_context_revision=1,
    )
    host = HostTasks(vm, lambda command: None, directory=tmp_path)
    host.serial = 'TEST'
    return host, platform


def accepted(host, platform, invocation='legacy'):
    platform.api.actions[invocation] = 'extension.test'
    host.extension_result('extension.test', ActionInvocationResult(invocation, 'accepted', '接受'))


def terminal(host, platform, invocation='legacy', status='completed'):
    platform.api.cancel_invocation(invocation)
    host.extension_result('extension.test', ActionInvocationResult(invocation, status, '终态'))


def test_legacy_socket_loss_and_terminal_during_disable_do_not_release_busy(qtbot, tmp_path):
    host, platform = setup_host(tmp_path)
    accepted(host, platform)
    assert host._legacy_actions['legacy'][1].interval() == 60_000
    platform.api.actions.clear()  # QLocalSocket disconnects before runner exits.
    assert host.running

    host.cancel_run()
    assert platform.api.cancel_requests == ['legacy']
    qtbot.waitUntil(lambda: bool(platform.disables))
    assert host.running
    terminal(host, platform)
    assert host.running
    assert '已完成' not in host.status
    platform.process_exited('extension.test')
    assert not host.running
    assert host._legacy_actions == {}
    assert host.status.startswith('已停止任务')


def test_cooperative_terminal_keeps_observer_extension_enabled(qtbot, tmp_path):
    host, platform = setup_host(tmp_path)
    accepted(host, platform)
    host.cancel_run()
    terminal(host, platform, status='failed')
    assert not host.running
    assert host.status.startswith('已停止任务')
    qtbot.wait(550)
    assert platform.disables == []


def test_legacy_timeout_stops_actual_runner_before_releasing_busy(qtbot, tmp_path):
    host, platform = setup_host(tmp_path)
    accepted(host, platform)
    host._legacy_actions['legacy'][1].start(1)
    qtbot.waitUntil(lambda: bool(platform.api.cancel_requests))
    assert host.running
    qtbot.waitUntil(lambda: bool(platform.disables))
    assert host.running
    platform.process_exited('extension.test')
    assert not host.running
    assert host.status.startswith('任务超时')


def test_legacy_natural_completion_clears_its_timeout(qtbot, tmp_path):
    host, platform = setup_host(tmp_path)
    accepted(host, platform)
    terminal(host, platform)
    assert not host.running
    assert host._legacy_actions == {}
    assert host.status.startswith('扩展动作已完成')
    assert platform.disables == []


def test_new_host_action_retains_claim_and_execution_timers(qtbot, tmp_path):
    host, platform = setup_host(tmp_path)
    host._validate_extension = lambda descriptor: None
    binding = SimpleNamespace(
        name='新动作',
        pending={'extension_id': 'extension.test', 'action_id': 'run'},
    )
    results = []
    host._run_extension(
        binding,
        automation_manual_test_event(device_serial='TEST', prompt_id=None),
        results.append,
    )
    assert host._extension_timer.interval() == 700
    invocation = platform.api.invocation.invocation_id
    result = ActionInvocationResult(invocation, 'accepted', '接受')
    platform.api.claim(result)
    host.extension_result('extension.test', result)
    assert host._extension_timer.interval() == 60_000
    assert host._legacy_actions == {}
    host.cancel_run()
    terminal(host, platform, invocation, status='failed')
    assert not host.running
    assert len(results) == 1 and not results[0].succeeded
    qtbot.wait(550)
    assert platform.disables == []


def test_stop_waits_for_real_runner_exit_after_socket_disconnect(qtbot, tmp_path):
    class ProcessPlatform(QObject):
        changed = Signal()
        disable_async = ExtensionPlatformController.disable_async

        def __init__(self):
            super().__init__()
            self.api = FakeApi()
            self.process = QProcess(self)
            self._manager = SimpleNamespace(disable=lambda extension_id: None)
            self._action_coordinator = SimpleNamespace(cancel_extension=lambda *args: None)
            self._runtimes = {'extension.test': SimpleNamespace(
                process=self.process, set_enabled=lambda enabled: None,
            )}

    host, _ = setup_host(tmp_path)
    platform = ProcessPlatform()
    host.vm.extension_platform = platform
    ready = []
    platform.process.readyReadStandardOutput.connect(
        lambda: ready.append(bytes(platform.process.readAllStandardOutput()))
    )
    platform.process.start(sys.executable, ['-c',
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(30)",
    ])
    try:
        qtbot.waitUntil(lambda: bool(ready))
        accepted(host, platform)
        host.cancel_run()
        qtbot.waitUntil(lambda: host._stopping_extensions.get('extension.test', ('', False))[1])
        assert platform.process.state() != QProcess.NotRunning
        platform.api.actions.clear()
        terminal(host, platform)
        assert host.running
        assert '已完成' not in host.status
        qtbot.waitUntil(lambda: platform.process.state() == QProcess.NotRunning)
        assert not host.running
        assert host.status.startswith('已停止任务')
    finally:
        if platform.process.state() != QProcess.NotRunning:
            platform.process.kill()
            platform.process.waitForFinished(3000)
