from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from typing import Callable

from .errors import CompatibilityError, OperatorActionRequired
from .history import HistoryStore
from .models import ProvisionMode, ProvisionRequest, ProvisionResult
from .ota import CdcOtaUpdater, OtaTransportInterrupted
from .protocol import WmpClient


EventCallback = Callable[[str, str, int], None]


def _no_event(_stage: str, _message: str, _progress: int) -> None:
    pass


class Provisioner:
    def __init__(self, flash_runner, verifier, history_root: Path, ota_updater=None):
        self.flash_runner = flash_runner
        self.verifier = verifier
        self.history = HistoryStore(history_root)
        self.ota_updater = ota_updater or CdcOtaUpdater()

    @staticmethod
    def _open_serial(port: str):
        try:
            import serial
        except ImportError as exc:
            raise CompatibilityError("缺少 pyserial，无法执行 USB CDC OTA") from exc
        return serial.Serial(port, 115200, timeout=0.05, write_timeout=1)

    def _read_regions(self, port, images):
        start = min(image.offset for image in images)
        end = max(image.offset + image.size for image in images)
        raw = self.flash_runner.read_flash(port, start, end - start)
        return {
            image.offset: raw[
                image.offset - start : image.offset - start + image.size
            ]
            for image in images
        }

    def _verify_images(self, port, images, mismatch_message) -> None:
        installed_regions = self._read_regions(port, images)
        for image in images:
            installed = installed_regions[image.offset]
            actual = hashlib.sha256(installed).hexdigest()
            if actual != image.sha256:
                raise CompatibilityError(mismatch_message(image))

    def _verify_baseline(self, request: ProvisionRequest, event: EventCallback) -> None:
        event("preflight", "正在读取并核对 bootloader 与分区表", 20)
        images = (request.package.bootloader, request.package.partition_table)
        labels = {
            request.package.bootloader.offset: "bootloader",
            request.package.partition_table.offset: "partition table",
        }
        self._verify_images(
            request.port,
            images,
            lambda image: (
                f"板上 {labels[image.offset]} 与发布包不匹配；请改用“首次安装”，"
                    "不要进行应用固件单段更新"
            ),
        )

    def _verify_readback(self, request: ProvisionRequest, event: EventCallback) -> None:
        event("readback", "正在从烧录器逐段读回并核对 SHA-256", 78)
        self._verify_images(
            request.port,
            request.package.flash_images,
            lambda image: (
                f"0x{image.offset:X} {image.relative_path} 读回校验失败"
            ),
        )

    def run(
        self,
        request: ProvisionRequest,
        event: EventCallback | None = None,
    ) -> ProvisionResult:
        notify = event or _no_event
        started = datetime.now(timezone.utc).isoformat()
        chip = None
        images = ()
        operation_path = None
        try:
            notify("inspect", "正在识别芯片、MAC 与 Flash 容量", 5)
            chip = self.flash_runner.inspect(request.port)
            if chip.chip.upper() != "ESP32-S3":
                raise CompatibilityError(f"仅支持 ESP32-S3，检测到 {chip.chip}")
            if chip.flash_size.upper() != "16MB":
                raise CompatibilityError(
                    f"发布包要求 16MB Flash，检测到 {chip.flash_size}"
                )

            full_recovery = (
                request.mode is ProvisionMode.RECOVERY
                and request.package.ota_application is not None
            )
            if request.mode not in (
                ProvisionMode.FIRST_INSTALL,
                ProvisionMode.CHIP_PRELOAD,
            ) and not full_recovery:
                self._verify_baseline(request, notify)

            images = (
                request.package.flash_images
                if request.mode
                in (ProvisionMode.FIRST_INSTALL, ProvisionMode.CHIP_PRELOAD)
                or full_recovery
                else (request.package.application,)
            )
            scope = (
                f"{len(images)} 段完整安装"
                if len(images) > 1
                else "仅应用固件（保留 NVS）"
            )
            if request.mode is not ProvisionMode.CHIP_PRELOAD:
                existing_runtime_devices = self.verifier.capture_existing(
                    request.package.runtime_usb
                )
            operation_path = self.history.begin(
                self._base_receipt(request, started, chip, images)
            )
            notify("flash", f"正在烧录：{scope}", 45)
            self.flash_runner.write_flash(request.port, images)
            if request.mode is ProvisionMode.CHIP_PRELOAD:
                self._verify_readback(request, notify)
                runtime = None
                verification = "flash-readback"
            else:
                notify(
                    "operator-action",
                    "固件写入完成。请松开 BOOT，拔下 USB，再重新插入；"
                    "无需再次点击烧录，工具会自动继续验证",
                    80,
                )
                runtime = self.verifier.verify(
                    request.package.runtime_usb,
                    request.verify_timeout_s,
                    existing_runtime_devices,
                    chip.mac,
                    request.package.hardware_id or "WMP-S3-REV-A",
                    request.package.build_id,
                )
                verification = "runtime-wmp"
            receipt = self._base_receipt(request, started, chip, images)
            receipt.update(
                {
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "success": True,
                    "status": "completed",
                    "verification": verification,
                    "runtime": asdict(runtime) if runtime is not None else {},
                }
            )
            receipt_path = self.history.finish(operation_path, receipt)
            message = (
                f"{len(images)} 段读回校验通过"
                if request.mode is ProvisionMode.CHIP_PRELOAD
                else "运行验证通过"
            )
            notify("done", f"{message}，凭证已保存：{receipt_path.name}", 100)
            return ProvisionResult(
                release_id=request.package.release_id,
                mode=request.mode,
                chip=chip,
                runtime=runtime,
                receipt_path=receipt_path,
            )
        except Exception as exc:
            action_required = isinstance(exc, OperatorActionRequired)
            receipt = self._base_receipt(request, started, chip, images)
            receipt.update(
                {
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "success": None if action_required else False,
                    "status": (
                        "operator-action-required" if action_required else "failed"
                    ),
                    "runtime": {},
                    "error": str(exc),
                }
            )
            try:
                failure_path = (
                    self.history.finish(operation_path, receipt)
                    if operation_path is not None
                    else self.history.write(receipt)
                )
                if action_required:
                    notify(
                        "operator-action-required",
                        f"{exc}；待验证记录：{failure_path.name}",
                        85,
                    )
                else:
                    notify("error", f"{exc}；失败凭证：{failure_path.name}", 100)
            except OSError:
                notify(
                    "operator-action-required" if action_required else "error",
                    str(exc),
                    85 if action_required else 100,
                )
            raise

    def run_ota(
        self,
        request: ProvisionRequest,
        event: EventCallback | None = None,
        *,
        restart_mismatched: bool = False,
    ) -> ProvisionResult:
        if request.mode is not ProvisionMode.UPDATE:
            raise CompatibilityError("USB CDC OTA 只用于常规更新")
        application = request.package.ota_application
        if application is None:
            raise CompatibilityError("当前发布包不支持 USB CDC OTA")

        notify = event or _no_event
        started = datetime.now(timezone.utc).isoformat()
        operation_path = None
        ota_result = None
        images = (request.package.application,)
        try:
            operation_path = self.history.begin(
                self._base_receipt(request, started, None, images)
            )
            notify("ota", "已连接运行设备，正在核对身份与 OTA 能力", 5)

            def progress(current: int, total: int) -> None:
                percent = 10 + int((current / total) * 65) if total else 10
                notify("ota", f"正在传输固件 {current}/{total} 字节", percent)

            current_port = request.port
            expected_serial = None
            expected_target_partition = None
            finalization_uncertain = False
            reconnect_deadline = None
            while ota_result is None:
                try:
                    with self._open_serial(current_port) as connection:
                        client = WmpClient(connection, timeout_s=4.0)
                        ota_result = self.ota_updater.update(
                            client,
                            application,
                            restart_mismatched=restart_mismatched,
                            progress=progress,
                            expected_serial=expected_serial,
                            expected_target_partition=expected_target_partition,
                            finalization_uncertain=finalization_uncertain,
                        )
                except OtaTransportInterrupted as exc:
                    if not exc.serial:
                        raise
                    expected_serial = expected_serial or exc.serial
                    expected_target_partition = (
                        exc.target_partition or expected_target_partition
                    )
                    finalization_uncertain = (
                        finalization_uncertain or exc.finalization_uncertain
                    )
                    if reconnect_deadline is None:
                        reconnect_deadline = time.monotonic() + request.verify_timeout_s
                    remaining = reconnect_deadline - time.monotonic()
                    if remaining <= 0:
                        raise CompatibilityError(
                            f"OTA 中断后未能重连 {expected_serial}"
                        ) from exc
                    notify(
                        "ota",
                        f"串口中断，正在重新定位 {expected_serial} 并继续",
                        70,
                    )
                    current_port = self.verifier.find_serial_port(
                        request.package.runtime_usb,
                        remaining,
                        expected_serial,
                        application.hardware_id,
                    )

            notify("verify", "OTA 传输完成，等待重启并读回目标 build_id", 80)
            runtime = self.verifier.verify_serial(
                request.package.runtime_usb,
                request.verify_timeout_s,
                ota_result.serial,
                application.hardware_id,
                application.build_id,
                ota_result.target_partition,
            )
            receipt = self._base_receipt(request, started, None, images)
            receipt.update(
                {
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "success": True,
                    "status": "completed",
                    "verification": "cdc-ota-runtime-wmp",
                    "update_route": "cdc-ota",
                    "target_partition": ota_result.target_partition,
                    "runtime": asdict(runtime),
                }
            )
            receipt_path = self.history.finish(operation_path, receipt)
            notify("done", f"OTA 与运行读回通过，凭证已保存：{receipt_path.name}", 100)
            return ProvisionResult(
                release_id=request.package.release_id,
                mode=request.mode,
                chip=None,
                runtime=runtime,
                receipt_path=receipt_path,
            )
        except Exception as exc:
            receipt = self._base_receipt(request, started, None, images)
            receipt.update(
                {
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "success": False,
                    "status": "failed",
                    "verification": "cdc-ota-runtime-wmp",
                    "update_route": "cdc-ota",
                    "target_partition": (
                        ota_result.target_partition if ota_result is not None else ""
                    ),
                    "runtime": {},
                    "error": str(exc),
                }
            )
            try:
                failure_path = (
                    self.history.finish(operation_path, receipt)
                    if operation_path is not None
                    else self.history.write(receipt)
                )
                notify("error", f"{exc}；失败凭证：{failure_path.name}", 100)
            except OSError:
                notify("error", str(exc), 100)
            raise

    @staticmethod
    def _base_receipt(request, started, chip, images) -> dict:
        return {
            "schema": 1,
            "timestamp": started,
            "record_kind": request.record_kind.value,
            "release_id": request.package.release_id,
            "git_tag": request.package.git_tag,
            "source_commit": request.package.source_commit,
            "target": request.package.target,
            "esp_idf": request.package.esp_idf,
            "mode": request.mode.value,
            "port": request.port,
            "operator": request.operator,
            "preserved_nvs": True,
            "erased_flash": False,
            "images": [
                {
                    "path": image.relative_path,
                    "offset": f"0x{image.offset:X}",
                    "sha256": image.sha256,
                }
                for image in images
            ],
            "chip": asdict(chip) if chip is not None else {},
        }
