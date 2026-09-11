from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from controller_config.protocol.framing import canonical_json_bytes


HAPTIC_OPTIONAL_CHANNELS = ("on_encoder", "on_joystick", "on_task")


def configs_match_readback(candidate: dict, actual: dict) -> bool:
    """Compare effective channel defaults without changing the wire digest."""
    def normalized(config: dict) -> bytes:
        config = copy.deepcopy(config)
        haptic = config.get("haptic")
        if isinstance(haptic, dict):
            for field in HAPTIC_OPTIONAL_CHANNELS:
                haptic.setdefault(field, True)
        return canonical_json_bytes(config)

    return normalized(candidate) == normalized(actual)


class ConfigTransactionState(str, Enum):
    IDLE = "idle"
    VALIDATING = "validating"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    WRITING = "writing"
    PENDING = "pending"
    VERIFYING = "verifying"
    ACTIVE = "active"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class ConfigTransaction:
    state: ConfigTransactionState = ConfigTransactionState.IDLE
    message: str = "尚未开始设备写入"
    technical: str = ""
    candidate_digest: str = ""
    base_generation: int | None = None
    base_digest: str = ""
    candidate_config: dict[str, Any] = field(default_factory=dict)
    device_serial: str = ""

    @property
    def is_busy(self) -> bool:
        return self.state in {
            ConfigTransactionState.VALIDATING,
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
        }

    @property
    def blocks_editing(self) -> bool:
        return self.state in {
            ConfigTransactionState.WRITING,
            ConfigTransactionState.PENDING,
            ConfigTransactionState.VERIFYING,
            ConfigTransactionState.UNKNOWN,
        }

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            ConfigTransactionState.ACTIVE,
            ConfigTransactionState.FAILED,
            ConfigTransactionState.UNKNOWN,
            ConfigTransactionState.CONFLICT,
        }
