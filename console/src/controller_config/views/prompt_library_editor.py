from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from controller_config.views.ui import notice
from controller_config import desktop_permissions
from controller_config.prompt_library import (
    PROMPT_BODY_MAX_BYTES,
    PROMPT_NAME_MAX_BYTES,
    QUICK_PROMPT_DIRECTIONS,
    QUICK_PROMPT_IDS,
    PromptEntry,
    PromptLibrary,
    PromptLibraryError,
)
from controller_config.i18n import (
    ENGLISH,
    normalize_language,
    set_translatable_accessible_name,
    set_translatable_text,
    translate_ui_text,
)
from controller_config.prompt_device import (
    PromptEventLogEntry,
    PromptListenerStatus,
)
from controller_config.style import tokens
from controller_config.style.sheets import PROMPT_PAGE_STYLE
from controller_config.views.v4_widgets import V4Card
from controller_config.views.draft_dialog import confirm_local_draft
from controller_config.views.settings_wall import (
    COLUMNS as WALL_COLUMNS,
    GAP as WALL_GAP,
    WALL_STYLE,
    SettingsWall,
    TileIcon,
    WallTile,
    pill_button,
    title_label,
)
from controller_config.prompt_status_state import PromptStatusInputs
from controller_config.views.ui import (
    SMALL,
    DeviceTopView,
    Dialog,
    DirectionCross,
    DirectionGlyph,
    Menu,
    PromptDiagnostics,
    PromptStatusBar,
    StepNumber,
    confirm,
    direction_of,
    prompt_diagnostics_dialog,
)
from controller_config.views.ui.permission_row import PermissionWatcher
from controller_config.views.ui.prompt_status_bar import BLUETOOTH_TEXT
from controller_config.views.ui.status_icon import StatusIcon


# -- How much more fits (评审“提示词 7”) -----------------------------------------
#
# The device limits are UTF-8 bytes (48 for a name, 4096 for a body). People
# count characters, so the page shows characters and keeps checking bytes:
# a Chinese or Japanese character takes 3 bytes, an English letter 1. "Room
# left" is an estimate in the characters the interface language usually
# writes, so it reads "about N".

BODY_NEAR_FULL_BYTES = PROMPT_BODY_MAX_BYTES // 10  # show the body count only this close to the limit

# -- Page layout (the designer Prompt 页定稿 v1) -----------------------------------------

TOP_BAR_HEIGHT = 44   # the line above the wall
CROSS_GAP = 12        # between the cross's cells
DEMO_STEP_MS = 2000   # 看演示: how long each step stays lit (09 单第 2 件, the designer 按推荐定 2.0 秒)
DIRECTION_TITLES = {1: "上方向", 2: "右方向", 3: "下方向", 4: "左方向"}
DIRECTION_SHORT = {1: "上", 2: "右", 3: "下", 4: "左"}
CROSS_STATES = {"ok": "saved", "wait": "dirty", "off": "empty"}  # slot_status icon -> DirectionCross state
# 从示例开始 / 用示例填入 (the designer: 代码审查、翻译成英文、总结要点): chip, name, body.
PROMPT_EXAMPLE_CHIPS = (
    ("代码审查", "代码审查",
     "请审查这段代码：找出潜在的 bug、边界条件和可读性问题，按严重程度从高到低列出，每条给出修改建议。"),
    # The button's own source text: in English it is shorter than the prompt's name, so the
    # three buttons fit their row at their full width (design decision 第 3 件; zh unchanged).
    ("翻译成英文（示例按钮）", "翻译成英文",
     "请把下面的内容翻译成自然、地道的英文，保持原意和语气，不要逐字直译："),
    ("总结要点", "总结要点并列出下一步要做的事",
     "请把下面的内容总结成 3～5 条要点，每条一句话，先说结论；最后单独列出下一步要做的事，写清谁来做。"),
)


