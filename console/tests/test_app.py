from __future__ import annotations

import pytest

import controller_config.app as app_module
from controller_config.app import build_parser


def test_runtime_pid_cannot_be_overridden_from_cli() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--debug-usb-pid", "0x9999"])


def test_production_trust_policy_cannot_be_overridden_from_cli() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--trust-policy", "development"])


def test_firmware_manifest_url_is_an_explicit_runtime_integration_point() -> None:
    args = build_parser().parse_args(
        [
            "--firmware-manifest-url",
            "https://updates.example.test/{hardware_id}/firmware-manifest.json",
        ]
    )

    assert args.firmware_manifest_url.startswith("https://updates.example.test/")


def test_background_mode_is_an_explicit_startup_option() -> None:
    args = build_parser().parse_args(["--background"])

    assert args.background is True


def test_packaged_firmware_source_and_explicit_sample_channel() -> None:
    from controller_config.firmware_release import default_firmware_source
    source = default_firmware_source()
    assert source["channel"] == "stable"
    assert isinstance(source["manifest_url"], str)
    assert build_parser().parse_args(["--firmware-channel", "sample"]).firmware_channel == "sample"


def test_authenticity_failure_is_available_for_ui_qa() -> None:
    args = build_parser().parse_args(["--demo", "authenticity-failed"])

    assert args.demo == "authenticity-failed"


@pytest.mark.parametrize(
    ("platform", "usage_enabled", "live_device"),
    (("linux", False, False), ("darwin", True, True)),
)
def test_application_quit_always_shuts_down_device_gateway(
    monkeypatch, platform: str, usage_enabled: bool, live_device: bool
) -> None:
    shutdowns = []
    integrations = []
    device_authenticators = []
    extension_starts = []
    authenticator = object()

    class FakeSignal:
        def __init__(self) -> None:
            self._callbacks = []

        def connect(self, callback) -> None:
            self._callbacks.append(callback)

        def emit(self) -> None:
            for callback in tuple(self._callbacks):
                callback()

    class FakeApplication:
        def __init__(self, _argv) -> None:
            self.aboutToQuit = FakeSignal()

        def setWindowIcon(self, _icon):
            pass

        def style(self):
            from PySide6.QtGui import QIcon
            from types import SimpleNamespace
            return SimpleNamespace(standardIcon=lambda *_: QIcon())

        def setApplicationName(self, _name: str) -> None:  # noqa: N802
            pass

        def setApplicationDisplayName(self, _name: str) -> None:  # noqa: N802
            pass

        def setOrganizationName(self, _name: str) -> None:  # noqa: N802
            pass

        def clipboard(self):
            return object()

        def exec(self) -> int:
            self.aboutToQuit.emit()
            return 0

    class FakeInstance:
        def __init__(self) -> None:
            self.activate_requested = FakeSignal()

        def acquire(self, *, activate_existing: bool) -> bool:
            return True

        def close(self) -> None:
            shutdowns.append("instance")

    class FakeViewModel:
        def __init__(self, *_args, **_kwargs) -> None:
            from types import SimpleNamespace
            self.claude_status = SimpleNamespace(start=lambda: None)
            self.codex_agent_focus = SimpleNamespace(requested=FakeSignal())

        def report_chatgpt_focus_failure(self, _message) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self) -> None:
            shutdowns.append("view-model")

        def attach_extension_platform(self, platform) -> None:
            self.extension_platform = platform

    class FakeExtensionPlatform:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            extension_starts.append("started")

    class FakeBackground:
        background_enabled = False

        def __init__(self, *_args, **kwargs) -> None:
            integrations.append(
                (
                    kwargs.get("codex_usage_monitor"),
                    kwargs.get("claude_usage_monitor"),
                )
            )

        def attach_window(self, _window) -> None:
            pass

        def show_window(self) -> None:
            pass

    class FakeWindow:
        def show(self) -> None:
            pass

    class FakeUsageMonitor:
        name = "usage"

        def start(self) -> None:
            shutdowns.append(f"{self.name}-started")

        def stop(self) -> None:
            shutdowns.append(self.name)

    class FakeCodexUsageMonitor(FakeUsageMonitor):
        name = "codex-usage"

    class FakeClaudeUsageMonitor(FakeUsageMonitor):
        name = "claude-usage"

    class FakeFocusActivator:
        def __init__(self, _parent):
            self.failed = FakeSignal()

        def activate(self, _agent):
            pass

        def shutdown(self):
            shutdowns.append("chatgpt-focus")

    import controller_config.codex_agent_focus as focus_module
    monkeypatch.setattr(focus_module, "MacChatGPTActivator", FakeFocusActivator)

    monkeypatch.setattr(app_module, "QApplication", FakeApplication)
    monkeypatch.setattr(app_module, "ApplicationInstanceCoordinator", FakeInstance)
    monkeypatch.setattr(app_module.Contract, "load", lambda: object())
    monkeypatch.setattr(app_module, "load_default_authenticator", lambda: authenticator)
    monkeypatch.setattr(app_module, "DemoGateway", lambda *_args: object())
    monkeypatch.setattr(
        app_module,
        "DeviceGateway",
        lambda _contract, value: device_authenticators.append(value) or object(),
    )
    monkeypatch.setattr(app_module, "LanguageManager", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(app_module, "MainViewModel", FakeViewModel)
    monkeypatch.setattr(
        app_module, "ExtensionPlatformController", FakeExtensionPlatform
    )
    monkeypatch.setattr(app_module, "PromptBackgroundController", FakeBackground)
    monkeypatch.setattr(app_module, "CodexUsageMonitor", FakeCodexUsageMonitor)
    monkeypatch.setattr(app_module, "ClaudeUsageMonitor", FakeClaudeUsageMonitor)
    monkeypatch.setattr(app_module, "MacClipboardPaster", lambda *_args: object())
    monkeypatch.setattr(app_module, "PromptHelperRuntime", lambda *_args: object())
    monkeypatch.setattr(app_module, "MacLaunchAgent", lambda *_args: object())
    monkeypatch.setattr(app_module, "MainWindow", lambda *_args, **_kwargs: FakeWindow())
    monkeypatch.setattr(app_module.sys, "platform", platform)

    arguments = [] if live_device else ["--demo", "ready"]
    assert app_module.main(arguments) == 0

    expected_shutdowns = [
        "codex-usage-started",
        "claude-usage-started",
        "chatgpt-focus",
        "view-model",
        "codex-usage",
        "claude-usage",
        "instance",
    ]
    if not usage_enabled:
        expected_shutdowns = ["view-model", "instance"]
    assert shutdowns == expected_shutdowns
    assert len(integrations) == 1
    codex_integration, claude_integration = integrations[0]
    assert isinstance(codex_integration, FakeCodexUsageMonitor) is usage_enabled
    assert isinstance(claude_integration, FakeClaudeUsageMonitor) is usage_enabled
    assert device_authenticators == ([authenticator] if live_device else [])
    assert extension_starts == []
