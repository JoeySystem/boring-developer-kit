from __future__ import annotations

import json
import uuid

import pytest
from PySide6.QtNetwork import QLocalSocket

from controller_config.extensions.contracts import (
    ActionInvocation,
    ExtensionManifest,
    SemanticEvent,
    SetMappingProposalResult,
)
from controller_config.extensions.local_api import ExtensionLocalApiServer


EXTENSION_ID = "com.example.prompt-tools"
SERIAL = "CP01-AABBCCDDEEFF"
DIGEST = "a" * 64
_RETAINED_SERVERS: list[ExtensionLocalApiServer] = []


def _context() -> dict[str, object]:
    return {
        "schema_version": 1,
        "revision": 4,
        "device_serial": SERIAL,
        "connection_state": "ready",
        "identity": {
            "product_id": "wired-macro-pad-v1",
            "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
            "serial": SERIAL,
        },
        "compatibility": {"read": True, "write": True, "reason": ""},
        "capabilities": {
            "controls": ["key.12"],
            "actions": ["key", "prompt"],
        },
        "status": {"state": "active", "operating_mode": "normal"},
        "active_profile": {"id": 0, "name": "Default"},
        "config_summary": {
            "schema_version": 1,
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
        "proposal_id": "proposal-1",
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
            "name": "Prompt tools",
            "version": "1.0.0",
            "api_version": {"major": 1, "minor": 0},
            "entrypoint": "main.py",
            "observer_events": ["prompt.triggered"],
            "actions": [{"id": "use_prompt", "name": "Use prompt"}],
        }
    )
    return ActionInvocation.from_mapping(
        {
            "schema_version": 1,
            "invocation_id": "invocation-1",
            "extension_id": EXTENSION_ID,
            "action_id": "use_prompt",
            "device_serial": SERIAL,
            "context_revision": 4,
            "event": _event().as_mapping(),
        },
        manifest=manifest,
    )


def _connect(qtbot, server_name: str) -> QLocalSocket:
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    qtbot.waitUntil(
        lambda: socket.state() == QLocalSocket.LocalSocketState.ConnectedState
    )
    return socket


def _send(qtbot, socket: QLocalSocket, value: dict[str, object]) -> None:
    payload = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    assert socket.write(payload) == len(payload)
    socket.flush()
    qtbot.waitUntil(lambda: socket.bytesToWrite() == 0)


def _receive(qtbot, socket: QLocalSocket) -> dict[str, object]:
    qtbot.waitUntil(lambda: socket.canReadLine())
    value = json.loads(bytes(socket.readLine()).decode("utf-8"))
    assert isinstance(value, dict)
    return value


def _handshake(qtbot, socket: QLocalSocket, extension_id: str = EXTENSION_ID):
    _send(
        qtbot,
        socket,
        {
            "request_id": "hello-1",
            "kind": "handshake",
            "extension_id": extension_id,
            "api_version": {"major": 1, "minor": 0},
        },
    )
    return _receive(qtbot, socket)


def _server(contract, *, authorized=None, proposals=None, observer_events=None):
    allowed = {EXTENSION_ID} if authorized is None else authorized
    declared_events = (
        {EXTENSION_ID: frozenset({"prompt.triggered"})}
        if observer_events is None
        else {EXTENSION_ID: frozenset(observer_events)}
    )
    proposal_log = [] if proposals is None else proposals

    def handle(proposal):
        proposal_log.append(proposal)
        return SetMappingProposalResult(
            proposal_id=proposal.proposal_id,
            status="pending",
            message="waiting for review",
        )

    server = ExtensionLocalApiServer(
        f"boring-extension-test-{uuid.uuid4().hex}",
        contract=contract,
        authorize_extension=lambda extension_id: extension_id in allowed,
        allowed_observer_events=lambda extension_id: declared_events.get(
            extension_id, frozenset()
        ),
        context_provider=_context,
        proposal_handler=handle,
    )
    server.start()
    # Keep the QObject wrapper alive until QApplication teardown. Destroying a
    # just-closed QLocalServer between tests can invalidate queued macOS socket
    # notifications before pytest-qt drains them.
    _RETAINED_SERVERS.append(server)
    return server