def ask_switch_direction(parent: QWidget, name: str, *, device_available: bool) -> QMessageBox.StandardButton:
    """Save, Discard or Cancel before moving to another direction (定稿 v1 第 4 张)."""
    dialog = Dialog(parent, translate_ui_text("「{v1}」还没存进设备").format(v1=name),
                    width=SMALL, object_name="promptSwitchDialog")
    dialog.add_text("要先保存，还是放弃刚才的修改？", secondary=True)
    discard = dialog.add_button("放弃修改", "secondary", object_name="promptSwitchDiscard")
    save = dialog.add_button("保存到设备" if device_available else "先存在这台电脑",
                             "primary", object_name="promptSwitchSave")
    choice = [QMessageBox.StandardButton.Cancel]
    discard.clicked.connect(lambda: (choice.__setitem__(0, QMessageBox.StandardButton.Discard), dialog.accept()))
    save.clicked.connect(lambda: (choice.__setitem__(0, QMessageBox.StandardButton.Save), dialog.accept()))
    dialog.exec()
    return choice[0]


def _label(text: str, object_name: str) -> QLabel:
    label = QLabel(objectName=object_name)
    set_translatable_text(label, text)
    return label


@dataclass(frozen=True)
class TextAllowance:
    used: int   # characters typed
    limit: int  # about how many characters fit: typed + room left
    left: int   # about how many more characters fit
    over: int   # about how many characters too many; 0 when it fits
    near_full: bool


def estimated_bytes_per_character(language: str | None = None) -> int:
    if language is None:
        application = QApplication.instance()
        language = normalize_language(application.property("boringUiLanguage")) if application else None
    return 1 if language == ENGLISH else 3


