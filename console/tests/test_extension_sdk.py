from __future__ import annotations

import sys
import json
import os
import uuid
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess, QProcessEnvironment

from controller_config.extensions.contracts import (
    ActionInvocation,
    ExtensionManifest,
    SemanticEvent,
    SetMappingProposalResult,
)
from controller_config.extensions.local_api import ExtensionLocalApiServer


SDK_ROOT = Path(__file__).resolve().parents[2] / "sdk" / "python"
sys.path.insert(0, str(SDK_ROOT))

from boring_console_sdk import (  # noqa: E402
    BoringConsoleClient,
    BoringConsoleError,
    RequestError,
)


EXTENSION_ID = "com.example.sdk"
SERIAL = "CP01-AABBCCDDEEFF"
DIGEST = "a" * 64
_RETAINED_SERVERS: list[ExtensionLocalApiServer] = []


def _context() -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": 4,
        "device_serial": SERIAL,
        "connection_state": "ready",
        "identity": {"serial": SERIAL},
        "compatibility": {"read": True, "write": True},
        "capabilities": {"controls": ["key.12"], "actions": ["key"]},
        "status": {"state": "active"},
        "active_profile": {"id": 0, "name": "Default"},
        "config_summary": {
            "generation": 6,
            "digest": DIGEST,
            "profile_count": 1,
        },
        "draft": {"dirty": False, "change_count": 0},
        "prompt_listener": {"state": "online", "online": True},
    }


def _event() -> SemanticEvent:
    body = "你好 BORING"
    return SemanticEvent.from_mapping(
        {
            "schema_version": 1,
            "kind": "prompt.triggered",
            "source": "usb.prompt",
            "device_serial": SERIAL,
            "event_id": 7,
            "payload": {
                "prompt_id": 2,
                "prompt_name": "快捷提示词",
                "prompt_body": body,
                "body_bytes": len(body.encode("utf-8")),
            },
        }
    )


def _proposal() -> dict[str, object]:
    return {
        "schema_version": 1,
        "proposal_id": "proposal-sdk-1",
        "extension_id": EXTENSION_ID,
        "device_serial": SERIAL,
        "base_generation": 6,
        "base_digest": DIGEST,
        "profile_id": 0,
        "control_id": "key.12",
        "action": {"type": "key", "usage": 40, "modifiers": []},
    }


def _invocation() -> ActionInvocation:
    manifest = ExtensionManifest.from_mapping(
        {
            "manifest_version": 1,
            "id": EXTENSION_ID,
            "name": "SDK action",
            "version": "1.0.0",
            "api_version": {"major": 1, "minor": 0},
            "entrypoint": "main.py",
            "observer_events": ["prompt.triggered"],
            "actions": [{"id": "run", "name": "Run"}],
        }
    )
    return ActionInvocation.from_mapping(
        {
            "schema_version": 1,
            "invocation_id": "invocation-sdk-1",
            "extension_id": EXTENSION_ID,
            "action_id": "run",
            "device_serial": SERIAL,
            "context_revision": 4,
            "event": _event().as_mapping(),
        },
        manifest=manifest,
    )


def _server(contract, proposals):
    def handle(proposal):
        proposals.append(proposal)
        return SetMappingProposalResult(
            proposal_id=proposal.proposal_id,
            status="pending",
            message="waiting for review",
        )

    server = ExtensionLocalApiServer(
        f"boring-sdk-test-{uuid.uuid4().hex}",
        contract=contract,
        authorize_extension=lambda extension_id: extension_id == EXTENSION_ID,
        allowed_observer_events=lambda extension_id: (
            frozenset({"prompt.triggered"})
            if extension_id == EXTENSION_ID
            else frozenset()
        ),
        context_provider=_context,
        proposal_handler=handle,
    )
    server.start()
    _RETAINED_SERVERS.append(server)
    return server