def test_handshake_is_required_and_only_authorized_identity_is_accepted(
    qtbot, contract
) -> None:
    server = _server(contract)
    sockets: list[QLocalSocket] = []
    try:
        missing_handshake = _connect(qtbot, server.server_name)
        sockets.append(missing_handshake)
        _send(
            qtbot,
            missing_handshake,
            {"request_id": "context-1", "kind": "get_context"},
        )
        error = _receive(qtbot, missing_handshake)
        assert error["code"] == "handshake_required"

        unauthorized = _connect(qtbot, server.server_name)
        sockets.append(unauthorized)
        result = _handshake(qtbot, unauthorized, "com.example.not-installed")
        assert result["kind"] == "handshake.result"
        assert result["accepted"] is False

        accepted = _connect(qtbot, server.server_name)
        sockets.append(accepted)
        result = _handshake(qtbot, accepted)
        assert result["accepted"] is True
        assert result["api_version"] == {"major": 1, "minor": 0}
    finally:
        for socket in sockets:
            socket.abort()
        server.close()


def test_second_api_server_does_not_replace_a_live_primary(qtbot, contract) -> None:
    primary = _server(contract)
    secondary = ExtensionLocalApiServer(
        primary.server_name,
        contract=contract,
        authorize_extension=lambda extension_id: extension_id == EXTENSION_ID,
        allowed_observer_events=lambda _extension_id: frozenset(
            {"prompt.triggered"}
        ),
        context_provider=_context,
        proposal_handler=lambda proposal: SetMappingProposalResult(
            proposal_id=proposal.proposal_id,
            status="pending",
            message="waiting for review",
        ),
    )
    _RETAINED_SERVERS.append(secondary)
    socket = None
    try:
        with pytest.raises(RuntimeError, match="already running"):
            secondary.start()
        assert primary.is_listening
        socket = _connect(qtbot, primary.server_name)
        assert _handshake(qtbot, socket)["accepted"] is True
    finally:
        if socket is not None:
            socket.abort()
        secondary.close()
        primary.close()


def test_authorized_session_reads_context_and_subscribes_to_live_events(
    qtbot, contract
) -> None:
    server = _server(contract)
    socket = None
    try:
        socket = _connect(qtbot, server.server_name)
        assert _handshake(qtbot, socket)["accepted"] is True

        _send(qtbot, socket, {"request_id": "context-1", "kind": "get_context"})
        response = _receive(qtbot, socket)
        assert response == {
            "request_id": "context-1",
            "kind": "context.result",
            "context": _context(),
        }

        _send(
            qtbot,
            socket,
            {
                "request_id": "subscribe-1",
                "kind": "subscribe_events",
                "events": ["prompt.triggered"],
            },
        )
        assert _receive(qtbot, socket)["subscribed"] == ["prompt.triggered"]

        assert server.broadcast_event(_event()) == 1
        notification = _receive(qtbot, socket)
        assert notification == {"kind": "event", "event": _event().as_mapping()}
    finally:
        if socket is not None:
            socket.abort()
        server.close()


def test_subscription_requires_manifest_observer_declaration(qtbot, contract) -> None:
    server = _server(contract, observer_events=())
    socket = None
    try:
        socket = _connect(qtbot, server.server_name)
        assert _handshake(qtbot, socket)["accepted"] is True
        _send(
            qtbot,
            socket,
            {
                "request_id": "subscribe-undeclared",
                "kind": "subscribe_events",
                "events": ["prompt.triggered"],
            },
        )

        response = _receive(qtbot, socket)

        assert response["kind"] == "error"
        assert response["code"] == "event_not_declared"
        assert server.broadcast_event(_event()) == 0
    finally:
        if socket is not None:
            socket.abort()
        server.close()


