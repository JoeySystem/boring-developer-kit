"""The Settings tab shows the smallest dot ring while an update downloads (进度条点阵样式 v1)."""
from controller_config.views.ui import ProgressRing
from test_session_recovery import session  # noqa: F401


def test_settings_tab_ring_follows_the_download(session):  # noqa: F811
    window, _vm, _gateway, _snapshot, _store = session
    tab = window._nav_buttons["settings"]
    tab.set_update_notice(desktop="downloading", progress=40, detail="正在下载 40%")
    ring = tab.findChild(ProgressRing, "settingsDownloadRing")
    assert ring is not None and not ring.isHidden()
    assert ring.value() == 40
    assert "40%" in tab.accessibleDescription()  # no hover box on the tabs (the designer 11-20)
    assert tab.toolTip() == ""
    tab.set_update_notice(desktop="downloading", progress=-1)
    assert ring.value() is None  # size unknown: the dots go round
    tab.set_update_notice(desktop="ready")
    assert ring.isHidden()
