"""The strip at the bottom for update notices takes no height while it has nothing
to say (design decision, 窗口尺寸研究 第 5 节第 3 件)."""
from test_session_recovery import session  # noqa: F401


def test_empty_update_strip_collapses_and_comes_back_with_a_notice(session):  # noqa: F811
    window, *_ = session
    window.show()
    area = window._update_notice_area
    footer = window._desktop_update_footer
    footer.hide()
    window._firmware_update_footer.hide()
    assert area.isHidden()
    footer.show()
    assert not area.isHidden()
    footer.hide()
    assert area.isHidden()
