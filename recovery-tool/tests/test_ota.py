from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest

from provisioner.errors import TransportError
from provisioner.ota import (
    CAPABILITIES,
    FW_ABORT,
    FW_BEGIN,
    FW_DATA,
    FW_END,
    FW_STATUS,
    HELLO,
    CdcOtaUpdater,
    OtaTransportInterrupted,
    OtaUpdateError,
)
from provisioner.release import ReleasePackage
from tests.helpers import make_ota_release


class FakeClient:
    def __init__(self, application, *, status=None, hardware_id=None):
        self.application = application
        self.received = bytearray()
        self.calls = []
        self.hardware_id = hardware_id or application.hardware_id
        self.status = status or {
            "state": "IDLE",
            "running_partition": "ota_0",
            "rollback_pending": False,
        }

    def exchange(self, message_type, payload):
        self.calls.append((message_type, payload))
        if message_type == HELLO:
            result = {
                "identity": {
                    "product_id": self.application.product_id,
                    "hardware_id": self.hardware_id,
                    "serial": "CP01-001122334455",
                },
                "versions": {"firmware": "0.2.0", "build_id": "old-build"},
            }
            return {"command": "HELLO", "result": result}
        if message_type == CAPABILITIES:
            return {
                "command": "CAPABILITIES",
                "result": {
                    "features": {"firmware_update": True},
                    "limits": {
                        "firmware_chunk_bytes": 512,
                        "firmware_image_bytes": 0x200000,
                    },
                },
            }
        if message_type == FW_STATUS:
            return {
                "command": "FW_STATUS",
                "result": {"firmware_update": self.status},
            }
        if message_type == FW_ABORT:
            self.status = {
                "state": "IDLE",
                "running_partition": "ota_0",
                "rollback_pending": False,
            }
            return {"command": "FW_ABORT", "result": {"state": "IDLE"}}
        if message_type == FW_BEGIN:
            self.received.clear()
            return {
                "command": "FW_BEGIN",
                "result": {"offset": 0, "target_partition": "ota_1"},
            }
        if message_type == FW_DATA:
            self.received.extend(base64.b64decode(payload["data"]))
            return {
                "command": "FW_DATA",
                "result": {"received_size": len(self.received)},
            }
        if message_type == FW_END:
            return {
                "command": "FW_END",
                "result": {
                    "version": self.application.version,
                    "state": "READY_TO_REBOOT",
                },
            }
        raise AssertionError(f"unexpected message type {message_type}")


class CdcOtaUpdaterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.package = ReleasePackage.open(make_ota_release(Path(self.temp.name)))
        self.application = self.package.ota_application

    def tearDown(self):
        self.package.close()
        self.temp.cleanup()

    def test_uploads_application_and_reports_target_partition(self):
        client = FakeClient(self.application)
        progress = []

        result = CdcOtaUpdater().update(
            client, self.application, progress=lambda current, total: progress.append((current, total))
        )

        self.assertEqual(self.application.path.read_bytes(), bytes(client.received))
        self.assertEqual("CP01-001122334455", result.serial)
        self.assertEqual("0.2.0", result.previous_version)
        self.assertEqual("ota_1", result.target_partition)
        self.assertEqual((self.application.size, self.application.size), progress[-1])

    def test_resumes_matching_interrupted_update(self):
        prefix = self.application.path.read_bytes()[:5]
        status = {
            "state": "RECEIVING",
            "running_partition": "ota_0",
            "target_partition": "ota_1",
            "received_size": len(prefix),
            "expected_size": self.application.size,
            "expected_sha256": self.application.sha256,
            "expected_version": self.application.version,
            "rollback_pending": False,
        }
        client = FakeClient(self.application, status=status)
        client.received.extend(prefix)

        result = CdcOtaUpdater().update(client, self.application)

        self.assertNotIn(FW_BEGIN, [message_type for message_type, _ in client.calls])
        self.assertEqual(self.application.path.read_bytes(), bytes(client.received))
        self.assertEqual("ota_1", result.target_partition)

    def test_rejects_different_interrupted_update_without_restart(self):
        status = {
            "state": "RECEIVING",
            "running_partition": "ota_0",
            "target_partition": "ota_1",
            "received_size": 4,
            "expected_size": 99,
            "expected_sha256": "0" * 64,
            "expected_version": "other",
            "rollback_pending": False,
        }
        client = FakeClient(self.application, status=status)

        with self.assertRaisesRegex(OtaUpdateError, "different interrupted update"):
            CdcOtaUpdater().update(client, self.application)

        self.assertNotIn(FW_ABORT, [message_type for message_type, _ in client.calls])

    def test_restart_aborts_different_interrupted_update(self):
        status = {
            "state": "RECEIVING",
            "running_partition": "ota_0",
            "target_partition": "ota_1",
            "received_size": 4,
            "expected_size": 99,
            "expected_sha256": "0" * 64,
            "expected_version": "other",
            "rollback_pending": False,
        }
        client = FakeClient(self.application, status=status)

        CdcOtaUpdater().update(client, self.application, restart_mismatched=True)

        self.assertIn(FW_ABORT, [message_type for message_type, _ in client.calls])
        self.assertEqual(self.application.path.read_bytes(), bytes(client.received))

    def test_rejects_device_for_other_hardware(self):
        client = FakeClient(self.application, hardware_id="WMP-S3-REV-A")

        with self.assertRaisesRegex(OtaUpdateError, "hardware_id"):
            CdcOtaUpdater().update(client, self.application)

        self.assertNotIn(FW_BEGIN, [message_type for message_type, _ in client.calls])

    def test_rejects_unexpected_acknowledged_offset(self):
        client = FakeClient(self.application)
        original_exchange = client.exchange

        def exchange(message_type, payload):
            response = original_exchange(message_type, payload)
            if message_type == FW_DATA:
                response["result"]["received_size"] += 1
            return response

        client.exchange = exchange

        with self.assertRaisesRegex(OtaUpdateError, "unexpected firmware offset"):
            CdcOtaUpdater().update(client, self.application)

    def test_transport_interruption_carries_same_device_resume_context(self):
        client = FakeClient(self.application)
        original_exchange = client.exchange

        def exchange(message_type, payload):
            if message_type == FW_DATA:
                raise TransportError("cable disconnected")
            return original_exchange(message_type, payload)

        client.exchange = exchange

        with self.assertRaises(OtaTransportInterrupted) as caught:
            CdcOtaUpdater().update(client, self.application)

        self.assertEqual("CP01-001122334455", caught.exception.serial)
        self.assertEqual("ota_1", caught.exception.target_partition)
        self.assertFalse(caught.exception.finalization_uncertain)

    def test_uncertain_finalization_accepts_confirmed_new_runtime_without_reflash(self):
        client = FakeClient(
            self.application,
            status={
                "state": "IDLE",
                "running_partition": "ota_1",
                "rollback_pending": False,
            },
        )
        original_exchange = client.exchange

        def exchange(message_type, payload):
            response = original_exchange(message_type, payload)
            if message_type == HELLO:
                response["result"]["versions"] = {
                    "firmware": self.application.version,
                    "build_id": self.application.build_id,
                }
            return response

        client.exchange = exchange
        result = CdcOtaUpdater().update(
            client,
            self.application,
            expected_serial="CP01-001122334455",
            expected_target_partition="ota_1",
            finalization_uncertain=True,
        )

        self.assertEqual("ota_1", result.target_partition)
        self.assertNotIn(FW_BEGIN, [message_type for message_type, _ in client.calls])

    def test_repeated_reconnect_interruption_preserves_known_identity(self):
        class HelloInterrupted:
            def exchange(self, _message_type, _payload):
                raise TransportError("disconnected again")

        with self.assertRaises(OtaTransportInterrupted) as caught:
            CdcOtaUpdater().update(
                HelloInterrupted(),
                self.application,
                expected_serial="CP01-001122334455",
                expected_target_partition="ota_1",
            )

        self.assertEqual("CP01-001122334455", caught.exception.serial)
        self.assertEqual("ota_1", caught.exception.target_partition)


if __name__ == "__main__":
    unittest.main()
