from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, Sequence

from .errors import DeviceError
from .models import UsbIdentity


SERIAL_BRIDGE_HINTS = (
    "esp32",
    "espressif",
    "usb jtag",
    "usb serial",
    "cp210",
    "silicon labs",
    "ch340",
    "wch",
    "ftdi",
)
SERIAL_BRIDGE_IDS = {
    (0x10C4, 0xEA60),
    (0x1A86, 0x7523),
    (0x1A86, 0x55D4),
    (0x0403, 0x6001),
}
MULTI_CHANNEL_FTDI_IDS = {
    (0x0403, 0x6010),  # FT2232H; ESP-Prog uses channel B for UART.
}


class DeviceKind(str, Enum):
    DOWNLOAD = "download"
    RUNTIME = "runtime"
    OTHER = "other"


@dataclass(frozen=True)
class DetectedDevice:
    device: str
    kind: DeviceKind
    vid: int | None
    pid: int | None
    description: str
    product: str
    serial_number: str | None
    location: str | None
    interface: str | None

    def to_dict(self) -> dict:
        value = asdict(self)
        value["kind"] = self.kind.value
        value["vid_pid"] = (
            f"{self.vid:04X}:{self.pid:04X}"
            if self.vid is not None and self.pid is not None
            else ""
        )
        return value


def load_ports() -> list:
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        raise DeviceError("缺少 pyserial，无法扫描串口设备") from exc
    return list(list_ports.comports())


def classify_port(port, runtime: UsbIdentity) -> DetectedDevice:
    pair = (port.vid, port.pid)
    text = " ".join(
        value or ""
        for value in (
            port.description,
            port.manufacturer,
            port.product,
            port.hwid,
        )
    ).lower()
    channel_text = " ".join(
        value or ""
        for value in (
            getattr(port, "interface", None),
            getattr(port, "serial_number", None),
            port.device,
        )
    ).upper()
    ftdi_uart_channel = (
        pair in MULTI_CHANNEL_FTDI_IDS
        and (
            channel_text.endswith("B")
            or "CHANNEL B" in channel_text
            or "INTERFACE 1" in channel_text
            or "RS232-HS B" in channel_text
        )
    )
    if pair == (runtime.vid_int, runtime.pid_int):
        kind = DeviceKind.RUNTIME
    elif (
        pair == (0x303A, 0x1001)
        or pair in SERIAL_BRIDGE_IDS
        or ftdi_uart_channel
    ):
        kind = DeviceKind.DOWNLOAD
    elif pair in MULTI_CHANNEL_FTDI_IDS:
        kind = DeviceKind.OTHER
    elif pair != (runtime.vid_int, runtime.pid_int) and any(
        hint in text for hint in SERIAL_BRIDGE_HINTS
    ):
        kind = DeviceKind.DOWNLOAD
    else:
        kind = DeviceKind.OTHER
    return DetectedDevice(
        device=port.device,
        kind=kind,
        vid=port.vid,
        pid=port.pid,
        description=port.description or "",
        product=port.product or "",
        serial_number=getattr(port, "serial_number", None),
        location=getattr(port, "location", None),
        interface=getattr(port, "interface", None),
    )


def detect_devices(runtime: UsbIdentity, ports: Iterable | None = None) -> list[DetectedDevice]:
    raw = load_ports() if ports is None else list(ports)
    return sorted(
        (classify_port(port, runtime) for port in raw),
        key=lambda item: item.device,
    )


def select_download_ports(
    ports: Sequence,
    runtime: UsbIdentity,
    requested: Sequence[str] | None = None,
) -> list[DetectedDevice]:
    detected = detect_devices(runtime, ports)
    downloads = [item for item in detected if item.kind is DeviceKind.DOWNLOAD]
    if requested is not None:
        by_name = {item.device: item for item in downloads}
        missing = [name for name in requested if name not in by_name]
        if missing:
            raise DeviceError(
                "以下设备不是可用下载端口: " + ", ".join(missing)
            )
        return [by_name[name] for name in requested]
    if not downloads:
        runtime_devices = [
            item.device for item in detected if item.kind is DeviceKind.RUNTIME
        ]
        detail = (
            "；检测到应用设备 " + ", ".join(runtime_devices)
            if runtime_devices
            else ""
        )
        raise DeviceError("未发现处于下载模式的 ESP32-S3" + detail)
    return downloads


def select_runtime_ports(
    ports: Sequence,
    runtime: UsbIdentity,
    requested: Sequence[str] | None = None,
) -> list[DetectedDevice]:
    detected = detect_devices(runtime, ports)
    running = [item for item in detected if item.kind is DeviceKind.RUNTIME]
    if requested is not None:
        by_name = {item.device: item for item in running}
        missing = [name for name in requested if name not in by_name]
        if missing:
            raise DeviceError(
                "以下设备不是可用的运行设备: " + ", ".join(missing)
            )
        return [by_name[name] for name in requested]
    if not running:
        raise DeviceError("未发现可用于 USB CDC OTA 的运行设备")
    return running
