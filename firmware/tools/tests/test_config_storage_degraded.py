"""F-04: config storage that cannot be used no longer stops the boot.

A confirmed image runs read-only (the config it read, or the built-in
defaults) so USB maintenance stays reachable; a fresh unconfirmed update still
fails its boot so the bootloader rolls back to the image that wrote the data.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "components/config_store/config_store.c"
STORE_HEADER = ROOT / "components/config_store/include/config_store.h"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
RECOVERY = ROOT / "components/storage_recovery"
APP_MAIN = ROOT / "main/app_main.c"

ESP_OK = 0
ESP_FAIL = -1
ESP_ERR_NO_MEM = 0x101
ESP_ERR_NVS_NOT_ENOUGH_SPACE = 0x1105

HARNESS = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define PSA_SUCCESS 0
#define ESP_LOGE(tag, ...) ((void)(tag))
static const char *TAG = "test";
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
typedef struct cJSON { int id; } cJSON;
typedef struct { uint32_t generation; } slot_meta_t;
typedef uint32_t nvs_handle_t;
typedef void *SemaphoreHandle_t;
enum { CONFIG_PLATFORM_UNSELECTED = 0, CONFIG_PLATFORM_CUSTOM = 3 };

static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static cJSON *s_active_root, *s_pending_root;
static char *s_active_json, *s_pending_json;
static slot_meta_t s_active_meta;
static uint8_t s_active_slot;
static bool s_pending_factory_reset;
static atomic_bool s_has_pending, s_factory_reset_activated, s_storage_degraded;
static atomic_int s_platform;
static esp_err_t s_storage_error;
static bool s_storage_using_defaults;

static esp_err_t load_result;
static bool loaded_config, pending_verify, defaults_broken;
static unsigned closes;
static cJSON stored = {1}, pending = {2}, defaults_root = {3};
static int psa_crypto_init(void) { return PSA_SUCCESS; }
static void install_config_json_allocator(void) {}
static SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
static void nvs_close(nvs_handle_t h) { (void)h; closes++; }
static void cJSON_Delete(cJSON *c) { (void)c; }
static bool storage_recovery_image_pending_verify(void) { return pending_verify; }
static cJSON *parse_defaults(void) { return defaults_broken ? NULL : &defaults_root; }
static esp_err_t prepare_candidate(const cJSON *candidate, const char *digest,
                                   cJSON **root, char **json, slot_meta_t *meta,
                                   char *reason, size_t reason_size)
{
    (void)digest; (void)reason; (void)reason_size;
    *root = (cJSON *)candidate; *json = strdup("defaults"); meta->generation = 9;
    return ESP_OK;
}
/* Stands in for the real NVS load: it may read a config and stage a pending
 * one before a later write fails. */
static esp_err_t load_from_storage(void)
{
    s_nvs = 7;
    if (loaded_config) { s_active_root = &stored; s_active_json = strdup("stored"); }
    s_pending_root = &pending; s_pending_json = strdup("pending");
    atomic_store(&s_has_pending, true);
    return load_result;
}
'''


class DegradedStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = STORE.read_text()
        code = HARNESS
        for signature in ("static esp_err_t run_without_storage(esp_err_t cause)",
                          "esp_err_t config_store_init(void)"):
            code += c_function(source, signature) + "\n"
        code += r'''
int boot(int result, bool read_config, bool unconfirmed, bool broken) {
    s_nvs = 0; s_active_root = s_pending_root = NULL;
    s_active_json = s_pending_json = NULL;
    atomic_store(&s_storage_degraded, false); s_storage_using_defaults = false;
    s_storage_error = ESP_OK; closes = 0;
    load_result = result; loaded_config = read_config;
    pending_verify = unconfirmed; defaults_broken = broken;
    return config_store_init();
}
bool degraded(void) { return atomic_load(&s_storage_degraded); }
bool using_defaults(void) { return s_storage_using_defaults; }
int storage_error(void) { return s_storage_error; }
int active(void) { return s_active_root ? s_active_root->id : 0; }
bool has_pending(void) { return atomic_load(&s_has_pending) || s_pending_root; }
unsigned handle(void) { return s_nvs; }
unsigned close_count(void) { return closes; }
unsigned generation(void) { return s_active_meta.generation; }
'''
        path = Path(cls.temp.name) / "degraded.c"
        path.write_text(code)
        library = path.with_suffix(".so")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
                        str(path), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        for name in ("degraded", "using_defaults", "has_pending"):
            getattr(cls.lib, name).restype = ctypes.c_bool
        cls.lib.boot.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]

    def boot(self, result, read_config=False, unconfirmed=False, broken=False):
        return self.lib.boot(result, read_config, unconfirmed, broken)

    def test_working_storage_is_unchanged(self):
        self.assertEqual(ESP_OK, self.boot(ESP_OK, read_config=True))
        self.assertFalse(self.lib.degraded())
        self.assertEqual(7, self.lib.handle())

    def test_unconfirmed_update_still_fails_so_it_rolls_back(self):
        self.assertEqual(ESP_ERR_NVS_NOT_ENOUGH_SPACE,
                         self.boot(ESP_ERR_NVS_NOT_ENOUGH_SPACE, unconfirmed=True))
        self.assertFalse(self.lib.degraded())

    def test_confirmed_image_keeps_the_config_it_read(self):
        self.assertEqual(ESP_OK, self.boot(ESP_ERR_NVS_NOT_ENOUGH_SPACE, read_config=True))
        self.assertTrue(self.lib.degraded())
        self.assertFalse(self.lib.using_defaults())
        self.assertEqual(1, self.lib.active())
        self.assertEqual(ESP_ERR_NVS_NOT_ENOUGH_SPACE, self.lib.storage_error())

    def test_confirmed_image_without_a_config_runs_defaults(self):
        self.assertEqual(ESP_OK, self.boot(ESP_ERR_NVS_NOT_ENOUGH_SPACE))
        self.assertTrue(self.lib.degraded())
        self.assertTrue(self.lib.using_defaults())
        self.assertEqual(3, self.lib.active())
        self.assertEqual(1, self.lib.generation())

    def test_degraded_store_closes_the_handle_and_drops_pending(self):
        self.boot(ESP_FAIL, read_config=True)
        self.assertEqual(0, self.lib.handle())
        self.assertEqual(1, self.lib.close_count())
        self.assertFalse(self.lib.has_pending())

    def test_broken_built_in_defaults_still_fail(self):
        self.assertEqual(ESP_FAIL, self.boot(ESP_ERR_NVS_NOT_ENOUGH_SPACE, broken=True))


class DegradedWiringTests(unittest.TestCase):
    def test_staging_refuses_first_while_degraded(self):
        stage = c_function(STORE.read_text(), "static esp_err_t stage_locked(")
        guard = stage.index("if (atomic_load(&s_storage_degraded)) {")
        self.assertLess(guard, stage.index("if (s_pending_root != NULL) {"))
        self.assertIn("return ESP_FAIL;", stage[guard:guard + 200])

    def test_unusable_slots_are_reported_and_never_replaced(self):
        # Review A2: only a device with no stored slot gets defaults written.
        load = c_function(STORE.read_text(), "static esp_err_t load_from_storage(void)")
        self.assertNotIn("s_unreadable_config_replaced = ", load)
        self.assertIn("s_stored_config_unusable = true;", load)
        self.assertLess(load.index("if (unusable[0] || unusable[1]) {"),
                        load.index("write_slot(0, canonical, &meta, NULL, 0)"))
        present = c_function(STORE.read_text(), "static bool slot_present(uint8_t slot)")
        self.assertIn("!= ESP_ERR_NVS_NOT_FOUND", present)

    def test_status_carries_the_degraded_state(self):
        header = STORE_HEADER.read_text()
        for field in ("bool storage_degraded;", "esp_err_t storage_error;",
                      "bool storage_using_defaults;", "bool unreadable_config_replaced;",
                      "bool stored_config_unusable;"):
            self.assertIn(field, header)
        status = c_function(STORE.read_text(), "void config_store_get_status(")
        self.assertIn("status->storage_degraded = atomic_load(&s_storage_degraded);", status)

    def test_hello_keeps_firmware_maintenance_open_and_names_the_reason(self):
        protocol = PROTOCOL.read_text()
        self.assertIn("const bool writable = board_is_product_target();", protocol)
        self.assertIn(': status.storage_degraded ? "config_storage_unavailable"', protocol)

    def test_get_status_reports_degraded_storage(self):
        protocol = PROTOCOL.read_text()
        for key in ('"config_storage_degraded"', '"config_storage_error"',
                    '"config_using_defaults"', '"unreadable_config_replaced"',
                    '"stored_config_unusable"'):
            self.assertIn(key, protocol)

    def test_rollback_check_is_shared_with_storage_recovery(self):
        header = (RECOVERY / "include/storage_recovery.h").read_text()
        self.assertIn("bool storage_recovery_image_pending_verify(void);", header)
        source = (RECOVERY / "storage_recovery.c").read_text()
        self.assertIn("storage_recovery_decide(error, storage_recovery_image_pending_verify())",
                      source)

    def test_boot_still_requires_config_store_init(self):
        # Init now fails only for an unconfirmed update (roll back) or a broken
        # image, so stopping the boot there is still right.
        # Safe mode (stability item 6) takes the read-only defaults path instead.
        self.assertIn("ESP_ERROR_CHECK(safe_mode ? config_store_init_safe_mode() : config_store_init());",
                      APP_MAIN.read_text())


if __name__ == "__main__":
    unittest.main()
