from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from controller_config.screen_icon_transfer import ScreenIconTransfer, TOTAL


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QApplication([])


def snapshot(serial="CP01-AABBCCDDEEFF", supported=True):
    return SimpleNamespace(identity={"serial": serial, "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        port_name="/dev/test", capabilities={"features": {"custom_home_icon": supported},
        "screen_icon": {"version": 1, "target": "normal_home", "format": "rgb565_le",
        "width": 128, "height": 128, "total_bytes": TOTAL, "max_chunk_bytes": 1024,
        "session_timeout_ms": 15000}})


class Device:
    def __init__(self, contract, app):
        self.app = app
        self.pending = []
        self.commands = []
        self.transfer = ScreenIconTransfer(contract, self.send)
        self.transfer.attach(snapshot())
        self.revision = 0
        self.pixels = None
        self.buffer = bytearray()

    def send(self, command):
        self.commands.append(command)
        self.pending.append(command)

    def metadata(self):
        return {"revision": self.revision, "source": "custom" if self.pixels is not None else "default",
            "target": "normal_home", "format": "rgb565_le", "width": 128, "height": 128,
            "total_bytes": TOTAL if self.pixels is not None else 0}

    def next(self):
        self.app.processEvents()
        assert self.pending
        return self.pending.pop(0)

    def result(self, command):
        name, payload = command.name.removeprefix("SCREEN_ICON_"), command.payload
        if name == "GET":
            return self.metadata()
        if name == "BEGIN":
            assert payload["base_revision"] == self.revision
            self.buffer.clear()
            return {"upload_id": 9, "next_offset": 0, "max_chunk_bytes": 1024}
        if name == "DATA":
            assert payload["offset"] == len(self.buffer)
            self.buffer.extend(base64.b64decode(payload["data"]))
            return {"next_offset": len(self.buffer)}
        if name == "COMMIT":
            self.pixels = bytes(self.buffer)
            self.revision += 1
            return self.metadata()
        if name == "RESET":
            assert payload["base_revision"] == self.revision
            self.pixels = None
            self.revision += 1
            return self.metadata()
        if name == "READ":
            assert payload["revision"] == self.revision
            offset, length = payload["offset"], payload["length"]
            return {"revision": self.revision, "offset": offset,
                "data": base64.b64encode(self.pixels[offset:offset+length]).decode()}
        if name == "ABORT":
            self.buffer.clear()
            return {"aborted": True}
        raise AssertionError(name)

    def respond(self, command, result=None):
        self.transfer.completed(command.name, {"result": self.result(command) if result is None else result})

    def finish(self):
        for _ in range(300):
            if not self.transfer.busy:
                return
            self.respond(self.next())
        raise AssertionError("transfer did not finish")

    def until(self, suffix):
        for _ in range(150):
            command = self.next()
            if command.name == "SCREEN_ICON_" + suffix:
                return command
            self.respond(command)
        raise AssertionError("command not reached")


def test_upload_swap_and_reset_have_exact_readback(contract, app):
    d = Device(contract, app)
    for pixels in (b"\x00\xf8" * (TOTAL // 2), b"\xe0\x07" * (TOTAL // 2)):
        d.transfer.upload(pixels)
        d.finish()
        assert d.transfer.verified_upload == pixels == d.transfer.pixels == d.pixels
        assert d.transfer.progress == 100
        assert all(len(base64.b64decode(c.payload["data"])) <= 512 for c in d.commands if c.name.endswith("_DATA"))
    d.commands.clear()
    d.transfer.reset()
    assert d.transfer.verified_upload is None
    d.finish()
    assert d.transfer.metadata["source"] == "default"
    assert d.transfer.pixels is None
    assert d.revision == 3
    assert all(not c.name.endswith("_READ") for c in d.commands)


@pytest.mark.parametrize("capability,writable", [(False, True), (True, False)])
def test_unsupported_and_unauthorized_never_send(contract, app, capability, writable):
    d = Device(contract, app)
    d.transfer.attach(snapshot(supported=capability), writable=writable)
    d.transfer.refresh(); d.transfer.upload(bytes(TOTAL)); d.transfer.reset()
    app.processEvents()
    assert d.commands == []


@pytest.mark.parametrize("bad", [{"next_offset": 1}, {"next_offset": True}, {"next_offset": 1024}])
def test_bad_data_offset_aborts_without_commit(contract, app, bad):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL))
    command = d.until("DATA")
    d.respond(command, bad)
    abort = d.next()
    assert abort.name.endswith("_ABORT")
    d.respond(abort)
    assert d.transfer.state == "error"
    assert d.pixels is None


