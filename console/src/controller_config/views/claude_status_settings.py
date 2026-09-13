from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QMessageBox
from controller_config.i18n import translate_ui_text as tr
from controller_config.claude_sessions import CLAUDE_STATUS_KEYS


class ClaudeStatusSettings(QFrame):
    """Opt-in Hooks control, kept in Settings rather than a new top-level page."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        self.bridge = bridge
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 17, 16, 17)
        layout.addWidget(QLabel("Claude Code 状态联动", objectName="inspectorTitle"))
        description = QLabel(
            "开发中 / 待实机联调。通过当前已认证连接将 Claude Code 的执行、审批、待回答与本轮结束状态显示在六颗状态键上。"
            "启用后请重新打开 Claude Code 会话；关闭控制台窗口可继续后台运行，退出后台则停止联动。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel(objectName="claudeStatusSummary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        details_toggle = QPushButton("技术详情", objectName="claudeStatusDetailsToggle")
        details_toggle.setCheckable(True)
        layout.addWidget(details_toggle)
        details = QFrame(objectName="claudeStatusTechnical")
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details.hide()
        details_toggle.toggled.connect(details.setVisible)
        layout.addWidget(details)
        self.detail = QLabel(objectName="claudeStatusDetail")
        self.detail.setWordWrap(True)
        details_layout.addWidget(self.detail)
        self.sessions = QLabel(objectName="claudeStatusSessions")
        self.sessions.setWordWrap(True)
        details_layout.addWidget(self.sessions)
        note = QLabel(
            "只传事件与进程身份，不传提示词、回复正文或工具参数。USB 与蓝牙共用当前已认证的设备连接；"
            "蓝牙链路待实机验收，Windows 原生和 WSL 待独立验证。120 秒无新事件会清除陈旧状态，"
            "长时间静默任务可能提前熄灯；缺少可靠结束信号时不会显示完成。",
            objectName="muted",
        )
        note.setWordWrap(True)
        details_layout.addWidget(note)
        buttons = QHBoxLayout()
        self.detect = QPushButton("检测 Claude Code", objectName="detectClaudeStatus")
        self.enable_button = QPushButton("启用状态联动…", objectName="enableClaudeStatus")
        self.disable_button = QPushButton("禁用并移除联动", objectName="disableClaudeStatus")
        self.retry_button = QPushButton("重试状态转发", objectName="retryClaudeStatus")
        for button in (self.detect, self.enable_button, self.disable_button, self.retry_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.detect.clicked.connect(lambda: self._run(bridge.inspect))
        self.enable_button.clicked.connect(self._enable)
        self.disable_button.clicked.connect(lambda: self._run(bridge.disable))
        self.retry_button.clicked.connect(bridge.retry)
        bridge.changed.connect(self.refresh)
        self.refresh()

    def _run(self, action):
        try:
            action()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, tr("Claude Code 状态联动"), tr(str(exc)))
        self.refresh()

    def _enable(self):
        answer = QMessageBox.question(
            self, tr("启用 Claude Code 状态联动"),
            tr("将合并 BORING Hooks 到本机 Claude Code 设置，保留已有配置。只上报状态，不批准权限或替你操作。是否启用？"),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            self._run(self.bridge.enable)

    def refresh(self):
        inspection = self.bridge.inspection
        summary = [tr(self.bridge.message)]
        if inspection and inspection.problem:
            summary.append(tr(inspection.problem))
        self.status.setText("\n".join(summary))
        lines = [tr(self.bridge.message)]
        if inspection is not None:
            lines.extend(filter(None, [inspection.executable, inspection.version,
                *(tr(note) for note in inspection.note.splitlines()), tr(inspection.problem)]))
        self.detail.setText("\n".join(lines))
        rows = []
        labels = {"idle": "空闲", "working": "执行中", "completed": "本轮回复结束",
                  "approval": "等待审批", "reply": "等待回答", "error": "本轮终止错误"}
        for session in self.bridge.registry.sessions:
            key = CLAUDE_STATUS_KEYS[session.slot].replace("key.", "Key")
            rows.append(f"{key} · {session.session_id[:12]} · {tr(labels[session.state])}"
                        + (f" · {tr('无新事件，已清除陈旧状态')}" if session.stale else ""))
        for session in self.bridge.registry.overflow:
            rows.append(f"{tr('未映射（六槽已满）')} · {session.session_id[:12]}")
        self.sessions.setText("\n".join(rows) or tr("尚无 Claude Code 会话"))
        self.enable_button.setEnabled(not self.bridge.enabled)
        self.disable_button.setEnabled(self.bridge.enabled or bool(inspection and inspection.installed))
        self.retry_button.setEnabled(self.bridge.enabled)
