from PySide6.QtWidgets import QMessageBox, QWidget

from controller_config.i18n import translate_ui_text


def confirm_local_draft(parent: QWidget, title: str, text: str) -> QMessageBox.StandardButton:
    """Name the local-only choices without implying a device write."""
    dialog = QMessageBox(QMessageBox.Warning, translate_ui_text(title),
                         translate_ui_text(text),
                         QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                         parent)
    dialog.setObjectName("localDraftChoice")
    for button, label in ((QMessageBox.Save, "保留草稿"),
                          (QMessageBox.Discard, "放弃修改"),
                          (QMessageBox.Cancel, "继续编辑")):
        dialog.button(button).setText(translate_ui_text(label))
    dialog.setDefaultButton(QMessageBox.Cancel)
    dialog.setEscapeButton(QMessageBox.Cancel)
    return QMessageBox.StandardButton(dialog.exec())
