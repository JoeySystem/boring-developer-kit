"""Banners (the designer key 8 banner v1.1; several at once stack, at most three)."""
from __future__ import annotations

from PySide6.QtWidgets import QWidget

from controller_config.style.sheets import application_stylesheet
from controller_config.views.ui import Banner, BannerStack, StatusIcon
from controller_config.views.ui import banner as banner_module
from test_session_recovery import session  # noqa: F401 - fixture


def _host(qtbot, width=900, height=500):
    host = QWidget()
    host.setStyleSheet(application_stylesheet())
    host.resize(width, height)
    qtbot.addWidget(host)
    host.show()
    qtbot.waitExposed(host)
    return host


def test_banner_is_one_rounded_line_sized_to_its_text(qtbot):
    host = _host(qtbot)
    stack = BannerStack(host)
    short = stack.show(Banner("已导出", timeout_ms=None))
    long = stack.show(Banner("按键 8 可以改成语音手势：单击说话，双击发送", timeout_ms=None, key="k"))
    assert short.height() == long.height() == banner_module.HEIGHT
    assert long.width() > short.width()
    # Right edges line up with the card column.
    for banner in (short, long):
        assert banner.target().x() + banner.width() == host.width() - banner_module.RIGHT_INSET
        qtbot.waitUntil(lambda banner=banner: banner.sliding() is None, timeout=1000)
        assert banner.geometry().right() + 1 == host.width() - banner_module.RIGHT_INSET


def test_newest_on_top_stacks_down_and_shows_at_most_three(qtbot):
    host = _host(qtbot)
    stack = BannerStack(host)
    banners = [stack.show(Banner(f"第 {i} 条", timeout_ms=None)) for i in range(4)]
    visible = stack.visible_banners()
    assert visible == [banners[3], banners[2], banners[1]]
    assert [b.y() for b in visible] == sorted(b.y() for b in visible)
    assert not banners[0].isVisible()
    banners[3].dismiss()
    qtbot.waitUntil(lambda: banners[3] not in stack.banners(), timeout=1000)   # slides out first
    assert banners[0].isVisible()
    assert stack.visible_banners() == [banners[2], banners[1], banners[0]]


def test_same_key_replaces_in_place(qtbot):
    host = _host(qtbot)
    stack = BannerStack(host)
    first = stack.show(Banner("正在把按键 8 改成语音手势…", state="busy", key="key8", timeout_ms=None))
    stack.show(Banner("别的", timeout_ms=None))
    second = stack.show(Banner("按键 8 已改成语音手势", state="ok", key="key8", timeout_ms=None))
    assert stack.banners()[1] is second
    assert not first.isVisible()
    assert second.y() == banner_module.TOP_INSET + banner_module.HEIGHT + banner_module.GAP


def test_ok_banner_hides_itself_but_one_with_buttons_waits(qtbot):
    host = _host(qtbot)
    stack = BannerStack(host)
    quick = stack.notify("已导出", timeout_ms=50)
    asking = Banner("按键 8 可以改成语音手势", state=None, timeout_ms=50)
    keep = asking.add_button("保留现在的设置")
    asking.add_button("改成语音手势", "primary")
    stack.show(asking)
    qtbot.waitUntil(lambda: quick not in stack.banners(), timeout=2000)
    qtbot.wait(120)
    assert asking.isVisible()
    assert keep.property("bannerButton") is True
    assert keep.height() <= 36  # 34 plus the 1 px outline, top and bottom


