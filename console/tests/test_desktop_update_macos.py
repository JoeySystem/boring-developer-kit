"""Exercise restart gating and real Objective-C callback block marshalling."""
from pathlib import Path
import os
import plistlib
import subprocess
import sys

import pytest

import controller_config.desktop_update_macos as macos_updater
from controller_config.desktop_update_macos import (
    MacDesktopUpdater,
    _load_driver_class,
    _validate_update_installation,
)


def _app(path: Path, *, bundle_id: str = "com.boring.console") -> Path:
    app = path
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
    return app


def test_automatic_update_checks_run_hourly():
    calls = []

    class NativeUpdater:
        def setAutomaticallyDownloadsUpdates_(self, value):
            calls.append(("downloads", value))

        def setAutomaticallyChecksForUpdates_(self, value):
            calls.append(("checks", value))

        def setUpdateCheckInterval_(self, value):
            calls.append(("interval", value))

    macos_updater._configure_automatic_checks(NativeUpdater(), {})

    assert calls == [
        ("downloads", False),
        ("checks", True),
        ("interval", 60 * 60),
    ]


def test_update_installation_accepts_single_canonical_app(tmp_path):
    applications = tmp_path / "Applications"
    current = _app(applications / "BORING Console.app")

    _validate_update_installation(
        current,
        "com.boring.console",
        application_roots=(applications,),
    )


def test_update_installation_rejects_renamed_running_copy(tmp_path):
    applications = tmp_path / "Applications"
    backup = _app(applications / "BORING Console.backup-0.1.50.app")

    with pytest.raises(RuntimeError, match="应用程序"):
        _validate_update_installation(
            backup,
            "com.boring.console",
            application_roots=(applications,),
        )


def test_update_installation_rejects_multiple_installed_copies(tmp_path):
    applications = tmp_path / "Applications"
    current = _app(applications / "BORING Console.app")
    _app(applications / "BORING Console Old.app")

    with pytest.raises(RuntimeError, match="多个 BORING Console"):
        _validate_update_installation(
            current,
            "com.boring.console",
            application_roots=(applications,),
        )


def test_update_installation_ignores_unrelated_app_with_invalid_plist(tmp_path):
    applications = tmp_path / "Applications"
    current = _app(applications / "BORING Console.app")
    invalid_info = applications / "Unrelated.app/Contents/Info.plist"
    invalid_info.parent.mkdir(parents=True)
    invalid_info.write_bytes(b"not a plist")

    _validate_update_installation(
        current,
        "com.boring.console",
        application_roots=(applications,),
    )


def test_install_waits_for_restart_preparation(qapp):
    allow = [False]
    choices = []
    updater = MacDesktopUpdater({}, lambda: allow[0])
    updater._install_reply = choices.append
    updater._publish("ready")
    updater.install()
    assert choices == []
    assert updater.status.state == "ready"
    allow[0] = True
    updater.install()
    assert choices == [1]
    assert updater.status.state == "installing"


def test_retry_termination_also_requires_preparation(qapp):
    allow = [False]
    calls = []
    updater = MacDesktopUpdater({}, lambda: allow[0])
    updater._retry_termination = lambda: calls.append("quit")
    updater.install()
    assert calls == []
    allow[0] = True
    updater.install()
    assert calls == ["quit"]


def test_cancel_ready_cancels_install_on_quit(qapp):
    choices = []
    updater = MacDesktopUpdater({}, lambda: True)
    updater._install_reply = choices.append
    updater._publish("ready")
    updater.cancel()
    assert choices == [0]  # Skip cancels staged install; Dismiss would install on quit.
    assert updater.status.state == "idle"


def test_unconfigured_does_not_load_native_framework(qapp, monkeypatch):
    monkeypatch.setattr("controller_config.desktop_update_macos._load_driver_class",
                        lambda path: pytest.fail("must not load framework"))
    updater = MacDesktopUpdater({}, lambda: pytest.fail("must not restart"))
    updater.start()
    assert updater.status.state == "unconfigured"


@pytest.mark.parametrize("config", [
    {"feed_url": "http://example.com/appcast.xml", "public_key": "A" * 43 + "="},
    {"feed_url": "https://example.com/appcast.xml", "public_key": "bad"},
])
def test_invalid_source_fails_before_native_start(config, qapp, monkeypatch):
    monkeypatch.setattr("controller_config.desktop_update_macos._load_driver_class",
                        lambda path: pytest.fail("invalid source must not load"))
    owner = MacDesktopUpdater(config, lambda: False)
    owner.start()
    assert owner.status.state == "failed"


