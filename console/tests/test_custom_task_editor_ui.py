from __future__ import annotations

from PySide6.QtWidgets import QWidget

from controller_config.views.macro_editor import MacroEditor


def test_recorded_shortcut_keeps_modifier_press_release_paired(qtbot, contract):
    editor = MacroEditor(
        [{"op": "delay", "duration_ms": 100}], (("A", 4), ("C", 6)),
        byte_limit=256, rules=contract.editor_rules,
    )
    qtbot.addWidget(editor)
    editor._append_shortcut({"type": "key", "usage": 6, "modifiers": [225, 227]})
    assert editor.steps() == [
        {"op": "delay", "duration_ms": 100},
        {"op": "press", "usage": 225},
        {"op": "press", "usage": 227},
        {"op": "tap", "usage": 6},
        {"op": "release", "usage": 227},
        {"op": "release", "usage": 225},
    ]
    assert editor.findChild(QWidget, "macroAdvancedControls").isHidden()


def test_shortcut_recording_uses_macos_modifier_semantics(qtbot, contract, monkeypatch):
    monkeypatch.setattr("controller_config.views.macro_editor.sys.platform", "darwin")
    editor = MacroEditor(
        [{"op": "tap", "usage": 4}], (("A", 4),),
        byte_limit=256, rules=contract.editor_rules,
    )
    qtbot.addWidget(editor)
    editor._record_shortcut()
    assert editor._recorder._platform == "macos"


def test_script_import_is_a_local_task_and_logs_start_collapsed(qtbot, contract, tmp_path):
    from controller_config.automation import AutomationStore
    from controller_config.transport.demo import DemoGateway
    from controller_config.viewmodels.main import MainViewModel
    from controller_config.views.automation import AutomationPage

    vm = MainViewModel(DemoGateway(contract, "ready"), contract,
                       automation_store=AutomationStore(tmp_path))
    vm.start()
    qtbot.waitUntil(lambda: vm.draft is not None)
    page = AutomationPage(vm)
    qtbot.addWidget(page)
    assert page.editing_state().trigger_prompt_id is None
    assert page.editing_state().timeout_ms == 60_000
    assert page._logs.isHidden()
    assert page._latest_event.isHidden()
    page._details.click()
    assert not page._logs.isHidden()
    vm.shutdown()


def test_script_catalog_refresh_keeps_unsaved_editor_fields(qtbot, contract, tmp_path):
    from controller_config.automation import AutomationStore
    from controller_config.transport.demo import DemoGateway
    from controller_config.viewmodels.main import MainViewModel
    from controller_config.views.automation import AutomationPage

    vm = MainViewModel(DemoGateway(contract, "ready"), contract,
                       automation_store=AutomationStore(tmp_path / "scripts"))
    vm.start()
    qtbot.waitUntil(lambda: vm.draft is not None)
    page = AutomationPage(vm)
    qtbot.addWidget(page)
    page._name.setText("未保存的工作")
    page._script_path.setText("/draft/tool.py")
    before = page.editing_state()
    script = tmp_path / "saved.py"
    script.write_text("print('saved')\n")
    vm.automation_host.save_definition(automation_id="saved", name="已应用", trigger_prompt_id=None,
                                      script_path=str(script), enabled=False)
    page.refresh_runtime()
    assert page.editing_state() == before
    assert page._list.count() == 1
    vm.shutdown()


def test_extension_page_keeps_technical_details_and_legacy_binding_collapsed(qtbot, contract):
    from controller_config.transport.demo import DemoGateway
    from controller_config.viewmodels.main import MainViewModel
    from controller_config.views.extensions import ExtensionsPage

    vm = MainViewModel(DemoGateway(contract, "ready"), contract)
    page = ExtensionsPage(vm)
    qtbot.addWidget(page)
    assert page._logs.isHidden()
    assert page._legacy_binding.isHidden()
    assert not page._apply_task.isEnabled()
    page._log_toggle.click()
    assert not page._logs.isHidden()
    vm.shutdown()
