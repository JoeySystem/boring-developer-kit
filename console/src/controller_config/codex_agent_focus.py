"""Fresh physical Codex Agent presses may foreground the desktop client."""
from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QSettings, QTimer, Signal

from controller_config.ai_setup import installed_desktop_app
from controller_config.models import DeviceSnapshot
from controller_config.protocol.contract import validate_agent_press


AGENT_CLIENT_KEY = "ui/agent_desktop_client"


def installed_agent_clients():
    return {name: path for name in ("Codex", "ChatGPT")
            if (path := installed_desktop_app(name)) is not None}


class CodexAgentFocus(QObject):
    """Observe GET_STATUS, never consume Vendor HID or select a task ourselves."""

    requested = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sequence: int | None = None
        self._transport: str | None = None
        self._supports_normal = False
        self._normal_open = False
        self._normal_baseline_pending = False

    def unbind(self) -> None:
        self._sequence = None
        self._transport = None
        self._supports_normal = False
        self._normal_open = False
        self._normal_baseline_pending = False

    def bind(self, snapshot: DeviceSnapshot) -> None:
        self.unbind()
        if (not snapshot.trust.is_authenticated or snapshot.is_read_only
                or snapshot.capabilities.get("features", {}).get("codex_agent_focus") is not True):
            return
        event = snapshot.status.get("codex_agent_press")
        try:
            validate_agent_press(event)
        except ValueError:
            return
        self._transport = "ble" if snapshot.connection_kind == "bluetooth" else "usb"
        self._supports_normal = (
            snapshot.capabilities.get("features", {}).get("normal_agent_key_behavior") is True
        )
        # Bootstrap/readback is a baseline, not a user action. Never replay it.
        self._sequence = event["sequence"]

    def set_normal_behavior(self, value: str | None, *, status: dict | None = None) -> None:
        """Apply a live GET result, never an unverified draft or SET acknowledgement."""
        enabled = self._supports_normal and value == "open_conversation"
        if enabled and not self._normal_open:
            # GET and GET_STATUS are separate reads. Drain the first Normal status
            # after enabling so an event from before confirmation cannot steal focus.
            self._normal_baseline_pending = True
        elif not enabled:
            self._normal_baseline_pending = False
        self._normal_open = enabled
        if enabled and status is not None:
            event = status.get("codex_agent_press")
            validate_agent_press(event)
            self._sequence = event["sequence"]
            self._normal_baseline_pending = False

    def consume(self, status: dict, *, allowed: bool = True) -> None:
        if self._transport is None:
            return
        event = status.get("codex_agent_press")
        try:
            validate_agent_press(event)
        except ValueError:
            self.unbind()
            return
        previous, self._sequence = self._sequence, event["sequence"]
        mode = status.get("operating_mode")
        if mode == "normal" and self._normal_baseline_pending:
            self._normal_baseline_pending = False
            return
        if previous is None or previous == self._sequence:
            return
        # Advance even when suppressed: busy/other-mode notifications aren't queued.
        if (not allowed or event["agent"] is None or event["transport"] != self._transport
                or not (mode == "codex" or (mode == "normal" and self._normal_open))):
            return
        engine = status.get("action_engine", {})
        capture = status.get("diagnostic_capture", {})
        if (engine.get("local_page", "none") != "none"
                or engine.get("usb_standby_active") is True
                or capture.get("active") is True
                or capture.get("waiting_for_neutral") is True):
            return
        self.requested.emit(event["agent"])


class MacChatGPTActivator(QObject):
    """LaunchServices reopen/activate; no AppleScript, key injection or always-on-top."""

    failed = Signal(str)

    def __init__(self, parent: QObject | None = None, *, settings=None) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings()
        self._process = QProcess(self)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(5000)
        self._deadline.timeout.connect(self._timeout)
        self._pending = False

    def activate(self, _agent: int = 0) -> None:
        if self._pending or self._process.state() != QProcess.ProcessState.NotRunning:
            return
        clients = installed_agent_clients()
        selected = self._settings.value(AGENT_CLIENT_KEY, "", type=str)
        if selected and selected not in clients:
            self.failed.emit("找不到已选择的桌面应用，请在状态灯按键设置中重新选择。")
            return
        if not selected and len(clients) > 1:
            self.failed.emit("检测到多个桌面应用，请在状态灯按键设置中选择接收设备操作的应用。")
            return
        path = clients.get(selected) if selected else next(iter(clients.values()), None)
        if path is None:
            self.failed.emit("未找到 Codex / ChatGPT 桌面应用，请先安装到“应用程序”文件夹。")
            return
        self._pending = True
        self._deadline.start()
        # Use the same installed-app lookup as common AI setup. If both clients
        # exist, the user's explicit choice avoids opening the wrong application.
        # No -g/-j (background), -n (duplicate instance), or -F (discard window state).
        self._process.start("/usr/bin/open", ["-a", str(path)])

    def shutdown(self) -> None:
        self._pending = False
        self._deadline.stop()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()  # Only our short-lived `open` helper, never ChatGPT.
            self._process.waitForFinished(1000)

    def _fail(self, message: str) -> None:
        if not self._pending:
            return
        self._pending = False
        self._deadline.stop()
        self.failed.emit(message)

    def _finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        self._process.readAllStandardError()
        if exit_code != 0:
            self._fail("无法打开所选桌面应用，请先手动打开，再按状态灯键重试。")
        self._pending = False
        self._deadline.stop()

    def _error(self, _error: QProcess.ProcessError) -> None:
        self._fail("无法调用 macOS 应用唤起服务，请手动打开所选应用后重试。")

    def _timeout(self) -> None:
        self._fail("打开桌面应用超时，请手动打开后再按状态灯键重试。")
        self._process.kill()
