"""Unusable NVS partitions no longer stop the boot (#9), and since review A2
(Joey, 2026-11-10) they are never erased automatically: a fresh update rolls
back; a confirmed image leaves the partition untouched, runs without it
(config read-only on defaults, BLE off) and reports it. Clearing is explicit
(the safe-mode factory reset)."""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/storage_recovery"

ESP_OK = 0
NO_FREE_PAGES = 0x110D
NEW_VERSION_FOUND = 0x1110
NOT_FOUND = 0x1102
NONE, ROLLBACK, KEEP = range(3)


class StorageRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        (root / "esp_err.h").write_text(
            "#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n")
        (root / "nvs_flash.h").write_text(
            "#pragma once\n#define ESP_ERR_NVS_NO_FREE_PAGES 0x110d\n"
            "#define ESP_ERR_NVS_NEW_VERSION_FOUND 0x1110\n")
        library = root / "policy.so"
        subprocess.run([
            "cc", "-std=c11", "-shared", "-fPIC", "-I", str(root),
            "-I", str(COMPONENT / "include"),
            str(COMPONENT / "storage_recovery_policy.c"), "-o", str(library),
        ], check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.storage_recovery_decide.argtypes = [ctypes.c_int, ctypes.c_bool]

    def decide(self, error, pending):
        return self.lib.storage_recovery_decide(error, pending)

    def test_healthy_or_other_errors_are_left_alone(self):
        for error in (ESP_OK, NOT_FOUND, -1):
            for pending in (False, True):
                self.assertEqual(self.decide(error, pending), NONE)

    def test_unconfirmed_update_rolls_back(self):
        for error in (NO_FREE_PAGES, NEW_VERSION_FOUND):
            self.assertEqual(self.decide(error, True), ROLLBACK)

    def test_confirmed_image_keeps_the_partition(self):
        for error in (NO_FREE_PAGES, NEW_VERSION_FOUND):
            self.assertEqual(self.decide(error, False), KEEP)

    def test_never_erased_automatically_and_reported(self):
        source = (COMPONENT / "storage_recovery.c").read_text()
        init = c_function(source, "esp_err_t storage_recovery_init_partition(")
        self.assertNotIn("nvs_flash_erase_partition", init)
        self.assertIn("atomic_fetch_or(&s_unusable_mask, partition_bit);", init)
        self.assertIn("esp_ota_mark_app_invalid_rollback_and_reboot", init)
        clear = c_function(source, "esp_err_t storage_recovery_clear_partition(const char *label)")
        self.assertIn("nvs_flash_erase_partition(label)", clear)
        callers = [p.name for p in (ROOT / "components").rglob("*.c")
                   if "storage_recovery_clear_partition(" in p.read_text()
                   and p.parent.name != "storage_recovery"]
        self.assertEqual(["config_store.c"], callers)
        reset = c_function((ROOT / "components/config_store/config_store.c").read_text(),
                           "esp_err_t config_store_safe_mode_factory_reset(")
        self.assertIn("storage_recovery_clear_partition(CONFIG_PARTITION)", reset)

    def test_both_partitions_use_recovery(self):
        ble = (ROOT / "components/codex_micro/codex_micro_ble.c").read_text()
        self.assertIn("storage_recovery_init_partition(\n        NVS_DEFAULT_PART_NAME, "
                      "STORAGE_RECOVERY_PARTITION_BLE)", ble)
        config = (ROOT / "components/config_store/config_store.c").read_text()
        self.assertIn("storage_recovery_init_partition(\n        CONFIG_PARTITION, "
                      "STORAGE_RECOVERY_PARTITION_CONFIG)", config)
        self.assertNotIn("nvs_flash_init_partition(CONFIG_PARTITION)", config)
        protocol = (ROOT / "components/config_protocol/config_protocol.c").read_text()
        self.assertIn('"ble_storage_unusable"', protocol)
        self.assertIn('"config_storage_unusable"', protocol)


if __name__ == "__main__":
    unittest.main()