def _sdk_process(
    source: str, *, server_name: str, extension_id: str
) -> QProcess:
    process = QProcess()
    environment = QProcessEnvironment.systemEnvironment()
    existing = environment.value("PYTHONPATH")
    value = str(SDK_ROOT) if not existing else f"{SDK_ROOT}{os.pathsep}{existing}"
    environment.insert("PYTHONPATH", value)
    environment.insert("BORING_EXTENSION_API_SERVER", server_name)
    environment.insert("BORING_EXTENSION_ID", extension_id)
    process.setProcessEnvironment(environment)
    process.start(sys.executable, ["-c", source])
    return process


def test_sdk_context_subscription_event_and_proposal_round_trip(
    qtbot, contract
) -> None:
    proposals = []
    server = _server(contract, proposals)
    source = f"""
import json
from boring_console_sdk import BoringConsoleClient
proposal = {repr(_proposal())}
with BoringConsoleClient.from_environment() as client:
    print(json.dumps({{"context": client.get_context()}}, ensure_ascii=False), flush=True)
    print(json.dumps({{"subscribed": client.subscribe_events()}}, ensure_ascii=False), flush=True)
    event = client.next_event(timeout_ms=2000)
    result = client.propose_mapping(proposal)
    print(json.dumps({{"event": event, "proposal": result}}, ensure_ascii=False), flush=True)
"""
    process = _sdk_process(
        source, server_name=server.server_name, extension_id=EXTENSION_ID
    )
    try:
        qtbot.waitUntil(process.canReadLine, timeout=3_000)
        context_line = json.loads(bytes(process.readLine()).decode("utf-8"))
        qtbot.waitUntil(process.canReadLine, timeout=3_000)
        subscribed_line = json.loads(bytes(process.readLine()).decode("utf-8"))
        assert subscribed_line == {"subscribed": ["prompt.triggered"]}
        assert server.broadcast_event(_event()) == 1
        qtbot.waitUntil(
            lambda: process.state() == QProcess.ProcessState.NotRunning,
            timeout=5_000,
        )
        output = bytes(process.readAllStandardOutput()).decode("utf-8").strip()
        error = bytes(process.readAllStandardError()).decode("utf-8")
        assert process.exitCode() == 0, error
        result = json.loads(output)
        assert context_line["context"] == _context()
        assert result["event"] == _event().as_mapping()
        assert result["proposal"]["status"] == "pending"
        assert len(proposals) == 1
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1_000)
        server.close()


def test_sdk_rejects_unauthorized_identity(qtbot, contract) -> None:
    server = _server(contract, [])
    process = _sdk_process(
        f"""
from boring_console_sdk import BoringConsoleClient
BoringConsoleClient.from_environment().connect()
""",
        server_name=server.server_name,
        extension_id="com.example.unknown",
    )
    try:
        qtbot.waitUntil(
            lambda: process.state() == QProcess.ProcessState.NotRunning,
            timeout=3_000,
        )
        error = bytes(process.readAllStandardError()).decode("utf-8")
        assert process.exitCode() != 0
        assert "authorized" in error
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1_000)
        server.close()


def test_sdk_has_no_public_raw_transport_or_device_command_methods() -> None:
    public = {name for name in dir(BoringConsoleClient) if not name.startswith("_")}

    assert "write" not in public
    assert "request" not in public
    assert "send_command" not in public
    assert "install_firmware" not in public
    assert RequestError.__name__ == "RequestError"


def test_sdk_notification_queues_are_bounded_by_kind() -> None:
    client = BoringConsoleClient("test-server", EXTENSION_ID)
    for index in range(256):
        client._queue_notification({"kind": "event", "event": {"id": index}})

    with pytest.raises(BoringConsoleError, match="event notification queue is full"):
        client._queue_notification({"kind": "event", "event": {"id": 257}})

    client._queue_notification(
        {"kind": "action.invoke", "invocation": {"invocation_id": "one"}}
    )
    assert len(client._notifications["action.invoke"]) == 1


