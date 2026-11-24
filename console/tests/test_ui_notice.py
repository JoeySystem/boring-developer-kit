"""Banners and the dialog shell in place of QMessageBox (design decision, 弹窗改横幅盘点)."""
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton, QWidget

from controller_config.views.ui import BannerStack, notice
from controller_config.views.ui.banner import Banner


class _Window(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(900, 600)
        self._banners = BannerStack(self)


def test_notify_puts_a_banner_on_the_main_window_and_errors_stay(qtbot, notice_log):
    window = _Window()
    qtbot.addWidget(window)
    window.show()
    notice.notify(window, "无法导入扩展", "磁盘已满")
    banner = window._banners.banners()[0]
    assert banner.text() == "无法导入扩展：磁盘已满"
    assert banner._timeout_ms is None  # an error stays until closed
    notice.notify(window, "请先保存正在编辑的配置方案", state="wait")
    assert window._banners.banners()[0]._timeout_ms == 5000
    assert notice_log == []  # nothing modal


def test_notify_inside_a_dialog_or_before_start_up_falls_back_to_a_dialog(qtbot, notice_log):
    notice.notify(None, "无法启动", "原因")
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    notice.notify(dialog, "无法导入扩展", "格式不对")
    assert notice_log == [("无法启动", "原因"), ("无法导入扩展", "格式不对")]


def test_long_banner_text_is_cut_and_kept_in_the_tooltip(qtbot):
    window = _Window()
    qtbot.addWidget(window)
    window.show()
    notice.notify(window, "无法导入配置", "很长的原因" * 40)
    banner = window._banners.banners()[0]
    assert banner.text().endswith("…") and "很长的原因" * 40 in banner.toolTip()
    assert banner.width() < window.width()


def test_alert_is_a_read_only_dialog_shell(qtbot, notice_log, monkeypatch):
    shown = []
    monkeypatch.setattr(notice, "_run", lambda dialog: shown.append(dialog) or 0)
    notice.alert(None, "无法写入设备", "设备没有回应")
    dialog = shown[0]
    assert dialog.property("boringDialog") is True
    assert dialog.close_button is not None and dialog.close_button.isDefault()
    assert [b for b in dialog.findChildren(QPushButton) if b is not dialog.close_button] == []


def test_ask_keeps_standard_buttons_and_danger_puts_cream_cancel_last(qtbot, monkeypatch):
    shown = []

    def press_first(dialog):
        shown.append(dialog)
        buttons = [b for b in dialog.findChildren(QPushButton) if b.objectName().startswith("choice_")]
        buttons[0].click()
        return dialog.result()

    monkeypatch.setattr(notice, "_run", press_first)
    answer = notice.ask(None, "删除配置方案", "删除后不能恢复。",
                        QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel, danger=True)
    labels = [b.text() for b in shown[0].findChildren(QPushButton) if b.objectName().startswith("choice_")]
    assert labels == ["是", "取消"]  # danger: the action first, cancel last and cream
    assert answer == QMessageBox.Yes
    assert shown[0].findChild(QPushButton, f"choice_{int(QMessageBox.Cancel.value)}").isDefault()


def test_ask_returns_the_escape_button_when_closed(qtbot, notice_log):
    answer = notice.ask(None, "未保存的修改", "保存当前修改后再继续？",
                        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
    assert answer == QMessageBox.Cancel


def test_choose_returns_the_picked_key(qtbot, monkeypatch):
    def press(dialog):
        dialog.findChild(QPushButton, "choice_keep").click()
        return dialog.result()

    monkeypatch.setattr(notice, "_run", press)
    assert notice.choose(None, "离开", "还没存", [("cancel", "取消"), ("drop", "放弃"), ("keep", "保留草稿")],
                         default="keep", escape="cancel") == "keep"
