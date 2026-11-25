from PySide6.QtWidgets import QMessageBox, QWidget

from controller_config.views.ui import notice


def confirm_local_draft(parent: QWidget, title: str, text: str) -> QMessageBox.StandardButton:
    """Name the local-only choices without implying a device write."""
    picked = notice.choose(
        parent, title, text,
        [("cancel", "继续编辑"), ("discard", "放弃修改"), ("keep", "保留草稿")],
        default="cancel", escape="cancel", object_name="localDraftChoice",
    )
    return {"keep": QMessageBox.Save, "discard": QMessageBox.Discard}.get(picked, QMessageBox.Cancel)
