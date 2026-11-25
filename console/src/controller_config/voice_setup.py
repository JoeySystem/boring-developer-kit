"""Remember user-confirmed voice trials for the device mapping that was tried."""
import json


def _gesture(action):
    return {
        "type": action.get("type"),
        "usage": action.get("usage"),
        "modifiers": sorted(action.get("modifiers", [])),
        "double_usage": action.get("double_usage"),
        "double_modifiers": sorted(action.get("double_modifiers", [])),
    }


def _trials(settings):
    if settings is None:
        return {}
    try:
        result = json.loads(settings.value("ui/voice_trials", "{}"))
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def voice_provider_key(serial=None, profile_id=None, mode="normal"):
    if mode == "global":
        return f"ui/global_voice_input_software/{serial}"
    if mode == "codex":
        return f"ui/codex_voice_input_software/{serial}/{profile_id}"
    return "ui/voice_input_software"


def confirmed_voice_choice(settings, serial, profile_id, *, mode="codex"):
    """Last device-read shortcut and its confirmed input software, not a draft."""
    if settings is None:
        return None
    try:
        record = json.loads(settings.value(f"ui/confirmed_voice/{serial}/{profile_id}/{mode}", "null"))
    except (TypeError, ValueError):
        return None
    if (isinstance(record, dict) and isinstance(record.get("action"), dict)
            and record["action"].get("type") in {"key", "key_gesture"}):
        return record
    return None


def save_confirmed_voice_choice(settings, serial, profile_id, action, provider, *, mode="codex"):
    if settings is None or action.get("type") not in {"key", "key_gesture"}:
        return
    record = {"action": action, "provider": provider}
    if confirmed_voice_choice(settings, serial, profile_id, mode=mode) == record:
        return
    settings.setValue(f"ui/confirmed_voice/{serial}/{profile_id}/{mode}",
                      json.dumps(record, ensure_ascii=False))
    settings.sync()


def voice_mapping(snapshot, control, mode="normal"):
    if (control == "key.8" and mode in {"normal", "codex"}
            and isinstance(snapshot.config.get("voice_action"), dict)):
        return {"short_name": "语音输入", "action": snapshot.config["voice_action"]}
    if mode == "codex":
        return {"short_name": "", "action": (snapshot.active_profile or {}).get("codex_voice", {"type": "none"})}
    return snapshot.mappings.get(control, {})


def voice_draft_is_scoped(config, snapshot, control, mode="normal"):
    """Allow a retained edit of this key, without silently saving other work."""
    import copy
    candidate = copy.deepcopy(snapshot.config)
    profile_id = snapshot.active_profile_id
    edited = next((p for p in config["profiles"] if p["id"] == profile_id), None)
    original = next((p for p in candidate["profiles"] if p["id"] == profile_id), None)
    if edited is None or original is None:
        return False
    if mode == "codex":
        if "codex_voice" in edited:
            original["codex_voice"] = copy.deepcopy(edited["codex_voice"])
        else:
            original.pop("codex_voice", None)
    else:
        mapping = next((m for m in edited["mappings"] if m["control_id"] == control), None)
        mappings = original["mappings"]
        index = next((i for i, m in enumerate(mappings) if m["control_id"] == control), None)
        if index is not None:
            if mapping is None:
                mappings.pop(index)
            else:
                mappings[index] = copy.deepcopy(mapping)
        elif mapping is not None:
            mappings.append(copy.deepcopy(mapping))
    return candidate == config


def voice_trial(settings, serial, profile_id, control, action, *, mode="normal"):
    control = f"codex/{control}" if mode == "codex" else control
    record = _trials(settings).get(f"{serial}/{profile_id}/{control}")
    provider = settings.value(voice_provider_key(serial, profile_id, mode), "other") if settings is not None else "other"
    if (isinstance(record, dict) and record.get("action") == _gesture(action)
            and record.get("provider") == provider):
        return record
    return None


def voice_trial_state(settings, serial, profile_id, control, action, *, mode="normal"):
    """Return ``valid``, ``stale`` or ``none`` for this exact mapping slot.

    A stale record is retained so restoring the previously verified action or
    provider can make it valid again.  This is historical evidence, not a live
    assertion about the input method's current system permissions.
    """
    scoped_control = f"codex/{control}" if mode == "codex" else control
    record = _trials(settings).get(f"{serial}/{profile_id}/{scoped_control}")
    if not isinstance(record, dict):
        return "none"
    provider = settings.value(voice_provider_key(serial, profile_id, mode), "other") if settings is not None else "other"
    if record.get("action") == _gesture(action) and record.get("provider") == provider:
        return "valid"
    return "stale"


def remove_voice_trial(settings, serial, profile_id, control, action, provider=None, *, mode="normal"):
    """Remove only the currently matching trial after an explicit failure."""
    if settings is None:
        return False
    scoped_control = f"codex/{control}" if mode == "codex" else control
    key = f"{serial}/{profile_id}/{scoped_control}"
    records = _trials(settings)
    record = records.get(key)
    expected_provider = provider or settings.value(
        voice_provider_key(serial, profile_id, mode), "other"
    )
    if not (isinstance(record, dict)
            and record.get("action") == _gesture(action)
            and record.get("provider") == expected_provider):
        return False
    del records[key]
    settings.setValue("ui/voice_trials", json.dumps(records, ensure_ascii=False))
    settings.sync()
    return True


def save_voice_trial(settings, serial, profile_id, control, action, provider, target, *, mode="normal"):
    control = f"codex/{control}" if mode == "codex" else control
    if settings is None:
        return
    records = _trials(settings)
    records[f"{serial}/{profile_id}/{control}"] = {
        "action": _gesture(action), "provider": provider, "target": target,
    }
    settings.setValue("ui/voice_trials", json.dumps(records, ensure_ascii=False))
    settings.sync()


def adapted_voice_action(action, provider, platform):
    """Use shortcuts exposed by the inspected macOS IME settings.

    Preserve a compatible user shortcut and the independent send gesture.
    Other platforms and apps keep their existing configurable shortcut.
    """
    import copy
    result = copy.deepcopy(action)
    if platform != "darwin" or provider not in {"doubao", "qianwen"}:
        return result
    usage = action.get("usage", 0)
    modifiers = action.get("modifiers", [])
    compatible = (224 <= usage <= 231 and usage not in {225, 229}
                  and all(m in {224, 226, 227, 228, 230, 231} for m in modifiers))
    if provider == "qianwen":
        compatible = usage in {230, 231} and not modifiers
    if not compatible:
        result["usage"] = 228 if provider == "doubao" else 230
        result.pop("modifiers", None)
    return result
