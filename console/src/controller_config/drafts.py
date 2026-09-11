from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from controller_config.models import DeviceSnapshot
from controller_config.protocol.contract import ConfigEditorRules, Contract, ContractError
from controller_config.protocol.framing import canonical_json_bytes


@dataclass(frozen=True)
class DraftChange:
    path: str
    before: Any
    after: Any


class LocalDraft:
    def __init__(
        self,
        *,
        serial: str,
        hardware_id: str,
        schema_version: int,
        base_generation: int,
        base_digest: str,
        max_profiles: int,
        controls: tuple[str, ...],
        actions: tuple[str, ...],
        limits: dict[str, int],
        features: dict[str, Any],
        editor_rules: ConfigEditorRules,
        config: dict[str, Any],
    ) -> None:
        self.serial = serial
        self.hardware_id = hardware_id
        self.schema_version = schema_version
        self.base_generation = base_generation
        self.base_digest = base_digest
        self.max_profiles = max_profiles
        self.controls = controls
        self.actions = actions
        self.limits = dict(limits)
        self.features = copy.deepcopy(features)
        self.editor_rules = editor_rules
        self._baseline = copy.deepcopy(config)
        self.config = copy.deepcopy(config)

    @classmethod
    def from_snapshot(cls, snapshot: DeviceSnapshot, contract: Contract) -> "LocalDraft":
        generation = snapshot.config_result.get("generation")
        digest = snapshot.config_result.get("digest")
        schema_version = snapshot.versions.get("schema_version")
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise ValueError("GET_CONFIG generation 不可用")
        if not isinstance(digest, str):
            raise ValueError("GET_CONFIG digest 不可用")
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise ValueError("Schema version 不可用")
        actions = snapshot.capabilities.get("actions")
        limits = snapshot.capabilities.get("limits")
        features = snapshot.capabilities.get("features")
        max_profiles = limits.get("profiles") if isinstance(limits, dict) else None
        if not isinstance(max_profiles, int) or isinstance(max_profiles, bool):
            raise ValueError("CAPABILITIES limits.profiles 不可用")
        return cls(
            serial=str(snapshot.identity.get("serial", "")),
            hardware_id=str(snapshot.identity.get("hardware_id", "")),
            schema_version=schema_version,
            base_generation=generation,
            base_digest=digest,
            max_profiles=max_profiles,
            controls=snapshot.controls,
            actions=tuple(value for value in actions if isinstance(value, str))
            if isinstance(actions, list)
            else (),
            limits={
                str(key): value
                for key, value in limits.items()
                if isinstance(value, int) and not isinstance(value, bool)
            }
            if isinstance(limits, dict)
            else {},
            features=features if isinstance(features, dict) else {},
            editor_rules=contract.editor_rules,
            config=snapshot.config,
        )

    @property
    def key(self) -> tuple[str, str, int]:
        return self.serial, self.hardware_id, self.base_generation

    @property
    def is_dirty(self) -> bool:
        return self.config != self._baseline

    @property
    def changes(self) -> tuple[DraftChange, ...]:
        return _changes_between(self._baseline, self.config)

    @property
    def only_active_profile_changed(self) -> bool:
        changes = self.changes
        return len(changes) == 1 and changes[0].path == "config.active_profile"

    def preview_changes(self, config: dict[str, Any]) -> tuple[DraftChange, ...]:
        return _changes_between(self.config, config)

    @property
    def profiles(self) -> tuple[dict[str, Any], ...]:
        profiles = self.config.get("profiles")
        if not isinstance(profiles, list):
            return ()
        return tuple(profile for profile in profiles if isinstance(profile, dict))

    @property
    def macros(self) -> tuple[dict[str, Any], ...]:
        macros = self.config.get("macros")
        if not isinstance(macros, list):
            return ()
        return tuple(macro for macro in macros if isinstance(macro, dict))

    @property
    def confirmed_config(self) -> dict[str, Any]:
        return copy.deepcopy(self._baseline)

    @property
    def action_presets(self) -> tuple[dict[str, Any], ...]:
        presets: dict[bytes, dict[str, Any]] = {}
        for profile in self.profiles:
            mappings = profile.get("mappings")
            if not isinstance(mappings, list):
                continue
            for mapping in mappings:
                if not isinstance(mapping, dict) or not isinstance(mapping.get("action"), dict):
                    continue
                action = mapping["action"]
                if action.get("type") not in self.actions:
                    continue
                key = canonical_json_bytes(action)
                presets.setdefault(
                    key,
                    {
                        "short_name": str(mapping.get("short_name", action.get("type", "action"))),
                        "action": copy.deepcopy(action),
                    },
                )
        return tuple(presets.values())

    def profile(self, profile_id: int | None) -> dict[str, Any]:
        for profile in self.profiles:
            if profile.get("id") == profile_id:
                return profile
        raise ValueError(f"Profile {profile_id} 不存在")

    def mapping(self, profile_id: int | None, control_id: str) -> dict[str, Any] | None:
        mappings = self.profile(profile_id).get("mappings")
        if not isinstance(mappings, list):
            return None
        for mapping in mappings:
            if isinstance(mapping, dict) and mapping.get("control_id") == control_id:
                return mapping
        return None

    def rename_profile(self, profile_id: int | None, name: str) -> None:
        self.profile(profile_id)["name"] = name

    def create_profile(self) -> int:
        profile_id = self._next_profile_id()
        active_profile = self.profile(self.config.get("active_profile"))
        source_mappings = active_profile.get("mappings")
        if not isinstance(source_mappings, list):
            raise ValueError("当前 Profile mappings 不可用")
        if "none" in self.actions:
            mappings = [
                {"control_id": control_id, "short_name": "", "action": {"type": "none"}}
                for control_id in self.controls
            ]
        else:
            mappings = copy.deepcopy(source_mappings)
        profiles = self.config.get("profiles")
        if not isinstance(profiles, list):
            raise ValueError("Profiles 不可用")
        profiles.append(
            {
                "id": profile_id,
                "name": self._unique_profile_name("新配置方案"),
                "mappings": mappings,
            }
        )
        return profile_id

    def copy_profile(self, profile_id: int | None) -> int:
        source = self.profile(profile_id)
        new_id = self._next_profile_id()
        profiles = self.config.get("profiles")
        if not isinstance(profiles, list):
            raise ValueError("Profiles 不可用")
        copied = copy.deepcopy(source)
        copied["id"] = new_id
        copied["name"] = self.suggested_profile_copy_name(profile_id)
        profiles.append(copied)
        return new_id

    def suggested_profile_copy_name(self, profile_id: int | None) -> str:
        source = self.profile(profile_id)
        return self._unique_profile_name(
            f"{source.get('name', '配置方案')} 副本"
        )

    def save_profile_as(self, profile_id: int | None, name: str) -> int:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("配置方案名称不能为空。")
        if len(normalized_name) > self.editor_rules.profile_name_max_length:
            raise ValueError(
                f"配置方案名称最多 {self.editor_rules.profile_name_max_length} 个字符。"
            )
        existing_names = {str(profile.get("name", "")) for profile in self.profiles}
        if normalized_name in existing_names:
            raise ValueError("已存在同名配置方案。")
        new_id = self.copy_profile(profile_id)
        self.rename_profile(new_id, normalized_name)
        self.set_active_profile(new_id)
        return new_id

    def delete_profile(self, profile_id: int | None) -> None:
        profiles = self.config.get("profiles")
        if not isinstance(profiles, list):
            raise ValueError("Profiles 不可用")
        if len(profiles) <= 1:
            raise ValueError("配置必须至少保留一个 Profile")
        target = self.profile(profile_id)
        profiles.remove(target)
        if self.config.get("active_profile") == profile_id:
            self.config["active_profile"] = profiles[0]["id"]

    def set_active_profile(self, profile_id: int) -> None:
        self.profile(profile_id)
        self.config["active_profile"] = profile_id

    def macro(self, macro_id: int | None) -> dict[str, Any]:
        for macro in self.macros:
            if macro.get("id") == macro_id:
                return macro
        raise ValueError(f"按键序列 {macro_id} 不存在")

    def create_macro(self) -> int:
        macros = self.config.get("macros")
        if not isinstance(macros, list):
            raise ValueError("按键序列列表不可用")
        used_ids = {
            macro.get("id")
            for macro in self.macros
            if isinstance(macro.get("id"), int) and not isinstance(macro.get("id"), bool)
        }
        rules = self.editor_rules
        if len(used_ids) >= rules.max_macros:
            raise ValueError(
                f"当前 Schema 最多支持 {rules.max_macros} 条按键序列"
            )
        macro_id = next(
            value
            for value in range(rules.macro_id.minimum, rules.macro_id.maximum + 1)
            if value not in used_ids
        )
        macros.append(
            {
                "id": macro_id,
                "name": self._unique_macro_name("新按键序列"),
                "steps": [{"op": "tap", "usage": 4}],
            }
        )
        return macro_id

    def update_macro(self, macro_id: int, name: str, steps: list[dict[str, Any]]) -> None:
        macro = self.macro(macro_id)
        macro["name"] = name
        macro["steps"] = copy.deepcopy(steps)

    def delete_macro(self, macro_id: int) -> None:
        for profile in self.profiles:
            mappings = profile.get("mappings")
            if not isinstance(mappings, list):
                continue
            for mapping in mappings:
                action = mapping.get("action") if isinstance(mapping, dict) else None
                if isinstance(action, dict) and action.get("type") == "macro" and action.get("macro_id") == macro_id:
                    raise ValueError(
                        "这条按键序列仍被控件映射引用，请先修改对应控件"
                    )
        macros = self.config.get("macros")
        if not isinstance(macros, list):
            raise ValueError("按键序列列表不可用")
        macros.remove(self.macro(macro_id))

    def macro_encoded_size(self, macro_id: int) -> int:
        steps = self.macro(macro_id).get("steps")
        return macro_steps_encoded_size(steps if isinstance(steps, list) else [])

    @property
    def all_macro_encoded_size(self) -> int:
        return sum(
            macro_steps_encoded_size(macro.get("steps") if isinstance(macro.get("steps"), list) else [])
            for macro in self.macros
        )

    def set_preferences(
        self,
        *,
        lighting: dict[str, Any],
        haptic: dict[str, Any],
        display: dict[str, Any],
    ) -> None:
        self.config["lighting"] = copy.deepcopy(lighting)
        self.config["haptic"] = copy.deepcopy(haptic)
        self.config["display"] = copy.deepcopy(display)

    def replace_config(self, config: dict[str, Any], contract: Contract) -> None:
        candidate = copy.deepcopy(config)
        errors = self.validate_candidate(candidate, contract)
        if errors:
            raise ValueError(errors[0])
        self.config = candidate

    def rebase_to_snapshot(self, snapshot: DeviceSnapshot, contract: Contract) -> None:
        latest = LocalDraft.from_snapshot(snapshot, contract)
        if latest.serial != self.serial or latest.hardware_id != self.hardware_id:
            raise ValueError("不能把草稿重新绑定到另一台设备")
        candidate = copy.deepcopy(self.config)
        errors = latest.validate_candidate(candidate, contract)
        if errors:
            raise ValueError(errors[0])
        self.schema_version = latest.schema_version
        self.base_generation = latest.base_generation
        self.base_digest = latest.base_digest
        self.max_profiles = latest.max_profiles
        self.controls = latest.controls
        self.actions = latest.actions
        self.limits = latest.limits
        self.features = latest.features
        self.editor_rules = latest.editor_rules
        self._baseline = latest.confirmed_config
        self.config = candidate

    def _next_profile_id(self) -> int:
        used_ids = {
            profile.get("id")
            for profile in self.profiles
            if isinstance(profile.get("id"), int) and not isinstance(profile.get("id"), bool)
        }
        if len(used_ids) >= self.max_profiles:
            raise ValueError(f"设备最多支持 {self.max_profiles} 个 Profile")
        for profile_id in range(self.max_profiles):
            if profile_id not in used_ids:
                return profile_id
        raise ValueError("没有可用的 Profile ID")

    def _unique_profile_name(self, base: str) -> str:
        existing = {str(profile.get("name", "")) for profile in self.profiles}
        max_length = self.editor_rules.profile_name_max_length
        candidate = base[:max_length]
        if candidate not in existing:
            return candidate
        suffix_index = 2
        while True:
            suffix = f" {suffix_index}"
            candidate = f"{base[: max_length - len(suffix)]}{suffix}"
            if candidate not in existing:
                return candidate
            suffix_index += 1

    def _unique_macro_name(self, base: str) -> str:
        existing = {str(macro.get("name", "")) for macro in self.macros}
        max_length = self.editor_rules.macro_name_max_length
        candidate = base[:max_length]
        if candidate not in existing:
            return candidate
        for suffix_index in range(2, self.editor_rules.max_macros + 2):
            suffix = f" {suffix_index}"
            candidate = f"{base[: max_length - len(suffix)]}{suffix}"
            if candidate not in existing:
                return candidate
        raise ValueError("无法生成唯一的按键序列名称")

    def set_mapping(
        self,
        profile_id: int | None,
        control_id: str,
        short_name: str,
        action: dict[str, Any],
    ) -> None:
        if control_id not in self.controls:
            raise ValueError(f"{control_id} 不在设备 CAPABILITIES 中")
        action_type = action.get("type")
        if action_type not in self.actions:
            raise ValueError(f"设备不支持 {action_type} 动作")
        profile = self.profile(profile_id)
        mappings = profile.get("mappings")
        if not isinstance(mappings, list):
            raise ValueError("Profile mappings 不可用")
        replacement = {
            "control_id": control_id,
            "short_name": short_name,
            "action": copy.deepcopy(action),
        }
        for index, mapping in enumerate(mappings):
            if isinstance(mapping, dict) and mapping.get("control_id") == control_id:
                mappings[index] = replacement
                return
        mappings.append(replacement)

    def discard(self) -> None:
        self.config = copy.deepcopy(self._baseline)

    def validate(self, contract: Contract) -> tuple[str, ...]:
        return self.validate_candidate(self.config, contract)

    def validate_candidate(
        self, config: dict[str, Any], contract: Contract
    ) -> tuple[str, ...]:
        try:
            contract.validate_config(config)
        except ContractError as exc:
            return (str(exc),)
        errors: list[str] = []
        if config.get("hardware_id") != self.hardware_id:
            errors.append("候选配置 hardware_id 与当前设备不一致")
        if config.get("schema_version") != self.schema_version:
            errors.append("候选配置 schema_version 与当前设备不一致")
        profiles = [profile for profile in config.get("profiles", ()) if isinstance(profile, dict)]
        profile_ids = [profile.get("id") for profile in profiles]
        if len(profile_ids) != len(set(profile_ids)):
            errors.append("Profile ID 不能重复")
        if config.get("active_profile") not in profile_ids:
            errors.append("当前 Profile 必须指向已存在的 Profile")
        macros = [macro for macro in config.get("macros", ()) if isinstance(macro, dict)]
        macro_ids = [macro.get("id") for macro in macros]
        if len(macro_ids) != len(set(macro_ids)):
            errors.append("按键序列 ID 不能重复")
        macro_id_set = set(macro_ids)
        profile_id_set = set(profile_ids)
        for profile in profiles:
            mappings = profile.get("mappings")
            if not isinstance(mappings, list):
                continue
            control_ids = [
                mapping.get("control_id")
                for mapping in mappings
                if isinstance(mapping, dict)
            ]
            if len(control_ids) != len(set(control_ids)):
                errors.append(f"Profile {profile.get('id')} 中的 control_id 不能重复")
            for mapping in mappings:
                action = mapping.get("action") if isinstance(mapping, dict) else None
                if not isinstance(action, dict):
                    continue
                if mapping.get("control_id") not in self.controls:
                    errors.append(f"{mapping.get('control_id')} 不在设备 CAPABILITIES 中")
                if action.get("type") not in self.actions:
                    errors.append(f"设备不支持 {action.get('type')} 动作")
                if action.get("type") == "macro" and action.get("macro_id") not in macro_id_set:
                    errors.append(
                        f"控件引用的按键序列 {action.get('macro_id')} 不存在"
                    )
                if action.get("type") == "profile" and action.get("profile_id") not in profile_id_set:
                    errors.append(f"按键引用的 Profile {action.get('profile_id')} 不存在")
        per_macro_limit = self.limits["macro_bytes"]
        total_macro_limit = self.limits["all_macro_bytes"]
        for macro in macros:
            steps = macro.get("steps")
            if not isinstance(steps, list):
                continue
            size = macro_steps_encoded_size(steps)
            if size > per_macro_limit:
                errors.append(
                    f"按键序列 {macro.get('name', macro.get('id'))} 编码后 {size} 字节，超过单条 {per_macro_limit} 字节限制"
                )
            errors.extend(_validate_macro_release_sequence(macro, steps))
        total_macro_size = sum(
            macro_steps_encoded_size(macro.get("steps"))
            for macro in macros
            if isinstance(macro.get("steps"), list)
        )
        if total_macro_size > total_macro_limit:
            errors.append(
                f"全部按键序列共 {total_macro_size} 字节，"
                f"超过 {total_macro_limit} 字节总限制"
            )
        config_limit = self.limits["config_bytes"]
        config_size = len(canonical_json_bytes(config))
        if config_size > config_limit:
            errors.append(f"配置共 {config_size} 字节，超过 {config_limit} 字节限制")
        return tuple(dict.fromkeys(errors))


