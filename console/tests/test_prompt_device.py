from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from controller_config.prompt_device import (
    PromptDeviceSession,
    PromptDeviceState,
    PromptListenerState,
    delete_prompt_command,
    get_prompt_command,
    get_prompt_event_command,
    get_prompt_list_command,
    set_prompt_command,
)
from controller_config.automation import EventDispatchResult
from controller_config.prompt_library import PromptEntry, PromptLibrary, PromptLibraryStore
from controller_config.prompt_helper import PromptPasteError
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.transport.demo import _power_v2_snapshot


def _prompt_snapshot(contract):
    snapshot = _power_v2_snapshot(contract, read_only=False)
    capabilities = snapshot.capabilities
    return replace(
        snapshot,
        capabilities={
            **capabilities,
            "limits": {
                **capabilities["limits"],
                "prompt_slots": 12,
                "prompt_name_bytes": 48,
                "prompt_body_bytes": 4096,
                "all_prompt_body_bytes": 49152,
            },
            "actions": [*capabilities["actions"], "prompt"],
            "features": {
                **capabilities["features"],
                "prompt_storage": True,
                "prompt_trigger_usb": True,
            },
        },
    )


def _entry_payload(entry: PromptEntry) -> dict:
    return {
        "command": "GET_PROMPT",
        "result": {**entry.as_mapping(), "body_bytes": entry.body_bytes},
    }


def test_prompt_commands_match_shared_wire_values() -> None:
    entry = PromptEntry(3, "翻译", "翻译成英文")

    assert (get_prompt_list_command().message_type, get_prompt_list_command().payload) == (
        0x17,
        {},
    )
    assert (get_prompt_command(3).message_type, get_prompt_command(3).payload) == (
        0x18,
        {"prompt_id": 3},
    )
    assert set_prompt_command(entry).message_type == 0x19
    assert set_prompt_command(entry).payload == entry.as_mapping()
    assert delete_prompt_command(3).message_type == 0x1A
    assert get_prompt_event_command().message_type == 0x1B


def test_full_device_read_fetches_each_body_and_preserves_local_dirty_slot(
    qtbot, contract, tmp_path
) -> None:
    commands = []
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    library.set_draft(1, "本地草稿", "尚未写入")
    session = PromptDeviceSession(commands.append, PromptLibraryStore(tmp_path))
    first = PromptEntry(1, "设备一", "设备正文一")
    second = PromptEntry(2, "设备二", "设备正文二")

    session.bind(library, _prompt_snapshot(contract))
    assert commands.pop(0).name == "GET_PROMPT_LIST"
    session.handle_completed(
        "GET_PROMPT_LIST",
        {
            "command": "GET_PROMPT_LIST",
            "result": {
                "prompts": [
                    {"prompt_id": 1, "name": first.name, "body_bytes": first.body_bytes},
                    {"prompt_id": 2, "name": second.name, "body_bytes": second.body_bytes},
                ]
            },
        },
    )
    assert commands.pop(0).payload == {"prompt_id": 1}
    session.handle_completed("GET_PROMPT", _entry_payload(first))
    assert commands.pop(0).payload == {"prompt_id": 2}
    session.handle_completed("GET_PROMPT", _entry_payload(second))

    assert session.status.state is PromptDeviceState.IDLE
    assert library.confirmed == (first, second)
    assert library.draft_entry(1).body == "尚未写入"
    assert library.draft_entry(2) == second
    assert library.dirty_prompt_ids == (1,)


