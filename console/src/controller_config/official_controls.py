from __future__ import annotations


MATRIX12_HARDWARE_IDS = frozenset(
    {"WMP-S3-MATRIX12-V1", "WMP-S3-MATRIX12-POWER-V2"}
)
MATRIX12_AGENT_STATUS_KEY_ORDER = (
    "key.1", "key.2", "key.4", "key.5", "key.6", "key.7"
)
MATRIX12_AGENT_STATUS_KEYS = frozenset(MATRIX12_AGENT_STATUS_KEY_ORDER)
MATRIX12_CODEX_KEY_NAMES = {
    "key.1": "Agent 1",
    "key.2": "Agent 2",
    "key.3": "Fork",
    "key.4": "Agent 3",
    "key.5": "Agent 4",
    "key.6": "Agent 5",
    "key.7": "Agent 6",
    "key.8": "Dictate",
    "key.9": "Approve",
    "key.10": "Reject",
    "key.11": "Send",
    "key.12": "Stop",
}


def is_matrix12_official_status_key(hardware_id: str, control_id: str) -> bool:
    return (
        hardware_id in MATRIX12_HARDWARE_IDS
        and control_id in MATRIX12_AGENT_STATUS_KEYS
    )
