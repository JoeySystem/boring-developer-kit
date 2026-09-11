import base64
from types import SimpleNamespace

import pytest

from controller_config.screen_glyph_transfer import ScreenGlyphTransfer
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind


CATALOG = [dict(id=name, resource_id=i, width=3, height=2, editable=name != "warning")
           for i, name in enumerate(("timer", "settings", "warning"))]


def snapshot(serial="CP01-AABBCCDDEEFF", supported=True):
    return SimpleNamespace(identity={"serial": serial, "hardware_id": "WMP-S3-MATRIX12-POWER-V2"},
        port_name="test", capabilities={"features": {"custom_glyph_icons": supported},
        "screen_glyphs": {"version": 1, "format": "alpha8", "count": 3}})


class Device:
    def __init__(self, qtbot):
        self.qtbot = qtbot
        self.commands = []
        self.pending = []
        self.records = {c["id"]: dict(id=c["id"], revision=0, source="default", width=3,
            height=2, format="alpha8", data=base64.b64encode(b"\xff" * 6).decode()) for c in CATALOG}
        self.transfer = ScreenGlyphTransfer(self.send)
        self.transfer.attach(snapshot())

    def send(self, command):
        self.commands.append(command)
        self.pending.append(command)

    def next(self):
        self.qtbot.waitUntil(lambda: bool(self.pending))
        return self.pending.pop(0)

    def result(self, command):
        if command.name.endswith("_LIST"):
            return dict(version=1, format="alpha8", icons=CATALOG)
        record = self.records[command.payload["id"]]
        if command.name.endswith(("_SET", "_RESET")):
            assert record["revision"] == command.payload["base_revision"]
            record["revision"] += 1
            if command.name.endswith("_SET"):
                record.update(source="custom", data=command.payload["data"])
            else:
                record.update(source="default", data=base64.b64encode(b"\xff" * 6).decode())
        return dict(record)

    def reply(self, command):
        self.transfer.completed(command.name, {"result": self.result(command)})

    def finish(self):
        for _ in range(10):
            if not self.transfer.busy:
                return
            self.reply(self.next())
        raise AssertionError("operation did not finish")

    def start(self):
        self.transfer.load_catalog()
        self.finish()


def test_two_icons_independent_replace_reset_and_reconnect(qtbot):
    device = Device(qtbot)
    t = device.transfer
    device.start()
    t.write(b"123456")
    device.finish()
    assert t.verified_write == ("timer", b"123456")
    t.select("settings")
    device.finish()
    assert t.metadata["source"] == "default"
    t.write(b"abcdef")
    device.finish()
    t.select("timer")
    device.finish()
    assert t.pixels == b"123456"
    t.reset()
    device.finish()
    assert t.metadata["source"] == "default"
    assert device.records["settings"]["revision"] == 1
    t.attach(None)
    t.attach(snapshot())
    device.start()
    t.select("settings")
    device.finish()
    assert t.pixels == b"abcdef"
    assert all(c.name.startswith("SCREEN_GLYPH_") for c in device.commands)


def test_unknown_write_is_read_back_without_replaying_set(qtbot):
    device = Device(qtbot)
    t = device.transfer
    device.start()
    t.write(b"123456")
    device.reply(device.next())
    command = device.next()
    assert command.name == "SCREEN_GLYPH_SET"
    device.result(command)
    t.failed(command.name, BootstrapError(BootstrapKind.READ_FAILED, "ACK lost", error_name="TRANSPORT_TIMEOUT"))
    device.finish()
    assert t.verified_write == ("timer", b"123456")
    assert len([c for c in device.commands if c.name.endswith("_SET")]) == 1


def test_disconnect_pending_is_scoped_to_device_and_icon(qtbot):
    device = Device(qtbot)
    t = device.transfer
    device.start()
    t.write(b"123456")
    device.reply(device.next())
    command = device.next()
    device.result(command)
    t.attach(None)
    t.attach(snapshot("CP01-112233445566"))
    assert t.device_key[0] == "CP01-112233445566"
    assert not t.busy
    assert not device.pending
    t.attach(None)
    t.attach(snapshot())
    device.start()
    assert t.verified_write == ("timer", b"123456")
    assert len([c for c in device.commands if c.name.endswith("_SET")]) == 1


def test_readonly_icons_old_firmware_and_invalid_payload(qtbot):
    device = Device(qtbot)
    t = device.transfer
    t.attach(snapshot(supported=False))
    t.load_catalog()
    t.write(b"123456")
    assert not device.commands
    t.attach(None)
    t.attach(snapshot())
    device.start()
    with pytest.raises(ValueError):
        t.write(b"short")
    t.select("warning")
    device.finish()
    with pytest.raises(ValueError, match="内置系统"):
        t.reset()


def test_incorrect_get_cannot_clear_or_verify_candidate(qtbot):
    device = Device(qtbot)
    device.start()
    t = device.transfer
    t.refresh()
    command = device.next()
    invalid = device.result(command)
    invalid["id"] = "settings"
    t.completed(command.name, {"result": invalid})
    assert not t.busy
    assert "不一致" in t.status
    assert t.verified_write is None
