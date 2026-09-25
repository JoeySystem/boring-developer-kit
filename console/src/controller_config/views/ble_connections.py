"""Bluetooth host management, driven by device status rather than command ACKs."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu,
    QMessageBox, QPushButton, QVBoxLayout,
)

from controller_config.i18n import SKIP_TRANSLATION_PROPERTY, translate_ui_text as tr
from controller_config.models import AppState


class BleConnectionsDialog(QDialog):
    def __init__(self, view_model, snapshot, state, parent=None, *, settings=None):
        super().__init__(parent)
        self.setObjectName("bleSlotsDialog")
        self.setWindowTitle(tr("蓝牙连接"))
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(520)
        self._vm = view_model
        self._snapshot = snapshot
        self._serial = str(snapshot.identity["serial"])
        self._hardware = str(snapshot.identity["hardware_id"])
        self._settings = settings if settings is not None else QSettings()
        self._slots = snapshot.status["codex_micro"]["slots"]
        self._live = state in {AppState.READY, AppState.READ_ONLY}
        self._writable = state is AppState.READY
        self._pending = None
        self._request_status = None
        self._pairing_ready = False
        self._busy = False
        self._expired = False
        self._success_slot = None
        self._feedback = ""
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._timed_out)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="bleSlotsCard")
        outer.addWidget(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(14)
        box.addWidget(QLabel(tr("蓝牙连接"), objectName="inspectorTitle"))
        description = QLabel(tr("最多记住 3 台电脑，每次通过蓝牙连接 1 台。"), objectName="muted")
        description.setWordWrap(True)
        box.addWidget(description)
        self._rows = {}
        for slot in range(1, 4):
            row = QFrame(objectName="bleComputerRow")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(10)
            copy = QVBoxLayout()
            copy.setSpacing(4)
            name = QLabel(objectName="bleComputerName")
            name.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            name.setWordWrap(True)
            status = QLabel(objectName="bleComputerStatus")
            status.setWordWrap(True)
            copy.addWidget(name)
            copy.addWidget(status)
            layout.addLayout(copy, 1)
            action = QPushButton(objectName="bleComputerAction")
            action.setProperty("bleSlot", slot)
            action.setProperty("bleAction", "select")
            action.clicked.connect(lambda _=False, value=slot: self._start(value, "switch"))
            layout.addWidget(action)
            more = QPushButton(tr("更多"), objectName="bleComputerMore")
            more.setAccessibleName(tr("电脑 {slot} 的更多操作").format(slot=slot))
            more.clicked.connect(lambda _=False, value=slot: self._show_more(value))
            layout.addWidget(more)
            box.addWidget(row)
            self._rows[slot] = (row, name, status, action, more)
        self._add = QPushButton(tr("添加电脑"), objectName="primary")
        self._add.clicked.connect(self._add_computer)
        box.addWidget(self._add)
        self._message = QLabel(objectName="bleConnectionMessage")
        self._message.setWordWrap(True)
        box.addWidget(self._message)
        self._help = QPushButton(tr("如何用设备切换"), objectName="bleConnectionHelp")
        self._help.clicked.connect(self._show_physical_help)
        box.addWidget(self._help)
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.accept)
        close.setDefault(True)
        box.addWidget(close)
        close.setFocus(Qt.OtherFocusReason)
        self._vm.changed.connect(self._update_model)
        self._vm.ble_slot_failed.connect(self._command_failed)
        self._vm.ble_name.changed.connect(self._name_updated)
        self.finished.connect(self._detach)
        self._render()
        name_session = self._vm.ble_name
        if name_session.connected and name_session.supported and not name_session.busy:
            name_session.read()

    def _alias_key(self, slot):
        return f"bluetooth/computers/{self._hardware}/{self._serial}/{slot}"

    def _name(self, slot):
        return str(self._settings.value(self._alias_key(slot), "") or tr("电脑 {slot}").format(slot=slot))

    def _render(self):
        active = self._snapshot.status["codex_micro"].get("active_slot")
        can_operate = self._writable and not self._busy and not self._vm.ble_name.busy
        for slot, widgets in self._rows.items():
            row, name, status, action, more = widgets
            item = self._slots[slot - 1]
            paired = item.get("paired") is True
            row.setVisible(paired)
            name.setText(self._name(slot))
            connected = self._live and item.get("connected") is True
            status.setText(tr("已连接") if connected else tr("连接状态待确认") if not self._live
                           else tr("已选用 · 尚未连接") if active == slot else tr("已配对"))
            action.setText(tr("已连接") if connected else tr("重新连接") if active == slot else tr("切换到此电脑"))
            action.setVisible(not connected)
            action.setEnabled(can_operate and not connected)
            more.setEnabled(self._live and not self._busy)
        empty = next((i for i, item in enumerate(self._slots, 1) if not item.get("paired")), None)
        self._add.setVisible(empty is not None)
        self._add.setEnabled(can_operate)
        message = self._feedback
        if not self._live:
            message = tr("当前电脑已与设备断开，无法确认连接结果。请在目标电脑查看；接回 USB 后可在这里确认。") if self._pending else tr("设备已断开，连接后可继续操作。")
            if self._pending and self._pending[1] == "add":
                result = self._vm.ble_name.result
                if result and self._vm.ble_name.serial == self._serial:
                    message = tr("当前蓝牙连接已断开。请在目标电脑的蓝牙设置中选择“{name}”；本机暂时无法确认结果。").format(name=result["active_name"])
        elif not self._writable:
            message = tr("设备当前只读，暂时无法切换或添加电脑。")
        elif not message and self._snapshot.connection_kind == "bluetooth":
            message = tr("切换或添加电脑时，当前蓝牙连接会断开。")
        elif not message and empty is None:
            message = tr("已记住 3 台电脑。要更换，请在对应电脑的“更多”中选择“忘记此电脑”。")
        self._message.setText(message)
        self._message.setVisible(bool(message))

    def _add_computer(self):
        slot = next((i for i, item in enumerate(self._slots, 1) if not item.get("paired")), None)
        if slot is not None:
            self._start(slot, "add")

    def _start(self, slot, kind):
        if self._busy or not self._writable:
            return
        self._pending = (slot, kind)
        self._request_status = self._snapshot.status
        self._pairing_ready = False
        self._expired = False
        self._success_slot = None
        self._busy = True
        self._feedback = tr("正在准备连接…") if kind == "add" else tr("正在忘记此电脑…") if kind == "forget" else tr("正在切换，等待设备确认…")
        self._timeout.start(8000)
        self._render()
        try:
            if kind == "forget":
                self._vm.clear_ble_slot(slot)
            else:
                self._vm.select_ble_slot(slot)
        except ValueError as exc:
            self._fail(str(exc))

    def _update_model(self, model):
        snapshot = model.snapshot
        same_device = snapshot is not None and str(snapshot.identity.get("serial")) == self._serial and str(snapshot.identity.get("hardware_id")) == self._hardware
        slots = snapshot.status.get("codex_micro", {}).get("slots", []) if same_device else []
        self._live = same_device and len(slots) == 3 and model.state in {AppState.READY, AppState.READ_ONLY}
        self._writable = self._live and model.state is AppState.READY
        if self._live:
            for slot, (old, new) in enumerate(zip(self._slots, slots), 1):
                if old.get("paired") and not new.get("paired"):
                    self._settings.remove(self._alias_key(slot))
            self._snapshot = snapshot
            self._slots = slots
            if self._success_slot and not slots[self._success_slot - 1].get("connected"):
                self._success_slot = None
                self._feedback = ""
            if self._pending and snapshot.status is not self._request_status:
                slot, kind = self._pending
                item = slots[slot - 1]
                active = snapshot.status.get("codex_micro", {}).get("active_slot")
                if kind == "forget" and not item.get("paired"):
                    self._settings.remove(self._alias_key(slot))
                    self._feedback = tr("已忘记此电脑。如需连接新电脑，请点“添加电脑”。")
                    self._finish_request()
                elif active == slot and item.get("connected") is True:
                    self._success_slot = slot
                    self._feedback = tr("{name} 已连接，可以开始使用。").format(name=self._name(slot))
                    self._finish_request()
                elif kind == "add" and active == slot and not item.get("paired"):
                    if not self._pairing_ready:
                        self._pairing_ready = True
                        self._timeout.start(60000)
                    if not self._expired:
                        self._pairing_message()
                elif kind == "switch" and active == slot and not self._expired:
                    self._feedback = tr("已切换，正在等待目标电脑连接。请确认那台电脑的蓝牙已打开。")
        self._render()

    def _pairing_message(self):
        result = self._vm.ble_name.result
        if result and self._vm.ble_name.serial == self._serial:
            name = result["active_name"]
            self._feedback = tr("在要连接的电脑上打开蓝牙设置，选择“{name}”。连接成功后，这里会自动确认。").format(name=name)
        else:
            self._feedback = tr("在要连接的电脑上打开蓝牙设置，选择本设备。暂未读到设备蓝牙名称，可在设备设置中查看。")
        if self._snapshot.status.get("codex_micro", {}).get("ble_services_ready") is False:
            self._feedback = tr("设备蓝牙服务尚未就绪，请稍后再试。")
            self._finish_request()

    def _name_updated(self):
        if self._pending and self._pairing_ready and not self._expired:
            self._pairing_message()
        self._render()

    def _finish_request(self):
        self._timeout.stop()
        self._pending = None
        self._busy = False

    def _timed_out(self):
        self._expired = True
        self._busy = False
        self._feedback = tr("暂未确认连接结果。请检查目标电脑的蓝牙设置；连接状态恢复后会自动更新。")
        self._render()

    def _command_failed(self, command, message):
        if self._pending and command == ("BLE_SLOT_CLEAR" if self._pending[1] == "forget" else "BLE_SLOT_SELECT"):
            self._fail(message)

    def _fail(self, message):
        self._finish_request()
        self._feedback = tr("操作未完成：{reason}").format(reason=message)
        self._render()

    def _show_more(self, slot):
        menu = QMenu(self)
        menu.addAction(tr("备注名称…"), lambda: self._rename(slot))
        forget = menu.addAction(tr("忘记此电脑…"), lambda: self._forget(slot))
        forget.setEnabled(self._writable)
        button = self._rows[slot][4]
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _rename(self, slot):
        name, accepted = QInputDialog.getText(self, tr("备注名称"), tr("例如：办公室电脑。仅保存在本机。"), text=self._name(slot))
        if accepted:
            name = name.strip()
            if name:
                self._settings.setValue(self._alias_key(slot), name)
            else:
                self._settings.remove(self._alias_key(slot))
            self._render()

    def _forget(self, slot):
        text = tr("忘记“{name}”后，需要重新配对才能连接；设备会开始等待新的配对。").format(name=self._name(slot))
        if self._slots[slot - 1].get("connected"):
            text += "\n" + tr("当前蓝牙连接将断开。")
        question = QMessageBox(QMessageBox.Warning, tr("忘记此电脑"), text, parent=self)
        forget = question.addButton(tr("忘记此电脑"), QMessageBox.DestructiveRole)
        cancel = question.addButton(tr("取消"), QMessageBox.RejectRole)
        question.setDefaultButton(cancel)
        question.exec()
        if question.clickedButton() is forget:
            self._start(slot, "forget")

    def _show_physical_help(self):
        from controller_config.views.device_silhouette import DeviceModelCanvas
        dialog = QDialog(self)
        dialog.setObjectName("bleSlotsDialog")
        dialog.setWindowFlag(Qt.FramelessWindowHint, True)
        dialog.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame(objectName="bleSlotsCard")
        outer.addWidget(card)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 20, 24, 20)
        box.addWidget(QLabel(tr("如何用设备切换"), objectName="inspectorTitle"))
        choices = QHBoxLayout()
        box.addLayout(choices)
        model = DeviceModelCanvas(None, {}, None, card)
        box.addWidget(model, 0, Qt.AlignCenter)
        tip = QLabel(tr("同时短按亮起的两颗键，切换到对应电脑。"))
        tip.setWordWrap(True)
        box.addWidget(tip)
        warning = QLabel(tr("不要长按：按住约 3 秒会忘记该电脑并重新配对。"), objectName="muted")
        warning.setWordWrap(True)
        box.addWidget(warning)
        buttons = []
        def select(slot):
            model.set_highlighted_controls(("key.3", f"key.{slot + 7}"))
            model.set_control_labels({"key.3": tr("同时短按"), f"key.{slot + 7}": self._name(slot)})
            tip.setText(tr("同时短按亮起的两颗键，切换到对应电脑。") if self._slots[slot - 1].get("paired") else tr("这里还没有保存电脑，请返回后选择“添加电脑”。"))
            model.preview_key_press("key.3")
            model.preview_key_press(f"key.{slot + 7}")
            for index, button in enumerate(buttons, 1):
                button.setChecked(index == slot)
        for slot in range(1, 4):
            button = QPushButton(self._name(slot), objectName="bleHelpTarget")
            button.setProperty(SKIP_TRANSLATION_PROPERTY, True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, value=slot: select(value))
            choices.addWidget(button)
            buttons.append(button)
        close = QPushButton(tr("返回"))
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        select(1)
        dialog.exec()
        dialog.deleteLater()

    def _detach(self, _result):
        self.finished.disconnect(self._detach)
        self._timeout.stop()
        self._vm.changed.disconnect(self._update_model)
        self._vm.ble_slot_failed.disconnect(self._command_failed)
        self._vm.ble_name.changed.disconnect(self._name_updated)
