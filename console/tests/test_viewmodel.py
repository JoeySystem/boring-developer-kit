from __future__ import annotations

import copy
import pytest
from dataclasses import replace
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from controller_config.models import AppState, PortCandidate
from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
)
from controller_config.prompt_library import PromptLibraryStore
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel


class FakeGateway(QObject):
    candidates_found = Signal(object)
    progress = Signal(str)
    snapshot_ready = Signal(object)
    status_updated = Signal(object)
    command_completed = Signal(str, object)
    command_failed = Signal(str, object)
    failure = Signal(object, str, str)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.scan_calls = 0
        self.connected_ports: list[str] = []
        self.commands = []

    def scan(self, *, usb_only: bool = False) -> None:
        self.scan_calls += 1

    def connect_port(self, port_name: str) -> None:
        self.connected_ports.append(port_name)

    def execute_command(self, command) -> None:
        self.commands.append(command)

    def set_status_poll_interval(self, _interval_ms: int) -> None:
        pass

    def shutdown(self) -> None:
        pass


def test_icon_commands_share_gateway_and_block_concurrent_storage(qtbot, contract):
    from controller_config.prompt_library import PromptLibraryError
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    caps = copy.deepcopy(snapshot.capabilities)
    caps["features"]["custom_home_icon"] = True
    caps["screen_icon"] = {"version": 1, "target": "normal_home", "format": "rgb565_le",
        "width": 128, "height": 128, "total_bytes": 32768,
        "max_chunk_bytes": 1024, "session_timeout_ms": 15000}
    gateway.snapshot_ready.emit(replace(snapshot, capabilities=caps))
    vm.upload_screen_icon(bytes(32768))
    qtbot.waitUntil(lambda: any(c.name == "SCREEN_ICON_GET" for c in gateway.commands))
    with pytest.raises(ValueError, match="圆屏图标"):
        vm.prepare_device_write()
    with pytest.raises(PromptLibraryError, match="圆屏图标"):
        vm._ensure_prompt_device_operation_allowed()
    gateway.command_completed.emit("SCREEN_ICON_GET", {"result": {
        "revision": 0, "source": "default", "target": "normal_home", "format": "rgb565_le",
        "width": 128, "height": 128, "total_bytes": 0}})
    qtbot.waitUntil(lambda: any(c.name == "SCREEN_ICON_BEGIN" for c in gateway.commands))
    epoch = vm.screen_icon.connection_epoch
    gateway.disconnected.emit("test unplug")
    assert not vm.screen_icon.busy
    assert vm.screen_icon.connection_epoch > epoch
    vm.shutdown()


def test_no_device_state_is_actionable(qtbot) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway)
    gateway.candidates_found.emit(tuple())
    assert view_model.model.state is AppState.NO_DEVICE
    assert "重新扫描" not in view_model.model.message
    assert "连接设备" in view_model.model.technical_message


def test_no_device_keeps_scanning_until_bluetooth_returns(qtbot) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway)

    gateway.candidates_found.emit(tuple())

    assert view_model.model.state is AppState.NO_DEVICE
    assert view_model._reconnect_timer.isActive()
    view_model._reconnect_timer.timeout.emit()
    assert gateway.scan_calls == 1

    gateway.candidates_found.emit((
        PortCandidate("ble:returned-device", transport="bluetooth"),
    ))

    assert gateway.connected_ports == ["ble:returned-device"]
    assert not view_model._reconnect_timer.isActive()


