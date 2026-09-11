"""Fresh physical Codex Agent presses may foreground the desktop client."""
from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from controller_config.models import DeviceSnapshot
from controller_config.protocol.contract import validate_agent_press


class CodexAgentFocus(QObject):
    """Observe GET_STATUS, never consume Vendor HID or select a task ourselves."""

    requested = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sequence: int | None = None
        self._transport: str | None = None

    def unbind(self) -> None:
        self._sequence = None
        self._transport = None

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
        # Bootstrap/readback is a baseline, not a user action. Never replay it.
        self._sequence = event["sequence"]

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
        if previous is None or previous == self._sequence:
            return
        # Advance even when suppressed: busy/other-mode notifications aren't queued.
        if (not allowed or event["agent"] is None or event["transport"] != self._transport
                or status.get("operating_mode") != "codex"):
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

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
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
        self._pending = True
        self._deadline.start()
        # Resolve the app by name: installed ChatGPT variants don't share one bundle ID.
        # No -g/-j (background), -n (duplicate instance), or -F (discard window state).
        self._process.start("/usr/bin/open", ["-a", "ChatGPT"])

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
            self._fail("无法唤起 ChatGPT 桌面版；请确认已安装并可手动打开，然后重新按 Agent 键。")
        self._pending = False
        self._deadline.stop()

    def _error(self, _error: QProcess.ProcessError) -> None:
        self._fail("无法调用 macOS 应用唤起服务；请手动打开 ChatGPT 后重试。")

    def _timeout(self) -> None:
        self._fail("唤起 ChatGPT 超时；请手动打开后重新按 Agent 键。")
        self._process.kill()
