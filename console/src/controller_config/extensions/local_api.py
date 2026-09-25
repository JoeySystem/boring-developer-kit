from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import TypeAlias

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from controller_config.extensions.contracts import (
    API_MAJOR,
    API_MINOR,
    PROMPT_EVENT_KIND,
    ActionInvocation,
    ActionInvocationResult,
    ApiHandshakeRequest,
    ApiHandshakeResult,
    ApiVersion,
    ExtensionContext,
    ExtensionContractError,
    SemanticEvent,
    SetMappingProposal,
    SetMappingProposalResult,
)
from controller_config.protocol.contract import Contract


ContextProvider: TypeAlias = Callable[[], ExtensionContext | Mapping[str, object]]
ProposalHandler: TypeAlias = Callable[
    [SetMappingProposal], SetMappingProposalResult | Mapping[str, object]
]
AuthorizationCallback: TypeAlias = Callable[[str], bool]
ObserverEventsCallback: TypeAlias = Callable[[str], Collection[str]]
ActionCompleted: TypeAlias = Callable[[ActionInvocationResult], None]

_MAX_LINE_BYTES = 256 * 1024
_MAX_ACCEPTED_ACTIONS = 256
_SUPPORTED_OBSERVER_EVENTS = frozenset({PROMPT_EVENT_KIND, "host_action.triggered"})


@dataclass
class _ApiSession:
    socket: QLocalSocket
    buffer: bytearray = field(default_factory=bytearray)
    extension_id: str | None = None
    subscribed_events: set[str] = field(default_factory=set)
    api_minor: int = 0


@dataclass
class _PendingAction:
    extension_id: str
    completed: ActionCompleted


