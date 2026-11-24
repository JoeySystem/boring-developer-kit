"""Test helpers for views/ui/notice.py (the QMessageBox replacement).

Old tests replaced QMessageBox.warning / information / question; the code now
calls notice.notify / alert / ask with the same first three arguments. These
helpers keep those tests' fake answers working: keyword arguments the old
signatures never had (state=, kind=, danger=) are dropped before the fake runs.
"""
from __future__ import annotations

from controller_config.views.ui import notice


def drop_keywords(fake):
    return lambda *args, **_kwargs: fake(*args)


def answer_questions(monkeypatch, fake) -> None:
    """Old QMessageBox.question replacement → notice.ask."""
    monkeypatch.setattr(notice, "ask", drop_keywords(fake))


_ORIGINAL_ASK = notice.ask


def answer_warnings(monkeypatch, fake) -> None:
    """Old QMessageBox.warning replacement: warnings were confirmations,
    risky failures and one-line notices, now ask / alert / notify. A question
    fake the test already set stays in charge of ask."""
    for name in ("ask", "alert", "notify"):
        if name == "ask" and notice.ask is not _ORIGINAL_ASK:
            continue
        monkeypatch.setattr(notice, name, drop_keywords(fake))


def answer_information(monkeypatch, fake) -> None:
    for name in ("alert", "notify"):
        monkeypatch.setattr(notice, name, drop_keywords(fake))


def pick(monkeypatch, choose_key):
    """Old QMessageBox(...).exec + clickedButton replacement → notice.choose.
    `choose_key(title, text, keys)` returns the key to pick; every call is recorded."""
    seen: list[tuple[str, str, list[str]]] = []

    def fake(_parent, title, text, options, **_kwargs):
        keys = [key for key, _label in options]
        seen.append((title, text, keys))
        return choose_key(title, text, keys)

    monkeypatch.setattr(notice, "choose", fake)
    return seen


def answer_dialogs(monkeypatch, decide):
    """For tests that look at the real dialog: every message in the shell is
    built, then `decide(info)` names the option to click (None closes it).
    info: title, text, buttons {key: label}, dialog. Returns the list of infos."""
    from PySide6.QtWidgets import QLabel, QPushButton

    seen: list[dict] = []

    def run(dialog):
        buttons = {b.objectName()[len("choice_"):]: b for b in dialog.findChildren(QPushButton)
                   if b.objectName().startswith("choice_")}
        texts = [label.text() for label in dialog.findChildren(QLabel)
                 if label.objectName() == "boringNoticeText"]
        info = {"title": dialog.title_label.text(), "text": texts[0] if texts else "",
                "buttons": {key: button.text() for key, button in buttons.items()}, "dialog": dialog}
        seen.append(info)
        key = decide(info)
        if key in buttons:
            buttons[key].click()
        else:
            dialog.reject()
        return dialog.result()

    monkeypatch.setattr(notice, "_run", run)
    return seen
