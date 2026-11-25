"""Which status the Prompt page's top row shows. Pure: no Qt, easy to test.

Spec: the designer, Prompt页-定稿-v1-20261118-图/3-顶部一行和编辑区的各种样子.png
("顶部一行", six rows). The row replaces what used to be spread over the page:
the helper badge, the operation steps, the permission card and the storage
status (`views/prompt_library_editor.py`: `PermissionNotice`, `_summary_card`,
`_helper_card`, `update_runtime`). Details go to the diagnostics dialog.

Inputs (`PromptStatusInputs`), all already known where the page is built
(`main_window.py` `_prompt_library_page` / the `update_runtime(...)` call):

    connected          a device is connected now (the page has a library to edit;
                       `PromptLibraryEditor(library=None)` is the "not connected" page)
    over_bluetooth     `snapshot.connection_kind == "bluetooth"`
    palette            the hardware has the prompt palette (key 12 + joystick + knob):
                       `hardware_id == "WMP-S3-MATRIX12-POWER-V2"`, as
                       `PromptLibraryEditor._supports_prompt_palette`
    storage_available  `prompt_device.available` (update_runtime `available`)
    listener           `prompt_device.listener_status.state` (update_runtime `listener.state`)
    permission_missing macOS Accessibility is missing, so Command+V cannot be posted:
                       `not watcher.granted(ACCESSIBILITY)` of a `PermissionWatcher`
                       (same answer as `paste_permission_missing()`)

Mapping, first matching row wins:

    condition                                         -> status         look
    ------------------------------------------------  ----------------  ----------------------------------------
    not connected                                     -> DISCONNECTED   ○ 设备没连上 | 现在改的先存在这台电脑…   [诊断]
    over_bluetooth                                    -> BLUETOOTH      ● 现在用蓝牙连接 | (DRAFT, the firmware owner)  [说明][读取][诊断]
    not storage_available, or listener OFFLINE        -> HELPER_OFFLINE ○ 暂时打不出提示词 | 点右边的诊断…  [读取][诊断]  (DRAFT)
    permission_missing                                -> PERMISSION     ▲ 还差一步 | 允许 BORING Console…  [去允许][读取][诊断]
    listener STARTING / PAUSED / RETRYING             -> PREPARING      ◌ 正在准备…                       [读取][诊断]
    listener READY, palette                           -> READY          ● 可以使用 | 长按 12 号键打开…  [▶ 看演示][读取][诊断]
    listener READY, no palette                        -> READY_DIRECT   ● 可以使用 | 在 Mode 里把摇杆…           [读取][诊断]

Why this order:
- Not connected says nothing else: nothing can be typed or read back, and the
  edits are kept on this computer (the spec's only state without "重新读取").
- Bluetooth before the helper: the firmware's prompt trigger is the "USB
  helper event path" (`features.prompt_trigger_usb`, protocol.md); what works
  over Bluetooth is for the firmware owner to check on the device (spec: "句子等 the firmware owner").
- A helper that is off for good (no prompt storage on this firmware, no paste
  helper on this platform, no USB trigger declared) is not the spec's
  "刚打开几秒" and asking for a permission would not help. Not on the spec:
  status and sentence are DRAFT, the reason itself is in the diagnostics
  (`listener.message`).
- Permission before "preparing": it is the one thing the user has to do, and
  the listener can be ready while the permission is still missing
  (the designer: "权限引导提前，顶部一行，缺权限时给去允许").
- PAUSED (a device read or write is under way) and RETRYING (a poll failed,
  retried by itself) show "正在准备…" too: neutral grey, nothing to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from controller_config.prompt_device import PromptListenerState


class PromptStatusKind(str, Enum):
    PREPARING = "preparing"            # ◌ 正在准备…  (also before the first update)
    READY = "ready"                    # ● 可以使用 | 长按 12 号键打开 → 摇杆选 → 按旋钮确认 · 3 号键取消
    READY_DIRECT = "ready_direct"      # ● 可以使用 | 在 Mode 里把摇杆方向绑到提示词，拨一下就打出来
    PERMISSION = "permission"          # ▲ 还差一步 | 允许 BORING Console 控制这台电脑…  〔去允许〕
    BLUETOOTH = "bluetooth"            # ● 现在用蓝牙连接 | (sentence: the firmware owner)  〔说明〕
    DISCONNECTED = "disconnected"      # ○ 设备没连上 | 现在改的先存在这台电脑，连上后再存进设备
    HELPER_OFFLINE = "helper_offline"  # not on the spec (DRAFT)


_STARTING = {
    PromptListenerState.STARTING,
    PromptListenerState.PAUSED,
    PromptListenerState.RETRYING,
}


@dataclass(frozen=True)
class PromptStatusInputs:
    """What the prompt helper looks like right now (see the module docstring)."""

    connected: bool
    over_bluetooth: bool = False
    palette: bool = True
    storage_available: bool = True
    listener: PromptListenerState = PromptListenerState.STARTING
    permission_missing: bool = False


def prompt_status_kind(inputs: PromptStatusInputs) -> PromptStatusKind:
    """The top row's status for `inputs`; the table is in the module docstring."""
    if not inputs.connected:
        return PromptStatusKind.DISCONNECTED
    if inputs.over_bluetooth:
        return PromptStatusKind.BLUETOOTH
    if not inputs.storage_available or inputs.listener is PromptListenerState.OFFLINE:
        return PromptStatusKind.HELPER_OFFLINE
    if inputs.permission_missing:
        return PromptStatusKind.PERMISSION
    if inputs.listener in _STARTING:
        return PromptStatusKind.PREPARING
    return PromptStatusKind.READY if inputs.palette else PromptStatusKind.READY_DIRECT