def test_glyph_editor_uses_viewmodel_gateway_and_independent_storage(qtbot, contract, monkeypatch):
    import base64
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QMessageBox, QPushButton
    from controller_config.prompt_library import PromptLibraryError
    from controller_config.views.screen_glyph_editor import GlyphDraft, ScreenGlyphEditor
    from controller_config.views.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    monkeypatch.setattr(MainWindow, "closeEvent", lambda self, event: event.accept())
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    caps = copy.deepcopy(snapshot.capabilities)
    caps["features"]["custom_home_icon"] = True
    caps["screen_icon"] = {
        "version": 1, "target": "normal_home", "format": "rgb565_le",
        "width": 128, "height": 128, "total_bytes": 32768,
        "max_chunk_bytes": 1024, "session_timeout_ms": 15000,
    }
    caps["features"]["custom_glyph_icons"] = True
    caps["screen_glyphs"] = {"version": 1, "format": "alpha8", "count": 2}
    gateway.snapshot_ready.emit(replace(snapshot, capabilities=caps))
    window = MainWindow(vm)
    qtbot.addWidget(window)
    vm.navigate("lighting")
    window.show()
    assert not any(c.name.startswith("SCREEN_ICON_") for c in gateway.commands)
    assert not any(c.name.startswith("SCREEN_GLYPH_") for c in gateway.commands)
    window.findChild(QPushButton, "expandScreenIcons").click()
    qtbot.waitUntil(lambda: any(c.name == "SCREEN_ICON_GET" for c in gateway.commands))
    window.findChild(QPushButton, "expandScreenGlyphs").click()
    qtbot.wait(100)
    assert not any(c.name == "SCREEN_GLYPH_LIST" for c in gateway.commands)
    gateway.command_completed.emit("SCREEN_ICON_GET", {"result": {
        "revision": 0, "source": "default", "target": "normal_home",
        "format": "rgb565_le", "width": 128, "height": 128, "total_bytes": 0,
    }})
    qtbot.waitUntil(lambda: any(c.name == "SCREEN_GLYPH_LIST" for c in gateway.commands))
    items = [dict(id=id_, resource_id=i, width=2, height=2, editable=True)
             for i, id_ in enumerate(("timer", "settings"))]
    gateway.command_completed.emit("SCREEN_GLYPH_LIST", {"result": {
        "version": 1, "format": "alpha8", "icons": items}})
    qtbot.waitUntil(lambda: gateway.commands[-1].name == "SCREEN_GLYPH_GET")
    record = dict(id="timer", revision=0, source="default", width=2, height=2,
                  format="alpha8", data=base64.b64encode(bytes(4)).decode())
    gateway.command_completed.emit("SCREEN_GLYPH_GET", {"result": record})
    editor = window.findChild(ScreenGlyphEditor)
    assert editor is not None and editor.selector.count() == 2
    image = QImage(2, 2, QImage.Format_RGBA8888)
    image.fill("white")
    window._screen_glyph_drafts[editor.draft_key] = GlyphDraft(image, "test.png")
    editor._sync_transfer()
    commands_before_write = len(gateway.commands)
    editor.write_button.click()
    with pytest.raises(ValueError, match="圆屏图标"):
        vm.reset_screen_icon()
    with pytest.raises(PromptLibraryError, match="圆屏图标"):
        vm._ensure_prompt_device_operation_allowed()
    qtbot.waitUntil(lambda: len(gateway.commands) > commands_before_write
                    and gateway.commands[-1].name == "SCREEN_GLYPH_GET")
    gateway.command_completed.emit("SCREEN_GLYPH_GET", {"result": record})
    qtbot.waitUntil(lambda: gateway.commands[-1].name == "SCREEN_GLYPH_SET")
    assert gateway.commands[-1].payload["id"] == "timer"
    written = dict(record, revision=1, source="custom", data=base64.b64encode(b"\xff" * 4).decode())
    gateway.command_completed.emit("SCREEN_GLYPH_SET", {"result": written})
    qtbot.waitUntil(lambda: gateway.commands[-1].name == "SCREEN_GLYPH_GET")
    gateway.command_completed.emit("SCREEN_GLYPH_GET", {"result": written})
    assert not editor.draft.is_dirty
    editor.selector.setCurrentIndex(1)
    qtbot.waitUntil(lambda: gateway.commands[-1].payload == {"id": "settings"})
    assert all(c.name.startswith(("SCREEN_ICON_", "SCREEN_GLYPH_"))
               or c.name in {"CLEAR_LIGHTING_PREVIEW", "GET_PROMPT_EVENT"}
               for c in gateway.commands), [c.name for c in gateway.commands]
    epoch = vm.screen_glyphs.connection_epoch
    gateway.disconnected.emit("unplug")
    assert not vm.screen_glyphs.busy
    assert vm.screen_glyphs.connection_epoch > epoch
    vm.shutdown()
    window.hide()


