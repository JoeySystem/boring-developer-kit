from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from controller_config.diagnostics import (
    active_controls,
    joystick_diagnostics,
    joystick_direction_label,
)
from controller_config.i18n import set_translatable_text, translate_ui_text
from controller_config.models import AppState

if TYPE_CHECKING:
    from controller_config.viewmodels.main import MainViewModel


class DiagnosticsPage(QScrollArea):
    """Live, read-only diagnostics backed only by GET_STATUS and bootstrap data."""

    def __init__(self, view_model: MainViewModel, parent: QWidget | None = None) -> None:
        super().__init__(parent, objectName="diagnosticsPage")
        self._view_model = view_model
        self._last_static_render: tuple[object, ...] | None = None
        self._last_joystick_render: tuple[object, ...] | None = None
        self._last_event_render: tuple[int, int] | None = None
        self._joystick_issue_reported = False
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="diagnosticsPageContents")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = _card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(24, 20, 24, 22)
        heading = QVBoxLayout()
        heading.addWidget(QLabel("DEVICE DIAGNOSTICS", objectName="eyebrow"))
        heading.addWidget(QLabel("诊断与实时输入", objectName="inspectorTitle"))
        intro = QLabel(
            "集中查看设备身份、协议状态、配置同步、活动控件和摇杆实时数据。所有内容都来自当前读取链路。",
            objectName="muted",
        )
        intro.setWordWrap(True)
        heading.addWidget(intro)
        summary_layout.addLayout(heading, 1)
        self._connection_badge = QLabel(objectName="statusWarn")
        self._connection_badge.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(self._connection_badge, 0, Qt.AlignTop)
        layout.addWidget(summary)

        control_check = _card()
        control_check_layout = QVBoxLayout(control_check)
        control_check_layout.setContentsMargins(24, 20, 24, 22)
        control_check_layout.setSpacing(10)
        control_heading = QHBoxLayout()
        control_titles = QVBoxLayout()
        control_titles.addWidget(QLabel("CONTROL CHECK", objectName="eyebrow"))
        control_titles.addWidget(QLabel("逐项检查实体控件", objectName="inspectorTitle"))
        control_heading.addLayout(control_titles, 1)
        self._capture_state = QLabel(objectName="statusWarn")
        self._capture_state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        control_heading.addWidget(self._capture_state)
        control_check_layout.addLayout(control_heading)

        self._capture_instruction = QLabel(objectName="headerValue")
        self._capture_instruction.setWordWrap(True)
        control_check_layout.addWidget(self._capture_instruction)
        self._capture_groups = QLabel(objectName="muted")
        self._capture_groups.setWordWrap(True)
        control_check_layout.addWidget(self._capture_groups)
        self._capture_progress = QProgressBar(objectName="diagnosticControlProgress")
        self._capture_progress.setRange(0, 1)
        self._capture_progress.setValue(0)
        self._capture_progress.setFormat("0 / 0")
        control_check_layout.addWidget(self._capture_progress)

        capture_actions = QHBoxLayout()
        self._capture_start = QPushButton(
            "开始控件诊断", objectName="startDiagnosticCapture"
        )
        self._capture_start.setProperty("buttonRole", "primary")
        self._capture_start.clicked.connect(self._start_capture)
        capture_actions.addWidget(self._capture_start)
        self._capture_stop = QPushButton(
            "结束诊断", objectName="stopDiagnosticCapture"
        )
        self._capture_stop.setProperty("buttonRole", "secondary")
        self._capture_stop.clicked.connect(self._stop_capture)
        capture_actions.addWidget(self._capture_stop)
        capture_actions.addStretch(1)
        control_check_layout.addLayout(capture_actions)

        capture_boundary = QLabel(
            "诊断保护开启后，按键、旋钮和摇杆动作只用于本页检查，不会发送到电脑里的其他程序。结束时请先松开全部控件。",
            objectName="roleContext",
        )
        capture_boundary.setWordWrap(True)
        control_check_layout.addWidget(capture_boundary)
        layout.addWidget(control_check)

        calibration = _card()
        calibration_layout = QVBoxLayout(calibration)
        calibration_layout.setContentsMargins(24, 20, 24, 22)
        calibration_layout.setSpacing(10)
        calibration_layout.addWidget(QLabel("JOYSTICK RECOVERY", objectName="eyebrow"))
        calibration_layout.addWidget(
            QLabel("摇杆检查与校准", objectName="inspectorTitle")
        )
        self._calibration_state = QLabel(objectName="headerValue")
        calibration_layout.addWidget(self._calibration_state)
        self._calibration_help = QLabel(objectName="muted")
        self._calibration_help.setWordWrap(True)
        calibration_layout.addWidget(self._calibration_help)
        calibration_actions = QHBoxLayout()
        self._report_joystick_issue = QPushButton(
            "检测后仍有异常", objectName="reportJoystickIssue"
        )
        self._report_joystick_issue.setProperty("buttonRole", "secondary")
        self._report_joystick_issue.clicked.connect(self._report_joystick_issue_clicked)
        calibration_actions.addWidget(self._report_joystick_issue)
        self._open_calibration = QPushButton(
            "进入摇杆校准", objectName="openJoystickCalibration"
        )
        self._open_calibration.setProperty("buttonRole", "primary")
        self._open_calibration.clicked.connect(self._open_joystick_calibration)
        calibration_actions.addWidget(self._open_calibration)
        calibration_actions.addStretch(1)
        calibration_layout.addLayout(calibration_actions)
        layout.addWidget(calibration)

        facts = _card()
        facts_layout = QGridLayout(facts)
        facts_layout.setContentsMargins(24, 20, 24, 22)
        facts_layout.setHorizontalSpacing(28)
        facts_layout.setVerticalSpacing(9)
        facts_layout.addWidget(QLabel("CONNECTED DEVICE", objectName="eyebrow"), 0, 0, 1, 4)
        facts_layout.addWidget(QLabel("连接、身份与配置", objectName="inspectorTitle"), 1, 0, 1, 4)
        self._fact_values: dict[str, QLabel] = {}
        fact_rows = (
            ("hardware", "硬件", "serial", "序列号"),
            ("port", "端口", "firmware", "固件"),
            ("build", "构建", "protocol", "协议"),
            ("mode", "模式", "platform", "平台"),
            ("host_output", "主机输出", "local_page", "设备页面"),
            ("config", "配置", "input_state", "输入状态"),
        )
        for row, (left_key, left_label, right_key, right_label) in enumerate(fact_rows, start=2):
            facts_layout.addWidget(QLabel(left_label, objectName="muted"), row, 0)
            left_value = QLabel("—", objectName="headerValue")
            left_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            facts_layout.addWidget(left_value, row, 1)
            facts_layout.addWidget(QLabel(right_label, objectName="muted"), row, 2)
            right_value = QLabel("—", objectName="headerValue")
            right_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            facts_layout.addWidget(right_value, row, 3)
            self._fact_values[left_key] = left_value
            self._fact_values[right_key] = right_value
        facts_layout.setColumnStretch(1, 1)
        facts_layout.setColumnStretch(3, 1)
        layout.addWidget(facts)

        live = _card()
        live_layout = QVBoxLayout(live)
        live_layout.setContentsMargins(24, 20, 24, 22)
        live_layout.setSpacing(10)
        live_layout.addWidget(QLabel("LIVE INPUT", objectName="eyebrow"))
        live_layout.addWidget(QLabel("实时输入监视", objectName="inspectorTitle"))
        live_layout.addWidget(QLabel("活动控件", objectName="muted"))
        self._active_controls = QLabel("—", objectName="diagnosticActiveControls")
        self._active_controls.setWordWrap(True)
        self._active_controls.setTextInteractionFlags(Qt.TextSelectableByMouse)
        live_layout.addWidget(self._active_controls)

        axis_grid = QGridLayout()
        axis_grid.setHorizontalSpacing(14)
        axis_grid.addWidget(QLabel("原始 X", objectName="muted"), 0, 0)
        axis_grid.addWidget(QLabel("原始 Y", objectName="muted"), 0, 1)
        self._raw_x = _axis_bar("diagnosticRawX")
        self._raw_y = _axis_bar("diagnosticRawY")
        axis_grid.addWidget(self._raw_x, 1, 0)
        axis_grid.addWidget(self._raw_y, 1, 1)
        axis_grid.addWidget(QLabel("滤波 X", objectName="muted"), 2, 0)
        axis_grid.addWidget(QLabel("滤波 Y", objectName="muted"), 2, 1)
        self._filtered_x = _axis_bar("diagnosticFilteredX")
        self._filtered_y = _axis_bar("diagnosticFilteredY")
        axis_grid.addWidget(self._filtered_x, 3, 0)
        axis_grid.addWidget(self._filtered_y, 3, 1)
        live_layout.addLayout(axis_grid)

        direction_row = QHBoxLayout()
        direction_row.addWidget(QLabel("方向", objectName="muted"))
        self._direction = QLabel("—", objectName="headerValue")
        direction_row.addWidget(self._direction)
        direction_row.addSpacing(20)
        direction_row.addWidget(QLabel("径向", objectName="muted"))
        self._radial = QLabel("—", objectName="headerValue")
        direction_row.addWidget(self._radial)
        direction_row.addStretch(1)
        live_layout.addLayout(direction_row)
        self._joystick_boundary = QLabel(objectName="muted")
        self._joystick_boundary.setWordWrap(True)
        live_layout.addWidget(self._joystick_boundary)
        layout.addWidget(live)

        events = _card()
        events_layout = QVBoxLayout(events)
        events_layout.setContentsMargins(24, 20, 24, 22)
        events_layout.setSpacing(10)
        event_heading = QHBoxLayout()
        event_titles = QVBoxLayout()
        event_titles.addWidget(QLabel("SESSION EVENTS", objectName="eyebrow"))
        event_titles.addWidget(QLabel("本次连接事件", objectName="inspectorTitle"))
        event_heading.addLayout(event_titles, 1)
        clear = QPushButton("清空事件", objectName="clearDiagnosticEvents")
        clear.setProperty("buttonRole", "secondary")
        clear.clicked.connect(self._view_model.clear_diagnostic_events)
        event_heading.addWidget(clear, 0, Qt.AlignTop)
        events_layout.addLayout(event_heading)
        self._events = QPlainTextEdit(objectName="diagnosticEventLog")
        self._events.setReadOnly(True)
        self._events.setMaximumBlockCount(200)
        self._events.setMinimumHeight(150)
        events_layout.addWidget(self._events)
        action_row = QHBoxLayout()
        self._copy = QPushButton("复制诊断摘要", objectName="copyDiagnosticSummary")
        self._copy.setProperty("buttonRole", "secondary")
        self._copy.clicked.connect(self._copy_summary)
        action_row.addWidget(self._copy)
        self._export = QPushButton("导出诊断摘要", objectName="exportDiagnosticSummary")
        self._export.setProperty("buttonRole", "primary")
        self._export.clicked.connect(self._export_summary)
        action_row.addWidget(self._export)
        action_row.addStretch(1)
        self._copy_state = QLabel("", objectName="muted")
        action_row.addWidget(self._copy_state)
        events_layout.addLayout(action_row)
        layout.addWidget(events)

        boundary = QLabel(
            "当前事件记录由 BORING 控制台根据协议状态变化生成，不等同于设备内部日志。当前协议没有提供电池、充电或设备日志字段，因此这里不会推测这些信息。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        layout.addWidget(boundary)
        layout.addStretch(1)

        self.setWidget(page)
        self._view_model.diagnostics_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        model = self._view_model.model
        snapshot = model.snapshot
        connected = model.state in {AppState.READY, AppState.READ_ONLY}

        if snapshot is None:
            values = {key: "—" for key in self._fact_values}
            static_render = (connected, False, *values.values())
            if static_render != self._last_static_render:
                self._render_static(connected, False, values)
                self._last_static_render = static_render
            _set_text_if_changed(self._active_controls, "—")
            self._clear_joystick(
                translate_ui_text("连接设备后显示活动控件与摇杆数据。")
            )
        else:
            versions = snapshot.versions
            status = snapshot.status
            action_engine = status.get("action_engine")
            if not isinstance(action_engine, dict):
                action_engine = {}
            protocol = (
                f"{versions.get('protocol_major', '—')}.{versions.get('protocol_minor', '—')}"
                f" · Schema {versions.get('schema_version', '—')}"
            )
            values = {
                "hardware": snapshot.identity.get("hardware_id", "—"),
                "serial": snapshot.identity.get("serial", "—"),
                "port": snapshot.port_name or "—",
                "firmware": versions.get("firmware", "—"),
                "build": status.get("build_id", versions.get("build_id", "—")),
                "protocol": protocol,
                "mode": str(status.get("operating_mode", "unknown")).upper(),
                "platform": str(status.get("platform", "unknown")).upper(),
                "host_output": str(
                    action_engine.get("host_output_state", "unknown")
                ).upper(),
                "local_page": str(action_engine.get("local_page", "unknown")).upper(),
                "config": translate_ui_text(snapshot.config_status_label),
                "input_state": translate_ui_text(
                    _input_state(status.get("inputs_neutral"))
                ),
            }
            static_render = (connected, True, *values.values())
            if static_render != self._last_static_render:
                self._render_static(connected, True, values)
                self._last_static_render = static_render
            controls = active_controls(status)
            _set_text_if_changed(
                self._active_controls, ", ".join(controls) if controls else "NONE"
            )
            self._refresh_joystick(joystick_diagnostics(status))

        self._refresh_control_check()
        self._refresh_calibration_entry()

        events = self._view_model.diagnostics.events
        event_render = (len(events), events[-1].sequence if events else 0)
        if event_render != self._last_event_render:
            self._events.setPlainText("\n".join(event.line for event in events))
            bar = self._events.verticalScrollBar()
            bar.setValue(bar.maximum())
            self._last_event_render = event_render

    def _render_static(
        self, connected: bool, has_snapshot: bool, values: dict[str, object]
    ) -> None:
        _set_text_if_changed(
            self._connection_badge,
            translate_ui_text("实时读取中" if connected else "等待设备读取"),
        )
        self._set_badge_state(connected)
        self._copy.setEnabled(has_snapshot)
        self._export.setEnabled(has_snapshot)
        for key, value in values.items():
            _set_text_if_changed(self._fact_values[key], str(value))

    def _refresh_joystick(self, diagnostics: dict[str, object] | None) -> None:
        if diagnostics is None:
            self._clear_joystick(
                translate_ui_text("当前设备未提供摇杆实时诊断数据。")
            )
            return
        joystick_render = tuple(
            diagnostics.get(field)
            for field in (
                "minimum_x",
                "maximum_x",
                "minimum_y",
                "maximum_y",
                "raw_x",
                "filtered_x",
                "raw_y",
                "filtered_y",
                "directions",
                "radial_active",
                "radial_angle_turns",
            )
        )
        if joystick_render == self._last_joystick_render:
            return
        self._last_joystick_render = joystick_render
        minimum_x = _integer(diagnostics.get("minimum_x"), 0)
        maximum_x = _integer(diagnostics.get("maximum_x"), 4095)
        minimum_y = _integer(diagnostics.get("minimum_y"), 0)
        maximum_y = _integer(diagnostics.get("maximum_y"), 4095)
        _set_axis(self._raw_x, diagnostics.get("raw_x"), minimum_x, maximum_x)
        _set_axis(self._filtered_x, diagnostics.get("filtered_x"), minimum_x, maximum_x)
        _set_axis(self._raw_y, diagnostics.get("raw_y"), minimum_y, maximum_y)
        _set_axis(self._filtered_y, diagnostics.get("filtered_y"), minimum_y, maximum_y)
        _set_text_if_changed(
            self._direction, joystick_direction_label(diagnostics.get("directions"))
        )
        if diagnostics.get("radial_active") is True:
            angle = diagnostics.get("radial_angle_turns")
            radial = f"ACTIVE · {angle}" if isinstance(angle, (int, float)) else "ACTIVE"
        else:
            radial = "CENTER"
        _set_text_if_changed(self._radial, radial)
        _set_text_if_changed(
            self._joystick_boundary,
            translate_ui_text(
                "数值来自 GET_STATUS.joystick_diagnostics；该读取不会开始校准，也不会修改设备配置。"
            ),
        )

    def _clear_joystick(self, message: str) -> None:
        joystick_render = ("unavailable", message)
        if joystick_render == self._last_joystick_render:
            return
        self._last_joystick_render = joystick_render
        for bar in (self._raw_x, self._raw_y, self._filtered_x, self._filtered_y):
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setFormat("—")
        _set_text_if_changed(self._direction, "—")
        _set_text_if_changed(self._radial, "—")
        _set_text_if_changed(self._joystick_boundary, message)

    def _set_badge_state(self, connected: bool) -> None:
        name = "statusReady" if connected else "statusWarn"
        if self._connection_badge.objectName() == name:
            return
        self._connection_badge.setObjectName(name)
        self._connection_badge.style().unpolish(self._connection_badge)
        self._connection_badge.style().polish(self._connection_badge)

    def _refresh_control_check(self) -> None:
        snapshot = self._view_model.model.snapshot
        controls = tuple(snapshot.controls) if snapshot is not None else ()
        tested = self._view_model.diagnostics.tested_controls.intersection(controls)
        total = len(controls)
        self._capture_progress.setRange(0, max(1, total))
        self._capture_progress.setValue(len(tested))
        self._capture_progress.setFormat(
            translate_ui_text(f"已测试 {len(tested)} / {total}")
        )

        key_controls = tuple(item for item in controls if item.startswith("key."))
        encoder_controls = tuple(
            item for item in controls if item.startswith("encoder.")
        )
        joystick_controls = tuple(
            item for item in controls if item.startswith("joystick.")
        )
        _set_text_if_changed(
            self._capture_groups,
            translate_ui_text(
                f"按键 {len(tested.intersection(key_controls))}/{len(key_controls)}"
                f"  ·  旋钮 {len(tested.intersection(encoder_controls))}/{len(encoder_controls)}"
                f"  ·  摇杆 {len(tested.intersection(joystick_controls))}/{len(joystick_controls)}"
            ),
        )

        pending = self._view_model.diagnostic_capture_pending
        error = self._view_model.diagnostic_capture_error
        supported = self._view_model.diagnostic_capture_supported
        active = self._view_model.diagnostic_capture_active
        if not supported:
            state = "当前固件不支持安全诊断"
            instruction = (
                "当前页面只能只读监看。请不要在这里逐项操作控件；更新到支持诊断保护的固件后，控制台才会开放安全测试。"
            )
        elif error:
            state = "诊断保护未启动"
            instruction = f"设备没有进入安全诊断：{error}"
        elif pending == "starting":
            state = "正在开启诊断保护"
            instruction = "请保持所有控件松开，等待设备停止向主机发送动作。"
        elif pending == "stopping":
            state = "正在结束诊断"
            instruction = "请松开所有控件；设备将在输入归零后恢复正常操作。"
        elif active:
            state = "诊断保护已开启"
            remaining = tuple(item for item in controls if item not in tested)
            instruction = (
                _control_instruction(remaining[0])
                if remaining
                else "全部控件均已识别。请松开所有控件，然后点击“结束诊断”。"
            )
        else:
            state = "尚未开始"
            instruction = (
                "点击“开始控件诊断”，控制台会暂时接管设备输入，再按页面提示逐项操作。"
            )
        _set_text_if_changed(self._capture_state, translate_ui_text(state))
        _set_text_if_changed(
            self._capture_instruction, translate_ui_text(instruction)
        )
        self._set_capture_state_style(active and not pending and not error)
        self._capture_start.setEnabled(
            supported and not active and not pending and snapshot is not None
        )
        self._capture_stop.setEnabled(active or pending == "starting")

    def _set_capture_state_style(self, ready: bool) -> None:
        name = "statusReady" if ready else "statusWarn"
        if self._capture_state.objectName() == name:
            return
        self._capture_state.setObjectName(name)
        self._capture_state.style().unpolish(self._capture_state)
        self._capture_state.style().polish(self._capture_state)

    def _refresh_calibration_entry(self) -> None:
        model = self._view_model.model
        snapshot = model.snapshot
        if snapshot is None:
            self._joystick_issue_reported = False
            state = "等待设备读取"
            help_text = "连接设备后，先完成上方的控件诊断，再观察回中、四方向和按压是否正常。"
            action_available = False
        else:
            features = snapshot.capabilities.get("features")
            supported = (
                isinstance(features, dict)
                and features.get("joystick_calibration") is True
            )
            joystick = snapshot.config.get("joystick")
            calibrated = isinstance(joystick, dict) and joystick.get("calibrated") is True
            if not supported:
                self._joystick_issue_reported = False
                state = "当前设备未提供摇杆校准"
                help_text = "仍可在本页查看实时数据；如果确认存在异常，请先更新到支持校准的固件。"
                action_available = False
            elif self._view_model.diagnostic_capture_active or self._view_model.diagnostic_capture_pending:
                self._joystick_issue_reported = False
                state = "请先结束控件诊断"
                help_text = "摇杆校准与控件诊断不会同时运行。请松开所有控件并结束诊断。"
                action_available = False
            elif self._view_model.draft is not None and self._view_model.draft.is_dirty:
                state = "先处理本地配置修改"
                help_text = "为避免校准覆盖尚未写入的配置，请先保存或丢弃本地修改。"
                action_available = False
            elif self._joystick_issue_reported:
                state = "已确认仍有摇杆异常"
                help_text = "校准会重新采集中立点、四周端点和死区，不会更改方向绑定。"
                action_available = model.state is AppState.READY
            else:
                required = {
                    "joystick.up",
                    "joystick.down",
                    "joystick.left",
                    "joystick.right",
                    "joystick.press",
                }
                checked = required.issubset(
                    self._view_model.diagnostics.tested_controls
                ) and snapshot.status.get("inputs_neutral") is True
                state = (
                    "四方向和按压均已识别"
                    if checked
                    else ("当前配置：已校准" if calibrated else "当前配置：尚未校准")
                )
                help_text = (
                    "这只说明实体事件已被识别。如果回中后仍有方向、存在误触或无法到达边缘，请选择“检测后仍有异常”。"
                    if checked
                    else "先在本页检查回中、四方向与按压。只有出现误触、漏触或边缘行程不足时，才需要重新校准。"
                )
                action_available = model.state is AppState.READY
        _set_translatable_text_if_changed(self._calibration_state, state)
        _set_translatable_text_if_changed(self._calibration_help, help_text)
        self._report_joystick_issue.setVisible(not self._joystick_issue_reported)
        self._report_joystick_issue.setEnabled(action_available)
        self._open_calibration.setVisible(self._joystick_issue_reported)
        self._open_calibration.setEnabled(
            self._joystick_issue_reported and action_available
        )

    def _report_joystick_issue_clicked(self) -> None:
        self._joystick_issue_reported = True
        self._refresh_calibration_entry()

    def _open_joystick_calibration(self) -> None:
        try:
            self._view_model.navigate("joystick")
        except ValueError as exc:
            QMessageBox.warning(self, "无法进入摇杆校准", str(exc))

    def _start_capture(self) -> None:
        try:
            self._view_model.start_diagnostic_capture()
        except ValueError as exc:
            QMessageBox.warning(self, "无法开始控件诊断", str(exc))

    def _stop_capture(self) -> None:
        self._view_model.stop_diagnostic_capture()

    def _copy_summary(self) -> None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            return
        application.clipboard().setText(self._view_model.diagnostic_report_text())
        self._copy_state.setText(translate_ui_text("诊断摘要已复制"))

    def _export_summary(self) -> None:
        file_name, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出诊断摘要",
            "BORING-diagnostics.json",
            "BORING 诊断摘要 (*.json);;JSON (*.json)",
        )
        if not file_name:
            return
        try:
            self._view_model.export_diagnostic_summary(Path(file_name))
        except OSError as exc:
            QMessageBox.warning(self, "导出诊断摘要失败", str(exc))
            return
        QMessageBox.information(self, "诊断摘要已导出", file_name)