def test_write_requires_exact_get_prompt_readback(qtbot, contract, tmp_path) -> None:
    commands = []
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    session = PromptDeviceSession(commands.append, PromptLibraryStore(tmp_path))
    session.bind(library, _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_LIST",
        {"command": "GET_PROMPT_LIST", "result": {"prompts": []}},
    )
    draft = library.set_draft(4, "审查", "先给结论")

    session.write_draft(4)
    sent = commands.pop(0)
    assert (sent.name, sent.payload) == ("SET_PROMPT", draft.as_mapping())
    session.handle_completed(
        "SET_PROMPT",
        {
            "command": "SET_PROMPT",
            "result": {
                "prompt_id": 4,
                "name": draft.name,
                "body_bytes": draft.body_bytes,
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"
    session.handle_completed("GET_PROMPT", _entry_payload(draft))

    assert session.status.state is PromptDeviceState.IDLE
    assert library.confirmed_entry(4) == draft
    assert library.dirty_prompt_ids == ()


def test_write_waits_for_in_flight_event_poll_before_starting(
    qtbot, contract, tmp_path
) -> None:
    commands = []
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    draft = library.set_draft(4, "审查", "先给结论")
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=lambda _event: EventDispatchResult(False, False, ""),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(library, _prompt_snapshot(contract))
    session._event_timer.stop()
    session._poll_event()
    assert commands.pop(0).name == "GET_PROMPT_EVENT"

    session.write_draft(4)
    assert commands == []
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {"poll_after_ms": 100, "event": None},
        },
    )

    sent = commands.pop(0)
    assert (sent.name, sent.payload) == ("SET_PROMPT", draft.as_mapping())
    session.handle_completed(
        "SET_PROMPT",
        {
            "command": "SET_PROMPT",
            "result": {
                "prompt_id": 4,
                "name": draft.name,
                "body_bytes": draft.body_bytes,
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"
    session.handle_completed("GET_PROMPT", _entry_payload(draft))

    assert session.status.state is PromptDeviceState.IDLE
    assert library.confirmed_entry(4) == draft


def test_write_starts_after_in_flight_event_poll_failure(
    qtbot, contract, tmp_path
) -> None:
    commands = []
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    draft = library.set_draft(4, "审查", "先给结论")
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=lambda _event: EventDispatchResult(False, False, ""),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(library, _prompt_snapshot(contract))
    session._event_timer.stop()
    session._poll_event()
    assert commands.pop(0).name == "GET_PROMPT_EVENT"

    session.write_draft(4)
    assert commands == []
    session.handle_failed(
        "GET_PROMPT_EVENT",
        BootstrapError(
            BootstrapKind.READ_FAILED,
            "设备事件读取失败",
            "poll failed",
        ),
    )

    sent = commands.pop(0)
    assert (sent.name, sent.payload) == ("SET_PROMPT", draft.as_mapping())


def test_delete_is_confirmed_by_absence_from_readback_list(
    qtbot, contract, tmp_path
) -> None:
    commands = []
    entry = PromptEntry(5, "删除", "删除这个")
    library = PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(entry,))
    session = PromptDeviceSession(commands.append, PromptLibraryStore(tmp_path))
    session.bind(library, _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_LIST",
        {"command": "GET_PROMPT_LIST", "result": {"prompts": []}},
    )
    library.confirm_entry(entry)

    session.delete_confirmed(5)
    assert commands.pop(0).name == "DELETE_PROMPT"
    session.handle_completed(
        "DELETE_PROMPT",
        {
            "command": "DELETE_PROMPT",
            "result": {"prompt_id": 5, "deleted": True},
        },
    )
    assert commands.pop(0).name == "GET_PROMPT_LIST"
    session.handle_completed(
        "GET_PROMPT_LIST",
        {"command": "GET_PROMPT_LIST", "result": {"prompts": []}},
    )

    assert session.status.state is PromptDeviceState.IDLE
    assert library.confirmed_entry(5) is None


def test_prompt_event_reads_exact_device_body_before_host_paste(
    qtbot, contract, tmp_path
) -> None:
    class Helper:
        def __init__(self) -> None:
            self.calls = []

        def handle_entry(self, entry):
            self.calls.append((entry.body, entry.prompt_id))
            return SimpleNamespace(body_bytes=12)

    helper = Helper()
    entry = PromptEntry(1, "运行", "中文提示词")
    library = PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(entry,))
    commands = []
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        helper=helper,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(library, _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 7, "prompt_id": 1},
            },
        },
    )

    assert helper.calls == []
    assert commands.pop(0).payload == {"prompt_id": 1}
    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert helper.calls == [("中文提示词", 1)]
    assert "事件 7" in session.helper_message
    assert session.listener_status.state is PromptListenerState.READY
    assert session.listener_status.online
    assert any("事件 7" in entry.message for entry in session.event_log)


