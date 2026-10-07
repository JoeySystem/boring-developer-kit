from __future__ import annotations

import base64
import time
from typing import Any, Callable, Mapping, Protocol

from .errors import TransportError, VerificationError
from .models import OtaApplication, OtaUpdateResult
from .protocol import HELLO


CAPABILITIES = 0x02
FW_BEGIN = 0x40
FW_DATA = 0x41
FW_STATUS = 0x42
FW_END = 0x43
FW_ABORT = 0x44
VALID_STATES = {
    "IDLE",
    "RECEIVING",
    "FINALIZING",
    "READY_TO_REBOOT",
    "FAILED",
}


class OtaUpdateError(VerificationError):
    """The running device cannot safely complete the requested CDC update."""


class OtaTransportInterrupted(TransportError):
    def __init__(
        self,
        message: str,
        *,
        serial: str,
        target_partition: str | None,
        finalization_uncertain: bool,
    ):
        super().__init__(message)
        self.serial = serial
        self.target_partition = target_partition
        self.finalization_uncertain = finalization_uncertain


class ExchangeClient(Protocol):
    def exchange(self, message_type: int, payload: dict[str, Any]) -> dict[str, Any]: ...


def _result(response: Mapping[str, Any], command: str) -> dict[str, Any]:
    result = response.get("result")
    if response.get("command") != command or not isinstance(result, dict):
        raise OtaUpdateError(f"device returned an invalid {command} response")
    return result


def _partition(value: object, role: str) -> str:
    if value not in {"ota_0", "ota_1"}:
        raise OtaUpdateError(f"device returned an invalid OTA {role} partition")
    return str(value)


def _matches_application(
    status: Mapping[str, Any], application: OtaApplication
) -> bool:
    return (
        status.get("expected_size") == application.size
        and status.get("expected_sha256") == application.sha256
        and status.get("expected_version") == application.version
    )


