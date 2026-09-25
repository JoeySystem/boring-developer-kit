"""Device-wide NORMAL status-key preference, confirmed by independent readback."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from controller_config.protocol.bootstrap import Command

NORMAL_AGENT_BEHAVIOR_GET = 0x2A
NORMAL_AGENT_BEHAVIOR_SET = 0x2B
BEHAVIORS = ("open_conversation", "status_only")
_COMMANDS = {"NORMAL_AGENT_BEHAVIOR_GET", "NORMAL_AGENT_BEHAVIOR_SET"}


@dataclass
class _Choice:
    value: str | None = None
    draft: str | None = None
    expected: str | None = None

    @property
    def dirty(self):
        return self.expected is not None or self.draft != self.value


class NormalAgentSession(QObject):
    changed = Signal()

    def __init__(self, send, block_reason, parent=None):
        super().__init__(parent)
        self._send = send
        self._block_reason = block_reason
        self._choices = {}
        self._choice = _Choice()
        self._key = None
        self._command = ""
        self.preparing = False
        self._ready_message = ""
        self.connected = self.supported = self.loaded = False
        self.message = self.technical = ""

    @property
    def busy(self):
        return bool(self._command) or self.preparing

    @property
    def value(self):
        return self._choice.value

    @property
    def draft(self):
        return self._choice.draft

    @property
    def dirty(self):
        return self._choice.dirty

    @property
    def uncertain(self):
        return self._choice.expected is not None and not self.busy

    def pending_drafts(self):
        return tuple((key[0], item.draft) for key, item in self._choices.items() if item.dirty)

    def attach(self, snapshot):
        self.detach()
        self._key = (str(snapshot.identity['serial']), str(snapshot.identity['hardware_id']))
        self._choice = self._choices.setdefault(self._key, _Choice())
        self.connected = snapshot.trust.is_authenticated
        self.supported = snapshot.capabilities.get('features', {}).get('normal_agent_key_behavior') is True
        self.message = "正在读取状态灯按键设置…" if self.supported else "当前固件不支持设置状态灯按键行为"
        self.changed.emit()

    def detach(self):
        self.connected = self.loaded = False
        self._command = ""
        self.preparing = False
        self.message = "连接设备后可应用"
        self.changed.emit()

    def edit(self, value):
        if self.busy:
            raise ValueError("状态灯按键设置正在保存或读回，请稍后操作")
        if value not in BEHAVIORS:
            raise ValueError("请选择按下状态灯键时的行为")
        self._choice.draft = value
        self.message = "尚未应用到设备" if self.dirty else ""
        self.technical = ""
        self.changed.emit()

    def finish_preparing(self, error: str = ""):
        self.preparing = False
        self.loaded = not bool(error)
        self.message = "跳转准备失败，请重新读取" if error else self._ready_message
        self.technical = error
        self.changed.emit()

    def _require_available(self):
        if not self.connected:
            raise ValueError("请连接并认证设备后操作")
        if not self.supported:
            raise ValueError("当前固件不支持设置状态灯按键行为")
        if self.busy:
            raise ValueError("状态灯按键设置正在保存或读回，请稍后操作")

    def read(self):
        self._require_available()
        self.message = "正在读取状态灯按键设置…"
        self.technical = ""
        self._execute('NORMAL_AGENT_BEHAVIOR_GET', NORMAL_AGENT_BEHAVIOR_GET, {})

    def save(self):
        self._require_available()
        reason = self._block_reason()
        if reason:
            raise ValueError(reason)
        if self.uncertain:
            raise ValueError("应用结果待确认，请重新读取")
        if not self.loaded:
            raise ValueError("请先读取状态灯按键设置")
        if self.draft not in BEHAVIORS:
            raise ValueError("请选择按下状态灯键时的行为")
        if not self.dirty:
            return
        self._choice.expected = self.draft
        self.message = "正在应用状态灯按键设置…"
        self.technical = ""
        self._execute('NORMAL_AGENT_BEHAVIOR_SET', NORMAL_AGENT_BEHAVIOR_SET, {'behavior': self.draft})

    def _execute(self, name, kind, payload):
        self._command = name
        self.changed.emit()
        self._send(Command(name, kind, payload))

    def completed(self, name, payload):
        if name not in _COMMANDS:
            return False
        if not self.connected or name != self._command:
            return True
        self._command = ""
        result = payload.get('result') if isinstance(payload, dict) else None
        valid = (isinstance(result, dict) and 'behavior' in result
                 and (result['behavior'] is None or result['behavior'] in BEHAVIORS))
        if name == 'NORMAL_AGENT_BEHAVIOR_SET':
            # A lost/malformed ACK can still accompany a successful write: GET decides.
            self.message = "正在读回确认应用结果…"
            self._execute('NORMAL_AGENT_BEHAVIOR_GET', NORMAL_AGENT_BEHAVIOR_GET, {})
            return True
        if not valid:
            self.loaded = False
            self.message = "设置读取失败，请重新读取"
            self.technical = "NORMAL_AGENT_BEHAVIOR_GET result is invalid"
            self.changed.emit()
            return True
        keep = self.dirty
        expected = self._choice.expected
        self._choice.value = result['behavior']
        if not keep:
            self._choice.draft = self.value
        self._choice.expected = None
        self.loaded = True
        self.technical = ""
        self.message = ("设备读回与所选行为不一致，请重新应用" if expected is not None and expected != self.value
                        else "已应用" if expected is not None
                        else "请选择按下状态灯键时的行为" if self.value is None else "")
        if self.value == "open_conversation":
            self.preparing = True
            self.loaded = False
            self._ready_message = self.message
            self.message = "正在准备状态灯按键跳转…"
        self.changed.emit()
        return True

    def failed(self, name, error):
        if name not in _COMMANDS:
            return False
        if name != self._command:
            return True
        self._command = ""
        self.technical = error.technical or str(error)
        if name == 'NORMAL_AGENT_BEHAVIOR_SET':
            if error.error_name in {'VALIDATION_FAILED', 'BUSY', 'STORAGE_FAILURE', 'INTERNAL', 'UNSUPPORTED_COMMAND'}:
                self._choice.expected = None
                self.message = ("设备正忙，请松开按键后重试" if error.error_name == 'BUSY'
                                else "设置未能保存，请重试")
            elif self.connected:
                self.message = "正在读回确认应用结果…"
                self._execute('NORMAL_AGENT_BEHAVIOR_GET', NORMAL_AGENT_BEHAVIOR_GET, {})
                return True
        else:
            self.loaded = False
            self.message = "应用结果待确认，请重新读取" if self.uncertain else "设置读取失败，请重新读取"
        self.changed.emit()
        return True
