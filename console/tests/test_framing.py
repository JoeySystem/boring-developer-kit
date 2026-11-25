from __future__ import annotations

import json
import struct
import zlib

import pytest

from controller_config.protocol.framing import (
    HEADER,
    BadFrameMonitor,
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


def _decoder(contract) -> FrameDecoder:
    return FrameDecoder(
        max_payload_bytes=contract.max_payload_bytes,
        protocol_major=contract.protocol_major,
        protocol_minor=contract.protocol_minor,
    )


def test_decoder_skips_bad_crc_frame_and_keeps_neighbours(contract) -> None:
    """A-03: good + corrupt + good in one feed returns both good frames."""

    bad = bytearray(_response_frame(2, {"command": "GET_STATUS", "result": {}}))
    bad[-1] ^= 0x01
    decoder = _decoder(contract)
    frames = decoder.feed(
        _response_frame(1, {"command": "HELLO", "result": {}})
        + bytes(bad)
        + _response_frame(3, {"command": "GET_STATUS", "result": {}})
    )
    assert [frame.request_id for frame in frames] == [1, 3]
    discards = decoder.take_discards()
    assert len(discards) == 1
    assert discards[0].request_id == 2
    assert "CRC32" in discards[0].describe()
    assert "computed=" in discards[0].describe()
    assert decoder.take_discards() == []


def test_decoder_resyncs_after_garbage_and_corrupt_length(contract) -> None:
    """A-03: a header with a corrupt length must not swallow later frames."""

    corrupt = bytearray(_response_frame(5, {"command": "HELLO", "result": {}}))
    struct.pack_into("<I", corrupt, 12, contract.max_payload_bytes + 1)
    decoder = _decoder(contract)
    good = _response_frame(6, {"command": "HELLO", "result": {}})
    frames = decoder.feed(b"\x00noiseWM" + bytes(corrupt) + good)
    assert [frame.request_id for frame in frames] == [6]
    assert decoder.take_discards()


def test_decoder_resyncs_when_length_points_past_next_frame(contract) -> None:
    """A-03: CRC failure caused by an inflated length is resolved by
    advancing one byte, so the frame that follows still decodes."""

    first = bytearray(_response_frame(7, {"command": "HELLO", "result": {}}))
    struct.pack_into("<I", first, 12, len(first) - 20 + 30)
    second = _response_frame(8, {"command": "GET_STATUS", "result": {"x": "y" * 40}})
    decoder = _decoder(contract)
    frames = decoder.feed(bytes(first) + second)
    assert [frame.request_id for frame in frames] == [8]


def test_decoder_skips_reserved_flags_and_zero_request_id(contract) -> None:
    payload = json.dumps({"command": "HELLO"}).encode()
    reserved = HEADER.pack(b"WMP1", 1, 0, 0x7E, 0x05, 1, len(payload), zlib.crc32(payload)) + payload
    zero_id = HEADER.pack(b"WMP1", 1, 0, 0x7E, 0x01, 0, len(payload), zlib.crc32(payload)) + payload
    decoder = _decoder(contract)
    frames = decoder.feed(reserved + zero_id + _response_frame(9, {"command": "HELLO", "result": {}}))
    assert [frame.request_id for frame in frames] == [9]
    assert len(decoder.take_discards()) == 2


def test_decoder_rejects_crc_valid_frame_with_other_major(contract) -> None:
    frame = bytearray(_response_frame(1, {"command": "HELLO", "result": {}}))
    frame[4] = contract.protocol_major + 1
    with pytest.raises(ProtocolVersionError, match="major"):
        _decoder(contract).feed(bytes(frame))


def test_decoder_ignores_major_mismatch_on_corrupt_frame(contract) -> None:
    frame = bytearray(_response_frame(1, {"command": "HELLO", "result": {}}))
    frame[4] = contract.protocol_major + 1
    frame[-1] ^= 0x01
    decoder = _decoder(contract)
    assert decoder.feed(bytes(frame) + _response_frame(2, {"command": "HELLO", "result": {}}))[0].request_id == 2


@pytest.mark.parametrize("delta", [-1, 0, 1, 7])
def test_decoder_accepts_any_minor_within_major(contract, delta) -> None:
    """A-03: device minor lower, equal or higher than the client is accepted."""

    minor = contract.protocol_minor + delta
    if minor < 0:
        pytest.skip("client is already at minor 0")
    data = json.dumps({"command": "HELLO", "result": {}}).encode()
    frame = HEADER.pack(b"WMP1", 1, minor, 0x7E, 0x01, 1, len(data), zlib.crc32(data)) + data
    decoder = _decoder(contract)
    frames = decoder.feed(frame)
    assert frames[0].protocol_minor == minor
    assert decoder.peer_protocol_minor == minor
    assert decoder.peer_is_newer is (delta > 0)


def test_bad_frame_monitor_escalates_only_clustered_discards() -> None:
    now = [0.0]
    monitor = BadFrameMonitor(limit=5, window_s=10.0, clock=lambda: now[0])
    for _ in range(4):
        assert monitor.record() is False
        now[0] += 3.0
    # t=12: the t=0 discard fell out of the 10 s window, 4 remain.
    assert monitor.record() is False
    assert monitor.record() is True


def test_response_json_rejects_non_finite_numbers() -> None:
    response = Frame(1, 0, 0x7E, 0x01, 1, b'{"value":NaN}')
    with pytest.raises(FrameError, match="非有限"):
        response.json_payload()


def _response_frame(request_id: int, payload: dict) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return HEADER.pack(b"WMP1", 1, 0, 0x7E, 0x01, request_id, len(data), zlib.crc32(data)) + data
