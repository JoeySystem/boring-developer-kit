from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal, QRectF, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFormLayout,
    QBoxLayout,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from controller_config.prompt_library import (
    PROMPT_BODY_MAX_BYTES,
    PROMPT_NAME_MAX_BYTES,
    QUICK_PROMPT_DIRECTIONS,
    QUICK_PROMPT_IDS,
    PromptLibrary,
    PromptLibraryError,
)
from controller_config.i18n import translate_ui_text
from controller_config.prompt_device import (
    PromptEventLogEntry,
    PromptListenerState,
    PromptListenerStatus,
)
from controller_config.views.v4_widgets import V4Card
from controller_config.views.digital_label import Boring5RLabel
from controller_config.appearance import V4_STYLE


class PromptDevicePreview(QGraphicsView):
    """A cropped view of the existing device renderer; directions remain UI-only."""

    def __init__(self, shell: QWidget, buttons: dict[int, QPushButton]) -> None:
        super().__init__()
        self.setObjectName("promptDevicePreview")
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setStyleSheet("QGraphicsView { background: transparent; border: none; }")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(280, 520)
        scene = QGraphicsScene(self)
        self.setScene(scene)
        shell.ensurePolished()
        shell.adjustSize()
        if shell.layout() is not None:
            shell.layout().activate()
        shell.setAttribute(Qt.WA_TranslucentBackground)
        rendered_shell = shell.grab()
        joystick = shell.findChild(QWidget, "joystickControl")
        if joystick is not None:
            center = joystick.mapTo(shell, joystick.rect().center())
            cx, cy = center.x(), center.y()
        else:
            cx, cy = shell.width() * .8, shell.height() * .8
        # Render the existing widget once. A pixmap retains rounded transparency
        # without inheriting platform-native proxy window backgrounds.
        crop_x, crop_y = max(0, cx - 145), max(0, cy - 195)
        dpr = rendered_shell.devicePixelRatio()
        scene.addPixmap(rendered_shell.copy(QRect(
            round(crop_x * dpr), round(crop_y * dpr), round(270 * dpr), round(360 * dpr)
        )))
        shell.deleteLater()
        for prompt_id, (dx, dy) in {1: (0, -65), 2: (65, 0), 3: (0, 65), 4: (-65, 0)}.items():
            buttons[prompt_id].setAttribute(Qt.WA_TranslucentBackground)
            buttons[prompt_id].setStyleSheet(V4_STYLE)
            item = scene.addWidget(buttons[prompt_id])
            item.setPos(cx - crop_x + dx - 16, cy - crop_y + dy - 16)
            item.setZValue(1)
        self.setSceneRect(QRectF(0, 0, 270, 360))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


@dataclass(frozen=True)
class PromptEditingState:
    device_serial: str
    prompt_id: int
    name: str
    body: str
    focused_field: str | None = None
    preserve_fields: bool = True


