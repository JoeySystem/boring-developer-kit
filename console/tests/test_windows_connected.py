import json

import pytest

from controller_config.transport.windows_connected import connected_boring_rows


def test_connected_filter_excludes_nearby_history_and_unrelated_devices():
    rows = [
        dict(address="AABBCCDDEE01", name="BORING MIST", connected=False),
        dict(address="AABBCCDDEE02", name="Other keyboard", connected=True),
        dict(address="AABBCCDDEE03", name="Office keyboard", connected=True,
             services=["7e2f0001-7a91-4a5b-9c2d-6e8f4b524731"]),
        dict(address="AABBCCDDEE04", name="BORING MIST", connected=True),
        dict(address="AABBCCDDEE04", name="BORING MIST", connected=True),
    ]
    assert connected_boring_rows(json.dumps({"devices": rows}).encode()) == (
        ("aa:bb:cc:dd:ee:03", "Office keyboard"), ("aa:bb:cc:dd:ee:04", "BORING MIST"))


def test_connected_query_invalid_output_is_failure_not_empty_list():
    with pytest.raises(ValueError):
        connected_boring_rows(b"access denied")
    assert connected_boring_rows(b'{"devices": []}') == ()


def test_partial_access_failure_keeps_usable_boring_device(qtbot):
    from types import SimpleNamespace
    from controller_config.transport.windows_connected import WindowsConnectedDeviceFinder
    finder = WindowsConnectedDeviceFinder()
    finder._process = SimpleNamespace(readAllStandardOutput=lambda: json.dumps({
        "devices": [dict(address="AABBCCDDEE03", name="BORING MIST", connected=True)],
        "unavailable": ["BORING Office"],
    }).encode())
    found, warnings, failures, finished = [], [], [], []
    finder.device_found.connect(lambda *args: found.append(args))
    finder.warning.connect(warnings.append)
    finder.failed.connect(failures.append)
    finder.finished.connect(lambda: finished.append(True))
    finder._completed(0, None)
    assert found == [("aa:bb:cc:dd:ee:03", "BORING MIST")]
    assert warnings and not failures and finished == [True]


def test_access_denied_is_not_reported_as_successfully_empty(qtbot):
    from types import SimpleNamespace
    from controller_config.transport.windows_connected import WindowsConnectedDeviceFinder
    finder = WindowsConnectedDeviceFinder()
    finder._process = SimpleNamespace(readAllStandardOutput=lambda: b'{"devices": [], "unavailable": ["BORING MIST"]}')
    failures, finished = [], []
    finder.failed.connect(failures.append)
    finder.finished.connect(lambda: finished.append(True))
    finder._completed(0, None)
    assert failures and not finished
