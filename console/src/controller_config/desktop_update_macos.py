"""Sparkle 2 custom user driver for the Console's nonmodal Qt update UI.

Sparkle owns download verification, staging, replacement and relaunch. All choices
that can send a quit event pass through the application's restart preparation.
The framework is loaded only from the running app, never another installed app.
"""
from __future__ import annotations

from collections.abc import Callable
import base64
from pathlib import Path
import platform
import plistlib
from urllib.parse import urlparse
from xml.parsers.expat import ExpatError

from PySide6.QtCore import QObject, Signal

from .desktop_update import DESKTOP_UPDATE_CHECK_INTERVAL_SECONDS, UpdateStatus

_SKIP, _INSTALL, _DISMISS = 0, 1, 2
_INSTALLING_STAGE = 2
_driver_class = None
_no_update_reason_key = None


def _validate_update_installation(
    running_app: Path,
    bundle_identifier: str,
    *,
    application_roots: tuple[Path, ...] | None = None,
) -> None:
    roots = application_roots or (
        Path("/Applications"),
        Path.home() / "Applications",
    )
    running_app = running_app.resolve()
    expected_paths = {
        (root / "BORING Console.app").resolve()
        for root in roots
    }
    if running_app not in expected_paths:
        raise RuntimeError(
            "请从“应用程序”中的 BORING Console 启动后再检查更新。"
        )

    installed_copies = []
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.glob("*.app"):
            try:
                info = plistlib.loads(
                    (candidate / "Contents/Info.plist").read_bytes()
                )
            except (OSError, plistlib.InvalidFileException, ExpatError):
                continue
            if info.get("CFBundleIdentifier") == bundle_identifier:
                installed_copies.append(candidate.resolve())
    if len(set(installed_copies)) > 1:
        raise RuntimeError(
            "检测到多个 BORING Console。请只保留“应用程序”中的最新版，再检查更新。"
        )


def _configure_automatic_checks(updater, config: dict) -> None:
    updater.setAutomaticallyDownloadsUpdates_(False)
    updater.setAutomaticallyChecksForUpdates_(True)
    updater.setUpdateCheckInterval_(
        int(config.get("check_interval_seconds", DESKTOP_UPDATE_CHECK_INTERVAL_SECONDS))
    )