def test_sdk_receives_action_and_reports_accepted_then_completed(
    qtbot, contract
) -> None:
    server = _server(contract, [])
    source = """
import json
from boring_console_sdk import BoringConsoleClient
with BoringConsoleClient.from_environment() as client:
    client.subscribe_events()
    print('ready', flush=True)
    event = client.next_event(timeout_ms=3000)
    invocation = client.next_action(timeout_ms=3000)
    print(json.dumps({"event": event, "invocation": invocation}), flush=True)
    client.respond_action(invocation['invocation_id'], 'accepted', 'accepted')
    client.respond_action(invocation['invocation_id'], 'completed', 'done')
"""
    process = _sdk_process(
        source, server_name=server.server_name, extension_id=EXTENSION_ID
    )
    results = []
    try:
        qtbot.waitUntil(process.canReadLine, timeout=3_000)
        assert bytes(process.readLine()).decode("utf-8").strip() == "ready"
        assert server.invoke_action(_invocation(), results.append)
        assert server.broadcast_event(_event()) == 1
        qtbot.waitUntil(
            lambda: process.state() == QProcess.ProcessState.NotRunning,
            timeout=5_000,
        )
        assert process.exitCode() == 0, bytes(process.readAllStandardError()).decode(
            "utf-8"
        )
        result = json.loads(bytes(process.readLine()).decode("utf-8"))
        assert result["event"] == _event().as_mapping()
        assert result["invocation"]["action_id"] == "run"
        assert [item.status for item in results] == ["accepted"]
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1_000)
        server.close()


def test_sdk_host_event_and_cancel_arriving_during_accept_request(qtbot, contract):
    server = _server(contract, [])
    event = SemanticEvent.from_mapping({
        'schema_version': 1, 'kind': 'host_action.triggered', 'source': 'usb.host_action',
        'device_serial': SERIAL, 'event_id': 8, 'payload': {'action_id': 255, 'task_token': '0123456789abcdef0123456789abcdef'},
    })
    invocation = ActionInvocation('cancel-sdk', EXTENSION_ID, 'run', SERIAL, 4, event)
    results = []
    server.action_result_received.connect(lambda ext, result: results.append(result.status))
    process = _sdk_process('''
import json
from boring_console_sdk import BoringConsoleClient
with BoringConsoleClient.from_environment() as client:
    print('ready', flush=True)
    invocation = client.next_action(timeout_ms=3000)
    client.respond_action(invocation['invocation_id'], 'accepted')
    cancellation = client.next_cancellation(timeout_ms=3000)
    assert cancellation['invocation_id'] == invocation['invocation_id']
    client.respond_action(invocation['invocation_id'], 'failed', '已停止')
    print(json.dumps(invocation['event']), flush=True)
''', server_name=server.server_name, extension_id=EXTENSION_ID)
    try:
        qtbot.waitUntil(process.canReadLine, timeout=3000)
        assert bytes(process.readLine()).strip() == b'ready'
        assert server.invoke_action(invocation, lambda result: server.request_cancel_invocation(result.invocation_id))
        qtbot.waitUntil(lambda: process.state() == QProcess.ProcessState.NotRunning, timeout=5000)
        assert process.exitCode() == 0, bytes(process.readAllStandardError()).decode()
        assert json.loads(bytes(process.readLine())) == event.as_mapping()
        assert results == ['accepted', 'failed']
        assert server.active_action_count == 0
    finally:
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1000)
        server.close()


def test_sdk_host_event_rejects_prompt_payload_and_wrong_reference():
    event = {'schema_version': 1, 'kind': 'host_action.triggered', 'source': 'usb.host_action',
             'device_serial': SERIAL, 'event_id': 1, 'payload': {'action_id': 1, 'task_token': '0123456789abcdef0123456789abcdef'}}
    assert BoringConsoleClient._event_value({'event': event}) == event
    for payload in ({'prompt_id': 1}, {'action_id': True}, {'action_id': 256}, {'action_id': 1, 'prompt_body': ''}):
        with pytest.raises(RequestError, match='host_action'):
            BoringConsoleClient._event_value({'event': {**event, 'payload': payload}})
