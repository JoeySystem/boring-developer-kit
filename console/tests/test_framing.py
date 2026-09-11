from __future__ import annotations

import json
import struct
import zlib

import pytest

from controller_config.protocol.framing import (
    HEADER,
    Frame,
    FrameDecoder,
    FrameError,
    ProtocolVersionError,
    encode_request,
)


def test_hello_request_matches_shared_frame_fixture(contract, load_fixture) -> None:
    fixture = load_fixture("hello-request-frame.json")
    hello = load_fixture("hello-v1.json")["request"]
    encoded = encode_request(
        protocol_major=contract.protocol_major,
        protocol_minor=contract.protocol_minor,
        message_type=fixture["message_type"],
        request_id=fixture["request_id"],
        payload=hello,
        max_payload_bytes=contract.max_payload_bytes,
    )
    assert encoded.hex() == fixture["frame_hex"]


def test_decoder_accepts_split_and_coalesced_frames(contract) -> None:
    first = _response_frame(1, {"command": "HELLO", "result": {}})
    second = _response_frame(2, {"command": "GET_STATUS", "result": {}})
    decoder = FrameDecoder(
        max_payload_bytes=contract.max_payload_bytes,
        protocol_major=contract.protocol_major,
        protocol_minor=contract.protocol_minor,
    )
    assert decoder.feed(first[:9]) == []
    frames = decoder.feed(first[9:] + second)
    assert [frame.request_id for frame in frames] == [1, 2]


def test_decoder_rejects_bad_crc(contract) -> None:
    frame = bytearray(_response_frame(1, {"command": "HELLO", "result": {}}))
    frame[-1] ^= 0x01
    decoder = FrameDecoder(
        max_payload_bytes=contract.max_payload_bytes,
        protocol_major=contract.protocol_major,
        protocol_minor=contract.protocol_minor,
    )
    with pytest.raises(FrameError, match="CRC32"):
        decoder.feed(frame)


def test_decoder_rejects_protocol_minor_outside_v1_contract(contract) -> None:
    frame = bytearray(_response_frame(1, {"command": "HELLO", "result": {}}))
    frame[5] = 1
    decoder = FrameDecoder(
        max_payload_bytes=contract.max_payload_bytes,
        protocol_major=contract.protocol_major,
        protocol_minor=contract.protocol_minor,
    )

    with pytest.raises(ProtocolVersionError, match="minor"):
        decoder.feed(frame)


def test_response_json_rejects_non_finite_numbers() -> None:
    response = Frame(1, 0, 0x7E, 0x01, 1, b'{"value":NaN}')
    with pytest.raises(FrameError, match="非有限"):
        response.json_payload()


def _response_frame(request_id: int, payload: dict) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return HEADER.pack(b"WMP1", 1, 0, 0x7E, 0x01, request_id, len(data), zlib.crc32(data)) + data