def test_prompt_event_can_be_claimed_by_host_event_dispatcher(
    qtbot, contract, tmp_path
) -> None:
    events = []

    def dispatch(event):
        events.append(event)
        return EventDispatchResult(True, True, "已交给本地脚本")

    entry = PromptEntry(3, "运行脚本", "作为事件上下文")
    library = PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(entry,))
    commands = []
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=dispatch,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(library, _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 12, "prompt_id": 3},
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"

    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert len(events) == 1
    assert events[0].kind == "prompt.triggered"
    assert events[0].source == "usb.prompt"
    assert events[0].device_serial == "CP01-AABBCCDDEEFF"
    assert events[0].payload["prompt_body"] == "作为事件上下文"
    assert session.helper_message == "已交给本地脚本"


def test_async_extension_claim_pauses_event_poll_until_settled(
    qtbot, contract, tmp_path
) -> None:
    commands = []
    pending = []
    entry = PromptEntry(4, "Extension", "host action")

    def dispatch(_event):
        return EventDispatchResult(False, False, "")

    def dispatch_async(event, remaining_ms, completed):
        pending.append((event, remaining_ms, completed))

    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=dispatch,
        async_event_dispatcher=dispatch_async,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))
    commands.clear()
    session._event_timer.stop()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 13, "prompt_id": 4},
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"

    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert len(pending) == 1
    assert 0 < pending[0][1] <= 1350
    assert not session._event_timer.isActive()
    pending[0][2](EventDispatchResult(True, True, "extension accepted"))
    assert session._event_timer.isActive()
    assert session.helper_message == "extension accepted"


def test_late_async_claim_after_unbind_is_ignored(qtbot, contract, tmp_path) -> None:
    pending = []
    entry = PromptEntry(5, "Extension", "host action")
    session = PromptDeviceSession(
        lambda _command: None,
        PromptLibraryStore(tmp_path),
        event_dispatcher=lambda _event: EventDispatchResult(False, False, ""),
        async_event_dispatcher=lambda event, remaining, completed: pending.append(
            completed
        ),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))
    session._event_timer.stop()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 14, "prompt_id": 5},
            },
        },
    )
    session.handle_completed("GET_PROMPT", _entry_payload(entry))
    assert len(pending) == 1

    session.unbind()
    pending[0](EventDispatchResult(True, True, "late"))

    assert session.listener_status.state is PromptListenerState.OFFLINE
    assert session.helper_message != "late"


def test_listener_becomes_online_only_after_first_successful_event_poll(
    qtbot, contract, tmp_path
) -> None:
    helper = SimpleNamespace(handle_entry=lambda entry: None)
    session = PromptDeviceSession(
        lambda _command: None,
        PromptLibraryStore(tmp_path),
        helper=helper,
        auto_refresh=False,
        auto_poll=True,
    )

    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))

    assert session.listener_status.state is PromptListenerState.STARTING
    assert not session.listener_status.online
    session._event_timer.stop()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {"poll_after_ms": 100, "event": None},
        },
    )

    assert session.listener_status.state is PromptListenerState.READY
    assert session.listener_status.online
    assert "监听" in session.listener_status.message


def test_transient_event_poll_failure_retries_and_recovers_listener(
    qtbot, contract, tmp_path
) -> None:
    helper = SimpleNamespace(handle_entry=lambda entry: None)
    session = PromptDeviceSession(
        lambda _command: None,
        PromptLibraryStore(tmp_path),
        helper=helper,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))
    session._event_timer.stop()

    session.handle_failed(
        "GET_PROMPT_EVENT",
        SimpleNamespace(error_name="TRANSPORT_TIMEOUT", technical="poll timeout"),
    )

    assert session.status.state is PromptDeviceState.IDLE
    assert session.listener_status.state is PromptListenerState.RETRYING
    assert not session.listener_status.online
    assert session._event_timer.isActive()
    assert any("poll timeout" in entry.technical for entry in session.event_log)

    session._event_timer.stop()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {"poll_after_ms": 100, "event": None},
        },
    )

    assert session.listener_status.state is PromptListenerState.READY
    assert session.listener_status.online


