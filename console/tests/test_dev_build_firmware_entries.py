"""Internal development packages hide the two ways back to official firmware
(design decision, task): "恢复官方固件…" and "查看官方版本 → 切换到官方固件…".
The bundled official firmware lacks the desktop source under test. Official
and DIY packages keep both entries; the firmware update flow is unchanged."""
from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from controller_config.build_identity import BuildIdentity
from controller_config.firmware_release import (
    RemoteFirmwareCheck,
    RemoteFirmwareRelease,
    RemoteFirmwareState,
)
from controller_config.firmware_update import FirmwareUpdateState
from controller_config.text_catalog import get_text_catalog
from controller_config.transport.demo import _power_v2_snapshot
from controller_config.viewmodels.main import MainViewModel
from controller_config.views.main_window import MainWindow
from cryptography.hazmat.primitives import serialization

from controller_config import firmware_signature
from test_firmware_release import _TEST_SIGNING_KEY
from test_firmware_transaction import FirmwareGateway, _package

DEV = BuildIdentity("official", dev_label="豆包联动", dev_date="20261107")
OFFICIAL = BuildIdentity("official")
DIY = BuildIdentity("custom")
DEV_FIRMWARE = "custom-desktop-events-20261104.01-gb4b9c672"
NOTICE = "开发版不提供切回官方固件。换固件请按开发说明里的刷写步骤。"


@pytest.fixture
def firmware_page(qtbot, contract):
    made = []

    def make(identity: BuildIdentity, *, build_id: str = DEV_FIRMWARE, hardware_id: str | None = None):
        gateway = FirmwareGateway()
        vm = MainViewModel(gateway, contract)
        # A checked-nothing-yet page: the state in which "查看官方版本" shows.
        vm._set_remote_firmware(RemoteFirmwareCheck(state=RemoteFirmwareState.IDLE, message=""))
        snapshot = _power_v2_snapshot(contract, read_only=False)
        snapshot = replace(snapshot, versions={**snapshot.versions, "build_id": build_id})
        if hardware_id is not None:
            snapshot = replace(snapshot, identity={**snapshot.identity, "hardware_id": hardware_id})
        gateway.snapshot_ready.emit(snapshot)
        window = MainWindow(vm, build_identity=identity)
        qtbot.addWidget(window)
        vm.navigate("firmware")
        window.show()
        made.append(vm)
        return window, vm, gateway

    yield make
    for vm in made:
        vm.shutdown()


def _official_offer(vm) -> RemoteFirmwareRelease:
    identity = vm.model.snapshot.identity
    return RemoteFirmwareRelease(
        {"version": "0.3.0-alpha.1", "build_id": "20261102.01-g0000000", "size": 123,
         "product_id": identity["product_id"], "hardware_id": identity["hardware_id"]},
        "https://example.test/manifest.json", "https://example.test/a.bin",
    )


def _rerender(window, vm, state: RemoteFirmwareCheck) -> None:
    vm._set_remote_firmware(state)
    vm.navigate("overview")
    vm.navigate("firmware")


def _button(window, name):
    return window.findChild(QPushButton, name)


def _label(window, name):
    return window.findChild(QLabel, name)


# --- development package: both entries gone, one sentence instead ---------------

def test_dev_package_hides_both_entries_on_the_dev_firmware(firmware_page):
    window, vm, gateway = firmware_page(DEV)
    assert vm.firmware_origin == "custom"
    assert _button(window, "checkRemoteFirmware") is None
    assert _button(window, "prepareOfficialRecovery") is None
    assert _label(window, "officialRecoveryHint") is None
    notice = _label(window, "devBuildFirmwareSwitchNotice")
    assert notice is not None and notice.isVisible() and notice.text() == NOTICE
    assert not [c for c in gateway.commands if c.name.startswith("FW_")]


def test_dev_package_never_offers_switch_even_with_a_cached_official_offer(firmware_page):
    window, vm, _ = firmware_page(DEV)
    for state in (RemoteFirmwareState.RESTORE_AVAILABLE, RemoteFirmwareState.FAILED):
        _rerender(window, vm, RemoteFirmwareCheck(
            state=state, message="", release=_official_offer(vm), restoration=True,
        ))
        assert _button(window, "installRemoteFirmware") is None, state
        assert _button(window, "checkRemoteFirmware") is None, state
        assert _label(window, "devBuildFirmwareSwitchNotice") is not None


@pytest.mark.parametrize("identity, shown", ((DEV, False), (OFFICIAL, True), (DIY, True)),
                         ids=("dev", "official", "diy"))
