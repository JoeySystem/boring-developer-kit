"""Desktop Agent lights in Console: the hub, its apps and their slot memory.

Replaces the old DoubaoBridge in MainViewModel (design decision: firmware no longer
has the `doubao` source). MainViewModel calls the same session points as
before (split doc §8): bind on each authenticated snapshot, unbind when the
session ends, every GET_STATUS through ``consume_status`` with the port it was
read on, ``connection_changed`` from the ConnectionController, ``set_paused``
while writes, calibration or firmware maintenance own the device, and
``shutdown`` on exit (CLEAR).

Decisions applied here:
* Capability gate: no ``desktop_agent_status`` in CAPABILITIES -> nothing is
  sent and ``availability`` is ``needs_firmware`` (copy belongs to task 7).
* Polling: the hub's ``status_poll_requested`` is deliberately not connected.
  The transport polls GET_STATUS every 250 ms while a desktop lease is held,
  USB and BLE alike (the firmware owner 11-04: one place decides; firmware will report the
  measured BLE value later, design decision). Firmware updates set their own
  intervals undisturbed.
* Firmware maintenance (OTA) pauses the source: one CLEAR, no further SET,
  the lights return to Codex.
* No conversation on any light -> no lease (design decision).
* Slots are remembered per device and per Doubao account.
* The user can switch Doubao off (minimal settings page, 开发包 d-20261106):
  remembered in QSettings, default on. Off sends one CLEAR (the lights go
  back to Codex) and stops reading DoubaoWork until it is switched on again.
* Claude desktop's local Code sessions are a second app on the same hub
  (design decision, task): ``attach_claude`` registers them
  while the Claude status lights are on (its Hooks opt-in). The hub runs
  while either app is on; switching one off removes only its lights. Claude
  needs no slot memory: its sessions are known only while Console runs.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QSettings, Signal

from controller_config import desktop_slot_memory
from controller_config.desktop_agent_hub import SLOTS, DesktopAgentHub
from controller_config.claude_desktop_adapter import OPEN_FRONT_ONLY as CLAUDE_FRONT_ONLY

# availability codes (stable; the settings page turns them into copy)
DISABLED = "disabled"
UNAVAILABLE_NO_DEVICE = "no_device"
NEEDS_FIRMWARE = "needs_firmware"
PAUSED = "paused"
FAULT = "fault"
DEVICE_BUSY = "device_busy"  # device read-only or busy: retrying on its own
APP_NOT_RUNNING = "app_not_running"
# H17: Doubao runs but its state can no longer be read (an update changed the
# store or the log): the lights may be wrong, the page must not say "working".
SOURCE_BROKEN = "source_broken"
ACTIVE = "active"

# QSettings key of the user's on/off choice (absent: on).
ENABLED_KEY = "desktop_agents/doubao_enabled"


def cleanup_stale_store_copies(temp_root=None) -> int:
    """H16: remove Doubao store copies a crash, kill or power loss left behind.

    Called once by the app at start-up, before anything is copied, whether or
    not Doubao Link is on (it only deletes Console's own temporary copies and
    never reads Doubao). Never raises: start-up goes on regardless.
    """
    try:
        from controller_config.chromium_leveldb import sweep_stale_copies
    except ImportError:  # community build: the Doubao store reader is not included
        return 0
    try:
        return sweep_stale_copies(temp_root)
    except Exception:  # noqa: BLE001 - best effort; the next start tries again
        return 0


def _doubao_adapter(parent):
    from controller_config.doubao_adapter import DoubaoAdapter
    return DoubaoAdapter.for_platform(parent=parent)


class DesktopAgentLink(QObject):
    changed = Signal()
    # M23: a light was pressed but Doubao did not end on that conversation;
    # the reason in product language (the app shows it as a notice).
    open_failed = Signal(str)

    def __init__(self, gateway, *, adapter_factory=None, settings=None,
                 clock=time.monotonic, use_timer=True, parent=None):
        super().__init__(parent)
        self.hub = DesktopAgentHub(gateway, clock=clock, use_timer=use_timer,
                                   release_when_empty=True, parent=self)
        self._adapter_factory = adapter_factory or _doubao_adapter
        self._settings = settings
        self.adapter = None
        self.serial = ""
        self._paused = False
        self._restored_for: tuple[str, str] | None = None
        self._saved: tuple | None = None
        # M23: why the last press did not open its conversation ("" after a success).
        self.last_open_problem = ""
        self.claude = None  # ClaudeDesktopAdapter once attach_claude ran
        self._claude_bridge = None
        self._claude_on = False
        self._doubao_registered = False
        self.hub.changed.connect(self._on_hub_changed)
        self.hub.open_failed.connect(self._on_open_failed)
        self.hub.opened.connect(self._on_opened)
        self.enabled = self._load_enabled()
        if self.enabled:
            self.hub.enable()

    @property
    def settings(self):
        if self._settings is None:
            self._settings = QSettings()
        return self._settings

    # ---- session points (MainViewModel) --------------------------------------

    def bind(self, snapshot) -> None:
        self.serial = str(snapshot.identity.get("serial", "")) if snapshot.identity else ""
        self.hub.bind(snapshot)
        if self.hub.supported and self.enabled:
            self._ensure_adapter()
            self._restore()
        self.changed.emit()

    def unbind(self) -> None:
        self.hub.unbind()
        if self.adapter is not None:
            self.adapter.stop()
        self.changed.emit()

    def connection_changed(self, port_name: str) -> None:
        self.hub.connection_changed(port_name)

    def consume_status(self, status: dict, *, port_name: str | None = None) -> None:
        self.hub.consume_status(status, port_name=port_name)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.hub.set_paused(paused)

    def shutdown(self) -> None:
        self._save()
        self.hub.shutdown()
        if self.adapter is not None:
            self.adapter.close()
        if self.claude is not None:
            self.claude.close()

    # ---- Claude (second app on the hub) ---------------------------------------

    def attach_claude(self, bridge, *, adapter=None) -> None:
        """Show Claude desktop Code sessions on the lights while ``bridge`` is on."""
        if adapter is None:
            from controller_config.claude_desktop_adapter import ClaudeDesktopAdapter
            adapter = ClaudeDesktopAdapter(bridge.registry, parent=self)
        self.claude = adapter
        self._claude_bridge = bridge
        bridge.delivery = _ClaudeDelivery(self)
        bridge.sessions_changed.connect(self.hub.refresh)
        bridge.changed.connect(self._sync_claude)
        adapter.changed.connect(self.hub.refresh)
        self.hub.changed.connect(bridge._notify)
        self._sync_claude()

    def _sync_claude(self) -> None:
        on = bool(self._claude_bridge is not None and self._claude_bridge.enabled)
        if on == self._claude_on:
            return
        self._claude_on = on
        if on:
            self.hub.register(self.claude, self.claude)
            self._sync_hub()
        else:
            if not self.enabled:
                self.hub.disable()  # nothing else on: CLEAR, the lights go back to Codex
            self.hub.unregister(self.claude.app_id)
        self.changed.emit()

    def _sync_hub(self) -> None:
        if (self.enabled or self._claude_on) and not self.hub.enabled:
            self.hub.enable()

    @property
    def claude_lights_in_use(self) -> int:
        return sum(1 for key in self.hub.allocator.slots
                   if key is not None and key[0] == "claude")

    # ---- user choices (settings page) ----------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        """Switch Doubao on or off and remember the choice.

        Off: one CLEAR so the device shows Codex again, no further SET, and
        DoubaoWork is no longer read. On: reading resumes on a device that
        supports the desktop source and the lights follow Doubao again.
        """
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self.settings.setValue(ENABLED_KEY, enabled)
        if enabled:
            self._sync_hub()
            if self.hub.bound and self.hub.supported:
                self._ensure_adapter()
                self._restore()
        else:
            self._save()
            if not self._claude_on:
                self.hub.disable()  # CLEAR now: the lights go back to Codex.
            if self._doubao_registered:
                # Claude keeps its lights; only Doubao's go.
                self._doubao_registered = False
                self.hub.unregister(self.adapter.app_id)
            if self.adapter is not None:
                self.adapter.stop()
        self.changed.emit()

    def pin(self, slot: int, conversation_id: str) -> None:
        if self.adapter is None:
            raise ValueError("桌面 App 尚未接入，不能固定对话")
        self.hub.pin(slot, self.adapter.app_id, conversation_id)

    def unpin(self, slot: int) -> None:
        self.hub.unpin(slot)

    def retry(self) -> None:
        self.hub.retry()

    # ---- status for the UI and diagnostics -----------------------------------

    @property
    def availability(self) -> str:
        hub = self.hub
        if not self.enabled:
            return DISABLED
        if not hub.bound:
            return UNAVAILABLE_NO_DEVICE
        if not hub.supported:
            return NEEDS_FIRMWARE
        if hub.fault:
            return FAULT
        if self._paused:
            return PAUSED
        if hub.device_wait:
            return DEVICE_BUSY
        if self.adapter is None or not self.adapter.running:
            return APP_NOT_RUNNING
        if self.adapter.problems():
            return SOURCE_BROKEN
        return ACTIVE

    @property
    def fault_reason(self) -> str:
        """Why the hub stopped (desktop_agent_hub FAULT_* code, "" when running)."""
        return self.hub.fault_reason if self.hub.fault else ""

    @property
    def fault_detail(self) -> str:
        """Technical text of the fault or of the device's current refusal."""
        if self.hub.fault:
            return self.hub.fault_detail
        if self.hub.device_wait:
            return self.hub.device_wait
        if self.adapter is not None and self.adapter.running:
            return " ".join(self.adapter.problems())  # H17: codes only, no IDs.
        return ""

    @property
    def lights_in_use(self) -> int:
        """Lights showing a Doubao conversation right now."""
        return sum(1 for key in self.hub.allocator.slots
                   if key is not None and self.adapter is not None
                   and key[0] == self.adapter.app_id)

    def stats(self) -> dict:
        """Privacy-safe summary: counts, states and reason codes only."""
        states, seq = self.hub.wire()
        return {"availability": self.availability, "enabled": self.enabled, "states": list(states),
                "completion_seq": list(seq), "lease_active": self.hub.lease_active,
                "transport": self.hub.transport, "pinned": sorted(self.hub.allocator.pins),
                "queue": len(self.hub.allocator.queue),
                "app": self.adapter.stats() if self.adapter is not None else None}

    # ---- internals -----------------------------------------------------------

    def _load_enabled(self) -> bool:
        value = self.settings.value(ENABLED_KEY, True)
        if isinstance(value, str):  # INI/plist backends hand back text
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)

    def _ensure_adapter(self) -> None:
        if self.adapter is None:
            self.adapter = self._adapter_factory(self)
            self.adapter.account_changed.connect(lambda _account: self._restore())
            self.adapter.changed.connect(self.hub.refresh)
        if not self._doubao_registered:
            self._doubao_registered = True
            self.hub.register(self.adapter, self.adapter)
        self.adapter.start()

    def _memory_key(self) -> tuple[str, str] | None:
        if self.adapter is None or not self.serial or not self.adapter.account:
            return None
        return self.serial, self.adapter.account

    def _restore(self) -> None:
        key = self._memory_key()
        if key is None or key == self._restored_for:
            return
        try:
            saved = desktop_slot_memory.load(self.settings, key[0], self.adapter.app_id, key[1])
        except ValueError:
            saved = None
        self._restored_for = key
        slots, pins = saved if saved is not None else ((None,) * SLOTS, {})
        self._saved = (key, slots, pins)
        # Another account's lights (or none) replace what this app showed.
        self.hub.restore(self.adapter.app_id, slots, pins)

    def _save(self) -> None:
        key = self._memory_key()
        if key is None or key != self._restored_for or not self.adapter.running:
            return
        slots, pins = self.hub.assignment(self.adapter.app_id)
        if not any(slots) and not pins:
            return  # DoubaoWork reports nothing right now: keep what is saved.
        if self._saved == (key, slots, pins):
            return
        try:
            desktop_slot_memory.save(self.settings, key[0], self.adapter.app_id, key[1], slots, pins)
        except ValueError:
            return
        self._saved = (key, slots, pins)

    def _on_open_failed(self, app_id: str, message: str) -> None:
        if app_id != "claude":  # the settings page's line is Doubao's
            self.last_open_problem = message
        elif message == CLAUDE_FRONT_ONLY:
            # the designer 11-16: Claude came to the front, which is what the
            # press is for; no notice. Only a failed switch is shown.
            self.changed.emit()
            return
        self.open_failed.emit(message)
        self.changed.emit()

    def _on_opened(self, app_id: str, _conversation_id: str) -> None:
        if app_id != "claude" and self.last_open_problem:
            self.last_open_problem = ""
            self.changed.emit()

    def _on_hub_changed(self) -> None:
        self._save()
        self.changed.emit()


class _ClaudeDelivery:
    """What the Claude settings page shows about the lights on the device."""

    def __init__(self, link: DesktopAgentLink):
        self._link = link

    @property
    def fault(self) -> bool:
        return self._link.hub.fault

    def retry(self) -> None:
        self._link.hub.retry()

    def slot_of(self, session_id: str) -> int | None:
        key = ("claude", session_id)
        slots = self._link.hub.allocator.slots
        return slots.index(key) if key in slots else None

    def message(self) -> str:
        hub = self._link.hub
        if hub.fault:
            return "状态转发失败，可重试；设备将在租约结束后清除状态"
        if not hub.bound:
            return "等待设备连接并通过身份检查"
        if not hub.supported:
            return "当前固件不支持桌面 App 状态灯，请升级固件"
        if self._link._paused:
            return "固件维护期间暂停状态转发"
        if not hub._normal:
            return "状态灯只在普通模式下显示，双击旋钮切回普通模式"
        return "状态联动已开启"
