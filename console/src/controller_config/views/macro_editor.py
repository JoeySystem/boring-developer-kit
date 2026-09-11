from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from controller_config.drafts import macro_steps_encoded_size
from controller_config.i18n import set_translatable_text
from controller_config.protocol.contract import ConfigEditorRules


STEP_LABELS = {
    "tap": "敲击按键",
    "press": "按下按键",
    "release": "释放按键",
    "text": "ASCII 文本",
    "delay": "延时",
}


class MacroStepRow(QWidget):
    changed = Signal()

    def __init__(
        self,
        step: dict[str, Any],
        key_choices: tuple[tuple[str, int], ...],
        rules: ConfigEditorRules,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_choices = key_choices
        self._rules = rules
        self._value_widget: QWidget | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._op = QComboBox(objectName="macroStepOperation")
        for op, label in STEP_LABELS.items():
            self._op.addItem(label, op)
        index = self._op.findData(step.get("op"))
        self._op.setCurrentIndex(max(0, index))
        self._op.currentIndexChanged.connect(self._operation_changed)
        layout.addWidget(self._op)
        self._value_layout = QHBoxLayout()
        layout.addLayout(self._value_layout, 1)
        self._build_value(step)

    def step(self) -> dict[str, Any]:
        op = str(self._op.currentData())
        if op in {"tap", "press", "release"} and isinstance(self._value_widget, QComboBox):
            return {"op": op, "usage": int(self._value_widget.currentData())}
        if op == "text" and isinstance(self._value_widget, QLineEdit):
            return {"op": op, "text": self._value_widget.text()}
        if op == "delay" and isinstance(self._value_widget, QSpinBox):
            return {"op": op, "duration_ms": self._value_widget.value()}
        raise ValueError("按键序列步骤编辑器状态无效")

    def _operation_changed(self) -> None:
        self._build_value({"op": self._op.currentData()})
        self.changed.emit()

    def _build_value(self, step: dict[str, Any]) -> None:
        if self._value_widget is not None:
            self._value_layout.removeWidget(self._value_widget)
            self._value_widget.deleteLater()
        op = str(self._op.currentData())
        if op in {"tap", "press", "release"}:
            widget = QComboBox(objectName="macroStepUsage")
            current = step.get("usage", 4)
            choices = list(self._key_choices)
            if isinstance(current, int) and all(value != current for _label, value in choices):
                choices.insert(0, (f"当前高级值 · Usage {current}", current))
            for label, value in choices:
                widget.addItem(label, value)
            widget.setCurrentIndex(max(0, widget.findData(current)))
            widget.currentIndexChanged.connect(self.changed)
        elif op == "text":
            widget = QLineEdit(str(step.get("text", "")), objectName="macroStepText")
            widget.setMaxLength(self._rules.macro_text_max_length)
            widget.setPlaceholderText("可打印 ASCII 文本")
            widget.textChanged.connect(self.changed)
        else:
            widget = QSpinBox(objectName="macroStepDelay")
            delay_range = self._rules.macro_delay_ms
            widget.setRange(delay_range.minimum, delay_range.maximum)
            default_duration = max(delay_range.minimum, min(100, delay_range.maximum))
            duration = step.get("duration_ms", default_duration)
            widget.setValue(duration if isinstance(duration, int) else default_duration)
            widget.setSuffix(" ms")
            widget.valueChanged.connect(self.changed)
        self._value_widget = widget
        self._value_layout.addWidget(widget)


class MacroEditor(QWidget):
    def __init__(
        self,
        steps: list[dict[str, Any]],
        key_choices: tuple[tuple[str, int], ...],
        *,
        byte_limit: int,
        rules: ConfigEditorRules,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key_choices = key_choices
        self._byte_limit = byte_limit
        self._rules = rules
        self._rows: list[MacroStepRow] = []
        self._add_buttons: list[QPushButton] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._rows_layout = QVBoxLayout()
        layout.addLayout(self._rows_layout)
        add_buttons = QHBoxLayout()
        for op in ("tap", "text", "delay", "press", "release"):
            button = QPushButton(f"+ {STEP_LABELS[op]}", objectName="secondary")
            button.clicked.connect(lambda _checked=False, value=op: self._add_step(value))
            self._add_buttons.append(button)
            add_buttons.addWidget(button)
        add_buttons.addStretch(1)
        layout.addLayout(add_buttons)
        self._summary = QLabel(objectName="macroCapacitySummary")
        layout.addWidget(self._summary)
        initial_steps = steps or [{"op": "tap", "usage": 4}]
        for step in initial_steps:
            self._append_row(step)
        self._rebuild_rows()

    def steps(self) -> list[dict[str, Any]]:
        return [row.step() for row in self._rows]

    def _append_row(self, step: dict[str, Any]) -> None:
        row = MacroStepRow(step, self._key_choices, self._rules)
        row.changed.connect(self._update_summary)
        self._rows.append(row)

    def _add_step(self, op: str) -> None:
        if len(self._rows) >= self._rules.macro_steps_max_items:
            return
        delay_range = self._rules.macro_delay_ms
        default_duration = max(delay_range.minimum, min(100, delay_range.maximum))
        defaults: dict[str, dict[str, Any]] = {
            "tap": {"op": "tap", "usage": 4},
            "press": {"op": "press", "usage": 4},
            "release": {"op": "release", "usage": 4},
            "text": {"op": "text", "text": ""},
            "delay": {"op": "delay", "duration_ms": default_duration},
        }
        self._append_row(defaults[op])
        self._rebuild_rows()

    def _move_row(self, row: MacroStepRow, offset: int) -> None:
        index = self._rows.index(row)
        target = index + offset
        if target < 0 or target >= len(self._rows):
            return
        self._rows[index], self._rows[target] = self._rows[target], self._rows[index]
        self._rebuild_rows()

    def _remove_row(self, row: MacroStepRow) -> None:
        if len(self._rows) <= 1:
            return
        self._rows.remove(row)
        row.deleteLater()
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        old_wrappers: list[QWidget] = []
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                old_wrappers.append(widget)
        for index, row in enumerate(self._rows):
            wrapper = QWidget()
            layout = QHBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(QLabel(str(index + 1)))
            layout.addWidget(row, 1)
            up = QPushButton("↑", objectName="secondary")
            up.setAccessibleName(f"上移第 {index + 1} 个按键序列步骤")
            up.setEnabled(index > 0)
            up.clicked.connect(lambda _checked=False, current=row: self._move_row(current, -1))
            layout.addWidget(up)
            down = QPushButton("↓", objectName="secondary")
            down.setAccessibleName(f"下移第 {index + 1} 个按键序列步骤")
            down.setEnabled(index < len(self._rows) - 1)
            down.clicked.connect(lambda _checked=False, current=row: self._move_row(current, 1))
            layout.addWidget(down)
            remove = QPushButton("删除", objectName="secondary")
            remove.setEnabled(len(self._rows) > 1)
            remove.clicked.connect(lambda _checked=False, current=row: self._remove_row(current))
            layout.addWidget(remove)
            self._rows_layout.addWidget(wrapper)
        for wrapper in old_wrappers:
            wrapper.deleteLater()
        can_add = len(self._rows) < self._rules.macro_steps_max_items
        for button in self._add_buttons:
            button.setEnabled(can_add)
        self._update_summary()

    def _update_summary(self) -> None:
        steps = self.steps()
        encoded = macro_steps_encoded_size(steps)
        duration = sum(
            int(step.get("duration_ms", 0))
            for step in steps
            if step.get("op") == "delay"
        )
        state = "容量内" if encoded <= self._byte_limit else "已超限"
        set_translatable_text(
            self._summary,
            f"{len(steps)} 个步骤 · 编码 {encoded}/{self._byte_limit} 字节 · 显式延时 {duration} ms · {state}",
        )
