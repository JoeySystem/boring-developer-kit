from controller_config.automation import LocalScriptAutomation
from controller_config.extensions.bindings import ExtensionActionBinding
from controller_config.extensions.contracts import (
    ApiVersion,
    ExtensionActionDeclaration,
    ExtensionManifest,
)
from controller_config.extensions.manager import InstalledExtension
from controller_config.host_actions import (
    HostActionContext,
    HostActionProvider,
    HostActionRegistry,
    collect_host_actions,
)
from controller_config.host_selection import HostActionError
from controller_config.workflows import LocalWorkflow, WorkflowStep


class FakeHostServices:
    platform = "darwin"

    def __init__(self) -> None:
        self.clipboard = "剪贴板 / clipboard"
        self.selection = "第一行中文\nSecond line"
        self.appended = []
        self.opened = []
        self.notifications = []

    def capture_selection(self) -> str:
        return self.selection

    def read_clipboard(self) -> str:
        return self.clipboard

    def append_text_file(self, path: str, text: str, separator: str) -> None:
        self.appended.append((path, text, separator))

    def open_target(self, target: str) -> None:
        self.opened.append(target)

    def show_notification(self, message: str) -> None:
        self.notifications.append(message)


def test_official_action_registry_carries_unicode_text_between_steps() -> None:
    services = FakeHostServices()
    registry = HostActionRegistry(services)

    captured = registry.execute(
        WorkflowStep("capture_selection", {}), HostActionContext()
    )
    appended = registry.execute(
        WorkflowStep(
            "append_text_file",
            {"path": "/tmp/notes.md", "separator": "\n\n"},
        ),
        captured.context,
    )

    assert captured.succeeded and appended.succeeded
    assert captured.context.text == "第一行中文\nSecond line"
    assert services.appended == [
        ("/tmp/notes.md", "第一行中文\nSecond line", "\n\n")
    ]


def test_official_actions_open_notify_and_report_platform_unavailable() -> None:
    services = FakeHostServices()
    registry = HostActionRegistry(services)

    assert registry.execute(
        WorkflowStep("open_target", {"target": "https://example.com"}),
        HostActionContext(),
    ).succeeded
    assert registry.execute(
        WorkflowStep("show_notification", {"message": "完成"}),
        HostActionContext(),
    ).succeeded
    assert services.opened == ["https://example.com"]
    assert services.notifications == ["完成"]

    services.platform = "linux"
    result = registry.execute(
        WorkflowStep("capture_selection", {}), HostActionContext()
    )
    assert result.succeeded is False
    assert "尚未提供" in result.message


def test_catalog_unifies_local_scripts_and_extension_actions(tmp_path) -> None:
    script = tmp_path / "collect.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    automation = LocalScriptAutomation(
        automation_id="collect-context",
        name="收集上下文",
        trigger_prompt_id=2,
        script_path=str(script),
        enabled=True,
    )
    manifest = ExtensionManifest(
        extension_id="com.boring.example.capture",
        name="Capture Extension",
        version="1.0.0",
        api_version=ApiVersion(),
        entrypoint="main.py",
        observer_events=(),
        actions=(ExtensionActionDeclaration("append_note", "追加到笔记"),),
    )
    extension = InstalledExtension(manifest, tmp_path / "extension", enabled=True)
    bindings = (
        ExtensionActionBinding(
            device_serial="SERIAL-1",
            prompt_id=3,
            extension_id=manifest.extension_id,
            action_id="append_note",
        ),
        ExtensionActionBinding(
            device_serial="OTHER-DEVICE",
            prompt_id=4,
            extension_id=manifest.extension_id,
            action_id="append_note",
        ),
    )

    catalog = collect_host_actions(
        device_serial="SERIAL-1",
        workflows=(
            LocalWorkflow(
                "official",
                "保存摘录",
                1,
                (WorkflowStep("read_clipboard", {}),),
                enabled=True,
                tested=True,
            ),
        ),
        automations=(automation,),
        extensions=(extension,),
        extension_bindings=bindings,
        extension_is_ready=lambda extension_id: extension_id == manifest.extension_id,
        trigger_problem=lambda _prompt_id: "",
    )

    assert [action.action_key for action in catalog] == [
        "official:official",
        "local-script:collect-context",
        "extension:com.boring.example.capture:append_note",
    ]
    official_action, local_action, extension_action = catalog
    assert extension_action.provider is HostActionProvider.EXTENSION
    assert extension_action.available is True
    assert extension_action.prompt_ids == (3,)
    assert local_action.provider is HostActionProvider.LOCAL_SCRIPT
    assert local_action.available is True
    assert local_action.prompt_ids == (2,)
    assert official_action.provider is HostActionProvider.OFFICIAL
    assert official_action.available is True
    assert official_action.prompt_ids == (1,)


def test_catalog_keeps_disabled_or_unbound_actions_visible(tmp_path) -> None:
    automation = LocalScriptAutomation(
        automation_id="disabled",
        name="尚未启用",
        trigger_prompt_id=1,
        script_path=str(tmp_path / "missing.py"),
        enabled=False,
    )
    manifest = ExtensionManifest(
        extension_id="com.boring.example.unbound",
        name="Unbound Extension",
        version="1.0.0",
        api_version=ApiVersion(),
        entrypoint="main.py",
        observer_events=(),
        actions=(ExtensionActionDeclaration("do_thing", "执行动作"),),
    )

    catalog = collect_host_actions(
        device_serial="SERIAL-1",
        automations=(automation,),
        extensions=(InstalledExtension(manifest, tmp_path / "extension", False),),
        extension_bindings=(),
        extension_is_ready=lambda _extension_id: False,
        trigger_problem=lambda _prompt_id: "",
    )

    assert len(catalog) == 2
    assert all(action.available is False for action in catalog)
    assert next(
        action for action in catalog if action.provider is HostActionProvider.EXTENSION
    ).physically_bound is False
