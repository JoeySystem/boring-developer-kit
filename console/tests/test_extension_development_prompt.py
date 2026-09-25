from controller_config.extensions.context import build_extension_context
from controller_config.extensions.development_prompt import (
    build_extension_development_prompt,
)
from controller_config.models import AppState, ScreenModel
from controller_config.prompt_device import PromptListenerStatus


def test_development_prompt_is_bounded_and_contains_extension_contract() -> None:
    context = build_extension_context(
        model=ScreenModel(AppState.NO_DEVICE, "No device"),
        draft=None,
        listener=PromptListenerStatus(),
        revision=3,
    )

    prompt = build_extension_development_prompt(context)

    assert "最多 3 个问题" in prompt
    assert "boring-extension.json" in prompt
    assert "boring_console_sdk" in prompt
    assert "不能阻止默认动作" in prompt
    assert "禁止打开设备串口" in prompt
    assert "不要调用 sys.executable" in prompt
    assert 'api_version 必须为 {"major":1,"minor":1}' in prompt
    assert 'event.kind=host_action.triggered' in prompt
    assert 'task_token' in prompt
    assert 'next_cancellation(timeout_ms=0)' in prompt
    assert '不要让用户填写占位提示词' in prompt
    assert 'BORING Extension API: 1.0\n' not in prompt


def test_development_prompt_localizes_before_inserting_device_context(monkeypatch) -> None:
    import controller_config.extensions.development_prompt as development_prompt
    from controller_config.text_catalog import get_text_catalog

    context = build_extension_context(
        model=ScreenModel(AppState.NO_DEVICE, "No device"),
        draft=None,
        listener=PromptListenerStatus(),
        revision=3,
    )
    catalog = get_text_catalog()
    expected = {
        "zh_CN": ("最多 3 个问题", "设备未连接"),
        "en_US": ("no more than 3 questions", "Device Not Connected"),
        "ja_JP": ("質問を最大 3 つ", "デバイス未接続"),
    }
    for language, (question, device) in expected.items():
        monkeypatch.setattr(
            development_prompt,
            "translate_ui_text",
            lambda text, language=language: catalog.translate(text, language),
        )
        prompt = development_prompt.build_extension_development_prompt(context)
        assert question in prompt
        assert f"device_serial: {device}" in prompt
        assert '{"major":1,"minor":1}' in prompt
        assert "{device}" not in prompt
        assert "{hardware}" not in prompt
        for protocol_detail in (
            "700ms", "60", "8–12", "1–255",
            "event.kind=host_action.triggered", "source=usb.host_action",
            "next_cancellation(timeout_ms=0)", "sys.executable",
        ):
            assert protocol_detail in prompt
