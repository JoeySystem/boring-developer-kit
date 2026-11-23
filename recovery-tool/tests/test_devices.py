from __future__ import annotations

from types import SimpleNamespace
import unittest

from provisioner.devices import (
    DeviceKind,
    classify_port,
    select_download_ports,
    select_runtime_ports,
)
from provisioner.errors import DeviceError
from provisioner.models import UsbIdentity


def port(
    device,
    vid=None,
    pid=None,
    description="",
    product="",
    manufacturer="",
    hwid="",
    serial_number=None,
    interface=None,
):
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        description=description,
        product=product,
        manufacturer=manufacturer,
        hwid=hwid,
        serial_number=serial_number,
        location=None,
        interface=interface,
    )


class DeviceDetectionTest(unittest.TestCase):
    def setUp(self):
        self.runtime = UsbIdentity(product="Codex Micro", vid="303A", pid="8360")

    def test_distinguishes_rom_runtime_and_bridge_ports(self):
        self.assertEqual(
            DeviceKind.DOWNLOAD,
            classify_port(port("rom", 0x303A, 0x1001, "USB JTAG/serial debug unit"), self.runtime).kind,
        )
        self.assertEqual(
            DeviceKind.RUNTIME,
            classify_port(port("app", 0x303A, 0x8360, product="Codex Micro"), self.runtime).kind,
        )
        self.assertEqual(
            DeviceKind.DOWNLOAD,
            classify_port(port("bridge", 0x1A86, 0x7523, "USB-SERIAL CH340"), self.runtime).kind,
        )
        self.assertEqual(
            DeviceKind.OTHER,
            classify_port(port("bt", description="Bluetooth-Incoming-Port"), self.runtime).kind,
        )

    def test_batch_selection_is_deterministic_and_requires_download_mode(self):
        ports = [
            port("/dev/cu.z", 0x303A, 0x1001, "ESP32-S3"),
            port("/dev/cu.a", 0x1A86, 0x7523, "CH340"),
            port("/dev/cu.runtime", 0x303A, 0x8360, product="Codex Micro"),
        ]
        selected = select_download_ports(ports, self.runtime)
        self.assertEqual(["/dev/cu.a", "/dev/cu.z"], [item.device for item in selected])

        with self.assertRaisesRegex(DeviceError, "下载模式"):
            select_download_ports([ports[-1]], self.runtime)

        self.assertEqual([], select_download_ports(ports, self.runtime, requested=[]))

    def test_esp_prog_selects_uart_channel_b_not_jtag_channel_a(self):
        ports = [
            port(
                "/dev/cu.esp-prog-A",
                0x0403,
                0x6010,
                "FT2232H",
                serial_number="ESPPRGA",
                interface="Dual RS232-HS A",
            ),
            port(
                "/dev/cu.esp-prog-B",
                0x0403,
                0x6010,
                "FT2232H",
                serial_number="ESPPRGB",
                interface="Dual RS232-HS B",
            ),
        ]
        selected = select_download_ports(ports, self.runtime)
        self.assertEqual(["/dev/cu.esp-prog-B"], [item.device for item in selected])

    def test_ota_selection_accepts_only_running_product_ports(self):
        ports = [
            port("/dev/cu.rom", 0x303A, 0x1001, "ESP32-S3"),
            port("/dev/cu.runtime", 0x303A, 0x8360, product="Codex Micro"),
        ]

        selected = select_runtime_ports(
            ports,
            self.runtime,
            requested=["/dev/cu.runtime"],
        )

        self.assertEqual(["/dev/cu.runtime"], [item.device for item in selected])
        with self.assertRaisesRegex(DeviceError, "运行设备"):
            select_runtime_ports(ports, self.runtime, requested=["/dev/cu.rom"])


if __name__ == "__main__":
    unittest.main()
