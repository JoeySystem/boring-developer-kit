#!/usr/bin/env python3
"""Does every state of a page fit the window? (design decision 窗口尺寸规矩)

Offscreen Qt and the demo gateway only: it never opens a device and never
reads or writes the user's settings. For each window size, language and page
state it records:

    scroll   how far the page could scroll (must be 0)
    room     space left under the wall inside the content area (must be >= 16);
             sizes are real-Mac sizes, see MENU_BAR
    cut      visible text that does not fit its widget, or a widget sticking
             out of its tile (must be 0)
    folded   long text a ClampedLabel ends with "…" on purpose; the whole text
             is in its tooltip and, where it matters, behind a "more" link

Run from software/configurator:

    .venv/bin/python tools/fit_study.py                       # the table
    .venv/bin/python tools/fit_study.py --shots /tmp/fit      # plus one PNG per state
    .venv/bin/python tools/fit_study.py --sizes 960x640 --languages zh_CN

tests/test_window_fit.py runs the same measurement.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# 960×640 (design decision), 1280×800, MacBook 14 full screen, and its full screen at 125% (研究报告第 1 节).
SIZES = ((960, 640), (1280, 800), (1512, 945), (1210, 756))
# Offscreen windows carry a 24 px menu bar a real Mac keeps at the top of the screen;
# the window is made this much taller so the content area matches the real one (480 at 960×640).
MENU_BAR = 24
LANGUAGES = ("zh_CN", "en_US", "ja_JP")
BOTTOM_ROOM = 16
LONG_NOTES = (
    "修复蓝牙重连后按键灯不亮的问题；改进摇杆回中检测，减少误触；"
    "优化固件写入速度，安装时间缩短约 30%；新增屏幕图标的两种动画；修正日文界面的两处文字。"
)


@dataclass
class Result:
    size: tuple[int, int]
    language: str
    page: str
    state: str
    scroll: int = 0
    room: int = 0
    cut: list[str] = field(default_factory=list)
    folded: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.scroll == 0 and self.room >= BOTTOM_ROOM and not self.cut


def _spin(app, seconds: float = 0.05) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _settle(app, window, timeout: float = 3.0) -> None:
    """Wait until the page's wall is on screen and stops moving (slow under load)."""
    from controller_config.views.settings_wall import SettingsWall
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        _spin(app, 0.06)
        walls = [wall for wall in window._content.findChildren(SettingsWall) if wall.isVisible()]
        now = (walls[0].geometry().getRect(), walls[0].mapTo(window, walls[0].rect().topLeft()).toTuple()) \
            if walls else None
        if now is not None and now == last:
            return
        last = now


def measure(window, page: str, state: str, size, language) -> Result:
    """Measure what is on screen now (the window has been laid out)."""
    from PySide6.QtWidgets import QAbstractButton, QLabel, QScrollArea
    from controller_config.views.settings_wall import SettingsWall, WallTile
    from controller_config.views.ui.clamped_label import ClampedLabel

    result = Result(size, language, page, state)
    content = window._content
    for area in content.findChildren(QScrollArea):
        if area.isVisible():
            result.scroll = max(result.scroll, area.verticalScrollBar().maximum())
    walls = [wall for wall in content.findChildren(SettingsWall) if wall.isVisible()]
    if walls:
        wall = walls[0]
        area = next(a for a in content.findChildren(QScrollArea) if a.isVisible() and a.isAncestorOf(wall))
        bottom = wall.mapTo(area.viewport(), wall.rect().bottomLeft()).y()
        result.room = area.viewport().height() - bottom - 1
    tiles = [tile for tile in content.findChildren(WallTile) if tile.isVisible()]

    def name(widget) -> str:
        text = widget.text() if hasattr(widget, "text") else ""
        return f"{widget.objectName() or type(widget).__name__}「{text[:24]}」"

    for widget in content.findChildren(QLabel) + content.findChildren(QAbstractButton):
        if not widget.isVisible() or widget.width() <= 0:
            continue
        if isinstance(widget, QLabel) and not widget.pixmap().isNull():
            continue  # a still (a cross-fade's picture of the old look), not text
        tile = next((t for t in tiles if t.isAncestorOf(widget)), None)
        if tile is not None:
            top_left = widget.mapTo(tile, widget.rect().topLeft())
            bottom_right = widget.mapTo(tile, widget.rect().bottomRight())
            if (top_left.x() < 0 or top_left.y() < 0 or bottom_right.x() >= tile.width()
                    or bottom_right.y() >= tile.height()):
                result.cut.append(f"出了方块 {name(widget)}")
                continue
        text = widget.text() if hasattr(widget, "text") else ""
        if not text:
            continue
        if isinstance(widget, ClampedLabel):
            if widget.height() < widget.fontMetrics().lineSpacing():
                result.cut.append(f"一行都放不下 {name(widget)}")
            elif widget.is_clamped():
                result.folded.append(name(widget))
            continue
        if isinstance(widget, QLabel):
            if widget.textFormat() == widget.textFormat().RichText or not widget.pixmap().isNull():
                continue
            rect = widget.contentsRect()
            if widget.wordWrap():
                if QLabel.heightForWidth(widget, widget.width()) > widget.height() + 1:
                    result.cut.append(f"折行后放不下 {name(widget)}")
            else:
                lines = text.split("\n")
                wide = max(widget.fontMetrics().horizontalAdvance(line) for line in lines)
                tall = len(lines) * widget.fontMetrics().lineSpacing()
                if wide > rect.width() + 1 or tall > rect.height() + 2:
                    result.cut.append(f"太长 {name(widget)}")
        elif widget.sizeHint().width() > widget.width() + 1:
            result.cut.append(f"按钮字被截 {name(widget)}")
    return result


