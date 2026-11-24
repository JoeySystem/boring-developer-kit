"""Console OTA state machine, split out of MainViewModel (A-15 round 2).

The controller owns the FW_* conversation with the device: the
FirmwareUpdateTransaction state model, the 200 ms FW_STATUS poll, the 15 s
finalize / reconnect / verification deadline and rollback_pending checks.
It sends only its own FW_* commands and the status poll interval; everything
else leaves as signals for MainViewModel to apply.

Console OTA belongs to the firmware owner (design decision). This module moved the code without
changing FW_* semantics; the interface is for the firmware owner to review.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QObject, QTimer, Signal

from controller_config.firmware_update import (
    FirmwarePackage,
    FirmwarePackageError,
    FirmwareStatus,
    FirmwareUpdateState,
    FirmwareUpdateTransaction,
    firmware_abort_command,
    firmware_end_command,
    firmware_status_command,
)
from controller_config.models import DeviceSnapshot
from controller_config.protocol.bootstrap import COMMAND_DROPPED, BootstrapError, Command


FIRMWARE_COMMAND_PREFIX = "FW_"
FIRMWARE_STATUS_POLL_MS = 200
FIRMWARE_DEADLINE_MS = 15_000
OTA_STATUS_POLL_INTERVAL_MS = 60_000
NORMAL_STATUS_POLL_INTERVAL_MS = 500


class FirmwareUpdateController(QObject):
    # Every change of the transaction (same object rules as before: equal
    # transactions are not re-published).
    transaction_changed = Signal(object)
    # finish() ended the transaction in this state (COMPLETED, FAILED,
    # PACKAGE_READY); emitted after transaction_changed.
    finished = Signal(object)
    # The device did not come back by itself: rescan (USB only while busy).
    rescan_requested = Signal()

    def __init__(
        self,
        execute_command: Callable[[Command], None],
        set_status_poll_interval: Callable[[int], None],
        current_snapshot: Callable[[], DeviceSnapshot | None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._execute = execute_command
        self._set_status_poll_interval = set_status_poll_interval
        self._current_snapshot = current_snapshot
        self._transaction = FirmwareUpdateTransaction()
        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.setInterval(FIRMWARE_STATUS_POLL_MS)
        self.status_timer.timeout.connect(self.poll_status)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(FIRMWARE_DEADLINE_MS)
        self.deadline.timeout.connect(self.on_deadline)

    @property
    def transaction(self) -> FirmwareUpdateTransaction:
        return self._transaction

    def replace_silently(self, transaction: FirmwareUpdateTransaction) -> None:
        """Swap the model without publishing (tests and session resets)."""
        self._transaction = transaction

    def set_transaction(self, transaction: FirmwareUpdateTransaction) -> None:
        if transaction == self._transaction:
            return
        self._transaction = transaction
        self.transaction_changed.emit(transaction)

    # ---- user commands --------------------------------------------------

    def prepare(self, package: FirmwarePackage) -> None:
        self.set_transaction(
            FirmwareUpdateTransaction(
                state=FirmwareUpdateState.PACKAGE_READY,
                message="固件包已完成本地检查，尚未向设备发送数据",
                package=package,
            )
        )

    def start(self, snapshot: DeviceSnapshot) -> None:
        """Begin with FW_STATUS. MainViewModel checks the cross-session guards first."""
        transaction = self._transaction
        package = transaction.package
        chunk_size = package.validate_for_device(snapshot)
        serial = snapshot.identity.get("serial")
        if not isinstance(serial, str) or not serial:
            raise ValueError("设备没有返回可绑定的设备序列号")
        self._set_status_poll_interval(OTA_STATUS_POLL_INTERVAL_MS)
        self.set_transaction(
            replace(
                transaction,
                state=FirmwareUpdateState.CHECKING,
                message="正在读取设备固件维护状态",
                technical="",
                serial=serial,
                chunk_size=chunk_size,
                received_size=0,
                in_flight_size=0,
                target_partition="",
                abort_requested=False,
                restart_failed=True,
            )
        )
        self._execute(firmware_status_command())

    def abort(self, snapshot: DeviceSnapshot | None, *, link_usable: bool) -> None:
        transaction = self._transaction
        if not transaction.can_abort:
            raise ValueError("当前固件维护状态不能执行中止")
        if not link_usable:
            raise ValueError("请先重新连接设备，再中止固件接收事务")
        if snapshot is None or snapshot.identity.get("serial") != transaction.serial:
            raise ValueError("请连接本次固件维护的原设备，再中止固件接收事务")
        waiting_for_current_command = transaction.state in {
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
        }
        self.set_transaction(
            replace(
                transaction,
                state=FirmwareUpdateState.ABORTING,
                message=(
                    "等待当前请求结束，然后中止设备固件接收事务"
                    if waiting_for_current_command
                    else "正在让设备丢弃未完成的固件接收事务"
                ),
                abort_requested=True,
            )
        )
        if not waiting_for_current_command:
            self._execute(firmware_abort_command())

    def dismiss(self) -> None:
        if self._transaction.state not in {
            FirmwareUpdateState.NEEDS_ABORT,
            FirmwareUpdateState.PACKAGE_READY,
            FirmwareUpdateState.COMPLETED,
            FirmwareUpdateState.FAILED,
        }:
            raise ValueError("当前固件维护事务不能直接关闭")
        self._set_status_poll_interval(NORMAL_STATUS_POLL_INTERVAL_MS)
        self.set_transaction(FirmwareUpdateTransaction())

    def finish(
        self,
        state: FirmwareUpdateState,
        message: str,
        technical: str = "",
    ) -> None:
        self.status_timer.stop()
        self.deadline.stop()
        self._set_status_poll_interval(NORMAL_STATUS_POLL_INTERVAL_MS)
        self.set_transaction(
            replace(
                self._transaction,
                state=state,
                message=message,
                technical=technical,
                in_flight_size=0,
                abort_requested=False,
            )
        )
        self.finished.emit(state)

    def shutdown(self) -> None:
        self.status_timer.stop()
        self.deadline.stop()

    # ---- connection events ----------------------------------------------

    def pause(self, message: str, technical: str) -> None:
        self.status_timer.stop()
        state = self._transaction.state
        if state in {FirmwareUpdateState.FINALIZING, FirmwareUpdateState.WAITING_RECONNECT}:
            state = FirmwareUpdateState.WAITING_RECONNECT
        elif state is not FirmwareUpdateState.VERIFYING:
            state = FirmwareUpdateState.PAUSED
        if state is FirmwareUpdateState.PAUSED:
            self.deadline.stop()
        elif not self.deadline.isActive():
            self.deadline.start()
        self.set_transaction(
            replace(self._transaction, state=state, message=message, technical=technical, in_flight_size=0)
        )

    def on_disconnected(self, technical: str) -> None:
        if not self._transaction.is_busy:
            return
        if self._transaction.state in {
            FirmwareUpdateState.FINALIZING,
            FirmwareUpdateState.WAITING_RECONNECT,
            FirmwareUpdateState.VERIFYING,
        }:
            self.set_transaction(
                replace(
                    self._transaction,
                    state=(
                        FirmwareUpdateState.VERIFYING
                        if self._transaction.state is FirmwareUpdateState.VERIFYING
                        else FirmwareUpdateState.WAITING_RECONNECT
                    ),
                    message="设备正在重启；重新连接后将核对版本和 build_id",
                    technical=technical,
                )
            )
        else:
            self.pause(
                "传输期间连接中断；重连后将从设备报告的偏移继续，不会盲目重发",
                technical,
            )

    def reconcile_snapshot(self, snapshot: DeviceSnapshot) -> None:
        transaction = self._transaction
        package = transaction.package
        if package is None or transaction.state not in {
            FirmwareUpdateState.PAUSED,
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
            FirmwareUpdateState.WAITING_RECONNECT,
            FirmwareUpdateState.VERIFYING,
            FirmwareUpdateState.NEEDS_ABORT,
        }:
            return
        serial = snapshot.identity.get("serial")
        if serial != transaction.serial:
            self.finish(
                FirmwareUpdateState.FAILED,
                "重新连接的是另一台设备，已停止固件维护",
            )
            return
        try:
            chunk_size = package.validate_for_device(snapshot)
        except FirmwarePackageError as exc:
            self.finish(FirmwareUpdateState.FAILED, "重连设备与固件包不匹配", str(exc))
            return
        if transaction.state in {FirmwareUpdateState.WAITING_RECONNECT, FirmwareUpdateState.VERIFYING}:
            self.set_transaction(
                replace(
                    transaction,
                    state=FirmwareUpdateState.VERIFYING,
                    chunk_size=chunk_size,
                    message="设备已重连，正在核对运行版本、目标分区和回滚状态",
                )
            )
            if not self.deadline.isActive():
                self.deadline.start()
        else:
            self.set_transaction(
                replace(transaction, state=FirmwareUpdateState.CHECKING, chunk_size=chunk_size)
            )
        self._set_status_poll_interval(OTA_STATUS_POLL_INTERVAL_MS)
        self._execute(firmware_status_command())

    # ---- device responses (only FW_* commands this controller sent) -----

    def handle_completed(self, command_name: str, payload: dict) -> bool:
        if not command_name.startswith(FIRMWARE_COMMAND_PREFIX):
            return False
        self._on_command_completed(command_name, payload)
        return True

    def handle_failed(self, command_name: str, error: BootstrapError) -> bool:
        if not command_name.startswith(FIRMWARE_COMMAND_PREFIX):
            return False
        if error.error_name == COMMAND_DROPPED:
            # A-04: a command dropped by a closing link never ran; the
            # disconnect / failure path reconciles through FW_STATUS.
            return True
        self._on_command_failed(command_name, error)
        return True

    def _on_command_completed(self, command_name: str, payload: dict) -> None:
        transaction = self._transaction
        if not transaction.is_busy:
            return
        package = transaction.package
        if package is None:
            self.finish(
                FirmwareUpdateState.FAILED,
                "收到固件维护响应，但本地软件包上下文已经丢失",
            )
            return
        try:
            if command_name == "FW_STATUS":
                status = FirmwareStatus.from_payload(payload)
                if transaction.abort_requested:
                    if status.state == "IDLE":
                        self.finish(
                            FirmwareUpdateState.PACKAGE_READY,
                            "设备当前没有未完成的固件接收事务",
                        )
                    else:
                        self._execute(firmware_abort_command())
                    return
                self._handle_status(status)
                return
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                raise FirmwarePackageError(f"{command_name} 响应缺少 result object")
            if command_name == "FW_BEGIN":
                if result.get("state") != "RECEIVING":
                    raise FirmwarePackageError("FW_BEGIN 没有进入 RECEIVING")
                offset = result.get("offset")
                device_chunk = result.get("chunk_bytes")
                if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= package.size:
                    raise FirmwarePackageError("FW_BEGIN 返回的续传偏移无效")
                if not isinstance(device_chunk, int) or isinstance(device_chunk, bool) or device_chunk <= 0:
                    raise FirmwarePackageError("FW_BEGIN 返回的分片上限无效")
                target = result.get("target_partition")
                if target not in {"ota_0", "ota_1"}:
                    raise FirmwarePackageError("FW_BEGIN 返回的目标分区无效")
                updated = replace(
                    transaction,
                    state=(
                        FirmwareUpdateState.ABORTING
                        if transaction.abort_requested
                        else FirmwareUpdateState.TRANSFERRING
                    ),
                    message=(
                        "设备接收事务已建立，正在按用户请求中止"
                        if transaction.abort_requested
                        else "设备已准备接收固件"
                    ),
                    chunk_size=min(transaction.chunk_size, device_chunk),
                    received_size=offset,
                    target_partition=target,
                )
                self.set_transaction(updated)
                if transaction.abort_requested:
                    self._execute(firmware_abort_command())
                else:
                    self._send_next_chunk()
                return
            if command_name == "FW_DATA":
                received = result.get("received_size")
                expected = transaction.received_size + transaction.in_flight_size
                if received != expected or result.get("state") != "RECEIVING":
                    raise FirmwarePackageError("设备确认的固件分片偏移与本地传输进度不一致")
                updated = replace(
                    transaction,
                    state=(
                        FirmwareUpdateState.ABORTING
                        if transaction.abort_requested
                        else FirmwareUpdateState.TRANSFERRING
                    ),
                    message=(
                        "当前分片已确认，正在按用户请求中止"
                        if transaction.abort_requested
                        else f"正在传输固件：{received} / {package.size} 字节"
                    ),
                    received_size=received,
                    in_flight_size=0,
                )
                self.set_transaction(updated)
                if transaction.abort_requested:
                    self._execute(firmware_abort_command())
                else:
                    self._send_next_chunk()
                return
            if command_name == "FW_END":
                state = result.get("state")
                if result.get("version") != package.version or state not in {
                    "FINALIZING",
                    "READY_TO_REBOOT",
                }:
                    raise FirmwarePackageError("设备没有确认正在完成所选固件版本")
                if state == "READY_TO_REBOOT":
                    self._wait_for_reconnect()
                else:
                    self._start_finalization_wait()
                return
            if command_name == "FW_ABORT":
                if result.get("state") != "IDLE":
                    raise FirmwarePackageError("设备没有确认固件接收事务已中止")
                self.finish(
                    FirmwareUpdateState.PACKAGE_READY,
                    "设备中的未完成固件接收已清除；可重新开始所选软件包",
                )
        except FirmwarePackageError as exc:
            self.finish(FirmwareUpdateState.FAILED, "固件维护响应无效", str(exc))

    def _handle_status(self, status: FirmwareStatus) -> None:
        transaction = self._transaction
        package = transaction.package
        if package is None:
            return
        if transaction.state is FirmwareUpdateState.VERIFYING and status.state == "IDLE":
            # After reboot target_partition names the NEXT update partition.
            # Confirm this transaction against its saved destination instead.
            snapshot = self._current_snapshot()
            versions = snapshot.versions if snapshot is not None else {}
            if (
                versions.get("firmware") != package.version
                or (package.build_id and versions.get("build_id") != package.build_id)
                or status.running_version != package.version
                or transaction.target_partition not in {"ota_0", "ota_1"}
                or status.running_partition != transaction.target_partition
            ):
                self.finish(
                    FirmwareUpdateState.FAILED,
                    "重连读回的版本、build_id 或运行分区与本次更新不一致；更新未生效或已经回滚",
                )
            elif status.rollback_pending is False:
                self.finish(
                    FirmwareUpdateState.COMPLETED,
                    "设备已重新连接，版本、build_id 和目标分区读回一致，已确认无需回滚",
                )
            else:
                self._await_rollback_cleared(status)
            return
        target = transaction.target_partition or status.target_partition
        if transaction.target_partition and status.target_partition and transaction.target_partition != status.target_partition:
            self.finish(
                FirmwareUpdateState.FAILED,
                "设备在维护过程中改变了目标 OTA 分区",
            )
            return
        base = replace(transaction, target_partition=target, restart_failed=False)
        restart_failed = (
            status.state == "FAILED"
            and transaction.state is FirmwareUpdateState.CHECKING
            and transaction.restart_failed
            and status.reboot_pending is False
        )
        if status.state == "FAILED" and not restart_failed:
            self.finish(
                FirmwareUpdateState.FAILED,
                "设备报告固件维护失败；请检查原因后点击重新安装，再建立接收事务",
            )
            return
        if status.state == "IDLE" or restart_failed:
            self.set_transaction(
                replace(base, state=FirmwareUpdateState.BEGINNING, restart_failed=False,
                        message="正在建立固件接收事务")
            )
            self._execute(package.begin_command())
            return
        if not status.matches(package):
            if status.state == "RECEIVING":
                self.set_transaction(
                    replace(
                        base,
                        state=FirmwareUpdateState.NEEDS_ABORT,
                        message="设备中存在另一份未完成的固件接收；必须由用户明确中止后才能继续",
                    )
                )
            else:
                self.finish(
                    FirmwareUpdateState.FAILED,
                    "设备正在完成另一份固件，不能用当前软件包接管",
                )
            return
        if not 0 <= status.received_size <= package.size:
            self.finish(FirmwareUpdateState.FAILED, "设备返回的固件续传偏移无效")
            return
        if status.state == "RECEIVING":
            self.set_transaction(
                replace(
                    base,
                    state=FirmwareUpdateState.TRANSFERRING,
                    message=f"从设备确认的 {status.received_size} 字节位置继续传输",
                    received_size=status.received_size,
                    in_flight_size=0,
                )
            )
            self._send_next_chunk()
        elif status.state == "FINALIZING":
            self.set_transaction(base)
            self._start_finalization_wait()
        elif status.state == "READY_TO_REBOOT":
            self.set_transaction(base)
            self._wait_for_reconnect()

    def _await_rollback_cleared(self, status: FirmwareStatus) -> None:
        """New image runs with matching version, build_id and partition but
        rollback_pending is not False yet: keep polling FW_STATUS until the
        15 s deadline.

        J-02 / design decision seam (the firmware owner): this is where Console will send an
        explicit FW_CONFIRM once the USB session has passed HELLO and
        authentication and build_id matches. FW_CONFIRM is an FW_* command,
        so its ACK/NACK already routes to handle_completed / handle_failed.
        Not implemented in this split: behaviour is unchanged.
        """
        self.set_transaction(
            replace(self._transaction, message="新固件已运行，正在等待设备完成启动自检并取消回滚")
        )
        self.status_timer.start()

    def _send_next_chunk(self) -> None:
        transaction = self._transaction
        package = transaction.package
        if package is None:
            return
        if transaction.received_size >= package.size:
            self.set_transaction(
                replace(transaction, state=FirmwareUpdateState.FINALIZING, message="镜像传输完成，正在让设备校验并准备重启")
            )
            self._execute(firmware_end_command())
            return
        try:
            command, chunk_size = package.data_command(
                transaction.received_size, transaction.chunk_size
            )
        except FirmwarePackageError as exc:
            self.finish(FirmwareUpdateState.FAILED, "无法读取下一段固件镜像", str(exc))
            return
        self.set_transaction(
            replace(
                transaction,
                state=FirmwareUpdateState.TRANSFERRING,
                message=f"正在传输固件：{transaction.received_size} / {package.size} 字节",
                in_flight_size=chunk_size,
            )
        )
        self._execute(command)

    def _start_finalization_wait(self) -> None:
        self.set_transaction(
            replace(
                self._transaction,
                state=FirmwareUpdateState.FINALIZING,
                message="设备正在校验完整镜像并准备新的 OTA 分区",
            )
        )
        if not self.deadline.isActive():
            self.deadline.start()
        self.status_timer.start()

    def _wait_for_reconnect(self) -> None:
        self.status_timer.stop()
        self.set_transaction(
            replace(
                self._transaction,
                state=FirmwareUpdateState.WAITING_RECONNECT,
                message="设备已完成镜像校验，等待重启后核对版本和 build_id",
            )
        )
        self.deadline.start()

    def poll_status(self) -> None:
        if self._transaction.state in {
            FirmwareUpdateState.FINALIZING,
            FirmwareUpdateState.VERIFYING,
        }:
            self._execute(firmware_status_command())

    def on_deadline(self) -> None:
        if self._transaction.state is FirmwareUpdateState.FINALIZING:
            self.finish(
                FirmwareUpdateState.FAILED,
                "设备未在期限内完成固件镜像校验",
            )
        elif self._transaction.state is FirmwareUpdateState.WAITING_RECONNECT:
            self.set_transaction(
                replace(
                    self._transaction,
                    state=FirmwareUpdateState.VERIFYING,
                    message="尚未观察到设备自动重连，正在重新扫描并核对运行版本",
                )
            )
            self.deadline.start()
            self.rescan_requested.emit()
        elif self._transaction.state is FirmwareUpdateState.VERIFYING:
            self.finish(
                FirmwareUpdateState.FAILED,
                "未在期限内确认新固件的运行分区和回滚状态；更新结果尚未确认",
            )

    def _on_command_failed(self, command_name: str, error: BootstrapError) -> None:
        if command_name in {"FW_BEGIN", "FW_DATA"} and error.error_name == "TRANSPORT_TIMEOUT":
            self.set_transaction(
                replace(
                    self._transaction,
                    state=FirmwareUpdateState.CHECKING,
                    message="固件命令 ACK 未确认，正在通过 FW_STATUS 对账续传位置",
                    technical=error.technical,
                    in_flight_size=0,
                )
            )
            self._execute(firmware_status_command())
            return
        if command_name == "FW_END" and error.error_name == "TRANSPORT_TIMEOUT":
            self.set_transaction(
                replace(
                    self._transaction,
                    state=FirmwareUpdateState.VERIFYING,
                    message="FW_END 结果未知；将通过重连后的版本和 FW_STATUS 对账，不重复开始传输",
                    technical=error.technical,
                )
            )
            self.deadline.start()
            self.rescan_requested.emit()
            return
        self.finish(
            FirmwareUpdateState.FAILED,
            "设备拒绝固件维护命令",
            f"{command_name}: {error.technical}",
        )
