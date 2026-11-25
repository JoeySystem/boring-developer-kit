"""连续按键页 (route ``sequences``): edit the key sequences saved on the device."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from controller_config.views.ui import notice
from controller_config.actions import action_field_choices
from controller_config.i18n import LanguageManager, SKIP_TRANSLATION_PROPERTY
from controller_config.models import DeviceSnapshot, ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.macro_editor import MacroEditor
from controller_config.views.name_validator import NameLengthValidator
from controller_config.views.pages.common import card, digital_caption


class SequencesPage:
    """One sequence at a time: pick, create, rename, edit steps, save to the draft.

    The page owns which sequence is selected and hands its unsaved edits out
    through editing_state(); build(model, editing=...) puts them back (page
    rebuilds and the restore after a desktop update).

    The route keeps the main window's per-route input cache instead of
    Page.update(): without a draft the route shows the Mode or state page, so
    whether to rebuild cannot be decided from this page's own widget.
    """

    route = "sequences"

    def __init__(
        self,
        view_model: MainViewModel,
        language_manager: LanguageManager,
        *,
        dialog_parent: QWidget,
        rerender: Callable[[], None],
        write_transaction_card: Callable[[DeviceSnapshot, bool], QWidget],
        state_page: Callable[[ScreenModel], QWidget],
    ) -> None:
        self._view_model = view_model
        self._language_manager = language_manager
        self._dialog_parent = dialog_parent
        self._rerender = rerender
        self._write_transaction_card = write_transaction_card
        self._state_page = state_page
        self.selected_macro_id: int | None = None
        self._scroll: QScrollArea | None = None
        self._draft_identity: int | None = None
        self._built_macro_id: int | None = None
        self._name: QLineEdit | None = None
        self._editor: MacroEditor | None = None

    def render_inputs(self, model: ScreenModel) -> tuple:
        """What the page shows; the same inputs mean the live page needs no rebuild."""
        draft = self._view_model.draft
        snapshot = model.snapshot
        return (self._language_manager.language, id(draft), draft.config if draft else None,
                self.selected_macro_id, self._view_model.write_transaction, model.state,
                snapshot.config_result.get("generation") if snapshot else None)

    def editing_state(self) -> tuple[str, list] | None:
        """Name and steps typed into the open sequence, or None when nothing is open.

        Only edits made on the current draft and the selected sequence count.
        """
        draft = self._view_model.draft
        if (self._scroll is None or not isValid(self._scroll) or draft is None
                or self._draft_identity != id(draft)
                or self._built_macro_id != self.selected_macro_id
                or self._name is None or self._editor is None):
            return None
        return (self._name.text(), self._editor.steps())

    def build(self, model: ScreenModel, *, editing: tuple[str, list] | None = None) -> QWidget:
        """Build the page; ``editing`` puts back edits from editing_state()."""
        snapshot = model.snapshot
        editing_values = editing
        draft = self._view_model.draft
        if draft is None:
            return self._state_page(self._view_model.model)
        scroll = QScrollArea()
        scroll.setObjectName("deviceKeySequencesPage")
        scroll.setProperty("draftIdentity", id(draft))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        summary = card()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(22, 18, 22, 18)
        title = QVBoxLayout()
        title.addWidget(digital_caption("DEVICE KEY SEQUENCES"))
        title.addWidget(QLabel("按键序列步骤编辑", objectName="inspectorTitle"))
        note = QLabel(
            "保存在设备内，无需后台助手；支持按下、释放、敲击、ASCII 文本和有界延时。",
            objectName="muted",
        )
        note.setWordWrap(True)
        title.addWidget(note)
        summary_layout.addLayout(title, 1)
        total_limit = draft.limits["all_macro_bytes"]
        capacity = QLabel(
            f"{len(draft.macros)}/{draft.editor_rules.max_macros} 条按键序列\n"
            f"{draft.all_macro_encoded_size}/{total_limit} 字节",
            objectName="statusReady" if draft.all_macro_encoded_size <= total_limit else "statusError",
        )
        capacity.setAlignment(Qt.AlignCenter)
        summary_layout.addWidget(capacity)
        layout.addWidget(summary)

        macros = draft.macros
        macro_ids = [macro.get("id") for macro in macros if isinstance(macro.get("id"), int)]
        if self.selected_macro_id not in macro_ids:
            self.selected_macro_id = macro_ids[0] if macro_ids else None
        scroll.setProperty("macroId", self.selected_macro_id)
        editing_enabled = not self._view_model.write_transaction.blocks_editing

        editor_card = card()
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(24, 20, 24, 24)
        editor_layout.setSpacing(12)
        toolbar = QHBoxLayout()
        selector = QComboBox(objectName="macroSelector")
        selector.setProperty(SKIP_TRANSLATION_PROPERTY, True)
        for macro in macros:
            selector.addItem(str(macro.get("name", "按键序列")), macro.get("id"))
        if self.selected_macro_id is not None:
            selector.setCurrentIndex(selector.findData(self.selected_macro_id))
        selector.currentIndexChanged.connect(
            lambda _index, field=selector: self._select(field.currentData())
        )
        selector.setEnabled(bool(macros))
        toolbar.addWidget(selector, 1)
        create = QPushButton("新建按键序列", objectName="secondary")
        create.setEnabled(editing_enabled and len(macros) < draft.editor_rules.max_macros)
        create.clicked.connect(self._create)
        toolbar.addWidget(create)
        delete = QPushButton("从草稿删除", objectName="secondary")
        delete.setEnabled(editing_enabled and self.selected_macro_id is not None)
        delete.clicked.connect(self._delete)
        toolbar.addWidget(delete)
        editor_layout.addLayout(toolbar)

        self._name = self._editor = None
        if self.selected_macro_id is None:
            empty = QLabel(
                "当前配置没有按键序列。点击“新建按键序列”创建第一条。",
                objectName="muted",
            )
            empty.setWordWrap(True)
            editor_layout.addWidget(empty)
        else:
            macro = draft.macro(self.selected_macro_id)
            form = QFormLayout()
            name = QLineEdit(editing_values[0] if editing_values is not None else str(macro.get("name", "")), objectName="macroNameEditor")
            name.setValidator(NameLengthValidator(draft.editor_rules.macro_name_max_length, name))
            name.setEnabled(editing_enabled)
            form.addRow("按键序列名称", name)
            editor_layout.addLayout(form)
            steps = macro.get("steps") if isinstance(macro.get("steps"), list) else []
            macro_editor = MacroEditor(
                editing_values[1] if editing_values is not None else steps,
                self._key_choices(),
                byte_limit=draft.limits["macro_bytes"],
                rules=draft.editor_rules,
            )
            macro_editor.setEnabled(editing_enabled)
            editor_layout.addWidget(macro_editor)
            self._name, self._editor = name, macro_editor
            save = QPushButton("保存到本地草稿", objectName="primary")
            save.setEnabled(editing_enabled)
            save.clicked.connect(
                lambda: self._save(
                    int(self.selected_macro_id),
                    name.text(),
                    macro_editor.steps(),
                )
            )
            editor_layout.addWidget(save)
        layout.addWidget(editor_card)
        current_generation = snapshot.config_result.get("generation")
        layout.addWidget(
            self._write_transaction_card(snapshot, current_generation != draft.base_generation)
        )
        layout.addStretch(1)
        scroll.setWidget(page)
        self._scroll = scroll
        self._draft_identity = id(draft)
        self._built_macro_id = self.selected_macro_id
        return scroll

    def _key_choices(self) -> tuple[tuple[str, int], ...]:
        for definition in self._view_model.action_definitions:
            if definition.action_type != "key":
                continue
            for field in definition.fields:
                if field.name == "usage":
                    return tuple(
                        (label, int(value))
                        for label, value in action_field_choices("key", field, 4)
                    )
        return (("A", 4),)

    def _select(self, macro_id: object) -> None:
        if isinstance(macro_id, int) and macro_id != self.selected_macro_id:
            self.selected_macro_id = macro_id
            self._rerender()

    def _create(self) -> None:
        try:
            self.selected_macro_id = self._view_model.create_macro()
        except ValueError as exc:
            notice.notify(self._dialog_parent, "无法新建按键序列", str(exc))
            return
        self._rerender()

    def _save(self, macro_id: int, name: str, steps: list[dict]) -> None:
        try:
            self._view_model.update_macro(macro_id, name, steps)
        except ValueError as exc:
            notice.notify(self._dialog_parent, "无法保存按键序列", str(exc))
            return
        errors = self._view_model.validate_draft()
        if errors:
            notice.alert(
                self._dialog_parent, "按键序列已保存，但草稿未通过校验", errors[0]
            )

    def _delete(self) -> None:
        if self.selected_macro_id is None:
            return
        try:
            self._view_model.delete_macro(self.selected_macro_id)
        except ValueError as exc:
            notice.notify(self._dialog_parent, "无法删除按键序列", str(exc))
            return
        draft = self._view_model.draft
        self.selected_macro_id = draft.macros[0].get("id") if draft is not None and draft.macros else None
        self._rerender()