def test_failed_native_install_is_reported_after_preparation(qapp):
    prepared = []
    owner = MacDesktopUpdater({}, lambda: prepared.append(True) or True)
    def fail(choice):
        raise RuntimeError("installer unavailable")
    owner._install_reply = fail
    owner.install()
    assert prepared == [True]
    assert owner.status.state == "failed"
    assert "installer unavailable" in owner.status.message


@pytest.fixture
def native_driver(tmp_path, qapp):
    framework = os.environ.get("BORING_TEST_SPARKLE_FRAMEWORK")
    if sys.platform != "darwin" or not framework:
        pytest.skip("Requires an official Sparkle framework via BORING_TEST_SPARKLE_FRAMEWORK")
    import objc
    from Foundation import NSBundle
    driver = _load_driver_class(Path(framework)).alloc().init()
    # Native stack blocks deliberately escape the Objective-C method; this catches
    # both absent signature metadata and callback lifetime bugs hidden by lambdas.
    source = tmp_path / "Callbacks.m"
    source.write_text('''
#import <Foundation/Foundation.h>
#import <Sparkle/Sparkle.h>
@interface BORINGUpdateTestCallbacks : NSObject
@property NSInteger choice;
@property BOOL acknowledged;
- (void)ready:(id<SPUUserDriver>)driver;
- (void)progress:(id<SPUUserDriver>)driver;
- (void)lateProgress:(id<SPUUserDriver>)driver;
- (void)failed:(id<SPUUserDriver>)driver;
- (void)found:(id<SPUUserDriver>)driver stage:(NSInteger)stage;
@end
@interface BORINGTestItem : NSObject
@end
@implementation BORINGTestItem
- (NSString *)displayVersionString { return @"9.0"; }
- (NSString *)versionString { return @"9.0"; }
- (BOOL)isInformationOnlyUpdate { return NO; }
@end
@interface BORINGTestState : NSObject
@property NSInteger stage;
@end
@implementation BORINGTestState
@end
@implementation BORINGUpdateTestCallbacks
- (void)ready:(id<SPUUserDriver>)driver {
    self.choice = -1;
    [driver showReadyToInstallAndRelaunch:^(SPUUserUpdateChoice choice) { self.choice = choice; }];
}
- (void)progress:(id<SPUUserDriver>)driver {
    [driver showDownloadInitiatedWithCancellation:^{ self.acknowledged = YES; }];
    [driver showDownloadDidReceiveExpectedContentLength:100];
    [driver showDownloadDidReceiveDataOfLength:37];
}
- (void)lateProgress:(id<SPUUserDriver>)driver {
    [driver showDownloadDidReceiveExpectedContentLength:100];
    [driver showDownloadDidReceiveDataOfLength:7];
}
- (void)failed:(id<SPUUserDriver>)driver {
    NSError *error = [NSError errorWithDomain:@"test" code:9 userInfo:@{NSLocalizedDescriptionKey:@"network failed"}];
    [driver showUpdaterError:error acknowledgement:^{ self.acknowledged = YES; }];
}
- (void)found:(id<SPUUserDriver>)driver stage:(NSInteger)stage {
    self.choice = -1;
    BORINGTestState *state = [BORINGTestState new]; state.stage = stage;
    [driver showUpdateFoundWithAppcastItem:(id)[BORINGTestItem new] state:(id)state reply:^(SPUUserUpdateChoice choice) { self.choice = choice; }];
}
@end
''')
    library = tmp_path / "Callbacks.dylib"
    subprocess.run(["clang", "-dynamiclib", "-fobjc-arc", "-fblocks", "-framework", "Foundation",
                    "-F", str(Path(framework).parent), str(source), "-o", str(library)], check=True,
                   capture_output=True, text=True)
    import ctypes
    loaded = ctypes.CDLL(str(library))
    callbacks = objc.lookUpClass("BORINGUpdateTestCallbacks").alloc().init()
    return driver, callbacks, loaded