def _load_driver_class(framework: Path):
    global _driver_class, _no_update_reason_key
    if _driver_class is not None:
        return _driver_class
    import objc
    from Foundation import NSBundle, NSObject

    bundle = NSBundle.bundleWithPath_(str(framework))
    if bundle is None or not bundle.load():
        raise RuntimeError("无法加载随应用打包的 Sparkle 更新组件。")

    constants = {}
    objc.loadBundleVariables(bundle, constants, [("SPUNoUpdateFoundReasonKey", b"@")])
    _no_update_reason_key = constants["SPUNoUpdateFoundReasonKey"]

    # Objective-C protocol metadata includes @? but not block argument types.
    # Register the actual Sparkle 2 signatures before creating the Python class;
    # otherwise a native callback may appear callable yet fail when invoked.
    callbacks = {
        b"showUpdatePermissionRequest:reply:": (3, b"@"),
        b"showUserInitiatedUpdateCheckWithCancellation:": (2, None),
        b"showUpdateFoundWithAppcastItem:state:reply:": (4, b"q"),
        b"showUpdateNotFoundWithError:acknowledgement:": (3, None),
        b"showUpdaterError:acknowledgement:": (3, None),
        b"showDownloadInitiatedWithCancellation:": (2, None),
        b"showReadyToInstallAndRelaunch:": (2, b"q"),
        b"showInstallingUpdateWithApplicationTerminated:retryTerminatingApplication:": (3, None),
        b"showUpdateInstalledAndRelaunched:acknowledgement:": (3, None),
    }
    for selector, (index, argument_type) in callbacks.items():
        arguments = {0: {"type": b"^v"}}
        if argument_type is not None:
            arguments[1] = {"type": argument_type}
        objc.registerMetaDataForSelector(
            b"NSObject", selector,
            {"arguments": {index: {"type": b"@?", "callable": {
                "retval": {"type": b"v"}, "arguments": arguments}}}},
        )
    objc.registerMetaDataForSelector(
        b"SPUUpdater", b"startUpdater:",
        {"arguments": {2: {"type_modifier": b"o"}}},
    )

    bool_type = b"B" if platform.machine() == "arm64" else b"c"

    class BORINGSparkleUserDriver(
        NSObject,
        protocols=[objc.protocolNamed("SPUUserDriver"), objc.protocolNamed("SPUUpdaterDelegate")],
    ):
        @objc.typedSelector(b"v@:@@?")
        def showUpdatePermissionRequest_reply_(self, request, reply):
            response = objc.lookUpClass("SUUpdatePermissionResponse").alloc().initWithAutomaticUpdateChecks_sendSystemProfile_(True, False)
            reply(response)

        @objc.typedSelector(b"v@:@?")
        def showUserInitiatedUpdateCheckWithCancellation_(self, cancellation):
            self.owner._cancel_reply = cancellation
            self.owner._publish("checking")

        @objc.typedSelector(b"v@:@@@?")
        def showUpdateFoundWithAppcastItem_state_reply_(self, item, state, reply):
            owner = self.owner
            owner._cancel_reply = None
            owner._version = str(item.displayVersionString() or item.versionString())
            if item.isInformationOnlyUpdate():
                reply(_DISMISS)
                owner._publish("failed", message="此版本需要手动安装，请查看官方版本说明。")
                return
            if int(state.stage()) == _INSTALLING_STAGE:
                owner._install_reply = reply
                owner._publish("ready")
            else:
                owner._download_reply = reply
                owner._publish("available")

        def showUpdateReleaseNotesWithDownloadData_(self, data):
            pass  # Release notes do not interrupt the compact Qt update flow.

        def showUpdateReleaseNotesFailedToDownloadWithError_(self, error):
            pass  # Optional notes failure does not prevent installing a verified update.

        @objc.typedSelector(b"v@:@@?")
        def showUpdateNotFoundWithError_acknowledgement_(self, error, acknowledgement):
            self.owner._not_found(error)
            acknowledgement()

        @objc.typedSelector(b"v@:@@?")
        def showUpdaterError_acknowledgement_(self, error, acknowledgement):
            self.owner._fail(str(error.localizedDescription()))
            acknowledgement()

        @objc.typedSelector(b"v@:@?")
        def showDownloadInitiatedWithCancellation_(self, cancellation):
            self.owner._cancel_reply = cancellation
            self.owner._received = self.owner._total = 0
            self.owner._publish("downloading")

        @objc.typedSelector(b"v@:Q")
        def showDownloadDidReceiveExpectedContentLength_(self, length):
            if self.owner.status.state != "downloading":
                return
            self.owner._total = int(length)
            self.owner._publish("downloading")

        @objc.typedSelector(b"v@:Q")
        def showDownloadDidReceiveDataOfLength_(self, length):
            # Sparkle can deliver queued progress after cancellation completed.
            # It must not reopen a cancelled download in the Qt UI.
            if self.owner.status.state != "downloading":
                return
            self.owner._received += int(length)
            self.owner._publish("downloading")

        def showDownloadDidStartExtractingUpdate(self):
            self.owner._cancel_reply = None
            self.owner._publish("verifying")

        @objc.typedSelector(b"v@:d")
        def showExtractionReceivedProgress_(self, progress):
            self.owner._publish("verifying")

        @objc.typedSelector(b"v@:@?")
        def showReadyToInstallAndRelaunch_(self, reply):
            self.owner._install_reply = reply
            self.owner._publish("ready")

        @objc.typedSelector(b"v@:" + bool_type + b"@?")
        def showInstallingUpdateWithApplicationTerminated_retryTerminatingApplication_(self, terminated, retry):
            self.owner._retry_termination = None if terminated else retry
            self.owner._publish("installing")

        @objc.typedSelector(b"v@:" + bool_type + b"@?")
        def showUpdateInstalledAndRelaunched_acknowledgement_(self, relaunched, acknowledgement):
            self.owner._publish("current")
            acknowledgement()

        def dismissUpdateInstallation(self):
            self.owner._clear_replies()
            if self.owner.status.state not in {"failed", "current", "installing"}:
                self.owner._publish("idle")

        @objc.typedSelector(bool_type + b"@:@")
        def updaterShouldPromptForPermissionToCheckForUpdates_(self, updater):
            return False

        @objc.typedSelector(bool_type + b"@:@@")
        def updater_shouldDownloadReleaseNotesForUpdate_(self, updater, item):
            return False

        def feedURLStringForUpdater_(self, updater):
            return self.owner._config["feed_url"]

        def updater_didAbortWithError_(self, updater, error):
            if int(error.code()) == 1001:  # SUNoUpdateError
                self.owner._not_found(error)
            else:
                self.owner._fail(str(error.localizedDescription()))

    _driver_class = BORINGSparkleUserDriver
    return _driver_class


