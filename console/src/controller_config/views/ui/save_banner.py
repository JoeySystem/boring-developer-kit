"""The lighting page's save banner: replaces the "SYNC" save card on the right.

Spec: the designer, 灯光页-排法二-v0.4-20261118-图/5-保存横幅-间隙-切到别的组-各个状态.png
(states ①-⑥, text as drawn); the shell is the key 8 banner v1.1 (`Banner`).

    ① DIRTY     orange dots   有修改，还没存进设备          [保存到设备]
    ② CONFIRM   orange dots   确认写入设备？                [取消] [确认写入设备]
    ③ WRITING   spinner       正在写入设备…                 [取消] [正在写入…]  both grey
      VERIFYING spinner       正在读回核对…                 same buttons (-15 第 1 处, 乙)
    ④ SAVED     green check   已存进设备                    after 1.6 s fades out rising 6 (-15 第 1 处)
    ⑤ FAILED    orange tri.   没有存成功，设备上还是原来的设置  [查看原因] [再试一次]
    ⑥ OFFLINE   dashed circle 设备没连上，修改还在这台电脑上   [存为本地草稿]
    HIDDEN      no changes: the banner is hidden

Not on the spec, text drafted by the Agent for the designer (marked DRAFT below):

    CHECKING    spinner       有修改，还没存进设备          [正在检查…] grey
    CONFLICT    orange dots   设备上的设置在别处改过了        [查看原因]
    UNKNOWN     orange tri.   不确定有没有存进设备            [查看原因]

Which state when: `controller_config.save_banner_state` (pure, table in its
docstring). `apply()` takes a `SaveFlow` or a `SaveBannerKind`.

Signals, one per button (the banner never runs the flow itself):

    save_requested        保存到设备        -> main window `_save_preferences_and_prepare(editor)`
    confirm_requested     确认写入设备      -> `_view_model.confirm_device_write()`
    cancel_requested      取消              -> `_view_model.cancel_device_write_confirmation()`
    retry_requested       再试一次          -> `_save_preferences_and_prepare(editor)` again
    details_requested     查看原因          -> open the dialog with the reason and the actions
                                              of `_write_transaction_card` (FAILED: technical
                                              details; UNKNOWN: 确认设备保存结果; CONFLICT:
                                              rebase / use device config / re-read)
    save_draft_requested  存为本地草稿      -> `_save_preferences(editor)`
    shown_changed(bool)   the banner appeared or went away, so the host can re-place things

Object names (the old "SYNC" card's where there is one, so old tests that use
findChild keep working; a button only exists in the states that show it):

    lightingSyncCard           the whole banner
    preferencesSyncSummary     the sentence (QLabel)
    savePreferencesToDevice    the primary "save" slot: 保存到设备 ①, 正在检查… (grey),
                               正在写入… ③ (grey), 再试一次 ⑤
    confirmConfigurationWrite  确认写入设备 ②
    savePreferencesDraft       存为本地草稿 ⑥
    cancelConfigurationWrite   取消 ② ③ (new name)
    configurationWriteDetails  查看原因 ⑤ and the rare two (new name)

What the main window has to do (this file does not touch main_window.py):
- Create ONE SaveBanner for the window and keep it; do not rebuild it with the
  page. ④ only shows after a write the banner saw (from ③ or UNKNOWN), so a
  fresh banner on an already saved device stays hidden.
- Place it yourself under the tabs on the right: top 90 from the window top
  (design decision), right edge on the card column (`banner.RIGHT_INSET`), 16
  above the wall at the smallest window (spec v0.2). Do NOT hand it to
  `BannerStack`: the stack shows every banner it holds on each relayout and
  deletes a banner when it is dismissed, while this one hides and comes back.
  If both show at once, move the stack's top inset below this banner (how
  the two line up is not decided yet, see the spec notes).
- Keep it while the user switches to other Settings groups (the designer:
  "页签下面那一条要保留"); whether it stays on Mode / Prompt is not decided.
- Call `apply(SaveFlow(...))` wherever `update_summary` runs now, and from the
  write transaction signal.
- The banner's ② already asks "确认写入设备？", so the extra QMessageBox in
  `_confirm_device_write` should go (the firmware owner / the designer to agree).
- Lock the page while `transaction.blocks_editing`, as now.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from controller_config.save_banner_state import SaveBannerKind, SaveFlow, save_banner_kind
from controller_config.style import tokens
from controller_config.views.ui.banner import Banner

SAVED_HIDE_MS = tokens.BANNER_SAVED_HIDE_MS   # ④ 1.6 s (-15 第 1 处); the pointer resting on it keeps it

# Object names: the old "SYNC" save card's names where there is one, so the
# old tests keep finding them (asked for by the main session, 2026-11-19).
OBJECT_NAME = "lightingSyncCard"            # the whole banner (was the card)
TEXT_NAME = "preferencesSyncSummary"        # the sentence (was the card's summary line)
SAVE_BUTTON = "savePreferencesToDevice"     # the primary "save" slot: 保存到设备 ①,
                                            # 正在检查… (grey), 正在写入… ③ (grey), 再试一次 ⑤
CONFIRM_BUTTON = "confirmConfigurationWrite"  # 确认写入设备 ② (as on the old write card)
DRAFT_BUTTON = "savePreferencesDraft"       # 存为本地草稿 ⑥
CANCEL_BUTTON = "cancelConfigurationWrite"  # 取消 ② / ③ (new)
DETAILS_BUTTON = "configurationWriteDetails"  # 查看原因 ⑤ and the rare two (new)


@dataclass(frozen=True)
class _ButtonSpec:
    text: str
    kind: str          # Button kind: primary / secondary
    name: str          # objectName
    signal: str | None  # None: shown grey, does nothing
    enabled: bool = True


@dataclass(frozen=True)
class _Look:
    text: str
    icon: str                  # StatusIcon state
    buttons: tuple[_ButtonSpec, ...] = ()


_SAVE = _ButtonSpec("保存到设备", "primary", SAVE_BUTTON, "save_requested")
_DETAILS = _ButtonSpec("查看原因", "secondary", DETAILS_BUTTON, "details_requested")

LOOKS: dict[SaveBannerKind, _Look] = {
    SaveBannerKind.DIRTY: _Look("有修改，还没存进设备", "wait", (_SAVE,)),
    # DRAFT (the designer): the device checks before ②; the button says so, like ③.
    SaveBannerKind.CHECKING: _Look("有修改，还没存进设备", "busy", (
        _ButtonSpec("正在检查…", "primary", SAVE_BUTTON, None, enabled=False),
    )),
    SaveBannerKind.CONFIRM: _Look("确认写入设备？", "wait", (
        _ButtonSpec("取消", "secondary", CANCEL_BUTTON, "cancel_requested"),
        _ButtonSpec("确认写入设备", "primary", CONFIRM_BUTTON, "confirm_requested"),
    )),
    SaveBannerKind.WRITING: _Look("正在写入设备…", "busy", (
        _ButtonSpec("取消", "secondary", CANCEL_BUTTON, None, enabled=False),
        _ButtonSpec("正在写入…", "primary", SAVE_BUTTON, None, enabled=False),
    )),
    # -15 第 1 处 (乙): while reading back, only the sentence changes.
    SaveBannerKind.VERIFYING: _Look("正在读回核对…", "busy", (
        _ButtonSpec("取消", "secondary", CANCEL_BUTTON, None, enabled=False),
        _ButtonSpec("正在写入…", "primary", SAVE_BUTTON, None, enabled=False),
    )),
    SaveBannerKind.SAVED: _Look("已存进设备", "ok"),
    SaveBannerKind.FAILED: _Look("没有存成功，设备上还是原来的设置", "error", (
        _DETAILS,
        _ButtonSpec("再试一次", "primary", SAVE_BUTTON, "retry_requested"),
    )),
    SaveBannerKind.OFFLINE: _Look("设备没连上，修改还在这台电脑上", "off", (
        _ButtonSpec("存为本地草稿", "secondary", DRAFT_BUTTON, "save_draft_requested"),
    )),
    # DRAFT (the designer): the device's settings were changed elsewhere (another
    # computer, a reset); nothing was written. Waiting for you to choose.
    SaveBannerKind.CONFLICT: _Look("设备上的设置在别处改过了", "wait", (_DETAILS,)),
    # DRAFT (the designer): the connection dropped mid-write; the Console checks
    # by reading back once the device is connected again.
    SaveBannerKind.UNKNOWN: _Look("不确定有没有存进设备", "error", (_DETAILS,)),
}

# ④ only follows a write the banner saw: from ③, or an uncertain write that
# turned out fine.
_SAVED_AFTER = {SaveBannerKind.WRITING, SaveBannerKind.VERIFYING, SaveBannerKind.UNKNOWN}


class SaveBanner(Banner):
    """`SaveBanner()`, then `apply(SaveFlow(...))` whenever the flow changes."""

    save_requested = Signal()
    confirm_requested = Signal()
    cancel_requested = Signal()
    retry_requested = Signal()
    details_requested = Signal()
    save_draft_requested = Signal()
    shown_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        look = LOOKS[SaveBannerKind.DIRTY]
        super().__init__(look.text, state=look.icon, timeout_ms=None, key="lighting-save",
                         object_name=OBJECT_NAME, parent=parent)
        self._text.setObjectName(TEXT_NAME)
        self._kind = SaveBannerKind.HIDDEN
        self._shown = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._saved_timed_out)
        self.hide()

    def kind(self) -> SaveBannerKind:
        return self._kind

    def is_shown(self) -> bool:
        """Whether the banner wants to be on screen (also before the host shows)."""
        return self._shown

    def button(self, name: str):
        """The button with objectName `name` in the current state, or None."""
        return next((b for b in self.buttons() if b.objectName() == name), None)

    def apply(self, state: SaveFlow | SaveBannerKind | str) -> SaveBannerKind:
        """Show the banner for `state`; returns the state it settled on."""
        kind = save_banner_kind(state) if isinstance(state, SaveFlow) else SaveBannerKind(state)
        previous = self._kind
        if kind is previous:
            return kind
        if kind is SaveBannerKind.SAVED and previous not in _SAVED_AFTER:
            kind = SaveBannerKind.HIDDEN  # nothing just happened: nothing to say
            if previous is kind:
                return kind
        self._kind = kind
        self._hide_timer.stop()
        if kind is SaveBannerKind.HIDDEN:
            self._set_shown(False)
            return kind
        look = LOOKS[kind]
        # One look to the next: the pill changes length like the Dynamic Island, the
        # old words fade out inside it and the new ones come in (the designer 11-20).
        changing = self._shown and self.isVisible()
        if changing:
            self.begin_change()
        try:
            self.set_text(look.text)
            self.set_state(look.icon, waiting_for="you")
            self.clear_buttons()
            for spec in look.buttons:
                button = self.add_button(spec.text, spec.kind, object_name=spec.name)
                button.setEnabled(spec.enabled)
                if spec.signal is not None:
                    button.clicked.connect(getattr(self, spec.signal))  # signal to signal
        finally:
            if changing:
                self.end_change()
        self._set_shown(True)
        if kind is SaveBannerKind.SAVED:
            self._hide_timer.start(SAVED_HIDE_MS)
        return kind

    def clear_buttons(self) -> None:
        # Unparent first: `deleteLater` alone leaves the old button findable
        # (same object name) until the event loop runs.
        for button in self.buttons():
            button.setParent(None)
        super().clear_buttons()

    def _set_shown(self, shown: bool, *, rise: bool = False) -> None:
        """Slides in from the right when it appears and out to the right when it goes
        (-15 第 3 处); the host places it from `shown_changed`."""
        if shown == self._shown:
            if shown and not self.isVisible():
                self.setVisible(True)
            return
        self._shown = shown
        if shown:
            self.setVisible(True)
            self.shown_changed.emit(True)   # the host places it first
            self.slide_in()
        else:
            self.shown_changed.emit(False)
            if rise:
                self.rise_out()   # "已存进设备" goes up and away, 中 (-15 第 1 处)
            else:
                self.slide_out()

    def _saved_timed_out(self) -> None:
        if self.underMouse():
            self._hide_timer.start(SAVED_HIDE_MS)
            return
        self._kind = SaveBannerKind.HIDDEN
        self._set_shown(False, rise=True)
