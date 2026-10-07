from __future__ import annotations

import io
import json
import struct
import unittest
import zlib
from unittest.mock import patch

from provisioner.errors import VerificationError
from provisioner.protocol import (
    ACK,
    GET_CONFIG,
    GET_STATUS,
    HELLO,
    PING,
    WmpClient,
    encode_frame,
)


HEADER = struct.Struct("<4sBBBBIII")


class ScriptedSerial(io.BytesIO):
    def __init__(self, replies):
        super().__init__()
        self.replies = bytearray().join(replies)
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, length):
        data = bytes(self.replies[:length])
        del self.replies[:length]
        return data

    def reset_input_buffer(self):
        pass


def reply(request_id, command, result):
    return encode_frame(ACK, request_id, {"command": command, "result": result}, flags=0x01)


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        random_start = patch("secrets.randbelow", return_value=0)
        random_start.start()
        self.addCleanup(random_start.stop)

    def test_new_connections_do_not_reuse_request_ids(self):
        with patch("secrets.randbelow", side_effect=[0, 1234]):
            first = WmpClient(ScriptedSerial([]))
            second = WmpClient(ScriptedSerial([]))

        self.assertEqual(0, first.request_id)
        self.assertEqual(1234, second.request_id)

    def test_request_id_wraps_after_32_bit_limit(self):
        with patch("secrets.randbelow", return_value=0xFFFFFFFE):
            serial = ScriptedSerial(
                [reply(0xFFFFFFFF, "GET_STATUS", {}), reply(1, "GET_STATUS", {})]
            )
            client = WmpClient(serial)

        client.get_status()
        client.get_status()
        sent_ids = [HEADER.unpack(frame[: HEADER.size])[5] for frame in serial.writes]
        self.assertEqual([0xFFFFFFFF, 1], sent_ids)

    def test_frame_uses_current_ack_and_crc_contract(self):
        frame = encode_frame(PING, 7, {"nonce": "abc"})
        magic, major, minor, message_type, flags, request_id, length, checksum = HEADER.unpack(
            frame[: HEADER.size]
        )
        self.assertEqual((b"WMP1", 1, 0, PING, 0, 7), (magic, major, minor, message_type, flags, request_id))
        self.assertEqual(zlib.crc32(frame[HEADER.size :]), checksum)
        self.assertEqual(length, len(frame) - HEADER.size)
        self.assertEqual(0x7E, ACK)

    def test_runtime_verification_runs_hello_ping_and_get_config(self):
        serial = ScriptedSerial(
            [
                reply(
                    1,
                    "HELLO",
                    {
                        "identity": {
                            "product_id": "wired-macro-pad-v1",
                            "hardware_id": "WMP-S3-REV-A",
                            "serial": "CP01-288485AA3E04",
                        }
                    },
                ),
                reply(2, "PING", {"nonce": "provisioner-2"}),
                reply(3, "GET_CONFIG", {"generation": 4, "digest": "abc", "config": {}}),
            ]
        )

        result = WmpClient(serial).verify()

        self.assertEqual("wired-macro-pad-v1", result.product_id)
        self.assertEqual("WMP-S3-REV-A", result.hardware_id)
        self.assertEqual("CP01-288485AA3E04", result.serial)
        self.assertEqual(4, result.config_generation)
        sent_types = [HEADER.unpack(frame[: HEADER.size])[3] for frame in serial.writes]
        self.assertEqual([HELLO, PING, GET_CONFIG], sent_types)

    def test_runtime_verification_rejects_missing_factory_serial(self):
        serial = ScriptedSerial(
            [
                reply(
                    1,
                    "HELLO",
                    {
                        "identity": {
                            "product_id": "wired-macro-pad-v1",
                            "hardware_id": "WMP-S3-REV-A",
                        }
                    },
                )
            ]
        )
        with self.assertRaisesRegex(VerificationError, "serial"):
            WmpClient(serial).verify()

    def test_runtime_verification_accepts_expected_power_v2_build(self):
        serial = ScriptedSerial(
            [
                reply(
                    1,
                    "HELLO",
                    {
                        "identity": {
                            "product_id": "wired-macro-pad-v1",
                            "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
                            "serial": "CP01-288485AA3E04",
                        },
                        "versions": {"build_id": "power-v2-build"},
                    },
                ),
                reply(2, "PING", {"nonce": "provisioner-2"}),
                reply(3, "GET_CONFIG", {"generation": 4, "digest": "abc", "config": {}}),
            ]
        )

        result = WmpClient(serial).verify(
            expected_hardware_id="WMP-S3-MATRIX12-POWER-V2",
            expected_build_id="power-v2-build",
        )

        self.assertEqual("power-v2-build", result.build_id)

    def test_runtime_verification_rejects_unexpected_build(self):
        serial = ScriptedSerial(
            [
                reply(
                    1,
                    "HELLO",
                    {
                        "identity": {
                            "product_id": "wired-macro-pad-v1",
                            "hardware_id": "WMP-S3-MATRIX12-POWER-V2",
                            "serial": "CP01-288485AA3E04",
                        },
                        "versions": {"build_id": "other-build"},
                    },
                )
            ]
        )

        with self.assertRaisesRegex(VerificationError, "build_id"):
            WmpClient(serial).verify(
                expected_hardware_id="WMP-S3-MATRIX12-POWER-V2",
                expected_build_id="power-v2-build",
            )

    def test_get_status_returns_result_object(self):
        serial = ScriptedSerial(
            [reply(1, "GET_STATUS", {"build_id": "power-v2-build", "active_controls": []})]
        )

        result = WmpClient(serial).get_status()

        self.assertEqual("power-v2-build", result["build_id"])
        sent_type = HEADER.unpack(serial.writes[0][: HEADER.size])[3]
        self.assertEqual(GET_STATUS, sent_type)


if __name__ == "__main__":
    unittest.main()
