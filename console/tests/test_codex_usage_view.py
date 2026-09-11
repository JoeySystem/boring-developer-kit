from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from controller_config.transport.demo import DemoGateway
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow


def test_console_does_not_duplicate_menu_bar_codex_usage(qtbot, contract) -> None:
    view_model = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)

    assert "usage" not in window._nav_buttons
    assert window.findChild(QWidget, "codexUsageSidebarCard") is None
    assert window.findChild(QWidget, "codexUsagePage") is None
    with pytest.raises(ValueError, match="未知页面 usage"):
        view_model.navigate("usage")
