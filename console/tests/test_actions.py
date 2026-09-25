from __future__ import annotations

from controller_config.actions import action_definitions, describe_action


def test_action_definitions_come_from_authoritative_schema_and_capabilities(contract) -> None:
    definitions = action_definitions(
        contract,
        ("key", "consumer", "mouse", "macro", "profile", "device", "none"),
    )

    assert [definition.action_type for definition in definitions] == [
        "key",
        "consumer",
        "mouse",
        "macro",
        "profile",
        "device",
        "none",
    ]
    key = definitions[0]
    usage = next(field for field in key.fields if field.name == "usage")
    modifiers = next(field for field in key.fields if field.name == "modifiers")
    assert (usage.minimum, usage.maximum) == (4, 231)
    assert (modifiers.minimum, modifiers.maximum) == (224, 231)

    device = next(item for item in definitions if item.action_type == "device")
    name = next(field for field in device.fields if field.name == "name")
    assert name.choices == (
        "macro_cancel",
        "lighting_toggle",
        "haptic_toggle",
        "display_next",
    )

    for definition in definitions:
        contract.validate_action(definition.default_action())


def test_action_definitions_filter_unsupported_device_actions(contract) -> None:
    definitions = action_definitions(contract, ("key", "none"))

    assert [definition.action_type for definition in definitions] == ["key", "none"]


def test_describe_action_translates_real_keyboard_and_consumer_actions() -> None:
    assert describe_action(
        {"type": "key", "usage": 17, "modifiers": [227]},
        platform="macos",
    ) == "Command + N"
    assert describe_action(
        {"type": "key", "usage": 7, "modifiers": [224, 225]},
        platform="macos",
        compact=True,
    ) == "⌃⇧D"
    assert describe_action({"type": "key", "usage": 104}) == "F13"
    assert describe_action({"type": "key", "usage": 42}, compact=True) == "退格"
    assert describe_action({"type": "key", "usage": 75}, compact=True) == "PgUp"
    assert describe_action({"type": "consumer", "usage": 233}) == "音量提高"
    assert describe_action({"type": "consumer", "usage": 234}) == "音量降低"


def test_encoder_copy_preserves_custom_actions_and_names():
    from controller_config.actions import mapping_display_name
    scroll = {'type': 'mouse', 'wheel': 1}
    assert describe_action(scroll, control_id='encoder.ccw') == '页面滚动'
    assert describe_action(scroll, control_id='key.8') == '滚轮向上'
    assert mapping_display_name('encoder.ccw', {'short_name': '浏览资料', 'action': scroll}) == '浏览资料'
    shortcut = {'type': 'key', 'usage': 75}
    assert describe_action(shortcut, control_id='encoder.ccw') == describe_action(shortcut)
    assert mapping_display_name('encoder.ccw', {'short_name': 'Scroll up', 'action': shortcut}) == 'Scroll up'
    combined = {**scroll, 'button': 1}
    assert describe_action(combined, control_id='encoder.ccw') == describe_action(combined)


def test_describe_action_covers_mouse_and_non_keyboard_protocol_actions() -> None:
    assert describe_action(
        {"type": "mouse", "button": 0, "x": 0, "y": 0, "wheel": 1, "pan": 0}
    ) == "滚轮向上"
    assert describe_action({"type": "macro", "macro_id": 3}) == "运行按键序列 3"
    assert describe_action({"type": "prompt", "prompt_id": 4}) == "粘贴提示词 4"
    assert describe_action({"type": "profile", "profile_id": 2}) == "切换到 Profile 2"
    assert describe_action({"type": "device", "name": "macro_cancel"}) == "取消按键序列"
    assert describe_action({"type": "none"}) == "未映射"
    assert describe_action({"type": "key", "usage": 200}) == "键盘 Usage 200"
