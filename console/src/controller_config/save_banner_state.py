"""Which save banner the lighting page shows. Pure: no Qt, easy to test.

The save flow itself does not change (validate -> confirm or cancel -> write
-> read back). This only decides how it looks: one of the six states on
the designer's spec, two rare ones the spec leaves out, a short "checking" step,
or no banner at all.

Spec: Console视觉稿/灯光页-排法二-v0.4-20261118-图/5-保存横幅-间隙-切到别的组-各个状态.png.

Inputs (all already worked out in main_window.py `_preferences_page.update_summary`):

    model_state        AppState of the device connection
    transaction_state  ConfigTransactionState of the write
    has_changes        the editor differs from the draft, or the draft is dirty
                       (`pending or draft.is_dirty`)
    base_changed       the device's config generation is not the one the
                       draft was made from (someone changed it elsewhere)
    writable           `snapshot.compatibility["write"] is True`

"Connected" below means model_state is READY and writable is True: the only
case where the current page lets you press save (`can_apply`).

Mapping, first matching row wins:

    transaction_state      condition                     -> banner
    ---------------------  ----------------------------  ------------------------------
    VALIDATING             -                             -> CHECKING  (spinner, ① text, button "正在检查…" grey)
    AWAITING_CONFIRMATION  connected                     -> CONFIRM   ②
    AWAITING_CONFIRMATION  not connected                 -> OFFLINE   ⑥
    WRITING                -                             -> WRITING   ③
    PENDING                -                             -> WRITING   ③
    VERIFYING              -                             -> VERIFYING ③ "正在读回核对…" (-15 第 1 处)
    UNKNOWN                -                             -> UNKNOWN   (rare: result not certain)
    CONFLICT               -                             -> CONFLICT  (rare: device changed elsewhere)
    FAILED                 not connected                 -> OFFLINE   ⑥
    FAILED                 connected                     -> FAILED    ⑤
    ACTIVE                 no changes                    -> SAVED     ④ (hides itself after a few seconds)
    IDLE                   no changes                    -> HIDDEN
    IDLE / ACTIVE          changes, not connected        -> OFFLINE   ⑥
    IDLE / ACTIVE          changes, base_changed         -> CONFLICT  (rare)
    IDLE / ACTIVE          changes, connected            -> DIRTY     ①

Why these:
- Busy states win over everything: the write is under way and the page is
  locked (`transaction.blocks_editing`); a lost connection turns them into
  UNKNOWN or FAILED by itself (viewmodels/main.py `_on_connection_failure`).
- FAILED while not connected shows ⑥: "再试一次" could not work, and the
  changes are still only on this computer.
- ACTIVE stays as the transaction state after a good write until the next
  one; with new edits it is just "有修改" again.
- base_changed with the device connected is the same situation as CONFLICT
  (the current page disables save and offers the local draft), so it gets the
  same banner and the same "查看原因".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from controller_config.models import AppState
from controller_config.transactions import ConfigTransactionState


class SaveBannerKind(str, Enum):
    HIDDEN = "hidden"        # no changes: no banner
    DIRTY = "dirty"          # ① 有修改，还没存进设备 · 保存到设备
    CHECKING = "checking"    # between ① and ②: the device checks the changes
    CONFIRM = "confirm"      # ② 确认写入设备？ · 取消 · 确认写入设备
    WRITING = "writing"      # ③ 正在写入设备… · both buttons grey; page locked
    VERIFYING = "verifying"  # ③ then 正在读回核对… while reading back (-15 第 1 处, 乙)
    SAVED = "saved"          # ④ 已存进设备, hides itself
    FAILED = "failed"        # ⑤ 没有存成功… · 查看原因 · 再试一次
    OFFLINE = "offline"      # ⑥ 设备没连上… · 存为本地草稿
    CONFLICT = "conflict"    # rare, not on the spec · 查看原因
    UNKNOWN = "unknown"      # rare, not on the spec · 查看原因


_BUSY = {
    ConfigTransactionState.WRITING,
    ConfigTransactionState.PENDING,
}


@dataclass(frozen=True)
class SaveFlow:
    """What the save flow looks like right now (see the module docstring)."""

    model_state: AppState
    transaction_state: ConfigTransactionState
    has_changes: bool
    base_changed: bool = False
    writable: bool = True

    @property
    def connected(self) -> bool:
        return self.model_state is AppState.READY and self.writable


def save_banner_kind(flow: SaveFlow) -> SaveBannerKind:
    """The banner for `flow`; the table is in the module docstring."""
    state = flow.transaction_state
    if state is ConfigTransactionState.VALIDATING:
        return SaveBannerKind.CHECKING
    if state is ConfigTransactionState.AWAITING_CONFIRMATION:
        return SaveBannerKind.CONFIRM if flow.connected else SaveBannerKind.OFFLINE
    if state in _BUSY:
        return SaveBannerKind.WRITING
    if state is ConfigTransactionState.VERIFYING:
        return SaveBannerKind.VERIFYING
    if state is ConfigTransactionState.UNKNOWN:
        return SaveBannerKind.UNKNOWN
    if state is ConfigTransactionState.CONFLICT:
        return SaveBannerKind.CONFLICT
    if state is ConfigTransactionState.FAILED:
        return SaveBannerKind.FAILED if flow.connected else SaveBannerKind.OFFLINE
    # IDLE or ACTIVE
    if not flow.has_changes:
        return SaveBannerKind.SAVED if state is ConfigTransactionState.ACTIVE else SaveBannerKind.HIDDEN
    if not flow.connected:
        return SaveBannerKind.OFFLINE
    if flow.base_changed:
        return SaveBannerKind.CONFLICT
    return SaveBannerKind.DIRTY