def test_switch_install_after_a_cached_download_only_outside_dev(
    firmware_page, tmp_path, contract, monkeypatch, identity, shown,
):
    # Only here: the test package is signed with the test key. The bundled
    # recovery tests below must keep verifying against the real key.
    monkeypatch.setattr(
        firmware_signature, "default_firmware_public_key",
        lambda: _TEST_SIGNING_KEY.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ),
    )
    window, vm, _ = firmware_page(identity)
    vm.load_firmware_package(_package(tmp_path, contract))
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    _rerender(window, vm, RemoteFirmwareCheck(
        state=RemoteFirmwareState.DOWNLOADED, message="", release=_official_offer(vm), restoration=True,
    ))
    install = _button(window, "startFirmwareUpdate")
    assert (install is not None) is shown
    if shown:
        assert install.text() == "切换到官方固件…"


def test_dev_package_recovery_action_does_nothing_if_reached(firmware_page):
    window, vm, gateway = firmware_page(DEV)
    window._firmware_page.prepare_bundled_recovery()
    assert vm.firmware_update.package is None
    assert not [c for c in gateway.commands if c.name.startswith("FW_")]


def test_dev_package_with_unknown_firmware_origin_also_hides_the_switch(firmware_page):
    window, vm, _ = firmware_page(DEV, build_id="local-build-1")
    assert vm.firmware_origin == "unknown"
    assert _button(window, "checkRemoteFirmware") is None
    assert _button(window, "prepareOfficialRecovery") is None
    assert _label(window, "devBuildFirmwareSwitchNotice") is not None


@pytest.fixture
def official_origin(monkeypatch):
    monkeypatch.setattr(MainViewModel, "firmware_origin", property(lambda self: "official"))


def test_dev_package_keeps_online_updates_for_official_firmware(firmware_page, official_origin):
    # Not a switch back: an officially flashed sample still checks as before.
    window, vm, _ = firmware_page(DEV, build_id="20261030.01-gabcdef0")
    check = _button(window, "checkRemoteFirmware")
    assert check is not None and check.text() == "检查在线更新"
    assert _button(window, "prepareOfficialRecovery") is None
    assert _label(window, "devBuildFirmwareSwitchNotice") is not None


def test_dev_package_on_other_hardware_with_official_firmware_shows_nothing_extra(firmware_page, official_origin):
    window, vm, _ = firmware_page(DEV, build_id="20261030.01-gabcdef0", hardware_id="different-hardware")
    assert _button(window, "prepareOfficialRecovery") is None
    assert _label(window, "devBuildFirmwareSwitchNotice") is None


# --- official and DIY packages: unchanged ---------------------------------------

@pytest.mark.parametrize("identity", (OFFICIAL, DIY), ids=("official", "diy"))
def test_release_packages_keep_both_entries(firmware_page, identity):
    window, vm, _ = firmware_page(identity)
    assert vm.firmware_origin == "custom"
    check = _button(window, "checkRemoteFirmware")
    assert check is not None and check.text() == "查看官方版本"
    recovery = _button(window, "prepareOfficialRecovery")
    assert recovery is not None and recovery.isVisible()
    assert _label(window, "officialRecoveryHint") is not None
    assert _label(window, "devBuildFirmwareSwitchNotice") is None
    _rerender(window, vm, RemoteFirmwareCheck(
        state=RemoteFirmwareState.RESTORE_AVAILABLE, message="", release=_official_offer(vm), restoration=True,
    ))
    install = _button(window, "installRemoteFirmware")
    assert install is not None and install.text() == "切换到官方固件…"


@pytest.mark.parametrize("identity", (OFFICIAL, DIY), ids=("official", "diy"))
@pytest.mark.skip(reason="社区版不内置官方恢复固件 ZIP")
def test_release_packages_still_prepare_the_bundled_recovery(firmware_page, qtbot, identity):
    window, vm, gateway = firmware_page(identity)
    _button(window, "prepareOfficialRecovery").click()
    qtbot.waitUntil(lambda: vm.firmware_update.package is not None)
    assert vm.firmware_update.package.source_kind == "official"
    assert vm.firmware_update.state is FirmwareUpdateState.PACKAGE_READY
    assert not [c for c in gateway.commands if c.name.startswith("FW_")]


@pytest.mark.parametrize("language", ("en_US", "ja_JP"))
def test_notice_is_translated(language):
    translated = get_text_catalog().translate(NOTICE, language)
    assert translated != NOTICE and ("developer notes" in translated or "開発メモ" in translated)
