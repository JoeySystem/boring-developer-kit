"""Operator-facing Chinese text for low-level serial and update errors.

The original message is kept in parentheses so the history files and the
technical log still show exactly what the system reported.
"""

from __future__ import annotations

import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), text)
    for pattern, text in (
        (
            r"\b433\b|指定不存在的设备|device (?:is )?not connected",
            "电脑没有重新识别到设备（Windows 错误 433）。请拔下 USB 线，等 2 秒再插回后重试",
        ),
        (
            r"could not open port|filenotfounderror|系统找不到指定的文件|no such file or directory",
            "串口已经不存在：设备可能被拔下或正在重启。请确认 USB 已插好后重试",
        ),
        (
            r"write timeout|timed out|timeout waiting|did not respond",
            "设备没有在规定时间内响应。请检查 USB 线和接口后重试",
        ),
        (
            r"device reported a failed firmware",
            "设备报告固件写入失败。请重新开始；同一台连续失败请单独放置",
        ),
        (
            r"did not finish firmware finalization in time",
            "设备校验新固件超时。请重新开始",
        ),
        (
            r"does not match the OTA package|product_id does not match|hardware_id does not match",
            "这台设备不是本工具对应的产品型号，不能用这个固件包",
        ),
        (
            r"does not support safe USB updates",
            "设备上的固件不支持 USB 安全更新，需要用产线工具重新烧录",
        ),
        (
            r"different interrupted update",
            "设备里有一次未完成的其他更新。请拔下 USB 再插回后重新开始",
        ),
    )
)


def friendly_error(message: str) -> str:
    """Translate a known low-level error; leave already-explained ones alone."""
    text = (message or "").strip()
    if not text:
        return "未知原因"
    for pattern, explanation in _RULES:
        if pattern.search(text):
            if text.startswith(explanation):
                return text
            return f"{explanation}（原始信息：{text}）"
    return text


def is_stale_device_error(message: str) -> bool:
    return bool(_RULES[0][0].search(message or ""))


__all__ = ["friendly_error", "is_stale_device_error"]
