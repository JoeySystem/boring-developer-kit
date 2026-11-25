"""What the 豆包状态灯 tile cannot hold, shown in its 「详情」 dialog (Mac and Windows).

The switch and the one-line status live on the tile (settings_wall.DoubaoTile);
this panel adds the full status with its reason and next step, the
accessibility permission (rechecked when the user comes back from System
Settings) and 「复制诊断信息」 for the technical details (design decision).
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from controller_config import desktop_agent_hub as hub_codes
from controller_config import desktop_permissions as perm
from controller_config.i18n import set_translatable_text
from controller_config.viewmodels import desktop_agents as link_codes
from controller_config.views.ui.copy_diagnostics import copy_diagnostics_button, diagnostics_header
from controller_config.views.ui.permission_row import PermissionRow, PermissionWatcher

# Product-language status, one line each: what is going on · what to do next.
STATUS_TEXT = {
    link_codes.DISABLED: "已关闭 · 灯交还给 Codex，Console 不再读取豆包",
    link_codes.UNAVAILABLE_NO_DEVICE: "等待设备 · 连接 BORING 后自动开始",
    link_codes.NEEDS_FIRMWARE: "设备固件不支持豆包状态灯 · 需要先升级到支持豆包状态灯的固件",
    link_codes.PAUSED: "设备维护中，豆包状态灯暂停 · 维护结束后自动恢复",
    link_codes.FAULT: "设备没有确认灯的状态，已停止 · 检查连接后点“重试”",
    link_codes.DEVICE_BUSY: "设备暂时不接受灯的状态（只读或忙）· 正在自动重试，恢复后灯会自己回来",
    link_codes.APP_NOT_RUNNING: "豆包没有打开 · 打开豆包后自动开始",
    link_codes.SOURCE_BROKEN: "豆包状态灯暂时失效 · 读不到豆包里对话的状态（豆包可能刚更新），灯可能不准；Console 更新适配后恢复",
}
# The hub's fault reason in product language; fault_detail goes to the diagnostics.
FAULT_REASON_TEXT = {
    hub_codes.FAULT_REJECTED: "原因：设备拒绝了灯的状态，可能是固件和 Console 版本不匹配",
    hub_codes.FAULT_NO_REPLY: "原因：设备多次没有回应，可能是连接不稳",
    hub_codes.FAULT_INVALID_REPLY: "原因：设备的回应无法识别，可能是固件和 Console 版本不匹配",
    hub_codes.FAULT_INVALID_STATUS: "原因：设备报告的灯状态无法识别，可能是固件和 Console 版本不匹配",
}
FAULT_REASON_UNKNOWN = "原因：未知"
# M23: shown under the status after a press that did not open its conversation.
PRESS_PROBLEM = "上次按灯没有跳到对话：{reason}"
ACTIVE_NO_LIGHTS = "工作中 · 豆包里有对话开始后才会占灯"
ACTIVE_NO_LIGHTS_NEEDS_ACCESSIBILITY = "工作中 · 需要辅助功能权限才能按灯跳到对话"

ACCESSIBILITY_TEXT = {
    perm.TRUSTED: "辅助功能：已允许，按灯可以跳到对应对话",
    perm.NOT_TRUSTED: "辅助功能：未允许 · 按灯跳到对话需要这项权限。点“去允许”，"
                      "在“隐私与安全性 → 辅助功能”里打开 BORING Console 的开关",
    perm.UNKNOWN: "辅助功能：无法读取授权状态 · 请在“系统设置 → 隐私与安全性 → 辅助功能”里确认 BORING Console 已打开",
}
# 蓝牙授权说的是 Console 用蓝牙连 BORING 设备要的系统权限，和豆包无关（design decision）：
# 这几句现在显示在「设置与维护 → 设备与连接」的设备信息里，而且只在用蓝牙连接时出现。
BLUETOOTH_TEXT = {
    perm.BT_ALLOWED: "蓝牙：已允许",
    perm.BT_NOT_DETERMINED: "蓝牙：还没有授权 · 第一次用蓝牙连接设备时，系统会询问，请选“允许”",
    perm.BT_DENIED: "蓝牙：未允许 · 用蓝牙连接需要在“系统设置 → 隐私与安全性 → 蓝牙”里打开 BORING Console 的开关；只用 USB 时不需要",
    perm.BT_RESTRICTED: "蓝牙：被系统限制 · 这台 Mac 的管理设置不允许使用蓝牙；可以改用 USB 连接",
    perm.UNKNOWN: "蓝牙：无法读取授权状态",
}

PERMISSION_POLL_MS = 2000  # only right after "去允许"; otherwise it rechecks on coming back


def status_line(availability: str, lights: int, accessibility: str) -> str:
    """The one status line (Chinese source; the text catalog translates it)."""
    if availability != link_codes.ACTIVE:
        return STATUS_TEXT.get(availability, STATUS_TEXT[link_codes.APP_NOT_RUNNING])
    needs_access = accessibility == perm.NOT_TRUSTED
    if lights <= 0:
        return ACTIVE_NO_LIGHTS_NEEDS_ACCESSIBILITY if needs_access else ACTIVE_NO_LIGHTS
    text = f"工作中，占用 {lights} 颗灯"
    return f"{text}；需要辅助功能权限才能按灯跳到对话" if needs_access else text


def doubao_diagnostics(link, accessibility: str) -> str:
    """The technical side of the panel, for 「复制诊断信息」: codes, raw detail, counters.

    link.stats() carries no conversation ids or titles (H17), so this is safe to paste.
    """
    lines = [
        *diagnostics_header(),
        "",
        "[豆包状态灯]",
        f"enabled: {link.enabled}",
        f"availability: {link.availability}",
        f"lights_in_use: {link.lights_in_use}",
        f"fault_reason: {link.fault_reason or '-'}",
        f"fault_detail: {' '.join(link.fault_detail.split()) or '-'}",
        f"last_open_problem: {link.last_open_problem or '-'}",
        f"accessibility: {accessibility}",
    ]
    stats = getattr(link, "stats", None)
    if callable(stats):
        lines.append("stats: " + json.dumps(stats(), ensure_ascii=False, sort_keys=True, default=str))
    return "\n".join(lines)


class DoubaoLinkSettings(QFrame):
    """The 「详情」 panel bound to MainViewModel.desktop_agents (no second switch)."""

    def __init__(self, link, permissions: perm.SystemPermissions | None = None,
                 parent=None, *, poll_ms: int = PERMISSION_POLL_MS,
                 watcher: PermissionWatcher | None = None):
        super().__init__(parent)
        self.setObjectName("doubaoLinkDetails")
        self.link = link
        self.watcher = watcher or PermissionWatcher(permissions, self, kinds=(perm.ACCESSIBILITY,),
                                                    poll_ms=poll_ms)
        self.permissions = self.watcher.permissions
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        description = QLabel(
            "用 6 颗 Agent 灯显示豆包里对话的进度，按灯可以跳到对应对话。",
            objectName="muted",
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.status = QLabel(objectName="doubaoLinkStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.reason = QLabel(objectName="doubaoLinkReason")
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.press_problem = QLabel(objectName="doubaoPressProblem")
        self.press_problem.setWordWrap(True)
        layout.addWidget(self.press_problem)

        self.access_row = PermissionRow(
            self.watcher, perm.ACCESSIBILITY, ACCESSIBILITY_TEXT,
            object_name="doubaoAccessibilityStatus", button_name="openAccessibilitySettings")
        self.accessibility = self.access_row.label
        self.open_accessibility = self.access_row.allow
        layout.addWidget(self.access_row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 4, 0, 0)
        self.copy_diagnostics = copy_diagnostics_button(self.diagnostics, object_name="copyDoubaoDiagnostics")
        actions.addWidget(self.copy_diagnostics)
        actions.addStretch(1)
        self.retry_button = QPushButton("重试", objectName="retryDoubaoLink")
        self.retry_button.setProperty("dialogButton", "secondary")
        actions.addWidget(self.retry_button)
        layout.addLayout(actions)

        self.retry_button.clicked.connect(self.link.retry)
        self.link.changed.connect(self.refresh)
        self.watcher.changed.connect(self.refresh)
        self.refresh()

    def diagnostics(self) -> str:
        return doubao_diagnostics(self.link, self.watcher.status(perm.ACCESSIBILITY))

    def refresh(self) -> None:
        link = self.link
        access = self.watcher.status(perm.ACCESSIBILITY)
        set_translatable_text(self.status, status_line(link.availability, link.lights_in_use, access))
        availability = link.availability
        fault = availability == link_codes.FAULT
        trouble = fault or availability in (link_codes.DEVICE_BUSY, link_codes.SOURCE_BROKEN)
        self.reason.setVisible(fault)
        if fault:
            set_translatable_text(self.reason,
                                  FAULT_REASON_TEXT.get(link.fault_reason, FAULT_REASON_UNKNOWN))
        # Retry re-sends to the device; it cannot fix a Doubao read (H17).
        self.retry_button.setVisible(trouble and availability != link_codes.SOURCE_BROKEN)
        problem = link.last_open_problem if availability == link_codes.ACTIVE else ""
        self.press_problem.setVisible(bool(problem))
        if problem:
            set_translatable_text(self.press_problem, PRESS_PROBLEM.format(reason=problem))
