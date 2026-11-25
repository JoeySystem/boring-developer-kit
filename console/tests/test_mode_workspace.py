"""Mode configuration stays separate from the device's runtime mode."""
from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QScrollArea

from controller_config.views.action_editor import ActionEditor
from controller_config.views.mode_page import ModeStage
from test_session_recovery import session
from test_primary_setup_flow import ready, device_writes, current_editor
from test_ai_setup import readback
from controller_config.protocol.bootstrap import BootstrapError, BootstrapKind
from controller_config.models import DeviceSnapshot


def _stage(window):
    return next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())


def show_mode(qtbot, window, mode):
    """Click the Mode page's page dot for Codex, or for the set being edited in normal mode."""
    screens = window._mode_screens()
    if mode == "codex":
        index = screens.index(("codex", None))
    else:
        index = screens.index(("normal", window._editing_profile(window._view_model.draft)))
    stage = _stage(window)
    qtbot.mouseClick(stage, Qt.LeftButton, pos=stage.page_dot_rects()[index].center().toPoint())
    assert window._configuration_mode == mode


def has_screen(window, mode):
    return any(kind == mode for kind, _ in window._mode_screens())


def _dots_inside(stage, viewport):
    return stage.isVisible() and all(
        viewport.rect().contains(QRect(stage.mapTo(viewport, rect.topLeft().toPoint()), rect.size().toSize()))
        for rect in stage.page_dot_rects())


@pytest.mark.parametrize("mode", ["normal", "codex", "claude_code"])
def test_first_read_uses_reported_mode_only_for_new_device(session, mode):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot,
        identity={**snapshot.identity, "serial": "CP01-mode-initial"},
        status={**snapshot.status, "operating_mode": mode}))
    # design decision: Console no longer configures the Claude Code mode.
    expected = "normal" if mode == "claude_code" else mode
    assert window._configuration_mode == expected
    gateway.status_updated.emit({**snapshot.status, "operating_mode": "normal"})
    assert window._configuration_mode == expected
    assert not device_writes(gateway)


def test_mode_tabs_are_direct_configuration_entries_without_device_writes(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    original = deepcopy(vm.draft.config)
    assert window._nav_buttons["overview"].accessibleName() == "Mode"
    assert not has_screen(window, "claude_code")  # design decision
    for mode in ("codex", "normal"):
        show_mode(qtbot, window, mode)
        assert _stage(window).state().word == mode.upper()
        assert vm.model.snapshot.status["operating_mode"] == "normal"
        assert vm.draft.config == original
    assert not device_writes(gateway)


def test_normal_and_codex_edits_survive_tabs_and_save_together(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window._select_physical_control("key.8")
    normal = {"type": "key", "usage": 14, "modifiers": [227]}
    current_editor(window).set_action(normal)
    show_mode(qtbot, window, "codex")
    window._select_physical_control("key.8")
    codex = {"type": "key", "usage": 104}
    current_editor(window).set_action(codex)
    show_mode(qtbot, window, "normal")
    assert current_editor(window).action() == normal
    assert vm.draft.profile(0)["codex_voice"] == codex
    assert not device_writes(gateway)
    save = _stage(window).bar.save
    assert save.isVisible() and save.isEnabled()
    qtbot.mouseClick(save, Qt.LeftButton)
    candidate = readback(vm, gateway, snapshot)
    profile = next(p for p in candidate["profiles"] if p["id"] == 0)
    assert next(m for m in profile["mappings"] if m["control_id"] == "key.8")["action"] == normal
    assert profile["codex_voice"] == codex
    assert [c.name for c in gateway.commands].count("SET_CONFIG") == 1
    assert vm.model.snapshot.status["operating_mode"] == "normal"


def test_failed_combined_write_retains_both_mode_drafts_for_retry(session, monkeypatch, tmp_path, qtbot):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window._select_physical_control('key.8')
    normal = {'type': 'key', 'usage': 14, 'modifiers': [227]}
    current_editor(window).set_action(normal)
    show_mode(qtbot, window, 'codex')
    window._select_physical_control('key.8')
    codex = {'type': 'key', 'usage': 104}
    current_editor(window).set_action(codex)
    show_mode(qtbot, window, 'normal')
    qtbot.mouseClick(_stage(window).bar.save, Qt.LeftButton)
    gateway.command_failed.emit('VALIDATE_CONFIG', BootstrapError(
        BootstrapKind.READ_FAILED, '设备命令执行失败', 'VALIDATION_FAILED', error_name='VALIDATION_FAILED'))
    assert vm.draft.is_dirty
    for mode, action in (('codex', codex), ('normal', normal)):
        show_mode(qtbot, window, mode)
        assert current_editor(window).action() == action
    # The bar says "没保存上，可以再试一次"; retrying must be one click on it.
    save = _stage(window).bar.save
    assert save.isVisible() and save.isEnabled()
    qtbot.mouseClick(save, Qt.LeftButton)
    assert gateway.commands[-1].name == 'VALIDATE_CONFIG'
    config = gateway.commands[-1].payload['config']
    assert config['profiles'][0]['codex_voice'] == codex
    assert next(m for m in config['profiles'][0]['mappings'] if m['control_id'] == 'key.8')['action'] == normal
    assert not any(c.name == 'SET_CONFIG' for c in gateway.commands)


def test_old_firmware_disables_only_unsupported_mode_without_writes(session):
    window, vm, gateway, snapshot, _ = session
    gateway.snapshot_ready.emit(replace(snapshot, capabilities={**snapshot.capabilities,
        'features': {**snapshot.capabilities['features'], 'claude_code_mode': False}}))
    assert has_screen(window, 'normal') and has_screen(window, 'codex')
    assert not has_screen(window, 'claude_code')
    window._select_configuration_mode('claude_code')
    assert window._configuration_mode == 'normal'
    assert not device_writes(gateway)


def test_rev_a_keeps_normal_mapping_without_claiming_dedicated_modes(session, load_fixture):
    window, vm, gateway, previous, _ = session
    hello = load_fixture('hello-v1.json')['ack']
    config = {'command': 'GET_CONFIG', 'result': {**hello['result']['config'],
        'config': load_fixture('config-v1.json')}}
    snapshot = DeviceSnapshot.from_protocol(
        hello=hello, config=config, capabilities=load_fixture('capabilities-v1.json'),
        status={'result': {**previous.status, 'operating_mode': 'normal',
                          'active': {key: config['result'][key] for key in ('generation', 'digest')}}},
        port_name='demo://rev-a', trust=previous.trust)
    gateway.snapshot_ready.emit(snapshot)
    assert has_screen(window, 'normal')
    assert not has_screen(window, 'codex') and not has_screen(window, 'claude_code')
    assert _stage(window).state().screens == len(window._mode_screens())
    window._select_configuration_mode('codex')
    assert window._configuration_mode == 'normal'
    window._select_physical_control('key.1')
    assert current_editor(window) is not None
    assert not device_writes(gateway)


def test_physical_mode_switch_does_not_retarget_or_discard_current_editor(session, monkeypatch, tmp_path):
    window, vm, gateway, snapshot = ready(session, monkeypatch, tmp_path)
    window._select_physical_control("key.8")
    edited = {"type": "key", "usage": 15}
    current_editor(window).set_action(edited)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, "operating_mode": "claude_code"}))
    assert window._configuration_mode == "normal"
    assert current_editor(window).action() == edited
    assert current_editor(window)._codex_voice is False
    # No page dot claims the device is using one of these screens.
    assert _stage(window).state().active is None
    assert not device_writes(gateway)


