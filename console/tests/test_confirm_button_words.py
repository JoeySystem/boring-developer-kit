"""Confirm dialogs name the action on their button (design ticket 第 1 件, design decision).

Rule: the main button says what happens ("删除", "放弃修改", "恢复出厂设置…"),
the other one says "取消"; no "是 / 否". Read from the source, so a new
dialog that falls back to the generic words fails here.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from controller_config.views.ui import notice

VIEWS = Path(__file__).resolve().parents[1] / "src" / "controller_config" / "views"
GENERIC = {"是", "否", "好", "确定", "继续", "放弃"}
# Standard buttons whose built-in word is generic, so a call must name them.
NEEDS_LABEL = {"Yes", "No", "Discard", "Ok"}


def _calls(name: str):
    for path in sorted(VIEWS.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called == name:
                yield path.relative_to(VIEWS.parent), node


def _buttons(expr: ast.AST) -> set[str]:
    return {node.attr for node in ast.walk(expr) if isinstance(node, ast.Attribute) and node.attr in
            {"Yes", "No", "Discard", "Ok", "Save", "Cancel", "Close", "Abort", "Retry", "Open", "Ignore"}}


def _ask_sites():
    for where, node in _calls("ask"):
        if len(node.args) < 4:
            continue  # not notice.ask (it takes parent, title, text, buttons)
        yield where, node


def test_there_are_ask_sites_to_check() -> None:
    assert len(list(_ask_sites())) >= 20


@pytest.mark.parametrize("where,node", list(_ask_sites()), ids=lambda v: str(v) if isinstance(v, Path) else "")
def test_every_ask_names_its_action(where, node) -> None:
    buttons = _buttons(node.args[3])
    assert "No" not in buttons, f"{where}:{node.lineno} uses 否; the other button is 取消"
    labels = next((kw.value for kw in node.keywords if kw.arg == "labels"), None)
    named = _buttons(labels) if labels is not None else set()
    missing = (buttons & NEEDS_LABEL) - named
    assert not missing, f"{where}:{node.lineno} shows a generic word for {sorted(missing)}"
    if isinstance(labels, ast.Dict):
        for value in labels.values:
            if isinstance(value, ast.Constant):
                assert value.value not in GENERIC, f"{where}:{node.lineno}"


def test_confirm_and_choose_words_are_actions() -> None:
    for where, node in _calls("confirm"):
        if len(node.args) >= 4 and isinstance(node.args[3], ast.Constant):
            assert node.args[3].value not in GENERIC, f"{where}:{node.lineno}"
    for where, node in _calls("choose"):
        if len(node.args) >= 4 and isinstance(node.args[3], ast.List):
            for option in node.args[3].elts:
                label = option.elts[1] if isinstance(option, ast.Tuple) else None
                if isinstance(label, ast.Constant):
                    assert label.value not in GENERIC, f"{where}:{node.lineno}"


def test_ask_shows_the_given_words(monkeypatch) -> None:
    seen = {}

    def fake_choose(_parent, _title, _text, options, **_kwargs):
        seen["labels"] = [label for _key, label in options]
        return _kwargs["escape"]

    monkeypatch.setattr(notice, "choose", fake_choose)
    notice.ask(None, "删除配置方案", "…", QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
               danger=True, labels={QMessageBox.Yes: "删除"})
    assert seen["labels"] == ["取消", "删除"]
