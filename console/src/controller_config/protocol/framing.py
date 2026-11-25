from __future__ import annotations

import json
import struct
import time
import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


MAGIC = b"WMP1"
HEADER = struct.Struct("<4sBBBBIII")
HEADER_BYTES = HEADER.size
RESPONSE_FLAG = 0x01
ERROR_FLAG = 0x02
RESERVED_FLAGS = 0xFC


class FrameError(ValueError):
    pass


class ProtocolVersionError(FrameError):
    pass


@dataclass(frozen=True)
class FrameDiscard:
    """A corrupted or malformed frame the decoder skipped while resyncing."""

    reason: str
    request_id: int | None = None
    message_type: int | None = None

    def describe(self) -> str:
        parts = [self.reason]
        if self.request_id is not None:
            parts.append(f"request_id={self.request_id}")
        if self.message_type is not None:
            parts.append(f"type=0x{self.message_type:02x}")
        return "，".join(parts)


class BadFrameMonitor:
    """Escalates isolated corrupt frames to a fatal link error only when they
    cluster: ``limit`` discards inside ``window_s`` seconds."""

    def __init__(
        self,
        *,
        limit: int = 5,
        window_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._events: deque[float] = deque()

    def reset(self) -> None:
        self._events.clear()

    def record(self, count: int = 1) -> bool:
        now = self._clock()
        for _ in range(count):
            self._events.append(now)
        while self._events and now - self._events[0] > self._window_s:
            self._events.popleft()
        return len(self._events) >= self._limit


@dataclass(frozen=True)
class Frame:
    protocol_major: int
    protocol_minor: int
    message_type: int
    flags: int
    request_id: int
    payload_bytes: bytes

    def json_payload(self) -> dict[str, Any]:
        try:
            value = json.loads(
                self.payload_bytes.decode("utf-8"),
                parse_constant=lambda token: _reject_non_finite(token),
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise FrameError(f"响应 payload 不是有效 UTF-8 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise FrameError("响应 payload 必须是 JSON object")
        return value


def _reject_non_finite(token: str) -> None:
    raise ValueError(f"协议禁止非有限数值 {token}")


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FrameError(f"请求 payload 无法编码：{exc}") from exc


def encode_request(
    *,
    protocol_major: int,
    protocol_minor: int,
    message_type: int,
    request_id: int,
    payload: dict[str, Any],
    max_payload_bytes: int,
) -> bytes:
    if request_id <= 0 or request_id > 0xFFFFFFFF:
        raise FrameError("request_id 必须位于 1..0xffffffff")
    payload_bytes = canonical_json_bytes(payload)
    if len(payload_bytes) > max_payload_bytes:
        raise FrameError("请求 payload 超出共享协议上限")
    crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF if payload_bytes else 0
    return HEADER.pack(
        MAGIC,
        protocol_major,
        protocol_minor,
        message_type,
        0,
        request_id,
        len(payload_bytes),
        crc,
    ) + payload_bytes


class FrameDecoder:
    """Incremental WMP1 response decoder.

    A single bad frame (CRC mismatch, oversize length, reserved flags,
    request_id 0) is skipped by advancing one byte past its magic and scanning
    for the next ``WMP1``; it is reported through :meth:`take_discards`
    instead of raising, so the good frames around it survive. Only a
    CRC-valid frame with an incompatible protocol major raises
    :class:`ProtocolVersionError`. Within the same major any protocol minor is
    accepted: minors only add backward-compatible fields and commands, and
    feature availability is decided from CAPABILITIES (protocol.md §2).
    """

    def __init__(self, *, max_payload_bytes: int, protocol_major: int, protocol_minor: int) -> None:
        self._buffer = bytearray()
        self._max_payload_bytes = max_payload_bytes
        self._protocol_major = protocol_major
        self._protocol_minor = protocol_minor
        self._discards: list[FrameDiscard] = []
        self.peer_protocol_minor: int | None = None

    @property
    def peer_is_newer(self) -> bool:
        return (
            self.peer_protocol_minor is not None
            and self.peer_protocol_minor > self._protocol_minor
        )

    def reset(self) -> None:
        self._buffer.clear()
        self._discards.clear()
        self.peer_protocol_minor = None

    def take_discards(self) -> list[FrameDiscard]:
        discards, self._discards = self._discards, []
        return discards

    def _skip_frame_start(self, discard: FrameDiscard) -> None:
        self._discards.append(discard)
        del self._buffer[:1]

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            if len(self._buffer) < HEADER_BYTES:
                if self._buffer and not MAGIC.startswith(bytes(self._buffer[:4])):
                    self._resync()
                return frames
            if self._buffer[:4] != MAGIC:
                if not self._resync():
                    return frames
                continue

            (
                _magic,
                major,
                minor,
                message_type,
                flags,
                request_id,
                payload_length,
                payload_crc,
            ) = HEADER.unpack_from(self._buffer)
            if payload_length > self._max_payload_bytes:
                self._skip_frame_start(FrameDiscard("响应 payload 长度超出共享协议上限"))
                continue
            frame_length = HEADER_BYTES + payload_length
            if len(self._buffer) < frame_length:
                return frames
            payload = bytes(self._buffer[HEADER_BYTES:frame_length])
            expected_crc = zlib.crc32(payload) & 0xFFFFFFFF if payload else 0
            if payload_crc != expected_crc:
                # Do not trust the length field of a corrupt frame: advance one
                # byte so following good frames inside the span are recovered.
                self._skip_frame_start(
                    FrameDiscard(
                        f"响应 payload CRC32 不匹配（payload_bytes={payload_length}，"
                        f"received=0x{payload_crc:08x}，computed=0x{expected_crc:08x}）",
                        request_id=request_id,
                        message_type=message_type,
                    )
                )
                continue
            if major != self._protocol_major:
                del self._buffer[:frame_length]
                raise ProtocolVersionError(f"响应 protocol major {major} 不受支持")
            del self._buffer[:frame_length]
            if flags & RESERVED_FLAGS:
                self._discards.append(
                    FrameDiscard("响应设置了保留 flags", request_id, message_type)
                )
                continue
            if request_id == 0:
                self._discards.append(
                    FrameDiscard("响应 request_id 不能为 0", None, message_type)
                )
                continue
            self.peer_protocol_minor = minor
            frames.append(
                Frame(
                    protocol_major=major,
                    protocol_minor=minor,
                    message_type=message_type,
                    flags=flags,
                    request_id=request_id,
                    payload_bytes=payload,
                )
            )

    def _resync(self) -> bool:
        """Drop bytes up to the next magic. Returns True when one is at the
        buffer start and a full header may follow."""

        next_magic = self._buffer.find(MAGIC, 1)
        if next_magic < 0:
            # Keep a possible partial magic at the tail.
            keep = 0
            for size in (3, 2, 1):
                if len(self._buffer) >= size and MAGIC.startswith(bytes(self._buffer[-size:])):
                    keep = size
                    break
            del self._buffer[: len(self._buffer) - keep]
            return False
        del self._buffer[:next_magic]
        return len(self._buffer) >= HEADER_BYTES
