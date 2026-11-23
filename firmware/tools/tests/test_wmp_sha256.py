"""Allocation-free SHA-256 used for request fingerprints and firmware hashing.

A returned unit NACKed every request with "could not fingerprint request":
the PSA SHA driver mallocs DMA-capable internal RAM for each operation and
that pool was exhausted. wmp_sha256 keeps its whole state in the caller's
struct, so these paths no longer depend on the heap.
"""

import ctypes
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/wmp_sha256"
CONFIG_PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
FIRMWARE_UPDATE = ROOT / "components/firmware_update/firmware_update.c"
CONFIG_STORE = ROOT / "components/config_store/config_store.c"


class Context(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_uint32 * 8),
        ("length", ctypes.c_uint64),
        ("block", ctypes.c_uint8 * 64),
        ("used", ctypes.c_size_t),
    ]


class WmpSha256Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "wmp_sha256.so"
        subprocess.run(
            ["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
             "-I", str(COMPONENT / "include"), str(COMPONENT / "wmp_sha256.c"),
             "-o", str(library)],
            check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))

    def one_shot(self, data: bytes) -> bytes:
        digest = (ctypes.c_uint8 * 32)()
        self.lib.wmp_sha256(data, ctypes.c_size_t(len(data)), digest)
        return bytes(digest)

    def streamed(self, data: bytes, chunk: int) -> bytes:
        context = Context()
        digest = (ctypes.c_uint8 * 32)()
        self.lib.wmp_sha256_init(ctypes.byref(context))
        for offset in range(0, len(data), chunk):
            piece = data[offset:offset + chunk]
            self.lib.wmp_sha256_update(ctypes.byref(context), piece,
                                       ctypes.c_size_t(len(piece)))
        self.lib.wmp_sha256_finish(ctypes.byref(context), digest)
        return bytes(digest)

    def test_known_vectors(self):
        self.assertEqual(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            self.one_shot(b"").hex())
        self.assertEqual(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            self.one_shot(b"abc").hex())

    def test_padding_boundaries_match_hashlib(self):
        for length in (1, 55, 56, 57, 63, 64, 65, 119, 120, 127, 128, 129):
            data = bytes((i * 7 + length) & 0xFF for i in range(length))
            with self.subTest(length=length):
                self.assertEqual(hashlib.sha256(data).digest(), self.one_shot(data))

    def test_streaming_matches_one_shot_for_any_chunking(self):
        data = os.urandom(20 * 1024 + 13)
        expected = hashlib.sha256(data).digest()
        for chunk in (1, 3, 63, 64, 65, 512, 4096, len(data)):
            with self.subTest(chunk=chunk):
                self.assertEqual(expected, self.streamed(data, chunk))

    def test_firmware_sized_image(self):
        data = os.urandom(1_190_544)
        self.assertEqual(hashlib.sha256(data).digest(), self.streamed(data, 4096))


class HashCallSiteTests(unittest.TestCase):
    """The maintenance paths must not reach the PSA hash driver again."""

    def test_protocol_and_update_paths_use_wmp_sha256(self):
        for path in (CONFIG_PROTOCOL, FIRMWARE_UPDATE, CONFIG_STORE):
            source = path.read_text()
            with self.subTest(path=path.name):
                self.assertNotIn("psa_hash_", source)
                self.assertIn("wmp_sha256", source)

    def test_fingerprint_failure_nack_is_gone(self):
        self.assertNotIn("could not fingerprint request", CONFIG_PROTOCOL.read_text())


if __name__ == "__main__":
    unittest.main()