class PromptLibraryEditor(QScrollArea):
    direction_selected = Signal(int)

    def __init__(
        self,
        library: PromptLibrary | None,
        *,
        device_storage_available: bool,
        protocol_message: str,
        device_busy: bool,
        helper_message: str,
        listener_status: PromptListenerStatus,
        event_log: tuple[PromptEventLogEntry, ...],
        background_message: str,
        save_draft: Callable[[int, str, str], object],
        delete_draft: Callable[[int], None],
        discard_draft: Callable[[int], None],
        refresh_device: Callable[[], None],
        write_device: Callable[[int], None],
        delete_device: Callable[[int], None],
        device_preview: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._save_draft = save_draft
        self._delete_draft = delete_draft
        self._discard_draft = discard_draft
        self._refresh_device = refresh_device
        self._write_device = write_device
        self._delete_device = delete_device
        self._selected_prompt_id_value = QUICK_PROMPT_IDS[0]
        self._direction_buttons: dict[int, QPushButton] = {}
        self._preview_buttons: dict[int, QPushButton] = {}
        self._device_preview = device_preview
        self._preserve_fields_on_render = True
        self._device_storage_available = device_storage_available
        self._device_busy = device_busy
        self._helper_message = helper_message
        self._listener_status = listener_status
        self._event_log = event_log
        self._background_message = background_message
        self._protocol_message = protocol_message
        self.setObjectName("promptLibraryScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QScrollArea { background: transparent; }")

        page = QWidget(objectName="promptLibraryPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)
        if library is None:
            layout.addWidget(self._summary_card(device_storage_available, protocol_message))
            layout.addWidget(self._no_device_card())
        else:
            layout.addWidget(self._editor_card())
        if library is None:
            layout.addWidget(self._helper_card())
        layout.addStretch(1)
        self.setWidget(page)

    def _summary_card(
        self, device_storage_available: bool, protocol_message: str
    ) -> QWidget:
        card = V4Card(role="widget") if self._library is None else QWidget()
        row = QHBoxLayout(card)
        if self._library is None:
            row.setContentsMargins(24, 20, 24, 22)
        else:
            row.setContentsMargins(0, 0, 0, 0)
        title = QVBoxLayout()
        if self._library is None:
            title.addWidget(QLabel("QUICK PROMPTS", objectName="eyebrow"))
            title.addWidget(QLabel("四向提示词盘", objectName="inspectorTitle"))
        note = QLabel(
            "为提示词盘准备四条常用提示词：先打开，再选择，最后确认；移动摇杆仅选择，不直接触发。",
            objectName="muted",
        )
        note.setWordWrap(True)
        instructions = QWidget(objectName="promptPaletteInstructions")
        instructions_box = QVBoxLayout(instructions)
        instructions_box.setContentsMargins(0, 0, 0, 0)
        instructions_box.addWidget(note)
        guide = QLabel(
            "打开：长按 Key12 约 0.8 秒。\n"
            "选择：摇杆上 / 右 / 下 / 左对应提示词 1 / 2 / 3 / 4；旋钮短按确认。\n"
            "退出：Key3 取消；10 秒无操作自动退出。",
            objectName="promptPaletteGuide",
        )
        guide.setWordWrap(True)
        instructions_box.addWidget(guide)
        behavior = QLabel(
            "Key12 短按保留当前模式原功能。提示词盘打开期间由设备接管输入，不向电脑发送按键、滚动或提示词事件；确认后才触发所选提示词。",
            objectName="muted",
        )
        behavior.setWordWrap(True)
        instructions_box.addWidget(behavior)
        instructions.setVisible(False)
        show_guide = QPushButton("查看设备操作步骤", objectName="promptGuideToggle")
        show_guide.setProperty("buttonRole", "ghost")
        show_guide.setCheckable(True)
        show_guide.toggled.connect(instructions.setVisible)
        title.addWidget(show_guide, 0, Qt.AlignmentFlag.AlignLeft)
        title.addWidget(instructions)
        row.addLayout(title, 1)
        status = QLabel(
            "设备存储可用" if device_storage_available else "设备存储不可用",
            objectName="statusReady" if device_storage_available else "statusWarn",
        )
        status.setToolTip(protocol_message)
        title.addWidget(status, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _no_device_card(self) -> QWidget:
        card = V4Card(role="primary", dots=True)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 22, 24, 24)
        box.addWidget(QLabel("请先连接设备", objectName="inspectorTitle"))
        detail = QLabel(
            "四条提示词草稿按设备序列号保存。连接设备后可打开对应的草稿与最近一次设备读回内容。",
            objectName="muted",
        )
        detail.setWordWrap(True)
        box.addWidget(detail)
        return card

    def _editor_card(self) -> QWidget:
        assert self._library is not None
        card = QWidget(objectName="promptWorkspace")
        self._workspace_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, card)
        self._workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_layout.setSpacing(16)
        choices = V4Card(role="primary", dots=True)
        choices.setObjectName("promptChoicesCard")
        choices_box = QVBoxLayout(choices)
        choices_box.setContentsMargins(16, 16, 16, 16)
        choices_box.setSpacing(10)
        self._choices_heading = Boring5RLabel("PROMPTS", scale=.9, color="#A5A195")
        choices_box.addWidget(self._choices_heading)
        focus = V4Card(role="focus")
        focus.setObjectName("promptFocusCard")
        box = QVBoxLayout(focus)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(8)
        self._focus_direction_title = Boring5RLabel("", scale=1.35, color="#EFEAE0")
        focus_header = QHBoxLayout()
        focus_header.addWidget(self._focus_direction_title)
        self._slot_title = QLabel(objectName="promptSlotTitle")
        focus_header.addWidget(self._slot_title)
        focus_header.addStretch(1)
        box.addLayout(focus_header)
        left = QWidget(objectName="promptLeftColumn")
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(16)
        left_box.addWidget(choices, 3)
        right = QWidget(objectName="promptRightColumn")
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(16)
        right_box.addWidget(focus, 3)
        self._workspace_layout.addWidget(left, 5)
        self._workspace_layout.addWidget(right, 5)

        header = QHBoxLayout()
        configured = sum(
            self._library.draft_entry(prompt_id) is not None
            for prompt_id in QUICK_PROMPT_IDS
        )
        total = QLabel(
            f"{configured}/4 个方向已配置",
            objectName="muted",
        )
        total.setObjectName("promptQuickCount")
        header.addWidget(total)
        choices_box.addLayout(header)

        direction_grid = QVBoxLayout()
        direction_grid.setSpacing(4)
        for control_id, prompt_id, arrow, label in QUICK_PROMPT_DIRECTIONS:
            button = QPushButton(objectName="promptDirectionCard")
            button.setMinimumHeight(44)
            button.setMinimumWidth(0)
            button.setProperty("directionControlId", control_id)
            button.setProperty("promptId", prompt_id)
            button.setProperty("selected", prompt_id == self._selected_prompt_id_value)
            button.clicked.connect(
                lambda _checked=False, selected=prompt_id: self._select_direction(selected)
            )
            self._direction_buttons[prompt_id] = button
            direction_grid.addWidget(button)
            direction = QPushButton(arrow, objectName="promptPreviewDirection")
            direction.setFixedSize(32, 32)
            direction.setToolTip(translate_ui_text(label))
            direction.setAccessibleName(translate_ui_text(label))
            direction.clicked.connect(lambda _checked=False, selected=prompt_id: self._select_direction(selected))
            self._preview_buttons[prompt_id] = direction
        if self._device_preview is not None:
            stage = QWidget(objectName="promptDirectionStage")
            stage_box = QVBoxLayout(stage)
            stage_box.setContentsMargins(0, 30, 0, 30)
            stage_box.addStretch(1)
            stage_box.addWidget(PromptDevicePreview(self._device_preview, self._preview_buttons), 5)
            caption = QLabel("点击方向编辑提示词", objectName="muted")
            caption.setAlignment(Qt.AlignCenter)
            stage_box.addWidget(caption)
            stage_box.addStretch(1)
            self._workspace_layout.insertWidget(1, stage, 9)
        else:
            for button in self._preview_buttons.values():
                button.setParent(card)
                button.hide()
        choices_box.addLayout(direction_grid)
        choices_note = QLabel("摇杆选择\n旋钮确认", objectName="promptJoystickCenter")
        choices_note.setWordWrap(True)
        choices_box.addWidget(choices_note)
        choices_box.addWidget(self._summary_card(self._device_storage_available, self._protocol_message))
        choices_box.addStretch(1)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._direction_selection = QLabel(objectName="promptDirectionSelection")
        self._direction_selection.setParent(focus)
        self._direction_selection.hide()

        self._sync_state = QLabel(objectName="promptSyncState")
        form.addRow(self._sync_state)

        self._name = QLineEdit(objectName="promptNameEditor")
        self._name.setMinimumHeight(42)
        self._name.setProperty("pill", True)
        self._name.setPlaceholderText("例如：代码审查")
        form.addRow("名称", self._name)
        self._name_bytes = QLabel(objectName="muted")
        form.addRow(self._name_bytes)

        self._body = QPlainTextEdit(objectName="promptBodyEditor")
        self._body.setPlaceholderText(translate_ui_text("输入中文、英文、换行和常用符号；不会自动按 Enter 提交。"))
        self._body.setFixedHeight(150)
        form.addRow("正文", self._body)
        self._body_bytes = QLabel(objectName="muted")
        form.addRow(self._body_bytes)
        box.addLayout(form)

        more = QWidget(objectName="promptMoreActions")
        buttons = QGridLayout(more)
        buttons.setContentsMargins(0, 0, 0, 0)
        save = QPushButton("保存本地草稿", objectName="primary")
        save.setObjectName("savePromptDraft")
        save.clicked.connect(self._save)
        save.setProperty("buttonRole", "secondary")
        discard = QPushButton("丢弃本方向修改", objectName="secondary")
        discard.setObjectName("discardPromptDraft")
        discard.clicked.connect(self._discard)
        buttons.addWidget(discard, 0, 0)
        delete = QPushButton("从本地草稿删除", objectName="secondary")
        delete.setObjectName("deletePromptDraft")
        delete.clicked.connect(self._delete)
        delete.setProperty("buttonRole", "ghost")
        buttons.addWidget(delete, 1, 0)

        device_buttons = QGridLayout()
        self._write_device_button = QPushButton(
            "写入设备并读回确认", objectName="primary"
        )
        self._write_device_button.setObjectName("writePromptDevice")
        self._write_device_button.clicked.connect(self._write_to_device)
        self._write_device_button.setProperty("buttonRole", "primary")
        self._write_device_button.setText("写入设备并读回")
        device_buttons.addWidget(self._write_device_button, 0, 0)
        device_buttons.addWidget(save, 0, 1)
        refresh = QPushButton("从设备重新读取", objectName="secondary")
        refresh.setObjectName("refreshPromptDevice")
        refresh.setEnabled(self._device_storage_available and not self._device_busy)
        refresh.clicked.connect(self._refresh_from_device)
        buttons.addWidget(refresh, 2, 0)
        self._delete_device_button = QPushButton(
            "从设备删除", objectName="secondary"
        )
        self._delete_device_button.setObjectName("deletePromptDevice")
        self._delete_device_button.clicked.connect(self._delete_from_device)
        self._delete_device_button.setProperty("buttonRole", "ghost")
        device_buttons.addWidget(self._delete_device_button, 1, 0, 1, 2)
        box.addLayout(device_buttons)
        toggle = QPushButton("更多操作", objectName="promptMoreToggle")
        toggle.setProperty("buttonRole", "ghost")
        toggle.setCheckable(True)
        toggle.toggled.connect(more.setVisible)
        more.hide()
        box.addWidget(toggle)
        box.addWidget(more)

        boundary = QLabel(
            "提示词盘固定使用槽位 1–4，与配置方案中的普通摇杆映射无关；此页不会修改这些映射。保存草稿后，仍需写入设备并读回确认才会在设备上生效。",
            objectName="roleContext",
        )
        boundary.setWordWrap(True)
        buttons.addWidget(boundary, 3, 0)

        helper = self._helper_card()
        left_box.addWidget(helper.findChild(QWidget, "promptPasteCard"), 1)
        right_box.addWidget(helper.findChild(QWidget, "promptEventsCard"), 1)
        helper.deleteLater()
        del self._helper_layout

        self._name.textChanged.connect(self._update_byte_counts)
        self._body.textChanged.connect(self._update_byte_counts)
        self._load_selected()
        return card

    def _helper_card(self) -> QWidget:
        card = QWidget(objectName="promptHelperWorkspace")
        self._helper_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, card)
        self._helper_layout.setContentsMargins(0, 0, 0, 0)
        self._helper_layout.setSpacing(16)
        paste = V4Card(role="widget")
        paste.setObjectName("promptPasteCard")
        events = V4Card(role="widget")
        events.setObjectName("promptEventsCard")
        self._helper_layout.addWidget(paste, 1)
        self._helper_layout.addWidget(events, 1)
        box = QVBoxLayout(paste)
        box.setContentsMargins(24, 20, 24, 22)
        box.setSpacing(8)
        self._paste_heading = Boring5RLabel("PASTE", scale=.9, color="#A5A195")
        box.addWidget(self._paste_heading)
        detail = QLabel(
            "后台核心已经使用系统剪贴板和 Command+V 插入 UTF-8 正文，且不会自动按 Enter。首次实际粘贴时 macOS 可能要求辅助功能权限；失败时正文仍保留在剪贴板，不会发送乱码。",
            objectName="muted",
        )
        detail.setWordWrap(True)
        detail.hide()
        detail_toggle = QPushButton("粘贴说明", objectName="promptPasteHelp")
        detail_toggle.setProperty("buttonRole", "ghost")
        detail_toggle.setCheckable(True)
        detail_toggle.toggled.connect(detail.setVisible)
        status_row = QHBoxLayout()
        status_badge = QLabel(
            self._listener_status.label,
            objectName="promptHelperOnlineState",
        )
        status_badge.setProperty(
            "listenerOnline",
            self._listener_status.state is PromptListenerState.READY,
        )
        status_row.addWidget(status_badge)
        status_row.addStretch(1)
        box.addLayout(status_row)

        listener_detail = QLabel(
            translate_ui_text(self._listener_status.message), objectName="muted"
        )
        listener_detail.setObjectName("promptListenerStatus")
        listener_detail.setWordWrap(True)
        if self._listener_status.technical:
            listener_detail.setToolTip(self._listener_status.technical)
        box.addWidget(listener_detail)

        box.addWidget(QLabel("最近结果", objectName="eyebrow"))
        helper_status = QLabel(
            translate_ui_text(self._helper_message), objectName="muted"
        )
        helper_status.setObjectName("promptHelperStatus")
        helper_status.setWordWrap(True)
        box.addWidget(helper_status)

        background = QLabel(
            translate_ui_text(self._background_message), objectName="roleContext"
        )
        background.setObjectName("promptBackgroundStatus")
        background.setWordWrap(True)
        box.addWidget(background)
        box.addWidget(detail_toggle)
        box.addWidget(detail)
        box.addStretch(1)

        box = QVBoxLayout(events)
        box.setContentsMargins(24, 20, 24, 22)
        box.setSpacing(8)
        self._events_heading = Boring5RLabel("EVENTS", scale=.9, color="#A5A195")
        box.addWidget(self._events_heading)
        event_log = QPlainTextEdit(objectName="promptEventLog")
        event_log.setReadOnly(True)
        event_log.setMaximumHeight(100)
        event_log.setMinimumHeight(100)
        event_log.setPlainText(_event_log_text(self._event_log))
        box.addWidget(event_log)
        boundary = QLabel(
            self._protocol_message,
            objectName="roleContext",
        )
        boundary.setObjectName("promptProtocolBoundary")
        boundary.setWordWrap(True)
        box.addWidget(boundary)
        box.addStretch(1)
        return card

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace_layout"):
            direction = (
                QBoxLayout.Direction.TopToBottom
                if self.viewport().width() < 1060
                else QBoxLayout.Direction.LeftToRight
            )
            if self._workspace_layout.direction() != direction:
                self._workspace_layout.setDirection(direction)
        if hasattr(self, "_helper_layout"):
            self._helper_layout.setDirection(
                QBoxLayout.Direction.TopToBottom
                if self.viewport().width() < 820
                else QBoxLayout.Direction.LeftToRight
            )
        if self._direction_buttons:
            self._refresh_direction_cards()

    def _selected_prompt_id(self) -> int:
        return self._selected_prompt_id_value

    def editing_state(self) -> PromptEditingState | None:
        """Capture unsaved field contents before the surrounding page is rebuilt."""
        if self._library is None or not hasattr(self, "_name"):
            return None
        focus_widget = self.window().focusWidget()
        return PromptEditingState(
            device_serial=self._library.serial,
            prompt_id=self._selected_prompt_id(),
            name=self._name.text(),
            body=self._body.toPlainText(),
            focused_field=(
                "name"
                if focus_widget is self._name
                else "body"
                if focus_widget is self._body
                else "direction"
                if focus_widget in self._direction_buttons.values()
                else None
            ),
            preserve_fields=self._preserve_fields_on_render,
        )

    def restore_editing_state(self, state: PromptEditingState) -> None:
        """Restore UI-only edits without treating them as a saved local draft."""
        if (
            self._library is None
            or state.device_serial != self._library.serial
            or state.prompt_id not in QUICK_PROMPT_IDS
        ):
            return
        self._load_direction(state.prompt_id)
        if not state.preserve_fields:
            return
        self._name.blockSignals(True)
        self._body.blockSignals(True)
        self._name.setText(state.name)
        self._body.setPlainText(state.body)
        self._name.blockSignals(False)
        self._body.blockSignals(False)
        draft = self._library.draft_entry(state.prompt_id) if self._library is not None else None
        if draft is None or state.name != draft.name or state.body != draft.body:
            self._sync_state.setText("尚未保存到本地草稿")
        self._update_byte_counts()
        if state.focused_field == "name":
            self._name.setFocus()
        elif state.focused_field == "body":
            self._body.setFocus()
        elif state.focused_field == "direction":
            self._direction_buttons[state.prompt_id].setFocus()

    def _select_direction(self, prompt_id: int) -> None:
        if prompt_id not in QUICK_PROMPT_IDS or prompt_id == self._selected_prompt_id_value:
            return
        if not self.confirm_leave():
            return
        self._load_direction(prompt_id)
        self.direction_selected.emit(prompt_id)

    def has_unsaved_fields(self) -> bool:
        if self._library is None or not hasattr(self, "_name"):
            return False
        entry = self._library.draft_entry(self._selected_prompt_id())
        return (self._name.text(), self._body.toPlainText()) != (
            entry.name if entry else "", entry.body if entry else ""
        )

    def confirm_leave(self) -> bool:
        if not self.has_unsaved_fields():
            return True
        choice = QMessageBox.warning(
            self, "提示词输入尚未保存",
            "当前输入尚未保存到本地草稿。保存后仅保留在电脑，不会写入设备。",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            return self._save()
        return choice == QMessageBox.Discard

    def _load_direction(self, prompt_id: int) -> None:
        self._selected_prompt_id_value = prompt_id
        for candidate, button in self._direction_buttons.items():
            button.setProperty("selected", candidate == prompt_id)
            button.style().unpolish(button)
            button.style().polish(button)
        self._load_selected()

    def _load_selected(self) -> None:
        if self._library is None:
            return
        prompt_id = self._selected_prompt_id()
        _control_id, _expected_id, arrow, direction_label = self._direction(prompt_id)
        direction_title = {1: "UP", 2: "RIGHT", 3: "DOWN", 4: "LEFT"}[prompt_id]
        self._focus_direction_title.setText(direction_title)
        self._slot_title.setText(f"PROMPT {prompt_id}")
        entry = self._library.draft_entry(prompt_id)
        confirmed = self._library.confirmed_entry(prompt_id)
        self._direction_selection.setText(
            f"{arrow} {direction_label} · 快捷提示词 {prompt_id}"
        )
        self._name.blockSignals(True)
        self._body.blockSignals(True)
        self._name.setText(entry.name if entry is not None else "")
        self._body.setPlainText(entry.body if entry is not None else "")
        self._name.blockSignals(False)
        self._body.blockSignals(False)
        if entry == confirmed and entry is not None:
            state = "与设备读回缓存一致"
        elif entry is None and confirmed is None:
            state = "空槽位"
        else:
            state = "有尚未写入设备的本地修改"
        self._sync_state.setText(state)
        dirty = entry != confirmed
        self._write_device_button.setEnabled(
            self._device_storage_available
            and not self._device_busy
            and entry is not None
            and dirty
        )
        self._delete_device_button.setEnabled(
            self._device_storage_available
            and not self._device_busy
            and confirmed is not None
        )
        self._refresh_direction_cards()
        for candidate, button in self._preview_buttons.items():
            button.setProperty("selected", candidate == prompt_id)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_byte_counts()

    def _refresh_direction_cards(self) -> None:
        if self._library is None:
            return
        for _control_id, prompt_id, arrow, direction_label in QUICK_PROMPT_DIRECTIONS:
            entry = self._library.draft_entry(prompt_id)
            name = (
                entry.name
                if entry is not None
                else translate_ui_text("未设置提示词")
            )
            button = self._direction_buttons[prompt_id]
            configured = entry is not None
            if button.property("configured") != configured:
                button.setProperty("configured", configured)
                button.style().unpolish(button)
                button.style().polish(button)
            slot_label = translate_ui_text(f"提示词槽位 {prompt_id}")
            button.setToolTip(f"{translate_ui_text(direction_label)} · {slot_label}\n{name}")
            short_name = button.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, max(60, min(120, button.width() - 24))
            )
            button.setText(
                f"{arrow}  {translate_ui_text(direction_label)}   {short_name}"
            )

    @staticmethod
    def _direction(prompt_id: int) -> tuple[str, int, str, str]:
        for direction in QUICK_PROMPT_DIRECTIONS:
            if direction[1] == prompt_id:
                return direction
        raise ValueError(f"未知快捷提示词 {prompt_id}")

    def _update_byte_counts(self) -> None:
        name_bytes = len(self._name.text().encode("utf-8"))
        body_bytes = len(self._body.toPlainText().encode("utf-8"))
        self._name_bytes.setText(f"{name_bytes}/{PROMPT_NAME_MAX_BYTES} UTF-8 字节")
        self._body_bytes.setText(f"{body_bytes}/{PROMPT_BODY_MAX_BYTES} UTF-8 字节")
        self._name_bytes.setStyleSheet(
            "color: #8b2f24;" if name_bytes > PROMPT_NAME_MAX_BYTES else ""
        )
        self._body_bytes.setStyleSheet(
            "color: #8b2f24;" if body_bytes > PROMPT_BODY_MAX_BYTES else ""
        )

    def _save(self) -> bool:
        self._preserve_fields_on_render = False
        try:
            self._save_draft(
                self._selected_prompt_id(),
                self._name.text(),
                self._body.toPlainText(),
            )
            return True
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法保存提示词草稿", str(exc))
            return False
        finally:
            self._preserve_fields_on_render = True

    def _discard(self) -> None:
        self._preserve_fields_on_render = False
        try:
            self._discard_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法丢弃提示词修改", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _delete(self) -> None:
        self._preserve_fields_on_render = False
        try:
            self._delete_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法删除提示词草稿", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _refresh_from_device(self) -> None:
        self._preserve_fields_on_render = False
        try:
            self._refresh_device()
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法读取设备提示词", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _write_to_device(self) -> None:
        prompt_id = self._selected_prompt_id()
        choice = QMessageBox.warning(
            self,
            "确认写入提示词",
            f"将覆盖设备的{self._direction(prompt_id)[3]}方向快捷提示词，随后立即读回全文确认。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        try:
            self._write_device(prompt_id)
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法写入设备提示词", str(exc))

    def _delete_from_device(self) -> None:
        prompt_id = self._selected_prompt_id()
        choice = QMessageBox.warning(
            self,
            "确认删除设备提示词",
            f"将删除设备的{self._direction(prompt_id)[3]}方向快捷提示词。该方向在重新配置前不会执行提示词。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._delete_device(prompt_id)
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法删除设备提示词", str(exc))
        finally:
            self._preserve_fields_on_render = True


def _event_log_text(entries: tuple[PromptEventLogEntry, ...]) -> str:
    if not entries:
        return translate_ui_text("尚无事件记录")
    lines = []
    for entry in entries:
        line = f"{entry.sequence:02d} · {translate_ui_text(entry.message)}"
        if entry.technical:
            line = f"{line}\n     {entry.technical}"
        lines.append(line)
    return "\n".join(lines)
