"""Editable UI copy; original source strings are stable lookup keys, not display copy."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from string import Formatter


class TextCatalog:
    def __init__(self, data: dict, rule_data: dict) -> None:
        self.messages = data["messages"]
        self.dynamic = data["dynamic"]
        self.languages = tuple(data.get("languages", ("zh_CN", "en_US")))
        self.rules = []
        for key, entry in self.messages.items():
            for language in self.languages:
                if not isinstance(entry.get(language), str):
                    raise ValueError(f"{key}: {language} must be text")
        for rule in rule_data["rules"]:
            pattern = re.compile(rule["match"], re.DOTALL)
            entry = self.dynamic[rule["message"]]
            allowed = {f"v{i}" for i in range(1, pattern.groups + 1)}
            language_fields = []
            for language in self.languages:
                if not isinstance(entry.get(language), str):
                    raise ValueError(f"{rule['message']}: {language} must be text")
                fields = set()
                for _, field, spec, conversion in Formatter().parse(entry[language]):
                    if field is not None:
                        if field not in allowed or spec or conversion:
                            raise ValueError(f"{rule['message']}: invalid placeholder {{{field}}}")
                        fields.add(field)
                language_fields.append(fields)
            if any(fields != language_fields[0] for fields in language_fields[1:]):
                raise ValueError(f"{rule['message']}: language placeholders differ")
            self.rules.append((pattern, rule))

    @classmethod
    def load(cls, path: Path | None = None) -> TextCatalog:
        resources = files("controller_config.translations")
        copy = path if path is not None else resources.joinpath("ui_text.json")
        return cls(
            json.loads(copy.read_text(encoding="utf-8")),
            json.loads(resources.joinpath("text_rules.json").read_text(encoding="utf-8")),
        )

    def translate(self, source: str, language: str, *, _depth: int = 0) -> str:
        if _depth > 16:
            return source
        entry = self.messages.get(source)
        if entry is not None:
            return entry.get(language, source)
        for pattern, rule in self.rules:
            match = pattern.fullmatch(source)
            if match is None:
                continue
            values = {f"v{i}": value for i, value in enumerate(match.groups(), 1)}
            for index in rule.get("translate", []):
                key = f"v{index}"
                values[key] = self._fragment(values[key], language, _depth + 1)
            for index, separator in rule.get("split", {}).items():
                key = f"v{index}"
                values[key] = separator.join(
                    self._fragment(part, language, _depth + 1)
                    for part in values[key].split(separator)
                )
            return self.dynamic[rule["message"]].get(language, source).format_map(values)
        return source

    def _fragment(self, value: str, language: str, depth: int) -> str:
        if value in self.messages:
            return self.translate(value, language, _depth=depth)
        if "/" in value:
            return "/".join(self._fragment(part, language, depth + 1) for part in value.split("/"))
        return self.translate(value, language, _depth=depth)


@lru_cache(maxsize=1)
def get_text_catalog() -> TextCatalog:
    """Read once at startup. Restart the source app after editing ui_text.json."""
    return TextCatalog.load()


if __name__ == "__main__":
    import sys

    catalog = TextCatalog.load(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"文案检查通过：{len(catalog.messages)} 条固定文案，{len(catalog.dynamic)} 条动态文案。")