def test_states_and_buttons_follow_the_flow(qtbot):
    host = _host(qtbot)
    stack = BannerStack(host)
    banner = stack.show(Banner("按键 8 可以改成语音手势", state="wait", timeout_ms=None))
    banner.add_button("保留现在的设置")
    banner.add_button("改成语音手势", "primary")
    banner.set_state("busy")
    banner.set_buttons_enabled(False)
    assert isinstance(banner.icon(), StatusIcon) and banner.icon().state() == "busy"
    assert not any(button.isEnabled() for button in banner.buttons())
    width = banner.width()
    banner.set_text("按键 8 已改成语音手势：单击说话，双击发送")
    banner.clear_buttons()
    banner.set_state("ok")
    banner.set_closable(True)
    assert banner.width() != width
    close = banner.findChild(QWidget, "bannerClose")
    assert close is not None
    close.click()
    qtbot.waitUntil(lambda: banner not in stack.banners(), timeout=1000)   # slides out first


def test_export_success_is_a_banner_not_a_message_box(session, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    window, vm, _gateway, _snapshot, _store = session
    window.show()
    path = tmp_path / "out.boring-config.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(path), ""))
    boxes = []
    from controller_config.views.ui import notice
    monkeypatch.setattr(notice, "alert", lambda *args, **_kwargs: boxes.append(args))
    assert window._export_configuration("confirmed")
    assert path.exists()
    assert boxes == []
    banner = window._banners.find("configuration-export")
    assert banner is not None and banner.isVisible()
    assert banner.icon().state() == "ok"
    # Over the page's top right corner, on the card column.
    area = window._page_area
    assert banner.parentWidget() is area
    margin = 16 if window._compact_mode else banner_module.RIGHT_INSET
    assert banner.target().x() + banner.width() == area.width() - margin


def test_buttons_styled_after_showing_still_fit(session, qtbot):
    """Buttons get their real size only once the window style reaches them."""
    window, _vm, _gateway, _snapshot, _store = session
    window.show()
    banner = window._banners.show(Banner("按键 8 可以改成语音手势：单击说话，双击发送", state="wait", timeout_ms=None))
    banner.add_button("保留现在的设置")
    banner.add_button("改成语音手势", "primary")
    qtbot.waitUntil(lambda: banner.width() == banner.layout().sizeHint().width(), timeout=1000)
    for button in banner.buttons():
        assert button.width() >= button.sizeHint().width()


def test_banner_slides_in_from_the_right_past_its_place_and_out_again(qtbot):
    """-15 第 3 处 (丙-3): from outside the right edge, about 10 past its place, back; out to the right."""
    host = _host(qtbot)
    stack = BannerStack(host)
    banner = stack.show(Banner("已导出", timeout_ms=None))
    assert banner.sliding() == "in" and banner.x() >= host.width() - 1
    rest = banner.target().x()
    lowest = [banner.x()]
    while banner.sliding() == "in":
        lowest[0] = min(lowest[0], banner.x())
        qtbot.wait(5)
    assert rest - 13 <= lowest[0] <= rest - 7    # ran about 10 past its place
    assert banner.x() == rest
    banner.dismiss()
    assert banner.sliding() == "out"
    qtbot.waitUntil(lambda: banner not in stack.banners(), timeout=1000)


def test_doubao_press_that_did_not_open_is_a_banner_that_slides_in(session, qtbot):
    """-15 第 2 处 (乙, banner part): 豆包没有响应〔查看原因〕, goes after 4 s, no dialog;
    slides in from the right like the other banners (the designer 11-20 真屏验收)."""
    from controller_config.style import tokens

    window, *_ = session
    window.show()
    window.show_agent_open_failed("claude", "x")
    assert window._banners.find("doubao-open-failed") is None   # other apps keep the old notice
    window.show_agent_open_failed("doubao", "window_not_found")
    banner = window._banners.find("doubao-open-failed")
    assert banner is not None and banner.text() == "豆包没有响应"
    assert banner.icon().state() == "error"
    assert [b.text() for b in banner.buttons()] == ["查看原因"]
    assert banner.sliding() == "in" and banner.x() > banner.target().x()   # slides in from the right
    assert banner._timeout_ms == tokens.BANNER_FAILED_HIDE_MS == 4000
    qtbot.waitUntil(lambda: banner.sliding() is None, timeout=1000)
    assert banner._timer.isActive()   # goes by itself although it has a button
