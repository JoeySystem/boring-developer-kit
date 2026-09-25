"""Actual modifier shortcuts must remain readable in every setup language."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings

from controller_config.actions import describe_action
from controller_config.i18n import LanguageManager
from controller_config.text_catalog import TextCatalog
from controller_config.views.action_editor import ShortcutRecorder


@pytest.mark.parametrize('language,right', [('zh_CN', '右'), ('en_US', 'Right'), ('ja_JP', '右')])
@pytest.mark.parametrize('platform,compact,modifiers,prefix,key', [
    ('macos', True, [], '', 'Command'),
    ('macos', True, [226], '⌥', 'Command'),
    ('macos', False, [224, 226], 'Control + Option + ', 'Command'),
    ('windows', True, [226], 'Alt+', 'Win'),
    ('windows', False, [226], 'Alt + ', 'Win'),
])
def test_actual_modifier_shortcuts_translate_without_changing_combination(language, right, platform, compact, modifiers, prefix, key):
    action = {'type': 'key', 'usage': 231, 'modifiers': modifiers}
    label = describe_action(action, platform=platform, compact=compact)
    catalog = TextCatalog.load()
    assert catalog.translate(label, language) == f'{prefix}{right} {key}'


def test_modifier_combination_rule_does_not_rewrite_arbitrary_text():
    catalog = TextCatalog.load()
    for source in ['My app 右 Command', 'Option + 右 Command please', '名字 + 右 Command']:
        assert catalog.translate(source, 'en_US') == source


def test_recorded_modifier_preview_updates_when_language_changes(qapp, qtbot, tmp_path):
    previous_language = qapp.property('boringUiLanguage')
    owner = QObject()
    manager = LanguageManager(qapp, settings=QSettings(str(tmp_path / 'language.ini'), QSettings.IniFormat),
                              initial_language='zh_CN', persist=False, parent=owner)
    recorder = ShortcutRecorder({'type': 'key', 'usage': 231, 'modifiers': [226]}, platform='macos')
    qtbot.addWidget(recorder)
    try:
        recorder.show()
        assert recorder._preview.text() == '⌥右 Command'
        assert recorder._preview.accessibleName() == '修改为 Option + 右 Command'
        manager.set_language('en_US')
        assert recorder._preview.text() == '⌥Right Command'
        assert recorder._preview.accessibleName() == 'Change to Option + Right Command'
        manager.set_language('ja_JP')
        assert recorder._preview.text() == '⌥右 Command'
        assert recorder._preview.accessibleName() == '変更後：Option + 右 Command'
    finally:
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(owner, QEvent.DeferredDelete)
        qapp.setProperty('boringUiLanguage', previous_language)
