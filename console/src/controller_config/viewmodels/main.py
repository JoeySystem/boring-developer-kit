from __future__ import annotations

import copy
import hashlib
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.actions import ActionDefinition, action_definitions
from controller_config.automation import (
    AutomationError,
    AutomationHost,
    AutomationStore,
    DeviceEventBus,
    LocalScriptAutomation,
    ScriptRunner,
)
from controller_config.extensions.context import build_extension_context
from controller_config.extensions.contracts import ExtensionContext
from controller_config.host_actions import (
    HostAction,
    HostActionRegistry,
    collect_host_actions,
)
from controller_config.config_files import (
    ConfigPackage,
    export_config_package,
    load_config_package,
)
from controller_config.ble_name import BleNameSession
from controller_config.normal_agent import NormalAgentSession
from controller_config.drafts import LocalDraft
from controller_config.diagnostics import DiagnosticsSession, diagnostic_capture
from controller_config.firmware_update import (
    FirmwarePackage,
    FirmwarePackageError,
    FirmwareStatus,
    FirmwareUpdateState,
    FirmwareUpdateTransaction,
    firmware_abort_command,
    firmware_end_command,
    firmware_status_command,
)
from controller_config.firmware_origin import FirmwareReleaseHistory
from controller_config.firmware_signature import FirmwareSignatureError
from controller_config.firmware_release import (
    release_is_newer,
    load_local_firmware_package,
    validate_minimum_app_version,
    FirmwareReleaseError,
    FirmwareReleaseSource,
    RemoteFirmwareBundle,
    RemoteFirmwareCheck,
    RemoteFirmwareRelease,
    RemoteFirmwareState,
    load_remote_firmware_bundle,
)
from controller_config.joystick_calibration import (
    CalibrationError,
    CalibrationSample,
    CalibrationState,
    CalibrationTransaction,
    calibration_cancel_command,
    calibration_confirm_command,
    calibration_sample_command,
    calibration_start_command,
    parse_calibration_confirmation,
)
from controller_config.lighting_preview import (
    CLEAR_LIGHTING_PREVIEW,
    SET_LIGHTING_PREVIEW,
    LightingPreviewError,
    LightingPreviewStatus,
    build_lighting_preview_snapshot,
    clear_lighting_preview_command,
    set_lighting_preview_command,
    validate_lighting_preview_response,
)
from controller_config.models import AppState, DeviceSnapshot, PortCandidate, ScreenModel
from controller_config.prompt_library import (
    PROMPT_SLOT_COUNT,
    PromptEntry,
    PromptLibrary,
    PromptLibraryError,
    PromptLibraryStore,
)
from controller_config.prompt_device import PromptDeviceSession
from controller_config.prompt_helper import PromptHelperRuntime
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind, Command
from controller_config.protocol.contract import Contract, ContractError
from controller_config.protocol.framing import canonical_json_bytes
from controller_config.transactions import (
    ConfigTransaction,
    ConfigTransactionState,
    HAPTIC_OPTIONAL_CHANNELS,
    configs_match_readback,
)
from controller_config.host_tasks import HostTasks
from controller_config.workflow_runtime import WorkflowHost, WorkflowRunResult
from controller_config.workflows import LocalWorkflow, WorkflowError, WorkflowStore
from controller_config.screen_icon_transfer import ScreenIconTransfer
from controller_config.screen_glyph_transfer import ScreenGlyphTransfer


class DeviceGateway(Protocol):
    candidates_found: Signal
    progress: Signal
    snapshot_ready: Signal
    status_updated: Signal
    command_completed: Signal
    command_failed: Signal
    failure: Signal
    disconnected: Signal

    def scan(self, *, usb_only: bool = False) -> None: ...

    def connect_port(self, port_name: str) -> None: ...

    def execute_command(self, command: Command) -> None: ...

    def set_status_poll_interval(self, interval_ms: int) -> None: ...

    def shutdown(self) -> None: ...


