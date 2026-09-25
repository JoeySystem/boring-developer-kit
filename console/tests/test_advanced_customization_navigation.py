from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QAction
from controller_config.views.actions import ActionsPage
from test_session_recovery import session


def test_system_entry_preserves_action_draft_and_returns_to_system(session):
    window, vm, _, _, _ = session
    assert list(window._nav_buttons) == ['overview', 'prompts', 'lighting', 'settings']
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


def test_sequences_are_managed_from_key_configuration(session):
    window, vm, _, _, _ = session
    menu = window._content.findChild(QPushButton, 'profilePill').menu()
    menu.findChild(QAction, 'manageDeviceKeySequencesAction').trigger()
    assert vm.page == 'sequences'
    window._content.findChild(QPushButton, 'backFromAdvanced').click()
    assert vm.page == 'overview'
    vm.navigate('actions')
    assert window._content.findChild(QAction, 'manageDeviceKeySequences') is None
