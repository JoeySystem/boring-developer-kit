"""固件更新页 (route ``firmware``): a settings wall with the Settings rail on its left.

Builds the page, refreshes the transfer progress in place and handles every
action on the page: online check, download then confirm, importing a
maintenance package, the bundled official recovery package, install
confirmation, abort, leaving maintenance and stopping the local wait.

Layout (the designer 甲, design decision): a 4 × 2 wall like the other Settings groups.
The tiles keep their size in every state: progress and failure change what a
tile shows instead of adding cards, technical details open in a dialog, and
nothing on the page scrolls at the 960 × 640 minimum window.

    当前固件 2 × 2        | 版本说明 2 × 1  (仅固件维护 while in maintenance)
    version, main action, | 手动安装 2 × 1: file / recovery / 高级选项,
    progress or failure   |   or the running update and its abort while busy
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from importlib.resources import as_file, files
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from controller_config.views.ui import notice
from controller_config.build_identity import BuildIdentity
from controller_config.firmware_release import RemoteFirmwareCheck, RemoteFirmwareState
from controller_config.firmware_update import FirmwareUpdateState, FirmwareUpdateTransaction
from controller_config.i18n import LanguageManager, set_translatable_text, translate_ui_text
from controller_config.models import AppState, DeviceSnapshot, ScreenModel
from controller_config.style import tokens
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.pages.common import (
    planned_tool_capability,
    planned_tool_capability_label,
    planned_tool_facts,
)
from controller_config.views.settings_wall import (
    CardDialog,
    SettingsWall,
    TileIcon,
    WallTile,
    pill_button,
    show_card_dialog,
    title_label,
    wall_scroll,
)
from controller_config.views.ui import dialog as dialog_shell
from controller_config.views.ui.clamped_label import ClampedLabel
from controller_config.views.ui.firmware_steps import FirmwareSteps, step_of, step_states
from controller_config.views.ui.progress_ring import ProgressRingRow
from controller_config.views.ui.status_icon import StatusIcon

# Wall buttons keep the old buttonRole (read by tests and the global sheet) and
# take the wall's pill look.
_WALL_ROLE = {"primary": "primary", "secondary": "plain"}


def _role(button: QPushButton, role: str) -> QPushButton:
    button.setProperty("buttonRole", role)
    button.setProperty("wallRole", _WALL_ROLE[role])
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _note(text: str = "", object_name: str = "", *, translate: bool = True, lines: int | None = 1,
          clamp: bool = False) -> QLabel:
    """A small muted line in a tile (styled by the wall sheet's wallNote rule)."""
    label = ClampedLabel(lines=lines, object_name=object_name) if clamp else QLabel(objectName=object_name)
    label.setProperty("wallNote", True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if text:
        if translate:
            set_translatable_text(label, text)
        else:
            label.setText(text)
    return label


def _panel_header(icon: str, title: str, control: QWidget | None = None) -> QHBoxLayout:
    """The top row of a panel inside a tile, the same as WallTile.header."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    row.addWidget(TileIcon(icon), 0, Qt.AlignmentFlag.AlignVCenter)
    name = title_label(title)
    row.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    if control is not None:
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class _ButtonRow(QWidget):
    """Buttons side by side, or one under another when the translation is too long for that.

    Only the direction changes while the window is dragged; nothing is restyled or rebuilt.
    """

    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons = buttons
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for button in buttons:
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)

    def _arrange(self) -> None:
        shown = [button for button in self._buttons if not button.isHidden()]
        wide = sum(button.sizeHint().width() for button in shown) + 8 * max(0, len(shown) - 1)
        direction = (QBoxLayout.Direction.TopToBottom if wide > self.width()
                     else QBoxLayout.Direction.LeftToRight)
        if self.layout().direction() != direction:
            self.layout().setDirection(direction)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self._arrange()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt virtual method
        super().showEvent(event)
        self._arrange()


def _panel(object_name: str) -> tuple[QFrame, QVBoxLayout]:
    panel = QFrame(objectName=object_name)
    box = QVBoxLayout(panel)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(6)
    return panel, box


class FirmwarePage:
    """The firmware update page and everything its buttons start.

    update() only moves the progress ring and status text; when what the page
    shows changes, the main window either rebuilds the wall inside the live
    scroll area (rebuild_in_place, keeps the area under the styled window)
    or installs a fresh build().
    """

    route = "firmware"

    def __init__(
        self,
        view_model: MainViewModel,
        language_manager: LanguageManager,
        *,
        window: QWidget,
        build_identity: BuildIdentity,
        reveal_update_notice: Callable[[], None],
        quick_viewport: Callable[[], QWidget | None],
        factory_reset_available: Callable[[DeviceSnapshot | None], bool],
        settings_rail: Callable[[str], QWidget],
        wall_workspace: Callable[[str], QWidget],
    ) -> None:
        self._view_model = view_model
        self._language_manager = language_manager
        # Parent of the dialogs and context of the deferred calls.
        self._window = window
        self._build_identity = build_identity
        self._reveal_update_notice = reveal_update_notice
        self._quick_viewport = quick_viewport
        self._factory_reset_available = factory_reset_available
        self._settings_rail = settings_rail
        self._wall_workspace = wall_workspace
        self._inputs: tuple | None = None
        self._scroll: QScrollArea | None = None
        self._message: QLabel | None = None
        self._message_shows_transaction = False
        self._progress: ProgressRingRow | None = None
        self._progress_shows_transaction = False
        self._progress_steps: FirmwareSteps | None = None   # 传输 → 写入 → 重启 → 读回 (-15 第 8 处)
        self._firmware_step: int | None = None               # the last step seen, for a failure
        # 高级固件选项 opens only when the user asks (design decision); kept across rebuilds.
        self._package_options_open = False
        self._firmware_reached = 0                            # the last progress seen, for a failure
        self._install_after_remote_download = False
        self._remote_install_confirmation_scheduled = False
        self.file_dialog: QFileDialog | None = None
        self._file_dialog_pending = False

    def update(self, model: ScreenModel) -> bool:
        inputs = self._render_inputs(model)
        if self._live() and inputs == self._inputs:
            self._refresh_progress()
            return True
        self._inputs = inputs
        return False

    def build(self, model: ScreenModel) -> QWidget:
        workspace = self._wall_workspace("settingsWorkspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._render(model), 1)
        return workspace

    def rebuild_in_place(self, model: ScreenModel) -> bool:
        """Put a new wall into the live scroll area; False when there is none.

        Keeps the scroll area under the styled window when a phase changes:
        taking it out drops inherited styles for a frame.
        """
        if not self._live():
            return False
        self._render(model, scroll=self._scroll)
        return True

    def _live(self) -> bool:
        return self._scroll is not None and isValid(self._scroll)

    def _render_inputs(self, model: ScreenModel) -> tuple:
        transaction = self._view_model.firmware_update
        remote_render_input = (
            None
            if transaction.is_busy or transaction.state is FirmwareUpdateState.COMPLETED
            else replace(
                self._view_model.remote_firmware,
                message="",
                received_size=0,
                total_size=0,
            )
        )
        return (
            model.state,
            model.snapshot.identity if model.snapshot else None,
            model.snapshot.versions if model.snapshot else None,
            model.snapshot.trust if model.snapshot else None,
            planned_tool_capability("firmware", model.snapshot),
            planned_tool_facts("firmware", model.snapshot),
            replace(transaction, message="", received_size=0, in_flight_size=0),
            remote_render_input,
            self._view_model.calibration.blocks_editing,
            self._view_model.write_transaction.blocks_editing,
            self._factory_reset_available(model.snapshot),
            model.snapshot.connection_kind if model.snapshot is not None else "",
            self._language_manager.language,
        )

    def _refresh_progress(self) -> None:
        transaction = self._view_model.firmware_update
        set_translatable_text(
            self._message,
            transaction.message if self._message_shows_transaction
            else self._view_model.firmware_online_message,
        )
        if self._progress is None:
            return
        if self._progress_shows_transaction:
            _configure_device_firmware_progress(self._progress, transaction)
            self._show_firmware_steps(transaction)
        else:
            _configure_remote_firmware_progress(self._progress, self._view_model.remote_firmware)

    def _reached(self, transaction) -> int:
        """How far the update got: kept while it runs, so a failure that reports 0
        still shows where it stopped (the designer 11-20 真屏验收 第 13 条)."""
        if transaction.state in {FirmwareUpdateState.IDLE, FirmwareUpdateState.PACKAGE_READY}:
            self._firmware_reached = 0
        elif transaction.progress_percent > 0:
            self._firmware_reached = transaction.progress_percent
        return self._firmware_reached

    def _show_firmware_steps(self, transaction) -> None:
        self._reached(transaction)
        step = step_of(transaction.state, self._firmware_step)
        if transaction.state in {FirmwareUpdateState.IDLE, FirmwareUpdateState.PACKAGE_READY}:
            step = None   # a new update starts from the first step again
        self._firmware_step = step
        if self._progress_steps is not None:
            self._progress_steps.set_states(step_states(transaction.state, step))

    def _details_link(self, owner: QWidget, name: str, text: str) -> QPushButton:
        """“更多信息”: the technical text opens in a dialog instead of growing the tile.

        The label is built with the page (hidden, so tests and readers find it) and
        moves into the dialog while it is open; closing the dialog puts it back.
        """
        toggle = pill_button("更多信息", f"{name}DetailsToggle", role="link")
        toggle.setCheckable(True)
        holder = QWidget(owner)
        holder.hide()
        label = QLabel(text, holder, objectName=f"{name}Technical")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.hide()
        opened: list[CardDialog] = []

        def closed(_result: int) -> None:
            dialog = opened.pop() if opened else None
            if isValid(holder) and isValid(label):
                label.hide()
                label.setParent(holder)
            if isValid(toggle):
                toggle.blockSignals(True)
                toggle.setChecked(False)
                toggle.blockSignals(False)
            if dialog is not None and isValid(dialog):
                dialog.deleteLater()

        def toggled(checked: bool) -> None:
            if not checked:
                if opened and isValid(opened[-1]):
                    opened[-1].reject()
                return
            body = QFrame()
            box = QVBoxLayout(body)
            box.setContentsMargins(0, 0, 0, 0)
            label.setParent(body)
            box.addWidget(label)
            label.show()
            dialog = CardDialog(self._window, "更多信息", body, width=dialog_shell.MEDIUM)
            opened.append(dialog)
            dialog.finished.connect(closed)
            dialog.open()

        toggle.toggled.connect(toggled)
        return toggle

    def _render(self, model: ScreenModel, *, scroll: QScrollArea | None = None) -> QWidget:
        snapshot = model.snapshot
        capability = planned_tool_capability("firmware", snapshot)
        transaction = self._view_model.firmware_update
        package = transaction.package
        remote = self._view_model.remote_firmware
        maintenance = model.state is AppState.FIRMWARE_MAINTENANCE

        # -- header: the group name, and the support badge when it matters ---------
        header = QWidget(objectName="firmwareWallHeader")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(4, 0, 0, 0)
        header_row.setSpacing(12)
        heading = QLabel(objectName="settingsWallTitle")
        set_translatable_text(heading, "固件更新")
        header_row.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
        badge = QLabel(
            planned_tool_capability_label("firmware", capability),
            objectName="statusReady" if capability is True else "statusWarn",
        )
        badge.setProperty("capabilityState", capability)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setVisible(capability is not True)
        header_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        wall = SettingsWall("updates", header=header)
        wall.setObjectName("firmwareMaintenancePage")

        # -- 当前固件 (2 × 2): version, the one main action, progress or failure ----
        transaction_progress_states = {
            FirmwareUpdateState.CHECKING,
            FirmwareUpdateState.BEGINNING,
            FirmwareUpdateState.TRANSFERRING,
            FirmwareUpdateState.FINALIZING,
            FirmwareUpdateState.WAITING_RECONNECT,
            FirmwareUpdateState.VERIFYING,
            FirmwareUpdateState.ABORTING,
            FirmwareUpdateState.PAUSED,
            FirmwareUpdateState.COMPLETED,
        }
        show_transaction_progress = transaction.state in transaction_progress_states
        show_remote_progress = (
            not show_transaction_progress
            and remote.state in {
                RemoteFirmwareState.CHECKING,
                RemoteFirmwareState.DOWNLOADING,
            }
        )
        package_ready = package is not None and transaction.state in {
            FirmwareUpdateState.PACKAGE_READY, FirmwareUpdateState.FAILED,
        }
        online_package_ready = (
            remote.state is RemoteFirmwareState.DOWNLOADED
            and package is not None
            and package.source_kind == "official"
            and transaction.state is FirmwareUpdateState.PACKAGE_READY
        )
        device_details = "\n".join(planned_tool_facts("firmware", snapshot)).strip()
        remote_details = remote.technical
        if remote.release:
            release = remote.release
            remote_details += (
                f"\nversion: {release.version}\nbuild_id: {release.build_id}"
                f"\nsize: {release.size} bytes\nchannel: {release.manifest.get('channel', 'stable')}"
                f"\nminimum_app_version: {release.manifest.get('minimum_app_version', '0.1.0')}"
                f"\npublished_at: {release.manifest.get('published_at', '—')}"
            )
        combined_details = "\n\n".join(
            details for details in (device_details, remote_details.strip()) if details
        )

        focus = WallTile(2, 2)
        focus.setObjectName("firmwareUpdateFocusCard")
        focus.header(
            TileIcon("chip"), title="当前固件",
            control=self._details_link(focus, "remoteFirmware", combined_details) if combined_details else None,
        )
        focus.box.addSpacing(6)
        current = QLabel(
            snapshot.versions.get("firmware", "—") if snapshot else "—",
            objectName="firmwareCurrentVersion",
        )
        current.setStyleSheet("font-size: 28px; font-weight: 600;")
        current.setTextFormat(Qt.TextFormat.PlainText)
        current.setWordWrap(True)
        focus.box.addWidget(current)
        if snapshot and snapshot.versions.get("build_id"):
            focus.box.addWidget(_note(str(snapshot.versions["build_id"]).split("-g", 1)[0],
                                      "firmwareCurrentBuild", translate=False))
        if snapshot:
            origin_text = {
                "official": "官方固件",
                "custom": "正在使用自定义固件",
                "unknown": "固件来源未确认",
            }[self._view_model.firmware_origin]
            focus.box.addWidget(_note(origin_text, "firmwareOrigin"))
        if package_ready and remote.state is not RemoteFirmwareState.DOWNLOADED:
            target = _note(translate_ui_text("已准备：{version}").format(
                version=_firmware_version_display(package.version, package.build_id),
            ), "firmwareTargetVersion", translate=False)
            focus.box.addWidget(target)
        release_notes: QLabel | None = None
        if remote.release is not None:
            release = remote.release
            release_label = "新版本：{version}" if remote.state is RemoteFirmwareState.AVAILABLE else "官方版本：{version}"
            release_details = QLabel(objectName="remoteFirmwareReleaseSummary")
            set_translatable_text(release_details, release_label.format(
                version=_firmware_version_display(release.version, release.build_id),
            ))
            release_details.setWordWrap(True)
            release_details.setTextFormat(Qt.TextFormat.PlainText)
            release_details.setStyleSheet(
                f"color: {tokens.ACCENT}; font-weight: 600;"
                if remote.state is RemoteFirmwareState.AVAILABLE else f"color: {tokens.TEXT_SECONDARY};"
            )
            focus.box.addWidget(release_details)
            release_notes = ClampedLabel(
                str(release.manifest.get("release_notes", translate_ui_text("未提供"))),
                object_name="remoteFirmwareReleaseNotes",
            )
        focus.spacer()

        # 进度条点阵样式 定稿 v1: a medium ring on the left, the words on the right.
        steps = None
        action_progress = None
        if show_transaction_progress:
            action_progress = ProgressRingRow(48, object_name="firmwareProgress")
            _configure_device_firmware_progress(action_progress, transaction)
            focus.box.addWidget(action_progress)
            # -15 第 8 处 (乙): the four steps in a row under the ring.
            steps = FirmwareSteps()
            focus.box.addWidget(steps)
        elif show_remote_progress:
            action_progress = ProgressRingRow(48, object_name="remoteFirmwareProgress")
            _configure_remote_firmware_progress(action_progress, remote)
            focus.box.addWidget(action_progress)

        # Internal development packages (design decision): the bundled official
        # firmware lacks the desktop source under test, so these builds offer
        # no way back to it ("查看官方版本 → 切换到官方固件…" and "恢复官方固件…").
        # Firmware changes go through the firmware owner's flashing steps instead. Official
        # and DIY builds are unchanged; the update flow itself is untouched.
        dev_hides_official_switch = self._build_identity.is_dev_build
        retryable_download = (
            remote.state is RemoteFirmwareState.FAILED
            and remote.release is not None
        )
        # 固件更新没做成 (design ticket 第 7 件, 进度条点阵样式 定稿 v1 "没做成"): the ring
        # stays where it stopped, every lit dot orange (the designer 11-20 真屏验收 第 13 条), an orange triangle in
        # the middle; the retry button sits below. Looks only: when a run counts as failed
        # and what retrying does are unchanged. It takes the place of the running ring.
        failed_row = None
        if transaction.state is FirmwareUpdateState.FAILED:
            failed_progress = ProgressRingRow(48, object_name="firmwareFailedProgress")
            failed_progress.ring.set_value(self._reached(transaction))
            failed_progress.ring.set_state("failed")
            failed_progress.set_text(translate_ui_text("固件更新没做成"))
            failed_row = failed_progress
        elif retryable_download and not show_transaction_progress:
            failed_progress = ProgressRingRow(48, object_name="remoteFirmwareFailedProgress")
            failed_progress.ring.set_value(remote.progress_percent)
            failed_progress.ring.set_state("failed")
            failed_progress.set_text(translate_ui_text("固件更新没做成"))
            failed_row = failed_progress
        if failed_row is not None:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(failed_row, 1)
            if transaction.state is FirmwareUpdateState.FAILED and transaction.technical:
                row.addWidget(
                    self._details_link(focus, "firmwareTransaction", transaction.technical),
                    0, Qt.AlignmentFlag.AlignVCenter,
                )
            focus.box.addLayout(row)

        online_buttons = QHBoxLayout()
        online_buttons.setSpacing(8)
        if not (show_transaction_progress or show_remote_progress) and not (
            dev_hides_official_switch and self._view_model.firmware_origin != "official"
        ) and (remote.state in {
            RemoteFirmwareState.IDLE,
            RemoteFirmwareState.CURRENT,
            RemoteFirmwareState.UNPUBLISHED,
        } or (remote.state is RemoteFirmwareState.FAILED and not retryable_download)):
            check_online = QPushButton(
                "重新检查"
                if remote.state in {RemoteFirmwareState.UNPUBLISHED, RemoteFirmwareState.FAILED}
                else ("查看官方版本" if self._view_model.firmware_origin != "official" else "检查在线更新"),
            )
            check_online.setObjectName("checkRemoteFirmware")
            _role(check_online, "secondary" if self._view_model.firmware_origin != "official"
                  or remote.state is RemoteFirmwareState.CURRENT else "primary")
            check_online.setEnabled(
                snapshot is not None
                and capability is True
                and self._view_model.firmware_connection_ready
                and not transaction.is_busy
                and (model.state is AppState.FIRMWARE_MAINTENANCE or not self._view_model.calibration.blocks_editing)
                and (model.state is AppState.FIRMWARE_MAINTENANCE or not self._view_model.write_transaction.blocks_editing)
            )
            check_online.clicked.connect(self._check_remote)
            online_buttons.addWidget(check_online)
        if not (show_transaction_progress or show_remote_progress) and not (
            dev_hides_official_switch and remote.restoration
        ) and remote.release is not None and remote.state in {
            RemoteFirmwareState.AVAILABLE,
            RemoteFirmwareState.RESTORE_AVAILABLE,
            RemoteFirmwareState.FAILED,
        }:
            install_online = QPushButton(
                ("重新尝试切换…" if remote.restoration else "重新尝试安装…")
                if remote.state is RemoteFirmwareState.FAILED
                else self._remote_install_label(),
            )
            install_online.setObjectName("installRemoteFirmware")
            _role(install_online, "secondary" if remote.restoration else "primary")
            install_online.setEnabled(
                snapshot is not None
                and capability is True
                and self._view_model.firmware_connection_ready
                and not transaction.is_busy
            )
            install_online.clicked.connect(self._prepare_remote_install)
            online_buttons.addWidget(install_online)
        if package_ready and not (
            dev_hides_official_switch
            and remote.restoration
            and remote.state is RemoteFirmwareState.DOWNLOADED
        ):
            install_ready = QPushButton(
                "请连接 USB 后继续安装"
                if snapshot is not None and snapshot.connection_kind == "bluetooth"
                else "重新尝试安装…" if transaction.state is FirmwareUpdateState.FAILED
                else self._remote_install_label()
                if remote.state is RemoteFirmwareState.DOWNLOADED
                else "安装导入的自定义固件" if package.source_kind == "custom"
                else "安装导入的官方固件",
            )
            install_ready.setObjectName("startFirmwareUpdate")
            _role(install_ready, "secondary" if remote.restoration else "primary")
            install_ready.setEnabled(
                snapshot is not None
                and snapshot.connection_kind != "bluetooth"
                and capability is True
                and self._view_model.firmware_connection_ready
                and remote.state not in {RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING}
            )
            install_ready.clicked.connect(self.confirm_update)
            online_buttons.addWidget(install_ready)
        online_buttons.addStretch(1)
        focus.box.addLayout(online_buttons)

        online_message = ClampedLabel(
            transaction.message if show_transaction_progress or package_ready
            else self._view_model.firmware_online_message,
            lines=2,
            object_name="firmwareMessage" if show_transaction_progress else "remoteFirmwareMessage",
        )
        online_message.setStyleSheet(
            "color: #dc9b86;" if remote.state is RemoteFirmwareState.FAILED else f"color: {tokens.TEXT_SECONDARY};"
        )
        focus.box.addWidget(online_message)
        if snapshot is not None and snapshot.connection_kind == "bluetooth":
            focus.box.addWidget(_note("安装需要连接 USB", "firmwareUsbHint"))
        wall.place(focus, 0, 0)

        # -- 版本说明 (2 × 1); in maintenance it starts with why only firmware works ---
        notes = WallTile(2, 1)
        notes.setObjectName("firmwareReleaseNotesCard")
        more_notes = pill_button("全部说明…", "firmwareReleaseNotesMore", role="link")
        size_policy = more_notes.sizePolicy()
        size_policy.setRetainSizeWhenHidden(True)
        more_notes.setSizePolicy(size_policy)
        more_notes.hide()
        if maintenance:
            # 顶条只留认证状态；“仅固件维护”放在这一页顶上（the firmware owner 11-19，design ticket 选 B）
            notes.setObjectName("firmwareMaintenanceBanner")
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(9)
            # Orange rounded triangle = "needs you" (status graphics v1; design ticket 答复第 5 件).
            title_row.addWidget(StatusIcon("error"), 0, Qt.AlignmentFlag.AlignVCenter)
            maintenance_title = QLabel(objectName="firmwareMaintenanceTitle")
            maintenance_title.setProperty("wallTitle", True)
            set_translatable_text(maintenance_title, "仅固件维护")
            title_row.addWidget(maintenance_title, 0, Qt.AlignmentFlag.AlignVCenter)
            title_row.addStretch(1)
            title_row.addWidget(self._details_link(notes, "firmwareMaintenance", snapshot.maintenance_reason),
                                0, Qt.AlignmentFlag.AlignVCenter)
            notes.box.addLayout(title_row)
            notes.box.addWidget(_note(
                "设备已认证，当前仅可维护固件。配置无法读取，不影响安装官方签名固件；配置编辑与电脑联动已暂停。",
                "firmwareMaintenanceNotice", lines=None, clamp=True,
            ), 1)
            if release_notes is not None:
                notes_row = QHBoxLayout()
                notes_row.addStretch(1)
                notes_row.addWidget(more_notes)
                notes.box.addLayout(notes_row)
                release_notes.setProperty("wallNote", True)
                release_notes.hide()  # in the dialog only: the tile is about maintenance now
                more_notes.setVisible(True)
        else:
            notes.header(
                TileIcon("help"),
                title="更新内容" if remote.state is RemoteFirmwareState.AVAILABLE else "官方版本说明",
                control=more_notes,
            )
            notes.box.addSpacing(4)
            if release_notes is not None:
                release_notes.setProperty("wallNote", True)
                notes.box.addWidget(release_notes, 1)
                release_notes.clamped_changed.connect(more_notes.setVisible)
            else:
                notes.box.addWidget(_note(
                    "当前渠道没有可展示的官方版本说明。"
                    if remote.state is RemoteFirmwareState.UNPUBLISHED
                    else "查看官方版本后，这里会显示该版本的说明。",
                    "firmwareReleaseNotesEmpty", lines=None, clamp=True,
                ), 1)
        if release_notes is not None:
            full_notes = release_notes.text()
            more_notes.clicked.connect(lambda: self._show_release_notes(full_notes))
            if maintenance:
                notes.box.addWidget(release_notes)
        wall.place(notes, 0, 2)

        # -- 手动安装 (2 × 1): file / recovery / 高级选项, or the running update ----------
        install_tile = WallTile(2, 1)
        install_tile.setObjectName("firmwareInstallTile")
        busy_panel = (
            not online_package_ready
            and transaction.state is not FirmwareUpdateState.FAILED
            and (
                transaction.state is FirmwareUpdateState.NEEDS_ABORT
                or self._view_model.can_stop_firmware_wait
                or transaction.can_abort
                or transaction.is_busy
            )
        )
        # 更新时藏起“从文件安装”“恢复官方固件”(design decision); downloading counts as updating.
        hide_manual = busy_panel or show_remote_progress

        file_panel, file_box = _panel("firmwareFilePanel")
        file_box.addLayout(_panel_header("reset", "手动安装"))
        choose = QPushButton("从文件安装官方固件…", objectName="selectFirmwarePackage")
        _role(choose, "secondary")
        choose.setEnabled(
            not transaction.is_busy
            and remote.state not in {RemoteFirmwareState.CHECKING, RemoteFirmwareState.DOWNLOADING}
        )
        choose.clicked.connect(self._select_package)
        manual_buttons = [choose]
        manual_hint: QLabel | None = None
        power_v2 = snapshot is not None and snapshot.identity.get("hardware_id") == "WMP-S3-MATRIX12-POWER-V2"
        if dev_hides_official_switch and snapshot is not None and (
            power_v2 or self._view_model.firmware_origin != "official"
        ):
            manual_hint = _note(
                "开发版不提供切回官方固件。换固件请按开发说明里的刷写步骤。",
                "devBuildFirmwareSwitchNotice", lines=None, clamp=True,
            )
        elif power_v2:
            recovery = QPushButton("恢复官方固件…", objectName="prepareOfficialRecovery")
            _role(recovery, "secondary")
            recovery.setEnabled(
                self._view_model.firmware_connection_ready and capability is True
                and not transaction.blocks_editing
                and not show_remote_progress
                and (model.state is AppState.FIRMWARE_MAINTENANCE or not self._view_model.calibration.blocks_editing)
                and (model.state is AppState.FIRMWARE_MAINTENANCE or not self._view_model.write_transaction.blocks_editing)
            )
            recovery.clicked.connect(self.prepare_bundled_recovery)
            manual_buttons.append(recovery)
            manual_hint = _note(
                "内置官方恢复包 · 20261007.01 · 无需下载。先查看版本并验证签名，确认后才会安装。",
                "officialRecoveryHint", lines=None, clamp=True,
            )
        file_box.addSpacing(4)
        file_box.addWidget(_ButtonRow(manual_buttons))
        if manual_hint is not None:
            file_box.addWidget(manual_hint, 1)
        else:
            file_box.addStretch(1)
        if hide_manual:
            for widget in (choose, file_panel.findChild(QPushButton, "prepareOfficialRecovery"), manual_hint):
                if widget is not None:
                    widget.hide()

        package_card, package_box = _panel("firmwarePackageCard")
        package_box.addLayout(_panel_header(
            "chip", "高级固件选项",
            self._details_link(install_tile, "firmwarePackageDetails", (
                f"version: {package.version}\nbuild_id: {package.build_id}"
                f"\nhardware_id: {package.hardware_id}\nimage: {package.image_path.name}"
                f"\nsize: {package.size} bytes\nvalidation_state: {package.validation_state}"
                f"\ngit_dirty: {package.git_dirty}"
            )) if package is not None else None,
        ))
        if package is not None:
            package_box.addWidget(_note(translate_ui_text("已准备：{version}").format(
                version=_firmware_version_display(package.version, package.build_id)
            ), "firmwarePackageSummary", translate=False))
            source_text = {"official": "官方固件 · 发布签名已验证", "custom": "自定义固件 · 未经官方验证"}.get(
                package.source_kind, "固件来源尚未确认")
            package_box.addWidget(_note(source_text, "firmwarePackageSource"))
        custom = QPushButton("选择自定义固件文件…", objectName="selectCustomFirmwarePackage")
        _role(custom, "secondary")
        custom.setEnabled(choose.isEnabled() and model.state is not AppState.FIRMWARE_MAINTENANCE)
        custom.clicked.connect(lambda: self._select_package(custom=True))
        package_box.addWidget(custom, 0, Qt.AlignmentFlag.AlignLeft)
        package_box.addStretch(1)

        transaction_card, transaction_box = _panel("firmwareTransactionCard")
        transaction_box.addLayout(_panel_header(
            "update", _firmware_transaction_title(transaction.state),
            self._details_link(install_tile, "firmwareTransactionDetails", transaction.technical)
            if transaction.technical and transaction.state is not FirmwareUpdateState.FAILED else None,
        ))
        connection_notice = _note("更新完成前，请保持 USB 连接。设备会自动重启。",
                                  "firmwareConnectionNotice", lines=None, clamp=True)
        connection_notice.setVisible(transaction.is_busy)
        transaction_box.addWidget(connection_notice, 1)
        if not transaction.is_busy:
            transaction_box.addStretch(1)
        buttons: list[QPushButton] = []
        if transaction.state is FirmwareUpdateState.NEEDS_ABORT:
            abort = _role(QPushButton("中止设备中的旧更新", objectName="primary"), "primary")
            abort.clicked.connect(self._confirm_abort)
            buttons.append(abort)
            dismiss = _role(QPushButton("保留设备旧事务并退出维护", objectName="secondary"), "secondary")
            dismiss.clicked.connect(self._dismiss)
            buttons.append(dismiss)
        elif self._view_model.can_stop_firmware_wait:
            stop = _role(QPushButton("停止本地等待", objectName="stopFirmwareWait"), "secondary")
            stop.clicked.connect(self.confirm_stop_wait)
            buttons.append(stop)
        elif transaction.can_abort:
            abort = _role(QPushButton("中止本次固件接收", objectName="secondary"), "secondary")
            abort.setEnabled(model.state in {AppState.READY, AppState.READ_ONLY, AppState.FIRMWARE_MAINTENANCE})
            abort.clicked.connect(self._confirm_abort)
            buttons.append(abort)
        transaction_box.addWidget(_ButtonRow(buttons))

        package_toggle = pill_button("", "firmwarePackageToggle", role="link")
        package_toggle.setCheckable(True)
        package_toggle.setChecked(self._package_options_open)

        def show_panels(expanded: bool) -> None:
            self._package_options_open = expanded
            package_toggle.setText(
                ("▾ " if expanded else "▸ ") + translate_ui_text("高级选项 · 自定义固件与详情")
            )
            file_panel.setVisible(not busy_panel and not expanded)
            package_card.setVisible(not busy_panel and expanded)

        show_panels(self._package_options_open)
        package_toggle.toggled.connect(show_panels)
        transaction_card.setVisible(busy_panel)
        package_toggle.setVisible(not busy_panel)
        for panel in (file_panel, package_card, transaction_card):
            install_tile.box.addWidget(panel, 1)
        install_tile.box.addWidget(package_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        wall.place(install_tile, 1, 2)

        rail = self._settings_rail("firmware")
        if scroll is None:
            scroll = wall_scroll(wall, rail)
            scroll.setObjectName("firmwareMaintenanceScroll")
        else:
            wall_scroll(wall, rail, scroll=scroll)  # the main window retranslates the page
        self._scroll = scroll
        self._message = online_message
        self._message_shows_transaction = show_transaction_progress
        self._progress = action_progress
        self._progress_shows_transaction = show_transaction_progress
        self._progress_steps = steps
        self._show_firmware_steps(transaction)
        return scroll

    def _show_release_notes(self, text: str) -> None:
        body = QFrame()
        box = QVBoxLayout(body)
        box.setContentsMargins(0, 0, 0, 0)
        label = QLabel(text, objectName="firmwareReleaseNotesFull")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(label)
        show_card_dialog(self._window, "官方版本说明", body, width=dialog_shell.MEDIUM)

    def confirm_stop_wait(self) -> bool:
        if notice.ask(
            self._window, "停止本地等待",
            "设备当前离线。停止本地等待不会取消设备端更新，也不代表升级成功。"
            "重新打开后，请连接原设备并重新选择固件包，先读取状态再决定续传或中止。是否停止等待？",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
            danger=True, labels={QMessageBox.Yes: "停止等待"},
        ) != QMessageBox.Yes:
            return False
        try:
            self._view_model.stop_firmware_wait()
        except ValueError as exc:
            notice.alert(self._window, "设备状态已改变", str(exc))
            return False
        return True

    def _check_remote(self) -> None:
        self._reveal_update_notice()
        try:
            self._view_model.check_remote_firmware()
        except ValueError as exc:
            notice.notify(self._window, "无法检查在线固件", str(exc))

    def _remote_install_label(self) -> str:
        remote = self._view_model.remote_firmware
        if remote.restoration:
            return "切换到官方固件…"
        return "安装最新版固件…"

    def _prepare_remote_install(self) -> None:
        self._install_after_remote_download = True
        try:
            self._view_model.download_remote_firmware()
        except ValueError as exc:
            self._install_after_remote_download = False
            notice.alert(self._window, "无法准备固件更新", str(exc))

    def maybe_schedule_remote_install_confirmation(self, model: ScreenModel) -> None:
        remote = self._view_model.remote_firmware
        if remote.state in {
            RemoteFirmwareState.FAILED,
            RemoteFirmwareState.UNPUBLISHED,
            RemoteFirmwareState.UNCONFIGURED,
        }:
            self._install_after_remote_download = False
            self._remote_install_confirmation_scheduled = False
            return
        snapshot = model.snapshot
        if (
            not self._install_after_remote_download
            or self._remote_install_confirmation_scheduled
            or remote.state is not RemoteFirmwareState.DOWNLOADED
            or self._view_model.firmware_update.state is not FirmwareUpdateState.PACKAGE_READY
            or snapshot is None
            or snapshot.connection_kind == "bluetooth"
        ):
            return
        self._remote_install_confirmation_scheduled = True
        QTimer.singleShot(0, self._window, self.confirm_downloaded_remote_firmware)

    def confirm_downloaded_remote_firmware(self) -> None:
        self._remote_install_confirmation_scheduled = False
        if not self._install_after_remote_download:
            return
        remote = self._view_model.remote_firmware
        transaction = self._view_model.firmware_update
        snapshot = self._view_model.model.snapshot
        if (
            remote.state is not RemoteFirmwareState.DOWNLOADED
            or transaction.state is not FirmwareUpdateState.PACKAGE_READY
            or snapshot is None
            or snapshot.connection_kind == "bluetooth"
        ):
            return
        self._install_after_remote_download = False
        self.confirm_update()

    def prepare_bundled_recovery(self) -> None:
        if self._build_identity.is_dev_build:
            return  # design decision: development packages offer no way back.
        self._reveal_update_notice()
        # The normal import path retains signature, hardware and image checks.
        # Loading only prepares the package; installation needs the existing confirmation.
        resource = files("controller_config.assets").joinpath("official-recovery.zip")
        if not resource.is_file():
            # Community build: no bundled firmware; pick an official signed ZIP instead.
            self._select_package(custom=False)
            return
        with as_file(resource) as path:
            self._load_selected_package(str(path), custom=False)

    def _select_package(self, _checked: bool = False, *, custom: bool = False) -> None:
        if self.file_dialog is not None:
            self.file_dialog.show()
            self.file_dialog.raise_()
            self.file_dialog.activateWindow()
            return
        if self._file_dialog_pending:
            return
        self._file_dialog_pending = True
        QTimer.singleShot(0, self._window, lambda: self._open_package_dialog(custom=custom))

    def _open_package_dialog(self, *, custom: bool) -> None:
        self._file_dialog_pending = False
        dialog = QFileDialog(
            self._window,
            translate_ui_text("导入自定义固件" if custom else "导入官方固件"),
            "",
            translate_ui_text(
                "BORING 固件维护包 (*.zip *.json);;ZIP 压缩包 (*.zip);;firmware-manifest.json (*.json)"
            ),
        )
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        viewport = self._quick_viewport()
        restore_quick_overlay = bool(
            viewport is not None
            and isValid(viewport)
            and viewport.testAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
        )
        if restore_quick_overlay:
            # Keep the persistent Quick3D scene loaded, but remove its special
            # overlay while Cocoa owns the native file panel's composition.
            viewport.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
            viewport.update()
        self.file_dialog = dialog
        dialog.finished.connect(
            lambda result: self._finish_package_dialog(
                dialog,
                result,
                custom=custom,
                restore_quick_overlay=restore_quick_overlay,
            )
        )
        dialog.open()

    def _finish_package_dialog(
        self,
        dialog: QFileDialog,
        result: int,
        *,
        custom: bool,
        restore_quick_overlay: bool,
    ) -> None:
        if self.file_dialog is not dialog:
            return
        selected = dialog.selectedFiles() if result == QDialog.DialogCode.Accepted else []
        self.file_dialog = None
        viewport = self._quick_viewport()
        if restore_quick_overlay and viewport is not None and isValid(viewport):
            viewport.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
            viewport.update()
        dialog.deleteLater()
        if selected:
            QTimer.singleShot(
                0,
                self._window,
                lambda file_name=selected[0]: self._load_selected_package(
                    file_name,
                    custom=custom,
                ),
            )

    def _load_selected_package(self, file_name: str, *, custom: bool) -> None:
        try:
            self._view_model.load_firmware_package(Path(file_name), custom=custom)
        except (OSError, ValueError) as exc:
            notice.notify(self._window, "固件维护包导入失败", str(exc))

    def confirm_update(self) -> None:
        package = self._view_model.firmware_update.package
        snapshot = self._view_model.model.snapshot
        if package is None or snapshot is None:
            return
        warning = ""
        if package.source_kind == "custom":
            warning = translate_ui_text("此固件未经官方验证。安装将替换当前固件，可能改变功能、配置兼容性及后续连接能力。请先导出重要配置并准备恢复用的官方固件。") + "\n\n"
        elif self._view_model.firmware_origin != "official" or self._view_model.remote_firmware.restoration:
            warning = translate_ui_text("恢复官方固件将替换当前固件及自定义功能，不会合并代码。请先保留你的源码和重要配置。") + "\n\n"
        text = (
            warning + translate_ui_text("设备：{serial}\n当前版本：{current}\n目标版本：{target}\n\n安装期间请保持 USB 连接。设备会重启，请等待控制台确认更新结果。").format(
                serial=snapshot.identity.get("serial", "—"),
                current=_firmware_version_display(snapshot.versions.get("firmware", "—"), snapshot.versions.get("build_id", "")),
                target=_firmware_version_display(package.version, package.build_id),
            )
        )
        install_label = "安装自定义固件" if package.source_kind == "custom" else "安装官方固件"
        if notice.choose(
            self._window, "确认安装固件", text,
            [("install", install_label), ("cancel", "取消")],
            default="cancel", escape="cancel", danger=True, object_name="firmwareInstallConfirm",
        ) != "install":
            return
        try:
            self._view_model.start_firmware_update()
        except ValueError as exc:
            notice.alert(self._window, "无法开始固件维护", str(exc))

    def _confirm_abort(self) -> None:
        choice = notice.ask(
            self._window,
            "确认中止固件接收",
            "设备将丢弃当前非活动 OTA 分区中的未完成接收事务；当前正在运行的固件不会被替换。",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
            danger=True,
            labels={QMessageBox.Yes: "中止传输"}  # the title reads 确认中止传输,
        )
        if choice != QMessageBox.Yes:
            return
        try:
            self._view_model.abort_firmware_update()
        except ValueError as exc:
            notice.alert(self._window, "无法中止固件维护", str(exc))

    def _dismiss(self) -> None:
        try:
            self._view_model.dismiss_firmware_update()
            self._view_model.navigate("overview")
        except ValueError as exc:
            notice.alert(self._window, "无法退出固件维护", str(exc))


def _firmware_version_display(version: str, build_id: str | None) -> str:
    # Keep same-version daily builds distinguishable; the complete ID stays in details.
    build = (build_id or "").split("-g", 1)[0]
    return f"{version} · {build}" if build else version


def _configure_remote_firmware_progress(
    progress: ProgressRingRow,
    remote: RemoteFirmwareCheck,
) -> None:
    progress.ring.set_state("running")
    if remote.state is RemoteFirmwareState.CHECKING:
        progress.ring.set_value(None)
        progress.set_text(translate_ui_text("正在检查固件更新…"))
        return
    progress.ring.set_value(remote.progress_percent)
    progress.set_text(translate_ui_text("正在下载固件"))


def _configure_device_firmware_progress(
    progress: ProgressRingRow,
    transaction: FirmwareUpdateTransaction,
) -> None:
    indeterminate_labels = {
        FirmwareUpdateState.CHECKING: "正在读取设备状态…",
        FirmwareUpdateState.BEGINNING: "正在开始固件更新…",
        FirmwareUpdateState.ABORTING: "正在停止固件更新…",
    }
    if transaction.state in indeterminate_labels:
        progress.ring.set_state("running")
        progress.ring.set_value(None)
        progress.set_text(translate_ui_text(indeterminate_labels[transaction.state]))
        return
    completed = transaction.state is FirmwareUpdateState.COMPLETED
    progress.ring.set_value(100 if completed else transaction.progress_percent)
    progress.ring.set_state(
        "done" if completed
        else "paused" if transaction.state is FirmwareUpdateState.PAUSED
        else "running"
    )
    label = {
        FirmwareUpdateState.TRANSFERRING: "正在安装固件",
        FirmwareUpdateState.FINALIZING: "正在校验固件",
        FirmwareUpdateState.WAITING_RECONNECT: "设备正在重启",
        FirmwareUpdateState.VERIFYING: "正在确认新版本",
        FirmwareUpdateState.PAUSED: "连接中断，等待恢复",
        FirmwareUpdateState.COMPLETED: "固件更新完成",
    }.get(transaction.state, "")
    progress.set_text(translate_ui_text(label) if label else "")


def _firmware_transaction_title(state: FirmwareUpdateState) -> str:
    return {
        FirmwareUpdateState.IDLE: "尚未选择维护包",
        FirmwareUpdateState.PACKAGE_READY: "维护包已通过本地检查",
        FirmwareUpdateState.CHECKING: "正在读取设备更新状态",
        FirmwareUpdateState.NEEDS_ABORT: "发现另一份未完成更新",
        FirmwareUpdateState.BEGINNING: "正在建立设备接收事务",
        FirmwareUpdateState.TRANSFERRING: "正在传输固件镜像",
        FirmwareUpdateState.FINALIZING: "设备正在校验镜像",
        FirmwareUpdateState.WAITING_RECONNECT: "等待设备重启并重新连接",
        FirmwareUpdateState.VERIFYING: "正在核对新固件身份",
        FirmwareUpdateState.ABORTING: "正在中止接收事务",
        FirmwareUpdateState.PAUSED: "连接中断，等待对账续传",
        FirmwareUpdateState.COMPLETED: "固件维护已读回确认",
        FirmwareUpdateState.FAILED: "固件维护未完成",
    }[state]
