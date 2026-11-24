"""快捷提示词 (route ``prompts``): the Prompt page (the designer 定稿 v1).

The page itself is :class:`PromptLibraryEditor` (top line, direction cross,
editor); this class builds it for the current device and keeps its runtime
(helper, storage, event log) current.

The main window keeps its per-route cache for this route (reusing the editor
while the library is unchanged), so the page offers build() and refresh().
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from controller_config.models import ScreenModel
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.prompt_library_editor import PromptLibraryEditor

NO_BACKGROUND_HELPER = "当前运行方式不会在关闭窗口后保留后台助手"


class PromptsPage:
    """Build the Prompt page and feed it the prompt helper's state."""

    route = "prompts"

    def __init__(
        self,
        view_model: MainViewModel,
        *,
        content: Callable[[], QWidget],
        permissions: Callable[[], object],
        background_message: Callable[[], str | None],
    ) -> None:
        self._view_model = view_model
        self._content = content
        self._permissions = permissions
        self._background_message = background_message

    def build(self, model: ScreenModel) -> PromptLibraryEditor:
        device = self._view_model.prompt_device
        snapshot = model.snapshot
        editor = PromptLibraryEditor(
            self._view_model.prompt_library,
            hardware_id=snapshot.identity.get("hardware_id") if snapshot is not None else None,
            over_bluetooth=snapshot is not None and snapshot.connection_kind == "bluetooth",
            permissions=self._permissions(),
            device_storage_available=device.available,
            protocol_message=self._message(),
            device_busy=device.status.is_busy,
            helper_message=device.helper_message,
            listener_status=device.listener_status,
            event_log=device.event_log,
            background_message=self._background(),
            save_draft=self._view_model.save_prompt_draft,
            delete_draft=self._view_model.delete_prompt_draft,
            discard_draft=self._view_model.discard_prompt_draft,
            refresh_device=self._view_model.refresh_prompt_library,
            write_device=self._view_model.write_prompt_draft,
            delete_device=self._view_model.delete_prompt_from_device,
            save_and_write_device=self._view_model.save_and_write_prompt,
        )
        editor.direction_selected.connect(self._select_direction)
        return editor

    def refresh(self, editor: PromptLibraryEditor) -> None:
        """Bring a reused editor's top line and diagnostics up to date."""
        device = self._view_model.prompt_device
        editor.update_runtime(
            available=device.available, busy=device.status.is_busy, message=self._message(),
            helper_message=device.helper_message, listener=device.listener_status,
            event_log=device.event_log, background_message=self._background(),
        )

    def _message(self) -> str:
        device = self._view_model.prompt_device
        message = self._view_model.prompt_library_error or device.status.message
        return f"{message}：{device.status.technical}" if device.status.technical else message

    def _background(self) -> str:
        message = self._background_message()
        return message if message is not None else NO_BACKGROUND_HELPER

    def _select_direction(self, prompt_id: int) -> None:
        # Saving can synchronously rebuild the editor before the slot click returns.
        editor = self._content().findChild(PromptLibraryEditor)
        if editor is not None and editor._selected_prompt_id() != prompt_id:
            editor._load_direction(prompt_id)
