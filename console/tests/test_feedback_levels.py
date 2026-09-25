import copy

import pytest
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QSlider

from controller_config.appearance import V4_STYLE
from controller_config.i18n import LanguageManager
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.views.main_window import POWER_V2_HAPTIC_LEVELS, POWER_V2_LIGHTING_LEVELS
from controller_config.views.preferences_editor import PreferencesEditor
from test_session_recovery import session


def make_editor(qtbot, contract, strength=60, enabled=True):
    config = copy.deepcopy(_power_v2_snapshot(contract, read_only=False).config)
    config["haptic"].update(strength=strength, enabled=enabled, on_encoder=False)
    config["display"]["brightness"] = 64
    editor = PreferencesEditor(
        lighting=config["lighting"], haptic=config["haptic"], display=config["display"],
        features={"haptic": True, "haptic_channels": True, "display": True},
        under_key_control_ids=(), agent_status_control_ids=frozenset(),
        rules=contract.editor_rules, lighting_levels=POWER_V2_LIGHTING_LEVELS,
        haptic_levels=POWER_V2_HAPTIC_LEVELS,
    )
    qtbot.addWidget(editor)
    return editor, config


@pytest.mark.parametrize("level,strength", enumerate((0, 40, 50, 60, 80)))
def test_haptic_selection_writes_device_level_and_preserves_channels(qtbot, contract, level, strength):
    editor, config = make_editor(qtbot, contract, strength=48)
    control = editor.findChild(QComboBox, "hapticStrength")
    assert control.count() == 5 and not control.isEditable()
    control.setCurrentIndex(level)
    expected = {**config["haptic"], "enabled": level > 0}
    if level:
        expected["strength"] = strength
    assert editor.values()[1] == expected


@pytest.mark.parametrize("strength,enabled", [(48, True), (48, False), (60, True), (80, False)])
def test_existing_values_survive_open_master_toggle_and_unrelated_edit(qtbot, contract, strength, enabled):
    editor, config = make_editor(qtbot, contract, strength, enabled)
    assert editor.values() == (config["lighting"], config["haptic"], config["display"])
    editor._haptic_enabled.setChecked(not enabled)
    editor._haptic_enabled.setChecked(enabled)
    assert editor.values()[1] == config["haptic"]
    editor._display_hints.toggle()
    assert editor.values()[1] == config["haptic"]
    assert editor.values()[2]["brightness"] == 64


def test_zero_level_and_master_toggle_restore_selected_strength(qtbot, contract):
    editor, _ = make_editor(qtbot, contract)
    editor._haptic_strength.setCurrentIndex(4)
    editor._haptic_strength.setCurrentIndex(0)
    assert editor.values()[1]["enabled"] is False
    assert editor.values()[1]["strength"] == 80
    editor._haptic_enabled.setChecked(True)
    assert editor._haptic_strength.currentIndex() == 4
    assert editor.values()[1]["strength"] == 80


def test_display_brightness_exposes_the_firmware_range(qtbot, contract):
    editor, config = make_editor(qtbot, contract)
    control = editor.findChild(QSlider, "displayBrightness")
    value = editor.findChild(QLabel, "displayBrightnessValue")
    assert (control.minimum(), control.maximum(), control.value()) == (0, 100, 64)
    assert value.text() == "64%"
    control.setValue(28)
    assert value.text() == "28%"
    assert editor.values()[2] == {**config["display"], "brightness": 28}
    control.setValue(0)
    assert editor.values()[2]["brightness"] == 0
    control.setValue(64)
    assert editor.values()[2] == config["display"]
    rotations = editor.findChildren(QPushButton, "displayRotationOption")
    assert [button.property("rotationValue") for button in rotations] == [0, 90, 180, 270]
    assert [button.isChecked() for button in rotations] == [True, False, False, False]
    rotations[1].click()
    assert editor.values()[2] == {**config["display"], "rotation": 90}


def test_main_window_uses_hardware_levels_and_retains_draft_on_page_change(session, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    window, vm, gateway, _snapshot, _store = session
    vm.navigate("lighting")
    editor = window.findChild(PreferencesEditor)
    assert editor.findChild(QComboBox, "hapticStrength") is not None
    brightness = editor.findChild(QSlider, "displayBrightness")
    assert brightness is not None
    editor._haptic_strength.setCurrentIndex(4)
    brightness.setValue(47)
    editor.findChildren(QPushButton, "displayRotationOption")[1].click()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: QMessageBox.Save)
    window._nav_buttons["settings"].click()
    assert vm.draft.config["haptic"]["strength"] == 80
    vm.navigate("lighting")
    assert window.findChild(PreferencesEditor)._haptic_strength.currentIndex() == 4
    assert window.findChild(QSlider, "displayBrightness").value() == 47
    assert window.findChildren(QPushButton, "displayRotationOption")[1].isChecked()
    assert not any(command.name in {"VALIDATE_CONFIG", "SET_CONFIG"} for command in gateway.commands)


@pytest.mark.parametrize("language", ["zh_CN", "en_US"])
def test_level_copy_translates_and_card_fits(qtbot, qapp, contract, tmp_path, language):
    manager = LanguageManager(qapp, initial_language=language, persist=False)
    editor, _ = make_editor(qtbot, contract, strength=48)
    editor.setStyleSheet(V4_STYLE)
    manager.retranslate_widget_tree(editor)
    assert editor._haptic_strength.placeholderText() == (
        "Previous value retained; select a level" if language == "en_US"
        else "旧配置保留，请选择档位"
    )
    assert editor._haptic_strength.itemText(4) == ("4 · Maximum" if language == "en_US" else "4 · 最高")
    card = editor._haptic_card
    editor.layout().removeWidget(card)
    card.setParent(None)
    qtbot.addWidget(card)
    card.setStyleSheet(V4_STYLE)
    card.resize(460, 560)
    card.show()
    qtbot.wait(30)
    assert card.rect().contains(editor._haptic_strength.geometry())
    assert card.grab().save(str(tmp_path / f"haptic-levels-{language}.png"))
    screen = editor._screen_card
    editor.layout().removeWidget(screen)
    screen.setParent(None)
    qtbot.addWidget(screen)
    screen.setStyleSheet(V4_STYLE)
    screen.resize(460, 850)
    screen.show()
    qtbot.wait(30)
    assert screen.rect().contains(editor._display_brightness.geometry())
    assert editor._display_brightness.value() == 64
    assert screen.findChild(QLabel, "displayBrightnessValue").text() == "64%"
    rotations = screen.findChildren(QPushButton, "displayRotationOption")
    assert len(rotations) == 4
    assert all(screen.rect().contains(button.geometry()) for button in rotations)
    assert screen.grab().save(str(tmp_path / f"screen-backlight-{language}.png"))
    manager.set_language("zh_CN")
