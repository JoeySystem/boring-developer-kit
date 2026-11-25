"""Last confirmed device configuration, kept on this computer (SH-005).

Reading the full configuration over Bluetooth takes seconds: replies are
indications, confirmed one packet at a time (about 4 KB/s), and each extra
AI profile adds about 2 KB. HELLO already reports the active configuration's
``generation`` and ``digest``. When an authenticated device reports the same
pair as the copy saved here, and the saved copy still hashes to that digest,
the handshake uses it instead of sending GET_CONFIG.

Only authenticated sessions read or write the cache, keyed by the serial in
the verified device certificate. A copy is saved only when its configuration
hashes to its digest, so a cache hit is byte-for-byte the configuration the
device would have returned.

The BLE worker thread reads; the GUI thread writes. Writes replace the file
atomically, so a reader sees either the old copy or the new one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from controller_config.protocol.framing import FrameError, canonical_json_bytes


CACHE_FORMAT = "boring-config-cache"
CACHE_VERSION = 1
MAX_CACHE_FILE_BYTES = 256 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")


def config_digest(config: dict[str, Any]) -> str:
    """The protocol's configuration digest: SHA-256 of the JCS bytes."""

    return hashlib.sha256(canonical_json_bytes(config)).hexdigest()


def _revision(result: object) -> tuple[int, str] | None:
    if not isinstance(result, dict):
        return None
    generation = result.get("generation")
    digest = result.get("digest")
    if (type(generation) is not int or generation < 0
            or not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None):
        return None
    return generation, digest


def _self_consistent(result: dict[str, Any]) -> bool:
    config = result.get("config")
    if not isinstance(config, dict):
        return False
    try:
        return config_digest(config) == result.get("digest")
    except FrameError:
        return False


class DeviceConfigCache:
    """One JSON file per authenticated device serial."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> "DeviceConfigCache":
        from PySide6.QtCore import QStandardPaths

        root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        return cls(Path(root) / "config-cache")

    @property
    def directory(self) -> Path:
        return self._directory

    def path_for(self, serial: str) -> Path:
        name = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:32]
        return self._directory / f"{name}.json"

    def lookup(self, serial: str, generation: int, digest: str) -> dict[str, Any] | None:
        """The saved GET_CONFIG result when it matches ``generation``/``digest``.

        Returns None on any mismatch or damaged file; the caller then reads
        the configuration from the device as before.
        """

        if not serial or _revision({"generation": generation, "digest": digest}) is None:
            return None
        path = self.path_for(serial)
        try:
            with self._lock:
                if path.stat().st_size > MAX_CACHE_FILE_BYTES:
                    return None
                raw = path.read_bytes()
            document = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        if (not isinstance(document, dict)
                or document.get("format") != CACHE_FORMAT
                or document.get("version") != CACHE_VERSION
                or document.get("serial") != serial):
            return None
        result = document.get("result")
        if _revision(result) != (generation, digest) or not _self_consistent(result):
            return None
        return copy.deepcopy(result)

    def store(self, serial: str, result: dict[str, Any]) -> bool:
        """Save a confirmed GET_CONFIG result. Inconsistent results are refused."""

        if not serial or _revision(result) is None or not _self_consistent(result):
            return False
        document = {
            "format": CACHE_FORMAT,
            "version": CACHE_VERSION,
            "serial": serial,
            "result": {
                key: copy.deepcopy(result[key])
                for key in ("generation", "digest", "config")
            },
        }
        try:
            payload = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return False
        if len(payload) > MAX_CACHE_FILE_BYTES:
            return False
        path = self.path_for(serial)
        with self._lock:
            try:
                self._directory.mkdir(parents=True, exist_ok=True)
                handle, temporary = tempfile.mkstemp(
                    prefix=".config-cache-", suffix=".tmp", dir=self._directory,
                )
                try:
                    with os.fdopen(handle, "wb") as stream:
                        stream.write(payload)
                    os.replace(temporary, path)
                except BaseException:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                    raise
            except OSError:
                return False
        return True

    def forget(self, serial: str) -> None:
        if not serial:
            return
        with self._lock:
            try:
                self.path_for(serial).unlink()
            except OSError:
                pass