def _collect_changes(
    path: str,
    before: Any,
    after: Any,
    output: list[DraftChange],
) -> None:
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            _collect_changes(f"{path}.{key}", before.get(key), after.get(key), output)
        return
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for index, (before_item, after_item) in enumerate(zip(before, after, strict=True)):
            _collect_changes(f"{path}[{index}]", before_item, after_item, output)
        return
    if before != after:
        output.append(DraftChange(path, copy.deepcopy(before), copy.deepcopy(after)))


def _changes_between(before: dict[str, Any], after: dict[str, Any]) -> tuple[DraftChange, ...]:
    changes: list[DraftChange] = []
    _collect_changes("config", before, after, changes)
    return tuple(changes)


def macro_steps_encoded_size(steps: list[dict[str, Any]]) -> int:
    size = 0
    for step in steps:
        op = step.get("op") if isinstance(step, dict) else None
        if op in {"press", "release", "tap"}:
            size += 2
        elif op == "delay":
            size += 3
        elif op == "text":
            text = step.get("text")
            size += 3 + len(text.encode("ascii", errors="replace")) if isinstance(text, str) else 3
    return size


def _validate_macro_release_sequence(
    macro: dict[str, Any], steps: list[dict[str, Any]]
) -> tuple[str, ...]:
    pressed: set[int] = set()
    errors: list[str] = []
    label = macro.get("name", macro.get("id"))
    for index, step in enumerate(steps, start=1):
        op = step.get("op") if isinstance(step, dict) else None
        usage = step.get("usage") if isinstance(step, dict) else None
        if op == "press" and isinstance(usage, int):
            if usage in pressed:
                errors.append(
                    f"按键序列 {label} 第 {index} 步重复按下 Usage {usage}"
                )
            pressed.add(usage)
        elif op == "release" and isinstance(usage, int):
            if usage not in pressed:
                errors.append(
                    f"按键序列 {label} 第 {index} 步释放了未按下的 Usage {usage}"
                )
            pressed.discard(usage)
    if pressed:
        errors.append(
            f"按键序列 {label} 结束时仍有未释放的 Usage: "
            f"{', '.join(map(str, sorted(pressed)))}"
        )
    return tuple(errors)
