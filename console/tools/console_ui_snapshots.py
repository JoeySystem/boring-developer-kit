#!/usr/bin/env python3
"""Before/after screenshots of every Console page for style refactors.

Source-preview evidence only: offscreen Qt, the shared demo gateway, and an
isolated home, settings and data directory. It never opens a device, never
reads or writes the user's settings and never touches an installed app.

    capture     screenshots + widget census + stylesheet dump of every page,
                state and dialog in the fixed list below
    compare     pixel, census and stylesheet comparison of two captures,
                written as a Markdown report with highlighted diff images
    dump-style  only the stylesheet dump (window sheet, child sheets and the
                expanded selector x property table)

Compare two captures taken on the same machine only: fonts differ between
machines. Screenshots are not committed. Run from software/configurator:

    .venv/bin/python tools/console_ui_snapshots.py capture --out /tmp/base
    .venv/bin/python tools/console_ui_snapshots.py capture --out /tmp/head
    .venv/bin/python tools/console_ui_snapshots.py compare /tmp/base /tmp/head --report /tmp/report.md

Known gaps (offscreen limits): the Qt Quick 3D device model renders empty,
the connection terminal renders blank, and the voice-key dialogs do not open
in the demo state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path

LANGUAGES = ("zh_CN", "en_US", "ja_JP")
WIDE = (1280, 800)
SCREEN = (1600, 1000)
SETTLE_MS = 900
# Demo states other than "ready"; each is one screenshot of the whole window.
STATES = ("no-device", "read-failed", "incompatible", "read-only", "authenticity-failed")


# ---------------------------------------------------------------- isolation --

def _isolate(workdir: Path) -> None:
    """Point every user-scoped location at workdir before Qt or the app loads."""
    home = workdir / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    screen = workdir / "screen.json"
    screen.write_text(json.dumps({
        "synchronousWindowSystemEvents": False,
        "windowFrameMargins": False,
        "screens": [{
            "name": "snapshots", "x": 0, "y": 0,
            "width": SCREEN[0], "height": SCREEN[1],
            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1,
        }],
    }), encoding="utf-8")
    os.environ["QT_QPA_PLATFORM"] = f"offscreen:configfile={screen}"


# ------------------------------------------------------------------ capture --

class _Session:
    """One demo window plus the helpers that save its evidence."""

    def __init__(self, out: Path, workdir: Path, language: str) -> None:
        from PySide6.QtCore import QSettings, QStandardPaths, QTimer
        from PySide6.QtWidgets import QApplication

        QStandardPaths.setTestModeEnabled(True)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(workdir / "settings"))
        self.app = QApplication.instance() or QApplication([])
        self.app.setOrganizationName("BORING-snapshots")
        self.app.setApplicationName("console-ui-snapshots")

        import controller_config.firmware_origin as firmware_origin
        firmware_origin.QSettings = lambda *_: QSettings(
            str(workdir / "firmware-history.ini"), QSettings.IniFormat)

        from controller_config.i18n import LanguageManager
        self.settings = QSettings(str(workdir / "ui.ini"), QSettings.IniFormat)
        from controller_config.onboarding import ONBOARDING_COMPLETED_KEY
        self.settings.setValue(ONBOARDING_COMPLETED_KEY, True)
        self.language = LanguageManager(
            self.app, settings=self.settings, initial_language=language, persist=False)

        self.out = out
        self.workdir = workdir
        self.census: dict[str, list[dict]] = {}
        self.dialog_count = 0
        self.warnings: list[str] = []
        self._install_dialog_hooks()
        # Watchdog: a native modal loop we did not patch must never hang a run.
        self._modal_seen = None
        self._watchdog = QTimer()
        self._watchdog.setInterval(5000)
        self._watchdog.timeout.connect(self._close_stray_modal)
        self._watchdog.start()

    # -- evidence ---------------------------------------------------------

    def wait(self, ms: int = SETTLE_MS) -> None:
        from PySide6.QtTest import QTest
        QTest.qWait(ms)

    def save(self, widget, name: str) -> None:
        _freeze_animations(widget)
        self.wait(250)
        widget.grab().save(str(self.out / "shots" / f"{name}.png"))
        self.census[name] = _census(widget)
        print(f"saved {name} {widget.width()}x{widget.height()}", flush=True)

    def grab_open_dialogs(self, tag: str) -> None:
        from PySide6.QtWidgets import QApplication, QDialog
        # Dialogs fill in data (quota, permissions) after they open; let them settle.
        self.wait(1500)
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                self.dialog_count += 1
                label = widget.objectName() or type(widget).__name__
                self.save(widget, f"dialog-{tag}-{label}")
                widget.reject()
                self.wait(200)

    # -- dialog hooks -----------------------------------------------------

    def _install_dialog_hooks(self) -> None:
        from PySide6.QtWidgets import QDialog, QMessageBox
        session = self

        def exec_and_capture(dialog, *_args, **_kwargs):
            dialog.show()
            session.wait(400)
            label = dialog.objectName() or type(dialog).__name__
            session.save(dialog, f"dialog-{session.current_tag}-{label}")
            dialog.hide()
            return 0

        def message_box(icon):
            def show(parent, title, text, *_args, **_kwargs):
                box = QMessageBox(icon, str(title), str(text),
                                  QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                                  parent)
                box.show()
                session.wait(250)
                session.save(box, f"dialog-{session.current_tag}-messagebox")
                box.hide()
                return QMessageBox.StandardButton.Cancel
            return staticmethod(show)

        self.current_tag = "startup"
        QDialog.exec = exec_and_capture
        QMessageBox.information = message_box(QMessageBox.Icon.Information)
        QMessageBox.warning = message_box(QMessageBox.Icon.Warning)
        QMessageBox.question = message_box(QMessageBox.Icon.Question)
        QMessageBox.critical = message_box(QMessageBox.Icon.Critical)

    def _close_stray_modal(self) -> None:
        """Close a modal only when the same one is still open one tick later.

        Captured dialogs stay up for about a second, so they are never touched;
        only a native modal loop that blocks the script gets closed.
        """
        from PySide6.QtWidgets import QApplication
        modal = QApplication.activeModalWidget()
        if modal is not None and modal is self._modal_seen:
            self.warnings.append(f"closed stray modal {type(modal).__name__} '{modal.objectName()}'")
            modal.close()
            modal = None
        self._modal_seen = modal

    # -- window -----------------------------------------------------------

    def open_window(self, state: str):
        from controller_config.automation import AutomationStore
        from controller_config.prompt_library import PromptLibraryStore
        from controller_config.protocol.contract import Contract
        from controller_config.transport.demo import DemoGateway
        from controller_config.viewmodels.main import MainViewModel
        from controller_config.views.main_window import MainWindow
        from controller_config.workflows import WorkflowStore

        contract = Contract.load()
        data = self.workdir / f"data-{state}"
        gateway = DemoGateway(contract, state)
        vm = MainViewModel(
            gateway, contract,
            prompt_library_store=PromptLibraryStore(data / "prompts"),
            workflow_store=WorkflowStore(data / "workflows"),
            automation_store=AutomationStore(data / "automations"),
        )
        window = MainWindow(vm, language_manager=self.language, onboarding_settings=self.settings)
        window.resize(*WIDE)
        window.show()
        vm.start()
        for _ in range(50):
            self.wait(100)
            if state != "ready" or vm.model.snapshot is not None:
                break
        self.wait()
        return gateway, vm, window

    def step(self, tag: str, action, *, sizes, window, settle: int = SETTLE_MS) -> None:
        """Run one action, then capture the window at every requested size."""
        self.current_tag = tag
        try:
            action()
            for width, height in sizes:
                window.resize(width, height)
                self.wait(settle)
                self.save(window, f"{tag}@{width}x{height}")
            self.grab_open_dialogs(tag)
        except Exception:  # noqa: BLE001 - one broken page must not hide the rest
            self.warnings.append(f"{tag}: {traceback.format_exc(limit=3)}")
            print(f"FAILED {tag}", flush=True)
        finally:
            window.resize(*WIDE)


def _freeze_animations(widget) -> None:
    """Park onboarding clips on their first frame: frame timing is not repeatable."""
    from controller_config.onboarding.player import GuidePlayer
    for player in widget.findChildren(GuidePlayer):
        player.pause()
        player.seek(0)


def _census(root) -> list[dict]:
    """Every visible text, button, input and toggle with its rendered size."""
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
        QSpinBox, QTextEdit, QWidget,
    )
    kinds = (QPushButton, QCheckBox, QLabel, QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QSpinBox)
    rows = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not widget.isVisible() or not isinstance(widget, kinds):
            continue
        font = widget.font()
        size = font.pixelSize() if font.pixelSize() > 0 else round(font.pointSizeF() * 96 / 72, 2)
        text = widget.text() if hasattr(widget, "text") and callable(widget.text) else ""
        rows.append({
            "kind": next(kind.__name__ for kind in kinds if isinstance(widget, kind)),
            "name": widget.objectName(),
            "role": str(widget.property("buttonRole") or widget.property("wallRole") or ""),
            "px": size,
            "weight": int(font.weight()),
            "x": widget.x(), "y": widget.y(),
            "w": widget.width(), "h": widget.height(),
            "text": str(text)[:40],
        })
    return rows


_ASSET_PATH = re.compile(r'url\("[^"]*/controller_config/')


def _portable(sheet: str) -> str:
    """Drop the checkout location from asset URLs so two trees compare equal."""
    return _ASSET_PATH.sub('url("<controller_config>/', sheet)


def _portable_dump(value):
    if isinstance(value, str):
        return _portable(value)
    if isinstance(value, dict):
        return {key: _portable_dump(item) for key, item in value.items()}
    return value


def _style_dump(window) -> dict:
    from PySide6.QtWidgets import QApplication, QWidget
    children = {}
    for widget in window.findChildren(QWidget):
        sheet = _portable(widget.styleSheet())
        if sheet:
            path = f"{type(widget).__name__}#{widget.objectName() or '-'}"
            children.setdefault(path, sheet)
    sheet = _portable(window.styleSheet())
    return {
        "app": _portable(QApplication.instance().styleSheet()),
        "window": sheet,
        "children": dict(sorted(children.items())),
        "effective": effective_table(sheet),
    }


def effective_table(sheet: str) -> dict[str, str]:
    """Expand a stylesheet into {"selector | property": value}; later rules win.

    Same-selector, same-property overrides are resolved exactly; Qt's
    specificity between different selectors is not modelled, so compare two
    tables, do not read one as the rendered truth.
    """
    sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    table: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
        names = [" ".join(name.split()) for name in selectors.split(",") if name.strip()]
        for declaration in body.split(";"):
            if ":" not in declaration:
                continue
            prop, value = (part.strip() for part in declaration.split(":", 1))
            if not prop:
                continue
            for name in names:
                table[f"{name} | {prop}"] = " ".join(value.split())
    return table


def _pages(session: _Session, gateway, vm, window, *, sizes) -> None:
    def normal_mode() -> None:
        snapshot = vm.model.snapshot
        gateway.snapshot_ready.emit(replace(snapshot, status={**snapshot.status, "operating_mode": "normal"}))
        session.wait(500)
        window._select_configuration_mode("normal")
        # "已切换至 …" in the header stays for 3 s (_mode_notice_timer); wait it out.
        session.wait(3500)

    step = lambda tag, action: session.step(tag, action, sizes=sizes, window=window)  # noqa: E731
    step("mode-codex", lambda: vm.navigate("overview"))
    step("mode-codex-agent-key", lambda: window._select_control("key.1"))
    step("mode-normal", normal_mode)
    step("mode-normal-key8", lambda: window._select_control("key.8"))
    step("prompt", lambda: vm.navigate("prompts"))
    for section in ("device", "ai", "system", "updates"):
        step(f"settings-{section}", lambda section=section: window._select_settings_section(section))
    step("settings-appearance", lambda: window._select_settings_section("appearance"))
    step("firmware", lambda: vm.navigate("firmware"))
    step("calibration", lambda: vm.navigate("joystick"))
    step("actions", lambda: vm.navigate("actions"))
    from controller_config.views.actions import ActionsPage
    for index, label in ((1, "edit"), (2, "history"), (3, "develop")):
        def show_section(index=index):
            page = window.findChild(ActionsPage)
            page.show_section(index)
        step(f"actions-{label}", show_section)
    step("sequences", lambda: vm.navigate("sequences"))


def _dialogs(session: _Session, vm, window) -> None:
    from PySide6.QtWidgets import QPushButton

    def on_overview(action):
        def run():
            vm.navigate("overview")
            session.wait(500)
            action()
        return run

    def factory_reset():
        window._select_settings_section("device")
        session.wait(500)
        window.findChild(QPushButton, "factoryResetDevice").click()

    def tile_details(tile_name):
        def run():
            from controller_config.views import settings_wall
            window._select_settings_section("ai")
            session.wait(500)
            window.findChild(getattr(settings_wall, tile_name))._open_details()
        return run

    dialogs = (
        ("mode-controls", on_overview(window._show_mode_controls)),
        ("codex-usage", on_overview(window._show_codex_usage)),
        ("ble-name", on_overview(window._open_ble_name_editor)),
        ("ble-slots", on_overview(lambda: window._show_ble_slots(vm.model.snapshot, vm.model.state))),
        ("ai-setup", on_overview(window._show_ai_setup)),
        ("onboarding", on_overview(window._show_onboarding)),
        ("technical-details", on_overview(lambda: window._show_technical_details(vm.model.snapshot))),
        ("factory-reset", factory_reset),
        ("claude-details", tile_details("ClaudeStatusTile")),
        ("doubao-details", tile_details("DoubaoTile")),
    )
    for tag, action in dialogs:
        session.current_tag = tag
        try:
            action()
            session.grab_open_dialogs(tag)
        except Exception:  # noqa: BLE001
            session.warnings.append(f"{tag}: {traceback.format_exc(limit=3)}")
            print(f"FAILED {tag}", flush=True)


def capture(out: Path, language: str, *, style_only: bool = False) -> int:
    out.mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="console-ui-snapshots-") as directory:
        workdir = Path(directory)
        _isolate(workdir)
        from controller_config.qt_bootstrap import prepare_qt_platform_plugins
        prepare_qt_platform_plugins()
        session = _Session(out, workdir, language)

        gateway, vm, window = session.open_window("ready")
        (out / "style.json").write_text(
            json.dumps(_style_dump(window), ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        if not style_only:
            minimum = window.minimumSize()
            sizes = [WIDE, (minimum.width(), minimum.height())]
            _pages(session, gateway, vm, window, sizes=sizes)
            _dialogs(session, vm, window)
        vm.shutdown()
        window.close()
        session.wait(300)

        if not style_only:
            for state in STATES:
                gateway, vm, window = session.open_window(state)
                session.step(f"state-{state}", lambda: None, sizes=[WIDE], window=window)
                vm.shutdown()
                window.close()
                session.wait(300)

        (out / "census.json").write_text(
            json.dumps(session.census, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        (out / "meta.json").write_text(json.dumps({
            "language": language, "screen": SCREEN, "wide": WIDE,
            "shots": len(list((out / "shots").glob("*.png"))),
            "warnings": session.warnings,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        for warning in session.warnings:
            print("WARNING", warning, flush=True)
        print(f"done: {out}", flush=True)
    # Qt objects outlive the temporary directory otherwise; leave without teardown noise.
    os._exit(0)


# ------------------------------------------------------------------ compare --

def _image_diff(base: Path, head: Path, diff_out: Path) -> tuple[str, int, tuple | None]:
    from PySide6.QtGui import QColor, QImage, QPainter

    a = QImage(str(base)).convertToFormat(QImage.Format.Format_RGBA8888)
    b = QImage(str(head)).convertToFormat(QImage.Format.Format_RGBA8888)
    if a.size() != b.size():
        return f"size {a.width()}x{a.height()} -> {b.width()}x{b.height()}", -1, None
    left, right = bytes(a.constBits()), bytes(b.constBits())
    if left == right:
        return "identical", 0, None
    width = a.width()
    changed = []
    for offset in range(0, len(left), 4):
        if left[offset:offset + 4] != right[offset:offset + 4]:
            pixel = offset // 4
            changed.append((pixel % width, pixel // width))
    xs = [x for x, _ in changed]
    ys = [y for _, y in changed]
    box = (min(xs), min(ys), max(xs), max(ys))
    marked = b.copy()
    painter = QPainter(marked)
    painter.fillRect(marked.rect(), QColor(0, 0, 0, 150))
    painter.end()
    red = QColor(255, 40, 40).rgba()
    for x, y in changed:
        marked.setPixel(x, y, red)
    diff_out.parent.mkdir(parents=True, exist_ok=True)
    marked.save(str(diff_out))
    return "changed", len(changed), box


def compare(base: Path, head: Path, report: Path) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from controller_config.qt_bootstrap import prepare_qt_platform_plugins
    prepare_qt_platform_plugins()
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.instance() or QGuiApplication([])

    lines = [f"# Console 截图对比", "", f"- 基线：`{base}`", f"- 对比：`{head}`", ""]
    differences = 0

    base_shots = {path.name for path in (base / "shots").glob("*.png")}
    head_shots = {path.name for path in (head / "shots").glob("*.png")}
    rows = []
    for name in sorted(base_shots | head_shots):
        if name not in head_shots or name not in base_shots:
            rows.append((name, "只在基线" if name in base_shots else "只在对比", "", ""))
            differences += 1
            continue
        status, count, box = _image_diff(base / "shots" / name, head / "shots" / name,
                                         report.parent / "diff" / name)
        if status != "identical":
            differences += 1
        rows.append((name, status, "" if count <= 0 else str(count), "" if box is None else str(box)))
    changed_rows = [row for row in rows if row[1] != "identical"]
    lines += [f"## 截图：{len(rows)} 张，{len(changed_rows)} 张有差异", ""]
    if changed_rows:
        lines += ["| 截图 | 结果 | 变化像素 | 范围 |", "|---|---|---|---|"]
        lines += [f"| {name} | {status} | {count} | {box} |" for name, status, count, box in changed_rows]
        lines += ["", f"差异图在 `{report.parent / 'diff'}`（变化的像素标红）。", ""]

    base_census = json.loads((base / "census.json").read_text(encoding="utf-8"))
    head_census = json.loads((head / "census.json").read_text(encoding="utf-8"))
    census_changed = sorted(name for name in set(base_census) | set(head_census)
                            if base_census.get(name) != head_census.get(name))
    differences += len(census_changed)
    lines += [f"## 控件清单：{len(census_changed)} 处页面有差异", ""]
    for name in census_changed[:30]:
        before = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in base_census.get(name, [])}
        after = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in head_census.get(name, [])}
        lines.append(f"- `{name}`：少了 {len(before - after)} 个、多了 {len(after - before)} 个控件记录")
        for row in sorted(after - before)[:5]:
            lines.append(f"  - 新：`{row}`")
    lines.append("")

    base_style = _portable_dump(json.loads((base / "style.json").read_text(encoding="utf-8")))
    head_style = _portable_dump(json.loads((head / "style.json").read_text(encoding="utf-8")))
    window_same = base_style["window"] == head_style["window"]
    effective_base, effective_head = base_style["effective"], head_style["effective"]
    keys = set(effective_base) | set(effective_head)
    effective_changed = sorted(key for key in keys if effective_base.get(key) != effective_head.get(key))
    children_changed = sorted(key for key in set(base_style["children"]) | set(head_style["children"])
                              if base_style["children"].get(key) != head_style["children"].get(key))
    app_same = base_style["app"] == head_style["app"]
    differences += (not window_same) + (not app_same) + len(effective_changed) + len(children_changed)
    lines += [
        "## 样式表", "",
        f"- 窗口样式表文本：{'逐字相同' if window_same else '有变化'}",
        f"- 生效表（选择器×属性）：基线 {len(effective_base)} 条、对比 {len(effective_head)} 条，"
        f"取值不同或增删 {len(effective_changed)} 条",
        f"- 子控件样式表：{len(children_changed)} 处不同",
        f"- 应用级样式表：{'相同' if app_same else '有变化'}",
        "",
    ]
    for key in effective_changed[:40]:
        lines.append(f"  - `{key}`：`{effective_base.get(key)}` → `{effective_head.get(key)}`")
    lines.append("")

    for side, path in (("基线", base), ("对比", head)):
        warnings = json.loads((path / "meta.json").read_text(encoding="utf-8")).get("warnings", [])
        if warnings:
            lines += [f"## {side}截图时的警告", ""] + [f"- {warning.splitlines()[0]}" for warning in warnings] + [""]

    lines.insert(5, f"**结论：{'全部相同' if differences == 0 else f'{differences} 处差异，逐条核对是否预期'}**\n")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    print(report.read_text(encoding="utf-8"))
    return 0 if differences == 0 else 1


# --------------------------------------------------------------------- main --

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture", help="截全部页面、状态和对话框")
    capture_parser.add_argument("--out", type=Path, required=True)
    capture_parser.add_argument("--language", choices=LANGUAGES, default="zh_CN")
    style_parser = commands.add_parser("dump-style", help="只导出样式表")
    style_parser.add_argument("--out", type=Path, required=True)
    compare_parser = commands.add_parser("compare", help="对比两次 capture")
    compare_parser.add_argument("base", type=Path)
    compare_parser.add_argument("head", type=Path)
    compare_parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "capture":
        return capture(args.out, args.language)
    if args.command == "dump-style":
        return capture(args.out, "zh_CN", style_only=True)
    return compare(args.base, args.head, args.report)


if __name__ == "__main__":
    sys.exit(main())
