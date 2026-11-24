"""A-01/A-03: recoverable link events appear in the diagnostics record."""

from controller_config.diagnostics import DiagnosticsSession


def test_link_warnings_are_recorded() -> None:
    session = DiagnosticsSession()
    session.record_link_warning("STATUS_POLL", "GET_STATUS 失败 1/3：BUSY\n  detail")
    session.record_link_warning("FRAME_DISCARDED", "CRC32 不匹配")
    assert [(event.category, event.detail) for event in session.events] == [
        ("STATUS_POLL", "GET_STATUS 失败 1/3：BUSY detail"),
        ("FRAME_DISCARDED", "CRC32 不匹配"),
    ]
