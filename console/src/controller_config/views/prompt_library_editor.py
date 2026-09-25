from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
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
from controller_config.i18n import translate_ui_text, set_translatable_text
from controller_config.prompt_device import (
    PromptEventLogEntry,
    PromptListenerState,
    PromptListenerStatus,
)
from controller_config.views.v4_widgets import V4Card
from controller_config.views.digital_label import Boring5RLabel
from controller_config.views.draft_dialog import confirm_local_draft
from controller_config.appearance import V4_STYLE


class PromptDevicePreview(QWidget):
    """The full shared 3D model with controls anchored around its joystick."""

    def __init__(self, shell: QWidget, buttons: dict[int, QPushButton]) -> None:
        super().__init__()
        self.setObjectName("promptDevicePreview")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(320, 360)
        shell.ensurePolished()
        shell.adjustSize()
        shell.setAttribute(Qt.WA_TranslucentBackground)
        self._device_shell = shell
        self._device_shell.setParent(self)
        model_canvas = shell.findChild(QWidget, "deviceModelCanvas")
        if model_canvas is not None and hasattr(model_canvas, "set_preset"):
            model_canvas.set_preset("prompt")
        self._model_canvas = model_canvas
        self._buttons = buttons
        self._positions = {1: (0, -58), 2: (58, 0), 3: (0, 58), 4: (-58, 0)}
        control_ids = {
            prompt_id: control_id
            for control_id, prompt_id, _arrow, _label in QUICK_PROMPT_DIRECTIONS
        }
        for prompt_id in self._positions:
            buttons[prompt_id].setAttribute(Qt.WA_TranslucentBackground)
            buttons[prompt_id].setStyleSheet(V4_STYLE)
            if model_canvas is not None and hasattr(model_canvas, "preview_control"):
                buttons[prompt_id].clicked.connect(
                    lambda _checked=False,
                    value=control_ids[prompt_id],
                    canvas=model_canvas: canvas.preview_control(value)
                )
            if model_canvas is not None and hasattr(model_canvas, "attach_prompt_direction"):
                model_canvas.attach_prompt_direction(prompt_id, buttons[prompt_id])
            buttons[prompt_id].setParent(self)
            buttons[prompt_id].setVisible(
                not bool(getattr(model_canvas, "_realtime_enabled", False))
            )
            buttons[prompt_id].raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self._device_shell, "scale_to"):
            self._device_shell.scale_to(min(420, self.width(), self.height()))
        shell_x = (self.width() - self._device_shell.width()) // 2
        shell_y = (self.height() - self._device_shell.height()) // 2
        self._device_shell.move(shell_x, shell_y)
        if self._model_canvas is not None and hasattr(self._model_canvas, "control_rect"):
            center = self._model_canvas.control_rect("joystick").center()
            cx = shell_x + round(center.x())
            cy = shell_y + round(center.y())
        else:
            cx = shell_x + round(self._device_shell.width() * .75)
            cy = shell_y + round(self._device_shell.height() * .75)
        for prompt_id, (dx, dy) in self._positions.items():
            button = self._buttons[prompt_id]
            button.move(cx + dx - button.width() // 2, cy + dy - button.height() // 2)
            button.raise_()


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
        save_and_write_device: Callable[[int, str, str], None] | None = None,
        device_preview: QWidget | None = None,
        hardware_id: str | None = None,
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
        self._save_and_write_device = save_and_write_device
        self._selected_prompt_id_value = QUICK_PROMPT_IDS[0]
        self._direction_buttons: dict[int, QPushButton] = {}
        self._preview_buttons: dict[int, QPushButton] = {}
        self._device_preview = device_preview
        self._supports_prompt_palette = hardware_id == "WMP-S3-MATRIX12-POWER-V2"
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

    def update_runtime(self, *, available, busy, message, helper_message, listener, event_log, background_message) -> None:
        inputs = (available, busy, message, helper_message, listener, event_log, background_message)
        if inputs == getattr(self, "_runtime_inputs", None):
            return
        self._runtime_inputs = inputs
        self._device_storage_available = available
        self._device_busy = busy
        self._protocol_message = message
        self._helper_message = helper_message
        self._listener_status = listener
        self._event_log = event_log
        self._background_message = background_message
        set_translatable_text(self._storage_status, "设备存储可用" if available else "设备存储不可用")
        self._storage_status.setObjectName("statusReady" if available else "statusWarn")
        self._storage_status.setToolTip(message)
        self._storage_status.style().unpolish(self._storage_status)
        self._storage_status.style().polish(self._storage_status)
        for name, text in (("promptHelperOnlineState", listener.label),
                           ("promptListenerStatus", listener.message),
                           ("promptHelperStatus", helper_message),
                           ("promptBackgroundStatus", background_message),
                           ("promptProtocolBoundary", message)):
            set_translatable_text(self.findChild(QLabel, name), text)
        badge = self.findChild(QLabel, "promptHelperOnlineState")
        badge.setProperty("listenerOnline", listener.state is PromptListenerState.READY)
        badge.setToolTip(translate_ui_text(background_message))
        detail = self.findChild(QLabel, "promptListenerStatus")
        detail.setToolTip(listener.technical)
        detail.setVisible(listener.state is not PromptListenerState.READY)
        has_result = helper_message not in {"", "尚无设备触发记录"}
        self._recent_result_caption.setVisible(has_result)
        self.findChild(QLabel, "promptHelperStatus").setVisible(has_result)
        log = self.findChild(QPlainTextEdit, "promptEventLog")
        text = _event_log_text(event_log)
        if log.toPlainText() != text:
            position = log.verticalScrollBar().value()
            log.setPlainText(text)
            log.verticalScrollBar().setValue(position)
        if self._library is not None:
            self._write_device_button.setVisible(available)
            self._save_draft_button.setVisible(not available)
            self._refresh_primary_action()

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
            title.addWidget(QLabel(
                "四向提示词盘" if self._supports_prompt_palette else "快捷提示词",
                objectName="inspectorTitle",
            ))
        note = QLabel(
            "为提示词盘准备四条常用提示词：先打开，再选择，最后确认；移动摇杆仅选择，不直接触发。"
            if self._supports_prompt_palette else
            "保存提示词后，在按键配置中将摇杆方向绑定到对应提示词。",
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
            "退出：Key3 取消；10 秒无操作自动退出。"
            if self._supports_prompt_palette else
            "普通模式下，拨动已绑定的摇杆方向会直接触发对应提示词。",
            objectName="promptPaletteGuide",
        )
        guide.setWordWrap(True)
        instructions_box.addWidget(guide)
        if self._supports_prompt_palette:
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
        self._storage_status = status
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
        self._workspace_layout.setSpacing(12)
        choices = V4Card(role="primary", dots=True)
        choices.setObjectName("promptChoicesCard")
        choices_box = QVBoxLayout(choices)
        choices_box.setContentsMargins(16, 16, 16, 16)
        choices_box.setSpacing(10)
        self._choices_heading = Boring5RLabel("PROMPTS", scale=.9, color="#A5A195")
        self._choices_heading.hide()
        focus = V4Card(role="focus")
        focus.setObjectName("promptFocusCard")
        box = QVBoxLayout(focus)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(8)
        self._focus_direction_title = Boring5RLabel("", scale=1.35, color="#EFEAE0")
        focus_header = QHBoxLayout()
        focus_header.addWidget(self._focus_direction_title)
        self._slot_title = QLabel(objectName="promptSlotTitle")
        self._slot_title.hide()
        focus_header.addWidget(self._slot_title)
        focus_header.addStretch(1)
        box.addLayout(focus_header)
        left = QWidget(objectName="promptLeftColumn")
        left_box = QVBoxLayout(left)
        self._left_box = left_box
        self._left_column = left
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.setSpacing(12)
        left_box.addWidget(choices)
        right = QWidget(objectName="promptRightColumn")
        right_box = QVBoxLayout(right)
        self._right_box = right_box
        self._right_column = right
        right_box.setContentsMargins(0, 0, 0, 0)
        right_box.setSpacing(12)
        right_box.addWidget(focus)
        self._workspace_layout.addWidget(left, 4)
        self._workspace_layout.addWidget(right, 7)

        header = QHBoxLayout()
        configured = sum(
            self._library.confirmed_entry(prompt_id) is not None
            for prompt_id in QUICK_PROMPT_IDS
        )
        self._quick_count = QLabel(
            translate_ui_text("设备已保存 {v1}/4").format(v1=configured),
            objectName="muted",
        )
        self._quick_count.setObjectName("promptQuickCount")
        header.addWidget(self._quick_count)
        choices_box.addLayout(header)

        direction_grid = QVBoxLayout()
        direction_grid.setSpacing(4)
        for control_id, prompt_id, arrow, label in QUICK_PROMPT_DIRECTIONS:
            button = QPushButton(objectName="promptDirectionCard")
            button.setMinimumHeight(54)
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
            self._stage = stage
            stage_box = QVBoxLayout(stage)
            stage_box.setContentsMargins(0, 0, 0, 0)
            stage_box.setSpacing(8)
            preview = PromptDevicePreview(self._device_preview, self._preview_buttons)
            preview.setFixedHeight(360)
            stage_box.addWidget(preview)
            stage_box.addWidget(self._device_operation_guide())
            stage_box.addStretch(1)
            self._workspace_layout.insertWidget(1, stage, 6)
        else:
            for button in self._preview_buttons.values():
                button.setParent(card)
                button.hide()
        choices_box.addLayout(direction_grid)
        self._refresh_button = QPushButton("重新读取全部槽位", objectName="refreshPromptDevice")
        self._refresh_button.setProperty("buttonRole", "ghost")
        self._refresh_button.clicked.connect(self._refresh_from_device)
        choices_box.addWidget(self._refresh_button)
        if self._device_preview is None:
            choices_box.addWidget(self._device_operation_guide())
        choices_box.addWidget(self._summary_card(self._device_storage_available, self._protocol_message))

        self._direction_selection = QLabel(objectName="promptDirectionSelection")
        self._direction_selection.setParent(focus)
        self._direction_selection.hide()

        self._sync_state = QLabel(objectName="promptSyncState")
        focus_header.addWidget(self._sync_state)
        self._sync_state.setWordWrap(True)

        self._name = QLineEdit(objectName="promptNameEditor")
        self._name.setMinimumHeight(42)
        self._name.setProperty("pill", True)
        self._name.setPlaceholderText("例如：代码审查")
        self._name_bytes = QLabel(objectName="muted")
        name_heading = QHBoxLayout()
        name_label = QLabel("名称")
        name_label.setBuddy(self._name)
        name_heading.addWidget(name_label)
        name_heading.addStretch(1)
        name_heading.addWidget(self._name_bytes)
        box.addLayout(name_heading)
        box.addWidget(self._name)

        self._body = QPlainTextEdit(objectName="promptBodyEditor")
        self._body.setPlaceholderText(translate_ui_text("输入中文、英文、换行和常用符号；不会自动按 Enter 提交。"))
        self._body.setFixedHeight(200)
        self._body_bytes = QLabel(objectName="muted")
        body_heading = QHBoxLayout()
        body_label = QLabel("正文")
        body_label.setBuddy(self._body)
        body_heading.addWidget(body_label)
        body_heading.addStretch(1)
        body_heading.addWidget(self._body_bytes)
        box.addLayout(body_heading)
        box.addWidget(self._body)

        self._save_draft_button = QPushButton("保存本地草稿", objectName="primary")
        self._save_draft_button.setObjectName("savePromptDraft")
        self._save_draft_button.clicked.connect(self._save)
        self._save_draft_button.setProperty("buttonRole", "primary")
        device_buttons = QGridLayout()
        self._write_device_button = QPushButton("保存到设备", objectName="primary")
        self._write_device_button.setObjectName("writePromptDevice")
        self._write_device_button.clicked.connect(self._write_to_device)
        self._write_device_button.setProperty("buttonRole", "primary")
        device_buttons.addWidget(self._write_device_button, 0, 0, 1, 2)
        device_buttons.addWidget(self._save_draft_button, 0, 0, 1, 2)
        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(8)
        self._discard_button = QPushButton("恢复设备版本", objectName="discardPromptDraft")
        self._discard_button.setProperty("buttonRole", "secondary")
        self._discard_button.clicked.connect(self._discard)
        secondary_actions.addWidget(self._discard_button)
        self._delete_draft_button = QPushButton("删除本地草稿", objectName="deletePromptDraft")
        self._delete_draft_button.setProperty("buttonRole", "ghost")
        self._delete_draft_button.clicked.connect(self._delete)
        secondary_actions.addWidget(self._delete_draft_button)
        self._delete_device_button = QPushButton("从设备删除此提示词", objectName="deletePromptDevice")
        self._delete_device_button.setObjectName("deletePromptDevice")
        self._delete_device_button.clicked.connect(self._delete_from_device)
        self._delete_device_button.setProperty("buttonRole", "ghost")
        secondary_actions.addWidget(self._delete_device_button)
        box.addLayout(device_buttons)
        self._action_hint = QLabel(objectName="promptActionHint")
        self._action_hint.setWordWrap(True)
        box.addWidget(self._action_hint)
        box.addLayout(secondary_actions)
        # Set visibility after parenting: an unparented visible button creates
        # a native top-level window while this page is still being constructed.
        self._write_device_button.setVisible(self._device_storage_available)
        self._save_draft_button.setVisible(not self._device_storage_available)
        helper = self._helper_card()
        self._paste_card = helper.findChild(QWidget, "promptPasteCard")
        left_box.addWidget(self._paste_card)
        left_box.addStretch(1)
        right_box.addWidget(helper.findChild(QWidget, "promptEventsCard"))
        right_box.addStretch(1)
        helper.deleteLater()
        del self._helper_layout

        self._name.textChanged.connect(self._update_byte_counts)
        self._body.textChanged.connect(self._update_byte_counts)
        self._name.textChanged.connect(self._refresh_primary_action)
        self._body.textChanged.connect(self._refresh_primary_action)
        self._load_selected()
        return card

    def _device_operation_guide(self) -> QWidget:
        guide = QWidget(objectName="promptOperationGuide")
        layout = QVBoxLayout(guide)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if not self._supports_prompt_palette:
            note = QLabel(
                translate_ui_text("普通模式下，拨动已绑定的摇杆方向会直接触发对应提示词。"),
                objectName="promptDirectTriggerGuide",
            )
            note.setWordWrap(True)
            layout.addWidget(note)
            return guide
        self._operation_buttons: list[QPushButton] = []
        steps = (
            ("① 长按 12 号键 · 打开", "key.12"),
            ("② 拨动摇杆 · 选择", "joystick.up"),
            ("③ 短按旋钮 · 确认", "encoder.press"),
        )
        for label, control_id in steps:
            button = QPushButton(translate_ui_text(label), objectName="promptOperationStep")
            button.setProperty("guideControl", control_id)
            button.setProperty("buttonRole", "ghost")
            button.setMinimumHeight(38)
            button.setEnabled(self._device_preview is not None)
            button.setToolTip(translate_ui_text("点击在模型上演示；长按约 0.8 秒打开提示词盘。"))
            button.clicked.connect(
                lambda _checked=False, control=control_id: self._preview_operation(control)
            )
            self._operation_buttons.append(button)
            layout.addWidget(button)
        return guide

    def _preview_operation(self, control_id: str) -> None:
        if self._device_preview is None:
            return
        canvas = self._device_preview.findChild(QWidget, "deviceModelCanvas")
        if canvas is None:
            return
        # Only animate the existing model. Demonstrating a step must never
        # activate a prompt, switch its draft, or send a device command.
        selected_control = (
            control_id.split(".")[0]
            if control_id.startswith(("joystick.", "encoder."))
            else control_id
        )
        canvas.set_selected_control(selected_control)
        canvas.preview_control(control_id)

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
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        self._paste_heading = Boring5RLabel("PASTE", scale=.9, color="#A5A195")
        self._paste_heading.hide()
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
        listener_detail.setVisible(
            self._listener_status.state is not PromptListenerState.READY
        )

        recent_label = QLabel("最近结果", objectName="eyebrow")
        self._recent_result_caption = recent_label
        helper_status = QLabel(
            translate_ui_text(self._helper_message), objectName="muted"
        )
        helper_status.setObjectName("promptHelperStatus")
        helper_status.setWordWrap(True)
        has_result = self._helper_message not in {"", "尚无设备触发记录"}
        box.addWidget(recent_label)
        box.addWidget(helper_status)
        recent_label.setVisible(has_result)
        helper_status.setVisible(has_result)

        background = QLabel(
            translate_ui_text(self._background_message), objectName="roleContext"
        )
        background.setObjectName("promptBackgroundStatus")
        background.setWordWrap(True)
        background.hide()
        status_badge.setToolTip(translate_ui_text(self._background_message))
        box.addWidget(background)
        help_actions = QHBoxLayout()
        help_actions.setContentsMargins(0, 0, 0, 0)
        help_actions.setSpacing(4)
        help_actions.addWidget(detail_toggle)
        box.addLayout(help_actions)
        box.addWidget(detail)
        events_toggle = QPushButton("操作记录", objectName="promptEventDetailsToggle")
        events_toggle.setProperty("buttonRole", "ghost")
        events_toggle.setCheckable(True)
        events_toggle.toggled.connect(events.setVisible)
        help_actions.addWidget(events_toggle)

        box = QVBoxLayout(events)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(8)
        self._events_heading = Boring5RLabel("EVENTS", scale=.9, color="#A5A195")
        self._events_heading.hide()
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
        # Prompt history remains available on demand without occupying the
        # ordinary edit flow.
        events.hide()
        return card

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_workspace_layout") and hasattr(self, "_paste_card"):
            direction = (
                QBoxLayout.Direction.TopToBottom
                if self.viewport().width() < 1060
                else QBoxLayout.Direction.LeftToRight
            )
            if self._workspace_layout.direction() != direction:
                self._workspace_layout.setDirection(direction)
                if direction == QBoxLayout.Direction.TopToBottom:
                    self._left_box.removeWidget(self._paste_card)
                    self._right_box.insertWidget(1, self._paste_card)
                    if hasattr(self, "_stage"):
                        self._workspace_layout.removeWidget(self._stage)
                        self._workspace_layout.addWidget(self._stage)
                else:
                    self._right_box.removeWidget(self._paste_card)
                    self._left_box.insertWidget(1, self._paste_card)
                    if hasattr(self, "_stage"):
                        self._workspace_layout.removeWidget(self._stage)
                        self._workspace_layout.insertWidget(1, self._stage)
                    for index, factor in enumerate((4, 6, 7) if hasattr(self, "_stage") else (4, 7)):
                        self._workspace_layout.setStretch(index, factor)
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
        self._refresh_primary_action()
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
        entry = self._library.draft_entry(self._selected_prompt_id()) or self._library.confirmed_entry(self._selected_prompt_id())
        return (self._name.text().strip(), self._body.toPlainText()) != (
            entry.name if entry else "", entry.body if entry else ""
        )

    def confirm_leave(self) -> bool:
        if not self.has_unsaved_fields():
            return True
        choice = confirm_local_draft(
            self, "提示词输入尚未保存",
            "保留草稿后会保存在这台电脑，尚未应用到设备。",
        )
        if choice == QMessageBox.Save:
            return self._save()
        if choice == QMessageBox.Discard:
            self._load_selected()
            return True
        return False

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
        confirmed = self._library.confirmed_entry(prompt_id)
        entry = self._library.draft_entry(prompt_id) or confirmed
        self._direction_selection.setText(
            f"{arrow} {direction_label} · 快捷提示词 {prompt_id}"
        )
        self._name.blockSignals(True)
        self._body.blockSignals(True)
        self._name.setText(entry.name if entry is not None else "")
        self._body.setPlainText(entry.body if entry is not None else "")
        self._name.blockSignals(False)
        self._body.blockSignals(False)
        self._refresh_primary_action()
        self._refresh_direction_cards()
        if self._device_preview is not None:
            canvas = self._device_preview.findChild(QWidget, "deviceModelCanvas")
            if canvas is not None:
                canvas.set_selected_control(None)
                canvas.set_selected_prompt_direction(prompt_id)
        for candidate, button in self._preview_buttons.items():
            button.setProperty("selected", candidate == prompt_id)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_byte_counts()

    def _refresh_direction_cards(self) -> None:
        if self._library is None:
            return
        configured = sum(self._library.confirmed_entry(prompt_id) is not None for prompt_id in QUICK_PROMPT_IDS)
        self._quick_count.setText(translate_ui_text("设备已保存 {v1}/4").format(v1=configured))
        for _control_id, prompt_id, arrow, direction_label in QUICK_PROMPT_DIRECTIONS:
            draft = self._library.draft_entry(prompt_id)
            confirmed = self._library.confirmed_entry(prompt_id)
            entry = draft or confirmed
            status = (
                "仅本地草稿" if confirmed is None and draft is not None
                else "待保存到设备" if draft != confirmed
                else "设备已保存" if confirmed is not None
                else "未设置"
            )
            name = (
                entry.name
                if entry is not None
                else translate_ui_text("未设置提示词")
            )
            button = self._direction_buttons[prompt_id]
            configured = confirmed is not None
            if button.property("configured") != configured:
                button.setProperty("configured", configured)
                button.style().unpolish(button)
                button.style().polish(button)
            slot_label = translate_ui_text(f"提示词槽位 {prompt_id}")
            button.setToolTip(f"{translate_ui_text(direction_label)} · {slot_label}\n{name} · {translate_ui_text(status)}")
            short_name = button.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideRight, max(60, min(120, button.width() - 24))
            )
            button.setText(f"{arrow}  {translate_ui_text(direction_label)}   {short_name}\n      {translate_ui_text(status)}")

    @staticmethod
    def _direction(prompt_id: int) -> tuple[str, int, str, str]:
        for direction in QUICK_PROMPT_DIRECTIONS:
            if direction[1] == prompt_id:
                return direction
        raise ValueError(f"未知快捷提示词 {prompt_id}")

    def _update_byte_counts(self) -> None:
        name_bytes = len(self._name.text().encode("utf-8"))
        body_bytes = len(self._body.toPlainText().encode("utf-8"))
        self._name_bytes.setText(
            translate_ui_text("已用 {v1}/{v2}").format(
                v1=name_bytes, v2=PROMPT_NAME_MAX_BYTES
            )
        )
        self._body_bytes.setText(
            translate_ui_text("已用 {v1}/{v2}").format(
                v1=body_bytes, v2=PROMPT_BODY_MAX_BYTES
            )
        )
        self._name_bytes.setStyleSheet(
            "color: #8b2f24;" if name_bytes > PROMPT_NAME_MAX_BYTES else ""
        )
        self._body_bytes.setStyleSheet(
            "color: #8b2f24;" if body_bytes > PROMPT_BODY_MAX_BYTES else ""
        )

    def _refresh_primary_action(self) -> None:
        if self._library is None or not hasattr(self, "_name"):
            return
        prompt_id = self._selected_prompt_id()
        draft = self._library.draft_entry(prompt_id)
        confirmed = self._library.confirmed_entry(prompt_id)
        current = (self._name.text().strip(), self._body.toPlainText())
        saved_value = (draft.name, draft.body) if draft is not None else (
            (confirmed.name, confirmed.body) if confirmed is not None else ("", "")
        )
        confirmed_value = (
            (confirmed.name, confirmed.body) if confirmed is not None else ("", "")
        )
        name_bytes = len(current[0].encode("utf-8"))
        body_bytes = len(current[1].encode("utf-8"))
        valid_shape = (
            bool(current[0])
            and bool(current[1])
            and name_bytes <= PROMPT_NAME_MAX_BYTES
            and body_bytes <= PROMPT_BODY_MAX_BYTES
        )
        changed_in_editor = current != saved_value
        changed_on_device = current != confirmed_value
        if changed_in_editor:
            state = "正在编辑，尚未保存"
        elif confirmed is not None and draft is None:
            state = "本地草稿已移除，设备内容仍在"
        elif draft != confirmed:
            state = "仅本地草稿，设备仍使用旧版本" if confirmed is not None else "仅本地草稿，尚未写入设备"
        elif confirmed is not None:
            state = "设备已保存"
        else:
            state = "未设置"
        set_translatable_text(self._sync_state, state)
        if not current[0] or not current[1]:
            hint = "填写名称和正文后保存到设备。"
        elif not self._device_storage_available:
            hint = "设备未连接；草稿只保存在这台电脑，连接后再保存到设备。"
        elif changed_on_device or draft != confirmed:
            hint = "保存后实体键才会使用新内容；当前修改不会自动生效。"
        else:
            hint = "实体键使用设备中的版本；粘贴由控制台完成。"
        set_translatable_text(self._action_hint, hint)
        if not changed_on_device and confirmed is not None:
            label = "已保存到设备"
        elif not changed_in_editor and draft is not None:
            label = "将草稿保存到设备"
        else:
            label = "保存更改到设备" if confirmed is not None else "保存到设备"
        set_translatable_text(self._write_device_button, label)
        set_translatable_text(
            self._save_draft_button,
            "草稿已保存" if not changed_in_editor and draft is not None else "保存本地草稿",
        )
        self._write_device_button.setEnabled(
            self._device_storage_available
            and not self._device_busy
            and valid_shape
            and changed_on_device
        )
        self._save_draft_button.setEnabled(not self._device_busy and valid_shape and changed_in_editor)
        self._refresh_button.setEnabled(self._device_storage_available and not self._device_busy)
        self._discard_button.setVisible(confirmed is not None and (changed_on_device or draft != confirmed))
        self._discard_button.setEnabled(not self._device_busy)
        self._delete_draft_button.setVisible(confirmed is None and draft is not None)
        self._delete_draft_button.setEnabled(not self._device_busy)
        self._delete_device_button.setVisible(confirmed is not None)
        self._delete_device_button.setEnabled(self._device_storage_available and not self._device_busy)

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
        if self.has_unsaved_fields() and QMessageBox.question(
            self,
            translate_ui_text("恢复设备版本"),
            translate_ui_text("尚未保存的输入会丢失。确定恢复设备中的版本吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._discard_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法丢弃提示词修改", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _delete(self) -> None:
        if QMessageBox.question(
            self,
            translate_ui_text("删除本地草稿"),
            translate_ui_text("将删除这台电脑上此槽位的草稿和未保存输入；设备内容不受影响。是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._delete_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法删除提示词草稿", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _refresh_from_device(self) -> None:
        if self.has_unsaved_fields() and QMessageBox.question(
            self,
            translate_ui_text("重新读取全部槽位"),
            translate_ui_text("尚未保存的输入会丢失。本地已保存的草稿会保留，是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._refresh_device()
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法读取设备提示词", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _write_to_device(self) -> None:
        prompt_id = self._selected_prompt_id()
        try:
            if self._save_and_write_device is not None:
                self._save_and_write_device(
                    prompt_id,
                    self._name.text(),
                    self._body.toPlainText(),
                )
            else:
                if not self._save():
                    return
                self._write_device(prompt_id)
        except (PromptLibraryError, OSError) as exc:
            QMessageBox.warning(self, "无法写入设备提示词", str(exc))

    def _delete_from_device(self) -> None:
        prompt_id = self._selected_prompt_id()
        choice = QMessageBox.warning(
            self,
            translate_ui_text("确认删除设备提示词"),
            translate_ui_text("将从设备删除当前方向的提示词，并删除这台电脑上该方向的草稿。该方向在重新配置前不会执行提示词。是否继续？"),
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
