from __future__ import annotations

import copy

from controller_config.extensions.contracts import ExtensionContext
from controller_config.models import ScreenModel
from controller_config.prompt_device import PromptListenerStatus


def build_extension_context(
    *,
    model: ScreenModel,
    draft: object | None,
    listener: PromptListenerStatus,
    revision: int,
) -> ExtensionContext:
    """Build the JSON-only, read-only context exposed to local extensions."""

    snapshot = model.snapshot
    if snapshot is None:
        return ExtensionContext(
            revision=revision,
            device_serial=None,
            connection_state=model.state.value,
            identity={},
            compatibility={},
            capabilities={},
            status={},
            active_profile=None,
            config_summary={},
            draft={"available": False, "dirty": False, "change_count": 0},
            prompt_listener=_listener_mapping(listener),
        )

    identity = copy.deepcopy(snapshot.identity)
    serial_value = identity.get("serial")
    serial = serial_value if isinstance(serial_value, str) and serial_value else None
    config_result = snapshot.config_result
    config = snapshot.config
    profiles = config.get("profiles")
    profile_count = len(profiles) if isinstance(profiles, list) else 0
    config_summary = {
        "schema_version": snapshot.versions.get("schema_version"),
        "generation": config_result.get("generation"),
        "digest": config_result.get("digest"),
        "profile_count": profile_count,
    }
    draft_mapping: dict[str, object] = {
        "available": draft is not None,
        "dirty": False,
        "change_count": 0,
    }
    if draft is not None:
        draft_mapping.update(
            {
                "dirty": bool(getattr(draft, "is_dirty", False)),
                "change_count": len(tuple(getattr(draft, "changes", ()))),
                "base_generation": getattr(draft, "base_generation", None),
                "base_digest": getattr(draft, "base_digest", ""),
            }
        )

    return ExtensionContext(
        revision=revision,
        device_serial=serial,
        connection_state=model.state.value,
        identity=identity,
        compatibility=copy.deepcopy(snapshot.compatibility),
        capabilities=copy.deepcopy(snapshot.capabilities),
        status=copy.deepcopy(snapshot.status),
        active_profile=copy.deepcopy(snapshot.active_profile),
        config_summary=config_summary,
        draft=draft_mapping,
        prompt_listener=_listener_mapping(listener),
    )


def _listener_mapping(listener: PromptListenerStatus) -> dict[str, object]:
    return {
        "state": listener.state.value,
        "online": listener.online,
        "message": listener.message,
    }
