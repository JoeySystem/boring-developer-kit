from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Sequence

from .errors import FlashToolError
from .models import ChipInfo, FlashImage
from .paths import resource_root


CHIP_PATTERNS = (
    re.compile(r"Chip type:\s*(ESP32-S3)", re.IGNORECASE),
    re.compile(r"Chip is\s+(ESP32-S3)", re.IGNORECASE),
    re.compile(r"Connected to\s+(ESP32-S3)", re.IGNORECASE),
)
MAC_PATTERN = re.compile(r"\bMAC:\s*([0-9A-Fa-f:]{17})\b")
FLASH_PATTERNS = (
    re.compile(r"Detected flash size:\s*(\d+\s*(?:MB|KB))", re.IGNORECASE),
    re.compile(r"Flash size:\s*(\d+\s*(?:MB|KB))", re.IGNORECASE),
)


def parse_chip_info(output: str) -> ChipInfo:
    chip = next(
        (match.group(1).upper() for pattern in CHIP_PATTERNS if (match := pattern.search(output))),
        None,
    )
    mac_match = MAC_PATTERN.search(output)
    flash_size = next(
        (
            re.sub(r"\s+", "", match.group(1)).upper()
            for pattern in FLASH_PATTERNS
            if (match := pattern.search(output))
        ),
        None,
    )
    missing = [
        label
        for label, value in (("chip", chip), ("MAC", mac_match), ("flash size", flash_size))
        if not value
    ]
    if missing:
        raise FlashToolError(
            "无法从 esptool 输出识别 " + ", ".join(missing) + "；请检查下载模式和烧录器"
        )
    return ChipInfo(
        chip=chip,
        mac=mac_match.group(1).upper(),
        flash_size=flash_size,
    )


class EsptoolRunner:
    def __init__(self, python: str | Path | None = None, baud: int = 460800):
        self.python = str(python or sys.executable)
        self.baud = baud

    def _base(self, port: str, *, after: str = "no-reset") -> list[str]:
        if getattr(sys, "frozen", False):
            helper_name = (
                "factory-tool-helper.exe" if os.name == "nt"
                else "factory-tool-helper"
            )
            command = [str(resource_root() / "bin" / helper_name), "esptool"]
        else:
            command = [self.python, "-m", "esptool"]
        return command + [
            "--chip",
            "esp32s3",
            "--port",
            port,
            "--baud",
            str(self.baud),
            "--before",
            "default-reset",
            "--after",
            after,
        ]

    def _run(self, command: Sequence[str], *, timeout: float = 45.0) -> str:
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            result = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
                env=environment,
                timeout=timeout,
                start_new_session=os.name != "nt",
            )
        except subprocess.TimeoutExpired as exc:
            raise FlashToolError(
                f"esptool 超过 {timeout:.0f} 秒仍未完成，已终止本次操作；"
                "请检查烧录器、供电和串口连接"
            ) from exc
        except OSError as exc:
            raise FlashToolError(f"无法启动 esptool: {exc}") from exc
        output = "\n".join(value for value in (result.stdout, result.stderr) if value)
        if result.returncode != 0:
            detail = output.strip() or f"exit code {result.returncode}"
            raise FlashToolError(f"esptool 执行失败：{detail}")
        return output

    def inspect(self, port: str) -> ChipInfo:
        flash_output = self._run(self._base(port) + ["flash-id"], timeout=30)
        mac_output = self._run(self._base(port) + ["read-mac"], timeout=30)
        return parse_chip_info(flash_output + "\n" + mac_output)

    def _transfer_timeout(self, size: int, *, base: float, margin: float) -> float:
        serial_seconds = size * 10 / max(self.baud, 1)
        return max(45.0, base + serial_seconds * margin)

    def read_flash(self, port: str, offset: int, size: int) -> bytes:
        with tempfile.TemporaryDirectory(prefix="provisioner-read-") as directory:
            destination = Path(directory) / "region.bin"
            command = self._base(port) + [
                "read-flash",
                "--no-progress",
                f"0x{offset:X}",
                str(size),
                str(destination),
            ]
            self._run(
                command,
                timeout=self._transfer_timeout(size, base=30, margin=4),
            )
            try:
                data = destination.read_bytes()
            except OSError as exc:
                raise FlashToolError(f"无法读取 esptool 导出的 Flash 数据：{exc}") from exc
        if len(data) != size:
            raise FlashToolError(
                f"Flash 读取长度错误：期望 {size} 字节，实际 {len(data)} 字节"
            )
        return data

    def write_flash(self, port: str, images: Sequence[FlashImage]) -> None:
        if not images:
            raise FlashToolError("没有需要烧录的固件段")
        command = self._base(port, after="hard-reset") + [
            "write-flash",
            "--flash-mode",
            "dio",
            "--flash-freq",
            "80m",
            "--flash-size",
            "16MB",
            "--no-progress",
        ]
        for image in images:
            command.extend((f"0x{image.offset:X}", str(image.path)))
        total_size = sum(image.size for image in images)
        self._run(
            command,
            timeout=self._transfer_timeout(total_size, base=60, margin=5),
        )