# -- states -------------------------------------------------------------------

def _package(vm, *, custom: bool = False):
    from controller_config.firmware_update import FirmwarePackage
    snapshot = vm.model.snapshot
    return FirmwarePackage(Path("fixture.json"), Path("fixture.bin"), vm._contract.product_id,
                           snapshot.identity.get("hardware_id", ""), "0.3.0", 1136864, "",
                           build_id="20261120.02-gabc", source_kind="custom" if custom else "official")


def _release(notes: str = LONG_NOTES):
    from controller_config.firmware_release import RemoteFirmwareRelease
    return RemoteFirmwareRelease(
        dict(version="0.3.1", build_id="20261121.01-gabc", size=1136864, channel="stable", release_notes=notes),
        "https://example.test/manifest.json", "https://example.test/fw.bin")


def firmware_states():
    """(name, setup(vm, window)) for every state of 固件更新 worth looking at."""
    from controller_config.firmware_release import RemoteFirmwareState as R
    from controller_config.firmware_update import FirmwareUpdateState as F

    def remote(**changes):
        return lambda vm, _w: vm._set_remote_firmware(replace(vm.remote_firmware, **changes))

    def device(state, **changes):
        def setup(vm, _w):
            vm._firmware.set_transaction(replace(vm.firmware_update, state=state, package=_package(vm), **changes))
        return setup

    def expanded(vm, window):
        toggle = window._content.findChild(_button_type(), "firmwarePackageToggle")
        if toggle is not None and not toggle.isChecked():
            toggle.click()

    return [
        ("平时", lambda vm, w: None),
        ("检查中", remote(state=R.CHECKING, message="正在检查固件更新…")),
        ("有新版本", remote(state=R.AVAILABLE, release=_release(), message="有新的官方固件可以安装")),
        ("下载中", remote(state=R.DOWNLOADING, release=_release(), received_size=500_000, total_size=1136864,
                         message="正在下载固件：500000 / 1136864 字节")),
        ("下载失败", remote(state=R.FAILED, release=_release(), received_size=300_000, total_size=1136864,
                          message="固件下载没有完成，请检查网络后重试。", technical="HTTP 503 from example.test")),
        ("已是最新", remote(state=R.CURRENT, release=_release("与当前版本相同。"), message="已是最新官方固件")),
        ("未发布", remote(state=R.UNPUBLISHED, message="暂无可用的固件发布\n当前在线地址未提供固件。可使用本页的恢复入口，或从文件安装官方固件。",
                         technical="HTTP 404")),
        ("已导入", device(F.PACKAGE_READY, message="维护包已通过本地检查")),
        ("传输中", device(F.TRANSFERRING, received_size=600_000, message="正在传输固件：600000 / 1136864 字节")),
        ("等待重启", device(F.WAITING_RECONNECT, message="设备正在重启，请保持连接")),
        ("连接中断", device(F.PAUSED, message="连接中断，等待对账续传")),
        ("已完成", device(F.COMPLETED, message="固件维护已读回确认")),
        ("失败", device(F.FAILED, received_size=400_000, message="传输失败：设备没有在规定时间内回应，请重新连接后重试。",
                        technical="FW_DATA timeout at offset 400384\nretries: 3")),
        ("旧更新未完成", device(F.NEEDS_ABORT, message="设备里还有一份没做完的更新")),
        ("展开高级选项", lambda vm, w: (device(F.PACKAGE_READY, message="维护包已通过本地检查")(vm, w),
                                     _later(w, expanded))),
        ("仅固件维护", _maintenance),
    ]


def _button_type():
    from PySide6.QtWidgets import QPushButton
    return QPushButton


def _later(window, action):
    window._fit_study_after = action


def _maintenance(vm, window) -> None:
    from controller_config.protocol.device_auth import DeviceTrust, DeviceTrustState
    snapshot = vm.model.snapshot
    window._fit_study_gateway.snapshot_ready.emit(replace(
        snapshot, config_result={}, status={}, maintenance_reason="GET_CONFIG timeout",
        trust=DeviceTrust(DeviceTrustState.AUTHENTICATED, "设备已认证", "", serial=snapshot.identity.get("serial", ""))))


