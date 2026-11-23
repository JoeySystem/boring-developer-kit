from __future__ import annotations

import json
import threading
import time
import unittest
from unittest import mock

from provisioner.protocol import (
    ACK,
    DIAGNOSTIC_START,
    DIAGNOSTIC_STOP,
    HEADER,
    WmpClient,
    encode_frame,
)


class ResponsePort:
    def __init__(self, *, stale: bytes = b"", write_delay: float = 0):
        self.buffer = bytearray(stale)
        self.write_delay = write_delay
        self.reset_count = 0
        self._active_write = False
        self.overlapped = False
        self._lock = threading.Lock()

    def reset_input_buffer(self):
        self.reset_count += 1
        self.buffer.clear()

    def write(self, request: bytes):
        with self._lock:
            if self._active_write:
                self.overlapped = True
            self._active_write = True
        try:
            if self.write_delay:
                time.sleep(self.write_delay)
            _, _, _, _, _, request_id, length, _ = HEADER.unpack_from(request)
            payload = json.loads(request[HEADER.size:HEADER.size + length])
            response = encode_frame(
                ACK,
                request_id,
                {"request": payload},
                flags=0x01,
            )
            self.buffer.extend(response)
            return len(request)
        finally:
            with self._lock:
                self._active_write = False

    def read(self, length: int) -> bytes:
        if not self.buffer:
            return b""
        chunk = bytes(self.buffer[:length])
        del self.buffer[:length]
        return chunk


class WmpClientExchangeTest(unittest.TestCase):
    def test_exchange_discards_stale_serial_bytes_before_request(self):
        port = ResponsePort(stale=b"stale serial output")
        client = WmpClient(port, timeout_s=0.2)

        result = client.exchange(0x03, {"value": 1})

        self.assertEqual({"value": 1}, result["request"])
        self.assertEqual(1, port.reset_count)

    def test_exchange_serializes_requests_on_shared_calibration_client(self):
        port = ResponsePort(write_delay=0.03)
        client = WmpClient(port, timeout_s=0.5)
        results = []
        errors = []

        def exchange(value: int):
            try:
                results.append(client.exchange(0x21, {"value": value}))
            except Exception as exc:  # pragma: no cover - retained for thread reporting
                errors.append(exc)

        threads = [threading.Thread(target=exchange, args=(value,)) for value in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertFalse(errors)
        self.assertEqual([1, 2], sorted(item["request"]["value"] for item in results))
        self.assertFalse(port.overlapped)

    def test_diagnostic_capture_commands_use_empty_payloads(self):
        client = WmpClient(mock.MagicMock())
        client.exchange = mock.MagicMock(
            side_effect=[
                {"command": "DIAGNOSTIC_START", "result": {"active": True}},
                {"command": "DIAGNOSTIC_STOP", "result": {"active": False}},
            ]
        )

        self.assertTrue(client.diagnostic_start()["active"])
        self.assertFalse(client.diagnostic_stop()["active"])
        self.assertEqual(
            [
                mock.call(DIAGNOSTIC_START, {}),
                mock.call(DIAGNOSTIC_STOP, {}),
            ],
            client.exchange.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
