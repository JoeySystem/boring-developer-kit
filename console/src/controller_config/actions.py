from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from controller_config.protocol.contract import Contract, ContractError


ACTION_LABELS = {
    "key": "键盘按键",
    "consumer": "媒体与系统",
    "mouse": "鼠标",
    "macro": "按键序列",
    "prompt": "提示词",
    "profile": "切换 Profile",
    "device": "设备功能",
    "none": "无动作",
}

FIELD_LABELS = {
    "usage": "Usage",
    "modifiers": "修饰键 Usage",
    "button": "鼠标按键",
    "x": "水平移动",
    "y": "垂直移动",
    "wheel": "滚轮",
    "pan": "横向滚动",
    "macro_id": "按键序列",
    "prompt_id": "快捷提示词",
    "profile_id": "目标 Profile",
    "name": "设备功能",
}

KEY_USAGE_LABELS = {
    39: "0",
    40: "Enter",
    41: "Esc",
    42: "Backspace",
    43: "Tab",
    44: "Space",
    45: "-",
    46: "=",
    47: "[",
    48: "]",
    49: "\\",
    51: ";",
    52: "'",
    53: "`",
    54: ",",
    55: ".",
    56: "/",
    57: "Caps Lock",
    70: "Print Screen",
    71: "Scroll Lock",
    72: "Pause",
    73: "Insert",
    74: "Home",
    75: "Page Up",
    76: "Delete",
    77: "End",
    78: "Page Down",
    79: "→",
    80: "←",
    81: "↓",
    82: "↑",
    83: "Num Lock",
    100: "非美式键盘 \\ / |",
    101: "Menu",
    102: "Power",
    103: "数字键盘 =",
    116: "Execute",
    117: "Help",
    118: "Menu",
    119: "Select",
    120: "Stop",
    121: "Again",
    122: "Undo",
    123: "Cut",
    124: "Copy",
    125: "Paste",
    126: "Find",
    127: "Mute",
    128: "Volume Up",
    129: "Volume Down",
    133: "数字键盘 ,",
    134: "数字键盘 =（AS/400）",
}

CONSUMER_USAGE_LABELS = {
    0x0000: "未分配",
    0x00B0: "播放",
    0x00B1: "暂停",
    0x00B2: "录制",
    0x00B3: "快进",
    0x00B4: "快退",
    0x00B5: "下一首",
    0x00B6: "上一首",
    0x00B7: "停止播放",
    0x00B8: "弹出介质",
    0x00CD: "播放 / 暂停",
    0x00E2: "静音",
    0x00E9: "音量提高",
    0x00EA: "音量降低",
}

DEVICE_ACTION_LABELS = {
    "macro_cancel": "取消按键序列",
    "lighting_toggle": "切换灯光",
    "haptic_toggle": "切换震动",
    "display_next": "切换屏幕页面",
}


@dataclass(frozen=True)
class ActionField:
    name: str
    label: str
    value_type: str
    required: bool
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[Any, ...] = ()
    max_items: int | None = None
    unique: bool = False

    def default_value(self) -> Any:
        if self.choices:
            return self.choices[0]
        if self.value_type == "array":
            return []
        return self.minimum if self.minimum is not None else 0


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    fields: tuple[ActionField, ...]

    def default_action(self) -> dict[str, Any]:
        action: dict[str, Any] = {"type": self.action_type}
        for field in self.fields:
            if field.required:
                action[field.name] = field.default_value()
        return action