def _card() -> QFrame:
    return QFrame(objectName="card")


def _axis_bar(object_name: str) -> QProgressBar:
    bar = QProgressBar(objectName=object_name)
    bar.setRange(0, 4095)
    bar.setValue(0)
    bar.setFormat("0")
    return bar


def _set_axis(bar: QProgressBar, value: object, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat("—")
        return
    if maximum <= minimum:
        minimum, maximum = 0, max(4095, value)
    bar.setRange(minimum, maximum)
    bar.setValue(max(minimum, min(maximum, value)))
    bar.setFormat(str(value))


def _integer(value: object, fallback: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _input_state(value: object) -> str:
    if value is True:
        return "全部松开"
    if value is False:
        return "存在活动输入"
    return "未报告"


def _control_instruction(control_id: str) -> str:
    if control_id.startswith("key."):
        return f"下一步：按下并松开按键 {control_id.removeprefix('key.')}。"
    instructions = {
        "encoder.ccw": "下一步：将旋钮逆时针转动一格。",
        "encoder.cw": "下一步：将旋钮顺时针转动一格。",
        "encoder.press": "下一步：按下并松开旋钮。",
        "joystick.up": "下一步：将摇杆向上拨动一次，再松手回中。",
        "joystick.down": "下一步：将摇杆向下拨动一次，再松手回中。",
        "joystick.left": "下一步：将摇杆向左拨动一次，再松手回中。",
        "joystick.right": "下一步：将摇杆向右拨动一次，再松手回中。",
        "joystick.press": "下一步：垂直按下并松开摇杆；轻微方向晃动不算失败。",
    }
    return instructions.get(control_id, f"下一步：操作 {control_id} 一次。")


def _set_text_if_changed(label: QLabel, text: str) -> None:
    if label.text() != text:
        label.setText(text)


def _set_translatable_text_if_changed(label: QLabel, source: str) -> None:
    if label.property("boringI18nSource_text") != source:
        set_translatable_text(label, source)