class CdcOtaUpdater:
    def __init__(self, *, finalization_timeout_s: float = 12.0):
        self.finalization_timeout_s = finalization_timeout_s

    def update(
        self,
        client: ExchangeClient,
        application: OtaApplication,
        *,
        restart_mismatched: bool = False,
        progress: Callable[[int, int], None] | None = None,
        expected_serial: str | None = None,
        expected_target_partition: str | None = None,
        finalization_uncertain: bool = False,
    ) -> OtaUpdateResult:
        serial = expected_serial or ""
        target_partition = expected_target_partition
        finalization_started = finalization_uncertain
        try:
            serial, previous_version, running_build_id = self._verify_identity(
                client, application
            )
            if expected_serial is not None and serial != expected_serial:
                raise OtaUpdateError(
                    f"reconnected device {serial} does not match {expected_serial}"
                )
            chunk_size = self._chunk_size(client, application)
            status = self._status(client)
            state = status["state"]

            if finalization_uncertain and state == "IDLE":
                running_partition = status.get("running_partition")
                if (
                    expected_target_partition is not None
                    and running_partition == expected_target_partition
                    and previous_version == application.version
                    and running_build_id == application.build_id
                    and status.get("rollback_pending") is False
                ):
                    return OtaUpdateResult(
                        serial=serial,
                        previous_version=previous_version,
                        target_partition=expected_target_partition,
                        received_size=application.size,
                    )
                raise OtaUpdateError(
                    "device reappeared without a confirmed target firmware after finalization"
                )

            if state in {"RECEIVING", "FINALIZING", "READY_TO_REBOOT"}:
                target_partition = _partition(status.get("target_partition"), "target")
                if not _matches_application(status, application):
                    if state != "RECEIVING" or not restart_mismatched:
                        raise OtaUpdateError(
                            "device has a different interrupted update; restart it "
                            "explicitly before uploading this package"
                        )
                    _result(client.exchange(FW_ABORT, {}), "FW_ABORT")
                    state = "IDLE"

            if state == "FAILED":
                raise OtaUpdateError("device reported a failed firmware update")
            if state == "FINALIZING":
                finalization_started = True
                self._wait_for_finalization(client, application)
            if state in {"FINALIZING", "READY_TO_REBOOT"}:
                return OtaUpdateResult(
                    serial=serial,
                    previous_version=previous_version,
                    target_partition=target_partition,
                    received_size=application.size,
                )

            if state == "RECEIVING":
                offset = status.get("received_size")
            else:
                begin = _result(
                    client.exchange(
                        FW_BEGIN,
                        {
                            "product_id": application.product_id,
                            "hardware_id": application.hardware_id,
                            "version": application.version,
                            "size": application.size,
                            "sha256": application.sha256,
                        },
                    ),
                    "FW_BEGIN",
                )
                offset = begin.get("offset")
                target_partition = _partition(begin.get("target_partition"), "target")

            if type(offset) is not int or offset < 0 or offset > application.size:
                raise OtaUpdateError("device returned an invalid resume offset")
            if target_partition is None:
                raise OtaUpdateError("device did not identify the OTA target partition")

            if progress is not None:
                progress(offset, application.size)
            with application.path.open("rb") as image:
                image.seek(offset)
                while offset < application.size:
                    chunk = image.read(min(chunk_size, application.size - offset))
                    if not chunk:
                        raise OtaUpdateError(
                            "firmware image ended before its manifest size"
                        )
                    data = _result(
                        client.exchange(
                            FW_DATA,
                            {
                                "offset": offset,
                                "data": base64.b64encode(chunk).decode("ascii"),
                            },
                        ),
                        "FW_DATA",
                    )
                    next_offset = data.get("received_size")
                    if next_offset != offset + len(chunk):
                        raise OtaUpdateError(
                            "device acknowledged an unexpected firmware offset"
                        )
                    offset = next_offset
                    if progress is not None:
                        progress(offset, application.size)

            finalization_started = True
            end = _result(client.exchange(FW_END, {}), "FW_END")
            if end.get("version") != application.version or end.get("state") not in {
                "FINALIZING",
                "READY_TO_REBOOT",
            }:
                raise OtaUpdateError(
                    "device did not finalize the requested firmware version"
                )
            if end["state"] == "FINALIZING":
                self._wait_for_finalization(client, application)
            return OtaUpdateResult(
                serial=serial,
                previous_version=previous_version,
                target_partition=target_partition,
                received_size=offset,
            )
        except TransportError as exc:
            raise OtaTransportInterrupted(
                str(exc),
                serial=serial,
                target_partition=target_partition,
                finalization_uncertain=finalization_started,
            ) from exc

    def _verify_identity(
        self, client: ExchangeClient, application: OtaApplication
    ) -> tuple[str, str, str]:
        hello = _result(
            client.exchange(
                HELLO,
                {
                    "client": {"name": "offline-provisioner", "version": "0.2.0"},
                    "protocol": {"min_major": 1, "max_major": 1, "max_minor": 0},
                },
            ),
            "HELLO",
        )
        identity = hello.get("identity")
        versions = hello.get("versions")
        if not isinstance(identity, dict) or not isinstance(versions, dict):
            raise OtaUpdateError("HELLO response is missing identity or versions")
        if identity.get("product_id") != application.product_id:
            raise OtaUpdateError("device product_id does not match the OTA package")
        if identity.get("hardware_id") != application.hardware_id:
            raise OtaUpdateError("device hardware_id does not match the OTA package")
        serial = identity.get("serial")
        version = versions.get("firmware")
        build_id = versions.get("build_id")
        if not isinstance(serial, str) or not serial:
            raise OtaUpdateError("HELLO response contains an invalid serial")
        if not isinstance(version, str) or not version:
            raise OtaUpdateError("HELLO response contains an invalid firmware version")
        if not isinstance(build_id, str) or not build_id:
            raise OtaUpdateError("HELLO response contains an invalid build_id")
        return serial, version, build_id

    def _chunk_size(
        self, client: ExchangeClient, application: OtaApplication
    ) -> int:
        capabilities = _result(client.exchange(CAPABILITIES, {}), "CAPABILITIES")
        features = capabilities.get("features")
        limits = capabilities.get("limits")
        if not isinstance(features, dict) or features.get("firmware_update") is not True:
            raise OtaUpdateError(
                "device firmware does not support USB updates; use BOOT recovery"
            )
        if not isinstance(limits, dict):
            raise OtaUpdateError("device did not advertise firmware update limits")
        advertised_chunk = limits.get("firmware_chunk_bytes")
        image_limit = limits.get("firmware_image_bytes")
        if type(advertised_chunk) is not int or advertised_chunk < 512:
            raise OtaUpdateError("device firmware chunk limit is invalid")
        if type(image_limit) is not int or application.size > image_limit:
            raise OtaUpdateError("firmware image does not fit the device OTA slot")
        return min(4096, advertised_chunk)

    def _status(self, client: ExchangeClient) -> dict[str, Any]:
        result = _result(client.exchange(FW_STATUS, {}), "FW_STATUS")
        status = result.get("firmware_update")
        if not isinstance(status, dict) or status.get("state") not in VALID_STATES:
            raise OtaUpdateError("device returned an invalid firmware update status")
        return status

    def _wait_for_finalization(
        self, client: ExchangeClient, application: OtaApplication
    ) -> None:
        deadline = time.monotonic() + self.finalization_timeout_s
        while True:
            status = self._status(client)
            if not _matches_application(status, application):
                raise OtaUpdateError("device is finalizing a different firmware image")
            state = status["state"]
            if state == "READY_TO_REBOOT":
                return
            if state == "FAILED":
                raise OtaUpdateError("device reported a failed firmware finalization")
            if state != "FINALIZING":
                raise OtaUpdateError("device left firmware finalization unexpectedly")
            if time.monotonic() >= deadline:
                raise OtaUpdateError("device did not finish firmware finalization in time")
            time.sleep(0.05)
