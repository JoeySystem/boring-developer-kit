"""Exercise both incoming UI changes against the current local workspaces."""
import copy
import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLineEdit, QPushButton, QWidget

from controller_config.views.device_silhouette import DeviceModelCanvas
from controller_config.views.v4_widgets import UsageRings
from test_session_recovery import session


@pytest.fixture(autouse=True)
def restore_application_language(qapp):
    previous = qapp.property('boringUiLanguage')
    yield
    qapp.setProperty('boringUiLanguage', previous)


def test_language_switch_and_model_resize_preserve_pending_mapping(session, qtbot):
    window, vm, gateway, snapshot, _ = session
    window.resize(1280, 800)
    window.show()
    button = next(b for b in window.findChildren(QPushButton) if b.property('controlId') == 'key.8')
    qtbot.mouseClick(button, Qt.LeftButton, pos=button.rect().center())
    assert window.findChild(DeviceModelCanvas).selectedControlId == 'key.8'
    name = window.findChild(QLineEdit, 'mappingShortNameEditor')
    name.setText('设置・日本語')
    config_before = copy.deepcopy(vm.draft.config)
    for language, size in [('ja_JP', (1920, 1050)), ('en_US', (1100, 700)), ('zh_CN', (1280, 800))]:
        window._language_manager.set_language(language)
        window.resize(*size)
        qtbot.wait(80)
        assert window._selected_control_id == 'key.8'
        assert window.findChild(QLineEdit, 'mappingShortNameEditor').text() == '设置・日本語'
        assert vm.draft.config == config_before
        shell = window.findChild(QWidget, 'deviceShell')
        canvas = shell.findChild(DeviceModelCanvas)
        button = next(b for b in shell.findChildren(QPushButton) if b.property('controlId') == 'key.8')
        assert (button.geometry().center() - canvas.control_rect('key.8').center().toPoint()).manhattanLength() <= 2
        rings = window.findChild(UsageRings, 'homeUsageRings')
        assert rings.isVisible() and rings.width() == rings.height()
    assert not any(c.name in {'SET_CONFIG', 'VALIDATE_CONFIG', 'FW_BEGIN'} for c in gateway.commands)
    window.findChild(QPushButton, 'saveMappingDraft').click()


@pytest.mark.parametrize('side', [280, 420, 640])
def test_disconnected_preview_scales_without_enabling_device_controls(qtbot, side):
    from controller_config.views.device_silhouette import create_device_silhouette
    selected = []
    shell = create_device_silhouette(None, on_control=selected.append)
    qtbot.addWidget(shell)
    shell.scale_to(side)
    shell.show()
    canvas = shell._canvas
    assert canvas.size() == shell.size()
    assert not canvas.screenEnabled
    canvas.activateControl('key.1')
    assert selected == []


@pytest.mark.parametrize('language', ['en_US', 'ja_JP'])
def test_3d_action_label_is_translated_without_mutating_mapping(session, language, qtbot):
    from dataclasses import replace
    from controller_config.views.device_silhouette import create_device_silhouette
    from controller_config.i18n import translate_ui_text
    window, vm, _, snapshot, _ = session
    window._language_manager.set_language(language)
    mapping = {'action': {'type': 'key', 'usage': 42, 'modifiers': []}, 'short_name': '设置・日本語'}
    mappings = {'key.3': mapping}
    before = copy.deepcopy(mappings)
    shell = create_device_silhouette(snapshot, mappings=mappings)
    qtbot.addWidget(shell)
    canvas = shell._canvas
    assert canvas._control_labels['key.3'].startswith('Back')
    assert canvas._control_labels['key.8'].startswith('BT 1')
    assert mappings == before
    from PySide6.QtWidgets import QLabel
    assert window.findChild(QLabel, 'deviceStageName').text() == translate_ui_text('BORING MIST · 屏幕效果示意')


@pytest.mark.parametrize('language', ['en_US', 'ja_JP'])
def test_multiline_model_tooltip_translates_actions_and_preserves_user_name(session, language):
    from controller_config.i18n import translate_ui_text
    window, *_ = session
    window._language_manager.set_language(language)
    source = '点击设置旋钮\n逆时针 · 音量降低（设置/日本語）\n顺时针 · 音量提高（Volume）\n按 · 静音（Mute）'
    translated = translate_ui_text(source)
    assert '设置/日本語' in translated
    assert all(word not in translated for word in ('点击设置', '旋钮', '逆时针', '顺时针', '音量降低', '音量提高'))
    assert '未映射' not in translate_ui_text('点击设置功能键 3\n未映射')
    source = '点击设置摇杆\n↑/↓ · 未映射/未映射（Up/Down）\n←/→ · 音量提高/音量降低（Left/Right）\n按 · 静音（设置/日本語）'
    translated = translate_ui_text(source)
    assert '设置/日本語' in translated and 'Up/Down' in translated
    assert '未映射' not in translated and '音量提高' not in translated