def calibration_states():
    from controller_config.joystick_calibration import CalibrationState as C

    def calibration(state, **changes):
        def setup(vm, _w):
            vm._calibration = replace(vm.calibration, state=state, **changes)
            vm.changed.emit(vm.model)
        return setup

    sampled = dict(raw_x=3100, raw_y=900, center_x=2050, center_y=2040, minimum_x=120, minimum_y=95,
                   maximum_x=3990, maximum_y=4010)
    return [
        ("平时", lambda vm, w: None),
        ("采中立点", calibration(C.CENTERING, message="请完全松开摇杆，正在采集中立点…")),
        ("采行程", calibration(C.CAPTURING, message="请沿四个方向把摇杆推到底，再松手回中。", **sampled)),
        ("可保存", calibration(C.READY_TO_CONFIRM, message="四向行程与两次回中已完成，可以保存。",
                            travel_complete=True, ready_to_confirm=True, stable_returns_supported=True, **sampled)),
        ("保存中", calibration(C.CONFIRMING, message="正在检查回中并保存校准…", **sampled)),
        ("失败", calibration(C.FAILED, message="校准没有完成：设备在保存前断开了连接。原有校准没有改动。",
                           technical="CAL_CONFIRM timeout after 3000 ms\nsession 7", **sampled)),
        ("冲突", calibration(C.CONFLICT, message="设备上的配置已被别处修改，请重新读取后再校准。",
                           technical="generation 12 != 13")),
    ]


PAGES = {"firmware": firmware_states, "joystick": calibration_states}


def run(sizes=SIZES, languages=LANGUAGES, pages=tuple(PAGES), shots: Path | None = None, app=None,
        menu_bar: int = MENU_BAR):
    """menu_bar=0 measures the offscreen content area as it is: 24 px shorter than a real Mac's, stricter."""
    from PySide6.QtWidgets import QApplication
    from controller_config.qt_bootstrap import prepare_qt_platform_plugins

    prepare_qt_platform_plugins()
    app = app or QApplication.instance() or QApplication([])
    from controller_config.firmware_update import FirmwareUpdateTransaction
    from controller_config.joystick_calibration import CalibrationTransaction
    from controller_config.protocol.contract import Contract
    from controller_config.transport.demo import DemoGateway
    from controller_config.viewmodels.main import MainViewModel
    from controller_config.views.main_window import MainWindow

    contract = Contract.load(ROOT.parent / "protocol")
    results: list[Result] = []
    for page in pages:
        for name, setup in PAGES[page]():
            gateway = DemoGateway(contract, "ready")
            vm = MainViewModel(gateway, contract)
            window = MainWindow(vm)
            window._fit_study_gateway = gateway
            window._fit_study_after = None
            window.show()
            vm.start()
            deadline = time.monotonic() + 5
            while vm.model.snapshot is None and time.monotonic() < deadline:
                _spin(app)
            try:
                vm.navigate(page)
                setup(vm, window)
                _spin(app, 0.1)
                for language in languages:
                    window._language_manager.set_language(language)
                    for size in sizes:
                        window.resize(size[0], size[1] + menu_bar)
                        _settle(app, window)
                        if window._fit_study_after is not None:
                            window._fit_study_after(vm, window)
                            _spin(app, 0.05)
                        result = measure(window, page, name, size, language)
                        results.append(result)
                        if shots is not None:
                            shots.mkdir(parents=True, exist_ok=True)
                            image = window.grab()
                            image.save(str(shots / f"{page}-{name}-{language}-{size[0]}x{size[1]}.png"))
            finally:
                window._language_manager.set_language("zh_CN")
                vm._firmware.replace_silently(FirmwareUpdateTransaction())
                vm._calibration = CalibrationTransaction()
                vm.shutdown()
                window.close()
                window.deleteLater()
                _spin(app, 0.02)
    return results


def table(results: list[Result]) -> str:
    lines = ["| 尺寸 | 语言 | 页 | 状态 | 要滚动 | 底下余量 | 字被截 | 收起成… |", "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.size[0]}×{r.size[1]} | {r.language} | {r.page} | {r.state} | {r.scroll} | {r.room} | "
                     f"{len(r.cut)} | {len(r.folded)} |")
    bad = [r for r in results if not r.ok]
    lines.append("")
    lines.append(f"共 {len(results)} 项，没过 {len(bad)} 项。")
    for r in bad:
        lines.append(f"- {r.size[0]}×{r.size[1]} {r.language} {r.page}/{r.state}: 滚动 {r.scroll}，余量 {r.room}，"
                     f"截字 {'；'.join(r.cut)}")
    return "\n".join(lines)


def main() -> int:
    os.environ.setdefault("QT_SCALE_FACTOR", "2")  # as the window-size study (研究报告第 1 节)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SIZES))
    parser.add_argument("--languages", default=",".join(LANGUAGES))
    parser.add_argument("--pages", default=",".join(PAGES))
    parser.add_argument("--shots", type=Path)
    parser.add_argument("--menu-bar", type=int, default=MENU_BAR,
                        help="extra window height for the offscreen menu bar (0 = no allowance, stricter)")
    args = parser.parse_args()
    sizes = tuple(tuple(int(part) for part in item.split("x")) for item in args.sizes.split(","))
    results = run(sizes, tuple(args.languages.split(",")), tuple(args.pages.split(",")), args.shots,
                  menu_bar=args.menu_bar)
    print(table(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
