"""Device-bound computer tasks, independent of the prompt library."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QSaveFile, QStandardPaths, QTimer, Signal

from controller_config.automation import LocalScriptAutomation
from controller_config.device_events import DeviceEvent, HOST_ACTION_EVENT, HOST_ACTION_SOURCE
from controller_config.official_controls import MATRIX12_HARDWARE_IDS
from controller_config.protocol.bootstrap import Command
from controller_config.workflows import LocalWorkflow


@dataclass(frozen=True)
class HostTaskBinding:
    action_id: int
    provider: str
    target_id: str
    name: str
    profile_id: int
    control_id: str
    task_token: str
    applied: bool = False
    tested: bool = False
    pending: dict | None = None


class HostTasks(QObject):
    changed = Signal()

    def __init__(self, view_model, execute, *, directory: Path | None = None, parent=None):
        super().__init__(parent)
        self.preferred_control = None
        self.vm = view_model
        self._execute = execute
        self.directory = directory or Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)) / 'host-tasks'
        self.serial = ''
        self.snapshot = None
        self.bindings: tuple[HostTaskBinding, ...] = ()
        self.status = ''
        self.load_error = ''
        self._trial = None
        self._in_flight = False
        self._last_event = None
        self._paused = False
        self._extension = None
        self._extension_completion = None
        self._legacy_actions: dict[str, tuple[str, QTimer]] = {}
        # extension -> (stop reason, process termination already requested)
        self._stopping_extensions: dict[str, tuple[str, bool]] = {}
        self._extension_timer = QTimer(self)
        self._extension_timer.setSingleShot(True)
        self._extension_timer.timeout.connect(lambda: self.cancel_run('任务超时，已停止扩展'))
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._poll_event)

    @property
    def extension_running(self):
        return self._extension is not None or bool(self._legacy_actions) or bool(self._stopping_extensions)

    @property
    def extensions_busy(self):
        platform = self.vm.extension_platform
        return self.extension_running or bool(getattr(platform.api, "active_action_count", 0) if platform else 0)

    @property
    def running(self):
        platform = self.vm.extension_platform
        active_extensions = getattr(platform.api, "active_action_count", 0) if platform else 0
        return self.vm.workflow_host.running or self.vm.automation_host.running or self.extension_running or bool(active_extensions)

    @property
    def problem(self):
        s = self.snapshot
        if s is None:
            return '请先连接设备'
        if self.load_error:
            return self.load_error
        if s.connection_kind != 'usb':
            return '电脑任务请用 USB 连接'
        if s.capabilities.get('features', {}).get('host_action_usb') is not True:
            return '当前固件尚不支持独立电脑任务，请安装支持此功能的固件'
        if s.is_read_only:
            return '设备当前只读'
        if s.status.get('operating_mode') != 'normal':
            return '电脑任务仅用于普通模式；当前模式保留官方功能'
        return ''

    @property
    def controls(self):
        s = self.snapshot
        if s is None or s.identity.get('hardware_id') not in MATRIX12_HARDWARE_IDS:
            return ()
        controls = self.vm.draft.controls if self.vm.draft else ()
        return tuple((f'功能键 {n}', f'key.{n}') for n in range(8, 13) if f'key.{n}' in controls)

    def binding_for(self, provider, target_id):
        return next((b for b in self.bindings if b.provider == provider and b.target_id == target_id), None)

    def _path(self):
        serial = ''.join(c if c.isalnum() or c in '-_' else '_' for c in self.serial)
        return self.directory / f'{serial}.json'

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        out = QSaveFile(str(self._path()))
        data = json.dumps({'version': 1, 'bindings': [asdict(b) for b in self.bindings]}, ensure_ascii=False).encode()
        if not out.open(QSaveFile.WriteOnly) or out.write(data) != len(data) or not out.commit():
            raise ValueError('无法保存本机任务绑定：' + out.errorString())

    def _put(self, binding):
        previous = self.bindings
        self.bindings = tuple(b for b in previous if b.action_id != binding.action_id) + (binding,)
        try:
            self._save()
        except Exception:
            self.bindings = previous
            raise
        self.changed.emit()

    def attach(self, snapshot, *, readback=False):
        self.snapshot = snapshot
        if snapshot is None:
            self._poll.stop()
            self._in_flight = False
            self._last_event = None
            self.cancel_trial()
            return
        serial = str(snapshot.identity.get('serial', ''))
        if serial != self.serial:
            self.cancel_trial()
            self._last_event = None
            self.serial = serial
            self.bindings = ()
            self.load_error = ''
            try:
                data = json.loads(self._path().read_text())
                if data.get('version') != 1:
                    raise ValueError('本机任务版本不受支持')
                self.bindings = tuple(HostTaskBinding(**b) for b in data['bindings'])
            except FileNotFoundError:
                pass
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.load_error = f'无法读取本机任务：{exc}'
        try:
            for binding in tuple(self.bindings):
                if readback and binding.pending is not None and not binding.applied and self._candidate_matches(binding):
                    self._commit_pending(binding)
                elif readback and binding.applied and not self._mapping_matches(binding):
                    self._put(replace(binding, applied=False))
        except (ValueError, OSError) as exc:
            self.status = f'设备已读回，本机任务仍待保存：{exc}'
        if not self.problem and not self._paused and not self._poll.isActive() and not self._in_flight:
            self._poll.start(100)
        self.changed.emit()

    def _mapping_matches(self, b):
        if self.snapshot is None:
            return False
        return any(p.get('id') == b.profile_id and any(
            m.get('control_id') == b.control_id and m.get('action') == self._device_action(b)
            for m in p.get('mappings', [])) for p in self.snapshot.config.get('profiles', []))

    @staticmethod
    def _device_action(b):
        return {'type': 'host_action', 'action_id': b.action_id, 'task_token': b.task_token}

    def _candidate_matches(self, b):
        return self._mapping_matches(b) and any(p.get('id') == b.profile_id and any(
            m.get('control_id') == b.control_id and m.get('short_name') == b.name[:24]
            for m in p.get('mappings', [])) for p in self.snapshot.config.get('profiles', []))

    def apply(self, provider, definition, control_id):
        if self.problem:
            raise ValueError(self.problem)
        if self.running:
            raise ValueError('请等待当前任务结束或先停止')
        if control_id not in dict((value, label) for label, value in self.controls):
            raise ValueError('这颗键不支持电脑任务')
        draft = self.vm.draft
        target_id = (definition.workflow_id if provider == 'workflow' else definition.automation_id if provider == 'script' else definition['target_id'])
        if provider not in {'workflow', 'script', 'extension'}:
            raise ValueError('未知电脑任务类型')
        existing = self.binding_for(provider, target_id)
        if draft.is_dirty and not (existing and existing.pending is not None):
            raise ValueError('请先应用或放弃其他按键修改，再绑定电脑任务')
        if provider == 'extension':
            self._validate_extension(definition)
            payload = dict(definition)
        else:
            definition = replace(definition, trigger_prompt_id=None, enabled=False)
            definition.validate()
            payload = definition.as_mapping()
        used = {b.action_id for b in self.bindings}
        used.update(m['action']['action_id'] for p in self.snapshot.config.get('profiles', []) for m in p.get('mappings', [])
                    if m.get('action', {}).get('type') == 'host_action')
        limit = min(255, self.snapshot.capabilities.get('limits', {}).get('host_actions', 0))
        action_id = existing.action_id if existing else next((i for i in range(1, limit + 1) if i not in used), None)
        if action_id is None:
            raise ValueError('设备电脑任务位置已用完')
        name = definition.name if provider != 'extension' else definition['name']
        tested = False
        if existing and existing.tested and existing.control_id == control_id and existing.profile_id == self.snapshot.active_profile_id:
            previous = None
            if provider == 'workflow':
                previous = next((d.as_mapping() for d in self.vm.workflow_host.workflows if d.workflow_id == target_id), None)
            elif provider == 'script':
                previous = next((d.as_mapping() for d in self.vm.automation_host.definitions if d.automation_id == target_id), None)
            else:
                previous = existing.pending
            def content(value):
                return {k: v for k, v in (value or {}).items() if k not in {'name', 'enabled', 'tested', 'review_required'}}
            tested = content(previous) == content(payload)
        b = HostTaskBinding(action_id, provider, target_id, name, self.snapshot.active_profile_id, control_id, existing.task_token if existing else uuid.uuid4().hex, tested=tested, pending=payload)
        self._put(b)
        if existing and (existing.control_id != control_id or existing.profile_id != b.profile_id):
            old = draft.mapping(existing.profile_id, existing.control_id)
            if old and old.get('action') == self._device_action(existing):
                self.vm.set_mapping(existing.profile_id, existing.control_id, '', {'type': 'none'})
        self.vm.set_mapping(b.profile_id, control_id, name[:24], self._device_action(b))
        self.status = '正在应用；设备读回后可以试用'
        if draft.is_dirty:
            self.vm.prepare_device_write(confirm_after_validation=True)
        else:
            self._commit_pending(b)
        self.changed.emit()
        return self.binding_for(provider, target_id)

    def _commit_pending(self, b):
        if b.provider == 'workflow':
            self.vm.workflow_host.save_workflow(replace(LocalWorkflow.from_mapping(b.pending), tested=b.tested, enabled=b.tested))
        elif b.provider == 'script':
            d = LocalScriptAutomation.from_mapping(b.pending)
            self.vm.automation_host.save_definition(automation_id=d.automation_id, name=d.name, trigger_prompt_id=None,
                script_path=d.script_path, enabled=False, timeout_ms=d.timeout_ms)
        # Extension descriptors remain here: the extension manager owns the package.
        updated = replace(b, applied=True, pending=b.pending if b.provider == 'extension' else None)
        self._put(updated)
        self.status = '已应用，可以使用' if b.tested else '已应用，请按“开始实体试用”后按一下设备键'

    def arm_trial(self, action_id):
        if self.problem:
            raise ValueError(self.problem)
        b = next((b for b in self.bindings if b.action_id == action_id), None)
        if b is None or not b.applied or not self._mapping_matches(b):
            raise ValueError('请先应用到设备并完成读回')
        if self.snapshot.active_profile_id != b.profile_id:
            raise ValueError('请先切换到绑定的设备配置')
        if self.running:
            raise ValueError('当前已有任务正在运行')
        self._trial = action_id
        self.status = f'请按一下{b.control_id.replace("key.", "功能键 ")}，完成首次试用'
        self.changed.emit()

    def cancel_trial(self):
        if self._trial is not None:
            self._trial = None
            self.status = '试用已暂停，可稍后继续'
            self.changed.emit()

    def set_paused(self, paused):
        self._paused = paused
        if paused:
            self._poll.stop()
        elif not self.problem and not self._in_flight:
            self._poll.start(100)

    def _poll_event(self):
        if self.problem or self._paused or self._in_flight:
            return
        self._in_flight = True
        self._execute(Command('GET_HOST_ACTION_EVENT', 0x1E, {}))

    def completed(self, command, payload):
        if command != 'GET_HOST_ACTION_EVENT':
            return False
        if not self._in_flight:
            return True
        self._in_flight = False
        try:
            result = payload['result']
            raw = result.get('event')
            if raw is not None:
                event = DeviceEvent(HOST_ACTION_EVENT, HOST_ACTION_SOURCE, self.serial, raw['event_id'], {'action_id': raw['action_id'], 'task_token': raw['task_token']})
                if event.event_id != self._last_event:
                    self._last_event = event.event_id
                    self.dispatch(event)
        except (ValueError, KeyError, TypeError) as exc:
            self.status = f'电脑任务事件无效：{exc}'
            self.changed.emit()
        if not self.problem and not self._paused:
            self._poll.start(100)
        return True

    def failed(self, command, error):
        if command != 'GET_HOST_ACTION_EVENT':
            return False
        self._in_flight = False
        self.status = f'电脑任务监听中断：{error}'
        self.changed.emit()
        if not self.problem and not self._paused:
            self._poll.start(1000)
        return True

    def dispatch(self, event):
        if self.problem or event.device_serial != self.serial:
            return
        b = next((b for b in self.bindings if b.action_id == event.payload['action_id'] and b.task_token == event.payload['task_token']), None)
        if b is None:
            self.status = '此电脑尚未设置这个任务'
        elif not b.applied or not self._mapping_matches(b) or self.snapshot.active_profile_id != b.profile_id:
            self.status = '任务配置尚待应用或确认'
        elif self.running:
            self.status = '当前任务正在运行，不会重复执行'
        elif not b.tested and self._trial != b.action_id:
            self.status = '请先在任务设置中开始实体试用'
        else:
            trial = self._trial == b.action_id
            self._trial = None
            serial = self.serial
            def finished(result):
                if self.serial != serial:
                    return
                success = bool(getattr(result, 'succeeded', getattr(result, 'success', False)))
                if hasattr(result, 'exit_code'):
                    success = result.exit_code == 0 and not result.cancelled and not getattr(result, 'timed_out', False)
                if getattr(result, 'timed_out', False):
                    self.status = f'任务超时：{b.name}，已完成的操作不会撤销'
                elif getattr(result, 'cancelled', False):
                    self.status = f'已停止：{b.name}，已完成的操作不会撤销'
                elif success and b.provider == 'workflow':
                    self.status = result.message
                elif success and b.provider == 'script':
                    self.status = f'脚本已正常退出：{b.name}，请检查输出结果'
                else:
                    self.status = (f'已完成：{b.name}' if success else f'未完成：{b.name} · ' + str(getattr(result, 'message', '') or getattr(result, 'standard_error', '') or '请查看运行详情'))
                if success and trial:
                    try:
                        if b.provider == 'workflow':
                            d = next(d for d in self.vm.workflow_host.workflows if d.workflow_id == b.target_id)
                            self.vm.workflow_host.save_workflow(replace(d, tested=True, enabled=True))
                        self._put(replace(b, tested=True))
                    except (ValueError, OSError, StopIteration) as exc:
                        self.status = f'试用完成，但启用保存失败：{exc}'
                self.changed.emit()
            self.status = f'正在运行：{b.name}'
            try:
                if b.provider == 'workflow':
                    d = next(d for d in self.vm.workflow_host.workflows if d.workflow_id == b.target_id)
                    self.vm.workflow_host.run_definition(d, event, finished)
                elif b.provider == 'script':
                    d = next(d for d in self.vm.automation_host.definitions if d.automation_id == b.target_id)
                    self.vm.automation_host.launch_definition(d, event, finished)
                else:
                    self._run_extension(b, event, finished)
            except (ValueError, RuntimeError, StopIteration) as exc:
                self.status = f'无法运行 {b.name}：{exc or "此电脑缺少任务内容"}'
        self.changed.emit()

    def _validate_extension(self, descriptor):
        platform = self.vm.extension_platform
        if platform is None:
            raise ValueError('扩展服务尚未启动')
        ext = platform.manager.get(descriptor['extension_id'])
        if ext.manifest.api_version.minor < 1:
            raise ValueError('此扩展尚未声明支持独立电脑任务，请更新扩展')
        if descriptor['action_id'] not in ext.manifest.declared_action_ids:
            raise ValueError('扩展中找不到这个动作')
        if not platform.extension_is_ready(ext.manifest.extension_id):
            raise ValueError('请先启用扩展并等待连接完成')

    def _run_extension(self, b, event, finished):
        from controller_config.extensions.contracts import ActionInvocation, SemanticEvent
        self._validate_extension(b.pending)
        platform = self.vm.extension_platform
        invocation_id = uuid.uuid4().hex
        self._extension = (b.pending['extension_id'], invocation_id)
        self._extension_completion = finished
        invocation = ActionInvocation(invocation_id, b.pending['extension_id'], b.pending['action_id'], self.serial,
                                      self.vm.extension_context_revision, SemanticEvent.from_mapping(event.as_mapping()))
        self._extension_timer.start(700)
        def claimed(result):
            if self._extension is None:
                return
            if result.status == 'accepted':
                self.status = f'正在运行：{b.name}'
                self._extension_timer.start(int(b.pending.get('timeout_ms', 60000)))
                self.changed.emit()
            else:
                self._finish_extension(False, result.message or '扩展未接受任务')
        if not platform.api.invoke_action(invocation, claimed):
            self._finish_extension(False, '扩展未连接')

    def extension_result(self, extension_id, result):
        stopping = self._stopping_extensions.get(extension_id)
        if self._extension == (extension_id, result.invocation_id):
            if stopping:
                self._extension_timer.stop()
            if result.status in {'completed', 'failed'}:
                if stopping and stopping[1]:
                    return  # A terminal report cannot prove the runner exited.
                self._finish_extension(
                    result.status == 'completed' and not stopping,
                    stopping[0] if stopping else result.message,
                )
                if stopping:
                    self._finish_cooperative_stop(extension_id, stopping[0])
            return
        if result.status == 'accepted':
            if result.invocation_id not in self._legacy_actions:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(
                    lambda: self._request_extension_stop(extension_id, '任务超时，扩展动作已停止；已完成的操作不会撤销')
                )
                self._legacy_actions[result.invocation_id] = (extension_id, timer)
                if not stopping:
                    timer.start(60_000)
                    self.status = f'正在运行扩展动作：{extension_id}'
            self.changed.emit()
        elif result.status in {'completed', 'failed'}:
            active = self._legacy_actions.get(result.invocation_id)
            if active is None or active[0] != extension_id:
                return
            if stopping and stopping[1]:
                return
            self._remove_legacy_action(result.invocation_id)
            if stopping:
                self._finish_cooperative_stop(extension_id, stopping[0])
            else:
                self.status = ('扩展动作已完成' if result.status == 'completed' else '扩展动作未完成') + f'：{result.message or extension_id}'
                self.changed.emit()

    def _remove_legacy_action(self, invocation_id):
        active = self._legacy_actions.pop(invocation_id, None)
        if active is not None:
            active[1].stop()
            active[1].deleteLater()

    def _finish_cooperative_stop(self, extension_id, message):
        remaining = (self._extension and self._extension[0] == extension_id) or any(
            active[0] == extension_id for active in self._legacy_actions.values()
        )
        if not remaining:
            self._stopping_extensions.pop(extension_id, None)
        self.status = '正在等待其余扩展动作停止…' if self._stopping_extensions else message
        self.changed.emit()

    def _finish_extension(self, success, message):
        from types import SimpleNamespace
        self._extension_timer.stop()
        callback = self._extension_completion
        self._extension = None
        self._extension_completion = None
        if callback:
            callback(SimpleNamespace(succeeded=success, message=message))

    def cancel_run(self, message='已停止任务；已完成的操作不会撤销'):
        self.vm.workflow_host.cancel_run()
        self.vm.automation_host.cancel_run()
        platform = self.vm.extension_platform
        extensions = set(platform.api.active_extension_ids if platform else ())
        extensions.update(active[0] for active in self._legacy_actions.values())
        extensions.update(self._stopping_extensions)
        if self._extension is not None:
            extensions.add(self._extension[0])
        for extension_id in extensions:
            self._request_extension_stop(extension_id, message)
        self.changed.emit()

    def _request_extension_stop(self, extension_id, message):
        if extension_id in self._stopping_extensions:
            return
        platform = self.vm.extension_platform
        if platform is None:
            return
        stop_request = (message, False)
        self._stopping_extensions[extension_id] = stop_request
        invocations = [
            invocation_id for invocation_id, active in self._legacy_actions.items()
            if active[0] == extension_id
        ]
        for invocation_id in invocations:
            self._legacy_actions[invocation_id][1].stop()
        if self._extension and self._extension[0] == extension_id:
            self._extension_timer.stop()
            invocations.append(self._extension[1])
        self.status = '正在请求扩展停止；若未响应将停用该扩展的全部功能…'
        for invocation_id in invocations:
            platform.api.request_cancel_invocation(invocation_id)

        def stop_if_needed():
            pending = self._stopping_extensions.get(extension_id)
            if pending is not stop_request:
                return
            self._stopping_extensions[extension_id] = (pending[0], True)
            self.status = '正在停用扩展，等待运行进程退出…'
            self.changed.emit()
            try:
                platform.disable_async(extension_id, lambda: self.extension_stopped(extension_id))
            except (ValueError, OSError) as exc:
                self._stopping_extensions.pop(extension_id, None)
                self.status = f'无法停止扩展，请重试：{exc}'
                self.changed.emit()

        QTimer.singleShot(500, self, stop_if_needed)
        self.changed.emit()

    def extension_stopped(self, extension_id):
        stopped = self._stopping_extensions.pop(extension_id, None)
        message = stopped[0] if stopped else '扩展已停止，任务未确认完成'
        had_action = False
        platform = self.vm.extension_platform
        for invocation_id, active in tuple(self._legacy_actions.items()):
            if active[0] != extension_id:
                continue
            had_action = True
            platform.api.cancel_invocation(invocation_id)
            self._remove_legacy_action(invocation_id)
        if self._extension and self._extension[0] == extension_id:
            had_action = True
            platform.api.cancel_invocation(self._extension[1])
            self._finish_extension(False, message)
        if had_action or stopped:
            self.status = '正在等待其余扩展动作停止…' if self._stopping_extensions else message
            self.changed.emit()

    def ensure_not_bound(self, provider, target_id):
        if self.binding_for(provider, target_id):
            raise ValueError('这个任务仍绑定设备键，请先在按键配置中更换动作，再解除本机绑定')

    def remove_binding(self, action_id):
        b = next(b for b in self.bindings if b.action_id == action_id)
        if self._mapping_matches(b):
            raise ValueError('请先在按键配置中更换这颗键的动作并应用')
        previous = self.bindings
        self.bindings = tuple(b for b in self.bindings if b.action_id != action_id)
        try:
            self._save()
        except Exception:
            self.bindings = previous
            raise
        self.changed.emit()
