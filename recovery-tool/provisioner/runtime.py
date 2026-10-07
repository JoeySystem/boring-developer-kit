from __future__ import annotations

import time

from .devices import load_ports
from .errors import OperatorActionRequired, VerificationError
from .models import RuntimeVerification, UsbIdentity
from .protocol import WmpClient


def select_runtime_port(ports, identity: UsbIdentity):
    matches = [
        port
        for port in ports
        if port.vid == identity.vid_int
        and port.pid == identity.pid_int
        and (
            not getattr(port, "product", None)
            or getattr(port, "product", None) == identity.product
        )
    ]
    if len(matches) > 1:
        labels = ", ".join(port.device for port in matches)
        raise VerificationError(
            f"检测到多个相同的运行设备（{labels}），无法确定刚烧录的板子"
        )
    return matches[0] if matches else None


def serial_for_mac(mac: str) -> str:
    compact = mac.replace(":", "").replace("-", "").upper()
    if len(compact) != 12 or any(
        character not in "0123456789ABCDEF"
        for character in compact
    ):
        raise VerificationError(f"芯片 MAC 无效：{mac}")
    return f"CP01-{compact}"


class RuntimeVerifier:
    def __init__(self, poll_interval_s: float = 0.5):
        self.poll_interval_s = poll_interval_s

    def capture_existing(self, identity: UsbIdentity) -> frozenset[str]:
        return frozenset(
            port.device
            for port in load_ports()
            if port.vid == identity.vid_int and port.pid == identity.pid_int
        )

    @staticmethod
    def _verify_candidate(
        serial_module,
        candidate,
        identity: UsbIdentity,
        expected_hardware_id: str,
        expected_build_id: str | None,
        expected_partition: str | None = None,
    ) -> RuntimeVerification:
        with serial_module.Serial(
            candidate.device,
            115200,
            timeout=0.05,
            write_timeout=1,
        ) as connection:
            client = WmpClient(connection)
            wmp = client.verify(
                expected_hardware_id=expected_hardware_id,
                expected_build_id=expected_build_id,
            )
            running_partition = ""
            rollback_pending = None
            if expected_partition is not None:
                update = client.get_status().get("firmware_update")
                if not isinstance(update, dict):
                    raise VerificationError("GET_STATUS 未返回 firmware_update")
                running_partition = update.get("running_partition", "")
                rollback_pending = update.get("rollback_pending")
                if running_partition != expected_partition:
                    raise VerificationError(
                        f"设备运行分区 {running_partition or 'missing'}，期望 {expected_partition}"
                    )
                if rollback_pending is not False:
                    raise VerificationError("新固件尚未取消 bootloader rollback")
        return RuntimeVerification(
            port=candidate.device,
            product=identity.product,
            vid=identity.vid,
            pid=identity.pid,
            product_id=wmp.product_id,
            hardware_id=wmp.hardware_id,
            build_id=wmp.build_id,
            serial=wmp.serial,
            config_generation=wmp.config_generation,
            running_partition=running_partition,
            rollback_pending=rollback_pending,
        )

    def verify(
        self,
        identity: UsbIdentity,
        timeout_s: float,
        existing_runtime_devices: frozenset[str],
        expected_mac: str,
        expected_hardware_id: str = "WMP-S3-REV-A",
        expected_build_id: str | None = None,
    ):
        try:
            import serial
        except ImportError as exc:
            raise VerificationError("缺少 pyserial，无法验证运行固件") from exc

        deadline = time.monotonic() + timeout_s
        last_error = "设备尚未枚举"
        runtime_candidate_seen = False
        while time.monotonic() < deadline:
            try:
                ports = load_ports()
                new_ports = [
                    port
                    for port in ports
                    if port.device not in existing_runtime_devices
                ]
                selected = select_runtime_port(new_ports, identity)
                if selected is None and not existing_runtime_devices:
                    selected = select_runtime_port(ports, identity)
                if selected is not None:
                    runtime_candidate_seen = True
                    result = self._verify_candidate(
                        serial,
                        selected,
                        identity,
                        expected_hardware_id,
                        expected_build_id,
                    )
                    expected_serial = serial_for_mac(expected_mac)
                    if result.serial != expected_serial:
                        raise VerificationError(
                            f"HELLO serial {result.serial} 不属于本轮芯片 {expected_serial}"
                        )
                    return result
            except (OSError, serial.SerialException, VerificationError) as exc:
                last_error = str(exc)
            time.sleep(self.poll_interval_s)
        if not runtime_candidate_seen:
            raise OperatorActionRequired(
                "固件写入已完成，运行验证尚未完成。请松开 BOOT，拔下 USB，"
                "再重新插入；不要把此状态判定为 PCB 不良"
            )
        raise VerificationError(
            f"固件已写入，但 {timeout_s:g} 秒内未通过运行验证：{last_error}"
        )

    def verify_serial(
        self,
        identity: UsbIdentity,
        timeout_s: float,
        expected_serial: str,
        expected_hardware_id: str,
        expected_build_id: str,
        expected_partition: str,
    ) -> RuntimeVerification:
        try:
            import serial
        except ImportError as exc:
            raise VerificationError("缺少 pyserial，无法验证运行固件") from exc

        deadline = time.monotonic() + timeout_s
        last_error = "设备尚未重新枚举"
        while time.monotonic() < deadline:
            candidates = [
                port
                for port in load_ports()
                if port.vid == identity.vid_int and port.pid == identity.pid_int
            ]
            matching_serial = [
                port
                for port in candidates
                if getattr(port, "serial_number", None) == expected_serial
            ]
            unknown_serial = [
                port
                for port in candidates
                if not getattr(port, "serial_number", None)
            ]
            candidates = matching_serial + unknown_serial
            for candidate in candidates:
                try:
                    result = self._verify_candidate(
                        serial,
                        candidate,
                        identity,
                        expected_hardware_id,
                        expected_build_id,
                        expected_partition,
                    )
                    if result.serial != expected_serial:
                        last_error = f"{candidate.device} 属于其他设备 {result.serial}"
                        continue
                    return result
                except (OSError, serial.SerialException, VerificationError) as exc:
                    last_error = str(exc)
            time.sleep(self.poll_interval_s)
        raise VerificationError(
            f"OTA 传输完成，但 {timeout_s:g} 秒内未读回目标 build_id：{last_error}"
        )

    def find_serial_port(
        self,
        identity: UsbIdentity,
        timeout_s: float,
        expected_serial: str,
        expected_hardware_id: str,
    ) -> str:
        try:
            import serial
        except ImportError as exc:
            raise VerificationError("缺少 pyserial，无法重连 OTA 设备") from exc

        deadline = time.monotonic() + timeout_s
        last_error = "设备尚未重新枚举"
        while time.monotonic() < deadline:
            ports = [
                port
                for port in load_ports()
                if port.vid == identity.vid_int and port.pid == identity.pid_int
            ]
            matching = [
                port
                for port in ports
                if getattr(port, "serial_number", None) == expected_serial
            ]
            unknown = [
                port for port in ports if not getattr(port, "serial_number", None)
            ]
            for candidate in matching + unknown:
                try:
                    result = self._verify_candidate(
                        serial,
                        candidate,
                        identity,
                        expected_hardware_id,
                        None,
                    )
                    if result.serial == expected_serial:
                        return result.port
                    last_error = f"{candidate.device} 属于其他设备 {result.serial}"
                except (OSError, serial.SerialException, VerificationError) as exc:
                    last_error = str(exc)
            time.sleep(self.poll_interval_s)
        raise VerificationError(
            f"{timeout_s:g} 秒内未重新找到 {expected_serial}：{last_error}"
        )
