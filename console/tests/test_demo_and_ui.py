from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import re
import uuid
import pytest
from controller_config.models import AppState
from controller_config.official_controls import MATRIX12_AGENT_STATUS_KEYS

from PySide6.QtCore import QObject, QPoint, QRect, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from controller_config.automation import AutomationRunResult, AutomationStore, DeviceEvent
from controller_config.codex_usage import CodexUsageSnapshot
from controller_config.extensions.bindings import ExtensionBindingStore
from controller_config.extensions.manager import ExtensionManager
from controller_config.extensions.platform import ExtensionPlatformController
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.i18n import ENGLISH, SIMPLIFIED_CHINESE, LanguageManager
from controller_config.transport.demo import DemoGateway
from controller_config.prompt_device import PromptListenerState
from controller_config.prompt_library import PromptEntry, PromptLibraryStore
from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
from controller_config.transactions import ConfigTransaction, ConfigTransactionState
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.diagnostics import DiagnosticsPage
from controller_config.views.actions import ActionsPage
from controller_config.views.device_silhouette import create_control_layout
from controller_config.views.extensions import ExtensionsPage
from controller_config.views.main_window import (
    APP_STYLE,
    MainWindow,
    ROOT_MATERIAL_TINT,
    _DottedRoot,
    _NavigationCapsule,
    _TopNavigationButton,
)
from controller_config.views.action_editor import ActionEditor
from controller_config.views.preferences_editor import PreferencesEditor, RgbButton


@pytest.fixture(autouse=True)
def stop_demo_io_before_window_cleanup(qtbot, monkeypatch):
    # A test can finish while the demo's asynchronous icon read is in flight.
    # Stop its view model before pytest-qt closes the window, just as the session
    # fixtures do, so cleanup doesn't open a user-facing busy-operation dialog.
    add_widget = qtbot.addWidget

    def register(widget, *args, **kwargs):
        if isinstance(widget, MainWindow):
            previous_cleanup = kwargs.get("before_close_func")
            def cleanup(window):
                if previous_cleanup is not None:
                    previous_cleanup(window)
                window._view_model.shutdown()
            kwargs["before_close_func"] = cleanup
        return add_widget(widget, *args, **kwargs)

    monkeypatch.setattr(qtbot, "addWidget", register)


class _ImmediateScriptRunner:
    def __init__(self) -> None:
        self.launches = []

    def launch(self, definition, event, completed) -> None:
        self.launches.append((definition, event))
        completed(AutomationRunResult(0, "test complete", ""))

    def shutdown(self) -> None:
        return


EXTENSION_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "extensions"


def _set_demo_mode(gateway, view_model, qtbot, mode: str) -> None:
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    gateway.snapshot_ready.emit(
        replace(snapshot, status={**snapshot.status, "operating_mode": mode})
    )
    qtbot.waitUntil(
        lambda: view_model.model.snapshot.status.get("operating_mode") == mode,
        timeout=1000,
    )


def test_demo_haptic_apply_is_not_blocked_by_unimplemented_icon_read(qtbot, contract):
    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    vm.navigate("lighting")
    qtbot.wait(50)  # Let the page's deferred icon refresh run.
    assert not vm.screen_icon.busy
    assert not vm.screen_glyphs.busy
    qtbot.waitUntil(lambda: window.findChild(PreferencesEditor) is not None, timeout=2000)
    editor = window.findChild(PreferencesEditor)
    enabled = editor._haptic_enabled.isChecked()
    editor._haptic_enabled.setChecked(not enabled)
    window.findChild(QPushButton, "savePreferencesToDevice").click()
    qtbot.waitUntil(
        lambda: vm.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=5000,
    )
    assert window.findChild(QPushButton, "confirmConfigurationWrite") is None
    assert vm.model.snapshot.config_result["config"]["haptic"]["enabled"] is not enabled
    assert not window.findChild(QPushButton, "savePreferencesToDevice").isEnabled()
    vm.shutdown()


def test_firmware_status_refresh_preserves_scroll_position(qtbot, contract):
    gateway = DemoGateway(contract, "ready")
    vm = MainViewModel(gateway, contract)
    window = MainWindow(vm)
    qtbot.addWidget(window)
    window._fit_window_to_available_area(QRect(0, 0, 1280, 640))
    window.show()
    vm.start()
    qtbot.waitUntil(lambda: vm.model.snapshot is not None)
    vm._set_firmware_update(
        replace(
            vm.firmware_update,
            state=FirmwareUpdateState.CHECKING,
            message="正在读取设备状态",
        )
    )
    vm.navigate("firmware")
    scroll = window.findChild(QScrollArea, "firmwareMaintenanceScroll")
    window.findChild(QPushButton, "firmwarePackageToggle").click()
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    content = scroll.widget()
    target = min(200, scroll.verticalScrollBar().maximum())
    scroll.verticalScrollBar().setValue(target)
    vm.changed.emit(vm.model)
    vm.changed.emit(vm.model)
    vm.changed.emit(vm.model)
    qtbot.wait(30)
    refreshed = window.findChild(QScrollArea, "firmwareMaintenanceScroll")
    assert refreshed is scroll
    assert refreshed.widget() is content
    assert refreshed.verticalScrollBar().value() == target
    assert refreshed.widget().y() == -target
    poll = QTimer(window)
    poll.setInterval(500)
    poll.timeout.connect(lambda: vm.changed.emit(vm.model))
    poll.start()
    qtbot.wait(1200)  # Reproduce the real gateway's periodic status refresh.
    poll.stop()
    assert window.findChild(QScrollArea, "firmwareMaintenanceScroll").verticalScrollBar().value() == target
    assert window.findChild(QScrollArea, "firmwareMaintenanceScroll").widget().y() == -target
    vm._set_firmware_update(replace(vm.firmware_update, message="验收状态已更新"))
    qtbot.wait(50)
    assert window.findChild(QScrollArea, "firmwareMaintenanceScroll").widget() is content
    assert any(label.text() == "验收状态已更新" for label in window.findChildren(QLabel))
    assert scroll.isVisible()
    assert scroll.widget().isVisible()
    assert scroll.verticalScrollBar().value() == target
    assert scroll.widget().y() == -target
    vm._set_firmware_update(
        replace(vm.firmware_update, state=FirmwareUpdateState.IDLE, message="")
    )
    vm.shutdown()


def test_active_top_navigation_button_omits_redundant_focus_frame(qtbot) -> None:
    class FocusedNavigationButton(_TopNavigationButton):
        def initStyleOption(self, option) -> None:  # noqa: N802 - Qt virtual method
            super().initStyleOption(option)
            option.state |= QStyle.StateFlag.State_HasFocus

    button = FocusedNavigationButton("", objectName="navIconButton")
    qtbot.addWidget(button)

    button.setProperty("active", False)
    assert button._style_option().state & QStyle.StateFlag.State_HasFocus

    button.setProperty("active", True)
    assert not button._style_option().state & QStyle.StateFlag.State_HasFocus


def test_message_box_palette_keeps_confirmation_text_readable(qtbot) -> None:
    parent = QWidget()
    parent.setStyleSheet(APP_STYLE)
    qtbot.addWidget(parent)
    dialog = QMessageBox(
        QMessageBox.Icon.Warning,
        "确认写入提示词",
        "将覆盖设备的上方向快捷提示词，随后立即读回全文确认。是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        parent,
    )
    qtbot.addWidget(dialog)
    dialog.ensurePolished()

    palette = dialog.palette()
    assert palette.color(QPalette.ColorRole.Window).lightness() < 100
    assert palette.color(QPalette.ColorRole.WindowText).lightness() > 200
    yes_button = dialog.button(QMessageBox.StandardButton.Yes)
    yes_button.ensurePolished()
    assert yes_button.palette().color(QPalette.ColorRole.Button).lightness() < 100
    assert yes_button.palette().color(QPalette.ColorRole.ButtonText).lightness() > 200


def test_profile_name_dialog_palette_keeps_text_readable(qtbot) -> None:
    parent = QWidget()
    parent.setStyleSheet(APP_STYLE)
    qtbot.addWidget(parent)
    dialog = QInputDialog(parent)
    dialog.setWindowTitle("另存为新方案")
    dialog.setLabelText("新配置方案名称")
    dialog.setTextValue("Codex macOS 副本")
    qtbot.addWidget(dialog)
    dialog.ensurePolished()

    palette = dialog.palette()
    assert palette.color(QPalette.ColorRole.Window).lightness() < 100
    assert palette.color(QPalette.ColorRole.WindowText).lightness() > 200
    assert palette.color(QPalette.ColorRole.Base).lightness() < 100
    assert palette.color(QPalette.ColorRole.Text).lightness() > 200


def test_power_v2_demo_uses_authoritative_assets(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]
    assert snapshot.identity["hardware_id"] == "WMP-S3-MATRIX12-POWER-V2"
    assert snapshot.versions["firmware"] == "0.0.0-demo"
    assert len(snapshot.controls) == 20
    assert snapshot.config_is_synced


def test_view_model_keeps_bound_local_draft_for_same_device_reconnect(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]
    gateway.snapshot_ready.emit(snapshot)

    view_model.rename_profile(snapshot.active_profile_id, "My draft")
    draft = view_model.draft
    assert draft is not None and draft.is_dirty

    gateway.disconnected.emit("gone")
    gateway.snapshot_ready.emit(snapshot)

    assert view_model.draft is draft
    assert view_model.draft.profile(snapshot.active_profile_id)["name"] == "My draft"


def test_dirty_draft_is_not_replaced_when_device_generation_changes(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]
    gateway.snapshot_ready.emit(snapshot)
    view_model.rename_profile(snapshot.active_profile_id, "Keep this draft")
    draft = view_model.draft

    newer_snapshot = replace(
        snapshot,
        config_result={**snapshot.config_result, "generation": snapshot.config_result["generation"] + 1},
    )
    gateway.snapshot_ready.emit(newer_snapshot)

    assert view_model.draft is draft
    assert view_model.draft is not None and view_model.draft.is_dirty


def test_power_v2_control_layout_matches_physical_prototype(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]

    host = QWidget()
    qtbot.addWidget(host)
    host.setLayout(create_control_layout(snapshot))

    controls = {
        widget.property("controlId"): (
            widget.property("gridRow"),
            widget.property("gridColumn"),
            widget.property("gridColumnSpan"),
            widget.width(),
            widget.height(),
        )
        for widget in host.findChildren(QWidget)
        if widget.property("controlId")
    }

    assert controls == {
        "display": (0, 0, 1, 64, 64),
        "key.1": (0, 1, 1, 64, 64),
        "key.2": (0, 2, 1, 64, 64),
        "key.3": (0, 3, 1, 64, 64),
        "key.4": (1, 0, 1, 64, 64),
        "key.5": (1, 1, 1, 64, 64),
        "key.6": (1, 2, 1, 64, 64),
        "key.7": (1, 3, 1, 64, 64),
        "key.8": (2, 0, 2, 131, 64),
        "key.9": (2, 2, 1, 64, 64),
        "key.10": (2, 3, 1, 64, 64),
        "encoder": (3, 0, 1, 128, 128),
        "key.11": (3, 1, 1, 64, 64),
        "key.12": (3, 2, 1, 64, 64),
        "joystick": (3, 3, 1, 64, 64),
    }

    visible_labels = [label.text() for label in host.findChildren(QLabel)]
    assert host.findChild(QPushButton, "encoderControl").accessibleName() == "设置旋钮"
    assert "编码器" not in visible_labels
    roles = {
        widget.property("controlId"): widget.property("role")
        for widget in host.findChildren(QPushButton)
        if widget.property("controlId",).startswith("key.")
    }
    assert {control_id for control_id, role in roles.items() if role == "agent"} == {
        "key.1",
        "key.2",
        "key.4",
        "key.5",
        "key.6",
        "key.7",
    }
    assert {control_id for control_id, role in roles.items() if role == "human"} == {
        "key.3",
        "key.8",
        "key.9",
        "key.10",
        "key.11",
        "key.12",
    }


def test_encoder_tile_names_rotation_direction_before_the_mapped_action(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]
    mappings = dict(snapshot.mappings)
    mappings["encoder.ccw"] = {
        "control_id": "encoder.ccw",
        "short_name": "Scroll up",
        "action": {
            "type": "mouse",
            "button": 0,
            "x": 0,
            "y": 0,
            "wheel": 1,
            "pan": 0,
        },
    }
    mappings["encoder.cw"] = {
        "control_id": "encoder.cw",
        "short_name": "Scroll down",
        "action": {
            "type": "mouse",
            "button": 0,
            "x": 0,
            "y": 0,
            "wheel": -1,
            "pan": 0,
        },
    }

    host = QWidget()
    qtbot.addWidget(host)
    host.setLayout(create_control_layout(snapshot, mappings=mappings))
    encoder = next(
        button
        for button in host.findChildren(QPushButton)
        if button.property("controlId") == "encoder"
    )
    # The reference rotary surface has no text; its full directional mapping
    # remains discoverable in the tooltip.
    assert "逆时针 · 滚轮↑" in encoder.toolTip()
    assert "顺时针 · 滚轮↓" in encoder.toolTip()
    assert "转 ·" not in encoder.toolTip()