class ExtensionLocalApiServer(QObject):
    """Console-owned, JSON Lines local API for authorized extensions.

    The server deliberately receives only value providers and proposal handlers.
    It cannot access the serial gateway or issue a device/firmware command.
    """

    session_opened = Signal(str)
    session_closed = Signal(str)
    session_error = Signal(str, str)
    action_result_received = Signal(str, object)

    def __init__(
        self,
        server_name: str,
        *,
        contract: Contract,
        authorize_extension: AuthorizationCallback,
        allowed_observer_events: ObserverEventsCallback,
        context_provider: ContextProvider,
        proposal_handler: ProposalHandler,
        max_pending_bytes: int = _MAX_LINE_BYTES,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not server_name.strip():
            raise ValueError("local API server name cannot be empty")
        if max_pending_bytes < 1024:
            raise ValueError("max_pending_bytes must be at least 1024")
        self._server_name = server_name
        self._contract = contract
        self._authorize_extension = authorize_extension
        self._allowed_observer_events = allowed_observer_events
        self._context_provider = context_provider
        self._proposal_handler = proposal_handler
        self._max_pending_bytes = max_pending_bytes
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connections)
        self._sessions: dict[QLocalSocket, _ApiSession] = {}
        self._pending_actions: dict[str, _PendingAction] = {}
        self._accepted_actions: OrderedDict[str, str] = OrderedDict()
        self._owns_server_name = False

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def full_server_name(self) -> str:
        return self._server.fullServerName()

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def start(self) -> None:
        if self._server.isListening():
            return
        if self._server.listen(self._server_name):
            self._owns_server_name = True
            return

        # On Unix a crashed process can leave the local socket path behind.
        # Never remove a live primary's endpoint: probe it first, then perform
        # Qt's documented stale-server cleanup only when no process answers.
        probe = QLocalSocket(self)
        probe.connectToServer(self._server_name)
        if probe.waitForConnected(200):
            probe.disconnectFromServer()
            raise RuntimeError("BORING extension API is already running")
        QLocalServer.removeServer(self._server_name)
        if not self._server.listen(self._server_name):
            raise RuntimeError(
                f"cannot start BORING extension API: {self._server.errorString()}"
            )
        self._owns_server_name = True

    def close(self) -> None:
        self.stop_accepting()
        self._fail_pending_actions("BORING Console is shutting down")
        for socket in tuple(self._sessions):
            socket.abort()
            self._drop_session(socket)
        if self._owns_server_name:
            QLocalServer.removeServer(self._server_name)
            self._owns_server_name = False

    def stop_accepting(self) -> None:
        self._server.close()

    def has_session(self, extension_id: str) -> bool:
        return any(
            session.extension_id == extension_id
            and session.socket.state()
            == QLocalSocket.LocalSocketState.ConnectedState
            for session in self._sessions.values()
        )

    @property
    def active_action_count(self) -> int:
        return len(self._pending_actions) + len(self._accepted_actions)

    @property
    def active_extension_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            [pending.extension_id for pending in self._pending_actions.values()]
            + list(self._accepted_actions.values())
        ))

    def invoke_action(
        self,
        invocation: ActionInvocation,
        completed: ActionCompleted,
    ) -> bool:
        if invocation.invocation_id in self._pending_actions or invocation.invocation_id in self._accepted_actions:
            raise ValueError(f"duplicate invocation_id {invocation.invocation_id}")
        session = next(
            (
                item
                for item in self._sessions.values()
                if item.extension_id == invocation.extension_id
                and self._authorize_extension(invocation.extension_id)
            ),
            None,
        )
        if session is None or self.active_action_count >= _MAX_ACCEPTED_ACTIONS:
            return False
        if invocation.event.kind == "host_action.triggered" and session.api_minor < 1:
            return False
        self._pending_actions[invocation.invocation_id] = _PendingAction(
            extension_id=invocation.extension_id,
            completed=completed,
        )
        if not self._send(
            session,
            {"kind": "action.invoke", "invocation": invocation.as_mapping()},
        ):
            self._pending_actions.pop(invocation.invocation_id, None)
            return False
        return True

    def request_cancel_invocation(self, invocation_id: str) -> bool:
        """Request cooperation without discarding the eventual terminal result."""
        pending = self._pending_actions.get(invocation_id)
        extension_id = pending.extension_id if pending else self._accepted_actions.get(invocation_id)
        session = next((item for item in self._sessions.values()
                        if item.extension_id == extension_id and item.api_minor >= 1), None)
        if extension_id is None or session is None:
            return False
        return self._send(session, {"kind": "action.cancel", "invocation_id": invocation_id})

    def cancel_invocation(self, invocation_id: str) -> None:
        self._pending_actions.pop(invocation_id, None)
        self._accepted_actions.pop(invocation_id, None)

    def broadcast_event(self, event: SemanticEvent | Mapping[str, object]) -> int:
        public_event = (
            event
            if isinstance(event, SemanticEvent)
            else SemanticEvent.from_mapping(event)
        )
        message = {"kind": "event", "event": public_event.as_mapping()}
        delivered = 0
        for session in tuple(self._sessions.values()):
            extension_id = session.extension_id
            if (
                extension_id is None
                or public_event.kind not in session.subscribed_events
                or not self._authorize_extension(extension_id)
            ):
                continue
            if self._send(session, message):
                delivered += 1
        return delivered

    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.setParent(self)
            session = _ApiSession(socket=socket)
            self._sessions[socket] = session
            socket.readyRead.connect(
                lambda current=socket: self._read_available(current)
            )
            socket.disconnected.connect(
                lambda current=socket: self._drop_session(current)
            )
            if socket.bytesAvailable():
                self._read_available(socket)

    def _read_available(self, socket: QLocalSocket) -> None:
        session = self._sessions.get(socket)
        if session is None:
            return
        session.buffer.extend(bytes(socket.readAll()))
        if (
            len(session.buffer) > self._max_pending_bytes
            and b"\n" not in session.buffer
        ):
            self._fail_session(session, "request_too_large", "request is too large")
            return
        while b"\n" in session.buffer and socket in self._sessions:
            raw_line, _, remainder = session.buffer.partition(b"\n")
            session.buffer = bytearray(remainder)
            if not raw_line.strip():
                continue
            if len(raw_line) > self._max_pending_bytes:
                self._fail_session(
                    session, "request_too_large", "request is too large"
                )
                return
            self._handle_line(session, bytes(raw_line))

    def _handle_line(self, session: _ApiSession, raw_line: bytes) -> None:
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._fail_session(session, "invalid_json", "request must be UTF-8 JSON")
            return
        if not isinstance(value, dict):
            self._fail_session(session, "invalid_request", "request must be an object")
            return

        request_id = value.get("request_id")
        if not isinstance(request_id, str) or not request_id.strip():
            self._fail_session(
                session, "invalid_request", "request_id must be a non-empty string"
            )
            return

        kind = value.get("kind")
        if session.extension_id is None:
            if kind != "handshake":
                self._fail_session(
                    session,
                    "handshake_required",
                    "the first request must be handshake",
                    request_id=request_id,
                )
                return
            self._handle_handshake(session, value)
            return

        if not self._authorize_extension(session.extension_id):
            self._fail_session(
                session,
                "extension_not_authorized",
                "extension is no longer authorized",
                request_id=request_id,
            )
            return

        if kind == "get_context":
            self._handle_get_context(session, value)
        elif kind == "subscribe_events":
            self._handle_subscribe(session, value)
        elif kind == "propose_mapping":
            self._handle_proposal(session, value)
        elif kind == "action_result":
            self._handle_action_result(session, value)
        else:
            self._send_error(
                session,
                request_id=request_id,
                code="unknown_request",
                message=f"request kind {kind!r} is not supported",
            )

    def _handle_handshake(
        self, session: _ApiSession, value: Mapping[str, object]
    ) -> None:
        request_id = value.get("request_id")
        request_id = request_id if isinstance(request_id, str) else "handshake"
        try:
            request = ApiHandshakeRequest.from_mapping(value)
        except ExtensionContractError as exc:
            self._send(
                session,
                ApiHandshakeResult(
                    request_id=request_id,
                    api_version=ApiVersion(API_MAJOR, API_MINOR),
                    accepted=False,
                    message=str(exc),
                ).as_mapping(),
            )
            session.socket.disconnectFromServer()
            return
        if not self._authorize_extension(request.extension_id):
            self._send(
                session,
                ApiHandshakeResult(
                    request_id=request.request_id,
                    api_version=ApiVersion(API_MAJOR, API_MINOR),
                    accepted=False,
                    message="extension is not authorized",
                ).as_mapping(),
            )
            session.socket.disconnectFromServer()
            return
        if any(
            other is not session
            and other.extension_id == request.extension_id
            and other.socket.state()
            == QLocalSocket.LocalSocketState.ConnectedState
            for other in self._sessions.values()
        ):
            self._send(
                session,
                ApiHandshakeResult(
                    request_id=request.request_id,
                    api_version=ApiVersion(API_MAJOR, API_MINOR),
                    accepted=False,
                    message="extension already has an authenticated session",
                ).as_mapping(),
            )
            session.socket.disconnectFromServer()
            return
        session.extension_id = request.extension_id
        session.api_minor = request.api_version.minor
        self._send(
            session,
            ApiHandshakeResult(
                request_id=request.request_id,
                api_version=ApiVersion(API_MAJOR, session.api_minor),
                accepted=True,
                message="ready",
            ).as_mapping(),
        )
        self.session_opened.emit(request.extension_id)

    def _handle_get_context(
        self, session: _ApiSession, value: Mapping[str, object]
    ) -> None:
        request_id = self._validate_request_shape(
            session, value, expected={"request_id", "kind"}
        )
        if request_id is None:
            return
        try:
            raw_context = self._context_provider()
            context = (
                raw_context
                if isinstance(raw_context, ExtensionContext)
                else ExtensionContext.from_mapping(raw_context)
            )
        except (ExtensionContractError, RuntimeError, ValueError) as exc:
            self._send_error(
                session,
                request_id=request_id,
                code="context_unavailable",
                message=str(exc),
            )
            return
        self._send(
            session,
            {
                "request_id": request_id,
                "kind": "context.result",
                "context": context.as_mapping(),
            },
        )

    def _handle_subscribe(
        self, session: _ApiSession, value: Mapping[str, object]
    ) -> None:
        request_id = self._validate_request_shape(
            session, value, expected={"request_id", "kind", "events"}
        )
        if request_id is None:
            return
        events = value.get("events")
        if not isinstance(events, list) or any(
            not isinstance(event, str) for event in events
        ):
            self._send_error(
                session,
                request_id=request_id,
                code="invalid_request",
                message="events must be a string array",
            )
            return
        requested = set(events)
        supported = _SUPPORTED_OBSERVER_EVENTS if session.api_minor >= 1 else {PROMPT_EVENT_KIND}
        unsupported = requested - supported
        if unsupported:
            self._send_error(
                session,
                request_id=request_id,
                code="unsupported_event",
                message="unsupported events: " + ", ".join(sorted(unsupported)),
            )
            return
        extension_id = session.extension_id
        declared = (
            set(self._allowed_observer_events(extension_id))
            if extension_id is not None
            else set()
        )
        undeclared = requested - declared
        if undeclared:
            self._send_error(
                session,
                request_id=request_id,
                code="event_not_declared",
                message=(
                    "events are not declared in extension manifest: "
                    + ", ".join(sorted(undeclared))
                ),
            )
            return
        session.subscribed_events = requested
        self._send(
            session,
            {
                "request_id": request_id,
                "kind": "subscribe_events.result",
                "subscribed": sorted(requested),
            },
        )

    def _handle_proposal(
        self, session: _ApiSession, value: Mapping[str, object]
    ) -> None:
        request_id = self._validate_request_shape(
            session, value, expected={"request_id", "kind", "proposal"}
        )
        if request_id is None:
            return
        try:
            proposal = SetMappingProposal.from_mapping(
                value.get("proposal"), contract=self._contract
            )
            if proposal.extension_id != session.extension_id:
                raise ExtensionContractError(
                    "proposal extension_id does not match the authorized session"
                )
            raw_result = self._proposal_handler(proposal)
            result = (
                raw_result
                if isinstance(raw_result, SetMappingProposalResult)
                else SetMappingProposalResult.from_mapping(raw_result)
            )
            if result.proposal_id != proposal.proposal_id:
                raise ExtensionContractError(
                    "proposal result does not match the submitted proposal"
                )
        except (ExtensionContractError, RuntimeError, ValueError) as exc:
            self._send_error(
                session,
                request_id=request_id,
                code="invalid_proposal",
                message=str(exc),
            )
            return
        self._send(
            session,
            {
                "request_id": request_id,
                "kind": "proposal.result",
                "result": result.as_mapping(),
            },
        )

    def _handle_action_result(
        self, session: _ApiSession, value: Mapping[str, object]
    ) -> None:
        request_id = self._validate_request_shape(
            session, value, expected={"request_id", "kind", "result"}
        )
        if request_id is None:
            return
        try:
            result = ActionInvocationResult.from_mapping(value.get("result"))
        except ExtensionContractError as exc:
            self._send_error(
                session,
                request_id=request_id,
                code="invalid_action_result",
                message=str(exc),
            )
            return
        pending = self._pending_actions.get(result.invocation_id)
        accepted_extension = self._accepted_actions.get(result.invocation_id)
        expected_extension = (
            pending.extension_id if pending is not None else accepted_extension
        )
        if expected_extension is None or expected_extension != session.extension_id:
            self._send_error(
                session,
                request_id=request_id,
                code="unknown_invocation",
                message="action invocation is no longer pending",
            )
            return
        if result.status == "accepted":
            if pending is None:
                self._send_error(
                    session,
                    request_id=request_id,
                    code="invalid_action_result",
                    message="action invocation was already accepted",
                )
                return
            self._pending_actions.pop(result.invocation_id, None)
            self._accepted_actions[result.invocation_id] = pending.extension_id
            pending.completed(result)
            self.action_result_received.emit(session.extension_id or "", result)
        elif result.status == "rejected":
            if pending is None:
                self._send_error(
                    session,
                    request_id=request_id,
                    code="invalid_action_result",
                    message="accepted action cannot later be rejected",
                )
                return
            self._pending_actions.pop(result.invocation_id, None)
            pending.completed(result)
            self.action_result_received.emit(session.extension_id or "", result)
        elif result.status in {"completed", "failed"}:
            if pending is not None:
                self._send_error(
                    session,
                    request_id=request_id,
                    code="invalid_action_result",
                    message="action must be accepted before its terminal result",
                )
                return
            self._accepted_actions.pop(result.invocation_id, None)
            self.action_result_received.emit(session.extension_id or "", result)
        self._send(
            session,
            {
                "request_id": request_id,
                "kind": "action_result.result",
                "accepted": True,
            },
        )

    def _validate_request_shape(
        self,
        session: _ApiSession,
        value: Mapping[str, object],
        *,
        expected: set[str],
    ) -> str | None:
        request_id = value.get("request_id")
        if not isinstance(request_id, str):
            return None
        unknown = set(value) - expected
        missing = expected - set(value)
        if missing or unknown:
            detail = []
            if missing:
                detail.append("missing: " + ", ".join(sorted(missing)))
            if unknown:
                detail.append("unknown: " + ", ".join(sorted(unknown)))
            self._send_error(
                session,
                request_id=request_id,
                code="invalid_request",
                message="; ".join(detail),
            )
            return None
        return request_id

    def _send_error(
        self,
        session: _ApiSession,
        *,
        request_id: str,
        code: str,
        message: str,
    ) -> None:
        self._send(
            session,
            {
                "request_id": request_id,
                "kind": "error",
                "code": code,
                "message": message,
            },
        )

    def _fail_session(
        self,
        session: _ApiSession,
        code: str,
        message: str,
        *,
        request_id: str = "invalid",
    ) -> None:
        extension_id = session.extension_id or ""
        self._send_error(
            session, request_id=request_id, code=code, message=message
        )
        self.session_error.emit(extension_id, message)
        session.socket.disconnectFromServer()

    def _send(self, session: _ApiSession, value: Mapping[str, object]) -> bool:
        payload = (
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        socket = session.socket
        if socket.bytesToWrite() + len(payload) > self._max_pending_bytes:
            extension_id = session.extension_id or ""
            self.session_error.emit(extension_id, "extension send queue overflow")
            socket.abort()
            self._drop_session(socket)
            return False
        return socket.write(payload) == len(payload)

    def _drop_session(self, socket: QLocalSocket) -> None:
        session = self._sessions.pop(socket, None)
        if session is None:
            return
        if session.extension_id is not None:
            self._fail_pending_actions(
                "extension disconnected", extension_id=session.extension_id
            )
            self._remove_accepted_actions(session.extension_id)
            self.session_closed.emit(session.extension_id)
        socket.deleteLater()

    def _fail_pending_actions(
        self, message: str, *, extension_id: str | None = None
    ) -> None:
        for invocation_id, pending in tuple(self._pending_actions.items()):
            if extension_id is not None and pending.extension_id != extension_id:
                continue
            self._pending_actions.pop(invocation_id, None)
            pending.completed(ActionInvocationResult(invocation_id, "failed", message))

    def _remove_accepted_actions(self, extension_id: str) -> None:
        for invocation_id, accepted_extension in tuple(
            self._accepted_actions.items()
        ):
            if accepted_extension == extension_id:
                self._accepted_actions.pop(invocation_id, None)