def test_multiple_devices_require_explicit_selection(qtbot) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway)
    candidates = (PortCandidate("one"), PortCandidate("two"))
    gateway.candidates_found.emit(candidates)
    assert view_model.model.state is AppState.MULTIPLE_DEVICES
    assert view_model.model.candidates == candidates


def test_incompatible_read_failure_and_disconnect_are_distinct(qtbot) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway)
    gateway.failure.emit(BootstrapKind.INCOMPATIBLE, "不兼容", "UNSUPPORTED_PROTOCOL")
    assert view_model.model.state is AppState.INCOMPATIBLE
    gateway.failure.emit(
        BootstrapKind.AUTHENTICITY_FAILED,
        "无法确认这是 BORING 设备",
        "certificate_signature",
    )
    assert view_model.model.state is AppState.AUTHENTICITY_FAILED
    gateway.failure.emit(BootstrapKind.READ_FAILED, "读取失败", "GET_CONFIG timeout")
    assert view_model.model.state is AppState.READ_FAILED
    gateway.disconnected.emit("port gone")
    assert view_model.model.state is AppState.DISCONNECTED


def test_extension_platform_follows_an_accepted_device_session(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)

    class FakeExtensionPlatform:
        def __init__(self) -> None:
            self.start_calls = 0
            self.suspend_reasons: list[str] = []

        def start(self) -> None:
            self.start_calls += 1

        def suspend(self, reason: str) -> None:
            self.suspend_reasons.append(reason)

        def shutdown(self) -> None:
            pass

    platform = FakeExtensionPlatform()
    view_model.attach_extension_platform(platform)
    assert platform.start_calls == 0

    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert platform.start_calls == 1

    gateway.disconnected.emit("port gone")
    assert platform.suspend_reasons == ["设备连接已断开"]

    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert platform.start_calls == 2


def test_extension_platform_start_failure_does_not_break_device_session(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)

    class FailingExtensionPlatform:
        def start(self) -> None:
            raise RuntimeError("local API unavailable")

        def suspend(self, _reason: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    view_model.attach_extension_platform(FailingExtensionPlatform())
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))

    assert view_model.model.state is AppState.READY
    assert view_model.extension_platform_error == "local API unavailable"


def test_duplicate_screen_model_does_not_emit_changed(qtbot) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway)
    with qtbot.assertNotEmitted(view_model.changed):
        gateway.progress.emit("正在查找已连接到此电脑的 BORING 设备…")