def test_default_window_shows_complete_overview_without_vertical_scrolling(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.resize(1240, 780)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: window.findChild(QScrollArea, "overviewScroll") is not None,
        timeout=1000,
    )

    qtbot.waitUntil(
        lambda: (
            (current := window.findChild(QScrollArea, "overviewScroll")) is not None
            and current.verticalScrollBar().maximum() == 0
        ),
        timeout=1000,
    )
    overview = window.findChild(QScrollArea, "overviewScroll")
    assert overview is not None
    compact_status = next(
        label
        for label in window.findChildren(QLabel)
        if label.property("compactStatus") is True
    )
    assert compact_status.text() == "开发设备 · 未认证 [ DEV ]"
    assert compact_status.objectName() == "statusWarn"
    assert window._device_name_summary.text() == "BORING MIST"
    assert window._device_auth_summary.text() == "开发设备 · 未认证 [ DEV ]"
    assert window._device_auth_summary.property("trust") == "development"


def test_authenticated_device_has_distinct_connection_label(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = replace(
        blocker.args[0],
        trust=DeviceTrust(
            DeviceTrustState.AUTHENTICATED,
            "BORING 设备已认证",
            "issuer=BORING-PROD-ROOT-1",
            issuer_key_id="BORING-PROD-ROOT-1",
            serial="CP01-AABBCCDDEEFF",
        ),
    )
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()

    gateway.snapshot_ready.emit(snapshot)
    qtbot.waitUntil(
        lambda: any(
            label.property("compactStatus") is True
            and label.text() == "已认证 [ VERIFIED ]"
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )

    compact_status = next(
        label
        for label in window.findChildren(QLabel)
        if label.property("compactStatus") is True
    )
    assert compact_status.objectName() == "statusReady"
    assert window._device_auth_summary.text() == "已认证 [ VERIFIED ]"
    assert window._device_auth_summary.property("trust") == "authenticated"


def test_shared_status_labels_use_text_without_colored_blocks() -> None:
    for selector in (
        "statusReady",
        "statusWarn",
        "statusError",
        "promptHelperOnlineState",
    ):
        rules = re.findall(
            rf"QLabel#{selector}[^{{}}]*\{{([^{{}}]*)\}}",
            APP_STYLE,
            flags=re.DOTALL,
        )
        assert rules, selector
        for rule in rules:
            backgrounds = re.findall(r"background\s*:\s*([^;]+)", rule)
            assert all(value.strip() == "transparent" for value in backgrounds)
            borders = re.findall(r"border\s*:\s*([^;]+)", rule)
            assert all(value.strip() == "none" for value in borders)


def test_window_uses_translucent_desktop_material(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.resize(1240, 805)
    window.show()

    root = window.findChild(QWidget, "root")
    chrome = window.findChild(QWidget, "windowChrome")
    console = window.findChild(QWidget, "consoleFrame")
    assert root is not None
    assert chrome is not None
    assert console is not None
    assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert root.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert root.layout().contentsMargins().isNull()
    assert console.graphicsEffect() is None
    assert {
        button.property("windowRole")
        for button in chrome.findChildren(QPushButton, "windowControl")
    } == {"close", "minimize", "zoom"}
    for button in chrome.findChildren(QPushButton, "windowControl"):
        assert button.visibleRegion().boundingRect() == button.rect()
    assert "QMainWindow { background: transparent; }" in APP_STYLE
    assert "QWidget#root { background: transparent;" in APP_STYLE
    assert 'font-family: ".AppleSystemUIFont"' in APP_STYLE
    assert "background: rgba(28, 28, 30, 160)" in APP_STYLE
    assert "background: rgba(20, 20, 20, 40)" in APP_STYLE
    assert "rgba(12, 40, 57, 190)" not in APP_STYLE
    console_rules = re.findall(
        r"QFrame#consoleFrame\s*\{([^{}]*)\}", APP_STYLE, flags=re.DOTALL
    )
    assert console_rules
    assert all("border: none" in rule for rule in console_rules)


def test_dotted_root_does_not_paint_a_window_frame(qtbot) -> None:
    root = _DottedRoot()
    qtbot.addWidget(root)
    root.resize(200, 120)
    image = QImage(root.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        root.render(painter, QPoint())
    finally:
        painter.end()

    assert image.pixelColor(8, 60) == image.pixelColor(18, 60)
    assert ROOT_MATERIAL_TINT == QColor(28, 27, 25, 102)


def test_window_keeps_top_navigation_and_workspace_at_compact_width(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    window._fit_window_to_available_area(QRect(0, 0, 800, 620))
    qtbot.waitUntil(lambda: window._compact_mode, timeout=1000)
    assert window._content.isVisible()
    assert window._top_navigation.isVisible()
    assert not hasattr(window, "_sidebar")
    assert not window._shell_edition.isVisible()
    assert not window._device_name_summary.isVisible()
    assert all(button.isVisible() for button in window._nav_buttons.values())

    window._fit_window_to_available_area(QRect(0, 0, 1600, 1000))
    window.resize(1240, 780)
    qtbot.waitUntil(lambda: not window._compact_mode, timeout=1000)
    assert window._content.isVisible()
    assert window._shell_edition.isVisible()
    assert window._device_name_summary.isVisible()


def test_top_navigation_keeps_labels_and_routes_primary_pages(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)

    assert window.findChild(QWidget, "sidebar") is None
    expected = {
        "overview": "按键配置",
        "prompts": "快捷提示词",
        "lighting": "外观与反馈",
        "actions": "自动化",
        "settings": "固件与系统",
    }
    assert list(window._nav_buttons) == list(expected)
    navigation = window.findChild(QWidget, "navigationCapsule").layout()
    assert [
        navigation.itemAt(index).widget()
        for index in range(navigation.count())
        if isinstance(navigation.itemAt(index).widget(), QPushButton)
    ] == list(window._nav_buttons.values())

    def icon_lightness(button: QPushButton) -> float:
        image = button.icon().pixmap(20, 20).toImage().convertToFormat(
            QImage.Format.Format_ARGB32
        )
        values = [
            image.pixelColor(x, y).lightnessF()
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 32
        ]
        return sum(values) / len(values)

    assert (
        icon_lightness(window._nav_buttons["overview"])
        > icon_lightness(window._nav_buttons["prompts"]) + 0.2
    )
    for page, label in expected.items():
        button = window._nav_buttons[page]
        assert button.toolTip() == ("外观与反馈 · 灯光、震动、屏幕" if page == "lighting" else label)
        assert button.accessibleName() == label
        assert button.accessibleDescription() == (
            "固件与系统" if page == "settings" else "切换界面"
        )
        assert not button.icon().isNull()
        assert button.isEnabled()
        qtbot.mouseClick(button, Qt.LeftButton)
        assert view_model.page == page
        assert button.property("active") is True
        assert button.text() == label
        assert all(
            other.text() == other.accessibleName()
            for other_page, other in window._nav_buttons.items()
            if other_page != page
        )
        assert all(
            icon_lightness(button) > icon_lightness(other) + 0.2
            for other_page, other in window._nav_buttons.items()
            if other_page != page
        )


def test_navigation_capsule_paints_fully_rounded_ends(qtbot) -> None:
    capsule = _NavigationCapsule(objectName="navigationCapsule")
    qtbot.addWidget(capsule)
    capsule.resize(300, 52)
    image = QImage(capsule.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        capsule.render(painter, QPoint())
    finally:
        painter.end()

    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(1, capsule.height() // 2).alpha() > 0
    assert image.pixelColor(capsule.width() - 2, capsule.height() // 2).alpha() > 0
    # v4 explicitly restores the subtle material highlight, not an opaque frame.
    top = image.pixelColor(capsule.width() // 2, 0)
    center = image.pixelColor(capsule.width() // 2, capsule.height() // 2)
    assert top.lightness() > center.lightness()
    assert top.alpha() < center.alpha()


def test_active_navigation_segment_has_no_light_outline(qtbot) -> None:
    button = _TopNavigationButton("", objectName="navIconButton")
    qtbot.addWidget(button)
    button.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    button.set_active(True)
    button.resize(138, 44)
    image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    try:
        button.render(painter, QPoint())
    finally:
        painter.end()

    assert image.pixelColor(0, 0).alpha() == 0
    # Allow rounding at the antialiased, partially transparent edge.
    assert abs(
        image.pixelColor(button.width() // 2, 0).lightness()
        - image.pixelColor(button.width() // 2, button.height() // 2).lightness()
    ) <= 3


def test_active_navigation_segment_is_darker_than_inactive_segment(qtbot) -> None:
    def rendered_edge(button: _TopNavigationButton) -> QColor:
        button.resize(138, 44)
        button.ensurePolished()
        image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(32, 32, 32))
        painter = QPainter(image)
        try:
            button.render(painter, QPoint())
        finally:
            painter.end()
        return image.pixelColor(button.width() - 10, button.height() // 2)

    active = _TopNavigationButton("", objectName="navIconButton")
    inactive = _TopNavigationButton("", objectName="navIconButton")
    qtbot.addWidget(active)
    qtbot.addWidget(inactive)
    active.setAccessibleName("按键配置")
    inactive.setAccessibleName("快捷提示词")
    active.set_active(True)
    inactive.set_active(False)

    assert rendered_edge(active).lightness() < rendered_edge(inactive).lightness()


def test_device_shell_has_no_redundant_light_outer_stroke(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.resize(1240, 780)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.state is AppState.READY)
    qtbot.waitUntil(lambda: window.findChild(QFrame, "deviceShell") is not None
                   and window.findChild(QFrame, "deviceShell").isVisible())
    shell = window.findChild(QFrame, "deviceShell")
    assert shell is not None

    image = QImage(shell.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(48, 48, 48))
    painter = QPainter(image)
    try:
        shell.render(painter, QPoint())
    finally:
        painter.end()

    edge = image.pixelColor(0, shell.height() // 2).lightness()
    interior = image.pixelColor(3, shell.height() // 2).lightness()
    # The Blender model shell is deliberately transparent so its line work can
    # sit directly on the page material without a second rectangular outline.
    assert edge <= 48
    assert abs(edge - interior) < 8


def test_mapping_workspace_cards_use_subtle_v4_material_edges(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.resize(1240, 780)
    window.show()
    view_model.start()
    object_names = (
        "deviceContextCard",
        "codexHomeCard",
        "selectionInspectorCard",
        "syncSummaryCard",
    )
    qtbot.waitUntil(
        lambda: all(window.findChild(QFrame, name) is not None for name in object_names)
    )

    for object_name in object_names:
        card = window.findChild(QFrame, object_name)
        assert card is not None
        image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(48, 48, 48))
        painter = QPainter(image)
        try:
            card.render(painter, QPoint())
        finally:
            painter.end()
        edge = image.pixelColor(0, card.height() // 2).lightness()
        interior = image.pixelColor(3, card.height() // 2).lightness()
        assert edge < 60, object_name
        assert abs(edge - interior) < 30, object_name


def test_option_pages_keep_v4_material_edges_subtle(qtbot, contract) -> None:
    view_model = MainViewModel(DemoGateway(contract, "ready"), contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.resize(1240, 780)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None)

    def visible_panels():
        return [
            widget for widget in window._content.findChildren(QWidget)
            if widget.isVisible() and (
                isinstance(widget, QGroupBox)
                or widget.property("cardRole") in {"primary", "secondary", "focus", "widget"}
                or widget.objectName() in {"settingsRow", "card", "actionTabs"}
            )
        ]

    for page in (
        "settings", "prompts", "actions", "lighting", "diagnostics", "joystick", "firmware"
    ):
        view_model.navigate(page)
        qtbot.waitUntil(lambda: bool(visible_panels()))
        for panel in visible_panels():
            image = QImage(panel.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(48, 48, 48))
            painter = QPainter(image)
            try:
                panel.render(
                    painter, QPoint(),
                    renderFlags=QWidget.RenderFlag.DrawWindowBackground,
                )
            finally:
                painter.end()
            edge = image.pixelColor(0, panel.height() // 2).lightness()
            interior = image.pixelColor(3, panel.height() // 2).lightness()
            # The single focus card deliberately adds signal-coloured falloff.
            limit = 64 if panel.property("cardRole") == "focus" else 30
            assert abs(edge - interior) < limit, (page, panel.objectName())


def test_overview_disclosure_exposes_three_ble_host_slots(qtbot, contract, monkeypatch) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: window.findChild(QPushButton, "bleSlotsDisclosure") is not None)
    dialogs = []
    def capture_dialog(dialog):
        dialogs.append(dialog)
        dialog.show()
        return 0
    monkeypatch.setattr(QDialog, "exec", capture_dialog)
    qtbot.mouseClick(window.findChild(QPushButton, "bleSlotsDisclosure"), Qt.LeftButton)
    assert len(dialogs) == 1
    dialog = dialogs[0]
    qtbot.addWidget(dialog)
    dialog_text = [label.text() for label in dialog.findChildren(QLabel)]
    assert "蓝牙连接" in dialog_text
    assert "电脑 1" in dialog_text
    assert "已选用 · 尚未连接" in dialog_text
    assert dialog.findChild(QPushButton, "primary").text() == "添加电脑"
    assert dialog.findChild(QPushButton, "bleConnectionHelp").text() == "如何用设备切换"
    assert not any("KEY 3" in text for text in dialog_text)
    dialog.reject()


def test_codex_overview_uses_reference_deck_and_product_palette(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: any(
            label.text() == "CODEX" and label.isVisible()
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )

    visible_text = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
    digital_text = {
        label.text()
        for label in window.findChildren(Boring5RLabel)
        if label.isVisible()
    }
    assert "BORING MIST" in visible_text
    assert "CODEX" in visible_text
    assert "Key Mapping" not in visible_text  # v4 removes the redundant page heading.
    assert {
        "BORING",
        "CONSOLE",
        "CODEX",
    } <= digital_text
    assert "CODEX MACOS" not in digital_text
    visible_digital_labels = {
        label.text(): label
        for label in window.findChildren(Boring5RLabel)
        if label.isVisible()
    }
    device_name = window.findChild(QLabel, "deviceNameSummary")
    assert device_name is not None
    assert device_name.text() == "BORING MIST"
    assert not isinstance(device_name, Boring5RLabel)
    assert min(label.scale for label in visible_digital_labels.values()
               if label.objectName() != "deviceScreenBrand") >= 0.75
    assert window.findChild(QWidget, "consoleFrame") is not None
    assert window.findChild(QWidget, "deviceWorkspace") is not None
    assert window.findChild(QWidget, "deviceContextCard") is not None
    assert window.findChild(QWidget, "selectionInspectorCard") is not None
    assert window.findChild(QWidget, "syncSummaryCard") is not None
    assert window.findChild(QWidget, "mappingEditorCard") is None
    assert any(
        button.objectName() == "profilePill"
        for button in window.findChildren(QPushButton)
    )
    assert "#c9ff43" not in APP_STYLE.lower()
    assert "#e85b42" in APP_STYLE.lower()
    assert "#dfeaf5" in APP_STYLE.lower()
    assert "#ff6a00" in APP_STYLE.lower()


def test_activation_failure_takes_priority_over_pending(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    with qtbot.waitSignal(gateway.snapshot_ready, timeout=1000) as blocker:
        gateway.connect_port("demo://power-v2")
    snapshot = blocker.args[0]
    failed = replace(
        snapshot,
        status={**snapshot.status, "pending": {"generation": 2}, "activation_failed": True},
    )

    assert failed.config_status_label == "激活失败 · 需要恢复"


def test_window_replaces_scanning_content_when_no_device(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "no-device")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: any(
            "尚未连接 BORING 设备" in label.text() and label.isVisible()
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    visible_text = [label.text() for label in window.findChildren(QLabel) if label.isVisible()]
    assert any("尚未连接 BORING 设备" in text for text in visible_text)
    assert "正在查找已连接到此电脑的 BORING 设备…" not in visible_text


def test_window_renders_incompatible_action_without_clipping(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "incompatible")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: any(
            "这台设备与当前 BORING 控制台不兼容" in label.text() and label.isVisible()
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )
    title = next(
        label
        for label in window.findChildren(QLabel)
        if "这台设备与当前 BORING 控制台不兼容" in label.text() and label.isVisible()
    )
    assert title.height() >= title.sizeHint().height()


def test_window_renders_device_authenticity_failure_as_a_distinct_state(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "authenticity-failed")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()

    qtbot.waitUntil(
        lambda: any(
            "无法验证 BORING 设备身份" in label.text() and label.isVisible()
            for label in window.findChildren(QLabel)
        ),
        timeout=1000,
    )

    visible_text = [
        label.text() for label in window.findChildren(QLabel) if label.isVisible()
    ]
    assert view_model.model.state is AppState.AUTHENTICITY_FAILED
    assert not window.findChild(QPlainTextEdit, "connectionDetails").isVisible()
    window.findChild(QPushButton, "connectionDetailsToggle").click()
    assert "certificate_signature" in window.findChild(QPlainTextEdit, "connectionDetails").toPlainText()


def test_window_keeps_last_snapshot_visible_after_disconnect(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    serial = view_model.model.snapshot.identity["serial"]

    gateway.disconnected.emit("demo port gone")

    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    assert view_model.model.snapshot.identity["serial"] == serial
    assert view_model.draft is not None
    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and "Default" in profile_pill.text()


def test_overview_control_opens_editor_and_creates_dirty_local_draft(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    key_button = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.8"
    )
    assert key_button.isEnabled()
    assert key_button.accessibleName() == "设置功能键 8"
    qtbot.mouseClick(key_button, Qt.LeftButton)

    qtbot.waitUntil(
        lambda: (
            window.findChild(QScrollArea, "overviewScroll") is not None
            and window.findChild(QScrollArea, "overviewScroll")
            .verticalScrollBar()
            .maximum()
            == 0
        ),
        timeout=1000,
    )
    overview = window.findChild(QScrollArea, "overviewScroll")
    assert overview is not None

    labels = [label.text() for label in window.findChildren(QLabel)]
    assert "设备当前值" in labels
    assert "快捷键名称（可选）" in labels
    assert "屏幕短名" not in labels
    actual_action = window.findChild(QLabel, "actualActionValue")
    assert actual_action.text()
    assert actual_action.wordWrap()
    sync = window.findChild(QWidget, "syncSummaryCard")
    assert sync is not None
    assert not any(button.text() == "技术详情" for button in sync.findChildren(QPushButton))

    save_button = window.findChild(QPushButton, "saveMappingDraft")
    assert save_button is not None and save_button.text() == "保存草稿"
    action_type = window.findChild(QComboBox, "actionTypeEditor")
    assert action_type is not None
    action_type.setCurrentIndex(action_type.findData("none"))
    qtbot.mouseClick(save_button, Qt.LeftButton)

    assert view_model.draft is not None and view_model.draft.is_dirty
    assert view_model.draft.mapping(0, "key.8")["action"] == {"type": "none"}
    qtbot.waitUntil(
        lambda: any(
            label.text() == "未保存变更"
            for label in window.findChildren(QLabel)
            if label.isVisible()
        ),
        timeout=1000,
    )
    view_model.discard_draft()


def test_mapping_inspector_stays_beside_device_and_switches_controls(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    qtbot.waitUntil(
        lambda: view_model.prompt_device.listener_status.online,
        timeout=1000,
    )
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    qtbot.waitUntil(
        lambda: any(
            workspace.isVisible()
            for workspace in window.findChildren(QWidget, "mappingWorkspace")
        ),
        timeout=1000,
    )

    workspace_before = next(
        widget
        for widget in window.findChildren(QWidget, "mappingWorkspace")
        if widget.isVisible()
    )
    device_before = next(
        widget
        for widget in window.findChildren(QWidget, "deviceWorkspace")
        if widget.isVisible()
    )
    header_before = next(
        widget
        for widget in window.findChildren(QWidget, "topNavigation")
        if widget.isVisible()
    )
    window._select_physical_control("key.9")

    qtbot.waitUntil(
        lambda: any(
            workspace.isVisible() and workspace.property("stacked") is False
            for workspace in window.findChildren(QWidget, "mappingWorkspace")
        )
        and any(
            inspector.isVisible()
            for inspector in window.findChildren(QWidget, "mappingEditorCard")
        ),
        timeout=1000,
    )
    workspace = next(
        widget
        for widget in window.findChildren(QWidget, "mappingWorkspace")
        if widget.isVisible()
    )
    device = next(
        widget
        for widget in window.findChildren(QWidget, "deviceWorkspace")
        if widget.isVisible()
    )
    inspector = next(
        widget
        for widget in window.findChildren(QWidget, "mappingEditorCard")
        if widget.isVisible()
    )
    inspector_rail = next(
        widget
        for widget in window.findChildren(QWidget, "mappingInspectorRail")
        if widget.isVisible()
    )
    body = next(
        widget
        for widget in window.findChildren(QScrollArea, "mappingEditorBody")
        if widget.isVisible()
    )
    assert workspace is workspace_before
    assert device is device_before
    assert next(
        widget
        for widget in window.findChildren(QWidget, "topNavigation")
        if widget.isVisible()
    ) is header_before
    assert workspace.property("stacked") is False
    assert device is not None and device.parentWidget() is workspace
    assert inspector_rail.parentWidget() is workspace
    assert inspector is not None and inspector.parentWidget() is inspector_rail
    assert device.geometry().right() <= inspector_rail.geometry().left()
    assert 300 <= inspector_rail.width() <= 480
    assert body is not None and body.parentWidget() is inspector
    assert window.findChild(QScrollArea, "mappingEditorOverlay") is None
    save = window.findChild(QPushButton, "saveMappingDraft")
    assert save is not None and save.parentWidget() is inspector

    warnings = []
    monkeypatch.setattr(
        "controller_config.views.main_window.confirm_local_draft",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Discard,
    )
    key_2 = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.2" and button.isVisible()
    )
    qtbot.mouseClick(key_2, Qt.LeftButton)

    qtbot.waitUntil(
        lambda: any(
            inspector.isVisible() and inspector.property("controlId") == "key.2"
            for inspector in window.findChildren(QWidget, "mappingEditorCard")
        ),
        timeout=1000,
    )
    inspector = next(
        widget
        for widget in window.findChildren(QWidget, "mappingEditorCard")
        if widget.isVisible()
    )
    assert inspector.property("controlId") == "key.2"
    selected_key_2 = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.2"
    )
    assert selected_key_2.property("selected") is True
    assert warnings == []
    assert next(
        widget
        for widget in window.findChildren(QWidget, "mappingWorkspace")
        if widget.isVisible()
    ) is workspace_before
    assert next(
        widget
        for widget in window.findChildren(QWidget, "deviceWorkspace")
        if widget.isVisible()
    ) is device_before

    window._close_control_editor()
    qtbot.waitUntil(
        lambda: not any(
            inspector.isVisible()
            for inspector in window.findChildren(QWidget, "mappingEditorCard")
        ),
        timeout=1000,
    )
    assert next(
        widget
        for widget in window.findChildren(QWidget, "mappingWorkspace")
        if widget.isVisible()
    ) is workspace_before
    assert next(
        widget
        for widget in window.findChildren(QWidget, "deviceWorkspace")
        if widget.isVisible()
    ) is device_before


def test_mapping_inspector_stacks_below_device_in_narrow_workspace(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    window._fit_window_to_available_area(QRect(0, 0, 800, 805))
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    qtbot.waitUntil(
        lambda: view_model.prompt_device.listener_status.online,
        timeout=1000,
    )
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    qtbot.waitUntil(
        lambda: any(
            workspace.isVisible()
            for workspace in window.findChildren(QWidget, "mappingWorkspace")
        ),
        timeout=1000,
    )

    key_1 = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.1" and button.isVisible()
    )
    qtbot.mouseClick(key_1, Qt.LeftButton)

    qtbot.waitUntil(
        lambda: any(
            workspace.isVisible() and workspace.property("stacked") is True
            for workspace in window.findChildren(QWidget, "mappingWorkspace")
        )
        and any(
            inspector.isVisible()
            for inspector in window.findChildren(QWidget, "mappingEditorCard")
        ),
        timeout=1000,
    )
    workspace = next(
        widget
        for widget in window.findChildren(QWidget, "mappingWorkspace")
        if widget.isVisible()
    )
    device = next(
        widget
        for widget in window.findChildren(QWidget, "deviceWorkspace")
        if widget.isVisible()
    )
    inspector = next(
        widget
        for widget in window.findChildren(QWidget, "mappingEditorCard")
        if widget.isVisible()
    )
    inspector_rail = next(
        widget
        for widget in window.findChildren(QWidget, "mappingInspectorRail")
        if widget.isVisible()
    )
    assert workspace.property("stacked") is True
    assert device.geometry().bottom() <= inspector_rail.geometry().top()
    assert inspector.parentWidget() is inspector_rail
    # A stacked editor uses the available row instead of the desktop column cap.
    assert inspector_rail.width() <= workspace.width()
    assert inspector_rail.geometry().right() <= workspace.rect().right()


def test_mapping_inspector_warns_before_switching_away_from_unsaved_input(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    window._select_physical_control("key.8")
    qtbot.waitUntil(
        lambda: any(
            card.isVisible() and card.property("controlId") == "key.8"
            for card in window.findChildren(QWidget, "mappingEditorCard")
        ),
        timeout=1000,
    )
    inspector = next(
        card
        for card in window.findChildren(QWidget, "mappingEditorCard")
        if card.isVisible() and card.property("controlId") == "key.8"
    )
    short_name = inspector.findChild(QLineEdit, "mappingShortNameEditor")
    assert short_name is not None
    short_name.setText("尚未保存的按键名称")
    warnings = []
    monkeypatch.setattr(
        "controller_config.views.main_window.confirm_local_draft",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Cancel,
    )

    window._select_physical_control("key.9")

    inspector = next(
        widget
        for widget in window.findChildren(QWidget, "mappingEditorCard")
        if widget.isVisible()
    )
    assert warnings
    assert inspector.property("controlId") == "key.8"
    assert short_name.text() == "尚未保存的按键名称"

    window._close_control_editor()

    assert len(warnings) == 2
    assert inspector.isVisible()
    assert inspector.property("controlId") == "key.8"


def test_overview_records_shortcut_and_applies_verified_device_write(
    qtbot, contract, request
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    def cleanup(_window) -> None:
        if view_model.write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION:
            view_model.cancel_device_write_confirmation()
        if view_model.draft is not None and view_model.draft.is_dirty:
            view_model.discard_draft()
        window._selected_control_id = None
        view_model.shutdown()
    qtbot.addWidget(window, before_close_func=cleanup)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    window._select_physical_control("key.8")
    record = window.findChild(QPushButton, "shortcutRecordButton")
    preview = window.findChild(QLabel, "shortcutRecorderPreview")
    apply_to_device = window.findChild(QPushButton, "applyMappingToDevice")
    assert record is not None and preview is not None
    assert apply_to_device is not None and not apply_to_device.isEnabled()

    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    qtbot.keyClick(
        record,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier,
    )
    assert preview.text() == "⇧⌘V"
    action_editor = window.findChild(ActionEditor)
    assert action_editor is not None
    assert action_editor.action() == {
        "type": "key",
        "usage": 25,
        "modifiers": [225, 227],
    }
    assert apply_to_device.isEnabled()

    qtbot.mouseClick(apply_to_device, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state
        is ConfigTransactionState.ACTIVE,
        timeout=5000,
    )
    assert view_model.draft is not None
    assert view_model.draft.mapping(0, "key.8")["action"] == {
        "type": "key",
        "usage": 25,
        "modifiers": [225, 227],
    }
    device_current = window.findChild(QLabel, "actualActionValue")
    assert device_current is not None and device_current.text() == "Shift + Command + V"
    assert window.findChild(QPushButton, "confirmConfigurationWrite") is None


def test_live_diagnostics_do_not_rebuild_an_open_control_editor(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    key_button = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.8"
    )
    qtbot.mouseClick(key_button, Qt.LeftButton)
    action_type = window.findChild(QComboBox, "actionTypeEditor")
    assert action_type is not None
    snapshot = view_model.model.snapshot
    assert snapshot is not None

    gateway.status_updated.emit(
        {
            **snapshot.status,
            "joystick_diagnostics": {
                "raw_x": 2061,
                "raw_y": 2043,
                "filtered_x": 2058,
                "filtered_y": 2046,
            },
        }
    )

    assert view_model.model.snapshot is not None
    assert view_model.model.snapshot.status["joystick_diagnostics"]["raw_x"] == 2061
    assert window.findChild(QComboBox, "actionTypeEditor") is action_type


def test_codex_status_tile_separates_agent_state_from_device_action(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    key_button = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "key.1"
    )
    codex_action = key_button.findChild(QLabel, "controlAction")
    assert codex_action.text() == "状态未接入"
    assert not isinstance(codex_action, Boring5RLabel)
    assert key_button.findChild(QLabel, "controlName").text() == "动作 · A"

    snapshot = view_model.model.snapshot
    assert snapshot is not None
    normal_snapshot = replace(snapshot, status={**snapshot.status, "operating_mode": "normal"})
    normal_host = QWidget()
    qtbot.addWidget(normal_host)
    normal_host.setLayout(create_control_layout(normal_snapshot))
    normal_key = next(
        button
        for button in normal_host.findChildren(QPushButton)
        if button.property("controlId") == "key.1"
    )
    normal_action = normal_key.findChild(QLabel, "controlAction")
    assert normal_action.text() == "A"
    assert isinstance(normal_action, Boring5RLabel)


def test_encoder_tile_opens_trigger_choices_on_overview(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    encoder = next(
        button
        for button in window.findChildren(QPushButton)
        if button.property("controlId") == "encoder"
    )
    qtbot.mouseClick(encoder, Qt.LeftButton)

    trigger_selector = window.findChild(QComboBox, "triggerSelector")
    assert trigger_selector is not None
    assert trigger_selector.itemText(trigger_selector.findData("encoder.cw")) == "旋钮顺时针"

    short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert short_name is not None
    short_name.setText("尚未保存的旋钮动作")
    warnings = []
    monkeypatch.setattr(
        "controller_config.views.main_window.confirm_local_draft",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Cancel,
    )
    trigger_selector.setCurrentIndex(trigger_selector.findData("encoder.cw"))

    restored_selector = window.findChild(QComboBox, "triggerSelector")
    restored_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert warnings
    assert restored_selector is not None
    assert restored_selector.currentData() == "encoder.ccw"
    assert restored_name is not None
    assert restored_name.text() == "尚未保存的旋钮动作"


def test_profile_management_is_embedded_in_key_mapping(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    assert "profiles" not in window._nav_buttons
    profile_button = window.findChild(QPushButton, "profilePill")
    assert profile_button is not None and profile_button.menu() is not None
    assert profile_button.menu().findChild(QAction, "createProfileAction") is not None
    assert profile_button.menu().findChild(QAction, "renameProfileAction") is not None

    window._select_physical_control("key.9")
    assert window.findChild(QWidget, "mappingEditorCard") is not None
    assert window.findChild(QLineEdit, "profileNameEditor") is None


def test_macos_profile_menu_hides_windows_and_lists_agent_templates_directly(
    qtbot, contract, monkeypatch
) -> None:
    import controller_config.views.main_window as main_window_module

    monkeypatch.setattr(main_window_module.sys, "platform", "darwin")
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    draft = view_model.draft
    assert draft is not None
    draft.rename_profile(draft.config["active_profile"], "Codex macOS")
    windows_profile_id = draft.copy_profile(draft.config["active_profile"])
    draft.rename_profile(windows_profile_id, "Codex Windows")

    menu = window._profile_menu(window)
    assert "Codex Windows" not in [action.text() for action in menu.actions()]
    windows_action = menu.findChild(
        QAction, f"profileSelectAction_{windows_profile_id}"
    )
    assert windows_action is None
    assert menu.findChild(QMenu, "otherPlatformProfilesMenu") is None
    assert menu.findChild(QMenu, "agentProfileTemplatesMenu") is None
    assert menu.minimumWidth() >= 232
    assert all(action.menu() is None for action in menu.actions())
    assert [
        action.text()
        for action in menu.actions()
        if action.objectName().startswith("agentProfileTemplateAction_")
    ] == [
        "WorkBuddy",
        "千问办公",
        "豆包（基础）",
    ]
    confirmed_profile_count = len(view_model.model.snapshot.config["profiles"])
    menu.findChild(QAction, "agentProfileTemplateAction_qwen-work").trigger()
    assert len(draft.profiles) == confirmed_profile_count + 2
    assert draft.profile(draft.config["active_profile"])["name"] == "千问办公 macOS"
    assert len(view_model.model.snapshot.config["profiles"]) == confirmed_profile_count
    draft.discard()


def test_windows_profile_menu_only_lists_windows_schemes(
    qtbot, contract, monkeypatch
) -> None:
    import controller_config.views.main_window as main_window_module

    monkeypatch.setattr(main_window_module.sys, "platform", "win32")
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    draft = view_model.draft
    assert draft is not None
    draft.rename_profile(draft.config["active_profile"], "Codex macOS")
    windows_profile_id = draft.copy_profile(draft.config["active_profile"])
    draft.rename_profile(windows_profile_id, "Codex Windows")

    menu = window._profile_menu(window)
    assert menu.findChild(
        QAction, f"profileSelectAction_{draft.profiles[0]['id']}"
    ) is None
    assert menu.findChild(
        QAction, f"profileSelectAction_{windows_profile_id}"
    ) is not None
    template = menu.findChild(QAction, "agentProfileTemplateAction_workbuddy")
    assert template is not None
    template.trigger()
    assert draft.profile(draft.config["active_profile"])["name"] == (
        "WorkBuddy Windows"
    )
    draft.discard()



def test_settings_groups_tools_and_language_before_device_read(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()

    assert "diagnostics" not in window._nav_buttons
    assert "firmware" not in window._nav_buttons
    assert "joystick" not in window._nav_buttons
    assert not any(
        button.text() == "摇杆校准" for button in window.findChildren(QPushButton)
    )
    settings = window._nav_buttons["settings"]
    assert settings.text() == ("" if settings.property("compactNavigation") else "固件与系统")
    assert settings.isEnabled()
    assert settings.toolTip() == "固件与系统"
    assert settings.accessibleName() == "固件与系统"
    qtbot.mouseClick(settings, Qt.LeftButton)
    assert view_model.page == "settings"
    assert window.findChild(QWidget, "settingsPage") is not None
    assert not window.findChildren(QPushButton, "sidebarLanguageButton")
    assert {
        button.text()
        for button in window.findChildren(QPushButton, "settingsLanguageButton")
    } == {"简体中文", "English", "日本語"}

    qtbot.waitUntil(
        lambda: any(
            item.text() == "系统" and item.isVisible()
            for item in window.findChildren(QPushButton, "settingsGroup")
        )
    )
    assert {
        item.text()
        for item in window.findChildren(QPushButton, "settingsGroup")
        if item.isVisible()
    } == {"系统", "设备", "固件更新"}

    device = next(
        item
        for item in window.findChildren(QPushButton, "settingsGroup")
        if item.text() == "设备" and item.isVisible()
    )
    qtbot.mouseClick(device, Qt.LeftButton)
    diagnostics = window.findChild(QPushButton, "openDeviceDiagnostics")
    assert diagnostics is not None and diagnostics.isEnabled()
    qtbot.mouseClick(diagnostics, Qt.LeftButton)
    assert view_model.page == "diagnostics"
    assert window.findChild(QWidget, "diagnosticsPage") is not None
    assert any(
        item.text() == "等待设备读取"
        for item in window.findChildren(QLabel)
    )
    assert window._nav_buttons["settings"].property("active") is True

    qtbot.waitUntil(
        lambda: any(
            item.text() == "固件更新" and item.isVisible()
            for item in window.findChildren(QPushButton, "settingsGroup")
        )
    )
    firmware = next(
        item
        for item in window.findChildren(QPushButton, "settingsGroup")
        if item.text() == "固件更新" and item.isVisible()
    )
    qtbot.mouseClick(firmware, Qt.LeftButton)
    assert view_model.page == "firmware"
    assert window.findChild(QWidget, "firmwareMaintenancePage") is not None
    assert any(
        item.text() == "等待设备读取"
        for item in window.findChildren(QLabel)
    )

    firmware_import = window.findChild(QPushButton, "selectFirmwarePackage")
    assert firmware_import is not None
    assert firmware_import.text() == "选择官方固件文件…"
    assert window.findChild(QProgressBar, "firmwareProgress") is None


def test_factory_reset_is_console_only_and_requires_two_confirmations(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    commands = []
    original_execute = gateway.execute_command

    def record(command) -> None:
        commands.append(command)
        original_execute(command)

    gateway.execute_command = record
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    view_model.navigate("settings")
    window._select_settings_section("device")

    button = window.findChild(QPushButton, "factoryResetDevice")
    assert button is not None and button.isEnabled()
    assert button.property("buttonRole") == "secondary"

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Cancel,
    )
    button.click()
    assert not any(command.name == "FACTORY_DEFAULT" for command in commands)

    replies = iter((QMessageBox.Yes, QMessageBox.Cancel))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: next(replies),
    )
    button.click()
    assert not any(command.name == "FACTORY_DEFAULT" for command in commands)

    replies = iter((QMessageBox.Yes, QMessageBox.Yes))
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: next(replies),
    )
    button.click()

    command = next(command for command in commands if command.name == "FACTORY_DEFAULT")
    assert command.message_type == 0x30
    assert command.payload["confirmation"] == "FACTORY_DEFAULT"


def test_extension_entry_page_is_navigable_and_exposes_live_management(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    platform = ExtensionPlatformController(
        view_model,
        contract,
        prompt_helper=None,
        manager=ExtensionManager(tmp_path / "extensions"),
        binding_store=ExtensionBindingStore(tmp_path / "bindings.json"),
        server_name=f"boring-extension-ui-{uuid.uuid4().hex}",
    )
    view_model.attach_extension_platform(platform)
    platform.start()
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()

    assert "actions" not in window._nav_buttons
    window._select_settings_section("system")
    button = window._content.findChild(QPushButton, "openAdvancedCustomization")
    assert button.accessibleName() == "打开高级自定义"
    assert button.isEnabled()
    qtbot.mouseClick(button, Qt.LeftButton)

    assert view_model.page == "actions"
    actions_page = window.findChild(ActionsPage, "actionsPage")
    assert actions_page is not None
    choices = window.findChild(QWidget, "customTaskChoices")
    assert choices is not None
    qtbot.waitUntil(choices.isVisible, timeout=1000)
    assert window.findChild(QPushButton, "createAction").isHidden()
    assert window.findChild(QPushButton, "openActionTools").isHidden()
    assert not window.findChildren(QPushButton, "actionTab")
    assert actions_page._stack.currentIndex() == ActionsPage.MY_ACTIONS
    assert "automations" not in window._nav_buttons
    assert "extensions" not in window._nav_buttons
    tools_choice = next(button for button in choices.findChildren(QPushButton)
                        if button.text() == "运行脚本或扩展")
    qtbot.mouseClick(tools_choice, Qt.LeftButton)
    extension_tab = next(button for button in window.findChildren(QPushButton, "developerLaneTab")
                         if button.property("developerLane") == 1)
    qtbot.mouseClick(extension_tab, Qt.LeftButton)
    assert window.findChild(ExtensionsPage, "extensionsPage") is not None
    status = next(
        label
        for label in window.findChildren(QLabel)
        if label.property("extensionPageStatus") is True
    )
    assert status.text() == "尚未安装扩展"
    assert status.objectName() == "extensionPlatformStatus"
    import_directory = window.findChild(QPushButton, "importExtensionDirectory")
    import_package = window.findChild(QPushButton, "importExtensionPackage")
    assert import_directory is not None and import_directory.isEnabled()
    assert import_package is not None and import_package.isEnabled()
    boundary = next(
        label
        for label in window.findChildren(QLabel)
        if label.property("extensionBoundary") is True
    )
    assert "不能直接使用串口" in boundary.text()
    view_model.shutdown()


def test_extension_page_imports_enables_and_binds_an_action(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway, contract, prompt_library_store=PromptLibraryStore(tmp_path / "prompts")
    )
    platform = ExtensionPlatformController(
        view_model,
        contract,
        prompt_helper=None,
        manager=ExtensionManager(tmp_path / "extensions"),
        binding_store=ExtensionBindingStore(tmp_path / "bindings.json"),
        server_name=f"boring-extension-ui-{uuid.uuid4().hex}",
    )
    view_model.attach_extension_platform(platform)
    platform.start()
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1_000)
    view_model.prompt_device._read_entries = [PromptEntry(1, "Trigger", "Configured on device")]
    view_model.prompt_device._finish_full_read()
    view_model.navigate("actions")
    actions_page = window.findChild(ActionsPage, "actionsPage")
    assert actions_page is not None
    actions_page.show_developer_lane(1)
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        lambda *_args, **_kwargs: str(EXTENSION_EXAMPLES / "claim_prompt"),
    )

    window.findChild(QPushButton, "importExtensionDirectory").click()

    extension_id = "com.boring.example.claim-prompt"
    assert platform.manager.get(extension_id).enabled is False
    toggle = window.findChild(QPushButton, "extensionToggle")
    assert toggle is not None and toggle.text() == "启用"
    toggle.click()
    qtbot.waitUntil(lambda: platform.extension_is_ready(extension_id), timeout=5_000)

    bind = window.findChild(QPushButton, "extensionBindAction")
    assert bind is not None and bind.isEnabled()
    bind.click()
    assert len(platform.bindings) == 1
    assert platform.bindings[0].extension_id == extension_id
    assert platform.bindings[0].action_id == "use_prompt"
    actions_page.show_section(ActionsPage.MY_ACTIONS)
    actions = actions_page._my_actions._list
    assert actions.count() == 1
    assert "提示词槽位 1" in actions.item(0).text()
    actions_page._my_actions._edit.click()
    assert actions_page._developer._extensions_page._selected_extension_id() == extension_id
    assert actions_page._developer._extensions_page._action.currentData() == "use_prompt"
    view_model.shutdown()


def test_automation_page_saves_and_tests_a_local_python_binding(
    qtbot, contract, tmp_path
) -> None:
    script = tmp_path / "collect_context.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    runner = _ImmediateScriptRunner()
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path / "prompts"),
        automation_store=AutomationStore(tmp_path / "automations"),
        script_runner=runner,
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model.prompt_device._read_entries = [PromptEntry(1, "Trigger", "Configured on device")]
    view_model.prompt_device._finish_full_read()

    window._select_settings_section("system")
    window._content.findChild(QPushButton, "openAdvancedCustomization").click()
    window._content.findChild(ActionsPage).show_developer_lane(0)
    trigger = window.findChild(QComboBox, "automationTrigger")
    trigger.setCurrentIndex(trigger.findData(1))
    name = window.findChild(QLineEdit, "automationName")
    script_path = window.findChild(QLineEdit, "automationScriptPath")
    enabled = window.findChild(QCheckBox, "automationEnabled")
    assert name is not None and script_path is not None and enabled is not None
    latest_event = window.findChild(QLabel, "automationLatestEvent")
    assert latest_event is not None
    view_model.event_bus.dispatch(
        DeviceEvent(
            "prompt.triggered",
            "usb.prompt",
            "CP01-OTHER-DEVICE",
            88,
            {
                "prompt_id": 1,
                "prompt_name": "其他设备",
                "prompt_body": "other",
                "body_bytes": 5,
            },
        )
    )
    qtbot.waitUntil(lambda: "尚未收到" in latest_event.text(), timeout=1000)
    current_serial = view_model.model.snapshot.identity["serial"]
    view_model.event_bus.dispatch(
        DeviceEvent(
            "prompt.triggered",
            "usb.prompt",
            current_serial,
            89,
            {
                "prompt_id": 1,
                "prompt_name": "当前设备",
                "prompt_body": "current",
                "body_bytes": 7,
            },
        )
    )
    qtbot.waitUntil(lambda: "event_id=89" in latest_event.text(), timeout=1000)
    name.setText("收集上下文")
    script_path.setText(str(script))
    enabled.setChecked(True)
    save = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "保存本地脚本"
    )
    qtbot.mouseClick(save, Qt.LeftButton)

    definitions = view_model.automation_host.definitions
    assert len(definitions) == 1
    assert definitions[0].name == "收集上下文"
    assert definitions[0].enabled
    run = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "手动测试"
    )
    qtbot.mouseClick(run, Qt.LeftButton)

    assert len(runner.launches) == 1
    assert runner.launches[0][1].kind == "automation.manual_test"
    log = window.findChild(QPlainTextEdit, "automationRunLog")
    assert log is not None
    qtbot.waitUntil(
        lambda: "运行完成：收集上下文" in log.toPlainText(),
        timeout=1000,
    )


def test_actions_page_keeps_section_and_unsaved_local_action_during_render(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        automation_store=AutomationStore(tmp_path / "automations"),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1_000)
    window._select_settings_section("system")
    window._content.findChild(QPushButton, "openAdvancedCustomization").click()
    window._content.findChild(ActionsPage).show_developer_lane(0)

    name = window.findChild(QLineEdit, "automationName")
    assert name is not None
    name.setText("尚未保存的本地动作")
    actions_page = window.findChild(ActionsPage, "actionsPage")
    assert actions_page is not None
    actions_page.show_developer_lane(1)

    view_model.changed.emit(view_model.model)

    restored_page = window.findChild(ActionsPage, "actionsPage")
    assert restored_page is actions_page
    assert restored_page._stack.currentIndex() == ActionsPage.DEVELOP_ACTIONS
    extension_tab = next(
        button
        for button in window.findChildren(QPushButton, "developerLaneTab")
        if button.property("developerLane") == 1
    )
    assert extension_tab.isChecked()
    restored_page.show_developer_lane(0)
    assert window.findChild(QLineEdit, "automationName").text() == (
        "尚未保存的本地动作"
    )
    view_model.shutdown()


def test_prompt_library_page_saves_real_utf8_to_device_in_one_action(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: view_model.prompt_library is not None and view_model.draft is not None,
        timeout=1000,
    )

    qtbot.mouseClick(window._nav_buttons["prompts"], Qt.LeftButton)
    name = window.findChild(QLineEdit, "promptNameEditor")
    body = window.findChild(QPlainTextEdit, "promptBodyEditor")
    save = window.findChild(QPushButton, "writePromptDevice")
    assert name is not None and body is not None and save is not None
    assert save.text() == "保存到设备"
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: pytest.fail(
            "快捷提示词的保存到设备不应再要求二次确认"
        ),
    )
    name.setText("代码审查")
    body.setPlainText("检查这段代码。\n先给结论。")
    qtbot.mouseClick(save, Qt.LeftButton)
    qtbot.waitUntil(lambda: not view_model.prompt_device.status.is_busy, timeout=1_000)

    assert view_model.prompt_library is not None
    assert view_model.prompt_library.draft_entry(1).body == "检查这段代码。\n先给结论。"
    assert view_model.prompt_library.confirmed_entry(1).body == "检查这段代码。\n先给结论。"
    assert window.findChild(QWidget, "promptLibraryPage") is not None


def test_prompt_palette_uses_fixed_slots_without_changing_profile_mappings(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    commands = []
    execute = gateway.execute_command

    def record_command(command):
        commands.append(command)
        execute(command)

    monkeypatch.setattr(gateway, "execute_command", record_command)
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window, before_close_func=lambda _window: view_model.discard_draft())
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: view_model.prompt_library is not None and view_model.draft is not None,
        timeout=1000,
    )

    assert view_model.draft is not None
    profile_id = view_model.draft.config["active_profile"]
    view_model.set_mapping(
        profile_id, "joystick.up", "旧提示词", {"type": "prompt", "prompt_id": 9}
    )
    view_model.set_mapping(
        profile_id,
        "joystick.right",
        "普通按键",
        {"type": "key", "usage": 4, "modifiers": [227]},
    )
    original_draft = deepcopy(view_model.draft.config)
    original_device = deepcopy(gateway._snapshot.config)
    commands.clear()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Yes)
    qtbot.mouseClick(window._nav_buttons["prompts"], Qt.LeftButton)
    direction_cards = window.findChildren(QPushButton, "promptDirectionCard")
    expected_slots = {
        "joystick.up": 1,
        "joystick.right": 2,
        "joystick.down": 3,
        "joystick.left": 4,
    }
    assert {
        button.property("directionControlId"): button.property("promptId")
        for button in direction_cards
    } == expected_slots
    assert window.findChild(QComboBox, "promptSlotSelector") is None
    assert window.findChild(QPushButton, "bindPromptDirection") is None
    assert window.findChild(QLabel, "promptDirectionBindingState") is None
    assert [step.property("guideControl") for step in window.findChildren(QPushButton, "promptOperationStep")] == [
        "key.12", "joystick.up", "encoder.press"
    ]
    guide = window.findChild(QLabel, "promptPaletteGuide").text()
    for instruction in ("12 号按键", "0.8 秒", "上、右、下、左", "短按旋钮确认", "3 号按键", "10 秒"):
        assert instruction in guide

    for control_id, prompt_id in expected_slots.items():
        card = next(
            button
            for button in window.findChildren(QPushButton, "promptDirectionCard")
            if button.property("directionControlId") == control_id
        )
        assert f"提示词槽位 {prompt_id}" in card.toolTip()
        qtbot.mouseClick(card, Qt.LeftButton)
        window.findChild(QLineEdit, "promptNameEditor").setText(f"提示词 {prompt_id}")
        window.findChild(QPlainTextEdit, "promptBodyEditor").setPlainText(
            f"继续任务 {prompt_id}。"
        )
        write = window.findChild(QPushButton, "writePromptDevice")
        assert write.isEnabled()
        assert write.text() == "保存到设备"
        qtbot.mouseClick(write, Qt.LeftButton)
        qtbot.waitUntil(lambda: not view_model.prompt_device.status.is_busy, timeout=1000)
        entry = view_model.prompt_library.confirmed_entry(prompt_id)
        assert entry is not None and entry.body == f"继续任务 {prompt_id}。"
        assert view_model.prompt_library.draft_entry(prompt_id) == entry
        assert view_model.draft.config == original_draft
        assert gateway._snapshot.config == original_device

    prompt_commands = [cmd for cmd in commands if cmd.name in {"SET_PROMPT", "GET_PROMPT"}]
    assert [(cmd.name, cmd.payload["prompt_id"]) for cmd in prompt_commands] == [
        (name, prompt_id)
        for prompt_id in range(1, 5)
        for name in ("SET_PROMPT", "GET_PROMPT")
    ]
    view_model.discard_draft()
    view_model.shutdown()


def test_prompt_library_shows_listener_online_only_after_demo_poll_succeeds(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
        prompt_helper_runtime=SimpleNamespace(handle_entry=lambda entry: None),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.prompt_library is not None, timeout=1000)

    qtbot.mouseClick(window._nav_buttons["prompts"], Qt.LeftButton)
    qtbot.waitUntil(
        lambda: (
            window.findChild(QLabel, "promptHelperOnlineState") is not None
            and window.findChild(QLabel, "promptHelperOnlineState").text()
            == "助手在线"
        ),
        timeout=1000,
    )

    event_log = window.findChild(QPlainTextEdit, "promptEventLog")
    assert event_log is not None
    assert "监听已就绪" in event_log.toPlainText()


def test_mapping_editor_adopts_action_after_direct_apply_and_readback(
    qtbot, contract, monkeypatch, request
) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    request.addfinalizer(view_model.shutdown)

    window._select_physical_control("key.8")
    baseline_card = window._current_mapping_card()
    assert baseline_card is not None
    baseline_editor = baseline_card.findChild(ActionEditor)
    baseline_apply = baseline_card.findChild(QPushButton, "applyMappingToDevice")
    assert baseline_editor is not None and baseline_apply is not None
    baseline_editor.set_action(
        {"type": "key", "usage": 25, "modifiers": [227]}
    )
    qtbot.mouseClick(baseline_apply, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=5000,
    )
    assert window.findChild(QPushButton, "confirmConfigurationWrite") is None
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    for key_index in (1, 2, 4, 5, 6, 7):
        window._select_physical_control(f"key.{key_index}")
    window._select_physical_control("key.8")
    card = window._current_mapping_card()
    assert card is not None
    action_editor = card.findChild(ActionEditor)
    apply_to_device = card.findChild(QPushButton, "applyMappingToDevice")
    assert action_editor is not None and apply_to_device is not None
    view_model.changed.emit(view_model.model)
    card = window._current_mapping_card()
    assert card is not None
    action_editor = card.findChild(ActionEditor)
    apply_to_device = card.findChild(QPushButton, "applyMappingToDevice")
    assert action_editor is not None and apply_to_device is not None
    record = card.findChild(QPushButton, "shortcutRecordButton")
    assert record is not None
    qtbot.mouseClick(record, Qt.MouseButton.LeftButton)
    qtbot.keyClick(record, Qt.Key.Key_X)
    assert action_editor.action() == {"type": "key", "usage": 27}

    qtbot.mouseClick(apply_to_device, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state
        in {
            ConfigTransactionState.ACTIVE,
            ConfigTransactionState.FAILED,
            ConfigTransactionState.CONFLICT,
            ConfigTransactionState.UNKNOWN,
        },
        timeout=5000,
    )
    assert view_model.write_transaction.state is ConfigTransactionState.ACTIVE, (
        view_model.write_transaction
    )
    assert window.findChild(QPushButton, "confirmConfigurationWrite") is None

    snapshot = view_model.model.snapshot
    assert snapshot is not None
    assert snapshot.mappings["key.8"]["action"] == {"type": "key", "usage": 27}
    assert view_model.draft is not None
    assert view_model.draft.mapping(0, "key.8")["action"] == {
        "type": "key",
        "usage": 27,
    }
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    current_card = window._current_mapping_card()
    assert current_card is not None
    current_editor = current_card.findChild(ActionEditor)
    assert current_editor is not None
    assert current_editor.action() == {"type": "key", "usage": 27}
    assert not window._mapping_editor_has_uncommitted_changes()


def test_prompt_library_keeps_unsaved_utf8_text_during_background_render(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window, before_close_func=lambda _window: view_model.navigate("overview"))
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.prompt_library is not None, timeout=1000)

    qtbot.mouseClick(window._nav_buttons["prompts"], Qt.LeftButton)
    name = window.findChild(QLineEdit, "promptNameEditor")
    body = window.findChild(QPlainTextEdit, "promptBodyEditor")
    assert name is not None and body is not None
    name.setText("实机验收")
    body.setPlainText("中文输入正常\nEnglish OK")
    body.setFocus()

    view_model.changed.emit(view_model.model)

    restored_name = window.findChild(QLineEdit, "promptNameEditor")
    restored_body = window.findChild(QPlainTextEdit, "promptBodyEditor")
    assert restored_name is not None and restored_body is not None
    assert restored_name.text() == "实机验收"
    assert restored_body.toPlainText() == "中文输入正常\nEnglish OK"
    assert window.focusWidget() is restored_body
    assert view_model.prompt_library is not None
    assert view_model.prompt_library.draft_entry(1) is None


def test_mapping_editor_keeps_unsaved_prompt_action_during_background_render(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.prompt_library is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    window._select_physical_control("key.8")
    action_editor = window.findChild(ActionEditor)
    short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert action_editor is not None and short_name is not None
    action_editor.set_action({"type": "prompt", "prompt_id": 1})
    short_name.setText("提示词")

    view_model.changed.emit(view_model.model)

    restored = window.findChild(ActionEditor)
    restored_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert restored is not None and restored_name is not None
    assert restored.action() == {"type": "prompt", "prompt_id": 1}
    assert restored_name.text() == "提示词"
    assert view_model.draft is not None
    saved_mapping = view_model.draft.mapping(
        view_model.draft.config["active_profile"], "key.8"
    )
    assert saved_mapping is None or saved_mapping["action"] != {
        "type": "prompt",
        "prompt_id": 1,
    }


def test_mapping_editor_preserves_existing_advanced_prompt_reference(
    qtbot, contract, tmp_path
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_library_store=PromptLibraryStore(tmp_path),
    )
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(
        lambda: view_model.prompt_library is not None and view_model.draft is not None,
        timeout=1000,
    )
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    assert view_model.draft is not None
    profile_id = view_model.draft.config["active_profile"]
    view_model.set_mapping(
        profile_id,
        "key.8",
        "高级提示词",
        {"type": "prompt", "prompt_id": 5},
    )
    window._select_physical_control("key.8")

    selector = window.findChild(QComboBox, "actionField_prompt_id")
    assert selector is not None
    assert selector.currentData() == 5
    assert {selector.itemData(index) for index in range(selector.count())} == {
        1,
        2,
        3,
        4,
        5,
    }
    view_model.discard_draft()


def test_joystick_page_exposes_real_calibration_controls_after_device_read(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    assert "joystick" not in window._nav_buttons
    view_model.navigate("diagnostics")
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    open_calibration = window.findChild(QPushButton, "openJoystickCalibration")
    assert report_issue is not None and report_issue.isEnabled()
    assert open_calibration is not None and open_calibration.isHidden()
    report_issue.click()
    assert not open_calibration.isHidden() and open_calibration.isEnabled()
    open_calibration.click()

    start = window.findChild(QPushButton, "startJoystickCalibration")
    assert start is not None and start.isEnabled()
    assert window.findChild(QProgressBar, "calibrationRawX") is not None
    assert window.findChild(QProgressBar, "calibrationRawY") is not None
    assert window._nav_buttons["settings"].property("active") is True

    back = window.findChild(QPushButton, "backToDiagnostics")
    assert back is not None and back.isEnabled()
    qtbot.mouseClick(back, Qt.LeftButton)
    assert view_model.page == "diagnostics"
    assert window.findChild(DiagnosticsPage, "diagnosticsPage") is not None


def test_planned_tool_pages_show_connected_capabilities(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)

    for page in ("firmware",):
        view_model.navigate(page)
        badges = [
            item
            for item in window.findChildren(QLabel)
            if item.property("capabilityState") is True
        ]
        assert len(badges) == 1
        assert badges[0].text() == "支持固件更新"

    view_model.navigate("diagnostics")
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    open_calibration = window.findChild(QPushButton, "openJoystickCalibration")
    assert report_issue is not None and report_issue.isEnabled()
    assert open_calibration is not None and open_calibration.isHidden()
    report_issue.click()
    assert not open_calibration.isHidden() and open_calibration.isEnabled()
    open_calibration.click()
    badges = [
        item
        for item in window.findChildren(QLabel)
        if item.property("capabilityState") is True
    ]
    assert len(badges) == 1
    assert badges[0].text() == "支持摇杆校准"


def test_diagnostics_page_updates_live_inputs_without_rebuilding_page(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    view_model.navigate("diagnostics")

    page = window.findChild(DiagnosticsPage, "diagnosticsPage")
    assert page is not None
    assert page._fact_values["host_output"].text() == "ENABLED"
    assert page._fact_values["local_page"].text() == "NONE"
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    gateway.status_updated.emit(
        {
            **snapshot.status,
            "inputs_neutral": False,
            "active_controls": ["key.3", "joystick.right"],
            "joystick_diagnostics": {
                **snapshot.status["joystick_diagnostics"],
                "raw_x": 3080,
                "filtered_x": 3010,
                "directions": 2,
                "radial_active": True,
                "radial_angle_turns": 0.25,
            },
        }
    )

    assert window.findChild(QWidget, "diagnosticsPage") is page
    assert window.findChild(QLabel, "diagnosticActiveControls").text() == (
        "key.3, joystick.right"
    )
    assert window.findChild(QProgressBar, "diagnosticRawX").format() == "3080"
    assert window.findChild(QPlainTextEdit, "diagnosticEventLog").toPlainText().count(
        "INPUT"
    ) == 1
    assert window.findChild(QPushButton, "copyDiagnosticSummary").isEnabled()
    assert window.findChild(QPushButton, "exportDiagnosticSummary").isEnabled()


def test_diagnostic_control_check_guides_and_captures_without_host_output(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    view_model.navigate("diagnostics")

    start = window.findChild(QPushButton, "startDiagnosticCapture")
    stop = window.findChild(QPushButton, "stopDiagnosticCapture")
    progress = window.findChild(QProgressBar, "diagnosticControlProgress")
    page = window.findChild(DiagnosticsPage, "diagnosticsPage")
    assert start is not None and start.isEnabled()
    assert stop is not None and not stop.isEnabled()
    assert progress is not None and progress.maximum() == 20

    qtbot.mouseClick(start, Qt.LeftButton)
    qtbot.waitUntil(lambda: view_model.diagnostic_capture_active, timeout=1000)
    stop = window.findChild(QPushButton, "stopDiagnosticCapture")
    page = window.findChild(DiagnosticsPage, "diagnosticsPage")
    assert stop is not None
    assert stop.isEnabled()
    assert page is not None and page._capture_state.text() == "诊断保护已开启"
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    open_calibration = window.findChild(QPushButton, "openJoystickCalibration")
    assert report_issue is not None and not report_issue.isEnabled()
    assert open_calibration is not None and open_calibration.isHidden()
    assert page._calibration_state.text() == "请先结束控件诊断"

    snapshot = view_model.model.snapshot
    assert snapshot is not None
    gateway.status_updated.emit(
        {
            **snapshot.status,
            "inputs_neutral": False,
            "active_controls": ["key.1"],
            "diagnostic_capture": {
                "active": True,
                "timeout_ms": 3000,
                "event_sequence": 1,
                "last_control": "key.1",
                "last_pressed": True,
            },
        }
    )
    progress = window.findChild(QProgressBar, "diagnosticControlProgress")
    assert progress is not None
    assert progress.value() == 1
    assert "key.1" in view_model.diagnostics.tested_controls
    assert any(
        item.text() == "按下并松开按键 2。"
        for item in window.findChildren(QLabel)
    )

    qtbot.mouseClick(window._nav_buttons["overview"], Qt.LeftButton)
    qtbot.waitUntil(lambda: not view_model.diagnostic_capture_active, timeout=1000)


def test_diagnostic_control_check_refuses_unsafe_legacy_firmware(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    features = dict(snapshot.capabilities["features"])
    features.pop("diagnostic_capture")
    gateway.snapshot_ready.emit(
        replace(
            snapshot,
            capabilities={**snapshot.capabilities, "features": features},
        )
    )
    view_model.navigate("diagnostics")

    start = window.findChild(QPushButton, "startDiagnosticCapture")
    assert start is not None and not start.isEnabled()
    assert any(
        item.text() == "当前固件不支持安全诊断"
        for item in window.findChildren(QLabel)
    )


def test_planned_tool_page_does_not_invent_missing_device_capability(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    features = dict(snapshot.capabilities["features"])
    features["joystick_calibration"] = False
    gateway.snapshot_ready.emit(
        replace(
            snapshot,
            capabilities={**snapshot.capabilities, "features": features},
        )
    )

    view_model.navigate("diagnostics")
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    open_calibration = window.findChild(QPushButton, "openJoystickCalibration")
    assert report_issue is not None and not report_issue.isEnabled()
    assert open_calibration is not None and open_calibration.isHidden()
    assert any(
        item.text() == "当前设备未提供摇杆校准"
        for item in window.findChildren(QLabel)
    )


def test_diagnostics_requires_local_draft_resolution_before_calibration(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    view_model.rename_profile(snapshot.active_profile_id, "待保存")
    view_model.navigate("diagnostics")

    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    assert report_issue is not None and not report_issue.isEnabled()
    assert any(
        item.text() == "先处理未保存修改"
        for item in window.findChildren(QLabel)
    )

    view_model.discard_draft()
    report_issue = window.findChild(QPushButton, "reportJoystickIssue")
    assert report_issue is not None and report_issue.isEnabled()


def test_device_key_sequences_live_under_key_configuration_menu(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)

    assert "macros" not in window._nav_buttons
    assert "profiles" not in window._nav_buttons
    assert not any(
        button.text() == "宏" for button in window.findChildren(QPushButton)
    )

    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    assert profile_pill.menu().findChild(
        QAction, "manageDeviceKeySequencesAction"
    ) is not None
    sequence_button = profile_pill.menu().findChild(QAction, "manageDeviceKeySequencesAction")
    assert sequence_button is not None
    sequence_button.trigger()
    qtbot.waitUntil(lambda: view_model.page == "sequences", timeout=1000)
    name = window.findChild(QLineEdit, "macroNameEditor")
    assert name is not None
    name.clear()
    name.insert("🚀" * contract.editor_rules.macro_name_max_length)
    assert name.text() == "🚀" * contract.editor_rules.macro_name_max_length
    name.insert("x")
    assert name.text() == "🚀" * contract.editor_rules.macro_name_max_length
    name.setText("Hello 2")
    save_macro = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "保存到本地草稿"
    )
    qtbot.mouseClick(save_macro, Qt.LeftButton)
    assert view_model.draft is not None
    assert view_model.draft.macro(0)["name"] == "Hello 2"
    assert view_model.draft.validate(contract) == ()
    view_model.discard_draft()


def test_profile_menu_opens_sequences_without_losing_control_editor(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")

    window._select_physical_control("key.8")
    short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert short_name is not None
    short_name.setText("尚未保存的按键名")
    assert window.findChild(QPushButton, "manageDeviceKeySequences") is None
    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    assert profile_pill.menu().findChild(
        QAction, "manageDeviceKeySequencesAction"
    ) is not None
    manage = profile_pill.menu().findChild(QAction, "manageDeviceKeySequencesAction")
    assert manage is not None
    manage.trigger()

    qtbot.waitUntil(lambda: view_model.page == "sequences", timeout=1000)
    assert view_model.page == "sequences"
    assert window.findChild(QLineEdit, "macroNameEditor") is not None

    view_model.navigate("overview")
    restored_short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert restored_short_name is not None
    assert restored_short_name.text() == "尚未保存的按键名"

    warnings = []
    monkeypatch.setattr(
        "controller_config.views.main_window.confirm_local_draft",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Discard,
    )
    window._select_physical_control("key.9")
    other_short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert other_short_name is not None
    assert other_short_name.text() != "尚未保存的按键名"
    assert warnings

    current_profile_pill = window._content.findChild(QPushButton, "profilePill")
    assert current_profile_pill.menu().findChild(QAction, "createProfileAction") is not None


def test_preferences_navigation_enables_after_device_read(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    def discard_test_edits(_window):
        view_model.cancel_device_write_confirmation()
        view_model.navigate("overview")
        view_model.screen_icon.attach(None)
        view_model.screen_glyphs.attach(None)
        view_model.discard_draft()
    qtbot.addWidget(window, before_close_func=discard_test_edits)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert view_model.draft is not None
    view_model.draft.config["lighting"]["brightness"] = 80

    preferences_button = window._nav_buttons["lighting"]
    assert preferences_button.toolTip() == "外观与反馈 · 灯光、震动、屏幕"
    qtbot.mouseClick(preferences_button, Qt.LeftButton)
    brightness = window.findChild(QSlider, "lightingBrightness")
    level_control = window.findChild(QWidget, "lightingLevelControl")
    assert brightness is not None
    assert level_control is not None and level_control.maximumWidth() == 410
    assert brightness.maximumWidth() == 340
    assert brightness.minimum() == 0
    assert brightness.maximum() == 4
    assert brightness.value() == 4
    brightness.setValue(2)
    screen_brightness = window.findChild(QSlider, "displayBrightness")
    assert screen_brightness is not None
    assert (screen_brightness.minimum(), screen_brightness.maximum()) == (0, 100)
    screen_brightness.setValue(37)
    original_status_key_colors = {
        index: deepcopy(view_model.draft.config["lighting"]["under_key"][index])
        for index in (0, 1, 3, 4, 5, 6)
    }
    function_key = next(
        button
        for button in window.findChildren(RgbButton, "rgbButton")
        if button.property("controlId") == "key.3"
    )
    original_function_rgb = deepcopy(
        view_model.draft.config["lighting"]["under_key"][2]
    )
    function_key._set_rgb((255, 0, 0), emit=True)
    save_local = window.findChild(QPushButton, "savePreferencesDraft")
    save_to_device = window.findChild(QPushButton, "savePreferencesToDevice")
    assert save_local is not None and save_local.text() == "保存草稿"
    assert save_local.property("buttonRole") == "secondary"
    assert save_to_device is not None and save_to_device.text() == "应用到设备"
    assert save_to_device.property("buttonRole") == "primary"
    qtbot.waitUntil(save_to_device.isVisible, timeout=1000)
    assert save_to_device.width() >= 160
    # This demo test covers configuration writes, not the separate icon readers.
    view_model.screen_icon.attach(None)
    view_model.screen_glyphs.attach(None)
    qtbot.mouseClick(save_to_device, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=5000,
    )
    assert view_model.draft.config["lighting"]["enabled"] is True
    assert view_model.draft.config["lighting"]["brightness"] == 20
    assert view_model.draft.config["display"]["brightness"] == 37
    assert view_model.model.snapshot.config_result["config"]["display"][
        "brightness"
    ] == 37
    assert view_model.draft.config["lighting"]["under_key"][2] == {
        "r": 255,
        "g": 0,
        "b": 0,
    }
    assert view_model.model.snapshot.config_result["config"]["lighting"][
        "under_key"
    ][2] == {"r": 255, "g": 0, "b": 0}
    assert {
        index: view_model.model.snapshot.config_result["config"]["lighting"][
            "under_key"
        ][index]
        for index in original_status_key_colors
    } == original_status_key_colors
    assert view_model.draft.validate(contract) == ()
    refreshed_function_key = next(
        button
        for button in window.findChildren(RgbButton, "rgbButton")
        if button.property("controlId") == "key.3"
    )
    refreshed_function_key._set_rgb(
        (
            original_function_rgb["r"],
            original_function_rgb["g"],
            original_function_rgb["b"],
        ),
        emit=True,
    )
    refreshed_save_to_device = window.findChild(
        QPushButton, "savePreferencesToDevice"
    )
    assert refreshed_save_to_device is not None
    assert refreshed_save_to_device.isEnabled()
    assert not any(
        button.text().startswith("状态灯 ")
        for button in window.findChildren(QPushButton)
    )
    view_model.discard_draft()


def test_lighting_page_previews_latest_value_without_saving_draft(
    qtbot,
    contract,
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert view_model.draft is not None
    original_brightness = view_model.draft.config["lighting"]["brightness"]
    view_model.navigate("lighting")
    toggle = window.findChild(QCheckBox, "lightingPreviewEnabled")
    brightness = window.findChild(QSlider, "lightingBrightness")
    assert toggle is not None and toggle.isEnabled()
    assert brightness is not None

    toggle.setChecked(True)
    assert toggle.isChecked()
    brightness.setValue(3)

    qtbot.waitUntil(
        lambda: gateway._lighting_preview is not None
        and gateway._lighting_preview["brightness"] == 40,
        timeout=1000,
    )
    qtbot.waitUntil(lambda: view_model.lighting_preview.active, timeout=1000)
    assert brightness is window.findChild(QSlider, "lightingBrightness")
    assert view_model.draft.config["lighting"]["brightness"] == original_brightness
    assert "尚未保存" in window.findChild(QLabel, "lightingPreviewStatus").text()

    brightness.setValue(0)
    qtbot.waitUntil(
        lambda: gateway._lighting_preview is not None
        and gateway._lighting_preview["enabled"] is False
        and gateway._lighting_preview["brightness"] == original_brightness,
        timeout=1000,
    )
    assert view_model.draft.config["lighting"]["brightness"] == original_brightness

    view_model.navigate("overview")
    qtbot.waitUntil(lambda: gateway._lighting_preview is None, timeout=1000)


def test_lighting_level_slider_preserves_legacy_value_until_user_moves_it(
    qtbot,
    contract,
) -> None:
    editor = PreferencesEditor(
        lighting={"enabled": True, "brightness": 15, "status": [], "under_key": []},
        haptic={},
        display={},
        features={},
        under_key_control_ids=(),
        agent_status_control_ids=frozenset(),
        rules=contract.editor_rules,
        lighting_levels=(0, 10, 20, 40, 80),
    )
    qtbot.addWidget(editor)
    slider = editor.findChild(QSlider, "lightingBrightness")
    current = editor.findChild(QLabel, "lightingLevelValue")
    assert slider is not None and slider.value() == 1
    assert current is not None and current.text() == "自定义"
    assert editor.lighting_value()["brightness"] == 15

    slider.sliderPressed.emit()
    assert current.text() == "自定义"
    assert editor.lighting_value()["brightness"] == 15

    slider.setValue(3)

    assert current.text() == "高"
    assert editor.lighting_value()["enabled"] is True
    assert editor.lighting_value()["brightness"] == 40

    slider.setValue(0)

    assert current.text() == "关闭灯光"
    assert editor.lighting_value()["enabled"] is False
    assert editor.lighting_value()["brightness"] == 15


def test_lighting_level_numbers_align_with_slider_snap_points(qtbot, contract) -> None:
    editor = PreferencesEditor(
        lighting={"enabled": True, "brightness": 40, "status": [], "under_key": []},
        haptic={},
        display={},
        features={},
        under_key_control_ids=(),
        agent_status_control_ids=frozenset(),
        rules=contract.editor_rules,
        lighting_levels=(0, 10, 20, 40, 80),
    )
    qtbot.addWidget(editor)
    editor.resize(720, 360)
    editor.show()
    qtbot.waitUntil(lambda: editor.isVisible(), timeout=1000)

    slider = editor.findChild(QSlider, "lightingBrightness")
    scale = editor.findChild(QWidget, "lightingLevelScale")
    marks = sorted(
        editor.findChildren(QWidget, "lightingLevelMark"),
        key=lambda mark: int(mark.property("levelValue")),
    )
    assert slider is not None
    assert scale is not None
    assert len(marks) == 5

    for value, mark in enumerate(marks):
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        option.sliderPosition = value
        option.sliderValue = value
        handle = slider.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            slider,
        )
        snap_center = slider.mapTo(scale, handle.center()).x()
        assert abs(mark.geometry().center().x() - snap_center) <= 1


def test_lighting_level_slider_shows_disabled_device_as_level_zero(
    qtbot,
    contract,
) -> None:
    editor = PreferencesEditor(
        lighting={"enabled": False, "brightness": 40, "status": [], "under_key": []},
        haptic={},
        display={},
        features={},
        under_key_control_ids=(),
        agent_status_control_ids=frozenset(),
        rules=contract.editor_rules,
        lighting_levels=(0, 10, 20, 40, 80),
    )
    qtbot.addWidget(editor)
    slider = editor.findChild(QSlider, "lightingBrightness")
    current = editor.findChild(QLabel, "lightingLevelValue")
    assert slider is not None and slider.value() == 0
    assert current is not None and current.text() == "关闭灯光"
    assert editor.lighting_value()["enabled"] is False
    assert editor.lighting_value()["brightness"] == 40

    slider.setValue(2)

    assert editor.lighting_value()["enabled"] is True
    assert editor.lighting_value()["brightness"] == 20


def test_lighting_preview_async_status_stays_in_english(
    qtbot,
    qapp,
    tmp_path,
    contract,
) -> None:
    manager = LanguageManager(
        qapp,
        settings=QSettings(
            str(tmp_path / "lighting-preview-language.ini"),
            QSettings.Format.IniFormat,
        ),
        initial_language=ENGLISH,
        persist=False,
    )
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model, language_manager=manager)
    qtbot.addWidget(window, before_close_func=lambda _window: view_model.navigate("overview"))
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model.navigate("lighting")
    toggle = window.findChild(QCheckBox, "lightingPreviewEnabled")
    level_value = window.findChild(QLabel, "lightingLevelValue")
    save_to_device = window.findChild(QPushButton, "savePreferencesToDevice")
    save_local = window.findChild(QPushButton, "savePreferencesDraft")
    assert toggle is not None
    assert level_value is not None and level_value.text() == "Custom"
    assert save_to_device is not None and save_to_device.text() == "Apply to Device"
    assert save_local is not None and save_local.text() == "Save draft"
    assert [
        label.text()
        for label in window.findChildren(QLabel, "lightingLevelMarkName")
    ] == ["Off", "Low", "Medium", "High", "Maximum"]

    toggle.setChecked(True)
    qtbot.waitUntil(lambda: view_model.lighting_preview.active, timeout=1000)
    status = window.findChild(QLabel, "lightingPreviewStatus")
    assert status is not None
    assert status.text() == (
        "Live Preview · Unsaved"
    )

    view_model.navigate("overview")
    qtbot.waitUntil(lambda: gateway._lighting_preview is None, timeout=1000)
    manager.set_language(SIMPLIFIED_CHINESE)


def test_rgb_button_emits_drag_preview_and_cancel_restores_original(qtbot) -> None:
    button = RgbButton("key.3", {"r": 1, "g": 2, "b": 3})
    qtbot.addWidget(button)
    button._dialog_original = (1, 2, 3)

    with qtbot.waitSignal(button.value_changed, timeout=1000):
        button._preview_color(QColor(10, 20, 30))
    assert button.value() == {"r": 10, "g": 20, "b": 30}

    with qtbot.waitSignal(button.value_changed, timeout=1000):
        button._restore_dialog_color()
    assert button.value() == {"r": 1, "g": 2, "b": 3}


def test_matrix12_lighting_editor_exposes_function_key_colors_only(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)

    view_model.navigate("lighting")
    qtbot.waitUntil(
        lambda: len(window.findChildren(RgbButton, "rgbButton")) == 6,
        timeout=1000,
    )

    swatches = window.findChildren(RgbButton, "rgbButton")
    assert {button.property("controlId") for button in swatches} == {
        "key.3",
        "key.8",
        "key.9",
        "key.10",
        "key.11",
        "key.12",
    }
    assert all(
        button.property("controlId") not in MATRIX12_AGENT_STATUS_KEYS
        for button in swatches
    )
    assert all(button.isHidden() for button in swatches)
    assert window.findChild(QLabel, "functionKeyLightingNote") is None
    assert window.findChild(QLabel, "officialWhiteKeyLightingNote") is None
    assert any(
        label.text() == "状态灯"
        for label in window.findChildren(QLabel)
    )
    view_model.discard_draft()


def test_matrix12_mapping_editor_locks_status_keys_and_edits_white_normal_mappings(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.model.snapshot is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None

    normal_snapshot = replace(
        snapshot,
        status={**snapshot.status, "operating_mode": "normal"},
    )
    gateway.snapshot_ready.emit(normal_snapshot)
    qtbot.waitUntil(
        lambda: view_model.model.snapshot.status.get("operating_mode") == "normal",
        timeout=1000,
    )

    window._select_physical_control("key.1")
    qtbot.waitUntil(lambda: window._current_mapping_card() is not None, timeout=2000)
    locked = window._current_mapping_card()
    assert locked is not None
    assert locked.findChild(ActionEditor) is None
    assert locked.findChild(QLabel, "inspectorTitle").text() == "状态灯键 1"
    assert "不会由控制台将应用切到前台" in locked.findChild(
        QLabel, "officialControlDefinitionNote"
    ).text()
    assert locked.findChild(QPushButton, "normalAgentFirmwareUpdate") is not None
    assert locked.findChild(QComboBox, "agentDesktopClient") is None

    window._select_physical_control("key.8")
    qtbot.waitUntil(lambda: window._current_mapping_card() is not None, timeout=2000)
    editable = window._current_mapping_card()
    assert editable is not None
    assert editable.findChild(QLabel, "inspectorTitle").text() == "功能键 8"
    assert editable.findChild(ActionEditor) is not None

    gateway.snapshot_ready.emit(snapshot)
    qtbot.waitUntil(
        lambda: view_model.model.snapshot.status.get("operating_mode") == "codex",
        timeout=1000,
    )
    window._select_physical_control("key.1")
    qtbot.waitUntil(lambda: window._current_mapping_card() is not None, timeout=2000)
    codex_locked = window._current_mapping_card()
    assert codex_locked.findChild(ActionEditor) is None
    assert "Agent 1" in codex_locked.findChild(
        QLabel, "officialControlDefinitionNote"
    ).text()

    window._select_physical_control("key.8")
    qtbot.waitUntil(lambda: window._current_mapping_card() is not None, timeout=2000)
    codex_normal_mapping = window._current_mapping_card()
    # The demo firmware does not declare codex_voice: preserve its existing
    # update notice instead of asserting the pre-voice-choice editor layout.
    assert snapshot.capabilities.get("features", {}).get("codex_voice") is not True
    assert codex_normal_mapping.findChild(ActionEditor) is None
    assert codex_normal_mapping.findChild(QLabel, "voiceFirmwareNotice") is not None


def test_matrix12_lighting_editor_preserves_all_per_key_colors(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window, before_close_func=lambda _window: view_model.navigate("overview"))
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert view_model.draft is not None
    original = [
        dict(color) for color in view_model.draft.config["lighting"]["under_key"]
    ]

    view_model.navigate("lighting")
    qtbot.waitUntil(
        lambda: len(window.findChildren(RgbButton, "rgbButton")) == 6,
        timeout=1000,
    )
    editor = window.findChild(PreferencesEditor)
    function_key = next(
        button
        for button in window.findChildren(RgbButton, "rgbButton")
        if button.property("controlId") == "key.3"
    )
    function_key._set_rgb((255, 0, 0), emit=True)
    editor._lighting_brightness.setValue(4)
    save = window.findChild(QPushButton, "savePreferencesDraft")
    assert save is not None
    qtbot.mouseClick(save, Qt.LeftButton)

    saved = view_model.draft.config["lighting"]["under_key"]
    for index in range(12):
        expected = {"r": 255, "g": 0, "b": 0} if index == 2 else original[index]
        assert saved[index] == expected
    view_model.discard_draft()


def test_key_mapping_profile_menu_can_create_a_schema_valid_profile(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert "profiles" not in window._nav_buttons
    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    create = profile_pill.menu().findChild(QAction, "createProfileAction")
    assert create is not None and create.text() == "新建空白方案"
    create.trigger()

    assert view_model.draft is not None
    assert len(view_model.draft.profiles) == 2
    assert view_model.draft.validate(contract) == ()
    active_profile_id = view_model.draft.config["active_profile"]
    assert view_model.draft.profile(active_profile_id)["name"] == "新配置方案"
    assert "新配置方案" in window.findChild(QPushButton, "profilePill").text()
    view_model.discard_draft()


def test_key_mapping_save_as_profile_copies_names_and_selects_current_layout(
    qtbot, contract, monkeypatch
) -> None:
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Writing", True),
    )
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert view_model.draft is not None
    source_profile_id = view_model.draft.config["active_profile"]
    source_mappings = [
        dict(mapping)
        for mapping in view_model.draft.profile(source_profile_id)["mappings"]
    ]

    save_as = window.findChild(QPushButton, "saveProfileAsButton")
    assert save_as is not None and save_as.text() == "另存为新方案…"
    qtbot.mouseClick(save_as, Qt.MouseButton.LeftButton)

    assert len(view_model.draft.profiles) == 2
    saved_profile_id = view_model.draft.config["active_profile"]
    assert saved_profile_id != source_profile_id
    assert view_model.draft.profile(saved_profile_id)["name"] == "Writing"
    assert view_model.draft.profile(saved_profile_id)["mappings"] == source_mappings
    pending = window.findChild(QLabel, "profilePendingState")
    assert pending is not None and pending.text() == "尚未应用到设备"
    assert "Writing" in window.findChild(QPushButton, "profilePill").text()
    view_model.discard_draft()


def test_profile_menu_switches_renames_and_deletes_only_in_local_draft(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    confirmed_profile_id = snapshot.active_profile_id

    profile_pill = window.findChild(QPushButton, "profilePill")
    create = profile_pill.menu().findChild(QAction, "createProfileAction")
    create.trigger()
    assert view_model.draft is not None
    new_profile_id = view_model.draft.config["active_profile"]
    assert new_profile_id != confirmed_profile_id
    assert view_model.model.snapshot.active_profile_id == confirmed_profile_id

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Writing", True),
    )
    profile_pill = window.findChild(QPushButton, "profilePill")
    profile_pill.menu().findChild(QAction, "renameProfileAction").trigger()
    assert view_model.draft.profile(new_profile_id)["name"] == "Writing"

    profile_pill = window.findChild(QPushButton, "profilePill")
    original_action = profile_pill.menu().findChild(
        QAction, f"profileSelectAction_{confirmed_profile_id}"
    )
    original_action.trigger()
    assert view_model.draft.config["active_profile"] == confirmed_profile_id

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("Writing Copy", True),
    )
    save_as = window.findChild(QPushButton, "saveProfileAsButton")
    assert save_as is not None
    qtbot.mouseClick(save_as, Qt.MouseButton.LeftButton)
    copied_profile_id = view_model.draft.config["active_profile"]
    assert copied_profile_id not in {confirmed_profile_id, new_profile_id}

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    delete_action = window.findChild(QPushButton, "profilePill").menu().findChild(
        QAction, "deleteProfileAction"
    )
    delete_action.trigger()
    assert all(
        profile["id"] != copied_profile_id for profile in view_model.draft.profiles
    )
    assert view_model.model.snapshot.active_profile_id == confirmed_profile_id
    view_model.discard_draft()


def test_profile_only_switch_is_labeled_as_switch_and_apply(
    qtbot, contract, monkeypatch
) -> None:
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: QMessageBox.Yes)
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    assert view_model.draft is not None
    confirmed_profile_id = view_model.draft.config["active_profile"]
    copied_profile_id = view_model.copy_profile(confirmed_profile_id)
    view_model.set_active_profile(copied_profile_id)
    view_model.prepare_device_write()
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state
        is ConfigTransactionState.AWAITING_CONFIRMATION,
        timeout=1000,
    )
    view_model.confirm_device_write()
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=2000,
    )
    assert view_model.draft is not None and not view_model.draft.is_dirty

    original_action = window.findChild(QPushButton, "profilePill").menu().findChild(
        QAction, f"profileSelectAction_{confirmed_profile_id}"
    )
    assert original_action is not None
    original_action.trigger()

    assert [change.path for change in view_model.draft.changes] == [
        "config.active_profile"
    ]
    assert view_model.draft.only_active_profile_changed
    pending = window.findChild(QLabel, "profilePendingState")
    apply_switch = window.findChild(QPushButton, "saveConfigurationToDevice")
    assert pending is not None and pending.text() == "尚未应用到设备"
    assert apply_switch is not None and apply_switch.text() == "切换并应用"
    view_model.discard_draft()


def test_profile_switch_warns_before_discarding_unsaved_control_editor_text(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    assert view_model.draft is not None
    original_profile_count = len(view_model.draft.profiles)

    window._select_physical_control("key.8")
    short_name = window.findChild(QLineEdit, "mappingShortNameEditor")
    assert short_name is not None
    short_name.setText("尚未保存")
    warnings = []
    monkeypatch.setattr(
        "controller_config.views.main_window.confirm_local_draft",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Cancel,
    )

    create_action = window.findChild(QPushButton, "profilePill").menu().findChild(
        QAction, "createProfileAction"
    )
    create_action.trigger()

    assert warnings
    assert len(view_model.draft.profiles) == original_profile_count
    assert short_name.text() == "尚未保存"


def test_profile_guard_treats_unmapped_editor_defaults_as_unchanged(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    _set_demo_mode(gateway, view_model, qtbot, "normal")
    assert view_model.draft is not None
    profile_id = view_model.draft.config["active_profile"]
    profile = view_model.draft.profile(profile_id)
    profile["mappings"] = [
        mapping
        for mapping in profile["mappings"]
        if mapping.get("control_id") != "key.8"
    ]
    window.render(view_model.model)

    window._select_physical_control("key.8")

    assert window.findChild(QLineEdit, "mappingShortNameEditor").text() == "未映射"
    assert not window._mapping_editor_has_uncommitted_changes()


def test_profile_editor_saves_in_one_action_and_reads_back(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    view_model.rename_profile(snapshot.active_profile_id, "Confirmed write")

    validate = window.findChild(QPushButton, "saveConfigurationToDevice")
    assert validate is not None and validate.isEnabled()
    qtbot.mouseClick(validate, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=5000,
    )
    assert window.findChild(QPushButton, "confirmConfigurationWrite") is None
    assert view_model.draft is not None and not view_model.draft.is_dirty
    assert view_model.model.snapshot.profile_name == "Confirmed write"


def test_prompt_listener_resumes_after_successful_config_write(
    qtbot, contract
) -> None:
    gateway = DemoGateway(contract, "ready")
    helper = SimpleNamespace(handle_entry=lambda _entry: None)
    view_model = MainViewModel(
        gateway,
        contract,
        prompt_helper_runtime=helper,
    )
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    qtbot.waitUntil(
        lambda: view_model.prompt_device.listener_status.state
        is PromptListenerState.READY,
        timeout=1000,
    )
    snapshot = view_model.model.snapshot
    assert snapshot is not None
    view_model.rename_profile(snapshot.active_profile_id, "Listener resumes")

    view_model.prepare_device_write()
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state
        is ConfigTransactionState.AWAITING_CONFIRMATION,
        timeout=1000,
    )
    view_model.confirm_device_write()
    qtbot.waitUntil(
        lambda: view_model.write_transaction.state is ConfigTransactionState.ACTIVE,
        timeout=2000,
    )

    assert (
        view_model.prompt_device.listener_status.state
        is PromptListenerState.STARTING
    )
    assert view_model.prompt_device._event_timer.isActive()


def test_window_blocks_close_during_active_write(qtbot, contract, monkeypatch) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Cancel,
    )
    view_model._set_write_transaction(
        ConfigTransaction(ConfigTransactionState.PENDING, "等待设备激活")
    )
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert warnings
    view_model._write_transaction = ConfigTransaction()


def test_window_hides_without_draft_warning_when_background_helper_keeps_process_alive(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    hidden = []
    class UsageSignals(QObject):
        changed = Signal(object)
    usage_signals = UsageSignals()
    background = SimpleNamespace(
        state_changed=view_model.changed,
        codex_usage_changed=usage_signals.changed,
        codex_usage_snapshot=CodexUsageSnapshot.unavailable(),
        refresh_usage=lambda: None,
        background_enabled=True,
        login_enabled=False,
        runtime_message="关闭窗口后继续运行",
        quitting=False,
        quit_application=lambda: None,
        hide_window=lambda: hidden.append(True) or True,
        set_background_enabled=lambda _enabled: None,
        set_login_enabled=lambda _enabled: None,
        set_helper_status=lambda **_status: None,
    )
    window = MainWindow(
        view_model,
        background_controller=background,
    )
    qtbot.addWidget(window)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model.rename_profile(0, "Unsaved local profile")
    prompts = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda prompt: prompts.append(prompt) or 0,
    )
    event = QCloseEvent()

    try:
        window.closeEvent(event)

        assert prompts == []
        assert hidden == [True]
        assert not event.isAccepted()
        assert view_model.draft is not None and view_model.draft.is_dirty
    finally:
        view_model.discard_draft()
        background.quitting = True


def test_discard_and_close_really_discards_dirty_draft(
    qtbot, contract, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model.rename_profile(0, "Unsaved local profile")
    real_exec = QMessageBox.exec

    def choose_discard(prompt):
        button = next(button for button in prompt.buttons() if button.text() == "丢弃并关闭")
        QTimer.singleShot(50, button.click)
        return real_exec(prompt)

    monkeypatch.setattr(QMessageBox, "exec", choose_discard)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()
    assert view_model.draft is not None and not view_model.draft.is_dirty


def test_import_is_disabled_while_unknown_write_requires_reconciliation(qtbot, contract) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    window.show()
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model._set_write_transaction(
        ConfigTransaction(
            ConfigTransactionState.UNKNOWN,
            "写入结果未知",
            candidate_digest="a" * 64,
        )
    )

    profile_pill = window.findChild(QPushButton, "profilePill")
    assert profile_pill is not None and profile_pill.menu() is not None
    import_button = profile_pill.menu().findChild(
        QAction, "importConfigurationAction"
    )
    assert import_button is not None

    assert not import_button.isEnabled()
    view_model._write_transaction = ConfigTransaction()


def test_dirty_import_can_export_current_draft_before_replacement(
    qtbot, contract, tmp_path, monkeypatch
) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    view_model.rename_profile(0, "Imported profile")
    path = tmp_path / "incoming.boring-config.json"
    view_model.export_configuration(path, kind="draft")
    view_model.rename_profile(0, "Current local draft")
    exported = []

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(path), "JSON"),
    )

    real_exec = QMessageBox.exec

    def choose_export(prompt):
        button = next(button for button in prompt.buttons() if button.text() == "导出当前草稿后导入")
        QTimer.singleShot(50, button.click)
        return real_exec(prompt)

    monkeypatch.setattr(QMessageBox, "exec", choose_export)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: QMessageBox.Ok)
    monkeypatch.setattr(
        window,
        "_export_configuration",
        lambda kind: exported.append(kind) or True,
    )

    window._import_configuration()

    assert exported == ["draft"]
    assert view_model.draft.profile(0)["name"] == "Imported profile"
    view_model.discard_draft()


def test_window_blocks_close_while_write_outcome_is_unknown(qtbot, contract, monkeypatch) -> None:
    gateway = DemoGateway(contract, "ready")
    view_model = MainViewModel(gateway, contract)
    window = MainWindow(view_model)
    qtbot.addWidget(window)
    view_model.start()
    qtbot.waitUntil(lambda: view_model.draft is not None, timeout=1000)
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **_kwargs: warnings.append(args) or QMessageBox.Cancel,
    )
    view_model._set_write_transaction(
        ConfigTransaction(
            ConfigTransactionState.UNKNOWN,
            "写入结果未知",
            candidate_digest="a" * 64,
            device_serial=view_model.draft.serial,
        )
    )
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    assert warnings
    view_model._write_transaction = ConfigTransaction()