def action_field_choices(
    action_type: str,
    field: ActionField,
    current_value: object,
    *,
    platform: str = "",
) -> tuple[tuple[str, Any], ...]:
    """Return user-facing choices without changing the protocol value."""
    if action_type == "key" and field.name == "usage":
        minimum = field.minimum if field.minimum is not None else 0
        maximum = field.maximum if field.maximum is not None else -1
        choices = [
            (label, usage)
            for usage in range(minimum, maximum + 1)
            if not (label := _key_usage_label(usage, platform, False)).startswith("键盘 Usage")
        ]
        current = _integer_or_none(current_value)
        if current is not None and all(value != current for _label, value in choices):
            choices.insert(0, (f"当前高级值 · Usage {current}", current))
        return tuple(choices)
    if action_type == "consumer" and field.name == "usage":
        choices = [(label, usage) for usage, label in CONSUMER_USAGE_LABELS.items()]
        current = _integer_or_none(current_value)
        if current is not None and all(value != current for _label, value in choices):
            choices.insert(0, (f"当前高级值 · Usage {current}", current))
        return tuple(choices)
    if field.choices:
        return tuple(
            (
                DEVICE_ACTION_LABELS.get(str(value), str(value))
                if action_type == "device" and field.name == "name"
                else str(value),
                value,
            )
            for value in field.choices
        )
    return ()


def action_field_label(action_type: str, field_name: str) -> str:
    labels = {
        ("key", "usage"): "按键",
        ("key", "modifiers"): "组合键",
        ("consumer", "usage"): "媒体功能",
        ("device", "name"): "设备功能",
    }
    return labels.get((action_type, field_name), FIELD_LABELS.get(field_name, field_name))


def modifier_choice_label(usage: int, platform: str = "") -> str:
    side = "左" if usage < 228 else "右"
    return f"{side} {_modifier_label(usage, platform, False)}"


def action_definitions(
    contract: Contract,
    supported_actions: tuple[str, ...],
) -> tuple[ActionDefinition, ...]:
    action_schema = contract.config_schema.get("$defs", {}).get("action", {})
    variants = action_schema.get("oneOf")
    if not isinstance(variants, list):
        raise ContractError("配置 Schema 缺少 $defs.action.oneOf")

    definitions: list[ActionDefinition] = []
    supported = set(supported_actions)
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        properties = variant.get("properties")
        if not isinstance(properties, dict):
            continue
        type_schema = properties.get("type")
        action_type = type_schema.get("const") if isinstance(type_schema, dict) else None
        if not isinstance(action_type, str) or action_type not in supported:
            continue
        required = set(variant.get("required", ()))
        fields = tuple(
            _field_definition(name, schema, name in required)
            for name, schema in properties.items()
            if name != "type" and isinstance(schema, dict)
        )
        definitions.append(
            ActionDefinition(
                action_type=action_type,
                label=ACTION_LABELS.get(action_type, action_type),
                fields=fields,
            )
        )
    return tuple(definitions)


def describe_action(
    action: object,
    *,
    platform: str = "",
    compact: bool = False,
) -> str:
    """Return a user-facing description derived from the protocol action."""
    if not isinstance(action, dict):
        return "未映射"
    action_type = action.get("type")
    if action_type == "key":
        usage = _integer_or_none(action.get("usage"))
        if usage is None:
            return "键盘动作无效"
        key = _key_usage_label(usage, platform, compact)
        modifiers = action.get("modifiers")
        modifier_values = modifiers if isinstance(modifiers, list) else []
        modifier_labels = [
            _modifier_label(value, platform, compact)
            for value in modifier_values
            if _integer_or_none(value) is not None
        ]
        if not modifier_labels:
            return key
        separator = "" if compact and platform.lower() == "macos" else " + "
        if compact and separator:
            separator = "+"
        return separator.join((*modifier_labels, key))
    if action_type == "consumer":
        usage = _integer_or_none(action.get("usage"))
        if usage is None:
            return "媒体动作无效"
        return CONSUMER_USAGE_LABELS.get(usage, f"媒体 Usage {usage}")
    if action_type == "mouse":
        return _describe_mouse_action(action, compact)
    if action_type == "macro":
        return f"运行按键序列 {action.get('macro_id', '—')}"
    if action_type == "prompt":
        return f"粘贴提示词 {action.get('prompt_id', '—')}"
    if action_type == "profile":
        return f"切换到 Profile {action.get('profile_id', '—')}"
    if action_type == "device":
        name = action.get("name")
        return DEVICE_ACTION_LABELS.get(str(name), f"设备功能 {name}")
    if action_type == "none":
        return "未映射"
    return f"未知动作 {action_type}" if action_type is not None else "未映射"


