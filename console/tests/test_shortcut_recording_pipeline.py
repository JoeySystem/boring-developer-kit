"""A captured shortcut must retain its HID usages through confirmed device readback."""
from dataclasses import replace
from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QScrollArea

from controller_config.actions import describe_action
from controller_config.i18n import translate_ui_text
from controller_config.views.action_editor import ActionEditor, ShortcutRecorder
from test_session_recovery import session
from test_feedback_write_experience import complete_write
from test_responsive_workspace import _visible_inside


@pytest.mark.parametrize('language', ['zh_CN', 'en_US', 'ja_JP'])
@pytest.mark.parametrize('host,device,qt_modifier,usage,preview', [
    ('darwin', 'windows_linux', Qt.ControlModifier, 227, '⌘N'),
    ('darwin', 'windows_linux', Qt.MetaModifier, 224, '⌃N'),
    ('win32', 'macos', Qt.ControlModifier, 224, 'Ctrl+N'),
    ('win32', 'macos', Qt.MetaModifier, 227, 'Win+N'),
])
def test_recorded_modifiers_survive_edit_validate_write_and_readback(session, qtbot, monkeypatch, host, device, qt_modifier, usage, preview, language):
    window, vm, gateway, snapshot, _ = session
    window._language_manager.set_language(language)
    monkeypatch.setattr('controller_config.views.main_window.sys.platform', host)
    snapshot = replace(snapshot, status={**snapshot.status, 'platform': device, 'operating_mode': 'normal'})
    gateway.snapshot_ready.emit(snapshot)
    window._select_physical_control('key.8')
    shortcut_name = window.findChild(QLineEdit, 'mappingShortNameEditor')
    shortcut_name.setText('Next item')
    recorder = window.findChild(ShortcutRecorder)
    recorder.start_recording()
    qtbot.keyClick(recorder._capture, Qt.Key_N, qt_modifier)
    expected = {'type': 'key', 'usage': 17, 'modifiers': [usage]}
    assert recorder._preview.text() == preview
    assert window.findChild(ActionEditor).action() == expected
    window.findChild(QPushButton, 'applyMappingToDevice').click()
    validate = gateway.commands[-1]
    assert validate.name == 'VALIDATE_CONFIG'
    config = validate.payload['config']
    profile = next(p for p in config['profiles'] if p['id'] == config['active_profile'])
    written_mapping = next(m for m in profile['mappings'] if m['control_id'] == 'key.8')
    assert written_mapping['short_name'] == 'Next item'
    assert written_mapping['action'] == expected
    complete_write(vm, gateway, snapshot)
    written = next(c for c in gateway.commands if c.name == 'SET_CONFIG')
    assert written.payload['config'] == config
    assert vm.model.snapshot.mappings['key.8']['action'] == expected
    assert not window.findChild(QPushButton, 'applyMappingToDevice').isEnabled()
    # Host naming must remain consistent after readback without rewriting device preferences.
    assert vm.model.snapshot.status['platform'] == device
    assert window.findChild(QLabel, 'actualActionValue').text() == describe_action(expected, platform="macos" if host == "darwin" else "windows")


@pytest.mark.parametrize('language', ['zh_CN', 'en_US'])
def test_codex_workspace_shows_fixed_action_not_normal_mapping(session, qtbot, tmp_path, language):
    window, vm, gateway, snapshot, _ = session
    window._language_manager.set_language(language)
    gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, 'operating_mode': 'codex'}))
    original = deepcopy(vm.draft.profile(0)['mappings'])
    window._select_configuration_mode('codex')
    window._select_physical_control('key.9')
    card = window._current_mapping_card()
    assert card.property('officialReadOnly') is True
    assert card.findChild(QLabel, 'officialControlAction').text() == 'Approve'
    assert card.findChild(ActionEditor) is None
    assert card.findChild(QPushButton, 'applyMappingToDevice') is None
    window.resize(1100, 700)
    window.show()
    qtbot.wait(300)
    viewport = window._current_mapping_card()
    assert _visible_inside(card.findChild(QLabel, 'officialControlAction'), viewport)
    assert window.grab().save(str(tmp_path / f'codex-notice-{language}.png'))
    gateway.status_updated.emit({**vm.model.snapshot.status, 'operating_mode': 'normal'})
    assert window._configuration_mode == 'codex'
    assert window._current_mapping_card().findChild(ActionEditor) is None
    window._select_configuration_mode('normal')
    window._select_physical_control('key.9')
    assert window._current_mapping_card().findChild(ActionEditor) is not None
    assert vm.draft.profile(0)['mappings'] == original
    assert not any(c.name in {'VALIDATE_CONFIG', 'SET_CONFIG'} for c in gateway.commands)
    window._language_manager.set_language('zh_CN')


# A lone modifier is still recorded (mapping Option by itself is a real need),
# but the recorder says which key it got: when another app owns e.g. Option+Space
# (Doubao's voice shortcut, Raycast, an input method), macOS hands that app the
# Space and the user would otherwise silently end up with just Option.
from PySide6.QtCore import QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from controller_config.actions import action_definitions
from controller_config.views.global_voice import GlobalVoiceDialog
from controller_config.views.voice_guide import VoiceSetupDialog
from test_global_voice import _ready_vm

_TAKEN = '只录到了 {}。另一个键可能被别的应用（比如豆包、输入法）占用了，先退出它再录。'
_VOICE = '已录到 {}。语音键单独用这个键就行；要是还按了别的键，可能被语音软件（比如豆包）先收走了，先退出它再录。'
_OK = '已识别，可继续修改或应用到设备。'


