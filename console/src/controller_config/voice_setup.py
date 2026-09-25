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
    if mode == "codex":
        return f"ui/codex_voice_input_software/{serial}/{profile_id}"
    return "ui/voice_input_software"


def voice_provider(settings, serial=None, profile_id=None, mode="normal", default="other"):
    """Read the scoped choice, falling back to the pre-scope global choice."""
    if settings is None:
        return default
    selected = settings.value(voice_provider_key(serial, profile_id, mode), "")
    if not selected and mode == "codex":
        selected = settings.value("ui/voice_input_software", "")
    return selected or default


def _confirmed_provider_key(serial, profile_id, provider, mode):
    return f"ui/confirmed_voice_provider/{serial}/{profile_id}/{mode}/{provider}"


def confirmed_voice_choice(settings, serial, profile_id, *, mode="codex"):
    """Last device-read shortcut and its confirmed input software, not a draft."""
    if settings is None:
        return None
    try:
        record = json.loads(settings.value(f"ui/confirmed_voice/{serial}/{profile_id}/{mode}", "null"))
    except (TypeError, ValueError):
        return None
    if (isinstance(record, dict) and isinstance(record.get("action"), dict)
            and record["action"].get("type") == "key_gesture"):
        return record
    return None


def confirmed_voice_choice_for_provider(settings, serial, profile_id, provider, *, mode="codex"):
    """Return the last read-back shortcut confirmed for one input app.

    Provider changes are user-facing configuration switches.  Remembering each
    app separately prevents a Qianwen/Doubao modifier key from being presented
    as the user's Typeless shortcut when they switch back later.
    """
    if settings is None or not provider:
        return None
    try:
        record = json.loads(settings.value(
            _confirmed_provider_key(serial, profile_id, provider, mode), "null"))
    except (TypeError, ValueError):
        record = None
    if (isinstance(record, dict) and record.get("provider") == provider
            and isinstance(record.get("action"), dict)
            and record["action"].get("type") == "key_gesture"):
        return record
    # Existing installations have one last-confirmed record.  Reuse it only
    # when it names this provider; a record for another app is not compatible.
    latest = confirmed_voice_choice(settings, serial, profile_id, mode=mode)
    return latest if latest is not None and latest.get("provider") == provider else None


def save_confirmed_voice_choice(settings, serial, profile_id, action, provider, *, mode="codex"):
    if settings is None or action.get("type") != "key_gesture":
        return
    record = {"action": action, "provider": provider}
    changed = False
    if confirmed_voice_choice(settings, serial, profile_id, mode=mode) != record:
        settings.setValue(f"ui/confirmed_voice/{serial}/{profile_id}/{mode}",
                          json.dumps(record, ensure_ascii=False))
        changed = True
    if provider:
        provider_key = _confirmed_provider_key(serial, profile_id, provider, mode)
        try:
            provider_record = json.loads(settings.value(provider_key, "null"))
        except (TypeError, ValueError):
            provider_record = None
        if provider_record != record:
            settings.setValue(provider_key, json.dumps(record, ensure_ascii=False))
            changed = True
        # The visible selection must follow a successful read-back/confirmation,
        # rather than a dropdown choice that the user may cancel.
        selection_key = voice_provider_key(serial, profile_id, mode)
        if settings.value(selection_key, "") != provider:
            settings.setValue(selection_key, provider)
            changed = True
    if changed:
        settings.sync()


def voice_mapping(snapshot, control, mode="normal"):
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
    provider = voice_provider(settings, serial, profile_id, mode)
    if (isinstance(record, dict) and record.get("action") == _gesture(action)
            and record.get("provider") == provider):
        return record
    return None


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


def recommended_voice_action(action, provider, platform):
    """Return the tested starter shortcut for a known macOS voice app.

    Keep double-click send independent.  Users with a confirmed custom shortcut
    keep it; this preset only removes the need to discover and retype a shortcut
    when they select a provider for the first time.
    """
    import copy
    result = copy.deepcopy(action)
    if platform != "darwin":
        return result
    usage = {
        "typeless": 104,  # F13
        "doubao": 228,    # Right Control
        "qianwen": 230,   # Right Option
    }.get(provider)
    if usage is None:
        return result
    result["usage"] = usage
    result.pop("modifiers", None)
    return result
