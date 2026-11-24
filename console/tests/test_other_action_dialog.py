"""选别的动作… (Mode页 两个弹窗 v0, 2026-11-20)."""
import pytest
from PySide6.QtWidgets import QLineEdit, QPushButton

from controller_config.views.other_action_dialog import OtherActionContext, OtherActionDialog
from test_session_recovery import session  # noqa: F401


def _stage(window):
    from controller_config.views.mode_page import ModeStage
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def _dialog(window, control):
    from controller_config.views.action_editor import ActionEditor
    window._view_model.navigate("overview")
    window._show_mode_screen(0)
    _stage(window).device.choose(control)
    editor = window._current_mapping_card().findChild(ActionEditor)
    context = window._other_action_context(editor)
    dialog = OtherActionDialog(window, context)
    dialog.show()
    return dialog, editor


def test_step_one_has_four_common_shortcuts_and_the_kinds_this_key_can_take(session, qtbot):  # noqa: F811
    window, *_ = session
    dialog, _ = _dialog(window, "key.12")
    assert len(dialog._common.buttons()) == 4
    assert {"consumer", "mouse", "device", "key", "none"} <= set(dialog.tiles)
    assert not dialog.use.isEnabled()                           # nothing picked yet
    dialog._common.buttons()[1].click()                         # 粘贴
    assert dialog.use.isEnabled()
    dialog.use.click()
    assert dialog.chosen == dialog._common.buttons()[1].property("action")
    dialog.deleteLater()


def test_the_knob_never_offers_split_click_or_computer_tasks(session, qtbot):  # noqa: F811
    window, *_ = session
    dialog, _ = _dialog(window, "encoder.cw")
    assert "key_gesture" not in dialog.tiles and "host_action" not in dialog.tiles
    assert dialog.tiles["consumer"].current                      # the knob does a media key now: 现在
    dialog.reject()


def test_computer_tasks_are_greyed_with_the_reason_off_keys_8_to_12(session, qtbot):  # noqa: F811
    from controller_config.views.action_editor import ActionEditor
    window, vm, *_ = session
    window._view_model.navigate("overview")
    window._show_mode_screen(0)
    _stage(window).device.choose("key.9")
    editor = window._current_mapping_card().findChild(ActionEditor)
    window._selected_control_id = "key.2"                      # same card, asked as a key outside 8–12
    context = window._other_action_context(editor)
    if "host_action" in vm.draft.actions:
        assert context.greyed.get("host_action") == "只有功能键 8～12 能用"
    else:
        assert "host_action" in context.notices                # firmware cannot: said in step 2
    window._selected_control_id = "key.9"


def test_nothing_needs_no_second_step(session, qtbot):  # noqa: F811
    window, *_ = session
    dialog, _ = _dialog(window, "key.12")
    dialog.tiles["none"].click()
    assert dialog.use.isEnabled() and dialog.back.isHidden()
    dialog.use.click()
    assert dialog.chosen == {"type": "none"}


def test_media_mouse_device_and_manual_key_hand_back_a_complete_action(session, qtbot):  # noqa: F811
    window, *_ = session
    dialog, _ = _dialog(window, "key.12")
    dialog.show_kind("consumer")
    assert not dialog.back.isHidden() and dialog._step_label.text() == "选别的动作"
    dialog.findChild(QPushButton, "otherActionConsumer_226").click()   # 静音
    assert dialog._pending == {"type": "consumer", "usage": 0x00E2}
    dialog.show_kind("mouse")
    x = dialog.findChild(QLineEdit, "otherActionMouse_x")
    x.setText("200")                                            # out of range: orange, 用这个 off
    assert not dialog.use.isEnabled() and x.property("invalid") is True
    x.setText("-20")
    assert dialog._pending == {"type": "mouse", "button": 0, "x": -20, "y": 0, "wheel": 0, "pan": 0}
    dialog.show_kind("key")
    dialog.findChild(QPushButton, "otherActionModifier_227").click()   # ⌘
    assert dialog._pending["type"] == "key" and dialog._pending["modifiers"] == [227]
    dialog.show_kind("device")
    first = dialog.current_step().findChildren(__import__("PySide6.QtWidgets", fromlist=["QAbstractButton"]).QAbstractButton)
    rows = [row for row in first if row.objectName().startswith("otherActionChoice_")]
    rows[0].click()
    assert dialog._pending["type"] == "device"
    dialog.back.click()
    assert dialog.current_step() is dialog.first
    dialog.reject()


def test_each_step_takes_its_own_height(session, qtbot):  # noqa: F811
    window, *_ = session
    dialog, _ = _dialog(window, "key.12")
    first = dialog.height()
    dialog.show_kind("consumer")
    assert dialog.height() < first                             # 601 → 326 in the draft
    dialog.reject()


def test_the_card_writes_out_any_kind_and_keeps_no_form(session, qtbot, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QLabel, QWidget
    window, *_ = session
    window._view_model.navigate("overview")
    window._show_mode_screen(0)
    _stage(window).device.choose("key.12")

    def pick(self):
        self.chosen = {"type": "consumer", "usage": 0x00E9}
        return 1

    monkeypatch.setattr(OtherActionDialog, "exec", pick)
    card = window._current_mapping_card()
    card.findChild(QPushButton, "modeCardOtherActions").click()
    card = window._current_mapping_card()
    assert card.findChild(QLabel, "shortcutRecorderPreview").text() == "音量 +"
    assert card.findChild(QLabel, "modeCardActionCaption") is not None
    assert not card.findChild(QWidget, "manualActionEditor").isVisibleTo(card)
    assert _stage(window).bar.state().editing_key is True        # 这颗键有改动 · 保存到设备


def test_sequences_link_closes_the_dialog_and_opens_the_page(session, qtbot):  # noqa: F811
    window, vm, *_ = session
    dialog, _ = _dialog(window, "key.12")
    dialog.show_kind("macro")
    new = dialog.current_step().findChild(QPushButton, "otherActionNew")
    assert new is not None
    new.click()
    assert dialog.follow_up == "sequences" and dialog.chosen is None