def _key(target, kind, key, modifiers=Qt.NoModifier, native=0):
    QApplication.sendEvent(target, QKeyEvent(kind, key, modifiers, 0, native, 0))


def _press_release_option(recorder, platform):
    native = 0x3D if platform == 'macos' else 0  # Right Option on a Mac keyboard.
    recorder.start_recording()
    _key(recorder._capture, QEvent.KeyPress, Qt.Key_Alt, Qt.AltModifier, native)
    _key(recorder._capture, QEvent.KeyRelease, Qt.Key_Alt, Qt.NoModifier, native)


@pytest.fixture
def ui_language(qapp):
    previous = qapp.property('boringUiLanguage')
    yield lambda language: qapp.setProperty('boringUiLanguage', language)
    qapp.setProperty('boringUiLanguage', previous)


@pytest.mark.parametrize('language,expected', [
    ('zh_CN', '只录到了 右 Option。'),
    ('en_US', 'Only Right Option was recorded.'),
    ('ja_JP', '右 Option だけが記録されました。'),
])
def test_lone_modifier_is_recorded_and_says_another_app_may_own_the_combination(qtbot, ui_language, language, expected):
    ui_language(language)
    recorder = ShortcutRecorder({'type': 'none'}, platform='macos')
    qtbot.addWidget(recorder)
    recorded = []
    recorder.shortcut_recorded.connect(recorded.append)
    _press_release_option(recorder, 'macos')
    assert recorded == [{'type': 'key', 'usage': 230}]
    message = recorder.findChild(QLabel, 'shortcutRecorderMessage')
    assert not message.isHidden()
    assert message.text() == translate_ui_text(_TAKEN.format('右 Option'))
    assert message.text().startswith(expected)
    assert '只录到了' not in message.text() or language == 'zh_CN'  # Fully translated.


def test_windows_names_the_key_with_windows_words(qtbot, ui_language):
    ui_language('en_US')
    recorder = ShortcutRecorder({'type': 'none'}, platform='windows')
    qtbot.addWidget(recorder)
    recorder.start_recording()
    _key(recorder._capture, QEvent.KeyPress, Qt.Key_Control, Qt.ControlModifier)
    _key(recorder._capture, QEvent.KeyPress, Qt.Key_Alt, Qt.ControlModifier | Qt.AltModifier)
    _key(recorder._capture, QEvent.KeyRelease, Qt.Key_Alt, Qt.ControlModifier)
    assert recorder.findChild(QLabel, 'shortcutRecorderMessage').text().startswith(
        'Only Ctrl + Left Alt was recorded.')


def test_option_space_that_arrives_is_a_combination_without_the_hint(qtbot, ui_language):
    ui_language('zh_CN')
    recorder = ShortcutRecorder({'type': 'none'}, platform='macos')
    qtbot.addWidget(recorder)
    recorded = []
    recorder.shortcut_recorded.connect(recorded.append)
    recorder.start_recording()
    _key(recorder._capture, QEvent.KeyPress, Qt.Key_Alt, Qt.AltModifier, 0x3D)
    _key(recorder._capture, QEvent.KeyPress, Qt.Key_Space, Qt.AltModifier)
    assert recorded == [{'type': 'key', 'usage': 44, 'modifiers': [230]}]
    assert recorder.findChild(QLabel, 'shortcutRecorderMessage').text() == translate_ui_text(_OK)


def _assert_voice_wording(recorder, platform='macos'):
    recorded = []
    recorder.shortcut_recorded.connect(recorded.append)
    _press_release_option(recorder, platform)
    assert recorded[-1]['usage'] in {226, 230}
    text = recorder.findChild(QLabel, 'shortcutRecorderMessage').text()
    assert '只录到了' not in text and '占用' not in text
    assert text.startswith('已录到 ') and '语音键单独用这个键就行' in text


def test_voice_key_recorders_do_not_call_a_lone_modifier_a_lost_combination(qtbot, contract, ui_language):
    ui_language('zh_CN')
    # Mapping page, "语音输入" panel: the voice key itself vs. the send shortcut.
    editor = ActionEditor(action_definitions(contract, ('key', 'key_gesture', 'none')), {'type': 'key', 'usage': 4},
                          platform='macos', allow_voice_preset=True)
    qtbot.addWidget(editor)
    assert editor._voice_recorder.voice_key and not editor._send_recorder.voice_key
    assert not editor._shortcut_recorder.voice_key  # Ordinary shortcuts keep the hint.
    _assert_voice_wording(editor._voice_recorder)
    # Shared (global) voice key dialog.
    vm, _, _ = _ready_vm(contract)
    dialog = GlobalVoiceDialog(vm, platform='macos')
    qtbot.addWidget(dialog)
    _assert_voice_wording(dialog.recorder)
    # Voice setup guide: single press is the voice key, double press sends text.
    guide = VoiceSetupDialog(view_model=vm, platform='macos', include_target=False)
    qtbot.addWidget(guide)
    voice, send = (editor for _, editor in guide._shortcut_editors)
    assert voice._shortcut_recorder.voice_key and not send._shortcut_recorder.voice_key
    _assert_voice_wording(voice._shortcut_recorder)
    _press_release_option(send._shortcut_recorder, 'macos')
    assert send._shortcut_recorder.findChild(QLabel, 'shortcutRecorderMessage').text().startswith('只录到了 ')
