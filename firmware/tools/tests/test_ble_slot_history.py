"""Persisted BLE slot history (last 8 operations, with their source).

68DE8 lost both pairings while only the last slot operation was kept, so the
first clear could only be inferred. The history records select, clear and
pair-save operations, who asked (key, protocol, pairing), the result, the
uptime and the boot, and is written to NVS on every operation.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/codex_micro"
MODEL = COMPONENT / "ble_slot_history_model.c"
BLE = COMPONENT / "codex_micro_ble.c"
STUB = COMPONENT / "codex_micro_stub.c"
HEADER = COMPONENT / "include/codex_micro.h"
ACTION = ROOT / "components/action_engine/action_engine.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
LENGTH = 8


class Entry(ctypes.Structure):
    _fields_ = [("op", ctypes.c_uint8), ("slot", ctypes.c_uint8),
                ("source", ctypes.c_uint8), ("timeout", ctypes.c_uint8),
                ("error", ctypes.c_int32), ("uptime_ms", ctypes.c_uint32),
                ("boot", ctypes.c_uint32)]


class History(ctypes.Structure):
    _fields_ = [("magic", ctypes.c_uint32), ("version", ctypes.c_uint32),
                ("count", ctypes.c_uint32), ("entries", Entry * LENGTH)]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


class HistoryModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "history.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))

    def setUp(self):
        self.history = History()
        self.lib.ble_slot_history_reset(ctypes.byref(self.history))

    def record(self, op, slot, boot=1):
        entry = Entry(op=op, slot=slot, source=2, timeout=0, error=0,
                      uptime_ms=slot * 1000, boot=boot)
        self.lib.ble_slot_history_record(ctypes.byref(self.history), ctypes.byref(entry))

    def test_newest_first_and_bounded(self):
        for slot in range(1, 11):
            self.record(1, slot)
        self.assertEqual(LENGTH, self.history.count)
        self.assertEqual([10, 9, 8, 7, 6, 5, 4, 3],
                         [self.history.entries[i].slot for i in range(LENGTH)])

    def test_round_trip_through_a_blob(self):
        self.record(1, 2, boot=5)
        blob = bytes(self.history)
        loaded = History()
        self.lib.ble_slot_history_load(ctypes.byref(loaded), blob, len(blob))
        self.assertEqual((1, 2, 5), (loaded.count, loaded.entries[0].slot,
                                     loaded.entries[0].boot))

    def test_malformed_blobs_start_empty(self):
        self.record(1, 2)
        good = bytearray(bytes(self.history))
        for blob in (bytes(good[:-1]), b"\0" * len(good),
                     bytes(good[:8]) + (LENGTH + 1).to_bytes(4, "little") + bytes(good[12:])):
            loaded = History()
            self.lib.ble_slot_history_load(ctypes.byref(loaded), blob, len(blob))
            with self.subTest(blob=len(blob)):
                self.assertEqual(0, loaded.count)
                self.assertEqual(0x48534C42, loaded.magic)


class HistoryCallSiteTests(unittest.TestCase):
    def setUp(self):
        self.ble = BLE.read_text()

    def test_every_operation_is_recorded_and_persisted(self):
        note = function(self.ble, "static void note_ble_slot_op(")
        self.assertIn("ble_slot_history_record(&s_slot_history, &entry);", note)
        self.assertIn("nvs_set_blob(s_ble_peer_nvs, BLE_SLOT_HISTORY_NVS_KEY", note)
        self.assertIn("nvs_commit(s_ble_peer_nvs)", note)
        load = function(self.ble, "static esp_err_t load_ble_peer_slots(void)")
        self.assertIn("load_ble_slot_history();", load)

    def test_sources_follow_the_caller(self):
        select = function(self.ble, "esp_err_t codex_micro_select_ble_slot_from(")
        self.assertIn("CODEX_MICRO_BLE_SLOT_OP_SELECT, slot, result, false, source", select)
        clear = function(self.ble, "esp_err_t codex_micro_clear_ble_slot_from(")
        self.assertIn("atomic_store(&s_ble_slot_clear_source, (uint8_t)source);", clear)
        completion = function(self.ble, "static void complete_pending_ble_slot_clear(")
        self.assertIn("&s_ble_slot_clear_source", completion)
        self.assertIn("CODEX_MICRO_BLE_SLOT_SOURCE_PAIRING", self.ble)
        for plain, source in (("esp_err_t codex_micro_select_ble_slot(uint8_t slot)", "PROTOCOL"),
                              ("esp_err_t codex_micro_clear_ble_slot(uint8_t slot)", "PROTOCOL")):
            self.assertIn(f"CODEX_MICRO_BLE_SLOT_SOURCE_{source}", function(self.ble, plain))
        action = ACTION.read_text()
        self.assertIn("codex_micro_select_ble_slot_from(\n            slot, CODEX_MICRO_BLE_SLOT_SOURCE_KEY)",
                      action)
        # The Bluetooth key only selects; clearing is left to the protocol command.
        self.assertNotIn("codex_micro_clear_ble_slot", action)

    def test_header_stub_and_status_agree(self):
        for text in (HEADER.read_text(), STUB.read_text(), self.ble):
            self.assertIn("codex_micro_ble_slot_history(", text)
            self.assertIn("codex_micro_clear_ble_slot_from(", text)
        protocol = PROTOCOL.read_text()
        self.assertIn('cJSON_AddArrayToObject(ble, "ble_slot_history")', protocol)
        for name in ('"select"', '"key"', '"protocol"', '"pairing"', '"boots_ago"'):
            self.assertIn(name, protocol)


class LowMemoryPersistenceTests(unittest.TestCase):
    def test_episode_is_written_from_the_main_loop(self):
        protocol = PROTOCOL.read_text()
        tick = function(protocol, "static void heap_watch_tick(void *context)")
        self.assertIn("atomic_store(&s_heap_persist_pending, true);", tick)
        self.assertNotIn("nvs_", tick)
        poll = function(protocol, "void config_protocol_poll(void)")
        self.assertIn("persist_heap_watch();", poll)
        persist = function(protocol, "static void persist_heap_watch(void)")
        self.assertIn("nvs_set_blob(handle, HEAP_WATCH_NVS_KEY", persist)
        self.assertIn('cJSON_AddObjectToObject(watch, "persisted")', protocol)


if __name__ == "__main__":
    unittest.main()