def text_allowance(text: str, max_bytes: int, *, bytes_per_character: int = 3,
                   near_full_bytes: int = 0) -> TextAllowance:
    room = max_bytes - len(text.encode("utf-8"))
    if room >= 0:
        left = room // bytes_per_character
        return TextAllowance(len(text), len(text) + left, left, 0, room <= near_full_bytes)
    over = -(room // bytes_per_character)  # round up: one byte too many is one character too many
    return TextAllowance(len(text), len(text) - over, 0, over, True)


# -- Save status (评审“提示词 2”，the designer Prompt 页定稿 v1 编辑区底栏) ------------
#
# One sentence at the bottom left of the editor and one button that always
# says "保存到设备" (offline: "先存在这台电脑"). Only how the status is worked
# out and shown lives here; saving itself is unchanged.

SAVED = "已存进设备"
CHANGED = "有修改，还没存进设备"
NOT_SET = "还没设置"


@dataclass(frozen=True)
class SaveStatus:
    text: str              # Chinese source text, translated when shown
    icon: str | None       # StatusIcon state; None shows no icon
    can_write_device: bool
    can_save_local: bool


def _entry_value(entry: PromptEntry | None) -> tuple[str, str]:
    return (entry.name, entry.body) if entry is not None else ("", "")


def save_status(name: str, body: str, draft: PromptEntry | None, confirmed: PromptEntry | None,
                *, device_available: bool) -> SaveStatus:
    current = (name.strip(), body)
    changed_in_editor = current != _entry_value(draft or confirmed)
    changed_on_device = current != _entry_value(confirmed)
    if not current[0] or not current[1]:
        return SaveStatus("填好名称和正文就能保存", None, False, False)
    if len(current[0].encode("utf-8")) > PROMPT_NAME_MAX_BYTES:
        return SaveStatus("名称太长，改短后才能保存", "wait", False, False)
    if len(current[1].encode("utf-8")) > PROMPT_BODY_MAX_BYTES:
        return SaveStatus("正文太长，改短后才能保存", "wait", False, False)
    if not device_available:
        return SaveStatus("设备没连上", None, False, changed_in_editor)
    if changed_on_device:
        return SaveStatus(CHANGED, "wait", True, changed_in_editor)
    return SaveStatus(SAVED, "ok", False, False)


def slot_status(draft: PromptEntry | None, confirmed: PromptEntry | None) -> tuple[str, str]:
    """The same words on a direction card: (text, StatusIcon state)."""
    if draft is None and confirmed is None:
        return NOT_SET, "off"
    if draft is not None and draft != confirmed:
        return CHANGED, "wait"
    return SAVED, "ok"


# -- Permission to type prompts (评审“提示词 4”) ----------------------------------
#
# Pasting a prompt posts Command+V, which macOS allows only once BORING
# Console has Accessibility. This is the page's only question about system
# permissions; when the shared "permission + 去允许 + recheck" widget lands
# (settings-logic session), swap this function and PermissionNotice for it.

def paste_permission_missing(permissions: desktop_permissions.SystemPermissions | None) -> bool:
    if permissions is None or not permissions.is_mac:
        return False
    return permissions.accessibility() == desktop_permissions.NOT_TRUSTED


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
        hardware_id: str | None = None,
        over_bluetooth: bool = False,
        permissions: desktop_permissions.SystemPermissions | None = None,
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
        self.setStyleSheet("QScrollArea { background: transparent; }" + WALL_STYLE + PROMPT_PAGE_STYLE)

        page = QWidget(objectName="promptLibraryPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # The top line (the designer 定稿 v1): the page's state, permission first.
        watcher = (
            PermissionWatcher(permissions, self, kinds=(desktop_permissions.ACCESSIBILITY,))
            if permissions is not None else None
        )
        self._top_bar = PromptStatusBar(watcher)
        self._refresh_button = self._top_bar.refresh_button
        self._top_bar.refresh_requested.connect(self._refresh_from_device)
        self._top_bar.demo_requested.connect(lambda: self.show_demo(True))
        self._top_bar.diagnostics_requested.connect(self._show_diagnostics)
        self._top_bar.explain_requested.connect(self._explain_bluetooth)
        self._over_bluetooth = over_bluetooth
        self._wall = SettingsWall("prompts", header=self._top_bar, header_height=TOP_BAR_HEIGHT)
        self._wall.setObjectName("promptWall")
        self._cross_tile = WallTile(2, 2)
        self._cross_tile.setObjectName("promptChoicesCard")
        self._editor_tile = WallTile(2, 2)
        self._editor_tile.setObjectName("promptFocusCard")
        self._wall.place(self._cross_tile, 0, 0)
        self._wall.place(self._editor_tile, 0, 2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(self._wall)
        row.addStretch(1)
        layout.addStretch(2)
        layout.addLayout(row)
        layout.addStretch(3)
        # Engineering detail (helper, events, paste help) is in the diagnostics dialog.
        self._diagnostics = PromptDiagnostics()
        self._diagnostics.setParent(self)
        self._diagnostics.hide()
        self._apply_runtime()
        if library is None:
            self._cross_tile.box.addWidget(self._no_device_card())
            self._editor_tile.hide()
        else:
            self._build_cross()
            self._build_editor()
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
        self._apply_runtime()
        if self._library is not None:
            self._write_device_button.setVisible(available)
            self._save_draft_button.setVisible(not available)
            self._refresh_primary_action()

    def _apply_runtime(self) -> None:
        """The top line and the diagnostics follow the helper, storage and connection."""
        self._top_bar.apply(PromptStatusInputs(
            connected=self._library is not None,
            over_bluetooth=self._over_bluetooth,
            palette=self._supports_prompt_palette,
            storage_available=self._device_storage_available,
            listener=self._listener_status.state,
            permission_missing=bool(self._top_bar.permission_missing()),
        ))
        self._diagnostics.update_runtime(
            available=self._device_storage_available, busy=self._device_busy,
            message=self._protocol_message, helper_message=self._helper_message,
            listener=self._listener_status, event_log=self._event_log,
            background_message=self._background_message,
        )

    def _show_diagnostics(self) -> None:
        dialog = prompt_diagnostics_dialog(self, self._diagnostics)
        # Take the body back when the dialog closes, so update_runtime keeps it current.
        dialog.finished.connect(lambda _result: (self._diagnostics.setParent(self), self._diagnostics.hide()))
        self._diagnostics.show()
        dialog.open()

    def _explain_bluetooth(self) -> None:
        dialog = Dialog(self, "现在用蓝牙连接", width=SMALL, object_name="promptBluetoothDialog")
        dialog.add_text(BLUETOOTH_TEXT, secondary=True)
        dialog.open()

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

    # -- left: the four directions, or 怎么用 ---------------------------------------

    def _build_cross(self) -> None:
        box = self._cross_tile.box
        self._cross_page = QWidget(objectName="promptCrossPage")
        page = QVBoxLayout(self._cross_page)
        page.setContentsMargins(0, 0, 0, 0)
        self._cross = DirectionCross()
        self._cross.setObjectName("promptDirectionCross")
        self._cross.direction_selected.connect(self._select_direction)
        page.addWidget(self._cross, 1)
        box.addWidget(self._cross_page, 1)
        self._demo_page = self._demo_panel()
        self._demo_page.hide()
        box.addWidget(self._demo_page, 1)

    def _demo_panel(self) -> QWidget:
        """看演示: the device and the three steps take the cross's place (定稿 v1 第 4 张)."""
        panel = QWidget(objectName="promptOperationGuide")
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        back = pill_button("回到四个方向", "promptDemoBack")
        back.clicked.connect(lambda: self.show_demo(False))
        header = QHBoxLayout()
        header.setSpacing(9)
        header.addWidget(TileIcon("help"))
        header.addWidget(title_label("怎么用"))
        header.addStretch(1)
        header.addWidget(back)
        box.addLayout(header)
        self._demo_device = DeviceTopView(mode="marker")
        self._demo_device.setObjectName("promptDemoDevice")
        self._demo_device.set_selected("key.12")
        box.addWidget(self._demo_device, 1)
        self._operation_buttons: list[QPushButton] = []
        self._step_marks: list[StepNumber] = []
        if not self._supports_prompt_palette:
            note = QLabel(objectName="promptDirectTriggerGuide")
            set_translatable_text(note, "在 Mode 里把摇杆方向绑到提示词，拨一下就打出来")
            note.setWordWrap(True)
            box.addWidget(note)
            return panel
        steps = (
            ("长按 12 号键约 0.8 秒，打开提示词盘", "key.12"),
            ("拨摇杆选一个方向", "joystick.up"),
            ("短按旋钮确认，提示词打进电脑", "encoder.press"),
        )
        for number, (text, control_id) in enumerate(steps, start=1):
            mark = StepNumber(number)
            mark.setObjectName("promptStepNumber")
            self._step_marks.append(mark)
            button = QPushButton(objectName="promptOperationStep")
            set_translatable_text(button, text)
            button.setProperty("guideControl", control_id)
            button.setProperty("wallRole", "link")
            button.clicked.connect(lambda _checked=False, index=number - 1: self._show_step(index))
            self._operation_buttons.append(button)
            step = QHBoxLayout()
            step.setSpacing(8)
            step.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
            step.addWidget(button)
            step.addStretch(1)
            box.addLayout(step)
        note = QLabel(objectName="promptPaletteGuide")
        set_translatable_text(note, "3 号键取消；10 秒不操作自动退出。短按 12 号键还是原来的功能。提示词盘打开时，其它按键不会发到电脑。")
        note.setWordWrap(True)
        box.addWidget(note)
        self._demo_timer = QTimer(panel)
        self._demo_timer.setInterval(DEMO_STEP_MS)
        self._demo_timer.timeout.connect(lambda: self._show_step((self._demo_step + 1) % len(steps)))
        self._demo_step = 0
        return panel

    def show_demo(self, shown: bool) -> None:
        """Swap the four directions for 怎么用, or back."""
        if self._library is None:
            return
        self._cross_page.setVisible(not shown)
        self._demo_page.setVisible(shown)
        if not self._supports_prompt_palette:
            return
        if shown:
            self._show_step(0)
            self._demo_timer.start()
        else:
            self._demo_timer.stop()

    def _show_step(self, index: int) -> None:
        """Light one step: its circle turns orange and the picture marks the part (design decision)."""
        self._demo_step = index
        for position, button in enumerate(self._operation_buttons):
            button.setProperty("current", position == index)
            button.style().unpolish(button)
            button.style().polish(button)
            self._step_marks[position].set_current(position == index)
        control_id = self._operation_buttons[index].property("guideControl")
        self._demo_device.set_selected(control_id if control_id.startswith("key.") else None)
        self._demo_device.set_part_mark(None if control_id.startswith("key.") else control_id)

    # -- right: the editor --------------------------------------------------------

    def _build_editor(self) -> None:
        assert self._library is not None
        box = self._editor_tile.box
        box.setSpacing(6)
        self._direction_icon = DirectionGlyph()
        self._direction_icon.setObjectName("promptDirectionIcon")
        self._slot_title = title_label("上方向")
        self._more_button = QPushButton("···", objectName="promptMoreMenu")
        self._more_button.setProperty("wallRole", "plain")
        set_translatable_accessible_name(self._more_button, "更多")
        self._more_menu = Menu(self._more_button)
        self._discard_action = self._more_menu.addAction("恢复成设备里的版本")
        self._discard_action.setObjectName("discardPromptDraft")
        self._discard_action.triggered.connect(self._discard)
        self._example_menu = self._more_menu.addMenu("用示例填入")
        for short, name, body in PROMPT_EXAMPLE_CHIPS:
            action = self._example_menu.addAction(translate_ui_text(short))
            action.triggered.connect(lambda _checked=False, n=name, b=body: self._use_example(n, b))
        self._delete_draft_action = self._more_menu.addAction("放弃这次修改")
        self._delete_draft_action.setObjectName("deletePromptDraft")
        self._delete_draft_action.triggered.connect(self._delete)
        self._delete_device_action = self._more_menu.add_danger_action("从设备删除")
        self._delete_device_action.setObjectName("deletePromptDevice")
        self._delete_device_action.triggered.connect(self._delete_from_device)
        self._more_button.clicked.connect(lambda: self._more_menu.popup_under(self._more_button))
        header = QHBoxLayout()
        header.setSpacing(9)
        header.addWidget(self._direction_icon)
        header.addWidget(self._slot_title)
        header.addStretch(1)
        header.addWidget(self._more_button)
        box.addLayout(header)

        # 从示例开始: only while this direction is empty (the designer).
        self._examples = QWidget(objectName="promptExamples")
        examples = QHBoxLayout(self._examples)
        examples.setContentsMargins(0, 0, 0, 0)
        examples.setSpacing(6)
        examples.addWidget(_label("从示例开始", "wallTileNote"))
        for short, name, body in PROMPT_EXAMPLE_CHIPS:
            chip = pill_button(short, "promptExample")
            # As wide as its words, never pressed narrower (design decision 第 3 件).
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, chip.sizePolicy().verticalPolicy())
            chip.clicked.connect(lambda _checked=False, n=name, b=body: self._use_example(n, b))
            examples.addWidget(chip)
        examples.addStretch(1)
        box.addWidget(self._examples)

        self._name = QLineEdit(objectName="promptNameEditor")
        self._name.setProperty("pill", True)
        self._name.setPlaceholderText(translate_ui_text("例如：代码审查"))
        self._name_count = QLabel(objectName="muted")
        name_label = _label("名称", "lightingRowValue")
        name_label.setBuddy(self._name)
        name_heading = QHBoxLayout()
        name_heading.addWidget(name_label)
        name_heading.addStretch(1)
        name_heading.addWidget(self._name_count)
        box.addLayout(name_heading)
        box.addWidget(self._name)

        self._body = QPlainTextEdit(objectName="promptBodyEditor")
        self._body.setPlaceholderText(translate_ui_text("输入要发给 AI 的内容（不会自动按回车）"))
        self._body_count = QLabel(objectName="muted")
        body_label = _label("正文", "lightingRowValue")
        body_label.setBuddy(self._body)
        body_heading = QHBoxLayout()
        body_heading.addWidget(body_label)
        body_heading.addStretch(1)
        body_heading.addWidget(self._body_count)
        box.addLayout(body_heading)
        box.addWidget(self._body, 1)

        # One sentence and one button (评审“提示词 2”).
        save_bar = QHBoxLayout()
        save_bar.setSpacing(8)
        self._save_icon = StatusIcon("off")
        save_bar.addWidget(self._save_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sync_state = QLabel(objectName="promptSyncState")
        self._sync_state.setWordWrap(True)
        save_bar.addWidget(self._sync_state, 1)
        self._write_device_button = QPushButton(objectName="writePromptDevice")
        set_translatable_text(self._write_device_button, "保存到设备")
        self._write_device_button.setProperty("wallRole", "primary")
        self._write_device_button.clicked.connect(self._write_to_device)
        save_bar.addWidget(self._write_device_button)
        self._save_draft_button = QPushButton(objectName="savePromptDraft")
        set_translatable_text(self._save_draft_button, "先存在这台电脑")
        self._save_draft_button.setProperty("wallRole", "cream")
        self._save_draft_button.clicked.connect(self._save)
        save_bar.addWidget(self._save_draft_button)
        box.addLayout(save_bar)
        self._write_device_button.setVisible(self._device_storage_available)
        self._save_draft_button.setVisible(not self._device_storage_available)

        self._name.textChanged.connect(self._update_counts)
        self._body.textChanged.connect(self._update_counts)
        self._name.textChanged.connect(self._refresh_primary_action)
        self._body.textChanged.connect(self._refresh_primary_action)
        self._load_selected()

    def _use_example(self, name: str, body: str) -> None:
        """Fill both fields from an example; nothing is saved until the user saves."""
        self._name.setText(translate_ui_text(name))
        self._body.setPlainText(translate_ui_text(body))

    def resizeEvent(self, event) -> None:  # noqa: N802
        """The wall keeps its cells square and shrinks with the window (墙一格 150～200)."""
        super().resizeEvent(event)
        available = self.viewport().width()
        self._wall.set_unit((available - (WALL_COLUMNS - 1) * WALL_GAP) // WALL_COLUMNS)


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
                if focus_widget is getattr(self, "_cross", None)
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
        self._update_counts()
        if state.focused_field == "name":
            self._name.setFocus()
        elif state.focused_field == "body":
            self._body.setFocus()
        elif state.focused_field == "direction":
            self._cross.setFocus()

    def _select_direction(self, prompt_id: int) -> None:
        if prompt_id not in QUICK_PROMPT_IDS or prompt_id == self._selected_prompt_id_value:
            return
        if self.has_unsaved_fields() and not self._ask_before_switching():
            return
        self._load_direction(prompt_id)
        self.direction_selected.emit(prompt_id)

    def _ask_before_switching(self) -> bool:
        """「X」还没存进设备: save it, or drop the edits (定稿 v1 第 4 张). False stays put."""
        name = self._name.text().strip() or translate_ui_text(DIRECTION_TITLES[self._selected_prompt_id()])
        choice = ask_switch_direction(self, name, device_available=self._device_storage_available)
        if choice == QMessageBox.StandardButton.Discard:
            return True
        if choice != QMessageBox.StandardButton.Save or not self._save():
            return False
        if self._device_storage_available:
            self._write_device(self._selected_prompt_id())
        return True

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
        self._load_selected()

    def _load_selected(self) -> None:
        if self._library is None:
            return
        prompt_id = self._selected_prompt_id()
        self._direction_icon.set_direction(direction_of(prompt_id))
        set_translatable_text(self._slot_title, DIRECTION_TITLES[prompt_id])
        entry = self._library.draft_entry(prompt_id) or self._library.confirmed_entry(prompt_id)
        self._name.blockSignals(True)
        self._body.blockSignals(True)
        self._name.setText(entry.name if entry is not None else "")
        self._body.setPlainText(entry.body if entry is not None else "")
        self._name.blockSignals(False)
        self._body.blockSignals(False)
        self._refresh_primary_action()
        self._refresh_direction_cards()
        self._update_counts()

    def _refresh_direction_cards(self) -> None:
        """Each direction's name, the start of its body and its state, on the cross."""
        if self._library is None:
            return
        for prompt_id in QUICK_PROMPT_IDS:
            draft = self._library.draft_entry(prompt_id)
            confirmed = self._library.confirmed_entry(prompt_id)
            entry = draft or confirmed
            _status, icon = slot_status(draft, confirmed)
            self._cross.set_direction(
                prompt_id,
                entry.name if entry is not None else "",
                entry.body if entry is not None else "",
                CROSS_STATES[icon],
            )
        self._cross.set_selected(self._selected_prompt_id())

    @staticmethod
    def _direction(prompt_id: int) -> tuple[str, int, str, str]:
        for direction in QUICK_PROMPT_DIRECTIONS:
            if direction[1] == prompt_id:
                return direction
        raise ValueError(f"未知快捷提示词 {prompt_id}")

    def _update_counts(self) -> None:
        per_character = estimated_bytes_per_character()
        name = text_allowance(self._name.text(), PROMPT_NAME_MAX_BYTES,
                              bytes_per_character=per_character)
        body = text_allowance(self._body.toPlainText(), PROMPT_BODY_MAX_BYTES,
                              bytes_per_character=per_character,
                              near_full_bytes=BODY_NEAR_FULL_BYTES)
        self._name_count.setText(
            translate_ui_text("超出 {v1} 个字").format(v1=name.over) if name.over
            else translate_ui_text("{v1} / {v2} 字").format(v1=name.used, v2=name.limit)
        )
        # The body count stays out of the way until the body is nearly full.
        self._body_count.setText(
            translate_ui_text("超出 {v1} 个字").format(v1=body.over) if body.over
            else translate_ui_text("还能写约 {v1} 个字").format(v1=body.left) if body.near_full
            else ""
        )
        for label, allowance in ((self._name_count, name), (self._body_count, body)):
            over = bool(allowance.over)
            if label.property("countOver") != over:
                label.setProperty("countOver", over)
                label.setStyleSheet(f"color: {tokens.SIGNAL};" if over else "")

    def _refresh_primary_action(self) -> None:
        if self._library is None or not hasattr(self, "_name"):
            return
        prompt_id = self._selected_prompt_id()
        draft = self._library.draft_entry(prompt_id)
        confirmed = self._library.confirmed_entry(prompt_id)
        status = save_status(
            self._name.text(), self._body.toPlainText(), draft, confirmed,
            device_available=self._device_storage_available,
        )
        set_translatable_text(self._sync_state, status.text)
        self._save_icon.setVisible(status.icon is not None)
        if status.icon is not None:
            self._save_icon.set_state(status.icon)
        self._write_device_button.setEnabled(status.can_write_device and not self._device_busy)
        self._save_draft_button.setEnabled(status.can_save_local and not self._device_busy)
        changed_on_device = (self._name.text().strip(), self._body.toPlainText()) != _entry_value(confirmed)
        self._refresh_button.setEnabled(self._device_storage_available and not self._device_busy)
        self._discard_action.setVisible(confirmed is not None and (changed_on_device or draft != confirmed))
        self._discard_action.setEnabled(not self._device_busy)
        self._delete_draft_action.setVisible(confirmed is None and draft is not None)
        self._delete_draft_action.setEnabled(not self._device_busy)
        self._delete_device_action.setVisible(confirmed is not None)
        self._delete_device_action.setEnabled(self._device_storage_available and not self._device_busy)
        self._example_menu.menuAction().setEnabled(not self._device_busy)
        # 从示例开始 only while this direction has nothing in it yet.
        self._examples.setVisible(draft is None and confirmed is None
                                  and not self._name.text() and not self._body.toPlainText())

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
            notice.notify(self, "无法保存提示词草稿", str(exc))
            return False
        finally:
            self._preserve_fields_on_render = True

    def _discard(self) -> None:
        if self.has_unsaved_fields() and notice.ask(
            self,
            translate_ui_text("恢复设备版本"),
            translate_ui_text("尚未保存的输入会丢失。确定恢复设备中的版本吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
            danger=True,
            labels={QMessageBox.StandardButton.Yes: "恢复设备版本"},
        ) != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._discard_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            notice.notify(self, "无法丢弃提示词修改", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _delete(self) -> None:
        """放弃这次修改: the draft only lives on this computer; the device is not touched."""
        if not confirm(
            self, "放弃这次修改",
            "这台电脑上还没存进设备的修改会丢掉，设备里的内容不变。确定放弃吗？",
            "放弃修改", danger=True, object_name="promptDiscardConfirm",
        ):
            return
        self._preserve_fields_on_render = False
        try:
            self._delete_draft(self._selected_prompt_id())
        except (PromptLibraryError, OSError) as exc:
            notice.notify(self, "无法删除提示词草稿", str(exc))
        finally:
            self._preserve_fields_on_render = True

    def _refresh_from_device(self) -> None:
        if self.has_unsaved_fields() and notice.ask(
            self,
            translate_ui_text("重新读取全部槽位"),
            translate_ui_text("尚未保存的输入会丢失。本地已保存的草稿会保留，是否继续？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
            danger=True,
            labels={QMessageBox.StandardButton.Yes: "重新读取"},
        ) != QMessageBox.StandardButton.Yes:
            return
        self._preserve_fields_on_render = False
        try:
            self._refresh_device()
        except (PromptLibraryError, OSError) as exc:
            notice.alert(self, "无法读取设备提示词", str(exc))
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
            notice.alert(self, "无法写入设备提示词", str(exc))

    def _delete_from_device(self) -> None:
        prompt_id = self._selected_prompt_id()
        direction = translate_ui_text(DIRECTION_SHORT[prompt_id])
        if not confirm(
            self, "从设备删除这一条",
            translate_ui_text("删除后，摇杆往{v1}不会再打出提示词，直到重新设置。是否继续？").format(v1=direction),
            "删除", danger=True, object_name="promptDeleteConfirm",
        ):
            return
        self._preserve_fields_on_render = False
        try:
            self._delete_device(prompt_id)
        except (PromptLibraryError, OSError) as exc:
            notice.alert(self, "无法删除设备提示词", str(exc))
        finally:
            self._preserve_fields_on_render = True