@pytest.mark.parametrize("field,value", [("revision", 2), ("offset", 1), ("data", "not base64"), ("data", "AA==")])
def test_bad_read_never_confirms_upload(contract, app, field, value):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL))
    command = d.until("READ")
    result = d.result(command)
    result[field] = value
    d.respond(command, result)
    assert d.transfer.verified_upload is None
    assert d.transfer.state == "unknown"
    assert not d.transfer.busy


def test_matching_metadata_but_wrong_pixels_not_success(contract, app):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL))
    command = d.until("READ")
    d.pixels = b"\xff" * TOTAL
    d.respond(command)
    d.finish()
    assert d.transfer.state == "error"
    assert d.transfer.pixels == d.pixels
    assert d.transfer.verified_upload is None


def test_cancel_before_dispatch_and_cancel_live_upload(contract, app):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL)); d.transfer.cancel()
    app.processEvents()
    assert d.commands == []
    d.transfer.upload(bytes(TOTAL))
    command = d.until("DATA")
    d.transfer.cancel()
    d.respond(command)
    abort = d.next()
    assert abort.name.endswith("_ABORT")
    d.respond(abort)
    assert not d.transfer.busy and d.pixels is None


def test_lost_commit_response_readback_only_no_mutation_replay(contract, app):
    d = Device(contract, app)
    candidate = b"\xf8\x00" * (TOTAL // 2)
    d.transfer.upload(candidate)
    command = d.until("COMMIT")
    d.result(command)  # persisted, response lost
    d.transfer.failed(command.name, RuntimeError("timeout"))
    d.finish()
    assert d.transfer.verified_upload == candidate
    assert len([c for c in d.commands if c.name.endswith("_COMMIT")]) == 1


def test_disconnect_unknown_isolated_by_identity_and_recovered(contract, app):
    d = Device(contract, app)
    candidate = bytes(TOTAL)
    d.transfer.upload(candidate)
    command = d.until("COMMIT")
    d.result(command)
    d.transfer.attach(None)
    d.transfer.attach(snapshot("CP01-112233445566"))
    assert d.transfer.state == "idle" and d.transfer.verified_upload is None
    d.transfer.attach(None)
    d.transfer.attach(snapshot())
    assert d.transfer.state == "unknown"
    d.transfer.refresh(); d.finish()
    assert d.transfer.verified_upload == candidate
    assert len([c for c in d.commands if c.name.endswith("_BEGIN")]) == 1


def test_disconnect_invalidates_scheduled_command(contract, app):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL))
    d.transfer.attach(None)
    app.processEvents()
    assert not d.commands


def test_reset_lost_response_resolves_default_without_read(contract, app):
    d = Device(contract, app)
    d.transfer.reset()
    command = d.until("RESET")
    d.result(command)
    d.transfer.failed(command.name, RuntimeError("timeout"))
    d.finish()
    assert d.transfer.metadata["source"] == "default"
    assert d.revision == 1
    assert not any(c.name.endswith("_READ") for c in d.commands)


def test_conflict_reads_actual_image_without_retrying_mutation(contract, app):
    d = Device(contract, app)
    d.transfer.upload(bytes(TOTAL))
    command = d.until("BEGIN")
    d.pixels = b"\x01" * TOTAL
    d.revision = 4
    error = RuntimeError("base revision changed")
    error.error_name = "GENERATION_CONFLICT"
    d.transfer.failed(command.name, error)
    d.finish()
    assert d.transfer.state == "error"
    assert d.transfer.pixels == d.pixels
    assert d.transfer.verified_upload is None
    assert len([c for c in d.commands if c.name.endswith("_BEGIN")]) == 1
