from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from PySide6.QtNetwork import QLocalSocket


API_MAJOR = 1
API_MINOR = 0
DEFAULT_TIMEOUT_MS = 2_000
_MAX_QUEUED_NOTIFICATIONS = 256


class BoringConsoleError(RuntimeError):
    """Base error raised by the BORING Console SDK."""


class ConnectionError(BoringConsoleError):
    """The local BORING Console API is unavailable."""


class RequestError(BoringConsoleError):
    """BORING Console rejected one SDK request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BoringConsoleClient:
    """Blocking SDK client intended for a private extension-runner process."""

    def __init__(
        self,
        server_name: str,
        extension_id: str,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        if not server_name.strip():
            raise ValueError("server_name cannot be empty")
        if not extension_id.strip():
            raise ValueError("extension_id cannot be empty")
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._server_name = server_name
        self._extension_id = extension_id
        self._timeout_ms = timeout_ms
        self._socket = QLocalSocket()
        self._buffer = bytearray()
        self._notifications: dict[str, deque[dict[str, Any]]] = {
            "event": deque(),
            "action.invoke": deque(),
        }

    @classmethod
    def from_environment(cls, *, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        try:
            server_name = os.environ["BORING_EXTENSION_API_SERVER"]
            extension_id = os.environ["BORING_EXTENSION_ID"]
        except KeyError as exc:
            raise ConnectionError(f"missing runner environment: {exc.args[0]}") from exc
        return cls(server_name, extension_id, timeout_ms=timeout_ms)

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @property
    def is_connected(self) -> bool:
        return self._socket.state() == QLocalSocket.LocalSocketState.ConnectedState

    def connect(self) -> None:
        if self.is_connected:
            return
        self._socket.connectToServer(self._server_name)
        if not self._socket.waitForConnected(self._timeout_ms):
            raise ConnectionError(self._socket.errorString())
        response = self._request(
            "handshake",
            extension_id=self._extension_id,
            api_version={"major": API_MAJOR, "minor": API_MINOR},
        )
        if response.get("kind") != "handshake.result":
            self.close()
            raise ConnectionError("BORING Console returned an invalid handshake")
        if not response.get("accepted"):
            message = str(response.get("message", "extension was not accepted"))
            self.close()
            raise ConnectionError(message)

    def close(self) -> None:
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            return
        self._socket.disconnectFromServer()
        if self._socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.waitForDisconnected(min(self._timeout_ms, 250))

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def get_context(self) -> dict[str, Any]:
        response = self._request("get_context")
        if response.get("kind") != "context.result":
            raise RequestError("invalid_response", "invalid context response")
        context = response.get("context")
        if not isinstance(context, dict):
            raise RequestError("invalid_response", "context must be an object")
        return context

    def subscribe_events(
        self, events: Sequence[str] = ("prompt.triggered",)
    ) -> tuple[str, ...]:
        response = self._request("subscribe_events", events=list(events))
        if response.get("kind") != "subscribe_events.result":
            raise RequestError("invalid_response", "invalid subscription response")
        subscribed = response.get("subscribed")
        if not isinstance(subscribed, list) or any(
            not isinstance(item, str) for item in subscribed
        ):
            raise RequestError("invalid_response", "subscribed must be a string array")
        return tuple(subscribed)

    def next_event(self, *, timeout_ms: int | None = None) -> dict[str, Any] | None:
        timeout = self._timeout_ms if timeout_ms is None else timeout_ms
        if timeout < 0:
            raise ValueError("timeout_ms cannot be negative")
        message = self._next_notification("event", timeout)
        return None if message is None else self._event_value(message)

    def next_action(self, *, timeout_ms: int | None = None) -> dict[str, Any] | None:
        timeout = self._timeout_ms if timeout_ms is None else timeout_ms
        if timeout < 0:
            raise ValueError("timeout_ms cannot be negative")
        message = self._next_notification("action.invoke", timeout)
        return None if message is None else self._action_value(message)

    def respond_action(
        self, invocation_id: str, status: str, message: str = ""
    ) -> None:
        if status not in {"accepted", "rejected", "completed", "failed"}:
            raise ValueError("unsupported action result status")
        response = self._request(
            "action_result",
            result={
                "schema_version": 1,
                "invocation_id": invocation_id,
                "status": status,
                "message": message,
            },
        )
        if response.get("kind") != "action_result.result" or not response.get(
            "accepted"
        ):
            raise RequestError("invalid_response", "invalid action result response")

    def events(self) -> Iterator[dict[str, Any]]:
        while self.is_connected:
            event = self.next_event()
            if event is not None:
                yield event

    def propose_mapping(self, proposal: Mapping[str, object]) -> dict[str, Any]:
        response = self._request("propose_mapping", proposal=dict(proposal))
        if response.get("kind") != "proposal.result":
            raise RequestError("invalid_response", "invalid proposal response")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RequestError("invalid_response", "proposal result must be an object")
        return result

    def _request(self, kind: str, **fields: object) -> dict[str, Any]:
        if kind != "handshake" and not self.is_connected:
            raise ConnectionError("BORING Console is not connected")
        request_id = uuid.uuid4().hex
        self._write_message({"request_id": request_id, "kind": kind, **fields})
        deadline = time.monotonic() + self._timeout_ms / 1_000
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            try:
                response = self._read_message(remaining_ms)
            except TimeoutError as exc:
                raise ConnectionError(f"request {kind} timed out") from exc
            if response.get("kind") in {"event", "action.invoke"}:
                self._queue_notification(response)
                continue
            if response.get("request_id") != request_id:
                raise RequestError(
                    "unexpected_response", "response request_id does not match"
                )
            if response.get("kind") == "error":
                raise RequestError(
                    str(response.get("code", "request_failed")),
                    str(response.get("message", "request failed")),
                )
            return response

    def _next_notification(
        self, expected_kind: str, timeout_ms: int
    ) -> dict[str, Any] | None:
        queued = self._notifications[expected_kind]
        if queued:
            return queued.popleft()
        deadline = time.monotonic() + timeout_ms / 1_000
        while True:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            try:
                message = self._read_message(remaining_ms)
            except TimeoutError:
                return None
            if message.get("kind") == expected_kind:
                return message
            if message.get("kind") in {"event", "action.invoke"}:
                self._queue_notification(message)
                continue
            raise RequestError(
                "unexpected_message", f"unexpected notification {message.get('kind')!r}"
            )

    def _queue_notification(self, message: dict[str, Any]) -> None:
        kind = str(message.get("kind"))
        queue = self._notifications[kind]
        if len(queue) >= _MAX_QUEUED_NOTIFICATIONS:
            raise BoringConsoleError(f"{kind} notification queue is full")
        queue.append(message)

    def _write_message(self, value: Mapping[str, object]) -> None:
        payload = (
            json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        if self._socket.write(payload) != len(payload):
            raise ConnectionError(self._socket.errorString())
        if not self._socket.waitForBytesWritten(self._timeout_ms):
            raise ConnectionError(self._socket.errorString())

    def _read_message(self, timeout_ms: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_ms / 1_000
        while b"\n" not in self._buffer:
            if self._socket.bytesAvailable():
                self._buffer.extend(bytes(self._socket.readAll()))
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1_000))
            if remaining_ms == 0 or not self._socket.waitForReadyRead(remaining_ms):
                if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                    raise ConnectionError("BORING Console disconnected")
                raise TimeoutError
            self._buffer.extend(bytes(self._socket.readAll()))
        raw_line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("invalid_json", "invalid response from BORING Console") from exc
        if not isinstance(message, dict):
            raise RequestError("invalid_response", "response must be an object")
        return message

    @staticmethod
    def _event_value(message: Mapping[str, object]) -> dict[str, Any]:
        event = message.get("event")
        if not isinstance(event, dict):
            raise RequestError("invalid_event", "event must be an object")
        return event

    @staticmethod
    def _action_value(message: Mapping[str, object]) -> dict[str, Any]:
        invocation = message.get("invocation")
        if not isinstance(invocation, dict):
            raise RequestError("invalid_action", "invocation must be an object")
        return invocation