class MacDesktopUpdater(QObject):
    changed = Signal(object)
    quit_requested = Signal()  # Sparkle itself sends the native quit event on macOS.

    def __init__(self, config: dict, prepare_restart: Callable[[], bool], parent=None):
        super().__init__(parent)
        self._config = config
        self._prepare_restart = prepare_restart
        self.status = UpdateStatus()
        self._updater = self._driver = None
        self._version = ""
        self._received = self._total = 0
        self._clear_replies()

    def _clear_replies(self):
        self._download_reply = self._install_reply = None
        self._cancel_reply = self._retry_termination = None

    def _publish(self, state, *, message="", retry_action=""):
        self.status = UpdateStatus(state=state, version=self._version,
                                   received=self._received, total=self._total, message=message, retry_action=retry_action)
        self.changed.emit(self.status)

    def _fail(self, message):
        # Sparkle error callbacks end the current session. Its old reply blocks
        # must never be reused to retry a download or installation.
        self._clear_replies()
        self._publish("failed", message=message, retry_action="check")

    def _not_found(self, error):
        reason = int((error.userInfo() or {}).get(_no_update_reason_key, 0))
        # Unknown or host/OS-incompatible updates are not proof of being current.
        self._clear_replies()
        self._publish("current" if reason in {1, 2} else "failed",
                      message=str(error.localizedDescription()),
                      retry_action="" if reason in {1, 2} else "check")

    def start(self):
        if self._updater is not None:
            return
        if not self._config.get("feed_url") or not self._config.get("public_key"):
            self._publish("unconfigured", message="此安装包尚未配置软件更新源。")
            return
        try:
            feed = urlparse(self._config["feed_url"])
            if feed.scheme != "https" or not feed.netloc:
                raise RuntimeError("软件更新源必须是 HTTPS 地址。")
            if len(base64.b64decode(self._config["public_key"], validate=True)) != 32:
                raise RuntimeError("软件更新公钥必须为 32 字节 Ed25519 公钥。")
            import objc
            from Foundation import NSBundle
            host = NSBundle.mainBundle()
            root = Path(str(host.bundlePath()))
            if root.suffix != ".app":
                self._publish("unconfigured", message="软件更新仅在已安装的 macOS 应用中可用。")
                return
            _validate_update_installation(
                root,
                str(host.objectForInfoDictionaryKey_("CFBundleIdentifier") or ""),
            )
            if str(host.objectForInfoDictionaryKey_("SUPublicEDKey") or "") != self._config["public_key"]:
                raise RuntimeError("更新公钥与应用签名配置不一致，请重新安装完整的软件包。")
            driver_class = _load_driver_class(root / "Contents/Frameworks/Sparkle.framework")
            self._driver = driver_class.alloc().init()
            self._driver.owner = self
            self._updater = objc.lookUpClass("SPUUpdater").alloc().initWithHostBundle_applicationBundle_userDriver_delegate_(
                host, host, self._driver, self._driver)
            _configure_automatic_checks(self._updater, self._config)
            success, error = self._updater.startUpdater_(None)
            if not success:
                self._updater = None
                raise RuntimeError(str(error.localizedDescription()))
            self._publish("idle")
        except Exception as error:
            self._updater = None
            self._fail(str(error))

    def check(self):
        if self._updater is None:
            self.start()
        if self._updater is not None and self._updater.canCheckForUpdates():
            try:
                self._updater.checkForUpdates()
            except Exception as error:
                self._fail(str(error))

    def download(self):
        if self.status.state != "available" or self._download_reply is None:
            return
        reply, self._download_reply = self._download_reply, None
        self._publish("downloading")
        try:
            reply(_INSTALL)
        except Exception as error:
            self._fail(str(error))

    def retry(self):
        if self.status.retry_action == "install":
            self.install()
        elif self.status.retry_action == "check":
            self.check()

    def cancel(self):
        if self._cancel_reply is not None:
            reply, self._cancel_reply = self._cancel_reply, None
            reply()
        elif self._download_reply is not None:
            reply, self._download_reply = self._download_reply, None
            reply(_DISMISS)
        elif self._install_reply is not None:
            reply, self._install_reply = self._install_reply, None
            # Dismiss at this stage would still install on quit; Skip cancels it.
            reply(_SKIP)
        else:
            return
        self._publish("idle")

    def install(self):
        reply = self._install_reply
        retry = self._retry_termination
        if reply is None and retry is None:
            return
        try:
            if not self._prepare_restart():
                return
        except Exception as error:
            # Restart preparation has not invoked a native callback yet. The
            # staged package and original reply remain available for retry.
            self._publish("ready", message=str(error), retry_action="install")
            return
        self._install_reply = self._retry_termination = None
        self._publish("installing")
        try:
            if reply is not None:
                reply(_INSTALL)
            else:
                retry()
        except Exception as error:
            self._fail(str(error))

    def shutdown(self):
        if self._updater is not None:
            self._updater.setAutomaticallyChecksForUpdates_(False)
        if self.status.state != "installing":
            self.cancel()
