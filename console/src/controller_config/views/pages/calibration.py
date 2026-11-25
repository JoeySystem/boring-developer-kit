"""摇杆校准页 (route ``joystick``), reached from 设置 → 设备.

A settings wall with the Settings rail on its left (the designer 甲, design decision):

    实时采样 2 × 2          | 设备当前值 2 × 1
    state, readings, buttons | 说明 2 × 1

The tiles keep their size in every state; long messages end in "…" and keep
the whole text in the tooltip. While a calibration runs, the rail is locked
like every other way off the page (MainWindow._settings_rail).
"""
from __future__ import annotations

import copy
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from controller_config.views.ui import notice
from controller_config.i18n import SKIP_TRANSLATION_PROPERTY, LanguageManager, set_translatable_text
from controller_config.joystick_calibration import CalibrationState, CalibrationTransaction
from controller_config.models import AppState, ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.pages.common import (
    planned_tool_capability,
    planned_tool_capability_label,
)
from controller_config.views.settings_wall import (
    SettingsWall,
    TileIcon,
    WallTile,
    pill_button,
    show_card_dialog,
    wall_scroll,
)
from controller_config.views.ui import dialog as dialog_shell
from controller_config.views.ui.clamped_label import ClampedLabel
from controller_config.views.ui.dot_row import DotRow

RAW_MAXIMUM = 4095   # 12-bit joystick reading
RAW_DOTS = 64        # one dot per 64 readings (进度条点阵样式 定稿 v1)

INTRO = "仅在诊断中发现回中偏移、方向误触或边缘行程不足时使用。先采集松手中立点，再让摇杆走完四周边缘。"
BOUNDARY = "取消、USB 断开或会话超时都不会修改原有校准。保存后会等待候选激活，并通过 GET_CONFIG 读回确认。"


def _raw_text(value: int) -> str:
    return f"{value} / {RAW_MAXIMUM}"


def _note(text: str, object_name: str, *, lines: int | None = None) -> ClampedLabel:
    label = ClampedLabel(lines=lines, object_name=object_name)
    label.setProperty("wallNote", True)
    set_translatable_text(label, text)
    return label


def _button(text: str, object_name: str, role: str) -> QPushButton:
    return pill_button(text, object_name, role=role)