def test_native_blocks_round_trip_and_resumed_install_guard(native_driver, qapp, tmp_path):
    driver, callbacks, loaded = native_driver
    allow = [False]
    owner = MacDesktopUpdater({}, lambda: allow[0])
    driver.owner = owner
    callbacks.progress_(driver)
    assert (owner.status.state, owner.status.received, owner.status.total) == ("downloading", 37, 100)
    owner.cancel()
    assert callbacks.acknowledged()
    callbacks.lateProgress_(driver)
    assert owner.status.state == "idle"
    assert owner.status.received == 37
    callbacks.setAcknowledged_(False)
    callbacks.failed_(driver)
    assert owner.status.state == "failed"
    assert callbacks.acknowledged()
    callbacks.ready_(driver)
    owner.install()
    assert callbacks.choice() == -1
    allow[0] = True
    owner.install()
    assert callbacks.choice() == 1
    # Sparkle may resume an already-staged update in showUpdateFound: this must
    # never be exposed as the ordinary Download button (which has no quit guard).
    allow[0] = False
    callbacks.found_stage_(driver, 2)
    assert owner.status.state == "ready"
    owner.download()
    owner.install()
    assert callbacks.choice() == -1
    allow[0] = True
    owner.install()
    assert callbacks.choice() == 1
    callbacks.found_stage_(driver, 0)
    assert owner.status.state == "available"
    owner.download()
    assert callbacks.choice() == 1

    # Start a real SPUUpdater against an isolated application-shaped host. No
    # network request or installation is triggered by this startup smoke.
    import plistlib
    import shutil
    import objc
    from Foundation import NSBundle
    root = tmp_path / "Update Smoke.app"
    (root / "Contents/MacOS").mkdir(parents=True)
    shutil.copy("/usr/bin/true", root / "Contents/MacOS/smoke")
    (root / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "local.boring.update.smoke", "CFBundleName": "Update Smoke",
        "CFBundleExecutable": "smoke", "CFBundleVersion": "0.1.14",
        "CFBundleShortVersionString": "0.1.14",
        "SUPublicEDKey": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "SUEnableAutomaticChecks": False, "SUAllowsAutomaticUpdates": False,
        "SUAutomaticallyUpdate": False, "SUFeedURL": "https://example.invalid/appcast.xml",
    }))
    owner._config["feed_url"] = "https://example.invalid/appcast.xml"
    host = NSBundle.bundleWithPath_(str(root))
    native = objc.lookUpClass("SPUUpdater").alloc().initWithHostBundle_applicationBundle_userDriver_delegate_(
        host, host, driver, driver)
    success, error = native.startUpdater_(None)
    assert success, str(error)
    macos_updater._configure_automatic_checks(native, {})
    assert native.canCheckForUpdates()
    assert native.automaticallyChecksForUpdates()
    assert int(native.updateCheckInterval()) == 60 * 60
    assert not native.automaticallyDownloadsUpdates()


def test_preparation_failure_keeps_install_reply_for_explicit_retry(qapp):
    calls = []
    def fail():
        raise OSError("Unable to save draft")
    owner = MacDesktopUpdater({}, fail)
    owner._install_reply = calls.append
    owner._publish("ready")
    owner.install()
    assert owner.status.state == "ready"
    assert owner.status.retry_action == "install"
    assert calls == []
    owner._prepare_restart = lambda: False
    owner.retry()
    assert calls == []
    owner._prepare_restart = lambda: True
    owner.retry()
    assert calls == [1]
    assert owner.status.state == "installing"
    assert owner.status.retry_action == ""


def test_native_failure_discards_callbacks_and_retries_check(qapp):
    calls = []
    class NativeUpdater:
        def canCheckForUpdates(self):
            return True
        def checkForUpdates(self):
            calls.append("check")
    owner = MacDesktopUpdater({}, lambda: True)
    owner._updater = NativeUpdater()
    owner._install_reply = lambda _: pytest.fail("stale callback")
    owner._download_reply = lambda _: pytest.fail("stale callback")
    owner._cancel_reply = lambda: pytest.fail("stale callback")
    owner._fail("network failed")
    assert owner.status.retry_action == "check"
    assert owner._install_reply is owner._download_reply is owner._cancel_reply is None
    owner.retry()
    assert calls == ["check"]


def test_native_install_reply_exception_cannot_reuse_callback(qapp):
    calls = []
    def failed_reply(choice):
        calls.append(choice)
        raise RuntimeError("installer unavailable")
    owner = MacDesktopUpdater({}, lambda: True)
    owner._install_reply = failed_reply
    owner.install()
    assert owner.status.retry_action == "check"
    assert owner._install_reply is None
    owner.install()
    assert calls == [1]
