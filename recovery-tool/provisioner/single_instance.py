"""Keep exactly one recovery tool running per user session.

Two instances would race for the same USB serial port; on Windows the loser
only sees "access denied", and a hidden second server could keep a
calibration session open. The operating system releases the lock when the
process exits, including after a crash.
"""
from __future__ import annotations

import os
import subprocess
import sys

from .paths import data_root


MUTEX_NAME = "Local\\BORING-MIST-BLE-Recovery"
LOCK_FILENAME = "ble-recovery.lock"
ERROR_ALREADY_EXISTS = 183
APP_TITLE = "BORING MIST 固件更新与校准"


class InstanceLock:
    def __init__(self, release_callback):
        self._release = release_callback

    def release(self) -> None:
        if self._release is not None:
            release, self._release = self._release, None
            try:
                release()
            except OSError:
                pass


def _acquire_windows() -> InstanceLock | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        # Cannot create the mutex at all: do not lock the operator out.
        return InstanceLock(None)
    if error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return InstanceLock(lambda: kernel32.CloseHandle(handle))


def _acquire_posix() -> InstanceLock | None:
    import fcntl

    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except OSError:
        os.close(descriptor)
        return InstanceLock(None)
    os.ftruncate(descriptor, 0)
    os.write(descriptor, str(os.getpid()).encode("ascii"))

    def release() -> None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    return InstanceLock(release)


def acquire_single_instance() -> InstanceLock | None:
    """Return a held lock, or None when another instance already runs."""
    if sys.platform == "win32":
        return _acquire_windows()
    return _acquire_posix()


def notify_already_running() -> None:
    message = "工具已经在运行。请切换到已打开的窗口，不要同时打开两个。"
    if sys.platform == "win32":
        try:
            import ctypes

            MB_ICONINFORMATION = 0x40
            MB_TOPMOST = 0x40000
            ctypes.windll.user32.MessageBoxW(
                None, message, APP_TITLE, MB_ICONINFORMATION | MB_TOPMOST
            )
            return
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            subprocess.run(
                ["osascript", "-e", f'display alert "{APP_TITLE}" message "{message}"'],
                timeout=60,
                check=False,
            )
            return
        except Exception:
            pass
    print(message, flush=True)
