from __future__ import annotations

import json
import struct
import zlib
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
    def __init__(self, *, max_payload_bytes: int, protocol_major: int, protocol_minor: int) -> None:
        self._buffer = bytearray()
        self._max_payload_bytes = max_payload_bytes
        self._protocol_major = protocol_major
        self._protocol_minor = protocol_minor

    def reset(self) -> None:
        self._buffer.clear()

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer.extend(data)
        frames: list[Frame] = []
        while True:
            if len(self._buffer) < HEADER_BYTES:
                return frames
            if self._buffer[:4] != MAGIC:
                next_magic = self._buffer.find(MAGIC, 1)
                if next_magic < 0:
                    del self._buffer[:-3]
                    return frames
                del self._buffer[:next_magic]
                if len(self._buffer) < HEADER_BYTES:
                    return frames

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
            if major != self._protocol_major:
                raise ProtocolVersionError(f"响应 protocol major {major} 不受支持")
            if minor != self._protocol_minor:
                raise ProtocolVersionError(f"响应 protocol minor {minor} 不受支持")
            if flags & RESERVED_FLAGS:
                raise FrameError("响应设置了保留 flags")
            if request_id == 0:
                raise FrameError("响应 request_id 不能为 0")
            if payload_length > self._max_payload_bytes:
                raise FrameError("响应 payload 超出共享协议上限")
            frame_length = HEADER_BYTES + payload_length
            if len(self._buffer) < frame_length:
                return frames
            payload = bytes(self._buffer[HEADER_BYTES:frame_length])
            del self._buffer[:frame_length]
            expected_crc = zlib.crc32(payload) & 0xFFFFFFFF if payload else 0
            if payload_crc != expected_crc:
                raise FrameError("响应 payload CRC32 不匹配")
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