@pytest.mark.parametrize("mode,control", [
    ("codex", "key.3"), ("codex", "key.9"), ("codex", "encoder.cw"),
])
def test_dedicated_controls_do_not_offer_normal_mapping_edits(session, monkeypatch, tmp_path, mode, control):
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    before = deepcopy(vm.draft.config)
    window._select_configuration_mode(mode)
    window._select_physical_control(control)
    card = window._current_mapping_card()
    assert card.property("officialReadOnly") is True
    assert card.findChild(ActionEditor) is None
    assert card.findChild(QPushButton, "applyMappingToDevice") is None
    assert card.findChild(QLabel, "officialControlAction").text()
    assert vm.draft.config == before and not device_writes(gateway)


@pytest.mark.parametrize("language", ["zh_CN", "en_US", "ja_JP"])
@pytest.mark.parametrize("mode", ["normal", "codex"])
def test_mode_tabs_and_save_are_visible_in_minimum_window(session, monkeypatch, tmp_path, qtbot, language, mode):
    from test_responsive_workspace import _visible_inside
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._select_configuration_mode(mode)
    window._language_manager.set_language(language)
    window.resize(1100, 700)
    window._select_physical_control("key.8")
    current_editor(window).set_action({"type": "key", "usage": 15})
    qtbot.wait(250)
    outer = window.findChild(QScrollArea, "overviewScroll")
    assert outer.widget().width() <= outer.viewport().width()
    stage = _stage(window)
    assert _dots_inside(stage, outer.viewport())
    assert _visible_inside(stage.card(), outer.viewport())
    assert window.grab().save(str(tmp_path / f"mode-{mode}-{language}.png"))
    assert _visible_inside(stage.bar.save, outer.viewport()), str(tmp_path / f"mode-{mode}-{language}.png")


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
def test_small_screen_keeps_mode_choices_and_runtime_explanation_visible(session, monkeypatch, tmp_path, qtbot, language):
    from PySide6.QtCore import QRect
    from test_responsive_workspace import _visible_inside
    window, vm, gateway, _ = ready(session, monkeypatch, tmp_path)
    window._fit_window_to_available_area(QRect(0, 0, 780, 560))
    window.resize(780, 560)
    window._language_manager.set_language(language)
    for mode, cell in (('normal', 'synced'), ('codex', 'codex')):
        window._select_configuration_mode(mode)
        qtbot.wait(200)
        outer = window.findChild(QScrollArea, 'overviewScroll')
        assert outer.widget().width() <= outer.viewport().width()
        stage = _stage(window)
        assert _dots_inside(stage, outer.viewport())
        # The bar says whether the device runs this screen (Codex: double-click the knob to switch).
        assert stage.bar.state().right == cell
        assert _visible_inside(stage.bar.status_text, outer.viewport())
    assert not device_writes(gateway)