class MainViewModel(QObject):
    changed = Signal(object)
    ble_slot_failed = Signal(str, str)
    diagnostics_changed = Signal()
    lighting_preview_changed = Signal(object)
    extension_context_changed = Signal(int)

    def __init__(
        self,
        gateway: DeviceGateway,
        contract: Contract | None = None,
        firmware_release_source: FirmwareReleaseSource | None = None,
        prompt_library_store: PromptLibraryStore | None = None,
        prompt_helper_runtime: PromptHelperRuntime | None = None,
        automation_store: AutomationStore | None = None,
        script_runner: ScriptRunner | None = None,
        workflow_store: WorkflowStore | None = None,
        host_action_registry: HostActionRegistry | None = None,
        parent: QObject | None = None,
        firmware_release_history: FirmwareReleaseHistory | None = None,
    ) -> None:
        super().__init__(parent)
        self._gateway = gateway
        self.ble_name = BleNameSession(gateway.execute_command, self.ble_name_write_block_reason, self)
        self._known_device_names: dict[str, tuple[str, str]] = {}
        from controller_config.codex_agent_focus import CodexAgentFocus
        self.codex_agent_focus = CodexAgentFocus(self)
        self._normal_agent_status_refresh = False
        self.normal_agent = NormalAgentSession(gateway.execute_command, self.normal_agent_write_block_reason, self)
        self.normal_agent.changed.connect(self._sync_normal_agent_focus)
        from controller_config.claude_status import ClaudeStatusBridge
        self.claude_status = ClaudeStatusBridge(gateway, parent=self)
        self._contract = contract
        self._firmware_release_source = firmware_release_source
        self._firmware_release_history = firmware_release_history or FirmwareReleaseHistory()
        self._prompt_library_store = prompt_library_store or PromptLibraryStore()
        self._prompt_library: PromptLibrary | None = None
        self._prompt_library_error = ""
        self._model = ScreenModel(AppState.SCANNING, "正在查找已连接到此电脑的 BORING 设备…")
        self._page = "overview"
        self._drafts: dict[tuple[str, str, int], LocalDraft] = {}
        self._draft: LocalDraft | None = None
        self._config_refresh: tuple[str, str] | None = None
        self._write_transaction = ConfigTransaction()
        self._confirm_write_after_validation = False
        self._firmware_update = FirmwareUpdateTransaction()
        self._remote_firmware = RemoteFirmwareCheck(
            state=(
                RemoteFirmwareState.IDLE
                if firmware_release_source is not None
                else RemoteFirmwareState.UNCONFIGURED
            ),
            message=(
                "可以检查面向当前硬件的在线发布固件"
                if firmware_release_source is not None
                else "在线固件服务尚未配置"
            ),
        )
        self._remote_firmware_device: tuple[str, str, str] | None = None
        self._remote_firmware_last_check: dict[tuple[str, str, str], float] = {}
        self._calibration = CalibrationTransaction()
        self.screen_icon = ScreenIconTransfer(contract, gateway.execute_command, self)
        self.screen_glyphs = ScreenGlyphTransfer(gateway.execute_command, self)
        self._diagnostics = DiagnosticsSession()
        self._diagnostic_capture_pending = ""
        self._diagnostic_capture_error = ""
        self._lighting_preview = LightingPreviewStatus()
        self._lighting_preview_sent = False
        self._lighting_preview_clear_pending = False
        self._extension_context_revision = 0
        self._extension_listener_status = None
        self._extension_platform = None
        self._extension_platform_error = ""
        self._event_bus = DeviceEventBus(parent=self)
        self._workflow_host = WorkflowHost(
            workflow_store or WorkflowStore(),
            registry=host_action_registry,
            parent=self,
        )
        self._automation_host = AutomationHost(
            automation_store or AutomationStore(),
            runner=script_runner,
            parent=self,
        )
        self.host_tasks = HostTasks(self, gateway.execute_command, parent=self)
        self._workflow_host.external_busy = lambda: self._automation_host.running or self.host_tasks.extensions_busy
        self._automation_host.external_busy = lambda: self._workflow_host.running or self.host_tasks.extensions_busy
        self._event_bus.register(self._busy_task_event)
        self._event_bus.register(self._workflow_host.handle_event)
        self._event_bus.register(self._automation_host.handle_event)
        self._prompt_device = PromptDeviceSession(
            gateway.execute_command,
            self._prompt_library_store,
            helper=prompt_helper_runtime,
            event_dispatcher=self._event_bus.dispatch,
            auto_refresh=False,
            auto_poll=True,
            changed=self._on_prompt_device_changed,
            parent=self,
        )
        self._extension_listener_status = self._prompt_device.listener_status
        self._write_deadline = QTimer(self)
        self._write_deadline.setSingleShot(True)
        self._write_deadline.setInterval(10_000)
        self._write_deadline.timeout.connect(self._on_write_deadline)
        self._connection_port = ""
        self._failed_connection_ports: set[str] = set()
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setInterval(500)
        self._reconnect_timer.timeout.connect(self._scan_for_reconnect)
        self._usb_probe_timer = QTimer(self)
        self._usb_probe_timer.setInterval(2000)
        self._usb_probe_timer.timeout.connect(self._probe_usb_handoff)
        # A lightweight tick retries deferred checks; network attempts remain six hours apart.
        self._remote_firmware_check_timer = QTimer(self)
        self._remote_firmware_check_timer.setInterval(60_000)
        self._remote_firmware_check_timer.timeout.connect(self.auto_check_remote_firmware)
        if firmware_release_source is not None:
            self._remote_firmware_check_timer.start()
        self._firmware_status_timer = QTimer(self)
        self._firmware_status_timer.setSingleShot(True)
        self._firmware_status_timer.setInterval(200)
        self._firmware_status_timer.timeout.connect(self._poll_firmware_status)
        self._firmware_deadline = QTimer(self)
        self._firmware_deadline.setSingleShot(True)
        self._firmware_deadline.setInterval(15_000)
        self._firmware_deadline.timeout.connect(self._on_firmware_deadline)
        self._calibration_sample_timer = QTimer(self)
        self._calibration_sample_timer.setSingleShot(True)
        self._calibration_sample_timer.setInterval(100)
        self._calibration_sample_timer.timeout.connect(self._poll_calibration_sample)
        self._calibration_activation_deadline = QTimer(self)
        self._calibration_activation_deadline.setSingleShot(True)
        self._calibration_activation_deadline.setInterval(10_000)
        self._calibration_activation_deadline.timeout.connect(
            self._on_calibration_activation_deadline
        )
        self._diagnostic_capture_heartbeat = QTimer(self)
        self._diagnostic_capture_heartbeat.setInterval(1000)
        self._diagnostic_capture_heartbeat.timeout.connect(
            self._renew_diagnostic_capture
        )
        self._lighting_preview_debounce = QTimer(self)
        self._lighting_preview_debounce.setSingleShot(True)
        self._lighting_preview_debounce.setInterval(50)
        self._lighting_preview_debounce.timeout.connect(
            self._send_lighting_preview
        )
        self._lighting_preview_heartbeat = QTimer(self)
        self._lighting_preview_heartbeat.setInterval(1000)
        self._lighting_preview_heartbeat.timeout.connect(
            self._send_lighting_preview
        )
        self.changed.connect(lambda model: self.host_tasks.attach(model.snapshot if model.state is AppState.READY else None))
        gateway.candidates_found.connect(self._on_candidates)
        gateway.progress.connect(self._on_progress)
        gateway.snapshot_ready.connect(self._on_snapshot)
        gateway.status_updated.connect(self._on_status_updated)
        gateway.command_completed.connect(self._on_command_completed)
        gateway.command_failed.connect(self._on_command_failed)
        gateway.failure.connect(self._on_failure)
        gateway.disconnected.connect(self._on_disconnected)
        usb_candidate_found = getattr(gateway, "usb_candidate_found", None)
        if usb_candidate_found is not None:
            usb_candidate_found.connect(self._on_usb_handoff_candidate)
        if firmware_release_source is not None:
            firmware_release_source.release_found.connect(
                self._on_remote_firmware_release_found
            )
            firmware_release_source.download_completed.connect(
                self._on_remote_firmware_downloaded
            )
            firmware_release_source.download_progress.connect(
                self._on_remote_firmware_download_progress
            )
            firmware_release_source.failed.connect(self._on_remote_firmware_failed)

    @property
    def model(self) -> ScreenModel:
        return self._model

    @property
    def page(self) -> str:
        return self._page

    @property
    def draft(self) -> LocalDraft | None:
        return self._draft

    def _sync_normal_agent_focus(self) -> None:
        session = self.normal_agent
        self.codex_agent_focus.set_normal_behavior(
            session.value if session.connected and session.loaded and not session.busy
            and not session.uncertain else None
        )

    def normal_agent_write_block_reason(self) -> str:
        snapshot = self._model.snapshot
        if (snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}
                or not snapshot.trust.is_authenticated):
            return "请连接并认证设备后操作"
        if snapshot.compatibility.get("write") is not True:
            return "设备当前只读，不能应用设置"
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._write_transaction.state is ConfigTransactionState.UNKNOWN
                or self._config_refresh is not None or self.screen_icon.busy or self.screen_glyphs.busy
                or self._prompt_device.status.is_busy or self.ble_name.busy or self.normal_agent.busy
                or snapshot.status.get("pending") is not None):
            return "设备维护或写入尚未结束，请稍后应用"
        return ""

    def ble_name_write_block_reason(self) -> str:
        snapshot = self._model.snapshot
        if (snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}
                or not snapshot.trust.is_authenticated):
            return "请连接并认证设备后读取名称"
        if snapshot.compatibility.get("write") is not True:
            return "设备当前只读，不能保存蓝牙名称"
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._write_transaction.state is ConfigTransactionState.UNKNOWN
                or self._config_refresh is not None or self.screen_icon.busy or self.screen_glyphs.busy
                or self._prompt_device.status.is_busy or self.normal_agent.busy or snapshot.status.get("pending") is not None):
            return "设备维护或写入尚未结束，请稍后修改蓝牙名称"
        return ""

    def _ensure_device_preferences_idle(self) -> None:
        if self.normal_agent.busy or self.normal_agent.uncertain:
            raise ValueError("状态灯按键设置正在保存或读回，请稍后操作")
        if self.ble_name.busy:
            raise ValueError("蓝牙名称正在保存或读回，请稍后操作")

    def pending_dirty_workspaces(self) -> tuple[LocalDraft, ...]:
        """All in-memory device drafts that would be lost on process exit."""
        drafts = list(self._drafts.values())
        if self._draft is not None and self._draft not in drafts:
            drafts.append(self._draft)
        return tuple(draft for draft in drafts if draft.is_dirty)

    @property
    def write_transaction(self) -> ConfigTransaction:
        return self._write_transaction

    @property
    def firmware_update(self) -> FirmwareUpdateTransaction:
        return self._firmware_update

    @property
    def remote_firmware(self) -> RemoteFirmwareCheck:
        return self._remote_firmware

    @property
    def calibration(self) -> CalibrationTransaction:
        return self._calibration

    @property
    def prompt_library(self) -> PromptLibrary | None:
        return self._prompt_library

    @property
    def prompt_library_error(self) -> str:
        return self._prompt_library_error

    @property
    def prompt_device(self) -> PromptDeviceSession:
        return self._prompt_device

    @property
    def event_bus(self) -> DeviceEventBus:
        return self._event_bus

    @property
    def automation_host(self) -> AutomationHost:
        return self._automation_host

    @property
    def workflow_host(self) -> WorkflowHost:
        return self._workflow_host

    @property
    def extension_platform(self):
        return self._extension_platform

    @property
    def extension_platform_error(self) -> str:
        return self._extension_platform_error

    @property
    def extension_context_revision(self) -> int:
        return self._extension_context_revision

    def extension_context(self) -> ExtensionContext:
        return build_extension_context(
            model=self._model,
            draft=self._draft,
            listener=self._prompt_device.listener_status,
            revision=self._extension_context_revision,
        )

    @property
    def host_actions(self) -> tuple[HostAction, ...]:
        platform = self._extension_platform
        if platform is None:
            extensions = ()
            bindings = ()
            is_ready = lambda _extension_id: False
        else:
            extensions = platform.extensions
            bindings = platform.bindings
            is_ready = platform.extension_is_ready
        return collect_host_actions(
            device_serial=self._automation_host.serial,
            workflows=self._workflow_host.workflows,
            automations=self._automation_host.definitions,
            extensions=extensions,
            extension_bindings=bindings,
            extension_is_ready=is_ready,
            trigger_problem=self.prompt_trigger_problem,
        )

    def prompt_trigger_problem(self, prompt_id: int, device_serial: str | None = None) -> str:
        snapshot = self._model.snapshot
        library = self._prompt_library
        if (snapshot is None or library is None or not self._prompt_device.available
                or (device_serial is not None and library.serial != device_serial)):
            return "请先连接对应设备，再配置实体提示词触发。"
        if snapshot.capabilities.get("features", {}).get("prompt_trigger_usb") is not True:
            return "当前设备未声明 USB 提示词触发能力。"
        if not self._prompt_device.prompt_was_read(prompt_id) or self._prompt_device.status.is_busy:
            return "请先在快捷提示词页读取设备提示词，完成后再启用。"
        entry = library.confirmed_entry(prompt_id)
        if entry is None or not entry.body:
            return f"提示词槽位 {prompt_id} 尚未写入设备；请先在快捷提示词页填写、写入并读回。"
        if prompt_id not in (1, 2, 3, 4) and not any(
            isinstance(mapping, dict)
            and isinstance(mapping.get("action"), dict)
            and mapping["action"].get("type") == "prompt"
            and mapping["action"].get("prompt_id") == prompt_id
            for mapping in snapshot.mappings.values()
        ):
            return f"提示词槽位 {prompt_id} 不在四向提示词盘中；请先在设备按键配置中设置并写入触发映射。"
        return ""

    @property
    def prompt_trigger_choices(self) -> tuple[int, ...]:
        return (1, 2, 3, 4) + tuple(
            prompt_id for prompt_id in range(5, PROMPT_SLOT_COUNT + 1)
            if not self.prompt_trigger_problem(prompt_id)
        )

    def attach_extension_platform(self, platform) -> None:
        if self._extension_platform is not None and self._extension_platform is not platform:
            raise ValueError("扩展平台已经附加到当前控制台")
        self._extension_platform = platform
        self._start_extension_platform_for_current_session()

    @property
    def action_definitions(self) -> tuple[ActionDefinition, ...]:
        if self._draft is None or self._contract is None:
            return ()
        return action_definitions(self._contract, self._draft.actions)

    @property
    def diagnostics(self) -> DiagnosticsSession:
        return self._diagnostics

    @property
    def diagnostic_capture_pending(self) -> str:
        return self._diagnostic_capture_pending

    @property
    def diagnostic_capture_error(self) -> str:
        return self._diagnostic_capture_error

    @property
    def diagnostic_capture_supported(self) -> bool:
        snapshot = self._model.snapshot
        if snapshot is None:
            return False
        features = (
            snapshot.capabilities.get("features")
            if isinstance(snapshot, DeviceSnapshot)
            else None
        )
        return isinstance(features, dict) and features.get("diagnostic_capture") is True

    @property
    def diagnostic_capture_active(self) -> bool:
        snapshot = self._model.snapshot
        return (
            snapshot is not None
            and diagnostic_capture(snapshot.status).get("active") is True
        )

    @property
    def lighting_preview(self) -> LightingPreviewStatus:
        return self._lighting_preview

    @property
    def lighting_preview_supported(self) -> bool:
        snapshot = self._model.snapshot
        if snapshot is None:
            return False
        features = (
            snapshot.capabilities.get("features")
            if isinstance(snapshot, DeviceSnapshot)
            else None
        )
        return isinstance(features, dict) and features.get("lighting_preview") is True

    def set_lighting_preview_enabled(
        self,
        enabled: bool,
        lighting: dict | None = None,
    ) -> None:
        if not enabled:
            self.stop_lighting_preview()
            return
        if self._page != "lighting":
            raise ValueError("只能在外观与反馈页开启实时预览")
        if self._model.state is not AppState.READY:
            raise ValueError("设备当前不可开启实时预览")
        if not self.lighting_preview_supported:
            raise ValueError("当前固件不支持实时预览")
        if lighting is None:
            raise ValueError("开启预览时缺少灯光快照")
        candidate = self._build_lighting_preview_candidate(lighting)
        self._set_lighting_preview(
            replace(
                self._lighting_preview,
                supported=True,
                requested=True,
                active=False,
                pending=True,
                message="正在发送临时灯光预览…",
                technical="",
                candidate=candidate,
            )
        )
        self._lighting_preview_debounce.start()

    def update_lighting_preview(self, lighting: dict) -> None:
        candidate = self._build_lighting_preview_candidate(lighting)
        self._set_lighting_preview(
            replace(
                self._lighting_preview,
                candidate=candidate,
                pending=self._lighting_preview.requested,
                message=(
                    "正在更新设备预览…"
                    if self._lighting_preview.requested
                    else self._lighting_preview.message
                ),
                technical="" if self._lighting_preview.requested else self._lighting_preview.technical,
            )
        )
        if self._lighting_preview.requested:
            self._lighting_preview_debounce.start()

    def stop_lighting_preview(
        self,
        *,
        clear_candidate: bool = False,
        send_clear: bool = True,
    ) -> None:
        self._lighting_preview_debounce.stop()
        self._lighting_preview_heartbeat.stop()
        candidate = None if clear_candidate else self._lighting_preview.candidate
        should_clear = (
            send_clear
            and self._lighting_preview_sent
            and not self._lighting_preview_clear_pending
            and self.lighting_preview_supported
            and self._model.state in {AppState.READY, AppState.READ_ONLY}
        )
        self._set_lighting_preview(
            replace(
                self._lighting_preview,
                requested=False,
                active=False,
                pending=should_clear,
                message=(
                    "正在恢复设备已保存的灯光…"
                    if should_clear
                    else (
                        "实时预览已关闭"
                        if self._lighting_preview.supported
                        else "当前固件不支持实时预览"
                    )
                ),
                technical="",
                candidate=candidate,
            )
        )
        if should_clear:
            self._lighting_preview_clear_pending = True
            self._gateway.execute_command(clear_lighting_preview_command())

    def _build_lighting_preview_candidate(self, lighting: dict) -> dict:
        snapshot = self._model.snapshot
        features = snapshot.capabilities.get("features") if snapshot is not None else None
        count = features.get("under_key_rgb_count") if isinstance(features, dict) else None
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError("设备没有返回有效的 under_key_rgb_count")
        try:
            return build_lighting_preview_snapshot(
                lighting,
                under_key_rgb_count=count,
            )
        except LightingPreviewError as exc:
            raise ValueError(str(exc)) from exc

    def _send_lighting_preview(self) -> None:
        preview = self._lighting_preview
        if (
            not preview.requested
            or preview.candidate is None
            or self._page != "lighting"
            or self._model.state is not AppState.READY
            or not self.lighting_preview_supported
        ):
            self._lighting_preview_heartbeat.stop()
            return
        self._lighting_preview_sent = True
        if preview.pending or not preview.active:
            self._set_lighting_preview(
                replace(
                    preview,
                    pending=True,
                    message="正在更新设备预览…",
                    technical="",
                )
            )
        self._gateway.execute_command(
            set_lighting_preview_command(preview.candidate)
        )

    def _set_lighting_preview(self, status: LightingPreviewStatus) -> None:
        if status == self._lighting_preview:
            return
        self._lighting_preview = status
        self.lighting_preview_changed.emit(status)

    def _reset_lighting_preview(
        self,
        *,
        supported: bool,
        message: str,
    ) -> None:
        self._lighting_preview_debounce.stop()
        self._lighting_preview_heartbeat.stop()
        self._lighting_preview_sent = False
        self._lighting_preview_clear_pending = False
        self._set_lighting_preview(
            LightingPreviewStatus(supported=supported, message=message)
        )

    def navigate(self, page: str) -> None:
        if page not in {
            "overview",
            "sequences",
            "prompts",
            "actions",
            "lighting",
            "settings",
            "joystick",
            "diagnostics",
            "firmware",
        }:
            raise ValueError(f"未知页面 {page}")
        if self._firmware_update.blocks_editing and page != "firmware":
            raise ValueError("固件维护进行中，请先完成或中止当前事务")
        if self._calibration.blocks_editing and page != "joystick":
            raise ValueError("摇杆校准进行中，请先完成或取消校准")
        if page in {"sequences", "lighting"} and self._draft is None:
            return
        previous_page = self._page
        if previous_page == "diagnostics" and page != "diagnostics":
            self.stop_diagnostic_capture()
        if previous_page == "lighting" and page != "lighting":
            self.stop_lighting_preview(clear_candidate=True)
        self._page = page
        if page == "diagnostics" and previous_page != "diagnostics":
            self._gateway.set_status_poll_interval(250)
        elif previous_page == "diagnostics" and page != "diagnostics":
            self._gateway.set_status_poll_interval(500)
        self.changed.emit(self._model)

    def clear_diagnostic_events(self) -> None:
        self._diagnostics.clear()
        self.diagnostics_changed.emit()

    def start_diagnostic_capture(self) -> None:
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state not in {
            AppState.READY,
            AppState.READ_ONLY,
        }:
            raise ValueError("请先连接并读取设备")
        if not self.diagnostic_capture_supported:
            raise ValueError("当前固件尚未提供安全的诊断输入接管能力")
        if snapshot.status.get("inputs_neutral") is not True:
            raise ValueError("请先松开设备上的全部控件")
        action_engine = snapshot.status.get("action_engine")
        if isinstance(action_engine, dict) and (
            action_engine.get("host_output_state") != "enabled"
            or action_engine.get("local_page") != "none"
        ):
            raise ValueError("请先退出设备本地页面，等待主机输出恢复")
        self._diagnostics.reset_control_check()
        self._diagnostic_capture_pending = "starting"
        self._diagnostic_capture_error = ""
        self._diagnostic_capture_heartbeat.stop()
        self.diagnostics_changed.emit()
        self._gateway.execute_command(Command("DIAGNOSTIC_START", 0x24, {}))

    def stop_diagnostic_capture(self) -> None:
        self._diagnostic_capture_heartbeat.stop()
        if not self.diagnostic_capture_supported:
            return
        if (
            not self.diagnostic_capture_active
            and self._diagnostic_capture_pending != "starting"
        ):
            return
        self._diagnostic_capture_pending = "stopping"
        self.diagnostics_changed.emit()
        self._gateway.execute_command(Command("DIAGNOSTIC_STOP", 0x25, {}))

    def _renew_diagnostic_capture(self) -> None:
        if self._page != "diagnostics" or not self.diagnostic_capture_active:
            self._diagnostic_capture_heartbeat.stop()
            return
        self._gateway.execute_command(Command("DIAGNOSTIC_START", 0x24, {}))

    def diagnostic_report_text(self) -> str:
        return self._diagnostics.report_text(self._model)

    def export_diagnostic_summary(self, path: Path) -> None:
        if self._model.snapshot is None:
            raise ValueError("当前没有可导出的设备诊断信息")
        self._diagnostics.export(path, self._model)

    def save_prompt_draft(self, prompt_id: int, name: str, body: str) -> PromptEntry:
        if self._prompt_device.status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成，不能同时修改本地草稿")
        library = self._require_prompt_library()
        candidate = copy.deepcopy(library)
        entry = candidate.set_draft(prompt_id, name.strip(), body)
        self._prompt_library_store.save(candidate)
        library.set_draft(prompt_id, entry.name, entry.body)
        self._prompt_library_error = ""
        self.changed.emit(self._model)
        return entry

    def save_and_write_prompt(self, prompt_id: int, name: str, body: str) -> None:
        """Persist the current fields, then start the existing write/readback flow."""
        if self._prompt_device.status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成，不能同时保存到设备")
        library = self._require_prompt_library()
        candidate = copy.deepcopy(library)
        entry = candidate.set_draft(prompt_id, name.strip(), body)
        self._prompt_library_store.save(candidate)
        library.set_draft(prompt_id, entry.name, entry.body)
        self._prompt_library_error = ""
        # write_draft publishes the first operation state. Keeping the local
        # save and device start in one call avoids rebuilding the editor between
        # the two halves of the user's single action.
        self._prompt_device.write_draft(prompt_id)

    def delete_prompt_draft(self, prompt_id: int) -> None:
        if self._prompt_device.status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成，不能同时修改本地草稿")
        library = self._require_prompt_library()
        candidate = copy.deepcopy(library)
        candidate.delete_draft(prompt_id)
        self._prompt_library_store.save(candidate)
        library.delete_draft(prompt_id)
        self._prompt_library_error = ""
        self.changed.emit(self._model)

    def discard_prompt_draft(self, prompt_id: int) -> None:
        if self._prompt_device.status.is_busy:
            raise PromptLibraryError("提示词设备操作尚未完成，不能同时修改本地草稿")
        library = self._require_prompt_library()
        candidate = copy.deepcopy(library)
        candidate.discard_draft(prompt_id)
        self._prompt_library_store.save(candidate)
        library.discard_draft(prompt_id)
        self._prompt_library_error = ""
        self.changed.emit(self._model)

    def refresh_prompt_library(self) -> None:
        self._ensure_prompt_device_operation_allowed()
        self._prompt_device.refresh()

    def write_prompt_draft(self, prompt_id: int) -> None:
        self._ensure_prompt_device_operation_allowed()
        self._prompt_device.write_draft(prompt_id)

    def delete_prompt_from_device(self, prompt_id: int) -> None:
        self._ensure_prompt_device_operation_allowed()
        self._prompt_device.delete_confirmed(prompt_id)

    def _busy_task_event(self, event):
        if self.host_tasks.running:
            from controller_config.automation import EventDispatchResult
            return EventDispatchResult(True, False, "当前电脑任务正在运行，不会重复执行")
        return None

    def save_automation(
        self,
        *,
        automation_id: str | None,
        name: str,
        trigger_prompt_id: int,
        script_path: str,
        enabled: bool,
        timeout_ms: int = 60_000,
    ) -> LocalScriptAutomation:
        if enabled and trigger_prompt_id is not None and (problem := self.prompt_trigger_problem(trigger_prompt_id)):
            raise AutomationError(problem)
        if enabled and trigger_prompt_id is not None and any(
            workflow.enabled and workflow.trigger_prompt_id == trigger_prompt_id
            for workflow in self._workflow_host.workflows
        ):
            raise AutomationError(
                "这个提示词槽位已绑定内置自动化，请先停用对应自动化"
            )
        if enabled and self._extension_platform is not None:
            serial = self._automation_host.serial
            if any(
                binding.device_serial == serial
                and binding.prompt_id == trigger_prompt_id
                for binding in self._extension_platform.bindings
            ):
                raise AutomationError(
                    "这个提示词槽位已绑定扩展，请先解除扩展绑定"
                )
        return self._automation_host.save_definition(
            automation_id=automation_id,
            name=name,
            trigger_prompt_id=trigger_prompt_id,
            script_path=script_path,
            enabled=enabled,
            timeout_ms=timeout_ms,
        )

    def delete_automation(self, automation_id: str) -> None:
        self.host_tasks.ensure_not_bound("script", automation_id)
        self._automation_host.delete_definition(automation_id)

    def run_automation_test(self, automation_id: str) -> None:
        self._automation_host.run_test(automation_id)

    def save_workflow(self, workflow: LocalWorkflow) -> LocalWorkflow:
        if workflow.enabled and workflow.trigger_prompt_id is not None:
            if problem := self.prompt_trigger_problem(workflow.trigger_prompt_id):
                raise WorkflowError(problem)
            if any(
                definition.enabled
                and definition.trigger_prompt_id == workflow.trigger_prompt_id
                for definition in self._automation_host.definitions
            ):
                raise WorkflowError(
                    "这个提示词槽位已绑定本地脚本，请先停用对应脚本"
                )
            if self._extension_platform is not None and any(
                binding.device_serial == self._workflow_host.serial
                and binding.prompt_id == workflow.trigger_prompt_id
                for binding in self._extension_platform.bindings
            ):
                raise WorkflowError(
                    "这个提示词槽位已绑定扩展，请先解除扩展绑定"
                )
        return self._workflow_host.save_workflow(workflow)

    def delete_workflow(self, workflow_id: str) -> None:
        self.host_tasks.ensure_not_bound("workflow", workflow_id)
        self._workflow_host.delete_workflow(workflow_id)

    def duplicate_workflow(self, workflow_id: str) -> LocalWorkflow:
        return self._workflow_host.duplicate_workflow(workflow_id)

    def test_workflow(self, workflow_id: str, completed=None) -> None:
        self._workflow_host.test_workflow(workflow_id, completed=completed)

    def _ensure_icon_idle(self) -> None:
        if self.screen_icon.busy or self.screen_glyphs.busy:
            raise ValueError("圆屏图标正在传输或读回，请先等待完成或取消")

    def _ensure_icon_operation_allowed(self) -> None:
        if self._model.state is not AppState.READY:
            raise ValueError("请先连接可配置的 BORING 设备")
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION
                or self._write_transaction.state is ConfigTransactionState.UNKNOWN
                or self._prompt_device.status.is_busy):
            raise ValueError("设备维护或写入尚未结束，请稍后操作圆屏图标")

    def upload_screen_icon(self, pixels: bytes) -> None:
        self._ensure_device_preferences_idle()
        self._ensure_icon_idle()
        self._ensure_icon_operation_allowed()
        self.stop_lighting_preview()
        self.screen_icon.upload(pixels)

    def reset_screen_icon(self) -> None:
        self._ensure_device_preferences_idle()
        self._ensure_icon_idle()
        self._ensure_icon_operation_allowed()
        self.stop_lighting_preview()
        self.screen_icon.reset()

    def refresh_screen_icon(self) -> None:
        if self.screen_glyphs.busy:
            return
        if self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            return
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION):
            return
        self.screen_icon.refresh()

    def refresh_screen_glyphs(self, icon_id: str | None = None) -> None:
        if self._model.state is not AppState.READY or self.screen_icon.busy:
            return
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION):
            return
        if not self.screen_glyphs.catalog:
            self.screen_glyphs.load_catalog()
        elif icon_id is not None:
            self.screen_glyphs.select(icon_id)
        else:
            self.screen_glyphs.refresh()

    def write_screen_glyph(self, pixels: bytes | None) -> None:
        self._ensure_device_preferences_idle()
        self._ensure_icon_idle()
        self._ensure_icon_operation_allowed()
        self.stop_lighting_preview()
        if pixels is None:
            self.screen_glyphs.reset()
        else:
            self.screen_glyphs.write(pixels)

    def _ensure_prompt_device_operation_allowed(self) -> None:
        if self.normal_agent.busy:
            raise PromptLibraryError("状态灯按键设置正在保存或读回，请稍后操作")
        if self.ble_name.busy:
            raise PromptLibraryError("蓝牙名称正在保存或读回，请稍后操作")
        if self.screen_icon.busy or self.screen_glyphs.busy:
            raise PromptLibraryError("圆屏图标正在传输或读回，请先等待完成或取消")
        if self._write_transaction.is_busy:
            raise PromptLibraryError("配置写入进行中，不能同时操作提示词存储")
        if self._calibration.blocks_editing:
            raise PromptLibraryError("摇杆校准进行中，不能同时操作提示词存储")
        if self._firmware_update.blocks_editing:
            raise PromptLibraryError("固件维护进行中，不能同时操作提示词存储")

    def _require_prompt_library(self) -> PromptLibrary:
        if self._prompt_library is None:
            raise PromptLibraryError("请先连接并读取一台 BORING 设备")
        return self._prompt_library

    def rename_profile(self, profile_id: int | None, name: str) -> None:
        self._before_draft_change()
        self._require_draft().rename_profile(profile_id, name)
        self._notify_draft_changed()

    def create_profile(self) -> int:
        self._before_draft_change()
        profile_id = self._require_draft().create_profile()
        self._notify_draft_changed()
        return profile_id

    def create_profile_from_template(self, name: str, mappings: list[dict]) -> int:
        self._before_draft_change()
        profile_id = self._require_draft().create_profile_from_template(name, mappings)
        self._notify_draft_changed()
        return profile_id

    def copy_profile(self, profile_id: int | None) -> int:
        self._before_draft_change()
        copied_id = self._require_draft().copy_profile(profile_id)
        self._notify_draft_changed()
        return copied_id

    def save_profile_as(self, profile_id: int | None, name: str) -> int:
        self._before_draft_change()
        saved_id = self._require_draft().save_profile_as(profile_id, name)
        self._notify_draft_changed()
        return saved_id

    def delete_profile(self, profile_id: int | None) -> None:
        self._before_draft_change()
        self._require_draft().delete_profile(profile_id)
        self._notify_draft_changed()

    def set_active_profile(self, profile_id: int) -> None:
        self._before_draft_change()
        self._require_draft().set_active_profile(profile_id)
        self._notify_draft_changed()

    def preview_common_ai(self, selected, current, voice, saved, *, platform):
        from controller_config.ai_setup import prepare_ai_profiles

        draft = self._require_draft()
        config, entries = prepare_ai_profiles(
            draft, selected, current, voice, saved, platform=platform,
        )
        candidate = copy.copy(draft)
        candidate.config = config
        errors = candidate.validate(self._contract)
        if errors:
            raise ValueError(errors[0])
        return config, entries

    def prepare_common_ai(self, selected, current, voice, saved, *, platform):
        draft = self._require_draft()
        if draft.is_dirty:
            raise ValueError("请先应用或保留现有修改，再设置常用 AI。")
        config, entries = self.preview_common_ai(selected, current, voice, saved, platform=platform)
        self._before_draft_change()
        draft.config = config
        self._notify_draft_changed()
        return entries

    def create_macro(self) -> int:
        self._before_draft_change()
        macro_id = self._require_draft().create_macro()
        self._notify_draft_changed()
        return macro_id

    def update_macro(self, macro_id: int, name: str, steps: list[dict]) -> None:
        self._before_draft_change()
        self._require_draft().update_macro(macro_id, name, steps)
        self._notify_draft_changed()

    def delete_macro(self, macro_id: int) -> None:
        self._before_draft_change()
        self._require_draft().delete_macro(macro_id)
        self._notify_draft_changed()

    def set_preferences(self, *, lighting: dict, haptic: dict, display: dict) -> None:
        self._before_draft_change()
        self._require_draft().set_preferences(
            lighting=lighting,
            haptic=haptic,
            display=display,
        )
        self._notify_draft_changed()

    def export_configuration(self, path: Path, *, kind: str, draft: LocalDraft | None = None) -> None:
        current_workspace = draft is None
        draft = draft if draft is not None else self._require_draft()
        config = draft.config if kind == "draft" else draft.confirmed_config
        transaction = self._write_transaction
        unresolved_write = None
        if current_workspace and kind == "draft" and transaction.state is ConfigTransactionState.UNKNOWN:
            config = transaction.candidate_config
            unresolved_write = {
                "device_serial": transaction.device_serial,
                "state": transaction.state.value,
                "message": transaction.message,
                "technical": transaction.technical,
                "base_generation": transaction.base_generation,
                "base_digest": transaction.base_digest,
                "candidate_digest": transaction.candidate_digest,
                "recovery": "Reconnect the original device and read its configuration before deciding whether to apply this draft. Do not automatically retry SET_CONFIG.",
            }
        export_config_package(
            path,
            config=config,
            hardware_ids=(config["hardware_id"],),
            kind=kind,
            base_generation=transaction.base_generation if unresolved_write else draft.base_generation,
            base_digest=transaction.base_digest if unresolved_write else draft.base_digest,
            unresolved_write=unresolved_write,
        )

    def read_configuration_file(self, path: Path) -> ConfigPackage:
        draft = self._require_draft()
        if self._contract is None:
            raise ValueError("共享配置 Schema 不可用")
        package = load_config_package(
            path,
            self._contract,
            current_hardware_id=draft.hardware_id,
        )
        errors = draft.validate_candidate(package.config, self._contract)
        if errors:
            raise ValueError(errors[0])
        return package

    def apply_configuration_import(self, package: ConfigPackage) -> None:
        self._before_draft_change()
        if self._contract is None:
            raise ValueError("共享配置 Schema 不可用")
        self._require_draft().replace_config(package.config, self._contract)
        self._notify_draft_changed()

    def set_codex_voice(self, profile_id: int | None, action: dict) -> None:
        self._before_draft_change()
        self._require_draft().set_codex_voice(profile_id, action)
        self._notify_draft_changed()

    def set_mapping(
        self,
        profile_id: int | None,
        control_id: str,
        short_name: str,
        action: dict,
    ) -> None:
        self._before_draft_change()
        self._require_draft().set_mapping(profile_id, control_id, short_name, action)
        self._notify_draft_changed()

    def save_profile_mapping(
        self,
        profile_id: int,
        profile_name: str,
        control_id: str,
        short_name: str,
        action: dict,
    ) -> None:
        self._before_draft_change()
        draft = self._require_draft()
        draft.rename_profile(profile_id, profile_name)
        draft.set_mapping(profile_id, control_id, short_name, action)
        self._notify_draft_changed()

    def discard_draft(self) -> None:
        if self._draft is not None:
            self.stop_lighting_preview(clear_candidate=True)
            self._before_draft_change()
            self._draft.discard()
            self._notify_draft_changed()

    def select_ble_slot(self, slot: int) -> None:
        self._execute_ble_slot_command("BLE_SLOT_SELECT", 0x15, slot)

    def clear_ble_slot(self, slot: int) -> None:
        self._execute_ble_slot_command("BLE_SLOT_CLEAR", 0x16, slot)

    def factory_reset_device(self) -> None:
        self._ensure_device_preferences_idle()
        self._ensure_icon_idle()
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state not in {
            AppState.READY,
            AppState.READ_ONLY,
        }:
            raise ValueError("设备当前不可恢复出厂设置")
        if (
            self._model.state is AppState.READ_ONLY
            or snapshot.compatibility.get("write") is not True
        ):
            raise ValueError("设备当前只读，不允许恢复出厂设置")
        if self._firmware_update.blocks_editing:
            raise ValueError("固件维护进行中，不能恢复出厂设置")
        if self._calibration.blocks_editing:
            raise ValueError("摇杆校准进行中，不能恢复出厂设置")
        if self._write_transaction.blocks_editing:
            raise ValueError("配置写入或对账进行中，不能恢复出厂设置")
        if self._prompt_device.status.is_busy:
            raise ValueError("提示词设备操作进行中，不能恢复出厂设置")
        generation = snapshot.config_result.get("generation")
        if not isinstance(generation, int):
            raise ValueError("设备没有返回有效的配置 generation")
        self._gateway.execute_command(
            Command(
                "FACTORY_DEFAULT",
                0x30,
                {
                    "base_generation": generation,
                    "confirmation": "FACTORY_DEFAULT",
                },
                timeout_ms=8000,
            )
        )

    def _execute_ble_slot_command(
        self, name: str, message_type: int, slot: int
    ) -> None:
        if slot not in {1, 2, 3}:
            raise ValueError("蓝牙槽位必须是 1、2 或 3")
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state is not AppState.READY:
            raise ValueError("设备当前不可修改蓝牙槽位")
        features = snapshot.capabilities.get("features")
        if not isinstance(features, dict) or features.get("ble_host_slots") != 3:
            raise ValueError("当前固件不支持三槽蓝牙管理")
        if (self._firmware_update.blocks_editing or self._calibration.blocks_editing
                or self._write_transaction.blocks_editing
                or self._prompt_device.status.is_busy or self.screen_icon.busy
                or self.screen_glyphs.busy or self.ble_name.busy or self.normal_agent.busy):
            raise ValueError("设备正在保存或维护，请完成后再切换蓝牙连接")
        self._gateway.execute_command(Command(name, message_type, {"slot": slot}))

    def validate_draft(self) -> tuple[str, ...]:
        if self._draft is None:
            return ("当前没有可编辑草稿",)
        if self._contract is None:
            return ("共享配置 Schema 不可用",)
        return self._draft.validate(self._contract)

    def prepare_device_write(self, *, confirm_after_validation: bool = False) -> None:
        self._confirm_write_after_validation = False
        self._ensure_device_preferences_idle()
        if self._config_refresh is not None:
            raise ValueError("正在读取设备最新配置，请稍后再操作")
        self._ensure_icon_idle()
        if self._firmware_update.blocks_editing:
            raise ValueError("固件维护进行中，不能同时写入配置")
        if self._calibration.blocks_editing:
            raise ValueError("摇杆校准进行中，不能同时写入配置")
        if self._prompt_device.status.is_busy:
            raise ValueError("提示词设备操作进行中，不能同时写入配置")
        if self._write_transaction.state in {
            ConfigTransactionState.VALIDATING,
            ConfigTransactionState.AWAITING_CONFIRMATION,
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
            ConfigTransactionState.UNKNOWN,
        }:
            raise ValueError("当前写入事务尚未结束")
        snapshot = self._model.snapshot
        draft = self._require_draft()
        if snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            raise ValueError("设备当前不可写入")
        if snapshot.compatibility.get("write") is not True:
            raise ValueError("设备当前只读，不允许写入配置")
        draft.platform = str(snapshot.status.get("platform", ""))
        if not draft.is_dirty:
            raise ValueError("本地草稿没有需要写入的修改")
        errors = self.validate_draft()
        if errors:
            raise ValueError(errors[0])
        if snapshot.capabilities.get("features", {}).get("haptic_channels") is not True:
            if any(field in draft.config["haptic"] for field in HAPTIC_OPTIONAL_CHANNELS):
                raise ValueError(
                    "配置包含分类振动开关，但当前固件不支持。请升级固件或导入兼容配置；本地草稿已保留，未写入设备。"
                )
        current_generation = snapshot.config_result.get("generation")
        if current_generation != draft.base_generation:
            self._set_write_transaction(
                ConfigTransaction(
                    ConfigTransactionState.CONFLICT,
                    "设备配置已变化，不能用旧草稿覆盖",
                    f"base_generation={draft.base_generation}, current_generation={current_generation}",
                )
            )
            return
        candidate = copy.deepcopy(draft.config)
        digest = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
        self.stop_lighting_preview(clear_candidate=True)
        self._set_write_transaction(
            ConfigTransaction(
                ConfigTransactionState.VALIDATING,
                "正在让设备验证候选配置",
                candidate_digest=digest,
                base_generation=draft.base_generation,
                base_digest=draft.base_digest,
                candidate_config=candidate,
                device_serial=draft.serial,
            )
        )
        self._confirm_write_after_validation = confirm_after_validation
        self._gateway.execute_command(
            Command("VALIDATE_CONFIG", 0x11, {"config": candidate, "digest": digest})
        )

    def confirm_device_write(self) -> None:
        self._ensure_device_preferences_idle()
        if self._config_refresh is not None:
            raise ValueError("正在读取设备最新配置，请稍后再操作")
        self._ensure_icon_idle()
        transaction = self._write_transaction
        if transaction.state is not ConfigTransactionState.AWAITING_CONFIRMATION:
            raise ValueError("当前没有等待确认的设备写入")
        if self._model.state is not AppState.READY:
            raise ValueError("请先连接并读取设备，再确认写入")
        snapshot = self._model.snapshot
        draft = self._require_draft()
        current_digest = hashlib.sha256(canonical_json_bytes(draft.config)).hexdigest()
        current_generation = snapshot.config_result.get("generation") if snapshot else None
        if current_digest != transaction.candidate_digest:
            self._set_write_transaction(
                ConfigTransaction(
                    ConfigTransactionState.FAILED,
                    "草稿在设备验证后发生了变化，请重新验证",
                )
            )
            return
        if current_generation != transaction.base_generation:
            self._set_write_transaction(
                ConfigTransaction(
                    ConfigTransactionState.CONFLICT,
                    "确认写入前设备配置已经变化",
                    candidate_digest=transaction.candidate_digest,
                    base_generation=transaction.base_generation,
                    base_digest=transaction.base_digest,
                    candidate_config=transaction.candidate_config,
                )
            )
            return
        self._set_write_transaction(replace(transaction, state=ConfigTransactionState.WRITING, message="正在写入设备候选槽"))
        self._gateway.set_status_poll_interval(250)
        self._write_deadline.start()
        self._gateway.execute_command(
            Command(
                "SET_CONFIG",
                0x12,
                {
                    "config": copy.deepcopy(transaction.candidate_config),
                    "digest": transaction.candidate_digest,
                    "base_generation": transaction.base_generation,
                },
            )
        )

    def cancel_device_write_confirmation(self) -> None:
        if self._write_transaction.state is ConfigTransactionState.AWAITING_CONFIRMATION:
            self._set_write_transaction(ConfigTransaction())

    def reconcile_device_write(self) -> None:
        if self._config_refresh is not None:
            raise ValueError("正在读取设备最新配置，请稍后再操作")
        if not self._write_transaction.candidate_digest:
            raise ValueError("当前没有可对账的写入事务")
        if self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            self.refresh()
            return
        self._request_config_readback("正在重新读取设备配置进行对账")

    def refresh_conflicted_device(self) -> None:
        if self._write_transaction.state is not ConfigTransactionState.CONFLICT:
            raise ValueError("当前没有需要处理的配置冲突")
        self.refresh()

    def discard_conflicted_draft(self) -> None:
        snapshot, draft = self._conflict_snapshot_and_draft()
        if self._contract is None:
            raise ValueError("共享配置 Schema 不可用")
        candidate = LocalDraft.from_snapshot(snapshot, self._contract)
        if self._drafts.get(draft.key) is draft:
            self._drafts.pop(draft.key)
        self._draft = candidate
        self._drafts[candidate.key] = candidate
        self._set_write_transaction(ConfigTransaction())

    def rebase_conflicted_draft(self) -> None:
        snapshot, draft = self._conflict_snapshot_and_draft()
        if self._contract is None:
            raise ValueError("共享配置 Schema 不可用")
        old_key = draft.key
        draft.rebase_to_snapshot(snapshot, self._contract)
        if self._drafts.get(old_key) is draft:
            self._drafts.pop(old_key)
        self._drafts[draft.key] = draft
        self._set_write_transaction(ConfigTransaction())

    def _require_draft(self) -> LocalDraft:
        if self._draft is None:
            raise ValueError("当前没有可编辑草稿")
        return self._draft

    def _sync_draft_platform(self, snapshot: DeviceSnapshot) -> None:
        draft = self._draft
        if (
            draft is None
            or draft.serial != str(snapshot.identity.get("serial", ""))
            or draft.hardware_id != str(snapshot.identity.get("hardware_id", ""))
        ):
            return
        draft.platform = str(snapshot.status.get("platform", ""))

    def start_joystick_calibration(self) -> None:
        self._ensure_device_preferences_idle()
        if self._config_refresh is not None:
            raise ValueError("正在读取设备最新配置，请稍后再操作")
        self._ensure_icon_idle()
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state is not AppState.READY:
            raise ValueError("设备当前不可执行摇杆校准")
        features = snapshot.capabilities.get("features")
        if not isinstance(features, dict) or features.get("joystick_calibration") is not True:
            raise ValueError("当前设备没有声明摇杆校准能力")
        if snapshot.compatibility.get("write") is not True:
            raise ValueError("设备当前只读，不能保存校准")
        if self._firmware_update.blocks_editing:
            raise ValueError("固件维护进行中，不能开始校准")
        if self._prompt_device.status.is_busy:
            raise ValueError("提示词设备操作进行中，不能开始校准")
        if self._write_transaction.blocks_editing or self._write_transaction.state is ConfigTransactionState.UNKNOWN:
            raise ValueError("配置写入事务尚未结束")
        if self._draft is not None and self._draft.is_dirty:
            raise ValueError("请先写入、导出或丢弃本地草稿，再开始校准")
        generation = snapshot.config_result.get("generation")
        digest = snapshot.config_result.get("digest")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise ValueError("当前配置 generation 无效")
        if not isinstance(digest, str):
            raise ValueError("当前配置 digest 无效")
        self._calibration_sample_timer.stop()
        self._calibration_activation_deadline.stop()
        self._gateway.set_status_poll_interval(60_000)
        self._set_calibration(
            CalibrationTransaction(
                state=CalibrationState.STARTING,
                message="请松开摇杆并保持不动，正在建立校准会话",
                base_generation=generation,
                base_digest=digest,
            )
        )
        self._gateway.execute_command(calibration_start_command())

    def confirm_joystick_calibration(self) -> None:
        transaction = self._calibration
        if not transaction.can_confirm or transaction.base_generation is None:
            raise ValueError("当前校准还不能保存")
        if self._model.state is not AppState.READY:
            raise ValueError("设备当前不可保存校准")
        self._calibration_sample_timer.stop()
        self._set_calibration(
            replace(
                transaction,
                state=CalibrationState.CONFIRMING,
                message="正在让设备检查回中状态并保存校准候选",
                technical="",
            )
        )
        self._gateway.execute_command(
            calibration_confirm_command(
                transaction.session_id,
                transaction.base_generation,
            )
        )

    def cancel_joystick_calibration(self) -> None:
        transaction = self._calibration
        if not transaction.can_cancel:
            raise ValueError("当前没有可取消的校准会话")
        if self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            raise ValueError("设备已断开；设备会自动取消未完成校准")
        waiting_for_sample = transaction.state is CalibrationState.SAMPLING
        self._calibration_sample_timer.stop()
        self._set_calibration(
            replace(
                transaction,
                state=CalibrationState.CANCELLING,
                message=(
                    "等待当前采样返回，然后取消校准"
                    if waiting_for_sample
                    else "正在取消校准并保留原有配置"
                ),
                cancel_requested=True,
            )
        )
        if not waiting_for_sample:
            self._gateway.execute_command(
                calibration_cancel_command(transaction.session_id)
            )

    @property
    def remote_firmware_device(self) -> tuple[str, str, str] | None:
        """The connected device owning this result; offline results are not actionable."""
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state is not AppState.READY:
            return None
        identity = tuple(str(snapshot.identity.get(field, ""))
                         for field in ("serial", "product_id", "hardware_id"))
        return identity if identity == self._remote_firmware_device else None

    @property
    def firmware_origin(self) -> str:
        return self._firmware_release_history.classify(self._model.snapshot)

    @property
    def firmware_origin_notice(self) -> str:
        if self.firmware_origin == "custom":
            return "正在使用自定义固件，无需按官方版本更新。切换到官方固件会替换自定义功能。"
        if self.firmware_origin == "unknown":
            return "无法确认当前固件来源。查看官方版本不代表需要更新。"
        return "设备报告的版本与构建编号匹配官方签名发布记录；不代表对运行代码进行了验真。"

    @property
    def firmware_online_message(self) -> str:
        if (self._model.snapshot is not None and self.firmware_origin != "official"
                and self._remote_firmware.state in {RemoteFirmwareState.IDLE, RemoteFirmwareState.CURRENT}):
            return self.firmware_origin_notice
        return self._remote_firmware.message

    @property
    def can_auto_check_remote_firmware(self) -> bool:
        snapshot = self._model.snapshot
        if (self._firmware_release_source is None or snapshot is None
                or self._model.state is not AppState.READY
                or self.firmware_origin != "official"):
            return False
        features = snapshot.capabilities.get("features")
        if not isinstance(features, dict) or features.get("firmware_update") is not True:
            return False
        return not (
            self._remote_firmware.state in {
                RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING,
                RemoteFirmwareState.DOWNLOADED,
            }
            or self._firmware_update.blocks_editing
            or (self._firmware_update.package is not None
                and self._firmware_update.state is not FirmwareUpdateState.COMPLETED)
            or self._calibration.blocks_editing or self._write_transaction.blocks_editing
            or self._write_transaction.state is ConfigTransactionState.UNKNOWN
            or self._config_refresh is not None or self.screen_icon.busy or self.screen_glyphs.busy
            or self._prompt_device.status.is_busy or self.ble_name.busy or self.normal_agent.busy
            or snapshot.status.get("pending") is not None
        )

    def auto_check_remote_firmware(self) -> bool:
        """Quiet background discovery; manual checks deliberately bypass the interval."""
        if not self.can_auto_check_remote_firmware:
            return False
        snapshot = self._model.snapshot
        identity = tuple(str(snapshot.identity.get(field, ""))
                         for field in ("serial", "product_id", "hardware_id"))
        last_check = self._remote_firmware_last_check.get(identity)
        if last_check is not None and monotonic() - last_check < 6 * 60 * 60:
            return False
        self.check_remote_firmware()
        return True

    def check_remote_firmware(self) -> None:
        source = self._firmware_release_source
        snapshot = self._model.snapshot
        if source is None:
            raise ValueError("在线固件服务尚未配置")
        if self._remote_firmware.state in {
            RemoteFirmwareState.CHECKING,
            RemoteFirmwareState.DOWNLOADING,
        }:
            raise ValueError("在线固件请求正在进行")
        if self._remote_firmware.state is RemoteFirmwareState.DOWNLOADED:
            raise ValueError("新固件已经下载，请先安装或放弃当前维护包")
        if snapshot is None or self._model.state is not AppState.READY:
            raise ValueError("请先连接可写兼容的 BORING 设备")
        if self._firmware_update.is_busy:
            raise ValueError("固件维护事务进行中，不能检查其他发布包")
        if self._calibration.blocks_editing:
            raise ValueError("摇杆校准进行中，不能检查固件更新")
        if self._write_transaction.blocks_editing:
            raise ValueError("配置写入事务尚未结束")
        features = snapshot.capabilities.get("features")
        if not isinstance(features, dict) or features.get("firmware_update") is not True:
            raise ValueError("当前设备没有声明固件更新能力")
        self._remote_firmware_device = tuple(
            str(snapshot.identity.get(field, ""))
            for field in ("serial", "product_id", "hardware_id")
        )
        self._remote_firmware_last_check[self._remote_firmware_device] = monotonic()
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=RemoteFirmwareState.CHECKING,
                message="正在通过 HTTPS 检查当前硬件的发布固件",
                restoration=self.firmware_origin != "official",
            )
        )
        try:
            source.check(snapshot)
        except ValueError as exc:
            self._on_remote_firmware_failed(str(exc))

    def _on_remote_firmware_release_found(self, value: object) -> None:
        if (self._remote_firmware.state is not RemoteFirmwareState.CHECKING
                or self.remote_firmware_device is None):
            return
        snapshot = self._model.snapshot
        if not isinstance(value, RemoteFirmwareRelease) or snapshot is None:
            self._on_remote_firmware_failed("在线固件检查返回了无效结果")
            return
        if any(value.manifest.get(field) != snapshot.identity.get(field)
               for field in ("product_id", "hardware_id")):
            self._on_remote_firmware_failed("设备型号已改变，请重新检查当前设备的在线固件")
            return
        try:
            validate_minimum_app_version(value.manifest)
            self._firmware_release_history.remember(value.manifest)
            restoration = self._remote_firmware.restoration and self.firmware_origin != "official"
            newer = False if restoration else release_is_newer(value, snapshot)
        except (FirmwareReleaseError, FirmwareSignatureError) as exc:
            self._on_remote_firmware_failed(str(exc))
            return
        if restoration:
            self._set_remote_firmware(RemoteFirmwareCheck(
                state=RemoteFirmwareState.RESTORE_AVAILABLE,
                message="这是切换固件，不是发现了新版本。切换会替换当前固件及自定义功能。",
                release=value, restoration=True,
            ))
            return
        if not newer:
            self._set_remote_firmware(
                RemoteFirmwareCheck(
                    state=RemoteFirmwareState.CURRENT,
                    message=("已是最新官方固件" if value.version == snapshot.versions.get("firmware")
                             and value.build_id == snapshot.versions.get("build_id")
                             else "没有比当前版本更新的官方固件"),
                    release=value,
                )
            )
            return
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=RemoteFirmwareState.AVAILABLE,
                message=f"发现发布版本 {value.version}，可由你决定是否下载",
                release=value,
            )
        )

    def download_remote_firmware(self) -> None:
        source = self._firmware_release_source
        snapshot = self._model.snapshot
        release = self._remote_firmware.release
        if source is None:
            raise ValueError("在线固件服务尚未配置")
        if release is None or self._remote_firmware.state not in {
            RemoteFirmwareState.AVAILABLE,
            RemoteFirmwareState.RESTORE_AVAILABLE,
            RemoteFirmwareState.FAILED,
        }:
            raise ValueError("当前没有可下载的在线固件")
        if snapshot is None or self._model.state is not AppState.READY:
            raise ValueError("请先连接可写兼容的 BORING 设备")
        if self._firmware_update.is_busy:
            raise ValueError("固件维护事务进行中，不能下载其他发布包")
        validate_minimum_app_version(release.manifest)
        if self.remote_firmware_device is None:
            raise ValueError("设备已断开或切换，请重新检查官方版本")
        if not self._remote_firmware.restoration and (self.firmware_origin != "official" or not release_is_newer(release, snapshot)):
            raise ValueError("当前设备已不需要此在线更新，请重新检查版本")
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=RemoteFirmwareState.DOWNLOADING,
                message=f"正在下载发布版本 {release.version}",
                release=release,
                total_size=release.size,
                restoration=self._remote_firmware.restoration,
            )
        )
        try:
            source.download(release, snapshot)
        except ValueError as exc:
            self._on_remote_firmware_failed(str(exc))

    def _on_remote_firmware_download_progress(
        self,
        received: int,
        total: int,
    ) -> None:
        state = self._remote_firmware
        if state.state is not RemoteFirmwareState.DOWNLOADING:
            return
        expected = state.release.size if state.release is not None else state.total_size
        total_size = total if total > 0 else expected
        received_size = max(0, received)
        normalized_total = max(expected, total_size)
        next_percent = (
            min(100, int(received_size * 100 / normalized_total))
            if normalized_total > 0
            else 0
        )
        if next_percent == state.progress_percent and received_size < total_size:
            return
        self._set_remote_firmware(
            replace(
                state,
                received_size=received_size,
                total_size=normalized_total,
            )
        )

    def _on_remote_firmware_downloaded(self, value: object) -> None:
        state = self._remote_firmware
        snapshot = self._model.snapshot
        if state.state is not RemoteFirmwareState.DOWNLOADING:
            return
        if not isinstance(value, RemoteFirmwareBundle) or snapshot is None or self._contract is None:
            self._on_remote_firmware_failed("在线固件下载返回了无效结果")
            return
        try:
            package = load_remote_firmware_bundle(value, self._contract)
            package.validate_for_device(snapshot)
        except (FirmwareReleaseError, FirmwarePackageError) as exc:
            self._on_remote_firmware_failed(exc)
            return
        self._set_firmware_update(
            FirmwareUpdateTransaction(
                state=FirmwareUpdateState.PACKAGE_READY,
                message="在线发布包已下载并完成本地检查，尚未向设备发送数据",
                package=package,
            )
        )
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=RemoteFirmwareState.DOWNLOADED,
                message=f"发布版本 {package.version} 已下载并完成校验，可以安装",
                release=state.release,
                received_size=package.size,
                total_size=package.size,
                restoration=state.restoration,
            )
        )

    def _on_remote_firmware_failed(self, error: object) -> None:
        previous = self._remote_firmware
        if previous.state not in {RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING}:
            return
        downloading = previous.state is RemoteFirmwareState.DOWNLOADING
        kind = error.kind if isinstance(error, FirmwareReleaseError) else ""
        unpublished = kind == "unpublished" and not downloading
        if unpublished:
            message = "暂无可用的固件发布\n已连接更新服务，当前渠道尚未提供固件。你可以继续使用设备，稍后再检查。"
        elif kind == "network":
            message = "暂时无法连接更新服务，请检查网络后重试。"
        elif kind == "service":
            message = "更新服务暂时无法提供固件，请稍后重试。"
        elif kind == "image_missing":
            message = "固件下载暂不可用，未安装。请重新检查发布信息。"
        elif isinstance(error, (FirmwareReleaseError, FirmwarePackageError, UnicodeDecodeError, ValueError)):
            message = "固件校验未通过，未安装。" if downloading else "固件发布信息校验未通过，未安装。"
        else:
            message = "在线固件下载失败，未安装。" if downloading else "在线固件检查失败，请稍后重试。"
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=RemoteFirmwareState.UNPUBLISHED if unpublished else RemoteFirmwareState.FAILED,
                message=message,
                technical=str(error),
                release=None if unpublished else previous.release,
                restoration=previous.restoration,
            )
        )

    def load_firmware_package(self, package_path: Path, *, custom: bool = False) -> FirmwarePackage:
        if self._contract is None:
            raise ValueError("共享协议合同不可用")
        if self._remote_firmware.state in {
            RemoteFirmwareState.CHECKING,
            RemoteFirmwareState.DOWNLOADING,
        }:
            raise ValueError("在线固件请求进行中，暂时不能选择本地维护包")
        if self._firmware_update.is_busy:
            raise ValueError("固件维护事务进行中，不能更换软件包")
        package = load_local_firmware_package(package_path, self._contract, custom=custom)
        snapshot = self._model.snapshot
        if snapshot is not None:
            package.validate_for_device(snapshot)
        if package.source_kind == "official":
            self._firmware_release_history.remember(package.manifest)
        self._set_firmware_update(
            FirmwareUpdateTransaction(
                state=FirmwareUpdateState.PACKAGE_READY,
                message="固件包已完成本地检查，尚未向设备发送数据",
                package=package,
            )
        )
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=(
                    RemoteFirmwareState.IDLE
                    if self._firmware_release_source is not None
                    else RemoteFirmwareState.UNCONFIGURED
                ),
                message=(
                    "已选择本地维护包；仍可重新检查在线发布"
                    if self._firmware_release_source is not None
                    else "在线固件服务尚未配置"
                ),
            )
        )
        return package

    def start_firmware_update(self) -> None:
        self._ensure_device_preferences_idle()
        if self._config_refresh is not None:
            raise ValueError("正在读取设备最新配置，请稍后再操作")
        self._ensure_icon_idle()
        if self._remote_firmware.state in {
            RemoteFirmwareState.CHECKING,
            RemoteFirmwareState.DOWNLOADING,
        }:
            raise ValueError("在线固件请求正在进行，请等待完成后再安装")
        transaction = self._firmware_update
        package = transaction.package
        snapshot = self._model.snapshot
        if package is None:
            raise ValueError("请先导入固件维护包")
        if snapshot is None or self._model.state is not AppState.READY:
            raise ValueError("设备当前不可执行固件维护")
        if snapshot.connection_kind == "bluetooth":
            raise ValueError("蓝牙固件更新尚未完成实机验收，请连接 USB 后安装")
        if self._remote_firmware.state is RemoteFirmwareState.DOWNLOADED:
            release = self._remote_firmware.release
            if release is None:
                raise ValueError("在线发布信息已失效，请重新下载")
            validate_minimum_app_version(release.manifest)
            if self.remote_firmware_device is None:
                raise ValueError("设备已断开或切换，请重新检查官方版本")
            if not self._remote_firmware.restoration and (self.firmware_origin != "official" or not release_is_newer(release, snapshot)):
                raise ValueError("设备已不需要此在线更新，请重新检查版本")
        if self._write_transaction.is_busy or self._write_transaction.state is ConfigTransactionState.UNKNOWN:
            raise ValueError("配置写入事务尚未结束")
        if self._calibration.blocks_editing:
            raise ValueError("摇杆校准进行中，不能同时更新固件")
        if self._prompt_device.status.is_busy:
            raise ValueError("提示词设备操作进行中，不能同时更新固件")
        chunk_size = package.validate_for_device(snapshot)
        serial = snapshot.identity.get("serial")
        if not isinstance(serial, str) or not serial:
            raise ValueError("设备没有返回可绑定的设备序列号")
        self._gateway.set_status_poll_interval(60_000)
        self._set_firmware_update(
            replace(
                transaction,
                state=FirmwareUpdateState.CHECKING,
                message="正在读取设备固件维护状态",
                technical="",
                serial=serial,
                chunk_size=chunk_size,
                received_size=0,
                in_flight_size=0,
                target_partition="",
                abort_requested=False,
                restart_failed=True,
            )
        )
        self._gateway.execute_command(firmware_status_command())

    def abort_firmware_update(self) -> None:
        transaction = self._firmware_update
        if not transaction.can_abort:
            raise ValueError("当前固件维护状态不能执行中止")
        if self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            raise ValueError("请先重新连接设备，再中止固件接收事务")
        waiting_for_current_command = transaction.state in {
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
        }
        self._set_firmware_update(
            replace(
                transaction,
                state=FirmwareUpdateState.ABORTING,
                message=(
                    "等待当前请求结束，然后中止设备固件接收事务"
                    if waiting_for_current_command
                    else "正在让设备丢弃未完成的固件接收事务"
                ),
                abort_requested=True,
            )
        )
        if not waiting_for_current_command:
            self._gateway.execute_command(firmware_abort_command())

    @property
    def can_stop_firmware_wait(self) -> bool:
        return (self._firmware_update.state is FirmwareUpdateState.PAUSED
                and self._model.state not in {AppState.READY, AppState.READ_ONLY})

    def stop_firmware_wait(self) -> None:
        if not self.can_stop_firmware_wait:
            raise ValueError("设备已连接或维护状态已改变，请重新确认设备状态")
        self._reconnect_timer.stop()
        self._finish_firmware_update(
            FirmwareUpdateState.FAILED,
            "已停止本地等待；设备端更新未取消，结果尚未确认。重新连接并选择安装后将先读取设备状态。",
        )

    def dismiss_firmware_update(self) -> None:
        transaction = self._firmware_update
        if transaction.state not in {
            FirmwareUpdateState.NEEDS_ABORT,
            FirmwareUpdateState.PACKAGE_READY,
            FirmwareUpdateState.COMPLETED,
            FirmwareUpdateState.FAILED,
        }:
            raise ValueError("当前固件维护事务不能直接关闭")
        self._gateway.set_status_poll_interval(500)
        self._set_firmware_update(FirmwareUpdateTransaction())
        if self._remote_firmware.state in {
            RemoteFirmwareState.AVAILABLE,
            RemoteFirmwareState.DOWNLOADED,
        }:
            release = self._remote_firmware.release
            self._set_remote_firmware(
                RemoteFirmwareCheck(
                    state=(
                        (RemoteFirmwareState.RESTORE_AVAILABLE if self._remote_firmware.restoration
                         else RemoteFirmwareState.AVAILABLE)
                        if release is not None
                        else RemoteFirmwareState.IDLE
                    ),
                    message=(
                        f"发布版本 {release.version} 尚未安装，可重新下载"
                        if release is not None
                        else "可以重新检查面向当前硬件的在线发布固件"
                    ),
                    release=release,
                    restoration=self._remote_firmware.restoration,
                )
            )

    def _conflict_snapshot_and_draft(self) -> tuple[DeviceSnapshot, LocalDraft]:
        if self._write_transaction.state is not ConfigTransactionState.CONFLICT:
            raise ValueError("当前没有需要处理的配置冲突")
        snapshot = self._model.snapshot
        draft = self._require_draft()
        if snapshot is None or snapshot.config_result.get("generation") == draft.base_generation:
            raise ValueError("请先重新读取设备最新配置")
        return snapshot, draft

    def start(self) -> None:
        self.refresh()

    def report_chatgpt_focus_failure(self, message: str) -> None:
        self._diagnostics.record_host_focus_error(message)
        self.diagnostics_changed.emit()

    def refresh(self) -> None:
        self._failed_connection_ports.clear()
        self._cancel_remote_firmware_request()
        self.screen_glyphs.attach(None)
        self.screen_icon.attach(None)
        self.claude_status.unbind(clear=True)
        self._reconnect_timer.stop()
        self._reconnect_timer.setInterval(500)
        self._suspend_extension_platform("正在重新扫描设备")
        self.stop_lighting_preview(clear_candidate=True)
        self._prompt_device.unbind()
        self._set(ScreenModel(AppState.SCANNING, "正在查找已连接到此电脑的 BORING 设备…", snapshot=self._model.snapshot))
        if self._firmware_update.is_busy:
            self._gateway.scan(usb_only=True)
        else:
            self._gateway.scan()

    def connect_candidate(self, port_name: str) -> None:
        self._connection_port = port_name
        self._cancel_remote_firmware_request()
        self.screen_glyphs.attach(None)
        self.screen_icon.attach(None)
        self.claude_status.unbind(clear=True)
        self._reconnect_timer.stop()
        self._reconnect_timer.setInterval(500)
        self._set(ScreenModel(AppState.CONNECTING, "正在确认设备身份并读取配置…", snapshot=self._model.snapshot))
        self._gateway.connect_port(port_name)

    def shutdown(self) -> None:
        self._usb_probe_timer.stop()
        self.host_tasks.attach(None)
        self.host_tasks.cancel_run()
        self.ble_name.detach()
        self.normal_agent.detach()
        self._normal_agent_status_refresh = False
        self.screen_glyphs.attach(None)
        self.screen_icon.attach(None)
        self.codex_agent_focus.unbind()
        self.claude_status.shutdown()
        if self._extension_platform is not None:
            self._extension_platform.shutdown()
        self._reconnect_timer.stop()
        self.stop_lighting_preview(clear_candidate=True)
        self._prompt_device.unbind()
        self._write_deadline.stop()
        self._firmware_status_timer.stop()
        self._firmware_deadline.stop()
        self._remote_firmware_check_timer.stop()
        self._calibration_sample_timer.stop()
        self._calibration_activation_deadline.stop()
        self._diagnostic_capture_heartbeat.stop()
        self._lighting_preview_debounce.stop()
        self._lighting_preview_heartbeat.stop()
        if self._firmware_release_source is not None:
            self._firmware_release_source.cancel()
        self._automation_host.shutdown()
        self._gateway.shutdown()

    def _on_candidates(self, candidates: tuple[PortCandidate, ...]) -> None:
        if self._model.state is AppState.READ_FAILED and not (
            self._firmware_update.is_busy or self._calibration.state is CalibrationState.UNKNOWN
        ):
            # A failed BLE link must not prevent a newly inserted USB cable from
            # working, or silently retry the same failed connection every tick.
            candidates = tuple(candidate for candidate in candidates
                               if candidate.transport == "usb"
                               and candidate.port_name not in self._failed_connection_ports)
            if not candidates:
                return
        candidates = tuple(
            replace(candidate, serial_number=self._known_device_names[candidate.port_name][0])
            if candidate.transport == "bluetooth" and candidate.port_name in self._known_device_names else candidate
            for candidate in candidates
        )
        if not candidates and self._firmware_update.is_busy:
            self._set(ScreenModel(
                AppState.DISCONNECTED,
                "等待固件维护设备重新连接 USB…",
                snapshot=self._model.snapshot,
            ))
            self._reconnect_timer.start()
            return
        if self._model.state is AppState.DISCONNECTED and not candidates:
            return
        if not candidates:
            self._set(
                ScreenModel(
                    AppState.NO_DEVICE,
                    "尚未连接 BORING 设备",
                    "请插入 USB，或先在系统蓝牙设置中连接设备。",
                    snapshot=self._model.snapshot,
                )
            )
            if not self._reconnect_timer.isActive():
                self._reconnect_timer.start()
            return
        if len(candidates) > 1:
            self._reconnect_timer.stop()
            self._set(
                ScreenModel(
                    AppState.MULTIPLE_DEVICES,
                    "此电脑连接了多台 BORING 设备",
                    "请选择要管理的设备。",
                    candidates=candidates,
                    snapshot=self._model.snapshot,
                )
            )
            return
        self.connect_candidate(candidates[0].port_name)

    def _on_progress(self, message: str) -> None:
        if self._model.state in {AppState.SCANNING, AppState.CONNECTING}:
            self._set(
                ScreenModel(
                    self._model.state,
                    message,
                    candidates=self._model.candidates,
                    snapshot=self._model.snapshot,
                )
            )

    def _on_snapshot(self, snapshot: DeviceSnapshot) -> None:
        self._connection_port = snapshot.port_name
        self._failed_connection_ports.clear()
        self._normal_agent_status_refresh = False
        self.ble_name.attach(snapshot)
        self.normal_agent.attach(snapshot)
        if snapshot.trust.is_authenticated:
            self._known_device_names[snapshot.port_name] = (str(snapshot.identity["serial"]), "")
        self._config_refresh = None
        self._reconnect_timer.stop()
        if isinstance(snapshot, DeviceSnapshot):
            if self._contract is None:
                raise ValueError("共享配置 Schema 不可用")
            candidate = LocalDraft.from_snapshot(snapshot, self._contract)
            same_device_dirty_draft = (
                self._draft is not None
                and self._draft.is_dirty
                and self._draft.serial == candidate.serial
                and self._draft.hardware_id == candidate.hardware_id
            )
            if not same_device_dirty_draft:
                self._draft = self._drafts.setdefault(candidate.key, candidate)
            # Firmware can change capabilities without changing the saved config.
            # Keep edits and their original write base, but use the live limits.
            self._draft.max_profiles = candidate.max_profiles
            self._draft.controls = candidate.controls
            self._draft.actions = candidate.actions
            self._draft.limits = dict(candidate.limits)
            self._draft.features = copy.deepcopy(candidate.features)
            self._sync_draft_platform(snapshot)
            serial = str(snapshot.identity.get("serial", ""))
            self._workflow_host.bind(serial)
            self._automation_host.bind(serial)
            if self._prompt_library is None or self._prompt_library.serial != serial:
                try:
                    self._prompt_library = self._prompt_library_store.load(serial)
                    self._prompt_library_error = ""
                except PromptLibraryError as exc:
                    self._prompt_library = PromptLibrary(serial)
                    self._prompt_library_error = str(exc)
            self._diagnostics.bind_snapshot(snapshot)
        features = (
            snapshot.capabilities.get("features")
            if isinstance(snapshot, DeviceSnapshot)
            else None
        )
        preview_supported = (
            isinstance(features, dict)
            and features.get("lighting_preview") is True
        )
        self._reset_lighting_preview(
            supported=preview_supported,
            message=(
                "设备当前只读，实时预览不可用"
                if preview_supported and snapshot.is_read_only
                else (
                    "实时预览已就绪；开启后的效果不会自动保存"
                    if preview_supported
                    else "当前固件不支持实时预览"
                )
            ),
        )
        state = AppState.READ_ONLY if snapshot.is_read_only else AppState.READY
        self.screen_icon.attach(snapshot, writable=state is AppState.READY)
        self.screen_glyphs.attach(snapshot, writable=state is AppState.READY)
        self._set(ScreenModel(state, snapshot.config_status_label, snapshot=snapshot))
        if isinstance(snapshot, DeviceSnapshot):
            self.codex_agent_focus.bind(snapshot)
        self._start_extension_platform_for_current_session()
        if self._prompt_library is not None:
            self._prompt_device.bind(self._prompt_library, snapshot)
        self._reconcile_snapshot_after_write(snapshot)
        self.host_tasks.attach(snapshot, readback=True)
        self._reconcile_snapshot_after_calibration(snapshot)
        self._reconcile_snapshot_after_firmware(snapshot)
        self._reset_remote_firmware_for_changed_device(snapshot)
        self.claude_status.bind(snapshot)
        if self.ble_name.supported and self.ble_name.connected:
            self.ble_name.read()
        if self.normal_agent.supported and self.normal_agent.connected:
            self.normal_agent.read()
        self._reconcile_remote_firmware_version(snapshot)
        self.auto_check_remote_firmware()

    def _reconcile_remote_firmware_version(self, snapshot: DeviceSnapshot) -> None:
        current = self._remote_firmware
        # A package being installed still needs full OTA partition/rollback reconciliation.
        if current.state not in {RemoteFirmwareState.AVAILABLE, RemoteFirmwareState.CURRENT}:
            return
        if current.release is None:
            return
        if self.firmware_origin != "official":
            self._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.IDLE))
            return
        try:
            newer = release_is_newer(current.release, snapshot)
        except FirmwareReleaseError:
            self._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.IDLE))
            return
        self._set_remote_firmware(replace(
            current,
            state=RemoteFirmwareState.AVAILABLE if newer else RemoteFirmwareState.CURRENT,
            message=(f"发现发布版本 {current.release.version}，可由你决定是否下载" if newer
                     else f"没有更新的在线固件（服务端版本 {current.release.version}）"),
        ))

    def _reset_remote_firmware_for_changed_device(self, snapshot: DeviceSnapshot) -> None:
        identity = tuple(
            str(snapshot.identity.get(field, ""))
            for field in ("serial", "product_id", "hardware_id")
        )
        if self._remote_firmware_device is None or identity == self._remote_firmware_device:
            return
        if self._firmware_release_source is not None:
            self._firmware_release_source.cancel()
        previous = self._remote_firmware
        transaction = self._firmware_update
        online_package = previous.state is RemoteFirmwareState.DOWNLOADED or (
            previous.state is RemoteFirmwareState.CURRENT
            and transaction.state is FirmwareUpdateState.COMPLETED
        )
        # Reconnect reconciliation above has already stopped a different device's
        # active transfer. Keep its failure explanation, but do not offer its
        # downloaded package or success message as belonging to the new device.
        if online_package and not transaction.is_busy:
            self._set_firmware_update(
                replace(transaction, package=None)
                if transaction.state is FirmwareUpdateState.FAILED
                else FirmwareUpdateTransaction()
            )
        self._remote_firmware_device = identity
        # We retain one result, so selecting another device must fetch its own result.
        self._remote_firmware_last_check.pop(identity, None)
        self._set_remote_firmware(
            RemoteFirmwareCheck(
                state=(RemoteFirmwareState.IDLE if self._firmware_release_source is not None
                       else RemoteFirmwareState.UNCONFIGURED),
                message="设备已变化，请检查当前设备的在线固件",
            )
        )

    def _on_status_updated(self, status: dict) -> None:
        snapshot = self._model.snapshot
        if snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}:
            return
        self.codex_agent_focus.consume(
            status,
            allowed=(self._model.state is AppState.READY
                     and not self._write_transaction.blocks_editing
                     and not self._calibration.blocks_editing
                     and not self._firmware_update.is_busy),
        )
        if status == snapshot.status:
            self._maybe_refresh_device_config()
            return
        previous_engine = snapshot.status.get("action_engine")
        current_engine = status.get("action_engine")
        previous_local_page = (
            previous_engine.get("local_page")
            if isinstance(previous_engine, dict)
            else None
        )
        current_local_page = (
            current_engine.get("local_page")
            if isinstance(current_engine, dict)
            else None
        )
        if self._lighting_preview.requested and (
            status.get("operating_mode") != snapshot.status.get("operating_mode")
            or (
                current_local_page not in {None, "none"}
                and current_local_page != previous_local_page
            )
        ):
            self.stop_lighting_preview()
        self._diagnostics.observe_status(snapshot.status, status)
        presentation_fields = (
            "state",
            "active",
            "pending",
            "activation_failed",
            "platform",
            "operating_mode",
        )
        presentation_changed = any(
            status.get(field) != snapshot.status.get(field)
            for field in presentation_fields
        )
        # Traffic counters change while Codex is active, but are not page
        # content. Rebuilding their owner would destroy an open Profile menu.
        previous_micro = snapshot.status.get("codex_micro") or {}
        current_micro = status.get("codex_micro") or {}
        presentation_changed = presentation_changed or any(
            current_micro.get(field) != previous_micro.get(field)
            for field in ("connected", "usb_connected", "ble_connected", "mode", "active_slot", "slots")
        )
        updated = replace(snapshot, status=dict(status))
        self._sync_draft_platform(updated)
        state = AppState.READ_ONLY if updated.is_read_only else AppState.READY
        model = ScreenModel(state, updated.config_status_label, snapshot=updated)
        if presentation_changed:
            self._set(model)
        else:
            self._model = model
            self._mark_extension_context_changed()
        self.diagnostics_changed.emit()
        capture = diagnostic_capture(status)
        if capture.get("active") is True and self._page == "diagnostics":
            if not self._diagnostic_capture_heartbeat.isActive():
                self._diagnostic_capture_heartbeat.start()
        elif self._diagnostic_capture_pending != "starting":
            self._diagnostic_capture_heartbeat.stop()
        self._reconcile_status_after_write(updated)
        self._reconcile_status_after_calibration(updated)
        self._maybe_refresh_device_config()

    def _maybe_refresh_device_config(self) -> None:
        snapshot = self._model.snapshot
        if (snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}
                or self._config_refresh is not None or self._contract is None):
            return
        if (self._write_transaction.state not in {
                ConfigTransactionState.IDLE, ConfigTransactionState.ACTIVE,
                ConfigTransactionState.FAILED, ConfigTransactionState.CONFLICT}
                or self._calibration.blocks_editing or self._firmware_update.blocks_editing):
            return
        active = snapshot.status.get("active")
        if not isinstance(active, dict) or snapshot.status.get("pending") is not None:
            return
        if all(active.get(key) == snapshot.config_result.get(key) for key in ("generation", "digest")):
            return
        self._config_refresh = (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"]))
        self._gateway.execute_command(Command("GET_CONFIG", 0x10, {}))

    def _complete_device_config_refresh(self, payload: dict) -> None:
        identity = self._config_refresh
        self._config_refresh = None
        snapshot = self._model.snapshot
        if (snapshot is None or self._model.state not in {AppState.READY, AppState.READ_ONLY}
                or identity != (str(snapshot.identity["serial"]), str(snapshot.identity["hardware_id"]))):
            return
        # The command session already validates GET_CONFIG against the contract.
        result = payload.get("result")
        active = snapshot.status.get("active")
        if (not isinstance(result, dict) or not isinstance(result.get("config"), dict)
                or not isinstance(active, dict)
                or any(result.get(key) != active.get(key) for key in ("generation", "digest"))):
            # Device changed while the read was outstanding. Next status poll retries.
            return
        updated = replace(snapshot, hello_config=dict(active), config_result=copy.deepcopy(result))
        draft = self._draft
        if draft is not None and draft.is_dirty:
            self._write_transaction = ConfigTransaction(
                state=ConfigTransactionState.CONFLICT,
                message="设备配置已变化；已读取最新配置，本地草稿已保留，请选择保留改动或采用设备配置",
                base_generation=draft.base_generation, base_digest=draft.base_digest,
                candidate_config=copy.deepcopy(draft.config), device_serial=draft.serial,
            )
        else:
            candidate = LocalDraft.from_snapshot(updated, self._contract)
            if draft is not None and self._drafts.get(draft.key) is draft:
                self._drafts.pop(draft.key)
            self._draft = candidate
            self._drafts[candidate.key] = candidate
        state = AppState.READ_ONLY if updated.is_read_only else AppState.READY
        self._set(ScreenModel(state, updated.config_status_label, snapshot=updated))
        self.host_tasks.attach(updated, readback=True)

    def _on_command_completed(self, command_name: str, payload: dict) -> None:
        if command_name == "GET_STATUS" and self._normal_agent_status_refresh:
            self._normal_agent_status_refresh = False
            try:
                if self._contract is not None:
                    self._contract.validate_status(payload)
                status = payload["result"]
                self.codex_agent_focus.set_normal_behavior("open_conversation", status=status)
            except (ContractError, ValueError, KeyError, TypeError) as error:
                self.codex_agent_focus.set_normal_behavior(None)
                self.normal_agent.finish_preparing(str(error))
            else:
                self.normal_agent.finish_preparing()
                self._on_status_updated(status)
            return
        if self.normal_agent.completed(command_name, payload):
            if self.normal_agent.preparing and not self._normal_agent_status_refresh:
                self._normal_agent_status_refresh = True
                self._gateway.execute_command(Command("GET_STATUS", 0x13, {}))
            return
        if self.ble_name.completed(command_name, payload):
            snapshot = self._model.snapshot
            if self.ble_name.connected and self.ble_name.result is not None and snapshot is not None:
                self._known_device_names[snapshot.port_name] = (
                    self.ble_name.serial, self.ble_name.result["active_name"]
                )
            return
        if command_name == "GET_CONFIG" and self._config_refresh is not None:
            self._complete_device_config_refresh(payload)
            return
        if self.screen_glyphs.completed(command_name, payload):
            return
        if self.screen_icon.completed(command_name, payload):
            return
        if command_name in {"SET_AGENT_STATUS", "CLEAR_AGENT_STATUS"}:
            return
        if command_name in {"SET_CODEX_USAGE", "CLEAR_CODEX_USAGE"}:
            return
        if command_name in {SET_LIGHTING_PREVIEW, CLEAR_LIGHTING_PREVIEW}:
            self._on_lighting_preview_command_completed(command_name, payload)
            return
        if self.host_tasks.completed(command_name, payload):
            return
        if self._prompt_device.handle_completed(command_name, payload):
            return
        if command_name.startswith("CALIBRATION_"):
            self._on_calibration_command_completed(command_name, payload)
            return
        if command_name.startswith("FW_"):
            self._on_firmware_command_completed(command_name, payload)
            return
        if command_name == "DIAGNOSTIC_START":
            self._diagnostic_capture_pending = ""
            self._diagnostic_capture_error = ""
            if (
                self._page == "diagnostics"
                and not self._diagnostic_capture_heartbeat.isActive()
            ):
                self._diagnostic_capture_heartbeat.start()
            self.diagnostics_changed.emit()
            return
        if command_name == "DIAGNOSTIC_STOP":
            self._diagnostic_capture_pending = ""
            self._diagnostic_capture_error = ""
            self._diagnostic_capture_heartbeat.stop()
            self.diagnostics_changed.emit()
            return
        if command_name == "FACTORY_DEFAULT":
            snapshot = self._model.snapshot
            if snapshot is not None:
                self._set(
                    ScreenModel(
                        AppState.READY,
                        "设备已接收恢复出厂命令，等待激活并重启",
                        snapshot=snapshot,
                    )
                )
            return
        transaction = self._write_transaction
        if command_name == "VALIDATE_CONFIG" and transaction.state is ConfigTransactionState.VALIDATING:
            confirm_after_validation = self._confirm_write_after_validation
            self._confirm_write_after_validation = False
            self._set_write_transaction(
                replace(
                    transaction,
                    state=ConfigTransactionState.AWAITING_CONFIRMATION,
                    message="设备验证通过，等待用户确认写入",
                )
            )
            if confirm_after_validation:
                try:
                    self.confirm_device_write()
                except ValueError as exc:
                    self._finish_write(
                        ConfigTransactionState.FAILED,
                        "设备验证通过，但未能开始写入",
                        str(exc),
                    )
            return
        if command_name == "SET_CONFIG" and transaction.state is ConfigTransactionState.WRITING:
            self._set_write_transaction(
                replace(
                    transaction,
                    state=ConfigTransactionState.PENDING,
                    message="设备已接收候选配置，等待激活",
                )
            )
            return
        if command_name == "GET_CONFIG" and transaction.state is ConfigTransactionState.VERIFYING:
            result = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(result, dict):
                self._reconcile_config_result(result)
            else:
                self._finish_write(ConfigTransactionState.UNKNOWN, "设备读回结果不完整")
            return
        if command_name == "GET_CONFIG" and self._calibration.state is CalibrationState.VERIFYING:
            self._reconcile_calibration_config_result(payload)

    def _on_command_failed(self, command_name: str, error: BootstrapError) -> None:
        if command_name == "GET_STATUS" and self._normal_agent_status_refresh:
            self._normal_agent_status_refresh = False
            self.normal_agent.finish_preparing(error.technical or str(error))
            return
        if self.normal_agent.failed(command_name, error):
            return
        if command_name in {"BLE_SLOT_SELECT", "BLE_SLOT_CLEAR"}:
            self.ble_slot_failed.emit(command_name, str(error))
            return
        if self.ble_name.failed(command_name, error):
            return
        if command_name == "GET_CONFIG" and self._config_refresh is not None:
            self._config_refresh = None
            self._set(replace(self._model, message="读取设备最新配置失败；保留上次读取和本地草稿", technical_message=error.technical))
            return
        if self.screen_glyphs.failed(command_name, error):
            return
        if self.screen_icon.failed(command_name, error):
            return
        if command_name in {"SET_AGENT_STATUS", "CLEAR_AGENT_STATUS"}:
            return
        if command_name in {"SET_CODEX_USAGE", "CLEAR_CODEX_USAGE"}:
            return
        if command_name in {SET_LIGHTING_PREVIEW, CLEAR_LIGHTING_PREVIEW}:
            self._on_lighting_preview_command_failed(command_name, error)
            return
        if self.host_tasks.failed(command_name, error):
            return
        if self._prompt_device.handle_failed(command_name, error):
            return
        if command_name.startswith("CALIBRATION_"):
            self._on_calibration_command_failed(command_name, error)
            return
        if command_name.startswith("FW_"):
            self._on_firmware_command_failed(command_name, error)
            return
        if command_name in {"DIAGNOSTIC_START", "DIAGNOSTIC_STOP"}:
            self._diagnostic_capture_pending = ""
            self._diagnostic_capture_error = error.technical or str(error)
            self._diagnostic_capture_heartbeat.stop()
            self.diagnostics_changed.emit()
            return
        if command_name == "FACTORY_DEFAULT":
            snapshot = self._model.snapshot
            state = AppState.READ_ONLY if snapshot and snapshot.is_read_only else AppState.READY
            self._set(
                ScreenModel(
                    state,
                    "恢复出厂设置失败",
                    error.technical,
                    snapshot=snapshot,
                )
            )
            return
        transaction = self._write_transaction
        if command_name == "SET_CONFIG" and error.error_name == "TRANSPORT_TIMEOUT":
            self._set_write_transaction(
                replace(
                    transaction,
                    state=ConfigTransactionState.PENDING,
                    message="未收到写入 ACK，正在通过设备状态对账；不会自动重试写入",
                    technical=error.technical,
                )
            )
            return
        if error.error_name == "GENERATION_CONFLICT":
            current = error.details.get("current_generation", "未知")
            self._finish_write(
                ConfigTransactionState.CONFLICT,
                "设备配置已被其他操作更新",
                f"current_generation={current}",
            )
            return
        if command_name == "GET_CONFIG":
            if transaction.state in {ConfigTransactionState.VERIFYING, ConfigTransactionState.UNKNOWN}:
                self._finish_write(ConfigTransactionState.UNKNOWN, "无法读回设备配置完成对账", error.technical)
            return
        if command_name == "VALIDATE_CONFIG" and error.error_name == "TRANSPORT_TIMEOUT":
            self._finish_write(
                ConfigTransactionState.FAILED,
                "设备校验回复超时，配置尚未保存，请重试。",
                error.technical,
            )
            return
        if command_name == "VALIDATE_CONFIG" and not error.error_name:
            self._finish_write(
                ConfigTransactionState.FAILED,
                "设备校验未完成，配置尚未保存，请查看详情后重试。",
                error.technical,
            )
            return
        self._finish_write(
            ConfigTransactionState.FAILED,
            "设备拒绝候选配置" if command_name == "VALIDATE_CONFIG" else "设备写入失败",
            error.technical,
        )

    def _on_lighting_preview_command_completed(
        self,
        command_name: str,
        payload: dict,
    ) -> None:
        try:
            validate_lighting_preview_response(command_name, payload)
        except LightingPreviewError as exc:
            self._lighting_preview_debounce.stop()
            self._lighting_preview_heartbeat.stop()
            self._lighting_preview_sent = False
            self._lighting_preview_clear_pending = False
            self._set_lighting_preview(
                replace(
                    self._lighting_preview,
                    requested=False,
                    active=False,
                    pending=False,
                    message="设备返回的预览响应无效",
                    technical=str(exc),
                )
            )
            return
        if command_name == SET_LIGHTING_PREVIEW:
            if not self._lighting_preview.requested or self._lighting_preview_clear_pending:
                return
            self._set_lighting_preview(
                replace(
                    self._lighting_preview,
                    active=True,
                    pending=False,
                    message="正在设备上实时预览 · 临时效果尚未保存",
                    technical="",
                )
            )
            if not self._lighting_preview_heartbeat.isActive():
                self._lighting_preview_heartbeat.start()
            return

        self._lighting_preview_clear_pending = False
        self._lighting_preview_sent = False
        if self._lighting_preview.requested:
            self._set_lighting_preview(
                replace(
                    self._lighting_preview,
                    active=False,
                    pending=True,
                    message="正在重新发送临时预览…",
                    technical="",
                )
            )
            self._lighting_preview_debounce.start()
            return
        self._set_lighting_preview(
            replace(
                self._lighting_preview,
                active=False,
                pending=False,
                message="实时预览已关闭，设备已恢复保存值",
                technical="",
            )
        )

    def _on_lighting_preview_command_failed(
        self,
        command_name: str,
        error: BootstrapError,
    ) -> None:
        self._lighting_preview_debounce.stop()
        self._lighting_preview_heartbeat.stop()
        if command_name == CLEAR_LIGHTING_PREVIEW:
            self._lighting_preview_clear_pending = False
            if self._lighting_preview.requested:
                self._set_lighting_preview(
                    replace(
                        self._lighting_preview,
                        active=False,
                        pending=True,
                        message="正在重新发送临时预览…",
                        technical="",
                    )
                )
                self._lighting_preview_debounce.start()
                return
            self._lighting_preview_sent = False
            self._set_lighting_preview(
                replace(
                    self._lighting_preview,
                    active=False,
                    pending=False,
                    message="未确认设备已立即清除预览；固件将在租约到期后恢复",
                    technical=error.technical or str(error),
                )
            )
            return

        timed_out = error.error_name == "TRANSPORT_TIMEOUT"
        self._set_lighting_preview(
            replace(
                self._lighting_preview,
                requested=False,
                active=False,
                pending=timed_out,
                message=(
                    "未收到预览响应，正在尝试恢复设备保存值…"
                    if timed_out
                    else (
                        "设备本地灯光设置正在使用，实时预览已停止"
                        if error.error_name == "BUSY"
                        else "设备拒绝了灯光预览"
                    )
                ),
                technical=error.technical or str(error),
            )
        )
        if timed_out and not self._lighting_preview_clear_pending:
            self._lighting_preview_clear_pending = True
            self._gateway.execute_command(clear_lighting_preview_command())
        else:
            self._lighting_preview_sent = False

    def _cancel_remote_firmware_request(self) -> None:
        if self._remote_firmware.state not in {
            RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING,
        }:
            return
        if self._firmware_release_source is not None:
            self._firmware_release_source.cancel()
        # A canceled attempt is eligible after reconnection, unlike an ordinary network failure.
        self._remote_firmware_last_check.pop(self._remote_firmware_device, None)
        self._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.IDLE))

    def _on_failure(self, kind: BootstrapKind, message: str, technical: str) -> None:
        self._cancel_remote_firmware_request()
        self.screen_glyphs.attach(None)
        self.screen_icon.attach(None)
        self._reconnect_timer.stop()
        self._suspend_extension_platform("设备认证或通信失败")
        self._reset_lighting_preview(
            supported=False,
            message="设备通信不可用，实时预览已停止",
        )
        self._diagnostic_capture_heartbeat.stop()
        self._diagnostic_capture_pending = ""
        self._diagnostics.record_connection_loss(technical or message)
        self.diagnostics_changed.emit()
        self._prompt_device.unbind()
        firmware_was_busy = self._firmware_update.is_busy
        if firmware_was_busy:
            self._pause_firmware_update("固件维护期间通信失败，重连后将先读取 FW_STATUS 对账", technical)
        transaction_state = self._write_transaction.state
        if transaction_state in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
        }:
            self._finish_write(
                ConfigTransactionState.UNKNOWN,
                "写入期间通信失败，结果未知；重新连接后仅进行读回对账",
                technical,
            )
        elif transaction_state in {
            ConfigTransactionState.VALIDATING,
            ConfigTransactionState.AWAITING_CONFIRMATION,
        }:
            self._finish_write(ConfigTransactionState.FAILED, "写入前设备通信失败", technical)
        calibration_was_blocking = self._calibration.blocks_editing
        if calibration_was_blocking:
            self._handle_calibration_connection_loss(technical)
        if kind is BootstrapKind.FIRMWARE_UPDATE_REQUIRED:
            state = AppState.FIRMWARE_UPDATE_REQUIRED
        elif kind in {BootstrapKind.AUTHENTICATION_INTERRUPTED, BootstrapKind.DISCOVERY_INTERRUPTED}:
            state = AppState.DISCONNECTED
            if kind is BootstrapKind.DISCOVERY_INTERRUPTED:
                message = "蓝牙连接检查暂未完成，正在自动重试。"
        elif kind is BootstrapKind.INCOMPATIBLE:
            state = AppState.INCOMPATIBLE
        elif kind is BootstrapKind.AUTHENTICITY_FAILED:
            state = AppState.AUTHENTICITY_FAILED
        else:
            state = AppState.READ_FAILED
            if self._connection_port and self._model.state in {
                AppState.CONNECTING, AppState.READY, AppState.READ_ONLY,
            }:
                self._failed_connection_ports.add(self._connection_port)
            technical = "\n".join(part for part in (message, technical) if part)
            message = "配置连接失败；可重新连接，或使用 USB 连接设备。"
        self._set(ScreenModel(state, message, technical, snapshot=self._model.snapshot))
        if (kind in {BootstrapKind.AUTHENTICATION_INTERRUPTED, BootstrapKind.DISCOVERY_INTERRUPTED}
                and not self._reconnect_timer.isActive()):
            # The failed worker closes after publishing this signal. Let the
            # timer start a fresh scan after that cleanup instead of re-entering
            # the same worker and canceling the replacement scan.
            self._reconnect_timer.start(5000 if kind is BootstrapKind.DISCOVERY_INTERRUPTED else 500)
        elif (kind not in {BootstrapKind.FIRMWARE_UPDATE_REQUIRED, BootstrapKind.AUTHENTICITY_FAILED}
                and (state is AppState.READ_FAILED or firmware_was_busy
                     or self._calibration.state is CalibrationState.UNKNOWN)
                and not self._reconnect_timer.isActive()):
            self._reconnect_timer.start()

    def _on_disconnected(self, technical: str) -> None:
        self._reconnect_timer.setInterval(500)
        self._cancel_remote_firmware_request()
        self.screen_glyphs.attach(None)
        self.screen_icon.attach(None)
        self.claude_status.unbind()
        self._suspend_extension_platform("设备连接已断开")
        self._reset_lighting_preview(
            supported=False,
            message="设备已断开，固件将恢复已保存的灯光",
        )
        self._diagnostic_capture_heartbeat.stop()
        self._diagnostic_capture_pending = ""
        self._diagnostics.record_connection_loss(technical)
        self.diagnostics_changed.emit()
        self._prompt_device.unbind()
        self._set(
            ScreenModel(
                AppState.DISCONNECTED,
                "设备连接已断开",
                technical,
                snapshot=self._model.snapshot,
            )
        )
        if not self._reconnect_timer.isActive():
            self._reconnect_timer.start()
        self._scan_for_reconnect()
        if self._firmware_update.is_busy:
            if self._firmware_update.state in {
                FirmwareUpdateState.FINALIZING,
                FirmwareUpdateState.WAITING_RECONNECT,
                FirmwareUpdateState.VERIFYING,
            }:
                self._set_firmware_update(
                    replace(
                        self._firmware_update,
                        state=(
                            FirmwareUpdateState.VERIFYING
                            if self._firmware_update.state is FirmwareUpdateState.VERIFYING
                            else FirmwareUpdateState.WAITING_RECONNECT
                        ),
                        message="设备正在重启；重新连接后将核对版本和 build_id",
                        technical=technical,
                    )
                )
            else:
                self._pause_firmware_update(
                    "传输期间连接中断；重连后将从设备报告的偏移继续，不会盲目重发",
                    technical,
                )
        if self._write_transaction.state in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
        }:
            self._set_write_transaction(
                replace(
                    self._write_transaction,
                    state=ConfigTransactionState.PENDING,
                    message="写入期间连接中断；重连后仅按 generation 与 digest 对账，不会重发",
                    technical=technical,
                )
            )
        elif self._write_transaction.state is ConfigTransactionState.UNKNOWN:
            self._set_write_transaction(
                replace(
                    self._write_transaction,
                    message="设备连接已断开；重连后仍只进行读回对账，不会重发写入",
                    technical=technical,
                )
            )
        elif self._write_transaction.state in {
            ConfigTransactionState.VALIDATING,
            ConfigTransactionState.AWAITING_CONFIRMATION,
        }:
            self._finish_write(ConfigTransactionState.FAILED, "写入前设备连接已断开", technical)
        if self._calibration.blocks_editing:
            self._handle_calibration_connection_loss(technical)

    def _can_handoff_to_usb(self) -> bool:
        snapshot = self._model.snapshot
        return (
            self._model.state in {AppState.READY, AppState.READ_ONLY}
            and snapshot is not None and snapshot.connection_kind == "bluetooth"
            and snapshot.trust.is_authenticated
            and not (self._write_transaction.blocks_editing
                     or self._write_transaction.state is ConfigTransactionState.UNKNOWN
                     or self._firmware_update.blocks_editing or self._calibration.blocks_editing
                     or self._remote_firmware.state in {
                         RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING,
                     }
                     or self._config_refresh is not None
                     or self.screen_icon.busy or self.screen_glyphs.busy
                     or self._prompt_device.status.is_busy or self.ble_name.busy
                     or self.normal_agent.busy or self.normal_agent.uncertain
                     or self.diagnostic_capture_active or self._diagnostic_capture_pending
                     or self.host_tasks.running or self._automation_host.running
                     or self._workflow_host.running or snapshot.status.get("pending") is not None)
        )

    def _probe_usb_handoff(self) -> None:
        probe = getattr(self._gateway, "probe_usb", None)
        if callable(probe) and self._can_handoff_to_usb():
            probe(str(self._model.snapshot.identity["serial"]))

    def _on_usb_handoff_candidate(self, candidate: PortCandidate) -> None:
        # A write may have started while enumeration was in flight. Retry later.
        if (self._can_handoff_to_usb() and candidate.transport == "usb"
                and candidate.serial_number == self._model.snapshot.identity["serial"]):
            self.connect_candidate(candidate.port_name)

    def _scan_for_reconnect(self) -> None:
        if self._model.state is AppState.READ_FAILED and not (
            self._firmware_update.is_busy or self._calibration.state is CalibrationState.UNKNOWN
        ):
            self._gateway.scan(usb_only=True)
            return
        reconnectable = self._model.state in {AppState.NO_DEVICE, AppState.DISCONNECTED} or (
            self._model.state is AppState.READ_FAILED
            and (
                self._firmware_update.is_busy
                or self._calibration.state is CalibrationState.UNKNOWN
            )
        )
        if reconnectable:
            usb_only = self._firmware_update.is_busy
            snapshot = self._model.snapshot
            preferred_port = str(getattr(snapshot, "port_name", "") or "")
            targeted_scan = getattr(self._gateway, "scan_reconnect", None)
            if preferred_port and callable(targeted_scan):
                targeted_scan(preferred_port, usb_only=usb_only)
            elif usb_only:
                self._gateway.scan(usb_only=True)
            else:
                self._gateway.scan()

    def _set(self, model: ScreenModel) -> None:
        if model == self._model:
            return
        if (model.state in {AppState.SCANNING, AppState.CONNECTING}
                and self._write_transaction.state in {
                    ConfigTransactionState.VALIDATING,
                    ConfigTransactionState.AWAITING_CONFIRMATION,
                }):
            self._write_deadline.stop()
            self._write_transaction = replace(
                self._write_transaction, state=ConfigTransactionState.FAILED,
                message="连接已变化，请重新验证配置",
            )
        self._model = model
        if (model.state in {AppState.READY, AppState.READ_ONLY}
                and model.snapshot is not None
                and model.snapshot.connection_kind == "bluetooth"):
            if not self._usb_probe_timer.isActive():
                self._usb_probe_timer.start()
        else:
            self._usb_probe_timer.stop()
        if model.state not in {AppState.READY, AppState.READ_ONLY}:
            self._normal_agent_status_refresh = False
            self.ble_name.detach()
            self.normal_agent.detach()
            self._config_refresh = None
            self.codex_agent_focus.unbind()
            self.claude_status.unbind()
        self._mark_extension_context_changed()
        self.changed.emit(model)

    def _start_extension_platform_for_current_session(self) -> None:
        platform = self._extension_platform
        if platform is None or self._model.state not in {
            AppState.READY,
            AppState.READ_ONLY,
        }:
            return
        try:
            platform.start()
        except RuntimeError as exc:
            self._extension_platform_error = str(exc)
            self.changed.emit(self._model)
            return
        self._extension_platform_error = ""

    def _suspend_extension_platform(self, reason: str) -> None:
        if self._extension_platform is not None:
            self._extension_platform.suspend(reason)

    def _mark_extension_context_changed(self) -> None:
        self._extension_context_revision += 1
        self.extension_context_changed.emit(self._extension_context_revision)

    def _notify_draft_changed(self) -> None:
        self._mark_extension_context_changed()
        self.changed.emit(self._model)

    def _on_prompt_device_changed(self) -> None:
        listener = self._prompt_device.listener_status
        if listener != self._extension_listener_status:
            self._extension_listener_status = listener
            self._mark_extension_context_changed()
        self.changed.emit(self._model)

    def _before_draft_change(self) -> None:
        self._ensure_icon_idle()
        if self._firmware_update.blocks_editing:
            raise ValueError("固件维护进行中，不能修改本地草稿")
        if self._calibration.blocks_editing:
            raise ValueError("摇杆校准进行中，不能修改本地草稿")
        if self._write_transaction.blocks_editing:
            raise ValueError("设备写入和对账期间不能修改本地草稿")
        if self._write_transaction.state is not ConfigTransactionState.IDLE:
            self._write_transaction = ConfigTransaction()

    def _set_write_transaction(self, transaction: ConfigTransaction) -> None:
        if transaction == self._write_transaction:
            return
        self._write_transaction = transaction
        self._sync_prompt_polling()
        self.changed.emit(self._model)

    def _set_firmware_update(self, transaction: FirmwareUpdateTransaction) -> None:
        if transaction == self._firmware_update:
            return
        self._firmware_update = transaction
        self.claude_status.set_paused(transaction.blocks_editing)
        self._sync_prompt_polling()
        self.changed.emit(self._model)

    def _set_remote_firmware(self, state: RemoteFirmwareCheck) -> None:
        if state == self._remote_firmware:
            return
        self._remote_firmware = state
        self.changed.emit(self._model)

    def _set_calibration(self, transaction: CalibrationTransaction) -> None:
        if transaction == self._calibration:
            return
        self._calibration = transaction
        self._sync_prompt_polling()
        self.changed.emit(self._model)

    def _sync_prompt_polling(self) -> None:
        self.host_tasks.set_paused(self._write_transaction.is_busy or self._firmware_update.blocks_editing or self._calibration.blocks_editing)
        self._prompt_device.set_polling_paused(
            self._write_transaction.is_busy
            or self._firmware_update.blocks_editing
            or self._calibration.blocks_editing
        )

    def _on_calibration_command_completed(
        self, command_name: str, payload: dict
    ) -> None:
        transaction = self._calibration
        try:
            if command_name == "CALIBRATION_START":
                sample = CalibrationSample.from_payload(
                    payload,
                    require_start_timing=True,
                )
                self._apply_calibration_sample(sample)
                self._calibration_sample_timer.setInterval(sample.center_window_ms)
                self._calibration_sample_timer.start()
                return
            if command_name == "CALIBRATION_SAMPLE":
                sample = CalibrationSample.from_payload(
                    payload,
                    expected_session_id=transaction.session_id,
                )
                if transaction.cancel_requested:
                    self._set_calibration(
                        replace(
                            transaction,
                            state=CalibrationState.CANCELLING,
                            message="正在取消校准并保留原有配置",
                            cancel_requested=True,
                        )
                    )
                    self._gateway.execute_command(
                        calibration_cancel_command(transaction.session_id)
                    )
                else:
                    self._apply_calibration_sample(sample)
                    self._calibration_sample_timer.setInterval(100)
                    self._calibration_sample_timer.start()
                return
            if command_name == "CALIBRATION_CONFIRM":
                generation, digest = parse_calibration_confirmation(payload)
                expected_generation = (
                    transaction.base_generation + 1
                    if transaction.base_generation is not None
                    else None
                )
                if expected_generation is not None and generation != expected_generation:
                    raise CalibrationError("校准候选 generation 不是当前配置的下一代")
                self._gateway.set_status_poll_interval(250)
                self._calibration_activation_deadline.start()
                self._set_calibration(
                    replace(
                        transaction,
                        state=CalibrationState.PENDING,
                        message="校准候选已写入；请松开所有控件，等待设备激活",
                        technical="",
                        pending_generation=generation,
                        pending_digest=digest,
                        cancel_requested=False,
                    )
                )
                return
            if command_name == "CALIBRATION_CANCEL":
                result = payload.get("result") if isinstance(payload, dict) else None
                if (
                    not isinstance(result, dict)
                    or result.get("state") != "CANCELLED"
                    or result.get("session_id") != transaction.session_id
                ):
                    raise CalibrationError("设备没有确认取消当前校准会话")
                self._finish_calibration(
                    CalibrationState.CANCELLED,
                    "校准已取消，设备保留了原有摇杆配置",
                    clear_session=True,
                )
        except CalibrationError as exc:
            if command_name == "CALIBRATION_CONFIRM":
                self._calibration_sample_timer.stop()
                self._set_calibration(
                    replace(
                        transaction,
                        state=CalibrationState.UNKNOWN,
                        message="校准确认响应无效；正在重新连接并只读对账",
                        technical=str(exc),
                        session_id=0,
                    )
                )
                self.refresh()
                return
            self._finish_calibration(
                CalibrationState.FAILED,
                "设备返回了无效的校准响应",
                str(exc),
                clear_session=command_name == "CALIBRATION_START",
            )

    def _apply_calibration_sample(self, sample: CalibrationSample) -> None:
        if sample.state == "CENTERING":
            state = CalibrationState.CENTERING
        elif sample.travel_complete:
            state = CalibrationState.READY_TO_CONFIRM
        else:
            state = CalibrationState.CAPTURING
        message = {
            CalibrationState.CENTERING: "请保持摇杆松开，正在采集中立点",
            CalibrationState.CAPTURING: "请沿完整边缘缓慢转动摇杆",
            CalibrationState.READY_TO_CONFIRM: "行程已完整；请松开摇杆回到中心，然后保存",
        }[state]
        current = self._calibration
        self._set_calibration(
            replace(
                current,
                state=state,
                message=message,
                technical="",
                session_id=sample.session_id,
                raw_x=sample.raw_x,
                raw_y=sample.raw_y,
                center_x=sample.center_x,
                center_y=sample.center_y,
                minimum_x=sample.minimum_x,
                minimum_y=sample.minimum_y,
                maximum_x=sample.maximum_x,
                maximum_y=sample.maximum_y,
                travel_complete=sample.travel_complete,
                center_window_ms=sample.center_window_ms or current.center_window_ms,
                timeout_ms=sample.timeout_ms or current.timeout_ms,
            )
        )

    def _poll_calibration_sample(self) -> None:
        transaction = self._calibration
        if transaction.state not in {
            CalibrationState.CENTERING,
            CalibrationState.CAPTURING,
            CalibrationState.READY_TO_CONFIRM,
        } or transaction.session_id <= 0:
            return
        self._set_calibration(
            replace(transaction, state=CalibrationState.SAMPLING)
        )
        self._gateway.execute_command(
            calibration_sample_command(transaction.session_id)
        )

    def _on_calibration_command_failed(
        self, command_name: str, error: BootstrapError
    ) -> None:
        transaction = self._calibration
        if command_name == "CALIBRATION_CANCEL" and error.error_name == "NOT_FOUND":
            self._finish_calibration(
                CalibrationState.CANCELLED,
                "设备中已没有活动校准会话；原配置未改动",
                clear_session=True,
            )
            return
        if command_name == "CALIBRATION_SAMPLE" and error.error_name in {"NOT_FOUND", "TIMEOUT"}:
            self._finish_calibration(
                CalibrationState.FAILED,
                "校准会话已失效，原配置未改动",
                error.technical,
                clear_session=True,
            )
            return
        if command_name == "CALIBRATION_CONFIRM" and error.error_name == "VALIDATION_FAILED":
            self._set_calibration(
                replace(
                    transaction,
                    state=CalibrationState.READY_TO_CONFIRM,
                    message="设备尚未通过最终检查；请完成全行程并松手回中后重试",
                    technical=error.technical,
                )
            )
            self._calibration_sample_timer.setInterval(100)
            self._calibration_sample_timer.start()
            return
        if command_name == "CALIBRATION_CONFIRM" and error.error_name == "TRANSPORT_TIMEOUT":
            self._calibration_sample_timer.stop()
            self._set_calibration(
                replace(
                    transaction,
                    state=CalibrationState.UNKNOWN,
                    message="校准确认结果未知；正在重新连接并只读对账",
                    technical=error.technical,
                )
            )
            self.refresh()
            return
        self._finish_calibration(
            CalibrationState.FAILED,
            "设备拒绝了摇杆校准命令",
            f"{command_name}: {error.technical}",
            clear_session=command_name == "CALIBRATION_START",
        )

    def _handle_calibration_connection_loss(self, technical: str) -> None:
        self._calibration_sample_timer.stop()
        self._calibration_activation_deadline.stop()
        transaction = self._calibration
        if transaction.state in {
            CalibrationState.CONFIRMING,
            CalibrationState.PENDING,
            CalibrationState.VERIFYING,
            CalibrationState.UNKNOWN,
        }:
            self._set_calibration(
                replace(
                    transaction,
                    state=CalibrationState.UNKNOWN,
                    message="校准确认期间连接中断；重连后将按 generation 和 digest 对账",
                    technical=technical,
                    session_id=0,
                )
            )
        else:
            self._finish_calibration(
                CalibrationState.FAILED,
                "USB 连接中断已取消未确认的校准，原配置未改动",
                technical,
                clear_session=True,
            )

    def _reconcile_status_after_calibration(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._calibration
        if transaction.state not in {
            CalibrationState.PENDING,
            CalibrationState.VERIFYING,
            CalibrationState.UNKNOWN,
        }:
            return
        active = snapshot.status.get("active")
        pending = snapshot.status.get("pending")
        if snapshot.status.get("activation_failed") is True:
            self._finish_calibration(
                CalibrationState.FAILED,
                "设备报告校准候选激活失败",
                clear_session=True,
            )
            return
        if isinstance(pending, dict) and self._calibration_candidate_matches(pending):
            self._adopt_pending_calibration(pending)
            return
        if isinstance(active, dict) and self._calibration_candidate_matches(active):
            if transaction.state is not CalibrationState.VERIFYING:
                self._request_calibration_readback("校准候选已激活，正在读回完整配置")
            return
        active_generation = active.get("generation") if isinstance(active, dict) else None
        if (
            isinstance(active_generation, int)
            and transaction.base_generation is not None
            and active_generation > transaction.base_generation
        ):
            self._finish_calibration(
                CalibrationState.CONFLICT,
                "其他配置已先于当前校准生效",
                clear_session=True,
            )

    def _reconcile_snapshot_after_calibration(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._calibration
        if transaction.state not in {
            CalibrationState.PENDING,
            CalibrationState.VERIFYING,
            CalibrationState.UNKNOWN,
        }:
            return
        active = snapshot.status.get("active")
        pending = snapshot.status.get("pending")
        if isinstance(pending, dict) and self._calibration_candidate_matches(pending):
            self._adopt_pending_calibration(pending)
            return
        if (
            isinstance(active, dict)
            and self._calibration_candidate_matches(active)
            and snapshot.config_result.get("digest") == active.get("digest")
        ):
            self._complete_calibration(snapshot.config_result)
            return
        generation = snapshot.config_result.get("generation")
        digest = snapshot.config_result.get("digest")
        if (
            isinstance(generation, int)
            and transaction.base_generation is not None
            and generation == transaction.base_generation
            and digest == transaction.base_digest
        ):
            self._finish_calibration(
                CalibrationState.FAILED,
                "读回确认设备仍是校准前配置，本次候选未落盘",
                clear_session=True,
            )
            return
        if (
            isinstance(generation, int)
            and transaction.base_generation is not None
            and generation > transaction.base_generation
        ):
            self._finish_calibration(
                CalibrationState.CONFLICT,
                "重连后发现设备已激活其他配置",
                clear_session=True,
            )

    def _calibration_candidate_matches(self, value: dict) -> bool:
        transaction = self._calibration
        generation = value.get("generation")
        digest = value.get("digest")
        if transaction.pending_digest:
            return (
                generation == transaction.pending_generation
                and digest == transaction.pending_digest
            )
        return (
            transaction.state is CalibrationState.UNKNOWN
            and transaction.base_generation is not None
            and generation == transaction.base_generation + 1
            and isinstance(digest, str)
        )

    def _adopt_pending_calibration(self, pending: dict) -> None:
        generation = pending.get("generation")
        digest = pending.get("digest")
        self._gateway.set_status_poll_interval(250)
        if not self._calibration_activation_deadline.isActive():
            self._calibration_activation_deadline.start()
        self._set_calibration(
            replace(
                self._calibration,
                state=CalibrationState.PENDING,
                message="校准候选正在等待所有实体控件松开后激活",
                pending_generation=generation if isinstance(generation, int) else None,
                pending_digest=digest if isinstance(digest, str) else "",
                session_id=0,
            )
        )

    def _on_calibration_activation_deadline(self) -> None:
        if self._calibration.state in {
            CalibrationState.PENDING,
            CalibrationState.VERIFYING,
        }:
            self._request_calibration_readback(
                "10 秒内未确认激活，正在进行最后一次配置读回"
            )

    def _request_calibration_readback(self, message: str) -> None:
        self._calibration_activation_deadline.stop()
        self._set_calibration(
            replace(
                self._calibration,
                state=CalibrationState.VERIFYING,
                message=message,
            )
        )
        self._gateway.execute_command(Command("GET_CONFIG", 0x10, {}))

    def _reconcile_calibration_config_result(self, payload: dict) -> None:
        try:
            if self._contract is None:
                raise CalibrationError("共享配置 Schema 不可用")
            self._contract.validate_config_response(payload)
        except (CalibrationError, ContractError) as exc:
            self._finish_calibration(
                CalibrationState.UNKNOWN,
                "校准配置读回结构无效",
                str(exc),
                clear_session=True,
            )
            return
        result = payload["result"]
        pending = self._model.snapshot.status.get("pending") if self._model.snapshot else None
        if (
            result.get("digest") == self._calibration.pending_digest
            and self._calibration_config_matches(result.get("config"))
        ):
            self._complete_calibration(result)
        elif isinstance(pending, dict) and self._calibration_candidate_matches(pending):
            self._adopt_pending_calibration(pending)
        else:
            generation = result.get("generation")
            if generation == self._calibration.base_generation:
                self._finish_calibration(
                    CalibrationState.FAILED,
                    "读回确认设备仍是校准前配置",
                    clear_session=True,
                )
            else:
                self._finish_calibration(
                    CalibrationState.UNKNOWN,
                    "无法确认校准候选是否生效；不会自动重发确认",
                    clear_session=True,
                )

    def _calibration_config_matches(self, config: object) -> bool:
        if not isinstance(config, dict):
            return False
        joystick = config.get("joystick")
        if not isinstance(joystick, dict) or joystick.get("calibrated") is not True:
            return False
        x_axis = joystick.get("x")
        y_axis = joystick.get("y")
        transaction = self._calibration
        return (
            isinstance(x_axis, dict)
            and isinstance(y_axis, dict)
            and x_axis.get("minimum") == transaction.minimum_x
            and x_axis.get("center") == transaction.center_x
            and x_axis.get("maximum") == transaction.maximum_x
            and y_axis.get("minimum") == transaction.minimum_y
            and y_axis.get("center") == transaction.center_y
            and y_axis.get("maximum") == transaction.maximum_y
        )

    def _complete_calibration(self, config_result: dict) -> None:
        if not self._calibration_config_matches(config_result.get("config")):
            self._finish_calibration(
                CalibrationState.CONFLICT,
                "读回配置不是本次摇杆校准结果",
                clear_session=True,
            )
            return
        snapshot = self._model.snapshot
        if snapshot is None or self._contract is None:
            self._finish_calibration(
                CalibrationState.UNKNOWN,
                "校准已激活，但当前快照不可用",
                clear_session=True,
            )
            return
        generation = config_result.get("generation")
        digest = config_result.get("digest")
        updated = replace(
            snapshot,
            hello_config={"generation": generation, "digest": digest},
            config_result=dict(config_result),
        )
        candidate = LocalDraft.from_snapshot(updated, self._contract)
        self._draft = candidate
        self._drafts[candidate.key] = candidate
        state = AppState.READ_ONLY if updated.is_read_only else AppState.READY
        self._set(ScreenModel(state, updated.config_status_label, snapshot=updated))
        self.host_tasks.attach(updated, readback=True)
        self._finish_calibration(
            CalibrationState.COMPLETED,
            "摇杆校准已激活，并已通过 GET_CONFIG 读回确认",
            clear_session=True,
        )

    def _finish_calibration(
        self,
        state: CalibrationState,
        message: str,
        technical: str = "",
        *,
        clear_session: bool,
    ) -> None:
        self._calibration_sample_timer.stop()
        self._calibration_activation_deadline.stop()
        self._gateway.set_status_poll_interval(500)
        self._set_calibration(
            replace(
                self._calibration,
                state=state,
                message=message,
                technical=technical,
                session_id=0 if clear_session else self._calibration.session_id,
                cancel_requested=False,
            )
        )

    def _on_firmware_command_completed(self, command_name: str, payload: dict) -> None:
        transaction = self._firmware_update
        if not transaction.is_busy:
            return
        package = transaction.package
        if package is None:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "收到固件维护响应，但本地软件包上下文已经丢失",
            )
            return
        try:
            if command_name == "FW_STATUS":
                status = FirmwareStatus.from_payload(payload)
                if transaction.abort_requested:
                    if status.state == "IDLE":
                        self._finish_firmware_update(
                            FirmwareUpdateState.PACKAGE_READY,
                            "设备当前没有未完成的固件接收事务",
                        )
                    else:
                        self._gateway.execute_command(firmware_abort_command())
                    return
                self._handle_firmware_status(status)
                return
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise FirmwarePackageError(f"{command_name} 响应缺少 result object")
            if command_name == "FW_BEGIN":
                if result.get("state") != "RECEIVING":
                    raise FirmwarePackageError("FW_BEGIN 没有进入 RECEIVING")
                offset = result.get("offset")
                device_chunk = result.get("chunk_bytes")
                if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= package.size:
                    raise FirmwarePackageError("FW_BEGIN 返回的续传偏移无效")
                if not isinstance(device_chunk, int) or isinstance(device_chunk, bool) or device_chunk <= 0:
                    raise FirmwarePackageError("FW_BEGIN 返回的分片上限无效")
                target = result.get("target_partition")
                if target not in {"ota_0", "ota_1"}:
                    raise FirmwarePackageError("FW_BEGIN 返回的目标分区无效")
                updated = replace(
                    transaction,
                    state=(
                        FirmwareUpdateState.ABORTING
                        if transaction.abort_requested
                        else FirmwareUpdateState.TRANSFERRING
                    ),
                    message=(
                        "设备接收事务已建立，正在按用户请求中止"
                        if transaction.abort_requested
                        else "设备已准备接收固件"
                    ),
                    chunk_size=min(transaction.chunk_size, device_chunk),
                    received_size=offset,
                    target_partition=target,
                )
                self._set_firmware_update(updated)
                if transaction.abort_requested:
                    self._gateway.execute_command(firmware_abort_command())
                else:
                    self._send_next_firmware_chunk()
                return
            if command_name == "FW_DATA":
                received = result.get("received_size")
                expected = transaction.received_size + transaction.in_flight_size
                if received != expected or result.get("state") != "RECEIVING":
                    raise FirmwarePackageError("设备确认的固件分片偏移与本地传输进度不一致")
                updated = replace(
                    transaction,
                    state=(
                        FirmwareUpdateState.ABORTING
                        if transaction.abort_requested
                        else FirmwareUpdateState.TRANSFERRING
                    ),
                    message=(
                        "当前分片已确认，正在按用户请求中止"
                        if transaction.abort_requested
                        else f"正在传输固件：{received} / {package.size} 字节"
                    ),
                    received_size=received,
                    in_flight_size=0,
                )
                self._set_firmware_update(updated)
                if transaction.abort_requested:
                    self._gateway.execute_command(firmware_abort_command())
                else:
                    self._send_next_firmware_chunk()
                return
            if command_name == "FW_END":
                state = result.get("state")
                if result.get("version") != package.version or state not in {
                    "FINALIZING",
                    "READY_TO_REBOOT",
                }:
                    raise FirmwarePackageError("设备没有确认正在完成所选固件版本")
                if state == "READY_TO_REBOOT":
                    self._wait_for_firmware_reconnect()
                else:
                    self._start_firmware_finalization_wait()
                return
            if command_name == "FW_ABORT":
                if result.get("state") != "IDLE":
                    raise FirmwarePackageError("设备没有确认固件接收事务已中止")
                self._finish_firmware_update(
                    FirmwareUpdateState.PACKAGE_READY,
                    "设备中的未完成固件接收已清除；可重新开始所选软件包",
                )
        except FirmwarePackageError as exc:
            self._finish_firmware_update(FirmwareUpdateState.FAILED, "固件维护响应无效", str(exc))

    def _handle_firmware_status(self, status: FirmwareStatus) -> None:
        transaction = self._firmware_update
        package = transaction.package
        if package is None:
            return
        if transaction.state is FirmwareUpdateState.VERIFYING and status.state == "IDLE":
            # After reboot target_partition names the NEXT update partition.
            # Confirm this transaction against its saved destination instead.
            snapshot = self._model.snapshot
            versions = snapshot.versions if snapshot is not None else {}
            if (
                versions.get("firmware") != package.version
                or (package.build_id and versions.get("build_id") != package.build_id)
                or status.running_version != package.version
                or transaction.target_partition not in {"ota_0", "ota_1"}
                or status.running_partition != transaction.target_partition
            ):
                self._finish_firmware_update(
                    FirmwareUpdateState.FAILED,
                    "重连读回的版本、build_id 或运行分区与本次更新不一致；更新未生效或已经回滚",
                )
            elif status.rollback_pending is False:
                self._finish_firmware_update(
                    FirmwareUpdateState.COMPLETED,
                    "设备已重新连接，版本、build_id 和目标分区读回一致，已确认无需回滚",
                )
            else:
                self._set_firmware_update(
                    replace(transaction, message="新固件已运行，正在等待设备完成启动自检并取消回滚")
                )
                self._firmware_status_timer.start()
            return
        target = transaction.target_partition or status.target_partition
        if transaction.target_partition and status.target_partition and transaction.target_partition != status.target_partition:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "设备在维护过程中改变了目标 OTA 分区",
            )
            return
        base = replace(transaction, target_partition=target, restart_failed=False)
        restart_failed = (
            status.state == "FAILED"
            and transaction.state is FirmwareUpdateState.CHECKING
            and transaction.restart_failed
            and status.reboot_pending is False
        )
        if status.state == "FAILED" and not restart_failed:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "设备报告固件维护失败；请检查原因后点击重新安装，再建立接收事务",
            )
            return
        if status.state == "IDLE" or restart_failed:
            self._set_firmware_update(
                replace(base, state=FirmwareUpdateState.BEGINNING, restart_failed=False,
                        message="正在建立固件接收事务")
            )
            self._gateway.execute_command(package.begin_command())
            return
        if not status.matches(package):
            if status.state == "RECEIVING":
                self._set_firmware_update(
                    replace(
                        base,
                        state=FirmwareUpdateState.NEEDS_ABORT,
                        message="设备中存在另一份未完成的固件接收；必须由用户明确中止后才能继续",
                    )
                )
            else:
                self._finish_firmware_update(
                    FirmwareUpdateState.FAILED,
                    "设备正在完成另一份固件，不能用当前软件包接管",
                )
            return
        if not 0 <= status.received_size <= package.size:
            self._finish_firmware_update(FirmwareUpdateState.FAILED, "设备返回的固件续传偏移无效")
            return
        if status.state == "RECEIVING":
            self._set_firmware_update(
                replace(
                    base,
                    state=FirmwareUpdateState.TRANSFERRING,
                    message=f"从设备确认的 {status.received_size} 字节位置继续传输",
                    received_size=status.received_size,
                    in_flight_size=0,
                )
            )
            self._send_next_firmware_chunk()
        elif status.state == "FINALIZING":
            self._set_firmware_update(base)
            self._start_firmware_finalization_wait()
        elif status.state == "READY_TO_REBOOT":
            self._set_firmware_update(base)
            self._wait_for_firmware_reconnect()

    def _send_next_firmware_chunk(self) -> None:
        transaction = self._firmware_update
        package = transaction.package
        if package is None:
            return
        if transaction.received_size >= package.size:
            self._set_firmware_update(
                replace(transaction, state=FirmwareUpdateState.FINALIZING, message="镜像传输完成，正在让设备校验并准备重启")
            )
            self._gateway.execute_command(firmware_end_command())
            return
        try:
            command, chunk_size = package.data_command(
                transaction.received_size, transaction.chunk_size
            )
        except FirmwarePackageError as exc:
            self._finish_firmware_update(FirmwareUpdateState.FAILED, "无法读取下一段固件镜像", str(exc))
            return
        self._set_firmware_update(
            replace(
                transaction,
                state=FirmwareUpdateState.TRANSFERRING,
                message=f"正在传输固件：{transaction.received_size} / {package.size} 字节",
                in_flight_size=chunk_size,
            )
        )
        self._gateway.execute_command(command)

    def _start_firmware_finalization_wait(self) -> None:
        self._set_firmware_update(
            replace(
                self._firmware_update,
                state=FirmwareUpdateState.FINALIZING,
                message="设备正在校验完整镜像并准备新的 OTA 分区",
            )
        )
        if not self._firmware_deadline.isActive():
            self._firmware_deadline.start()
        self._firmware_status_timer.start()

    def _wait_for_firmware_reconnect(self) -> None:
        self._firmware_status_timer.stop()
        self._set_firmware_update(
            replace(
                self._firmware_update,
                state=FirmwareUpdateState.WAITING_RECONNECT,
                message="设备已完成镜像校验，等待重启后核对版本和 build_id",
            )
        )
        self._firmware_deadline.start()

    def _poll_firmware_status(self) -> None:
        if self._firmware_update.state in {
            FirmwareUpdateState.FINALIZING,
            FirmwareUpdateState.VERIFYING,
        }:
            self._gateway.execute_command(firmware_status_command())

    def _on_firmware_deadline(self) -> None:
        if self._firmware_update.state is FirmwareUpdateState.FINALIZING:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "设备未在期限内完成固件镜像校验",
            )
        elif self._firmware_update.state is FirmwareUpdateState.WAITING_RECONNECT:
            self._set_firmware_update(
                replace(
                    self._firmware_update,
                    state=FirmwareUpdateState.VERIFYING,
                    message="尚未观察到设备自动重连，正在重新扫描并核对运行版本",
                )
            )
            self._firmware_deadline.start()
            self.refresh()
        elif self._firmware_update.state is FirmwareUpdateState.VERIFYING:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "未在期限内确认新固件的运行分区和回滚状态；更新结果尚未确认",
            )

    def _on_firmware_command_failed(self, command_name: str, error: BootstrapError) -> None:
        if command_name in {"FW_BEGIN", "FW_DATA"} and error.error_name == "TRANSPORT_TIMEOUT":
            self._set_firmware_update(
                replace(
                    self._firmware_update,
                    state=FirmwareUpdateState.CHECKING,
                    message="固件命令 ACK 未确认，正在通过 FW_STATUS 对账续传位置",
                    technical=error.technical,
                    in_flight_size=0,
                )
            )
            self._gateway.execute_command(firmware_status_command())
            return
        if command_name == "FW_END" and error.error_name == "TRANSPORT_TIMEOUT":
            self._set_firmware_update(
                replace(
                    self._firmware_update,
                    state=FirmwareUpdateState.VERIFYING,
                    message="FW_END 结果未知；将通过重连后的版本和 FW_STATUS 对账，不重复开始传输",
                    technical=error.technical,
                )
            )
            self._firmware_deadline.start()
            self.refresh()
            return
        self._finish_firmware_update(
            FirmwareUpdateState.FAILED,
            "设备拒绝固件维护命令",
            f"{command_name}: {error.technical}",
        )

    def _pause_firmware_update(self, message: str, technical: str) -> None:
        self._firmware_status_timer.stop()
        state = self._firmware_update.state
        if state in {FirmwareUpdateState.FINALIZING, FirmwareUpdateState.WAITING_RECONNECT}:
            state = FirmwareUpdateState.WAITING_RECONNECT
        elif state is not FirmwareUpdateState.VERIFYING:
            state = FirmwareUpdateState.PAUSED
        if state is FirmwareUpdateState.PAUSED:
            self._firmware_deadline.stop()
        elif not self._firmware_deadline.isActive():
            self._firmware_deadline.start()
        self._set_firmware_update(
            replace(self._firmware_update, state=state, message=message, technical=technical, in_flight_size=0)
        )

    def _reconcile_snapshot_after_firmware(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._firmware_update
        package = transaction.package
        if package is None or transaction.state not in {
            FirmwareUpdateState.PAUSED,
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
            FirmwareUpdateState.WAITING_RECONNECT,
            FirmwareUpdateState.VERIFYING,
        }:
            return
        serial = snapshot.identity.get("serial")
        if serial != transaction.serial:
            self._finish_firmware_update(
                FirmwareUpdateState.FAILED,
                "重新连接的是另一台设备，已停止固件维护",
            )
            return
        try:
            chunk_size = package.validate_for_device(snapshot)
        except FirmwarePackageError as exc:
            self._finish_firmware_update(FirmwareUpdateState.FAILED, "重连设备与固件包不匹配", str(exc))
            return
        if transaction.state in {FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.VERIFYING}:
            self._set_firmware_update(
                replace(
                    transaction,
                    state=FirmwareUpdateState.VERIFYING,
                    chunk_size=chunk_size,
                    message="设备已重连，正在核对运行版本、目标分区和回滚状态",
                )
            )
            if not self._firmware_deadline.isActive():
                self._firmware_deadline.start()
        else:
            self._set_firmware_update(
                replace(transaction, state=FirmwareUpdateState.CHECKING, chunk_size=chunk_size)
            )
        self._gateway.set_status_poll_interval(60_000)
        self._gateway.execute_command(firmware_status_command())

    def _finish_firmware_update(
        self,
        state: FirmwareUpdateState,
        message: str,
        technical: str = "",
    ) -> None:
        self._firmware_status_timer.stop()
        self._firmware_deadline.stop()
        self._gateway.set_status_poll_interval(500)
        self._set_firmware_update(
            replace(
                self._firmware_update,
                state=state,
                message=message,
                technical=technical,
                in_flight_size=0,
                abort_requested=False,
            )
        )
        if (
            state is FirmwareUpdateState.COMPLETED
            and self._remote_firmware.state is RemoteFirmwareState.DOWNLOADED
        ):
            release = self._remote_firmware.release
            self._set_remote_firmware(
                RemoteFirmwareCheck(
                    state=RemoteFirmwareState.CURRENT,
                    message=(
                        f"在线发布版本 {release.version} 已安装并读回确认"
                        if release is not None
                        else "在线发布固件已安装并读回确认"
                    ),
                    release=release,
                )
            )

    def _on_write_deadline(self) -> None:
        if self._write_transaction.state in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
        }:
            self._request_config_readback("10 秒内未确认激活，正在进行最后一次读回对账")

    def _request_config_readback(self, message: str) -> None:
        self._write_deadline.stop()
        self._set_write_transaction(
            replace(
                self._write_transaction,
                state=ConfigTransactionState.VERIFYING,
                message=message,
            )
        )
        self._gateway.execute_command(Command("GET_CONFIG", 0x10, {}))

    def _reconcile_status_after_write(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._write_transaction
        active = snapshot.status.get("active")
        if (transaction.state in {ConfigTransactionState.VALIDATING, ConfigTransactionState.AWAITING_CONFIRMATION}
                and isinstance(active, dict) and transaction.base_generation is not None
                and active.get("generation") != transaction.base_generation):
            self._finish_write(ConfigTransactionState.CONFLICT, "确认写入前设备配置已变化；正在读取最新配置")
            return
        if transaction.state not in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
            ConfigTransactionState.UNKNOWN,
        }:
            return
        if snapshot.identity.get("serial") != transaction.device_serial:
            return
        if snapshot.status.get("activation_failed") is True:
            self._finish_write(ConfigTransactionState.FAILED, "设备报告候选配置激活失败")
            return
        active = snapshot.status.get("active")
        pending = snapshot.status.get("pending")
        if isinstance(active, dict) and active.get("digest") == transaction.candidate_digest:
            if transaction.state is not ConfigTransactionState.VERIFYING:
                self._request_config_readback("候选配置已激活，正在读回完整配置确认")
            return
        if isinstance(pending, dict) and pending.get("digest") == transaction.candidate_digest:
            neutral = snapshot.status.get("inputs_neutral") is True
            self._set_write_transaction(
                replace(
                    transaction,
                    state=ConfigTransactionState.PENDING,
                    message="候选配置等待激活" if neutral else "请松开所有实体控件，设备随后激活配置",
                )
            )
            return
        active_generation = active.get("generation") if isinstance(active, dict) else None
        if (
            isinstance(active_generation, int)
            and transaction.base_generation is not None
            and active_generation > transaction.base_generation
        ):
            self._finish_write(ConfigTransactionState.CONFLICT, "其他配置已先于当前候选生效")

    def _reconcile_snapshot_after_write(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._write_transaction
        if not transaction.candidate_digest or transaction.state not in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
            ConfigTransactionState.UNKNOWN,
        }:
            return
        if snapshot.identity.get("serial") != transaction.device_serial:
            self._finish_write(
                ConfigTransactionState.UNKNOWN,
                "当前连接的不是原设备；原设备写入结果仍未知，请连接原设备读回确认",
            )
            return
        active = snapshot.status.get("active")
        pending = snapshot.status.get("pending")
        if snapshot.status.get("activation_failed") is True:
            self._finish_write(ConfigTransactionState.FAILED, "设备报告候选配置激活失败")
            return
        if (
            isinstance(active, dict)
            and active.get("digest") == transaction.candidate_digest
            and snapshot.config_result.get("digest") == transaction.candidate_digest
        ):
            self._reconcile_config_result(snapshot.config_result)
            return
        if isinstance(pending, dict) and pending.get("digest") == transaction.candidate_digest:
            self._gateway.set_status_poll_interval(250)
            if not self._write_deadline.isActive():
                self._write_deadline.start()
            self._set_write_transaction(
                replace(transaction, state=ConfigTransactionState.PENDING, message="重连后找到待激活候选配置")
            )
            return
        generation = snapshot.config_result.get("generation")
        digest = snapshot.config_result.get("digest")
        if (
            isinstance(generation, int)
            and transaction.base_generation is not None
            and generation > transaction.base_generation
        ):
            self._finish_write(ConfigTransactionState.CONFLICT, "重连后发现设备已激活其他配置")
            return
        if generation == transaction.base_generation and digest == transaction.base_digest:
            self._finish_write(
                ConfigTransactionState.FAILED,
                "重连读回确认设备仍是写入前配置；本次候选未落盘，可以重新验证",
            )
            return
        self._finish_write(
            ConfigTransactionState.UNKNOWN,
            "重连读回未发现当前候选配置；没有自动重试写入",
        )

    def _reconcile_config_result(self, result: dict) -> None:
        transaction = self._write_transaction
        config = result.get("config")
        digest = result.get("digest")
        generation = result.get("generation")
        snapshot = self._model.snapshot
        if snapshot is None or snapshot.identity.get("serial") != transaction.device_serial:
            self._finish_write(
                ConfigTransactionState.UNKNOWN,
                "当前连接的不是原设备；原设备写入结果仍未知，请连接原设备读回确认",
            )
            return
        pending = snapshot.status.get("pending") if snapshot is not None else None
        matches = (
            isinstance(config, dict)
            and digest == transaction.candidate_digest
            and configs_match_readback(transaction.candidate_config, config)
            and pending is None
            and isinstance(generation, int)
            and transaction.base_generation is not None
            and generation > transaction.base_generation
        )
        if matches:
            if snapshot.status.get("activation_failed") is True:
                self._finish_write(ConfigTransactionState.FAILED, "设备报告候选配置激活失败")
            else:
                self._complete_write(result)
        elif isinstance(pending, dict) and pending.get("digest") == transaction.candidate_digest:
            self._gateway.set_status_poll_interval(250)
            if not self._write_deadline.isActive():
                self._write_deadline.start()
            self._set_write_transaction(
                replace(
                    transaction,
                    state=ConfigTransactionState.PENDING,
                    message="读回时确认候选仍在等待激活",
                )
            )
        elif (
            isinstance(generation, int)
            and transaction.base_generation is not None
            and generation > transaction.base_generation
        ):
            self._finish_write(ConfigTransactionState.CONFLICT, "读回时发现设备已激活其他配置")
        elif generation == transaction.base_generation and digest == transaction.base_digest:
            self._finish_write(
                ConfigTransactionState.FAILED,
                "读回确认设备仍是写入前配置；本次候选未落盘，可以重新验证",
            )
        else:
            self._finish_write(ConfigTransactionState.UNKNOWN, "无法确认候选配置是否生效；没有自动重试写入")

    def _complete_write(self, config_result: dict) -> None:
        snapshot = self._model.snapshot
        if snapshot is None:
            self._finish_write(ConfigTransactionState.UNKNOWN, "设备已写入但当前快照不可用")
            return
        generation = config_result.get("generation")
        digest = config_result.get("digest")
        updated = replace(
            snapshot,
            hello_config={"generation": generation, "digest": digest},
            config_result=dict(config_result),
        )
        if self._contract is None:
            self._finish_write(ConfigTransactionState.UNKNOWN, "配置已写入但共享配置 Schema 不可用")
            return
        candidate = LocalDraft.from_snapshot(updated, self._contract)
        previous = self._draft
        if (previous is not None and self._drafts.get(previous.key) is previous
                and configs_match_readback(previous.config, config_result["config"])):
            # This cached workspace has now been applied and confirmed.
            self._drafts.pop(previous.key)
        self._draft = candidate
        self._drafts[candidate.key] = candidate
        self._write_deadline.stop()
        self._gateway.set_status_poll_interval(500)
        self._set_write_transaction(
            replace(
                self._write_transaction,
                state=ConfigTransactionState.ACTIVE,
                message="配置已激活，并已通过 GET_CONFIG 读回确认",
                technical="",
            )
        )
        state = AppState.READ_ONLY if updated.is_read_only else AppState.READY
        self._set(ScreenModel(state, updated.config_status_label, snapshot=updated))
        self.host_tasks.attach(updated, readback=True)

    def _finish_write(
        self,
        state: ConfigTransactionState,
        message: str,
        technical: str = "",
    ) -> None:
        self._confirm_write_after_validation = False
        self._write_deadline.stop()
        self._gateway.set_status_poll_interval(500)
        self._set_write_transaction(
            replace(self._write_transaction, state=state, message=message, technical=technical)
        )