def test_second_authenticated_session_for_extension_is_rejected(
    qtbot, contract
) -> None:
    server = _server(contract)
    first = None
    second = None
    try:
        first = _connect(qtbot, server.server_name)
        assert _handshake(qtbot, first)["accepted"] is True
        second = _connect(qtbot, server.server_name)

        result = _handshake(qtbot, second)

        assert result["accepted"] is False
        assert "authenticated session" in result["message"]
        _send(qtbot, first, {"request_id": "context-live", "kind": "get_context"})
        assert _receive(qtbot, first)["kind"] == "context.result"
    finally:
        if first is not None:
            first.abort()
        if second is not None:
            second.abort()
        server.close()


def test_proposal_is_schema_validated_and_bound_to_authorized_identity(
    qtbot, contract
) -> None:
    proposals = []
    server = _server(contract, proposals=proposals)
    socket = None
    try:
        socket = _connect(qtbot, server.server_name)
        assert _handshake(qtbot, socket)["accepted"] is True
        _send(
            qtbot,
            socket,
            {
                "request_id": "proposal-request-1",
                "kind": "propose_mapping",
                "proposal": _proposal(),
            },
        )
        response = _receive(qtbot, socket)
        assert response["kind"] == "proposal.result"
        assert response["result"]["status"] == "pending"
        assert len(proposals) == 1
        assert proposals[0].control_id == "key.12"

        wrong_identity = _proposal()
        wrong_identity["extension_id"] = "com.example.someone-else"
        _send(
            qtbot,
            socket,
            {
                "request_id": "proposal-request-2",
                "kind": "propose_mapping",
                "proposal": wrong_identity,
            },
        )
        response = _receive(qtbot, socket)
        assert response["kind"] == "error"
        assert response["code"] == "invalid_proposal"
        assert len(proposals) == 1
    finally:
        if socket is not None:
            socket.abort()
        server.close()


def test_observer_subscription_does_not_expose_claim_or_raw_command(
    qtbot, contract
) -> None:
    server = _server(contract)
    socket = None
    try:
        socket = _connect(qtbot, server.server_name)
        assert _handshake(qtbot, socket)["accepted"] is True
        for kind in ("claim_event", "send_raw_command", "install_firmware"):
            _send(
                qtbot,
                socket,
                {"request_id": f"request-{kind}", "kind": kind},
            )
            response = _receive(qtbot, socket)
            assert response["kind"] == "error"
            assert response["code"] == "unknown_request"
    finally:
        if socket is not None:
            socket.abort()
        server.close()


def test_console_invokes_only_authorized_action_session_and_receives_acceptance(
    qtbot, contract
) -> None:
    server = _server(contract)
    socket = _connect(qtbot, server.server_name)
    results = []
    try:
        assert _handshake(qtbot, socket)["accepted"] is True
        invocation = _invocation()

        assert server.invoke_action(invocation, results.append)
        notification = _receive(qtbot, socket)
        assert notification == {
            "kind": "action.invoke",
            "invocation": invocation.as_mapping(),
        }

        _send(
            qtbot,
            socket,
            {
                "request_id": "action-result-1",
                "kind": "action_result",
                "result": {
                    "schema_version": 1,
                    "invocation_id": "invocation-1",
                    "status": "accepted",
                    "message": "accepted",
                },
            },
        )
        assert _receive(qtbot, socket)["kind"] == "action_result.result"
        assert [item.status for item in results] == ["accepted"]
        assert server._pending_actions == {}
        assert server._accepted_actions == {"invocation-1": EXTENSION_ID}

        _send(
            qtbot,
            socket,
            {
                "request_id": "action-result-2",
                "kind": "action_result",
                "result": {
                    "schema_version": 1,
                    "invocation_id": "invocation-1",
                    "status": "completed",
                    "message": "done",
                },
            },
        )
        assert _receive(qtbot, socket)["accepted"] is True
        assert [item.status for item in results] == ["accepted"]
        assert server._accepted_actions == {}
    finally:
        socket.abort()
        server.close()