def _key_usage_label(usage: int, platform: str, compact: bool) -> str:
    if 4 <= usage <= 29:
        return chr(ord("A") + usage - 4)
    if 30 <= usage <= 38:
        return str(usage - 29)
    if 58 <= usage <= 69:
        return f"F{usage - 57}"
    if 84 <= usage <= 99:
        keypad_labels = (
            "/",
            "*",
            "-",
            "+",
            "Enter",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "0",
            ".",
        )
        return f"数字键盘 {keypad_labels[usage - 84]}"
    if 104 <= usage <= 115:
        return f"F{usage - 91}"
    if 224 <= usage <= 231:
        return _modifier_label(usage, platform, compact)
    if compact and usage == 42:
        return "退格"
    if compact and usage == 75:
        return "PgUp"
    if compact and usage == 78:
        return "PgDn"
    return KEY_USAGE_LABELS.get(usage, f"键盘 Usage {usage}")


def _modifier_label(usage: object, platform: str, compact: bool) -> str:
    value = _integer_or_none(usage)
    if value is None:
        return "修饰键"
    kind = (value - 224) % 4
    normalized_platform = platform.lower()
    if normalized_platform == "macos":
        compact_labels = ("⌃", "⇧", "⌥", "⌘")
        full_labels = ("Control", "Shift", "Option", "Command")
    else:
        compact_labels = (
            "Ctrl",
            "Shift",
            "Alt",
            "Win" if normalized_platform in {"windows", "windows_linux"} else "GUI",
        )
        full_labels = compact_labels
    return compact_labels[kind] if compact else full_labels[kind]


def _describe_mouse_action(action: dict[str, Any], compact: bool) -> str:
    wheel = _integer_or_none(action.get("wheel")) or 0
    pan = _integer_or_none(action.get("pan")) or 0
    x = _integer_or_none(action.get("x")) or 0
    y = _integer_or_none(action.get("y")) or 0
    buttons = _integer_or_none(action.get("button")) or 0
    parts: list[str] = []
    if wheel:
        direction = "↑" if wheel > 0 else "↓"
        parts.append(f"滚轮{direction}" if compact else f"滚轮向{'上' if wheel > 0 else '下'}")
    if pan:
        direction = "→" if pan > 0 else "←"
        parts.append(f"横滚{direction}" if compact else f"横向滚动向{'右' if pan > 0 else '左'}")
    if x or y:
        parts.append(f"移动 {x},{y}" if compact else f"鼠标移动 X {x} / Y {y}")
    if buttons:
        pressed = [str(index + 1) for index in range(5) if buttons & (1 << index)]
        parts.append(f"鼠标键 {','.join(pressed) if pressed else buttons}")
    return " + ".join(parts) if parts else "鼠标无动作"


def _field_definition(name: str, schema: dict[str, Any], required: bool) -> ActionField:
    choices = schema.get("enum")
    if isinstance(choices, list):
        return ActionField(
            name=name,
            label=FIELD_LABELS.get(name, name),
            value_type="enum",
            required=required,
            choices=tuple(choices),
        )
    if schema.get("type") == "array":
        items = schema.get("items")
        item_schema = items if isinstance(items, dict) else {}
        return ActionField(
            name=name,
            label=FIELD_LABELS.get(name, name),
            value_type="array",
            required=required,
            minimum=_integer_or_none(item_schema.get("minimum")),
            maximum=_integer_or_none(item_schema.get("maximum")),
            max_items=_integer_or_none(schema.get("maxItems")),
            unique=schema.get("uniqueItems") is True,
        )
    return ActionField(
        name=name,
        label=FIELD_LABELS.get(name, name),
        value_type=str(schema.get("type", "integer")),
        required=required,
        minimum=_integer_or_none(schema.get("minimum")),
        maximum=_integer_or_none(schema.get("maximum")),
    )


def _integer_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