def test_disconnect_preserves_last_snapshot(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = replace(
        _power_v2_snapshot(contract, read_only=False),
        port_name="test-port",
    )

    gateway.snapshot_ready.emit(snapshot)
    assert view_model.model.state is AppState.READY
    gateway.disconnected.emit("port gone")

    assert view_model.model.state is AppState.DISCONNECTED
    assert view_model.model.snapshot is snapshot


def test_disconnected_device_is_polled_and_reconnected_when_it_returns(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = replace(
        _power_v2_snapshot(contract, read_only=False),
        port_name="test-port",
    )
    gateway.snapshot_ready.emit(snapshot)
    gateway.disconnected.emit("port gone")

    assert view_model._reconnect_timer.isActive()
    assert gateway.scan_calls == 1
    view_model._reconnect_timer.timeout.emit()
    assert gateway.scan_calls == 2
    assert view_model.model.state is AppState.DISCONNECTED
    assert view_model.model.snapshot is snapshot

    gateway.candidates_found.emit(tuple())
    assert view_model.model.state is AppState.DISCONNECTED
    assert view_model._reconnect_timer.isActive()

    gateway.candidates_found.emit((PortCandidate("returned"),))
    assert view_model.model.state is AppState.CONNECTING
    assert gateway.connected_ports == ["returned"]
    assert not view_model._reconnect_timer.isActive()


def test_disconnect_starts_immediate_targeted_reconnect(qtbot, contract) -> None:
    class ReconnectGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.reconnects: list[tuple[str, bool]] = []

        def scan_reconnect(self, preferred_port: str, *, usb_only: bool = False) -> None:
            self.reconnects.append((preferred_port, usb_only))

    gateway = ReconnectGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = replace(
        _power_v2_snapshot(contract, read_only=False),
        port_name="ble:known-device",
    )
    gateway.snapshot_ready.emit(snapshot)

    gateway.disconnected.emit("link lost")

    assert gateway.reconnects == [("ble:known-device", False)]
    assert view_model._reconnect_timer.interval() <= 500


def test_runtime_status_update_changes_mode_without_replacing_config_or_draft(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    draft = view_model.draft

    gateway.status_updated.emit({**snapshot.status, "operating_mode": "normal"})

    updated = view_model.model.snapshot
    assert updated is not None
    assert updated.status["operating_mode"] == "normal"
    assert updated.config_result is snapshot.config_result
    assert view_model.draft is draft


def test_ble_slot_runtime_actions_use_shared_commands(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)

    view_model.select_ble_slot(2)
    view_model.clear_ble_slot(3)

    assert [(command.name, command.message_type, command.payload) for command in gateway.commands] == [
        ("BLE_SLOT_SELECT", 0x15, {"slot": 2}),
        ("BLE_SLOT_CLEAR", 0x16, {"slot": 3}),
    ]


def test_factory_reset_uses_existing_confirmed_protocol_command(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)

    view_model.factory_reset_device()

    command = gateway.commands[-1]
    assert command.name == "FACTORY_DEFAULT"
    assert command.message_type == 0x30
    assert command.payload == {
        "base_generation": snapshot.config_result["generation"],
        "confirmation": "FACTORY_DEFAULT",
    }
    assert command.retries == 0


def test_factory_reset_is_rejected_for_read_only_device(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=True))

    try:
        view_model.factory_reset_device()
    except ValueError as exc:
        assert "只读" in str(exc)
    else:
        raise AssertionError("read-only device unexpectedly accepted factory reset")

    assert gateway.commands == []


def test_ble_slot_status_refresh_is_visible_to_ui(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    updated_status = {
        **snapshot.status,
        "codex_micro": {
            "active_slot": 2,
            "slots": [
                {"slot": 1, "paired": True, "connected": False},
                {"slot": 2, "paired": True, "connected": True},
                {"slot": 3, "paired": False, "connected": False},
            ],
        },
    }

    with qtbot.waitSignal(view_model.changed):
        gateway.status_updated.emit(updated_status)
    assert view_model.model.snapshot.status["codex_micro"]["active_slot"] == 2


def test_lighting_preview_sends_snapshot_and_clears_when_leaving_page(
    qtbot,
    contract,
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert view_model.draft is not None
    view_model.navigate("lighting")

    view_model.set_lighting_preview_enabled(
        True,
        view_model.draft.config["lighting"],
    )
    view_model._lighting_preview_debounce.stop()
    view_model._send_lighting_preview()

    preview_command = gateway.commands[-1]
    assert preview_command.name == SET_LIGHTING_PREVIEW
    assert set(preview_command.payload) == {"enabled", "brightness", "under_key"}
    assert "status" not in preview_command.payload
    gateway.command_completed.emit(
        SET_LIGHTING_PREVIEW,
        {"command": SET_LIGHTING_PREVIEW, "result": {"state": "ACTIVE"}},
    )
    assert view_model.lighting_preview.active is True
    assert view_model._lighting_preview_heartbeat.isActive()

    with qtbot.assertNotEmitted(view_model.lighting_preview_changed):
        view_model._send_lighting_preview()
    assert gateway.commands[-1].name == SET_LIGHTING_PREVIEW

    view_model.navigate("overview")

    assert gateway.commands[-1].name == CLEAR_LIGHTING_PREVIEW
    assert view_model.lighting_preview.requested is False
    assert not view_model._lighting_preview_heartbeat.isActive()


def test_lighting_preview_is_cleared_before_config_validation(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert view_model.draft is not None
    view_model.navigate("lighting")
    view_model.set_lighting_preview_enabled(
        True,
        view_model.draft.config["lighting"],
    )
    view_model._lighting_preview_debounce.stop()
    view_model._send_lighting_preview()
    view_model.draft.config["lighting"]["brightness"] = 31

    view_model.prepare_device_write()

    assert [command.name for command in gateway.commands[-2:]] == [
        CLEAR_LIGHTING_PREVIEW,
        "VALIDATE_CONFIG",
    ]
    assert view_model.lighting_preview.candidate is None


def test_configuration_write_preserves_function_key_mapping_and_custom_light(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert view_model.draft is not None
    draft = view_model.draft
    draft.set_mapping(
        draft.config["active_profile"],
        "key.3",
        "Custom",
        {"type": "key", "usage": 40, "modifiers": []},
    )
    draft.config["lighting"]["under_key"][2] = {"r": 255, "g": 0, "b": 0}

    view_model.prepare_device_write()

    candidate = gateway.commands[-1].payload["config"]
    restored = next(
        item
        for item in candidate["profiles"][0]["mappings"]
        if item["control_id"] == "key.3"
    )
    assert restored == {
        "control_id": "key.3",
        "short_name": "Custom",
        "action": {"type": "key", "usage": 40, "modifiers": []},
    }
    assert candidate["lighting"]["under_key"][2] == {"r": 255, "g": 0, "b": 0}


def test_platform_change_does_not_replace_custom_function_key_mapping(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    snapshot.config_result["config"]["profiles"][0]["mappings"].append(
        {
            "control_id": "key.9",
            "short_name": "Custom",
            "action": {"type": "key", "usage": 40, "modifiers": []},
        }
    )
    gateway.snapshot_ready.emit(snapshot)
    assert view_model.draft is not None
    profile_id = view_model.draft.config["active_profile"]
    assert view_model.draft.mapping(profile_id, "key.9")["action"] == {
        "type": "key",
        "usage": 40,
        "modifiers": [],
    }
    assert not view_model.draft.is_dirty

    gateway.status_updated.emit({**snapshot.status, "platform": "windows_linux"})

    assert view_model.draft.platform == "windows_linux"
    assert view_model.draft.mapping(profile_id, "key.9")["action"] == {
        "type": "key",
        "usage": 40,
        "modifiers": [],
    }
    assert not view_model.draft.is_dirty


def test_lighting_preview_stops_on_device_local_lighting_page(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    gateway.snapshot_ready.emit(snapshot)
    assert view_model.draft is not None
    view_model.navigate("lighting")
    view_model.set_lighting_preview_enabled(
        True,
        view_model.draft.config["lighting"],
    )
    view_model._lighting_preview_debounce.stop()
    view_model._send_lighting_preview()
    updated_status = copy.deepcopy(snapshot.status)
    updated_status.setdefault("action_engine", {})["local_page"] = "lighting"

    gateway.status_updated.emit(updated_status)

    assert gateway.commands[-1].name == CLEAR_LIGHTING_PREVIEW
    assert view_model.lighting_preview.requested is False


def test_lighting_preview_busy_failure_is_visible_and_stops_heartbeat(
    qtbot,
    contract,
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert view_model.draft is not None
    view_model.navigate("lighting")
    view_model.set_lighting_preview_enabled(
        True,
        view_model.draft.config["lighting"],
    )
    view_model._lighting_preview_debounce.stop()
    view_model._send_lighting_preview()

    gateway.command_failed.emit(
        SET_LIGHTING_PREVIEW,
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备拒绝请求",
            "BUSY: local lighting menu owns output",
            error_name="BUSY",
        ),
    )

    assert view_model.lighting_preview.requested is False
    assert "本地灯光设置" in view_model.lighting_preview.message
    assert not view_model._lighting_preview_heartbeat.isActive()


def test_lighting_preview_is_unavailable_without_capability(qtbot, contract) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    snapshot = _power_v2_snapshot(contract, read_only=False)
    capabilities = copy.deepcopy(snapshot.capabilities)
    capabilities["features"].pop("lighting_preview", None)
    gateway.snapshot_ready.emit(replace(snapshot, capabilities=capabilities))
    view_model.navigate("lighting")

    try:
        view_model.set_lighting_preview_enabled(
            True,
            view_model.draft.config["lighting"],
        )
    except ValueError as exc:
        assert "不支持实时预览" in str(exc)
    else:
        raise AssertionError("unsupported device unexpectedly accepted preview")

    assert gateway.commands == []


def test_prompt_draft_is_bound_to_serial_and_persists_between_sessions(
    qtbot, contract, tmp_path
) -> None:
    store = PromptLibraryStore(tmp_path)
    snapshot = _power_v2_snapshot(contract, read_only=False)

    first_gateway = FakeGateway()
    first = MainViewModel(first_gateway, contract, prompt_library_store=store)
    first_gateway.snapshot_ready.emit(snapshot)
    first.save_prompt_draft(1, "代码审查", "先给结论。\n再解释原因。")

    second_gateway = FakeGateway()
    second = MainViewModel(second_gateway, contract, prompt_library_store=store)
    second_gateway.snapshot_ready.emit(snapshot)

    assert second.prompt_library is not None
    assert second.prompt_library.serial == snapshot.identity["serial"]
    assert second.prompt_library.draft_entry(1).name == "代码审查"
    assert second.prompt_library.dirty_prompt_ids == (1,)


def test_extension_context_revision_tracks_draft_and_listener_changes(
    qtbot, contract
) -> None:
    gateway = FakeGateway()
    view_model = MainViewModel(gateway, contract)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    initial_revision = view_model.extension_context_revision

    assert view_model.draft is not None
    profile_id = int(view_model.draft.config["active_profile"])
    view_model.rename_profile(profile_id, "Updated profile")
    draft_revision = view_model.extension_context_revision
    assert draft_revision > initial_revision
    assert view_model.extension_context().draft["dirty"] is True

    view_model.prompt_device.set_polling_paused(True)
    assert view_model.extension_context_revision > draft_revision
    assert view_model.extension_context().prompt_listener["state"] == "paused"


def test_ble_known_bad_firmware_stops_but_auth_interruption_reconnects(qtbot, contract):
    gateway = FakeGateway()
    vm = MainViewModel(gateway, contract)
    vm._reconnect_timer.start()
    gateway.failure.emit(BootstrapKind.FIRMWARE_UPDATE_REQUIRED, '请通过 USB 更新固件', 'HELLO')
    assert vm.model.state is AppState.FIRMWARE_UPDATE_REQUIRED
    assert not vm._reconnect_timer.isActive()
    gateway.failure.emit(BootstrapKind.AUTHENTICATION_INTERRUPTED, '蓝牙认证中断', 'AUTH_CHALLENGE')
    assert vm.model.state is AppState.DISCONNECTED
    assert vm._reconnect_timer.isActive()
    qtbot.waitUntil(lambda: gateway.scan_calls == 1, timeout=1000)
    gateway.snapshot_ready.emit(_power_v2_snapshot(contract, read_only=False))
    assert vm.model.state is AppState.READY
