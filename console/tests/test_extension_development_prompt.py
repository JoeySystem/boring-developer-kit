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
