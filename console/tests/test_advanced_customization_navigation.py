from PySide6.QtWidgets import QMenu, QPushButton
from PySide6.QtGui import QAction
from controller_config.i18n import translate_ui_text
from controller_config.views.mode_page import ModeStage
from controller_config.views.actions import ActionsPage
from test_session_recovery import session


def test_system_entry_preserves_action_draft_and_returns_to_system(session):
    window, vm, _, _, _ = session
    assert list(window._nav_buttons) == ['overview', 'prompts', 'settings']  # Mode / Prompt / Settings
    window._select_settings_section('system')
    window._content.findChild(QPushButton, 'openAdvancedCustomization').click()
    assert vm.page == 'actions'
    page = window._content.findChild(ActionsPage)
    page._create.click()
    page._workflow_page._name.setText('保留我的动作')
    window._content.findChild(QPushButton, 'backFromAdvanced').click()
    assert vm.page == 'settings' and window._settings_section == 'system'
    window._content.findChild(QPushButton, 'openAdvancedCustomization').click()
    assert window._content.findChild(ActionsPage) is page
    assert page._workflow_page._name.text() == '保留我的动作'
    page.show_section(ActionsPage.MY_ACTIONS)
    page._tools.click()
    assert page._stack.currentIndex() == ActionsPage.DEVELOP_ACTIONS
    assert page._developer._automation_page is not None
    page.show_developer_lane(1)
    assert page._developer._extensions_page is not None


def test_sequences_are_managed_from_key_configuration(session, qtbot, monkeypatch):
    from controller_config.views.other_action_dialog import OtherActionDialog
    window, vm, _, _, _ = session
    # Sequences open from the key card's 选别的动作… › 连续按键 › ＋ 新建 / 编辑 (Mode页 两个弹窗 v0).
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    stage.device.choose('key.12')
    stage = next(stage for stage in window.findChildren(ModeStage) if stage.isVisible())
    monkeypatch.setattr(OtherActionDialog, "exec", lambda self: (self._open_page("sequences"), 1)[1])
    stage.card().findChild(QPushButton, 'modeCardOtherActions').click()
    assert vm.page == 'sequences'
    window._content.findChild(QPushButton, 'backFromAdvanced').click()
    assert vm.page == 'overview'
    vm.navigate('actions')
    assert window._content.findChild(QAction, 'manageDeviceKeySequences') is None
