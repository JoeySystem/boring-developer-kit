"""What the Claude 状态灯 tile cannot hold, shown in its 「了解更多」 dialog.

The switch (enable / disable the BORING Hooks) and the one fix action live on
the tile (settings_wall.ClaudeStatusTile). This panel keeps the explanation,
the full status, 「重新检测」 and 「复制诊断信息」; the program path, version,
Hooks notes and the session list go into the copied diagnostics (design decision).
"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from controller_config.views.ui import notice
from controller_config.claude_hooks import ELSEWHERE_NOTE, MISSING_TARGET_WARNING
from controller_config.claude_sessions import CLAUDE_STATUS_KEYS
from controller_config.i18n import translate_ui_text as tr
from controller_config.views.ui.copy_diagnostics import copy_diagnostics_button, diagnostics_header
from controller_config.views.ui.dialog import dialog_button

DESCRIPTION = ("用状态灯显示 Claude 的任务进度。在 Claude 桌面端的 Code 会话（本地）中生效，"
               "云端会话和普通对话暂不支持。")
HOW_IT_WORKS = (
    "只传事件与进程身份，不传提示词、回复正文或工具参数。状态灯在普通模式下和豆包、Codex 分灯显示，"
    "USB 和蓝牙连接都可以；按 Claude 的灯会把 Claude 切到前台，这颗灯记为看过。"
    "Windows 原生和 WSL 待独立验证。执行中 120 秒无新事件会转为空闲，长时间静默任务可能提前熄灯；"
    "完成、待审批和待回答会保持到该会话的下一条事件、会话结束或 Claude 进程退出，"
    "无法确认 Claude 进程时仍在 120 秒后清除。缺少可靠结束信号时不会显示完成。"
)
SESSION_STATES = {"idle": "空闲", "working": "执行中", "completed": "本轮回复结束",
                  "approval": "等待审批", "reply": "等待回答", "error": "本轮终止错误"}


def session_lines(bridge) -> list[str]:
    """One line per Claude session: its light, a short id, its state."""
    rows = []
    delivery = getattr(bridge, "delivery", None)
    registry = bridge.registry
    for session in (*registry.sessions, *registry.overflow):
        # The lights are the hub's (design decision): show the light a session
        # really has; without the hub, the registry's own slot.
        slot = delivery.slot_of(session.session_id) if delivery is not None else session.slot
        if slot is not None:
            key = CLAUDE_STATUS_KEYS[slot].replace("key.", "Key")
        elif session.state == "idle":
            key = "不占灯"
        else:
            key = "未映射（六槽已满）"
        rows.append(f"{key} · {session.session_id[:12]} · {SESSION_STATES[session.state]}"
                    + (" · 无新事件，已清除陈旧状态" if session.stale else ""))
    return rows


def claude_diagnostics(bridge) -> str:
    """The technical side of the panel, for 「复制诊断信息」."""
    inspection = bridge.inspection
    lines = [
        *diagnostics_header(),
        "",
        "[Claude 状态灯]",
        f"enabled: {bool(bridge.enabled)}",
        f"fault: {bool(getattr(bridge, 'fault', False))}",
        f"message: {bridge.message}",
    ]
    if inspection is None:
        lines.append("inspection: -")
    else:
        lines += [
            f"executable: {inspection.executable or '-'}",
            f"version: {inspection.version or '-'}",
            f"platform: {getattr(inspection, 'platform', '-')}",
            f"installed: {getattr(inspection, 'installed', '-')}",
            f"events: {', '.join(getattr(inspection, 'events', ())) or '-'}",
            f"problem: {inspection.problem or '-'}",
            f"missing_targets: {', '.join(getattr(inspection, 'missing_targets', ())) or '-'}",
            f"points_elsewhere: {getattr(inspection, 'points_elsewhere', False)}",
            *(f"note: {note}" for note in inspection.note.splitlines() if note),
        ]
    lines += ["", "[会话]", *(session_lines(bridge) or ["尚无 Claude 会话"])]
    return "\n".join(lines)


class ClaudeStatusSettings(QFrame):
    """The 「了解更多」 panel (no second switch: enabling lives on the tile)."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.setObjectName("claudeStatusDetails")
        self.bridge = bridge
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        description = QLabel(tr(DESCRIPTION), objectName="claudeStatusDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.status = QLabel(objectName="claudeStatusSummary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel(tr(HOW_IT_WORKS), objectName="muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        self.copy_diagnostics = copy_diagnostics_button(
            lambda: claude_diagnostics(self.bridge), object_name="copyClaudeDiagnostics")
        actions.addWidget(self.copy_diagnostics)
        actions.addStretch(1)
        self.detect = dialog_button("重新检测", "secondary", object_name="detectClaudeStatus")
        actions.addWidget(self.detect)
        layout.addLayout(actions)
        self.detect.clicked.connect(lambda: self._run(bridge.inspect))
        bridge.changed.connect(self.refresh)
        self.refresh()

    def _run(self, action):
        try:
            action()
        except (ValueError, OSError, RuntimeError) as exc:
            notice.notify(self, tr("Claude 状态灯"), tr(str(exc)))
        self.refresh()

    def refresh(self):
        inspection = self.bridge.inspection
        summary = [tr(self.bridge.message)]
        if inspection and inspection.problem:
            summary.append(tr(inspection.problem))
        elif getattr(inspection, "missing_targets", ()):
            summary.append(tr(MISSING_TARGET_WARNING))
        elif getattr(inspection, "points_elsewhere", False):
            summary.append(tr(ELSEWHERE_NOTE))
        self.status.setText("\n".join(summary))
