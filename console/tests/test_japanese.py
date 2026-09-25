from __future__ import annotations

import re

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPushButton

from controller_config.i18n import JAPANESE, normalize_language
from controller_config.text_catalog import TextCatalog
from test_session_recovery import session


def test_japanese_catalog_is_complete_and_keeps_all_format_parameters():
    catalog = TextCatalog.load()
    assert JAPANESE in catalog.languages
    fields = lambda text: set(re.findall(r"\{[A-Za-z_]\w*\}", text))
    for group in (catalog.messages, catalog.dynamic):
        for key, entry in group.items():
            assert isinstance(entry[JAPANESE], str), key
            assert fields(entry[JAPANESE]) == fields(entry['en_US']), key
    assert catalog.translate('仅保存本地草稿', JAPANESE) == '下書きを保存'
    assert catalog.translate('写入设备并读回确认', JAPANESE) == '本体に保存'
    assert catalog.translate('功能名称：设置', JAPANESE) == '名前：设置'
    assert catalog.translate('功能名称：私の設定 {name}', JAPANESE) == '名前：私の設定 {name}'
    assert catalog.translate(
        '将删除设备的右方向快捷提示词。该方向在重新配置前不会执行提示词。是否继续？', JAPANESE,
    ) == '本体の「右」方向のプロンプトを削除しますか？設定し直すまで、この方向では実行できません。'


@pytest.mark.parametrize('locale', ['ja', 'ja-JP', 'ja_JP', 'JA-jp'])
def test_japanese_locale_normalization(locale):
    assert normalize_language(locale) == JAPANESE


def test_japanese_setting_is_selectable_persisted_and_keeps_user_names(session, qtbot):
    window, vm, _gateway, snapshot, _store = session
    manager = window._language_manager
    window.show()
    vm.navigate('settings')
    japanese = next(b for b in window.findChildren(QPushButton, 'settingsLanguageButton')
                    if b.text() == '日本語')
    japanese.click()
    assert manager.language == JAPANESE
    assert window.findChild(QMenu, 'settingsMenu').title() == '設定'
    assert window.findChild(QMenu, 'languageMenu').title() == '言語'
    assert manager._settings.value('ui/language') == JAPANESE
    assert window.findChild(QAction, 'languageAction_ja_JP').isChecked()
    assert window._nav_buttons['overview'].accessibleName() == 'キー設定'
    assert {
        button.text()
        for button in window.findChildren(QPushButton, 'settingsGroup')
    } == {'システム', 'デバイス', 'ファームウェア更新'}
    vm.rename_profile(snapshot.active_profile_id, '设置・私の設定')
    vm.navigate('overview')
    for language, name in [('en_US', 'Key Mapping'), ('zh_CN', '按键配置'), ('ja_JP', 'キー設定')]:
        manager.set_language(language)
        assert window._nav_buttons['overview'].accessibleName() == name
        action = window.findChild(QAction, f'profileSelectAction_{snapshot.active_profile_id}')
        assert action.text() == '设置・私の設定'
    vm.discard_draft()