class CalibrationPage:
    """Shows the device's current calibration and runs a calibration session.

    Live samples only move the two dot rows and the status texts, so update()
    changes those widgets in place; any other change rebuilds the page.
    """

    route = "joystick"

    def __init__(
        self,
        view_model: MainViewModel,
        language_manager: LanguageManager,
        *,
        dialog_parent: QWidget,
        settings_rail: Callable[[str], QWidget],
        wall_workspace: Callable[[str], QWidget],
    ) -> None:
        self._view_model = view_model
        self._language_manager = language_manager
        self._dialog_parent = dialog_parent
        self._settings_rail = settings_rail
        self._wall_workspace = wall_workspace
        self._inputs: tuple | None = None
        self._scroll: QScrollArea | None = None
        self._raw_bars: dict[str, DotRow] = {}
        self._raw_values: dict[str, QLabel] = {}
        self._message: QLabel | None = None
        self._technical: QLabel | None = None
        self._endpoints: QLabel | None = None

    def update(self, model: ScreenModel) -> bool:
        if (self._scroll is None or not isValid(self._scroll)
                or self._render_inputs(model) != self._inputs):
            return False
        transaction = self._view_model.calibration
        for axis, value in (("X", transaction.raw_x), ("Y", transaction.raw_y)):
            self._raw_bars[axis].set_value(value)
            self._raw_values[axis].setText(_raw_text(value))
        set_translatable_text(self._message, transaction.message)
        set_translatable_text(self._technical, transaction.technical)
        self._technical.setVisible(bool(transaction.technical))
        set_translatable_text(self._endpoints, _calibration_endpoint_text(transaction))
        return True

    def _render_inputs(self, model: ScreenModel) -> tuple:
        snapshot = model.snapshot
        draft = self._view_model.draft
        calibration = self._view_model.calibration
        return (self._language_manager.language, model.state, planned_tool_capability("joystick", snapshot),
                snapshot.identity if snapshot else None, snapshot.config.get("joystick") if snapshot else None,
                bool(draft and draft.is_dirty), calibration.state,
                calibration.can_confirm, calibration.can_cancel, calibration.blocks_editing)

    def build(self, model: ScreenModel) -> QWidget:
        self._inputs = copy.deepcopy(self._render_inputs(model))
        snapshot = model.snapshot
        capability = planned_tool_capability("joystick", snapshot)
        transaction = self._view_model.calibration

        # -- header: the name and the support badge. The way back is the Settings icon
        # rail on the left ("返回设备设置" repeated it and went, design decision / 单 design ticket).
        header = QWidget(objectName="joystickCalibrationHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(4, 0, 0, 0)
        header_row.setSpacing(12)
        heading = QLabel(objectName="settingsWallTitle")
        set_translatable_text(heading, "摇杆校准")
        header_row.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
        badge = QLabel(
            planned_tool_capability_label("joystick", capability),
            objectName="statusReady" if capability is True else "statusWarn",
        )
        badge.setProperty("capabilityState", capability)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        wall = SettingsWall("device", header=header)
        wall.setObjectName("joystickCalibrationPage")

        # -- 实时采样 (2 × 2) ------------------------------------------------------
        live = WallTile(2, 2)
        live.setObjectName("joystickCalibrationLive")
        live.header(TileIcon("joystick"), title=_calibration_state_title(transaction.state))
        live.box.addSpacing(4)
        message = _note(transaction.message, "calibrationMessage", lines=2)
        live.box.addWidget(message)
        technical = _note(transaction.technical, "calibrationTechnical", lines=2)
        technical.setVisible(bool(transaction.technical))
        live.box.addWidget(technical)
        live.spacer()

        # 进度条点阵样式 定稿 v1: each reading is one row of orange dots,
        # X / Y on the left, "2048 / 4095" on the right as before.
        raw_bars = {}
        raw_values = {}
        for axis, value in (("X", transaction.raw_x), ("Y", transaction.raw_y)):
            row = QHBoxLayout()
            row.setSpacing(12)
            name = QLabel(axis, objectName="calibrationRawAxis")
            name.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            bar = DotRow(RAW_DOTS, maximum=RAW_MAXIMUM)
            bar.setObjectName(f"calibrationRaw{axis}")
            bar.set_value(value)
            number = QLabel(objectName="calibrationRawValue")
            number.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            number.setText(_raw_text(value))
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(number)
            live.box.addLayout(row)
            raw_bars[axis] = bar
            raw_values[axis] = number
        live.box.addSpacing(6)
        endpoints = _note(_calibration_endpoint_text(transaction), "calibrationEndpoints", lines=4)
        live.box.addWidget(endpoints)
        live.box.addSpacing(6)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        can_start = (
            capability is True
            and model.state is AppState.READY
            and not transaction.blocks_editing
            and (self._view_model.draft is None or not self._view_model.draft.is_dirty)
        )
        if transaction.can_confirm:
            confirm = _button("松手回中后保存校准", "confirmJoystickCalibration", "primary")
            confirm.clicked.connect(self._confirm)
            buttons.addWidget(confirm)
        if transaction.can_cancel:
            cancel = _button("取消并保留原配置", "cancelJoystickCalibration", "plain")
            cancel.clicked.connect(self._cancel)
            buttons.addWidget(cancel)
        if not transaction.blocks_editing:
            start = _button(
                "重新开始校准"
                if transaction.state is not CalibrationState.IDLE
                else "开始摇杆校准",
                "startJoystickCalibration", "primary",
            )
            start.setEnabled(can_start)
            start.clicked.connect(self._start)
            buttons.addWidget(start)
        buttons.addStretch(1)
        live.box.addLayout(buttons)
        wall.place(live, 0, 0)

        # -- 设备当前值 (2 × 1) ----------------------------------------------------
        current = WallTile(2, 1)
        current.setObjectName("joystickCalibrationCurrent")
        current.header(TileIcon("info"), title="设备当前值")
        current.spacer()
        joystick = snapshot.config.get("joystick") if snapshot is not None else None
        if isinstance(joystick, dict):
            x_axis = joystick.get("x") if isinstance(joystick.get("x"), dict) else {}
            y_axis = joystick.get("y") if isinstance(joystick.get("y"), dict) else {}
            calibrated = QLabel(objectName="calibrationCurrentState")
            calibrated.setProperty("wallState", True)
            set_translatable_text(calibrated, "已校准" if joystick.get("calibrated") is True else "尚未校准")
            current.box.addWidget(calibrated)
            values = QLabel(
                f"X  {x_axis.get('minimum', '—')} / {x_axis.get('center', '—')} / {x_axis.get('maximum', '—')}"
                f"    死区 {x_axis.get('deadzone', '—')}\n"
                f"Y  {y_axis.get('minimum', '—')} / {y_axis.get('center', '—')} / {y_axis.get('maximum', '—')}"
                f"    死区 {y_axis.get('deadzone', '—')}",
                objectName="calibrationCurrentValues",
            )
            values.setProperty("wallNote", True)
            current.box.addWidget(values)
        else:
            waiting = QLabel(objectName="calibrationCurrentValues")
            waiting.setProperty("wallNote", True)
            set_translatable_text(waiting, "等待设备读取")
            current.box.addWidget(waiting)
        wall.place(current, 0, 2)

        # -- 说明 (2 × 1): when to use it, and what cannot go wrong --------------
        guide = WallTile(2, 1)
        guide.setObjectName("joystickCalibrationGuide")
        more = pill_button("更多信息", "calibrationGuideMore", role="link")
        policy = more.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        more.setSizePolicy(policy)
        more.hide()
        guide.header(TileIcon("help"), control=more)
        guide.box.addSpacing(2)
        intro = _note(INTRO, "calibrationIntro")
        boundary = _note(BOUNDARY, "calibrationBoundary")
        guide.box.addWidget(intro, 3)
        guide.box.addWidget(boundary, 2)

        def show_more(_clamped: bool) -> None:
            more.setVisible(intro.is_clamped() or boundary.is_clamped())

        intro.clamped_changed.connect(show_more)
        boundary.clamped_changed.connect(show_more)
        more.clicked.connect(self._show_guide)
        wall.place(guide, 1, 2)

        workspace = self._wall_workspace("settingsWorkspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = wall_scroll(wall, self._settings_rail("joystick"))
        scroll.setObjectName("joystickCalibrationScroll")
        layout.addWidget(scroll, 1)
        self._scroll = scroll
        self._raw_bars = raw_bars
        self._raw_values = raw_values
        self._message = message
        self._technical = technical
        self._endpoints = endpoints
        return workspace

    def _show_guide(self) -> None:
        body = QFrame()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        for text in (INTRO, BOUNDARY):
            label = QLabel(objectName="calibrationGuideFull")
            label.setWordWrap(True)
            set_translatable_text(label, text)
            box.addWidget(label)
        show_card_dialog(self._dialog_parent, "摇杆校准", body, width=dialog_shell.MEDIUM)

    def _start(self) -> None:
        choice = notice.ask(
            self._dialog_parent,
            "开始摇杆校准",
            "请先完全松开摇杆。开始后摇杆方向、径向与按压输出会被临时抑制，直到保存、取消、断开或超时。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Yes: "开始校准"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.start_joystick_calibration()
        except ValueError as exc:
            notice.notify(self._dialog_parent, "无法开始校准", str(exc))

    def _confirm(self) -> None:
        choice = notice.ask(
            self._dialog_parent,
            "保存摇杆校准",
            "请确认已松开摇杆并让它回到中心。设备会做最终回中检查，通过后把校准值写入非活动配置槽。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            labels={QMessageBox.Yes: "保存校准"},
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.confirm_joystick_calibration()
        except ValueError as exc:
            notice.alert(self._dialog_parent, "无法保存校准", str(exc))

    def _cancel(self) -> None:
        try:
            self._view_model.cancel_joystick_calibration()
        except ValueError as exc:
            notice.alert(self._dialog_parent, "无法取消校准", str(exc))


def _calibration_state_title(state: CalibrationState) -> str:
    return {
        CalibrationState.IDLE: "等待开始",
        CalibrationState.STARTING: "建立校准会话",
        CalibrationState.CENTERING: "采集中立点",
        CalibrationState.SAMPLING: "读取实时采样",
        CalibrationState.CAPTURING: "采集完整行程",
        CalibrationState.READY_TO_CONFIRM: "可保存校准",
        CalibrationState.CONFIRMING: "检查回中并保存",
        CalibrationState.PENDING: "等待候选激活",
        CalibrationState.VERIFYING: "读回校准结果",
        CalibrationState.UNKNOWN: "结果待对账",
        CalibrationState.CANCELLING: "正在取消",
        CalibrationState.CANCELLED: "已取消",
        CalibrationState.COMPLETED: "校准已完成",
        CalibrationState.FAILED: "校准未完成",
        CalibrationState.CONFLICT: "配置冲突",
    }[state]


def _calibration_endpoint_text(transaction: CalibrationTransaction) -> str:
    if transaction.center_x is None or transaction.center_y is None:
        return "端点和中心将在中立点采集完成后显示。"
    if transaction.ready_to_confirm:
        status = (
            "四向行程与两次回中已完成"
            if transaction.stable_returns_supported else "四周行程已完整"
        )
    elif transaction.travel_complete:
        status = "四向行程已完整；请拨动并回中两次"
    else:
        status = "请沿四向拨动，并在拨动后松手回中"
    return (
        f"{status}\n"
        f"X  {transaction.minimum_x} / {transaction.center_x} / {transaction.maximum_x}\n"
        f"Y  {transaction.minimum_y} / {transaction.center_y} / {transaction.maximum_y}"
    )