def test_failed_event_read_does_not_capture_later_library_refresh(
    qtbot, contract, tmp_path
) -> None:
    class Helper:
        def __init__(self) -> None:
            self.calls = []

        def handle_entry(self, entry):
            self.calls.append(entry)
            return SimpleNamespace(body_bytes=entry.body_bytes)

    helper = Helper()
    library = PromptLibrary("CP01-AABBCCDDEEFF")
    commands = []
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        helper=helper,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(library, _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 9, "prompt_id": 2},
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"
    session.handle_failed(
        "GET_PROMPT",
        SimpleNamespace(error_name="TRANSPORT_TIMEOUT", technical="timeout"),
    )
    assert session._event_timer.isActive()

    entry = PromptEntry(2, "设备内容", "稍后读取")
    session.refresh()
    assert commands.pop(0).name == "GET_PROMPT_LIST"
    session.handle_completed(
        "GET_PROMPT_LIST",
        {
            "command": "GET_PROMPT_LIST",
            "result": {
                "prompts": [
                    {"prompt_id": 2, "name": entry.name, "body_bytes": entry.body_bytes}
                ]
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"
    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert helper.calls == []
    assert library.confirmed_entry(2) == entry


def test_unicode_paste_failure_is_logged_without_taking_listener_offline(
    qtbot, contract, tmp_path
) -> None:
    class FailingHelper:
        def handle_entry(self, _entry):
            raise PromptPasteError(
                "提示词已复制到剪贴板，但无法粘贴；请开启 macOS 辅助功能权限"
            )

    entry = PromptEntry(3, "中文", "准确粘贴中文")
    session = PromptDeviceSession(
        lambda _command: None,
        PromptLibraryStore(tmp_path),
        helper=FailingHelper(),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(
        PromptLibrary("CP01-AABBCCDDEEFF", confirmed=(entry,)),
        _prompt_snapshot(contract),
    )
    session._event_timer.stop()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 11, "prompt_id": 3},
            },
        },
    )
    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert session.listener_status.online
    assert "剪贴板" in session.helper_message
    failure = session.event_log[-1]
    assert failure.level == "error"
    assert failure.event_id == 11
    assert "辅助功能" in failure.technical


def test_background_polling_can_pause_for_other_device_transactions(
    qtbot, contract, tmp_path
) -> None:
    helper = SimpleNamespace(handle_entry=lambda entry: None)
    session = PromptDeviceSession(
        lambda _command: None,
        PromptLibraryStore(tmp_path),
        helper=helper,
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))

    assert session._event_timer.isActive()
    session.set_polling_paused(True)
    assert not session._event_timer.isActive()
    assert session.listener_status.state is PromptListenerState.PAUSED
    session.set_polling_paused(False)
    assert session._event_timer.isActive()
    assert session.listener_status.state is PromptListenerState.STARTING


def test_pause_discards_a_late_event_poll_response(
    qtbot, contract, tmp_path
) -> None:
    events = []
    commands = []
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=lambda event: events.append(event),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))
    session._event_timer.stop()
    session._poll_event()
    assert commands.pop(0).name == "GET_PROMPT_EVENT"

    session.set_polling_paused(True)
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 21, "prompt_id": 1},
            },
        },
    )

    assert commands == []
    assert events == []
    assert session.listener_status.state is PromptListenerState.PAUSED

    session.set_polling_paused(False)
    assert session._event_timer.isActive()


def test_pause_discards_a_late_prompt_read_for_an_event(
    qtbot, contract, tmp_path
) -> None:
    events = []
    commands = []
    entry = PromptEntry(1, "运行", "不要在事务中执行")
    session = PromptDeviceSession(
        commands.append,
        PromptLibraryStore(tmp_path),
        event_dispatcher=lambda event: events.append(event),
        auto_refresh=False,
        auto_poll=True,
    )
    session.bind(PromptLibrary("CP01-AABBCCDDEEFF"), _prompt_snapshot(contract))
    commands.clear()
    session.handle_completed(
        "GET_PROMPT_EVENT",
        {
            "command": "GET_PROMPT_EVENT",
            "result": {
                "poll_after_ms": 100,
                "event": {"event_id": 22, "prompt_id": 1},
            },
        },
    )
    assert commands.pop(0).name == "GET_PROMPT"

    session.set_polling_paused(True)
    session.handle_completed("GET_PROMPT", _entry_payload(entry))

    assert events == []
    assert commands == []
    assert session.listener_status.state is PromptListenerState.PAUSED
