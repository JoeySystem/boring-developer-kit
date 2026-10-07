from __future__ import annotations

import json
import re
import secrets
import struct
import time
import zlib

from .errors import TransportError, VerificationError


MAGIC = b"WMP1"
HEADER = struct.Struct("<4sBBBBIII")
HELLO = 0x01
CAPABILITIES = 0x02
PING = 0x03
AUTH_GET_CERTIFICATE = 0x04
AUTH_CHALLENGE = 0x05
GET_CONFIG = 0x10
GET_STATUS = 0x13
DIAGNOSTIC_START = 0x24
DIAGNOSTIC_STOP = 0x25
ACK = 0x7E
NACK = 0x7F
MAX_PAYLOAD = 20 * 1024


def encode_frame(
    message_type: int,
    request_id: int,
    payload: dict,
    *,
    flags: int = 0,
) -> bytes:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return HEADER.pack(
        MAGIC,
        1,
        0,
        message_type,
        flags,
        request_id,
        len(body),
        zlib.crc32(body),
    ) + body


def _read_exact(port, length: int, timeout_s: float) -> bytes:
    deadline = time.monotonic() + timeout_s
    data = bytearray()
    while len(data) < length and time.monotonic() < deadline:
        chunk = port.read(length - len(data))
        if chunk:
            data.extend(chunk)
        else:
            time.sleep(0.01)
    if len(data) != length:
        raise TransportError(f"串口响应超时：需要 {length} 字节，收到 {len(data)} 字节")
    return bytes(data)


class WmpClient:
    def __init__(self, port, timeout_s: float = 3.0):
        self.port = port
        self.timeout_s = timeout_s
        self.request_id = secrets.randbelow(0xFFFFFFFF)

    def exchange(self, message_type: int, payload: dict) -> dict:
        self.request_id = 1 if self.request_id == 0xFFFFFFFF else self.request_id + 1
        request_id = self.request_id
        try:
            self.port.write(encode_frame(message_type, request_id, payload))
            header = _read_exact(self.port, HEADER.size, self.timeout_s)
        except OSError as exc:
            raise TransportError(f"串口传输中断：{exc}") from exc
        magic, major, minor, response_type, flags, response_id, length, checksum = (
            HEADER.unpack(header)
        )
        if magic != MAGIC or (major, minor) != (1, 0):
            raise VerificationError("设备返回了不兼容的 WMP 协议")
        if length > MAX_PAYLOAD:
            raise VerificationError("设备返回的 WMP 数据过大")
        try:
            body = _read_exact(self.port, length, self.timeout_s)
        except OSError as exc:
            raise TransportError(f"串口传输中断：{exc}") from exc
        if zlib.crc32(body) != checksum:
            raise VerificationError("设备返回的 WMP 数据 CRC 错误")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VerificationError("设备返回的 WMP JSON 无效") from exc
        if response_id != request_id or flags not in (0x01, 0x03):
            raise VerificationError("设备返回的 WMP 请求编号或标志无效")
        if response_type == NACK:
            error = decoded.get("error", {})
            raise VerificationError(
                f"设备拒绝请求：{error.get('name', 'NACK')} {error.get('message', '')}".strip()
            )
        if response_type != ACK or flags != 0x01:
            raise VerificationError("设备未返回 ACK")
        return decoded

    def verify(
        self,
        *,
        expected_product_id: str = "wired-macro-pad-v1",
        expected_hardware_id: str = "WMP-S3-REV-A",
        expected_build_id: str | None = None,
    ):
        from .models import WmpVerification

        self.port.reset_input_buffer()
        hello = self.exchange(
            HELLO,
            {
                "client": {"name": "offline-provisioner", "version": "0.1.0"},
                "protocol": {"min_major": 1, "max_major": 1, "max_minor": 0},
            },
        )
        identity = hello.get("result", {}).get("identity", {})
        if identity.get("product_id") != expected_product_id:
            raise VerificationError("HELLO product_id 不匹配")
        if identity.get("hardware_id") != expected_hardware_id:
            raise VerificationError("HELLO hardware_id 不匹配")
        versions = hello.get("result", {}).get("versions", {})
        build_id = versions.get("build_id", "") if isinstance(versions, dict) else ""
        if not isinstance(build_id, str):
            raise VerificationError("HELLO build_id 无效")
        if expected_build_id is not None and build_id != expected_build_id:
            raise VerificationError(
                f"HELLO build_id {build_id or 'missing'} 与发布包 {expected_build_id} 不匹配"
            )
        serial = identity.get("serial")
        if not isinstance(serial, str) or not re.fullmatch(
            r"CP01-[0-9A-F]{12}",
            serial,
        ):
            raise VerificationError("HELLO serial 无效")
        nonce = f"provisioner-{self.request_id + 1}"
        ping = self.exchange(PING, {"nonce": nonce})
        if ping.get("result", {}).get("nonce") != nonce:
            raise VerificationError("PING nonce 不匹配")
        config = self.exchange(GET_CONFIG, {})
        result = config.get("result", {})
        generation = result.get("generation")
        if not isinstance(generation, int):
            raise VerificationError("GET_CONFIG 未返回有效 generation")
        return WmpVerification(
            product_id=identity["product_id"],
            hardware_id=identity["hardware_id"],
            build_id=build_id,
            serial=serial,
            config_generation=generation,
        )

    def get_status(self) -> dict:
        payload = self.exchange(GET_STATUS, {})
        if payload.get("command") != "GET_STATUS":
            raise VerificationError("设备未返回 GET_STATUS 响应")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise VerificationError("GET_STATUS 未返回 result object")
        return result
