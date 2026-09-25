from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QWidget

from controller_config.i18n import LanguageManager, translate_ui_text
from controller_config.views.desktop_update import DesktopUpdateUi
from controller_config.views.main_window import MainWindow
from controller_config.views.preferences_editor import BleNameEditor


class NameSession(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.serial = 'CP01-1234'
        self.supported = self.connected = True
        self.busy = False
        self.max_bytes = 24
        self.draft = 'Boring Mist'
        self.result = dict(saved_name='Boring Mist', active_name='Boring Mist',
                           default_name='Boring Mist', restart_required=False)
        self.message = self.technical = ''
        self.calls = []
        self.failure = False

    @property
    def dirty(self):
        return self.result is None or self.draft != self.result['saved_name']

    def edit(self, value):
        self.draft = value
        self.changed.emit()

    def save(self):
        self.calls.append(('save', self.draft))
        if self.failure:
            raise ValueError('名称保存失败，输入已保留')
        self.busy = True
        self.message = '正在读回确认保存结果…'
        self.changed.emit()

    def read(self):
        self.calls.append(('read',))

    def restore_default(self):
        self.calls.append(('restore', self.result['default_name']))

    def pending_drafts(self):
        return ((self.serial, self.draft),) if self.dirty else ()


class NameVm(QObject):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.ble_name = NameSession()
        self.reason = ''

    def ble_name_write_block_reason(self):
        return self.reason


@pytest.fixture
def editor(qtbot, qapp):
    previous = qapp.property("boringUiLanguage")
    qapp.setProperty("boringUiLanguage", "zh_CN")
    vm = NameVm()
    widget = BleNameEditor(vm)
    qtbot.addWidget(widget)
    widget.show()
    yield widget, vm
    qapp.setProperty("boringUiLanguage", previous)


def edit(widget, text):
    # QText input may come from an IME or clipboard; emit the user-edit signal.
    widget.name_input.setText(text)
    widget.name_input.textEdited.emit(text)


def test_default_name_keeps_one_write_action_and_read_failure_reuses_it(editor, qtbot):
    widget, vm = editor
    visible_actions = {
        button.objectName()
        for button in widget.findChildren(QPushButton)
        if button.isVisible()
    }
    assert visible_actions == {'saveBleName', 'bleNameHelpToggle'}
    assert not widget.name_help.isVisible()

    vm.ble_name.result = None
    vm.ble_name.draft = ''
    vm.ble_name.message = '名称读取失败，输入已保留'
    vm.ble_name.changed.emit()
    assert widget.save_button.text() == '重新读取名称'
    assert widget.save_button.isEnabled()
    assert not widget.name_help_toggle.isVisible()
    qtbot.mouseClick(widget.save_button, Qt.LeftButton)
    assert vm.ble_name.calls[-1] == ('read',)


def test_restore_default_only_appears_for_custom_saved_name(editor, qtbot):
    widget, vm = editor
    assert not widget.default_button.isVisible()
    vm.ble_name.result.update(saved_name='Office Mist', active_name='Office Mist')
    vm.ble_name.draft = 'Office Mist'
    vm.ble_name.changed.emit()
    assert widget.default_button.isVisible()
    qtbot.mouseClick(widget.default_button, Qt.LeftButton)
    assert vm.ble_name.calls[-1] == ('restore', 'Boring Mist')


def test_name_bytes_and_validation_do_not_truncate_input(editor):
    widget, vm = editor
    edit(widget, '办公室键盘')
    assert widget.counter.text() == '15 / 24 UTF-8 bytes'
    assert widget.save_button.isEnabled()
    too_long = '键' * 9
    edit(widget, too_long)
    assert vm.ble_name.draft == widget.name_input.text() == too_long
    assert widget.counter.text() == '27 / 24 UTF-8 bytes'
    assert not widget.save_button.isEnabled()
    assert '24' in widget.validation.text()
    for bad in ('', ' Leading', 'Trailing ', 'bad\nname'):
        edit(widget, bad)
        assert not widget.save_button.isEnabled()
        assert widget.validation.text()


def test_save_waits_for_readback_and_retains_failed_input(editor, qtbot):
    widget, vm = editor
    edit(widget, 'Office Mist')
    vm.ble_name.failure = True
    qtbot.mouseClick(widget.save_button, Qt.LeftButton)
    assert widget.name_input.text() == 'Office Mist'
    assert widget.saved.text() == 'Boring Mist'
    assert '输入已保留' in widget.message.text()
    vm.ble_name.failure = False
    qtbot.mouseClick(widget.save_button, Qt.LeftButton)
    assert not widget.save_button.isEnabled()
    assert widget.saved.text() == 'Boring Mist'
    assert '确认保存结果' in widget.message.text()
    vm.ble_name.busy = False
    vm.ble_name.result.update(saved_name='Office Mist', restart_required=True)
    vm.ble_name.message = '已保存，下次正常重启生效'
    vm.ble_name.changed.emit()
    assert widget.saved.text() == 'Office Mist'
    assert widget.active.text() == 'Boring Mist'
    assert '关机再开机' in widget.restart.text()
    assert not widget.save_button.isEnabled()
    qtbot.mouseClick(widget.default_button, Qt.LeftButton)
    assert vm.ble_name.calls[-1] == ('restore', 'Boring Mist')


def test_reconnect_confirms_active_name_and_help_does_not_change_pairing(editor, qtbot):
    widget, vm = editor
    vm.ble_name.result.update(saved_name='Office Mist', restart_required=True)
    vm.ble_name.draft = 'Office Mist'
    vm.ble_name.changed.emit()
    assert widget.restart.isVisible()
    assert widget.active.text() == 'Boring Mist'
    qtbot.mouseClick(widget.name_help_toggle, Qt.LeftButton)
    assert widget.name_help.isVisible()
    assert '退出并重新打开' in widget.name_help.text()
    assert vm.ble_name.calls == []

    vm.ble_name.result.update(active_name='Office Mist', restart_required=False)
    vm.ble_name.message = '已保存，当前名称已生效'
    vm.ble_name.changed.emit()
    assert not widget.restart.isVisible()
    assert widget.message.isVisible()
    assert '已生效' in widget.message.text()
    assert widget.name_help.isVisible()  # Preserve the user's expanded help.


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_name_refresh_help_has_explicit_three_language_copy(language):
    for source in (
        '电脑仍显示旧名称？',
        '请将设备关机再开机；重新连接后，控制台会自动确认新名称是否生效。',
        '先确认新名称已生效，再重新打开电脑的蓝牙设置。Mac 请退出并重新打开“系统设置”。旧条目不代表设备仍在线，无需为改名删除配对。',
    ):
        from controller_config.text_catalog import TextCatalog
        translated = TextCatalog.load().translate(source, language)
        assert translated
        if language != 'zh_CN':
            assert translated != source


def test_offline_old_firmware_and_maintenance_are_explained(editor):
    widget, vm = editor
    edit(widget, '离线草稿')
    vm.ble_name.connected = False
    vm.reason = '离线草稿，连接设备后可保存'
    vm.ble_name.message = vm.reason
    vm.changed.emit(None)
    assert widget.name_input.isEnabled()
    assert widget.name_input.text() == '离线草稿'
    assert not widget.save_button.isEnabled()
    assert '连接' in widget.reason.text()
    assert not widget.message.isVisible()
    vm.ble_name.supported = False
    vm.reason = '此固件暂不支持修改蓝牙名称'
    vm.changed.emit(None)
    assert not widget.name_input.isEnabled()
    assert not widget.save_button.isEnabled()
    assert not vm.ble_name.calls
    vm.ble_name.supported = vm.ble_name.connected = True
    vm.reason = '设备维护或写入尚未结束，请稍后修改蓝牙名称'
    vm.changed.emit(None)
    assert not widget.save_button.isEnabled()
    assert '维护' in widget.reason.text()
    vm.reason = '设备当前只读，不能保存蓝牙名称'
    vm.changed.emit(None)
    assert not widget.save_button.isEnabled()


@pytest.mark.parametrize('language,save_label,read_label,limit_fragment', [
    ('zh_CN', '保存名称', '重新读取名称', '字节'),
    ('en_US', 'Save name', 'Read name again', 'bytes'),
    ('ja_JP', '名前を保存', '名前を再読み取り', 'バイト'),
])
def test_three_languages_keep_device_values_literal(
    editor, qapp, language, save_label, read_label, limit_fragment
):
    widget, vm = editor
    manager = LanguageManager(qapp, initial_language='zh_CN', persist=False)
    vm.ble_name.result.update(saved_name='保存名称', active_name='<b>Device</b>')
    vm.ble_name.draft = '保存名称'
    vm.ble_name.changed.emit()
    manager.set_language(language)
    manager.retranslate_widget_tree(widget)
    assert widget.save_button.text() == save_label
    assert widget.saved.text() == '保存名称'
    assert widget.active.text() == '<b>Device</b>'
    assert widget.active.textFormat() == Qt.PlainText
    assert widget.identity.text() == 'CP01-1234'
    edit(widget, '键' * 9)
    assert limit_fragment in widget.validation.text()
    vm.ble_name.result = None
    vm.ble_name.draft = ''
    vm.ble_name.changed.emit()
    manager.retranslate_widget_tree(widget)
    assert widget.save_button.text() == read_label
    manager.set_language('zh_CN')
    qapp.removeEventFilter(manager)
    manager.deleteLater()


def test_name_session_refresh_keeps_editor_and_focus(editor):
    widget, vm = editor
    original = widget.name_input
    edit(widget, 'Joey Mist')
    original.setCursorPosition(4)
    vm.ble_name.message = '正在读取蓝牙名称…'
    vm.ble_name.changed.emit()
    assert widget.name_input is original
    assert original.cursorPosition() == 4
    vm.ble_name.serial = 'CP01-5678'
    vm.ble_name.draft = 'Other device'
    vm.ble_name.result.update(saved_name='Other device', active_name='Other device')
    vm.ble_name.changed.emit()
    assert widget.identity.text() == 'CP01-5678'
    assert original.text() == 'Other device'


def test_busy_name_operation_blocks_normal_close(monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    window = SimpleNamespace(
        _view_model=SimpleNamespace(ble_name=SimpleNamespace(busy=True),
                                    normal_agent=SimpleNamespace(busy=False)),
        _connection_transition_model=None,
        _cancel_connection_transition=lambda: None,
    )
    event = QCloseEvent()
    MainWindow.closeEvent(window, event)
    assert not event.isAccepted()
    assert warnings


def test_name_draft_exit_confirmation_keeps_names_and_can_cancel(qtbot, monkeypatch):
    window = QWidget()
    qtbot.addWidget(window)
    session = NameSession()
    session.edit('保存名称')
    window._view_model = SimpleNamespace(ble_name=session)
    seen = []
    def inspect(prompt):
        seen.append(prompt.informativeText())
        assert prompt.textFormat() == Qt.PlainText
        assert all(label.property('boringI18nSkip') for label in prompt.findChildren(QLabel))
        return QMessageBox.Cancel
    monkeypatch.setattr(QMessageBox, 'exec', inspect)
    assert not MainWindow._confirm_discard_ble_names(window)
    assert seen == ['CP01-1234  保存名称']
    assert session.draft == '保存名称'


def test_restart_update_respects_name_draft_cancel():
    ui = SimpleNamespace(blocked_reason=lambda: '', window=SimpleNamespace(
        _confirm_discard_ble_names=lambda: False))
    assert DesktopUpdateUi.prepare_restart(ui) is False


def test_restart_update_blocks_busy_name_before_workspace_operations():
    ui = SimpleNamespace(
        vm=SimpleNamespace(ble_name=SimpleNamespace(busy=True),
                           normal_agent=SimpleNamespace(busy=False, pending_drafts=lambda: ()),
                           host_tasks=SimpleNamespace(running=False)),
        window=SimpleNamespace(findChild=lambda *_: None),
    )
    assert DesktopUpdateUi.blocked_reason(ui) == '蓝牙名称正在保存或读回，请稍后操作'
