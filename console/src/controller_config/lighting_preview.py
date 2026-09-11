from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from controller_config.protocol.bootstrap import Command


SET_LIGHTING_PREVIEW = "SET_LIGHTING_PREVIEW"
CLEAR_LIGHTING_PREVIEW = "CLEAR_LIGHTING_PREVIEW"
SET_LIGHTING_PREVIEW_MESSAGE_TYPE = 0x26
CLEAR_LIGHTING_PREVIEW_MESSAGE_TYPE = 0x27


class LightingPreviewError(ValueError):
    """A lighting preview payload or response does not match the protocol."""


@dataclass(frozen=True)
class LightingPreviewStatus:
    supported: bool = False
    requested: bool = False
    active: bool = False
    pending: bool = False
    message: str = "当前固件不支持实时预览"
    technical: str = ""
    candidate: dict[str, Any] | None = None


def build_lighting_preview_snapshot(
    lighting: dict[str, Any],
    *,
    under_key_rgb_count: int,
) -> dict[str, Any]:
    """Extract and validate the independent volatile preview snapshot."""

    if not isinstance(lighting, dict):
        raise LightingPreviewError("灯光预览必须是 object")
    snapshot = {
        "enabled": lighting.get("enabled"),
        "brightness": lighting.get("brightness"),
        "under_key": copy.deepcopy(lighting.get("under_key")),
    }
    validate_lighting_preview_snapshot(
        snapshot,
        under_key_rgb_count=under_key_rgb_count,
    )
    return snapshot


def validate_lighting_preview_snapshot(
    snapshot: dict[str, Any],
    *,
    under_key_rgb_count: int,
) -> None:
    if not isinstance(snapshot, dict):
        raise LightingPreviewError("灯光预览必须是 object")
    if set(snapshot) != {"enabled", "brightness", "under_key"}:
        raise LightingPreviewError(
            "灯光预览只能包含 enabled、brightness 和 under_key"
        )
    if not isinstance(snapshot.get("enabled"), bool):
        raise LightingPreviewError("enabled 必须是布尔值")
    brightness = snapshot.get("brightness")
    if (
        not isinstance(brightness, int)
        or isinstance(brightness, bool)
        or not 0 <= brightness <= 100
    ):
        raise LightingPreviewError("brightness 必须是 0..100 的整数")
    if (
        not isinstance(under_key_rgb_count, int)
        or isinstance(under_key_rgb_count, bool)
        or under_key_rgb_count <= 0
    ):
        raise LightingPreviewError("设备没有返回有效的 under_key_rgb_count")
    under_key = snapshot.get("under_key")
    if not isinstance(under_key, list) or len(under_key) != under_key_rgb_count:
        raise LightingPreviewError(
            f"under_key 必须包含 {under_key_rgb_count} 项"
        )
    for index, color in enumerate(under_key):
        if not isinstance(color, dict) or set(color) != {"r", "g", "b"}:
            raise LightingPreviewError(
                f"under_key[{index}] 必须只包含 r、g、b"
            )
        for channel in ("r", "g", "b"):
            value = color.get(channel)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 255
            ):
                raise LightingPreviewError(
                    f"under_key[{index}].{channel} 必须是 0..255 的整数"
                )


def set_lighting_preview_command(snapshot: dict[str, Any]) -> Command:
    return Command(
        SET_LIGHTING_PREVIEW,
        SET_LIGHTING_PREVIEW_MESSAGE_TYPE,
        copy.deepcopy(snapshot),
        timeout_ms=1200,
    )


def clear_lighting_preview_command() -> Command:
    return Command(
        CLEAR_LIGHTING_PREVIEW,
        CLEAR_LIGHTING_PREVIEW_MESSAGE_TYPE,
        {},
        timeout_ms=1200,
    )


def validate_lighting_preview_response(
    command_name: str,
    payload: dict[str, Any],
) -> None:
    if command_name not in {SET_LIGHTING_PREVIEW, CLEAR_LIGHTING_PREVIEW}:
        raise LightingPreviewError(f"未知灯光预览命令 {command_name}")
    if payload.get("command") != command_name:
        raise LightingPreviewError(f"响应 command 与请求 {command_name} 不匹配")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise LightingPreviewError(f"{command_name} result 必须是 object")
    expected = "ACTIVE" if command_name == SET_LIGHTING_PREVIEW else "CLEARED"
    if result.get("state") != expected or set(result) != {"state"}:
        raise LightingPreviewError(
            f"{command_name} result.state 必须是 {expected}"
        )
