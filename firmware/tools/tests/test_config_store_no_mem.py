"""Stability item 1: running out of memory never replaces the user's config.

read_slot() used to report an allocation failure like a damaged slot, so a
boot short of memory could pick an older slot, repair markers, or write the
built-in defaults over both slots. Now any slot read that runs out of memory
(buffer, cJSON parse or canonical copy) makes load_from_storage() write
nothing: it keeps the marked active slot in RAM if it was read, otherwise the
store runs the built-in defaults read-only and the next boot retries.

The harness compiles the real read_slot(), slot_present(), load_from_storage(),
run_without_storage() and config_store_init() against a fake NVS that counts
every write.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "components/config_store/config_store.c"

ESP_OK = 0
ESP_FAIL = -1
ESP_ERR_NO_MEM = 0x101

HARNESS = r'''
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_INVALID_SIZE 0x104
#define ESP_ERR_INVALID_CRC 0x109
#define ESP_ERR_NVS_NOT_FOUND 0x1102
#define PSA_SUCCESS 0
#define NVS_READWRITE 1
#define ESP_LOGE(tag, ...) ((void)(tag))
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
#define WMP_CONFIG_MAX_CANONICAL_BYTES 4096
#define CONFIG_STORE_DIGEST_HEX_LENGTH 64
#define CONFIG_PARTITION "config_nvs"
#define CONFIG_NAMESPACE "wmpcfg"
#define CONFIG_META_MAGIC 0x574D5043u
#define CONFIG_INTENT_MAGIC 0x574D5049u
#define CONFIG_SCHEMA_VERSION 1u
#define CONFIG_KEY_ACTIVE "active"
#define CONFIG_KEY_PENDING "pending"
#define CONFIG_KEY_FACTORY_PENDING "factory"
#define CONFIG_KEY_PLATFORM "platform"
#define CONFIG_KEY_PENDING_PLATFORM "nextos"
#define CONFIG_KEY_BLE_NAME "ble_name"
#define CONFIG_KEY_NORMAL_AGENT_BEHAVIOR "agent_mode"
#define WMP_BLE_NAME_DEFAULT "BORING"
#define CONFIG_PLATFORM_CONTEXT_COUNT 2
#define STORAGE_RECOVERY_PARTITION_CONFIG 1
static const char *TAG = "test";
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
typedef enum { CONFIG_PLATFORM_UNSELECTED = 0, CONFIG_PLATFORM_MACOS = 1,
               CONFIG_PLATFORM_WINDOWS_LINUX = 2, CONFIG_PLATFORM_CUSTOM = 3 } config_platform_t;
typedef uint32_t nvs_handle_t;
typedef void *SemaphoreHandle_t;
typedef struct cJSON { char *text; } cJSON;
typedef struct {
    uint32_t magic, schema_version, generation, json_length;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
} legacy_slot_meta_t;
typedef struct {
    uint32_t magic, schema_version, generation, json_length;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    uint32_t intent_magic;
    uint8_t platform_intent, factory_reset, reserved[2];
} slot_meta_t;

static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static cJSON *s_active_root, *s_pending_root;
static char *s_active_json, *s_pending_json;
static slot_meta_t s_active_meta, s_pending_meta;
static uint8_t s_active_slot, s_pending_slot;
static bool s_pending_factory_reset;
static config_platform_t s_pending_platform;
static atomic_bool s_has_pending, s_factory_reset_activated, s_storage_degraded;
static atomic_int s_platform, s_platform_context;
static atomic_int s_context_platforms[CONFIG_PLATFORM_CONTEXT_COUNT];
static const char *const PLATFORM_CONTEXT_KEYS[CONFIG_PLATFORM_CONTEXT_COUNT] = {"ctx0", "ctx1"};
static esp_err_t s_storage_error;
static bool s_storage_using_defaults, s_unreadable_config_replaced, s_safe_mode;
static bool s_stored_config_unusable;
static unsigned blob_reads;
static atomic_uint s_config_alloc_failures;

/* ---- fake NVS ---- */
static const char DIGEST[] =
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
static bool slot_exists[2], slot_damaged[2];
static slot_meta_t slot_meta[2];
static char slot_json[2][64];
static int active_marker; /* -1: none */
static unsigned writes;
static int fail_calloc_slot = -1, fail_parse_slot = -1, fail_canonical_slot = -1;
static int reading_slot = -1;
static bool pending_verify;

static int slot_of(const char *key)
{
    if (key[0] == 'a' && key[1] == '_') return 0;
    if (key[0] == 'b' && key[1] == '_') return 1;
    return -1;
}
static esp_err_t nvs_get_blob(nvs_handle_t h, const char *key, void *out, size_t *len)
{
    (void)h;
    const int slot = slot_of(key);
    blob_reads++;
    if (slot < 0 || !slot_exists[slot]) return ESP_ERR_NVS_NOT_FOUND;
    reading_slot = slot;
    if (strstr(key, "meta")) {
        if (out) memcpy(out, &slot_meta[slot], sizeof(slot_meta_t));
        *len = sizeof(slot_meta_t);
    } else {
        const size_t size = strlen(slot_json[slot]) + 1;
        if (out) memcpy(out, slot_json[slot], size);
        *len = size;
    }
    return ESP_OK;
}
static esp_err_t nvs_get_u8(nvs_handle_t h, const char *key, uint8_t *out)
{
    (void)h;
    if (strcmp(key, CONFIG_KEY_ACTIVE) == 0 && active_marker >= 0) {
        *out = (uint8_t)active_marker; return ESP_OK;
    }
    if (strcmp(key, CONFIG_KEY_PLATFORM) == 0) { *out = CONFIG_PLATFORM_MACOS; return ESP_OK; }
    return ESP_ERR_NVS_NOT_FOUND;
}
static esp_err_t nvs_set_u8(nvs_handle_t h, const char *k, uint8_t v) { (void)h; (void)k; (void)v; writes++; return ESP_OK; }
static esp_err_t nvs_set_str(nvs_handle_t h, const char *k, const char *v) { (void)h; (void)k; (void)v; writes++; return ESP_OK; }
static esp_err_t nvs_commit(nvs_handle_t h) { (void)h; writes++; return ESP_OK; }
static void nvs_close(nvs_handle_t h) { (void)h; }
static esp_err_t nvs_open_from_partition(const char *p, const char *n, int mode, nvs_handle_t *h)
{ (void)p; (void)n; (void)mode; *h = 7; return ESP_OK; }
static esp_err_t storage_recovery_init_partition(const char *p, int kind) { (void)p; (void)kind; return ESP_OK; }
static bool storage_recovery_image_pending_verify(void) { return pending_verify; }
static esp_err_t erase_optional_key(const char *key) { (void)key; writes++; return ESP_OK; }
static esp_err_t write_slot(uint8_t slot, const char *json, const slot_meta_t *meta,
                            char *reason, size_t reason_size)
{ (void)slot; (void)json; (void)meta; (void)reason; (void)reason_size; writes++; return ESP_OK; }
static esp_err_t persist_context_intent(const slot_meta_t *meta) { (void)meta; writes++; return ESP_OK; }
static void apply_context_intent(const slot_meta_t *meta) { (void)meta; }
static esp_err_t migrate_matrix12_v1_slot(uint8_t slot, cJSON **root, char **json, slot_meta_t *meta)
{ (void)slot; (void)root; (void)json; (void)meta; return ESP_OK; }
static void set_reason(char *reason, size_t size, const char *value)
{ if (reason && size) snprintf(reason, size, "%s", value); }

/* ---- fake allocator / cJSON ---- */
static void *config_calloc(size_t count, size_t size)
{
    if (reading_slot == fail_calloc_slot) { atomic_fetch_add(&s_config_alloc_failures, 1u); return NULL; }
    return calloc(count, size);
}
static cJSON *cJSON_ParseWithLength(const char *text, size_t length)
{
    if (reading_slot == fail_parse_slot) {
        atomic_fetch_add(&s_config_alloc_failures, 1u); /* a node allocation failed */
        return NULL;
    }
    if (slot_damaged[reading_slot]) return NULL; /* syntax error, no allocation failed */
    cJSON *c = calloc(1, sizeof(*c));
    c->text = strndup(text, length);
    return c;
}
static void cJSON_Delete(cJSON *c) { if (c) { free(c->text); free(c); } }
static bool validate_schema_for_hardware(const cJSON *root, bool v1, char *reason, size_t size)
{ (void)root; (void)v1; (void)reason; (void)size; return true; }
static esp_err_t canonicalize(const cJSON *root, char **json, char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1])
{
    if (reading_slot == fail_canonical_slot) return ESP_ERR_NO_MEM;
    *json = strdup(root->text);
    memcpy(digest, DIGEST, sizeof(DIGEST));
    return ESP_OK;
}
static cJSON defaults_root = {"defaults"};
static cJSON *parse_defaults(void)
{
    cJSON *c = calloc(1, sizeof(*c)); c->text = strdup(defaults_root.text); return c;
}
static esp_err_t prepare_candidate(const cJSON *candidate, const char *digest,
                                   cJSON **root, char **json, slot_meta_t *meta,
                                   char *reason, size_t reason_size)
{
    (void)digest; (void)reason; (void)reason_size;
    cJSON *copy = calloc(1, sizeof(*copy)); copy->text = strdup(candidate->text);
    memset(meta, 0, sizeof(*meta));
    *root = copy; *json = strdup(candidate->text);
    return ESP_OK;
}
static int psa_crypto_init(void) { return PSA_SUCCESS; }
static void install_config_json_allocator(void) {}
static SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
'''

DRIVER = r'''
void reset_store(void)
{
    memset(slot_exists, 0, sizeof(slot_exists));
    memset(slot_damaged, 0, sizeof(slot_damaged));
    active_marker = -1; writes = 0; pending_verify = false; blob_reads = 0;
    s_safe_mode = false; s_stored_config_unusable = false;
    fail_calloc_slot = fail_parse_slot = fail_canonical_slot = -1;
    s_nvs = 0; s_active_root = s_pending_root = NULL;
    s_active_json = s_pending_json = NULL;
    memset(&s_active_meta, 0, sizeof(s_active_meta));
    atomic_store(&s_storage_degraded, false); atomic_store(&s_has_pending, false);
    s_storage_using_defaults = false; s_unreadable_config_replaced = false;
    s_storage_error = ESP_OK;
}
void put_slot(int slot, unsigned generation, const char *text)
{
    slot_exists[slot] = true;
    snprintf(slot_json[slot], sizeof(slot_json[slot]), "%s", text);
    slot_meta_t *m = &slot_meta[slot];
    memset(m, 0, sizeof(*m));
    m->magic = CONFIG_META_MAGIC; m->schema_version = CONFIG_SCHEMA_VERSION;
    m->generation = generation; m->json_length = (uint32_t)strlen(text);
    memcpy(m->digest, DIGEST, sizeof(DIGEST));
    m->intent_magic = CONFIG_INTENT_MAGIC;
}
void damage_slot(int slot) { slot_damaged[slot] = true; }
void set_marker(int slot) { active_marker = slot; }
void fail_calloc(int slot) { fail_calloc_slot = slot; }
void fail_parse(int slot) { fail_parse_slot = slot; }
void fail_canonical(int slot) { fail_canonical_slot = slot; }
void set_pending_verify(bool value) { pending_verify = value; }
int load(void) { return load_from_storage(); }
int init(void) { return config_store_init(); }
int init_safe(void) { return config_store_init_safe_mode(); }
unsigned blob_read_count(void) { return blob_reads; }
bool safe_mode(void) { return s_safe_mode; }
int read(int slot)
{
    cJSON *root = NULL; char *json = NULL; slot_meta_t meta;
    const esp_err_t result = read_slot((uint8_t)slot, &root, &json, &meta, NULL, 0);
    cJSON_Delete(root); free(json);
    return result;
}
unsigned write_count(void) { return writes; }
const char *active_text(void) { return s_active_root ? s_active_root->text : ""; }
unsigned active_generation(void) { return s_active_meta.generation; }
bool degraded(void) { return atomic_load(&s_storage_degraded); }
bool using_defaults(void) { return s_storage_using_defaults; }
bool unreadable_replaced(void) { return s_unreadable_config_replaced; }
bool stored_unusable(void) { return s_stored_config_unusable; }
void set_schema(int slot, unsigned schema) { slot_meta[slot].schema_version = schema; }
void drop_slot(int slot) { slot_exists[slot] = false; }
int storage_error(void) { return s_storage_error; }
unsigned handle(void) { return s_nvs; }
'''


class ConfigStoreNoMemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = STORE.read_text()
        code = HARNESS
        for signature in (
            "static const char *slot_json_key(uint8_t slot)",
            "static const char *slot_meta_key(uint8_t slot)",
            "static esp_err_t read_slot(uint8_t slot,",
            "static bool slot_present(uint8_t slot)",
            "static esp_err_t load_from_storage(void)",
            "static esp_err_t run_without_storage(esp_err_t cause)",
            "esp_err_t config_store_init(void)",
            "esp_err_t config_store_init_safe_mode(void)",
        ):
            code += c_function(source, signature) + "\n"
        code += DRIVER
        path = Path(cls.temp.name) / "no_mem.c"
        path.write_text(code)
        library = path.with_suffix(".so")
        subprocess.run(["cc", "-std=c11", "-D_GNU_SOURCE", "-D_DARWIN_C_SOURCE", "-shared",
                        "-fPIC", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-Wno-unused-parameter", "-Wno-unused-variable",
                        str(path), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        for name in ("degraded", "using_defaults", "unreadable_replaced", "safe_mode",
                     "stored_unusable"):
            getattr(cls.lib, name).restype = ctypes.c_bool
        cls.lib.active_text.restype = ctypes.c_char_p
        cls.lib.put_slot.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_char_p]
        cls.lib.set_pending_verify.argtypes = [ctypes.c_bool]

    def setUp(self):
        self.lib.reset_store()

    def two_slots(self, marker=1):
        self.lib.put_slot(0, 4, b"older")
        self.lib.put_slot(1, 5, b"newer")
        self.lib.set_marker(marker)

    # read_slot classification

    def test_buffer_allocation_failure_reads_as_no_mem(self):
        self.two_slots()
        self.lib.fail_calloc(0)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.read(0))

    def test_parse_out_of_memory_reads_as_no_mem(self):
        self.two_slots()
        self.lib.fail_parse(1)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.read(1))

    def test_canonical_copy_out_of_memory_reads_as_no_mem(self):
        self.two_slots()
        self.lib.fail_canonical(0)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.read(0))

    def test_damaged_json_is_still_damage_not_memory(self):
        self.two_slots()
        self.lib.damage_slot(0)
        result = self.lib.read(0)
        self.assertNotEqual(ESP_OK, result)
        self.assertNotEqual(ESP_ERR_NO_MEM, result)

    # load_from_storage never writes when memory ran out

    def test_both_slots_short_of_memory_write_nothing(self):
        self.two_slots()
        self.lib.fail_calloc(0)
        self.lib.fail_parse(1)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.load())
        self.assertEqual(0, self.lib.write_count())
        self.assertEqual(b"", self.lib.active_text())
        self.assertFalse(self.lib.unreadable_replaced())

    def test_active_slot_read_and_other_short_of_memory_keeps_active_and_writes_nothing(self):
        self.two_slots(marker=1)
        self.lib.fail_parse(0)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.load())
        self.assertEqual(0, self.lib.write_count())
        self.assertEqual(b"newer", self.lib.active_text())
        self.assertEqual(5, self.lib.active_generation())

    def test_active_slot_short_of_memory_does_not_fall_back_to_the_older_slot(self):
        self.two_slots(marker=1)
        self.lib.fail_canonical(1)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.load())
        self.assertEqual(0, self.lib.write_count())
        self.assertEqual(b"", self.lib.active_text())

    def test_confirmed_image_short_of_memory_runs_defaults_read_only(self):
        self.two_slots()
        self.lib.fail_calloc(0)
        self.lib.fail_calloc(1)
        self.lib.fail_parse(1)
        self.assertEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())
        self.assertTrue(self.lib.degraded())
        self.assertTrue(self.lib.using_defaults())
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.storage_error())
        self.assertEqual(b"defaults", self.lib.active_text())
        self.assertEqual(0, self.lib.handle(), "handle closed: nothing can write until restart")

    def test_confirmed_image_keeps_the_active_slot_read_only(self):
        self.two_slots(marker=0)
        self.lib.fail_parse(1)
        self.assertEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())
        self.assertTrue(self.lib.degraded())
        self.assertFalse(self.lib.using_defaults())
        self.assertEqual(b"older", self.lib.active_text())

    def test_unconfirmed_update_short_of_memory_fails_boot_without_writing(self):
        self.two_slots()
        self.lib.fail_parse(0)
        self.lib.fail_parse(1)
        self.lib.set_pending_verify(True)
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.init())
        self.assertEqual(0, self.lib.write_count())

    # unchanged paths

    def test_healthy_slots_select_the_marked_slot(self):
        self.two_slots(marker=1)
        self.assertEqual(ESP_OK, self.lib.load())
        self.assertEqual(b"newer", self.lib.active_text())

    # review A2: a stored slot that cannot be used is never written over

    def test_two_damaged_slots_are_left_untouched_and_defaults_run_read_only(self):
        self.two_slots()
        self.lib.damage_slot(0)
        self.lib.damage_slot(1)
        self.assertEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())
        self.assertFalse(self.lib.unreadable_replaced())
        self.assertTrue(self.lib.stored_unusable())
        self.assertTrue(self.lib.degraded())
        self.assertTrue(self.lib.using_defaults())
        self.assertEqual(b"defaults", self.lib.active_text())

    def test_unconfirmed_update_that_cannot_use_the_config_fails_its_boot(self):
        self.two_slots()
        self.lib.damage_slot(0)
        self.lib.damage_slot(1)
        self.lib.set_pending_verify(True)
        self.assertNotEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())

    def test_other_schema_version_is_left_untouched(self):
        self.two_slots()
        self.lib.set_schema(0, 2)
        self.lib.set_schema(1, 2)
        self.assertEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())
        self.assertTrue(self.lib.stored_unusable())

    def test_damaged_inactive_slot_keeps_the_active_one_writable_and_writes_nothing(self):
        self.two_slots(marker=1)
        self.lib.damage_slot(0)
        self.assertEqual(ESP_OK, self.lib.load())
        self.assertEqual(0, self.lib.write_count())
        self.assertEqual(b"newer", self.lib.active_text())
        self.assertTrue(self.lib.stored_unusable())
        self.assertFalse(self.lib.degraded())

    def test_damaged_active_slot_never_falls_back_to_a_writable_older_one(self):
        self.two_slots(marker=1)
        self.lib.damage_slot(1)
        self.assertEqual(ESP_OK, self.lib.init())
        self.assertEqual(0, self.lib.write_count())
        self.assertEqual(b"older", self.lib.active_text())
        self.assertTrue(self.lib.degraded(), "older generation only read-only")

    def test_only_a_device_with_no_stored_slot_gets_defaults_written(self):
        self.assertEqual(ESP_OK, self.lib.load())
        self.assertGreater(self.lib.write_count(), 0)
        self.assertEqual(b"defaults", self.lib.active_text())
        self.assertFalse(self.lib.stored_unusable())

    def test_one_missing_slot_still_selects_the_other_normally(self):
        self.two_slots(marker=0)
        self.lib.drop_slot(1)
        self.assertEqual(ESP_OK, self.lib.load())
        self.assertEqual(b"older", self.lib.active_text())
        self.assertFalse(self.lib.stored_unusable())

    # stability item 6: safe mode

    def test_safe_mode_never_reads_or_writes_the_slots(self):
        self.two_slots()
        self.assertEqual(ESP_OK, self.lib.init_safe())
        self.assertEqual(0, self.lib.blob_read_count())
        self.assertEqual(0, self.lib.write_count())
        self.assertTrue(self.lib.safe_mode())
        self.assertTrue(self.lib.degraded())
        self.assertTrue(self.lib.using_defaults())
        self.assertEqual(b"defaults", self.lib.active_text())
        self.assertEqual(0, self.lib.handle())

    def test_allocator_counts_failures(self):
        source = STORE.read_text()
        for name in ("static void *config_malloc(size_t size)",
                     "static void *config_calloc(size_t count, size_t size)"):
            self.assertIn("atomic_fetch_add(&s_config_alloc_failures, 1u);",
                          c_function(source, name))


if __name__ == "__main__":
    unittest.main()
