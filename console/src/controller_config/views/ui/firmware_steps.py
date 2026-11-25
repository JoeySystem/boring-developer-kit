"""The four steps of a firmware update under its ring (-15 第 8 处, 乙):

    传输 → 写入 → 重启 → 读回

drawn with the status shapes v1: a step done is a green check, the one going
on spins, one not reached is a dashed circle, the one it stopped at is the
orange rounded triangle. Each change of shapes cross-fades in 瞬时.

Only the look: the stages come from the firmware owner's update flow as they are
(FirmwareUpdateState). The flow has no stage of its own for "写入": the
device writes while it receives (TRANSFERRING) and checks the image after
FW_END (FINALIZING), so 写入 is FINALIZING here (Agent's mapping). The failed
step is the last step the page saw going on; the flow does not say it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from controller_config.firmware_update import FirmwareUpdateState as S
from controller_config.i18n import set_translatable_text
from controller_config.style import tokens
from controller_config.views.ui import motion
from controller_config.views.ui.page_motion import CrossFade, clear_grab
from controller_config.views.ui.status_icon import StatusIcon

STEPS = ("传输", "写入", "重启", "读回")
STEP_OF = {S.CHECKING: 0, S.BEGINNING: 0, S.TRANSFERRING: 0, S.FINALIZING: 1,
           S.WAITING_RECONNECT: 2, S.VERIFYING: 3}
ICON = tokens.CHECK_SIZE


def step_of(state: S, last: int | None = None) -> int | None:
    """Which step `state` is on; PAUSED / ABORTING / FAILED stay on `last`."""
    return STEP_OF.get(state, last)


def step_states(state: S, last: int | None = None) -> tuple[str, ...]:
    """The four status shapes for `state` (StatusIcon states)."""
    if state is S.COMPLETED:
        return ("ok",) * len(STEPS)
    current = step_of(state, last)
    if current is None:
        return ("off",) * len(STEPS)
    if state is S.FAILED:
        here = "error"
    elif state in {S.PAUSED, S.ABORTING}:
        here = "wait"
    else:
        here = "busy"
    return tuple("ok" if i < current else here if i == current else "off" for i in range(len(STEPS)))


class FirmwareSteps(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("firmwareSteps")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.icons: list[StatusIcon] = []
        self.labels: list[QLabel] = []
        for index, name in enumerate(STEPS):
            if index:
                arrow = QLabel("→", objectName="firmwareStepArrow")
                arrow.setProperty("muted", True)
                row.addWidget(arrow)
            icon = StatusIcon("off", size=ICON)
            icon.setObjectName("firmwareStepIcon")
            label = QLabel(objectName="firmwareStepText")
            set_translatable_text(label, name)
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            self.icons.append(icon)
            self.labels.append(label)
        row.addStretch(1)
        self._shown: tuple[str, ...] = ("off",) * len(STEPS)

    def shown(self) -> tuple[str, ...]:
        return self._shown

    def set_states(self, states: tuple[str, ...]) -> None:
        if states == self._shown:
            return
        if self.isVisible() and not motion.reduces_motion():
            CrossFade(self, clear_grab(self), tokens.MOTION_INSTANT_MS)   # each change, 瞬时
        self._shown = states
        for icon, state in zip(self.icons, states):
            icon.set_state(state)
