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
        layout.addWidget(QLabel(tr("Claude Code 状态联动"), objectName="inspectorTitle"))
        description = QLabel(
            tr("用状态灯显示 Claude Code 的任务进度。")
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel(objectName="claudeStatusSummary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        details_toggle = QPushButton(tr("更多信息"), objectName="claudeStatusDetailsToggle")
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
            tr(
                "只传事件与进程身份，不传提示词、回复正文或工具参数。USB 与蓝牙共用当前已认证的设备连接；"
                "蓝牙链路待实机验收，Windows 原生和 WSL 待独立验证。120 秒无新事件会清除陈旧状态，"
                "长时间静默任务可能提前熄灯；缺少可靠结束信号时不会显示完成。"
            ),
            objectName="muted",
        )
        note.setWordWrap(True)
        details_layout.addWidget(note)
        buttons = QHBoxLayout()
        self.enable_button = QPushButton(tr("启用状态联动…"), objectName="enableClaudeStatus")
        self.enable_button.setProperty("buttonRole", "primary")
        self.retry_button = QPushButton(tr("重试"), objectName="retryClaudeStatus")
        self.retry_button.setProperty("buttonRole", "primary")
        self.manage_button = QPushButton(tr("管理联动"), objectName="manageClaudeStatus")
        self.manage_button.setProperty("buttonRole", "secondary")
        self.manage_button.setCheckable(True)
        for button in (self.enable_button, self.retry_button, self.manage_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.management = QFrame(objectName="claudeStatusManagement")
        management_buttons = QHBoxLayout(self.management)
        management_buttons.setContentsMargins(0, 0, 0, 0)
        self.detect = QPushButton(tr("检测 Claude Code"), objectName="detectClaudeStatus")
        self.disable_button = QPushButton(tr("禁用并移除联动"), objectName="disableClaudeStatus")
        for button in (self.detect, self.disable_button):
            button.setProperty("buttonRole", "secondary")
            management_buttons.addWidget(button)
        management_buttons.addStretch(1)
        self.management.hide()
        self.manage_button.toggled.connect(self.management.setVisible)
        layout.addWidget(self.management)
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
        problem = bool(inspection and inspection.problem)
        fault = bool(getattr(self.bridge, "_fault", False))
        self.enable_button.setVisible(not self.bridge.enabled and not problem)
        self.enable_button.setEnabled(not self.bridge.enabled and not problem)
        retry_visible = self.bridge.enabled and fault and not problem
        self.retry_button.setVisible(retry_visible)
        self.retry_button.setEnabled(retry_visible)
        manage_visible = problem or (self.bridge.enabled and not fault)
        self.manage_button.setVisible(manage_visible)
        if not manage_visible:
            self.manage_button.setChecked(False)
        if problem:
            self.manage_button.setText(tr("处理问题"))
            self.manage_button.setChecked(True)
        else:
            self.manage_button.setText(tr("管理联动"))
        self.detect.setVisible(problem or self.manage_button.isChecked())
        self.detect.setText(tr("重新检测") if problem else tr("检测 Claude Code"))
        self.disable_button.setVisible(
            self.bridge.enabled or bool(inspection and inspection.installed)
        )
        self.disable_button.setEnabled(
            self.bridge.enabled or bool(inspection and inspection.installed)
        )
        self.management.setVisible(
            self.manage_button.isVisible() and self.manage_button.isChecked()
        )
