#include "config_store.h"

#include <stdio.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#include "board.h"
#include "ble_name.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "psa/crypto.h"
#include "protocol_contract.h"
#include "sdkconfig.h"

#define CONFIG_PARTITION "config_nvs"
#define CONFIG_NAMESPACE "wmpcfg"
#define CONFIG_META_MAGIC 0x574D5043u
#define CONFIG_INTENT_MAGIC 0x574D5049u
#define CONFIG_SCHEMA_VERSION 1u
#define CONFIG_FACTORY_CONFIRMATION "FACTORY_DEFAULT"
#define CONFIG_KEY_ACTIVE "active"
#define CONFIG_KEY_PENDING "pending"
#define CONFIG_KEY_FACTORY_PENDING "factory"
#define CONFIG_KEY_PLATFORM "platform"
#define CONFIG_KEY_PENDING_PLATFORM "nextos"
#define CONFIG_KEY_POMODORO_MINUTES "pomo_min"
#define CONFIG_KEY_STANDBY_MINUTES "idle_min"
#define CONFIG_KEY_USER_POWERED_ON "user_on"
#define CONFIG_KEY_BLE_NAME "ble_name"
#define CONFIG_CONTROL_COUNT BOARD_CONTROL_COUNT
#define CONFIG_MACRO_MAX_BYTES 256u
#define CONFIG_ALL_MACROS_MAX_BYTES 4096u
#define MATRIX12_V1_HARDWARE_ID "WMP-S3-MATRIX12-V1"
#define LEGACY_MATRIX12_EXTRA_KEY_FIRST 8u
#define LEGACY_MATRIX12_EXTRA_KEY_COUNT 5u

static const char *TAG = "config_store";

typedef struct {
    uint32_t magic;
    uint32_t schema_version;
    uint32_t generation;
    uint32_t json_length;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
} legacy_slot_meta_t;

typedef struct {
    uint32_t magic;
    uint32_t schema_version;
    uint32_t generation;
    uint32_t json_length;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    uint32_t intent_magic;
    uint8_t platform_intent;
    uint8_t factory_reset;
    uint8_t reserved[2];
} slot_meta_t;

_Static_assert(sizeof(slot_meta_t) > sizeof(legacy_slot_meta_t),
               "slot intent metadata must remain distinguishable from legacy slots");

typedef struct {
    char *data;
    size_t length;
    size_t capacity;
    bool ok;
} json_writer_t;

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
extern const uint8_t default_config_start[]
    asm("_binary_default_config_matrix12_power_v2_json_start");
extern const uint8_t default_config_end[]
    asm("_binary_default_config_matrix12_power_v2_json_end");
#elif CONFIG_MACROPAD_BOARD_MATRIX12_V1
extern const uint8_t default_config_start[]
    asm("_binary_default_config_matrix12_v1_json_start");
extern const uint8_t default_config_end[]
    asm("_binary_default_config_matrix12_v1_json_end");
#elif CONFIG_MACROPAD_BOARD_DEVKIT_BOOT_BUTTON
extern const uint8_t default_config_start[]
    asm("_binary_default_config_devkit_json_start");
extern const uint8_t default_config_end[]
    asm("_binary_default_config_devkit_json_end");
#else
extern const uint8_t default_config_start[] asm("_binary_default_config_json_start");
extern const uint8_t default_config_end[] asm("_binary_default_config_json_end");
#endif

static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static cJSON *s_active_root;
static char *s_active_json;
static slot_meta_t s_active_meta;
static uint8_t s_active_slot;
static cJSON *s_pending_root;
static char *s_pending_json;
static slot_meta_t s_pending_meta;
static uint8_t s_pending_slot;
static atomic_bool s_has_pending;
static atomic_bool s_activation_failed;
static atomic_bool s_factory_reset_activated;
static bool s_pending_factory_reset;
static atomic_int s_platform;
static atomic_int s_platform_context;
static atomic_int s_context_platforms[CONFIG_PLATFORM_CONTEXT_COUNT];
static const char *const PLATFORM_CONTEXT_KEYS[CONFIG_PLATFORM_CONTEXT_COUNT] = {
    "os_usb", "os_ble1", "os_ble2", "os_ble3",
};
static config_platform_t s_pending_platform;
static uint8_t s_activation_failures;
static TickType_t s_activation_retry_at;

typedef struct {
    config_platform_t platform;
    const char *profile_name;
} platform_profile_t;

static const platform_profile_t PLATFORM_PROFILES[] = {
    {CONFIG_PLATFORM_MACOS, "Codex macOS"},
    {CONFIG_PLATFORM_WINDOWS_LINUX, "Codex Windows"},
};

typedef struct {
    config_action_type_t type;
    uint16_t usage;
    const char *name;
} quick_preset_t;

static const quick_preset_t QUICK_PRESETS[CONFIG_QUICK_PRESET_COUNT] = {
    [CONFIG_QUICK_PRESET_NONE] = {CONFIG_ACTION_NONE, 0, "NONE"},
    [CONFIG_QUICK_PRESET_ENTER] = {CONFIG_ACTION_KEY, 40, "ENTER"},
    [CONFIG_QUICK_PRESET_ESCAPE] = {CONFIG_ACTION_KEY, 41, "ESC"},
    [CONFIG_QUICK_PRESET_SPACE] = {CONFIG_ACTION_KEY, 44, "SPACE"},
    [CONFIG_QUICK_PRESET_TAB] = {CONFIG_ACTION_KEY, 43, "TAB"},
    [CONFIG_QUICK_PRESET_BACKSPACE] = {CONFIG_ACTION_KEY, 42, "BACKSPACE"},
    [CONFIG_QUICK_PRESET_PAGE_UP] = {CONFIG_ACTION_KEY, 75, "PAGE UP"},
    [CONFIG_QUICK_PRESET_PAGE_DOWN] = {CONFIG_ACTION_KEY, 78, "PAGE DOWN"},
    [CONFIG_QUICK_PRESET_ARROW_UP] = {CONFIG_ACTION_KEY, 82, "ARROW UP"},
    [CONFIG_QUICK_PRESET_ARROW_DOWN] = {CONFIG_ACTION_KEY, 81, "ARROW DOWN"},
    [CONFIG_QUICK_PRESET_ARROW_LEFT] = {CONFIG_ACTION_KEY, 80, "ARROW LEFT"},
    [CONFIG_QUICK_PRESET_ARROW_RIGHT] = {CONFIG_ACTION_KEY, 79, "ARROW RIGHT"},
    [CONFIG_QUICK_PRESET_PLAY_PAUSE] = {CONFIG_ACTION_CONSUMER, 205, "PLAY PAUSE"},
    [CONFIG_QUICK_PRESET_MUTE] = {CONFIG_ACTION_CONSUMER, 226, "MUTE"},
    [CONFIG_QUICK_PRESET_VOLUME_UP] = {CONFIG_ACTION_CONSUMER, 233, "VOLUME UP"},
    [CONFIG_QUICK_PRESET_VOLUME_DOWN] = {CONFIG_ACTION_CONSUMER, 234, "VOLUME DOWN"},
};

static cJSON *parse_defaults(void);

static void set_reason(char *reason, size_t reason_size, const char *value)
{
    if (reason != NULL && reason_size > 0) {
        snprintf(reason, reason_size, "%s", value);
    }
}

static bool writer_append(json_writer_t *writer, const char *data, size_t length)
{
    if (!writer->ok || writer->length + length >= writer->capacity) {
        writer->ok = false;
        return false;
    }
    memcpy(writer->data + writer->length, data, length);
    writer->length += length;
    writer->data[writer->length] = '\0';
    return true;
}

static bool writer_char(json_writer_t *writer, char value)
{
    return writer_append(writer, &value, 1);
}

static int compare_json_keys(const void *left, const void *right)
{
    const cJSON *const *a = left;
    const cJSON *const *b = right;
    return strcmp((*a)->string, (*b)->string);
}

static bool canonical_write_value(json_writer_t *writer, const cJSON *value);

static bool canonical_write_string(json_writer_t *writer, const char *value)
{
    cJSON *string = cJSON_CreateString(value != NULL ? value : "");
    if (string == NULL) {
        return false;
    }
    char *encoded = cJSON_PrintUnformatted(string);
    cJSON_Delete(string);
    if (encoded == NULL) {
        return false;
    }
    const bool ok = writer_append(writer, encoded, strlen(encoded));
    cJSON_free(encoded);
    return ok;
}

static bool canonical_write_object(json_writer_t *writer, const cJSON *value)
{
    size_t count = 0;
    for (const cJSON *child = value->child; child != NULL; child = child->next) {
        ++count;
    }
    cJSON **children = count > 0 ? calloc(count, sizeof(*children)) : NULL;
    if (count > 0 && children == NULL) {
        return false;
    }
    size_t index = 0;
    for (cJSON *child = value->child; child != NULL; child = child->next) {
        children[index++] = child;
    }
    qsort(children, count, sizeof(*children), compare_json_keys);
    bool ok = writer_char(writer, '{');
    for (index = 0; ok && index < count; ++index) {
        if (index > 0) {
            ok = writer_char(writer, ',');
        }
        ok = ok && canonical_write_string(writer, children[index]->string) &&
             writer_char(writer, ':') && canonical_write_value(writer, children[index]);
    }
    free(children);
    return ok && writer_char(writer, '}');
}

static bool canonical_write_value(json_writer_t *writer, const cJSON *value)
{
    if (cJSON_IsObject(value)) {
        return canonical_write_object(writer, value);
    }
    if (cJSON_IsArray(value)) {
        bool ok = writer_char(writer, '[');
        size_t index = 0;
        for (const cJSON *child = value->child; ok && child != NULL; child = child->next, ++index) {
            if (index > 0) {
                ok = writer_char(writer, ',');
            }
            ok = ok && canonical_write_value(writer, child);
        }
        return ok && writer_char(writer, ']');
    }
    if (cJSON_IsString(value)) {
        return canonical_write_string(writer, value->valuestring);
    }
    if (cJSON_IsBool(value)) {
        const char *word = cJSON_IsTrue(value) ? "true" : "false";
        return writer_append(writer, word, strlen(word));
    }
    if (cJSON_IsNull(value)) {
        return writer_append(writer, "null", 4);
    }
    if (cJSON_IsNumber(value) && value->valuedouble == value->valueint) {
        char number[24];
        const int length = snprintf(number, sizeof(number), "%d", value->valueint);
        return length > 0 && (size_t)length < sizeof(number) &&
               writer_append(writer, number, (size_t)length);
    }
    return false;
}

static esp_err_t canonicalize(const cJSON *root, char **json,
                              char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1])
{
    char *output = calloc(WMP_CONFIG_MAX_CANONICAL_BYTES + 1, 1);
    if (output == NULL) {
        return ESP_ERR_NO_MEM;
    }
    json_writer_t writer = {
        .data = output,
        .capacity = WMP_CONFIG_MAX_CANONICAL_BYTES + 1,
        .ok = true,
    };
    if (!canonical_write_value(&writer, root) || !writer.ok) {
        free(output);
        return ESP_ERR_INVALID_SIZE;
    }
    unsigned char hash[32];
    size_t hash_length = 0;
    if (psa_hash_compute(PSA_ALG_SHA_256, (const uint8_t *)output, writer.length,
                         hash, sizeof(hash), &hash_length) != PSA_SUCCESS ||
        hash_length != sizeof(hash)) {
        free(output);
        return ESP_FAIL;
    }
    for (size_t index = 0; index < sizeof(hash); ++index) {
        snprintf(digest + index * 2, 3, "%02x", hash[index]);
    }
    digest[CONFIG_STORE_DIGEST_HEX_LENGTH] = '\0';
    *json = output;
    return ESP_OK;
}

static const cJSON *field(const cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static cJSON *mutable_field(cJSON *object, const char *name)
{
    return cJSON_GetObjectItemCaseSensitive(object, name);
}

static bool integer_in(const cJSON *value, int minimum, int maximum)
{
    return cJSON_IsNumber(value) && value->valuedouble == value->valueint &&
           value->valueint >= minimum && value->valueint <= maximum;
}

/* JSON Schema maxLength counts Unicode code points, not UTF-8 bytes. */
static bool string_length_in(const cJSON *value, size_t minimum, size_t maximum)
{
    if (!cJSON_IsString(value) || value->valuestring == NULL) return false;
    const unsigned char *cursor = (const unsigned char *)value->valuestring;
    size_t count = 0;
    while (*cursor != '\0') {
        const unsigned char first = *cursor++;
        unsigned extra;
        uint32_t codepoint;
        if (first < 0x80) { extra = 0; codepoint = first; }
        else if (first >= 0xc2 && first <= 0xdf) { extra = 1; codepoint = first & 0x1f; }
        else if (first >= 0xe0 && first <= 0xef) { extra = 2; codepoint = first & 0x0f; }
        else if (first >= 0xf0 && first <= 0xf4) { extra = 3; codepoint = first & 0x07; }
        else return false;
        for (unsigned index = 0; index < extra; ++index) {
            if (*cursor < 0x80 || *cursor > 0xbf) return false;
            codepoint = (codepoint << 6) | (*cursor++ & 0x3f);
        }
        if ((extra == 2 && codepoint < 0x800) ||
            (extra == 3 && codepoint < 0x10000) ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff) || codepoint > 0x10ffff) return false;
        if (++count > maximum) return false;
    }
    return count >= minimum;
}

static bool printable_ascii_string(const cJSON *value, size_t minimum, size_t maximum)
{
    if (!string_length_in(value, minimum, maximum)) {
        return false;
    }
    for (const unsigned char *cursor = (const unsigned char *)value->valuestring;
         *cursor != '\0'; ++cursor) {
        if (*cursor < 0x20 || *cursor > 0x7e) {
            return false;
        }
    }
    return true;
}

static bool object_keys_are(const cJSON *object, const char *const *allowed, size_t count)
{
    if (!cJSON_IsObject(object)) {
        return false;
    }
    for (const cJSON *child = object->child; child != NULL; child = child->next) {
        bool found = false;
        for (size_t index = 0; index < count; ++index) {
            found = found || strcmp(child->string, allowed[index]) == 0;
        }
        if (!found) {
            return false;
        }
    }
    return true;
}

#define KEYS_ARE(object, ...) object_keys_are((object), (const char *const[]){__VA_ARGS__}, \
                                               sizeof((const char *const[]){__VA_ARGS__}) / sizeof(char *))

static bool valid_control_id(const char *value)
{
    board_control_t control = BOARD_CONTROL_KEY_1;
    return board_control_from_id(value, &control) &&
           board_control_supported(control);
}

static bool valid_action(const cJSON *action)
{
    const cJSON *type = field(action, "type");
    if (!cJSON_IsObject(action) || !cJSON_IsString(type)) {
        return false;
    }
    if (strcmp(type->valuestring, "none") == 0) {
        return KEYS_ARE(action, "type");
    }
    if (strcmp(type->valuestring, "key") == 0) {
        if (!KEYS_ARE(action, "type", "usage", "modifiers") ||
            !integer_in(field(action, "usage"), 4, 231)) {
            return false;
        }
        const cJSON *modifiers = field(action, "modifiers");
        if (modifiers != NULL) {
            if (!cJSON_IsArray(modifiers) || cJSON_GetArraySize(modifiers) > CONFIG_STORE_MAX_MODIFIERS) {
                return false;
            }
            cJSON *modifier = NULL;
            bool seen[8] = {0};
            cJSON_ArrayForEach(modifier, modifiers) {
                if (!integer_in(modifier, 224, 231) || seen[modifier->valueint - 224]) {
                    return false;
                }
                seen[modifier->valueint - 224] = true;
            }
        }
        return true;
    }
    if (strcmp(type->valuestring, "consumer") == 0) {
        return KEYS_ARE(action, "type", "usage") && integer_in(field(action, "usage"), 0, 1023);
    }
    if (strcmp(type->valuestring, "mouse") == 0) {
        return KEYS_ARE(action, "type", "button", "x", "y", "wheel", "pan") &&
               integer_in(field(action, "button"), 0, 31) &&
               integer_in(field(action, "x"), -127, 127) &&
               integer_in(field(action, "y"), -127, 127) &&
               integer_in(field(action, "wheel"), -127, 127) &&
               integer_in(field(action, "pan"), -127, 127);
    }
    if (strcmp(type->valuestring, "macro") == 0) {
        return KEYS_ARE(action, "type", "macro_id") && integer_in(field(action, "macro_id"), 0, 31);
    }
    if (strcmp(type->valuestring, "prompt") == 0) {
        return KEYS_ARE(action, "type", "prompt_id") &&
               integer_in(field(action, "prompt_id"), 1, 12);
    }
    if (strcmp(type->valuestring, "profile") == 0) {
        return KEYS_ARE(action, "type", "profile_id") && integer_in(field(action, "profile_id"), 0, 7);
    }
    if (strcmp(type->valuestring, "device") == 0) {
        const cJSON *name = field(action, "name");
        return KEYS_ARE(action, "type", "name") && cJSON_IsString(name) &&
               (strcmp(name->valuestring, "macro_cancel") == 0 ||
                strcmp(name->valuestring, "lighting_toggle") == 0 ||
                strcmp(name->valuestring, "haptic_toggle") == 0 ||
                strcmp(name->valuestring, "display_next") == 0);
    }
    return false;
}

static bool valid_macro_step(const cJSON *step)
{
    const cJSON *op = field(step, "op");
    if (!cJSON_IsObject(step) || !cJSON_IsString(op)) {
        return false;
    }
    if (strcmp(op->valuestring, "press") == 0 || strcmp(op->valuestring, "release") == 0 ||
        strcmp(op->valuestring, "tap") == 0) {
        return KEYS_ARE(step, "op", "usage") && integer_in(field(step, "usage"), 4, 231);
    }
    if (strcmp(op->valuestring, "delay") == 0) {
        return KEYS_ARE(step, "op", "duration_ms") && integer_in(field(step, "duration_ms"), 10, 5000);
    }
    if (strcmp(op->valuestring, "text") == 0) {
        const cJSON *text = field(step, "text");
        if (!KEYS_ARE(step, "op", "text") ||
            !string_length_in(text, 0, CONFIG_STORE_MAX_TEXT_BYTES)) {
            return false;
        }
        for (const unsigned char *cursor = (const unsigned char *)text->valuestring;
             *cursor != '\0'; ++cursor) {
            if (*cursor < 0x20 || *cursor > 0x7e) return false;
        }
        return true;
    }
    return false;
}

/*
 * Macro limits describe the compact step stream executed by the firmware, not
 * the JSON representation. Each step has a one-byte opcode. Key operations
 * add a one-byte HID usage, delays add a two-byte millisecond value, and text
 * adds a two-byte byte count followed by its printable-ASCII bytes.
 */
static size_t macro_step_encoded_cost(const cJSON *step)
{
    const char *op = field(step, "op")->valuestring;
    if (strcmp(op, "press") == 0 || strcmp(op, "release") == 0 ||
        strcmp(op, "tap") == 0) {
        return 2;
    }
    if (strcmp(op, "delay") == 0) {
        return 3;
    }
    return 3 + strlen(field(step, "text")->valuestring);
}

static bool valid_rgb_array(const cJSON *array, int count)
{
    if (!cJSON_IsArray(array) || cJSON_GetArraySize(array) != count) {
        return false;
    }
    cJSON *color = NULL;
    cJSON_ArrayForEach(color, array) {
        if (!KEYS_ARE(color, "r", "g", "b") || !integer_in(field(color, "r"), 0, 255) ||
            !integer_in(field(color, "g"), 0, 255) || !integer_in(field(color, "b"), 0, 255)) {
            return false;
        }
    }
    return true;
}

static bool valid_axis(const cJSON *axis)
{
    const cJSON *minimum = field(axis, "minimum");
    const cJSON *center = field(axis, "center");
    const cJSON *maximum = field(axis, "maximum");
    return KEYS_ARE(axis, "minimum", "center", "maximum", "deadzone", "invert") &&
           integer_in(minimum, 0, 4095) &&
           integer_in(center, 0, 4095) && integer_in(maximum, 0, 4095) &&
           minimum->valueint < center->valueint && center->valueint < maximum->valueint &&
           integer_in(field(axis, "deadzone"), 0, 1024) && cJSON_IsBool(field(axis, "invert"));
}

static bool validate_schema_for_hardware(const cJSON *root, bool allow_matrix12_v1,
                                         char *reason, size_t reason_size)
{
    if (!KEYS_ARE(root, "product_id", "hardware_id", "schema_version", "created_by",
                  "active_profile", "profiles", "macros", "lighting", "haptic",
                  "display", "joystick")) {
        set_reason(reason, reason_size, "config must be an object");
        return false;
    }
    const cJSON *product = field(root, "product_id");
    const cJSON *hardware = field(root, "hardware_id");
    if (!cJSON_IsString(product) || strcmp(product->valuestring, WMP_PRODUCT_ID) != 0) {
        set_reason(reason, reason_size, "product_id does not match");
        return false;
    }
    bool hardware_matches = cJSON_IsString(hardware) &&
                            strcmp(hardware->valuestring, board_hardware_id()) == 0;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    hardware_matches = hardware_matches ||
                       (allow_matrix12_v1 && cJSON_IsString(hardware) &&
                        strcmp(hardware->valuestring, MATRIX12_V1_HARDWARE_ID) == 0);
#else
    (void)allow_matrix12_v1;
#endif
    if (!hardware_matches) {
        set_reason(reason, reason_size, "hardware_id does not match");
        return false;
    }
    if (!integer_in(field(root, "schema_version"), CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_VERSION)) {
        set_reason(reason, reason_size, "schema_version is unsupported");
        return false;
    }
    if (!printable_ascii_string(field(root, "created_by"), 1, 64) ||
        !integer_in(field(root, "active_profile"), 0, 7)) {
        set_reason(reason, reason_size, "config metadata is invalid");
        return false;
    }

    const int active_profile = field(root, "active_profile")->valueint;
    const cJSON *profiles = field(root, "profiles");
    const int profile_count = cJSON_IsArray(profiles) ? cJSON_GetArraySize(profiles) : 0;
    if (profile_count < 1 || profile_count > 8) {
        set_reason(reason, reason_size, "profiles must contain 1 through 8 items");
        return false;
    }
    bool profile_ids[8] = {0};
    cJSON *profile = NULL;
    cJSON_ArrayForEach(profile, profiles) {
        const cJSON *id = field(profile, "id");
        const cJSON *mappings = field(profile, "mappings");
        if (!KEYS_ARE(profile, "id", "name", "mappings") ||
            !integer_in(id, 0, 7) || profile_ids[id->valueint] ||
            !string_length_in(field(profile, "name"), 1, 24) || !cJSON_IsArray(mappings) ||
            cJSON_GetArraySize(mappings) < 1 ||
            cJSON_GetArraySize(mappings) >
                (int)board_control_count()) {
            set_reason(reason, reason_size, "profile is invalid or duplicated");
            return false;
        }
        profile_ids[id->valueint] = true;
        bool controls[CONFIG_CONTROL_COUNT] = {0};
        cJSON *mapping = NULL;
        cJSON_ArrayForEach(mapping, mappings) {
            const cJSON *control = field(mapping, "control_id");
            if (!KEYS_ARE(mapping, "control_id", "short_name", "action") ||
                !cJSON_IsString(control) ||
                !valid_control_id(control->valuestring) ||
                !string_length_in(field(mapping, "short_name"), 0, 12) ||
                !valid_action(field(mapping, "action"))) {
                set_reason(reason, reason_size, "mapping is invalid");
                return false;
            }
            board_control_t control_index = BOARD_CONTROL_KEY_1;
            if (!board_control_from_id(control->valuestring, &control_index) ||
                controls[control_index]) {
                set_reason(reason, reason_size, "control mapping is duplicated");
                return false;
            }
            controls[control_index] = true;
        }
    }
    if (!profile_ids[active_profile]) {
        set_reason(reason, reason_size, "active_profile does not exist");
        return false;
    }

    const cJSON *macros = field(root, "macros");
    if (!cJSON_IsArray(macros) || cJSON_GetArraySize(macros) > 32) {
        set_reason(reason, reason_size, "macros array is invalid");
        return false;
    }
    bool macro_ids[32] = {0};
    size_t all_macro_bytes = 0;
    cJSON *macro = NULL;
    cJSON_ArrayForEach(macro, macros) {
        const cJSON *id = field(macro, "id");
        const cJSON *steps = field(macro, "steps");
        if (!KEYS_ARE(macro, "id", "name", "steps") ||
            !integer_in(id, 0, 31) || macro_ids[id->valueint] ||
            !string_length_in(field(macro, "name"), 1, 24) || !cJSON_IsArray(steps) ||
            cJSON_GetArraySize(steps) < 1 || cJSON_GetArraySize(steps) > 128) {
            set_reason(reason, reason_size, "macro is invalid or duplicated");
            return false;
        }
        macro_ids[id->valueint] = true;
        size_t macro_bytes = 0;
        cJSON *step = NULL;
        cJSON_ArrayForEach(step, steps) {
            if (!valid_macro_step(step)) {
                set_reason(reason, reason_size, "macro step is invalid");
                return false;
            }
            macro_bytes += macro_step_encoded_cost(step);
            if (macro_bytes > CONFIG_MACRO_MAX_BYTES) {
                set_reason(reason, reason_size, "macro encoded size exceeds 256 bytes");
                return false;
            }
        }
        all_macro_bytes += macro_bytes;
        if (all_macro_bytes > CONFIG_ALL_MACROS_MAX_BYTES) {
            set_reason(reason, reason_size, "all macro encoded sizes exceed 4096 bytes");
            return false;
        }
    }

    /* JSON Schema cannot express these cross-object references. */
    cJSON_ArrayForEach(profile, profiles) {
        cJSON *mapping = NULL;
        cJSON_ArrayForEach(mapping, field(profile, "mappings")) {
            const cJSON *action = field(mapping, "action");
            const char *type = field(action, "type")->valuestring;
            if (strcmp(type, "macro") == 0 && !macro_ids[field(action, "macro_id")->valueint]) {
                set_reason(reason, reason_size, "mapping references a missing macro");
                return false;
            }
            if (strcmp(type, "profile") == 0 && !profile_ids[field(action, "profile_id")->valueint]) {
                set_reason(reason, reason_size, "mapping references a missing profile");
                return false;
            }
        }
    }

    const cJSON *lighting = field(root, "lighting");
    const cJSON *haptic = field(root, "haptic");
    const cJSON *display = field(root, "display");
    const cJSON *joystick = field(root, "joystick");
    if (!KEYS_ARE(lighting, "enabled", "brightness", "status", "under_key") ||
        !cJSON_IsBool(field(lighting, "enabled")) ||
        !integer_in(field(lighting, "brightness"), 0, 100) ||
        !valid_rgb_array(field(lighting, "status"), 8) ||
        !valid_rgb_array(field(lighting, "under_key"),
                         board_under_key_rgb_count() > 0
                             ? (int)board_under_key_rgb_count() : 7)) {
        set_reason(reason, reason_size, "lighting config is invalid");
        return false;
    }
    if (!KEYS_ARE(haptic, "enabled", "strength", "duration_ms", "on_press", "on_profile",
                  "on_encoder", "on_joystick", "on_task") ||
        !cJSON_IsBool(field(haptic, "enabled")) ||
        !integer_in(field(haptic, "strength"), 0, 100) ||
        !integer_in(field(haptic, "duration_ms"), 10, 500) ||
        !cJSON_IsBool(field(haptic, "on_press")) || !cJSON_IsBool(field(haptic, "on_profile")) ||
        (field(haptic, "on_encoder") && !cJSON_IsBool(field(haptic, "on_encoder"))) ||
        (field(haptic, "on_joystick") && !cJSON_IsBool(field(haptic, "on_joystick"))) ||
        (field(haptic, "on_task") && !cJSON_IsBool(field(haptic, "on_task")))) {
        set_reason(reason, reason_size, "haptic config is invalid");
        return false;
    }
    const cJSON *rotation = field(display, "rotation");
    if (!KEYS_ARE(display, "brightness", "rotation", "show_control_hints") ||
        !integer_in(field(display, "brightness"), 0, 100) ||
        !integer_in(rotation, 0, 270) ||
        !(rotation->valueint == 0 || rotation->valueint == 90 || rotation->valueint == 180 || rotation->valueint == 270) ||
        !cJSON_IsBool(field(display, "show_control_hints"))) {
        set_reason(reason, reason_size, "display config is invalid");
        return false;
    }
    if (!KEYS_ARE(joystick, "calibrated", "filter", "x", "y") ||
        !cJSON_IsBool(field(joystick, "calibrated")) ||
        !integer_in(field(joystick, "filter"), 0, 100) || !valid_axis(field(joystick, "x")) ||
        !valid_axis(field(joystick, "y"))) {
        set_reason(reason, reason_size, "joystick config is invalid");
        return false;
    }
    return true;
}

static bool validate_schema(const cJSON *root, char *reason, size_t reason_size)
{
    return validate_schema_for_hardware(root, false, reason, reason_size);
}

static cJSON *profile_mapping(cJSON *profile, const char *control_id)
{
    cJSON *mappings = mutable_field(profile, "mappings");
    cJSON *mapping = NULL;
    cJSON_ArrayForEach(mapping, mappings) {
        cJSON *candidate = mutable_field(mapping, "control_id");
        if (cJSON_IsString(candidate) &&
            strcmp(candidate->valuestring, control_id) == 0) {
            return mapping;
        }
    }
    return NULL;
}

static bool mapping_is_legacy_function_key(cJSON *mapping, size_t offset)
{
    char expected_name[4];
    snprintf(expected_name, sizeof(expected_name), "F%u", (unsigned)(13u + offset));
    const cJSON *name = field(mapping, "short_name");
    const cJSON *action = field(mapping, "action");
    const cJSON *type = field(action, "type");
    const cJSON *usage = field(action, "usage");
    const cJSON *modifiers = field(action, "modifiers");
    return cJSON_IsString(name) && strcmp(name->valuestring, expected_name) == 0 &&
           cJSON_IsObject(action) && cJSON_IsString(type) &&
           strcmp(type->valuestring, "key") == 0 && cJSON_IsNumber(usage) &&
           usage->valueint == (int)(104u + offset) && cJSON_IsArray(modifiers) &&
           cJSON_GetArraySize(modifiers) == 0;
}

static esp_err_t replace_key_mapping(cJSON *mapping, const char *short_name,
                                     int usage, const int *modifiers,
                                     size_t modifier_count)
{
    cJSON *replacement_name = cJSON_CreateString(short_name);
    cJSON *replacement_action = cJSON_CreateObject();
    cJSON *replacement_modifiers = cJSON_CreateArray();
    if (replacement_name == NULL || replacement_action == NULL ||
        replacement_modifiers == NULL) {
        cJSON_Delete(replacement_name);
        cJSON_Delete(replacement_action);
        cJSON_Delete(replacement_modifiers);
        return ESP_ERR_NO_MEM;
    }
    for (size_t index = 0; index < modifier_count; ++index) {
        cJSON *modifier = cJSON_CreateNumber(modifiers[index]);
        if (modifier == NULL ||
            !cJSON_AddItemToArray(replacement_modifiers, modifier)) {
            cJSON_Delete(modifier);
            cJSON_Delete(replacement_name);
            cJSON_Delete(replacement_action);
            cJSON_Delete(replacement_modifiers);
            return ESP_ERR_NO_MEM;
        }
    }
    if (!cJSON_AddStringToObject(replacement_action, "type", "key") ||
        !cJSON_AddNumberToObject(replacement_action, "usage", usage)) {
        cJSON_Delete(replacement_name);
        cJSON_Delete(replacement_action);
        cJSON_Delete(replacement_modifiers);
        return ESP_ERR_NO_MEM;
    }
    if (!cJSON_AddItemToObject(replacement_action, "modifiers",
                               replacement_modifiers)) {
        cJSON_Delete(replacement_name);
        cJSON_Delete(replacement_action);
        cJSON_Delete(replacement_modifiers);
        return ESP_ERR_NO_MEM;
    }
    if (!cJSON_ReplaceItemInObjectCaseSensitive(mapping, "short_name",
                                                 replacement_name)) {
        cJSON_Delete(replacement_name);
        cJSON_Delete(replacement_action);
        return ESP_ERR_INVALID_STATE;
    }
    if (!cJSON_ReplaceItemInObjectCaseSensitive(mapping, "action",
                                                 replacement_action)) {
        cJSON_Delete(replacement_action);
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

static esp_err_t migrate_legacy_matrix12_extra_keys(cJSON *root, bool *changed)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_V1 || CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    static const char *const SHORT_NAMES[LEGACY_MATRIX12_EXTRA_KEY_COUNT] = {
        "Space", "Undo", "Redo", "Copy", "Paste",
    };
    static const int USAGES[LEGACY_MATRIX12_EXTRA_KEY_COUNT] = {44, 29, 29, 6, 25};
    cJSON *profiles = mutable_field(root, "profiles");
    cJSON *profile = NULL;
    cJSON_ArrayForEach(profile, profiles) {
        const cJSON *profile_name = field(profile, "name");
        const bool macos = cJSON_IsString(profile_name) &&
                           strcmp(profile_name->valuestring, "Codex macOS") == 0;
        const bool windows = cJSON_IsString(profile_name) &&
                             strcmp(profile_name->valuestring, "Codex Windows") == 0;
        if (!macos && !windows) {
            continue;
        }

        cJSON *legacy[LEGACY_MATRIX12_EXTRA_KEY_COUNT] = {0};
        bool exact_legacy_set = true;
        for (size_t offset = 0; offset < LEGACY_MATRIX12_EXTRA_KEY_COUNT; ++offset) {
            char control_id[8];
            snprintf(control_id, sizeof(control_id), "key.%u",
                     (unsigned)(LEGACY_MATRIX12_EXTRA_KEY_FIRST + offset));
            legacy[offset] = profile_mapping(profile, control_id);
            exact_legacy_set = exact_legacy_set && legacy[offset] != NULL &&
                               mapping_is_legacy_function_key(legacy[offset], offset);
        }
        if (!exact_legacy_set) {
            continue;
        }

        for (size_t offset = 0; offset < LEGACY_MATRIX12_EXTRA_KEY_COUNT; ++offset) {
            int modifiers[2] = {0};
            size_t modifier_count = 0;
            if (offset > 0) {
                if (macos && offset == 2) {
                    modifiers[modifier_count++] = 225;
                }
                modifiers[modifier_count++] = macos ? 227 : 224;
            }
            const int usage = !macos && offset == 2 ? 28 : USAGES[offset];
            esp_err_t error = replace_key_mapping(
                legacy[offset], SHORT_NAMES[offset], usage, modifiers,
                modifier_count);
            if (error != ESP_OK) {
                return error;
            }
        }
        *changed = true;
    }
#else
    (void)root;
    (void)changed;
#endif
    return ESP_OK;
}

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
static bool json_rgb_equals(const cJSON *value, config_rgb_t expected)
{
    return cJSON_IsObject(value) &&
           integer_in(field(value, "r"), 0, 255) &&
           integer_in(field(value, "g"), 0, 255) &&
           integer_in(field(value, "b"), 0, 255) &&
           field(value, "r")->valueint == expected.red &&
           field(value, "g")->valueint == expected.green &&
           field(value, "b")->valueint == expected.blue;
}

static cJSON *create_rgb(config_rgb_t value)
{
    cJSON *color = cJSON_CreateObject();
    if (color == NULL ||
        !cJSON_AddNumberToObject(color, "r", value.red) ||
        !cJSON_AddNumberToObject(color, "g", value.green) ||
        !cJSON_AddNumberToObject(color, "b", value.blue)) {
        cJSON_Delete(color);
        return NULL;
    }
    return color;
}
#endif

static esp_err_t migrate_power_v2_default_action_key_colors(cJSON *root,
                                                             bool *changed)
{
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    static const size_t KEY3_AND_ACTION_KEY_INDEXES[] = {2, 7, 8, 9, 10, 11};
    static const config_rgb_t LEGACY_KEY3_AND_ACTION_KEY_COLORS[] = {
        {.red = 0, .green = 48, .blue = 64},
        {.red = 0, .green = 24, .blue = 48},
        {.red = 24, .green = 0, .blue = 48},
        {.red = 0, .green = 40, .blue = 32},
        {.red = 40, .green = 16, .blue = 0},
        {.red = 32, .green = 32, .blue = 0},
    };
    static const config_rgb_t UNIFIED_ACTION_KEY_COLOR = {
        .red = 0,
        .green = 24,
        .blue = 48,
    };
    cJSON *lighting = mutable_field(root, "lighting");
    cJSON *under_key = mutable_field(lighting, "under_key");
    bool exact_legacy_palette = cJSON_IsArray(under_key);
    for (size_t position = 0;
         exact_legacy_palette &&
         position < sizeof(KEY3_AND_ACTION_KEY_INDEXES) /
                        sizeof(KEY3_AND_ACTION_KEY_INDEXES[0]);
         ++position) {
        exact_legacy_palette = json_rgb_equals(
            cJSON_GetArrayItem(under_key, KEY3_AND_ACTION_KEY_INDEXES[position]),
            LEGACY_KEY3_AND_ACTION_KEY_COLORS[position]);
    }
    if (!exact_legacy_palette) {
        return ESP_OK;
    }

    for (size_t position = 1;
         position < sizeof(KEY3_AND_ACTION_KEY_INDEXES) /
                        sizeof(KEY3_AND_ACTION_KEY_INDEXES[0]);
         ++position) {
        cJSON *replacement = create_rgb(UNIFIED_ACTION_KEY_COLOR);
        if (replacement == NULL) {
            return ESP_ERR_NO_MEM;
        }
        if (!cJSON_ReplaceItemInArray(
                under_key, KEY3_AND_ACTION_KEY_INDEXES[position], replacement)) {
            cJSON_Delete(replacement);
            return ESP_ERR_INVALID_STATE;
        }
    }
    *changed = true;
#else
    (void)root;
    (void)changed;
#endif
    return ESP_OK;
}

static esp_err_t migrate_matrix12_v1_root(cJSON *root, bool *changed)
{
    *changed = false;
#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
    cJSON *hardware = mutable_field(root, "hardware_id");
    if (cJSON_IsString(hardware) &&
        strcmp(hardware->valuestring, MATRIX12_V1_HARDWARE_ID) == 0) {
        cJSON *replacement = cJSON_CreateString(board_hardware_id());
        if (replacement == NULL) {
            return ESP_ERR_NO_MEM;
        }
        if (!cJSON_ReplaceItemInObjectCaseSensitive(root, "hardware_id", replacement)) {
            cJSON_Delete(replacement);
            return ESP_ERR_INVALID_STATE;
        }
        *changed = true;
    }
#else
    (void)root;
#endif
    esp_err_t error = migrate_power_v2_default_action_key_colors(root, changed);
    if (error != ESP_OK) {
        return error;
    }
    return migrate_legacy_matrix12_extra_keys(root, changed);
}

static const char *slot_json_key(uint8_t slot)
{
    return slot == 0 ? "a_json" : "b_json";
}

static const char *slot_meta_key(uint8_t slot)
{
    return slot == 0 ? "a_meta" : "b_meta";
}

static esp_err_t read_slot(uint8_t slot, cJSON **root, char **json, slot_meta_t *meta,
                           char *reason, size_t reason_size)
{
    size_t meta_size = 0;
    esp_err_t error = nvs_get_blob(s_nvs, slot_meta_key(slot), NULL, &meta_size);
    memset(meta, 0, sizeof(*meta));
    if (error == ESP_OK && meta_size == sizeof(*meta)) {
        error = nvs_get_blob(s_nvs, slot_meta_key(slot), meta, &meta_size);
        if (error == ESP_OK &&
            (meta->intent_magic != CONFIG_INTENT_MAGIC ||
             meta->platform_intent > CONFIG_PLATFORM_CUSTOM || meta->factory_reset > 1)) {
            error = ESP_ERR_INVALID_STATE;
        }
    } else if (error == ESP_OK && meta_size == sizeof(legacy_slot_meta_t)) {
        legacy_slot_meta_t legacy;
        error = nvs_get_blob(s_nvs, slot_meta_key(slot), &legacy, &meta_size);
        if (error == ESP_OK) {
            meta->magic = legacy.magic;
            meta->schema_version = legacy.schema_version;
            meta->generation = legacy.generation;
            meta->json_length = legacy.json_length;
            memcpy(meta->digest, legacy.digest, sizeof(meta->digest));
            meta->digest[CONFIG_STORE_DIGEST_HEX_LENGTH] = '\0';
        }
    } else if (error == ESP_OK) {
        error = ESP_ERR_INVALID_SIZE;
    }
    if (error != ESP_OK || meta->magic != CONFIG_META_MAGIC ||
        meta->schema_version != CONFIG_SCHEMA_VERSION || meta->json_length == 0 ||
        meta->json_length > WMP_CONFIG_MAX_CANONICAL_BYTES) {
        if (reason != NULL && reason_size > 0) {
            snprintf(reason, reason_size,
                     "inactive slot metadata invalid: %s size=%u magic=0x%08x schema=%u len=%u",
                     esp_err_to_name(error), (unsigned)meta_size,
                     (unsigned)meta->magic, (unsigned)meta->schema_version,
                     (unsigned)meta->json_length);
        }
        return ESP_ERR_INVALID_STATE;
    }
    size_t json_size = meta->json_length + 1;
    char *stored = calloc(json_size, 1);
    if (stored == NULL) {
        return ESP_ERR_NO_MEM;
    }
    error = nvs_get_blob(s_nvs, slot_json_key(slot), stored, &json_size);
    if (error != ESP_OK || json_size != meta->json_length + 1 ||
        stored[meta->json_length] != '\0') {
        if (reason != NULL && reason_size > 0) {
            snprintf(reason, reason_size,
                     "inactive slot JSON invalid: %s size=%u expected=%u nul=%u",
                     esp_err_to_name(error), (unsigned)json_size,
                     (unsigned)(meta->json_length + 1),
                     (unsigned)(uint8_t)stored[meta->json_length]);
        }
        free(stored);
        return ESP_ERR_INVALID_STATE;
    }
    cJSON *parsed = cJSON_ParseWithLength(stored, meta->json_length);
    char schema_reason[96];
    if (parsed == NULL ||
        !validate_schema_for_hardware(parsed, true, schema_reason, sizeof(schema_reason))) {
        if (reason != NULL && reason_size > 0) {
            snprintf(reason, reason_size, "inactive slot config invalid: %s",
                     parsed == NULL ? "JSON parse failed" : schema_reason);
        }
        cJSON_Delete(parsed);
        free(stored);
        return ESP_ERR_INVALID_STATE;
    }
    char *canonical = NULL;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    error = canonicalize(parsed, &canonical, digest);
    if (error != ESP_OK || strcmp(digest, meta->digest) != 0 ||
        strlen(canonical) != meta->json_length ||
        memcmp(canonical, stored, meta->json_length) != 0) {
        free(canonical);
        cJSON_Delete(parsed);
        free(stored);
        return ESP_ERR_INVALID_CRC;
    }
    free(stored);
    *root = parsed;
    *json = canonical;
    return ESP_OK;
}

static esp_err_t slot_write_error(esp_err_t error, const char *step,
                                  char *reason, size_t reason_size)
{
    if (reason != NULL && reason_size > 0) {
        snprintf(reason, reason_size, "inactive slot %s failed: %s (0x%x)",
                 step, esp_err_to_name(error), (unsigned)error);
    }
    return error;
}

static esp_err_t verify_slot_write(uint8_t slot, const char *json,
                                   const slot_meta_t *meta,
                                   char *reason, size_t reason_size)
{
    slot_meta_t stored_meta;
    size_t meta_size = sizeof(stored_meta);
    esp_err_t error = nvs_get_blob(s_nvs, slot_meta_key(slot),
                                   &stored_meta, &meta_size);
    if (error != ESP_OK) {
        return slot_write_error(error, "metadata readback", reason, reason_size);
    }
    if (meta_size != sizeof(stored_meta) ||
        memcmp(&stored_meta, meta, sizeof(stored_meta)) != 0) {
        return slot_write_error(ESP_ERR_INVALID_STATE, "metadata compare",
                                reason, reason_size);
    }

    const size_t expected_json_size = meta->json_length + 1;
    size_t stored_json_size = expected_json_size;
    char *stored_json = malloc(expected_json_size);
    if (stored_json == NULL) {
        return slot_write_error(ESP_ERR_NO_MEM, "JSON readback allocation",
                                reason, reason_size);
    }
    error = nvs_get_blob(s_nvs, slot_json_key(slot), stored_json,
                         &stored_json_size);
    if (error == ESP_OK &&
        (stored_json_size != expected_json_size ||
         memcmp(stored_json, json, expected_json_size) != 0)) {
        error = ESP_ERR_INVALID_STATE;
    }
    free(stored_json);
    return error == ESP_OK ? ESP_OK
                           : slot_write_error(error, "JSON compare",
                                              reason, reason_size);
}

static esp_err_t write_slot(uint8_t slot, const char *json, const slot_meta_t *meta,
                            char *reason, size_t reason_size)
{
    esp_err_t error = nvs_set_blob(s_nvs, slot_json_key(slot), json, meta->json_length + 1);
    if (error != ESP_OK) {
        return slot_write_error(error, "JSON write", reason, reason_size);
    }
    error = nvs_set_blob(s_nvs, slot_meta_key(slot), meta, sizeof(*meta));
    if (error != ESP_OK) {
        return slot_write_error(error, "metadata write", reason, reason_size);
    }
    error = nvs_commit(s_nvs);
    if (error != ESP_OK) {
        return slot_write_error(error, "commit", reason, reason_size);
    }
    return verify_slot_write(slot, json, meta, reason, reason_size);
}

static esp_err_t migrate_matrix12_v1_slot(uint8_t slot, cJSON **root, char **json,
                                          slot_meta_t *meta)
{
    bool changed = false;
    esp_err_t error = migrate_matrix12_v1_root(*root, &changed);
    if (error != ESP_OK || !changed) {
        return error;
    }

    char *canonical = NULL;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    error = canonicalize(*root, &canonical, digest);
    if (error != ESP_OK) {
        return error;
    }

    slot_meta_t migrated = *meta;
    migrated.json_length = strlen(canonical);
    snprintf(migrated.digest, sizeof(migrated.digest), "%s", digest);
    error = write_slot(slot, canonical, &migrated, NULL, 0);
    if (error != ESP_OK) {
        free(canonical);
        return error;
    }

    free(*json);
    *json = canonical;
    *meta = migrated;
    ESP_LOGI(TAG, "migrated config slot %u for %s", slot, board_hardware_id());
    return ESP_OK;
}

static esp_err_t prepare_candidate(const cJSON *candidate, const char *expected_digest,
                                   cJSON **root, char **json, slot_meta_t *meta,
                                   char *reason, size_t reason_size)
{
    if (!validate_schema(candidate, reason, reason_size)) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *copy = cJSON_Duplicate(candidate, true);
    if (copy == NULL) {
        set_reason(reason, reason_size, "could not copy candidate config");
        return ESP_ERR_NO_MEM;
    }
    char *canonical = NULL;
    char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1];
    esp_err_t error = canonicalize(copy, &canonical, digest);
    if (error != ESP_OK) {
        cJSON_Delete(copy);
        if (reason != NULL && reason_size > 0) {
            snprintf(reason, reason_size, "config canonicalization failed: %s (0x%x)",
                     esp_err_to_name(error), (unsigned)error);
        }
        return error;
    }
    if (expected_digest != NULL && expected_digest[0] != '\0' && strcmp(expected_digest, digest) != 0) {
        cJSON_Delete(copy);
        free(canonical);
        set_reason(reason, reason_size, "candidate digest does not match canonical config");
        return ESP_ERR_INVALID_CRC;
    }
    memset(meta, 0, sizeof(*meta));
    meta->magic = CONFIG_META_MAGIC;
    meta->schema_version = CONFIG_SCHEMA_VERSION;
    meta->json_length = strlen(canonical);
    snprintf(meta->digest, sizeof(meta->digest), "%s", digest);
    *root = copy;
    *json = canonical;
    return ESP_OK;
}

static esp_err_t erase_optional_key(const char *key)
{
    const esp_err_t error = nvs_erase_key(s_nvs, key);
    return error == ESP_ERR_NVS_NOT_FOUND ? ESP_OK : error;
}

static esp_err_t read_ble_name_locked(char name[WMP_BLE_NAME_MAX_UTF8_BYTES + 1])
{
    size_t length = WMP_BLE_NAME_MAX_UTF8_BYTES + 1;
    const esp_err_t error = nvs_get_str(s_nvs, CONFIG_KEY_BLE_NAME, name, &length);
    if (error == ESP_ERR_NVS_NOT_FOUND || error == ESP_ERR_NVS_INVALID_LENGTH ||
        (error == ESP_OK && (length == 0 || length > WMP_BLE_NAME_MAX_UTF8_BYTES + 1 ||
                            name[length - 1] != '\0' || !ble_name_valid(name, length - 1)))) {
        memcpy(name, WMP_BLE_NAME_DEFAULT, sizeof(WMP_BLE_NAME_DEFAULT));
        return ESP_OK;
    }
    return error;
}

/* NVS set operations may already be visible when commit fails. Restore the
 * previous effective preference when possible, and keep GET reading real NVS. */
static void restore_ble_name_locked(const char *name)
{
    esp_err_t error = nvs_set_str(s_nvs, CONFIG_KEY_BLE_NAME, name);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error != ESP_OK) {
        ESP_LOGE(TAG, "BLE name restore failed: %s", esp_err_to_name(error));
    }
}

/* reserved[0] in the existing slot metadata captures context + 1. Zero in old
 * slots means no connection preference intent; the binary layout is unchanged. */
static esp_err_t persist_context_intent(const slot_meta_t *meta)
{
    if (meta->factory_reset) {
        for (uint8_t context = 0; context < CONFIG_PLATFORM_CONTEXT_COUNT; ++context) {
            const esp_err_t error = erase_optional_key(PLATFORM_CONTEXT_KEYS[context]);
            if (error != ESP_OK) {
                return error;
            }
        }
    } else if (meta->reserved[0] >= 1 &&
               meta->reserved[0] <= CONFIG_PLATFORM_CONTEXT_COUNT &&
               (meta->platform_intent == CONFIG_PLATFORM_MACOS ||
                meta->platform_intent == CONFIG_PLATFORM_WINDOWS_LINUX)) {
        return nvs_set_u8(s_nvs, PLATFORM_CONTEXT_KEYS[meta->reserved[0] - 1],
                          meta->platform_intent);
    }
    return ESP_OK;
}

static void apply_context_intent(const slot_meta_t *meta)
{
    if (meta->factory_reset) {
        for (uint8_t context = 0; context < CONFIG_PLATFORM_CONTEXT_COUNT; ++context) {
            atomic_store(&s_context_platforms[context], CONFIG_PLATFORM_UNSELECTED);
        }
    } else if (meta->reserved[0] >= 1 &&
               meta->reserved[0] <= CONFIG_PLATFORM_CONTEXT_COUNT &&
               (meta->platform_intent == CONFIG_PLATFORM_MACOS ||
                meta->platform_intent == CONFIG_PLATFORM_WINDOWS_LINUX)) {
        atomic_store(&s_context_platforms[meta->reserved[0] - 1], meta->platform_intent);
    }
}

static esp_err_t stage_locked(const cJSON *candidate, const char *expected_digest,
                              uint32_t base_generation, bool enforce_generation,
                              bool factory_reset, config_platform_t pending_platform,
                              uint8_t context_intent,
                              char *reason, size_t reason_size)
{
    if (s_pending_root != NULL) {
        set_reason(reason, reason_size, "another config is pending activation");
        return ESP_ERR_INVALID_STATE;
    }
    if (enforce_generation && base_generation != s_active_meta.generation) {
        set_reason(reason, reason_size, "base_generation does not match");
        return ESP_ERR_INVALID_VERSION;
    }
    cJSON *root = NULL;
    char *json = NULL;
    slot_meta_t meta;
    esp_err_t error = prepare_candidate(candidate, expected_digest, &root, &json, &meta,
                                        reason, reason_size);
    if (error != ESP_OK) {
        return error;
    }
    meta.generation = s_active_meta.generation + 1;
    meta.intent_magic = CONFIG_INTENT_MAGIC;
    meta.platform_intent = (uint8_t)(factory_reset ? CONFIG_PLATFORM_UNSELECTED
                                                   : pending_platform);
    meta.factory_reset = factory_reset ? 1 : 0;
    meta.reserved[0] = factory_reset ? 0 : context_intent;
    const uint8_t slot = s_active_slot == 0 ? 1 : 0;
    error = write_slot(slot, json, &meta, reason, reason_size);
    if (error != ESP_OK) {
        cJSON_Delete(root);
        free(json);
        return error;
    }
    error = nvs_set_u8(s_nvs, CONFIG_KEY_PENDING, slot);
    if (error == ESP_OK) {
        error = factory_reset ? nvs_set_u8(s_nvs, CONFIG_KEY_FACTORY_PENDING, 1)
                              : erase_optional_key(CONFIG_KEY_FACTORY_PENDING);
    }
    if (error == ESP_OK) {
        if (!factory_reset && pending_platform != CONFIG_PLATFORM_UNSELECTED) {
            error = nvs_set_u8(s_nvs, CONFIG_KEY_PENDING_PLATFORM,
                               (uint8_t)pending_platform);
        } else {
            error = erase_optional_key(CONFIG_KEY_PENDING_PLATFORM);
        }
    }
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error != ESP_OK) {
        cJSON_Delete(root);
        free(json);
        set_reason(reason, reason_size, "could not persist pending activation marker");
        return error;
    }
    s_pending_root = root;
    s_pending_json = json;
    s_pending_meta = meta;
    s_pending_slot = slot;
    s_pending_factory_reset = factory_reset;
    s_pending_platform = factory_reset ? CONFIG_PLATFORM_UNSELECTED : pending_platform;
    s_activation_failures = 0;
    s_activation_retry_at = 0;
    atomic_store(&s_activation_failed, false);
    atomic_store(&s_has_pending, true);
    set_reason(reason, reason_size, "valid");
    return ESP_OK;
}

esp_err_t config_store_init(void)
{
    atomic_store(&s_factory_reset_activated, false);
    if (psa_crypto_init() != PSA_SUCCESS) {
        return ESP_FAIL;
    }
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t error = nvs_flash_init_partition(CONFIG_PARTITION);
    if (error != ESP_OK) {
        return error;
    }
    error = nvs_open_from_partition(CONFIG_PARTITION, CONFIG_NAMESPACE, NVS_READWRITE, &s_nvs);
    if (error != ESP_OK) {
        return error;
    }

    atomic_store(&s_platform_context, 0);
    for (uint8_t context = 0; context < CONFIG_PLATFORM_CONTEXT_COUNT; ++context) {
        uint8_t value = CONFIG_PLATFORM_UNSELECTED;
        const bool valid = nvs_get_u8(s_nvs, PLATFORM_CONTEXT_KEYS[context], &value) == ESP_OK &&
                           (value == CONFIG_PLATFORM_MACOS || value == CONFIG_PLATFORM_WINDOWS_LINUX);
        atomic_store(&s_context_platforms[context], valid ? value : CONFIG_PLATFORM_UNSELECTED);
    }

    uint8_t marker = 0;
    const bool marker_valid = nvs_get_u8(s_nvs, CONFIG_KEY_ACTIVE, &marker) == ESP_OK && marker <= 1;
    uint8_t pending_marker = 0;
    const bool pending_marker_valid =
        nvs_get_u8(s_nvs, CONFIG_KEY_PENDING, &pending_marker) == ESP_OK && pending_marker <= 1;
    uint8_t factory_marker = 0;
    const bool factory_pending =
        nvs_get_u8(s_nvs, CONFIG_KEY_FACTORY_PENDING, &factory_marker) == ESP_OK &&
        factory_marker == 1;
    uint8_t platform_value = 0;
    const bool platform_stored =
        nvs_get_u8(s_nvs, CONFIG_KEY_PLATFORM, &platform_value) == ESP_OK &&
        platform_value <= CONFIG_PLATFORM_CUSTOM;
    bool platform_state_known = platform_stored;
    atomic_store(&s_platform, platform_stored ? (config_platform_t)platform_value
                                              : CONFIG_PLATFORM_UNSELECTED);
    uint8_t pending_platform_value = 0;
    const bool pending_platform_valid =
        nvs_get_u8(s_nvs, CONFIG_KEY_PENDING_PLATFORM, &pending_platform_value) == ESP_OK &&
        pending_platform_value >= CONFIG_PLATFORM_MACOS &&
        pending_platform_value <= CONFIG_PLATFORM_CUSTOM;
    cJSON *roots[2] = {NULL, NULL};
    char *jsons[2] = {NULL, NULL};
    slot_meta_t metas[2];
    bool valid[2] = {
        read_slot(0, &roots[0], &jsons[0], &metas[0], NULL, 0) == ESP_OK,
        read_slot(1, &roots[1], &jsons[1], &metas[1], NULL, 0) == ESP_OK,
    };
    for (uint8_t slot = 0; slot < 2; ++slot) {
        if (!valid[slot]) {
            continue;
        }
        error = migrate_matrix12_v1_slot(slot, &roots[slot], &jsons[slot],
                                         &metas[slot]);
        if (error != ESP_OK) {
            for (size_t index = 0; index < 2; ++index) {
                cJSON_Delete(roots[index]);
                free(jsons[index]);
            }
            return error;
        }
    }
    int selected = -1;
    if (marker_valid && valid[marker]) {
        selected = marker;
    } else if (pending_marker_valid && valid[1 - pending_marker]) {
        selected = 1 - pending_marker;
    } else if (valid[0] || valid[1]) {
        selected = valid[0] && valid[1] ? (metas[1].generation > metas[0].generation ? 1 : 0)
                                         : (valid[0] ? 0 : 1);
    }
    if (selected >= 0) {
        s_active_slot = (uint8_t)selected;
        s_active_root = roots[selected];
        s_active_json = jsons[selected];
        s_active_meta = metas[selected];
        const int other = 1 - selected;
        if (pending_marker_valid && pending_marker == other && valid[other] &&
            metas[other].generation == s_active_meta.generation + 1) {
            s_pending_slot = (uint8_t)other;
            s_pending_root = roots[other];
            s_pending_json = jsons[other];
            s_pending_meta = metas[other];
            const bool slot_intent = metas[other].intent_magic == CONFIG_INTENT_MAGIC;
            s_pending_factory_reset = slot_intent ? metas[other].factory_reset != 0
                                                   : factory_pending;
            s_pending_platform = slot_intent
                ? (config_platform_t)metas[other].platform_intent
                : (!factory_pending && pending_platform_valid
                       ? (config_platform_t)pending_platform_value
                       : CONFIG_PLATFORM_UNSELECTED);
            atomic_store(&s_has_pending, true);
        } else {
            cJSON_Delete(roots[other]);
            free(jsons[other]);
            const bool interrupted_activation = marker_valid && pending_marker_valid &&
                                                marker == selected &&
                                                pending_marker == selected;
            if (interrupted_activation) {
                const bool slot_intent = metas[selected].intent_magic == CONFIG_INTENT_MAGIC;
                const bool reset = slot_intent ? metas[selected].factory_reset != 0
                                               : factory_pending;
                const config_platform_t intent = slot_intent
                    ? (config_platform_t)metas[selected].platform_intent
                    : (pending_platform_valid
                           ? (config_platform_t)pending_platform_value
                           : CONFIG_PLATFORM_UNSELECTED);
                if (reset || intent != CONFIG_PLATFORM_UNSELECTED) {
                    const config_platform_t repaired = reset
                        ? CONFIG_PLATFORM_UNSELECTED : intent;
                    atomic_store(&s_platform, repaired);
                    platform_state_known = true;
                    error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM, (uint8_t)repaired);
                }
                if (error == ESP_OK && slot_intent) {
                    error = persist_context_intent(&metas[selected]);
                    if (error == ESP_OK) {
                        apply_context_intent(&metas[selected]);
                    }
                }
                if (error == ESP_OK && reset) {
                    error = nvs_set_str(s_nvs, CONFIG_KEY_BLE_NAME, WMP_BLE_NAME_DEFAULT);
                }
            }
            (void)erase_optional_key(CONFIG_KEY_PENDING);
            (void)erase_optional_key(CONFIG_KEY_FACTORY_PENDING);
            (void)erase_optional_key(CONFIG_KEY_PENDING_PLATFORM);
        }
        if (!platform_state_known && !s_pending_factory_reset) {
            atomic_store(&s_platform, CONFIG_PLATFORM_CUSTOM);
            error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM,
                               (uint8_t)CONFIG_PLATFORM_CUSTOM);
        }
        if (error == ESP_OK) {
            error = nvs_set_u8(s_nvs, CONFIG_KEY_ACTIVE, s_active_slot);
        }
        if (error != ESP_OK) {
            return error;
        }
        return nvs_commit(s_nvs);
    }
    cJSON_Delete(roots[0]);
    cJSON_Delete(roots[1]);
    free(jsons[0]);
    free(jsons[1]);

    cJSON *defaults = parse_defaults();
    if (defaults == NULL) {
        return ESP_FAIL;
    }
    char reason[96];
    char *canonical = NULL;
    slot_meta_t meta;
    cJSON *copy = NULL;
    error = prepare_candidate(defaults, NULL, &copy, &canonical, &meta, reason, sizeof(reason));
    cJSON_Delete(defaults);
    if (error != ESP_OK) {
        return error;
    }
    meta.generation = 1;
    meta.intent_magic = CONFIG_INTENT_MAGIC;
    meta.platform_intent = CONFIG_PLATFORM_UNSELECTED;
    meta.factory_reset = 0;
    error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM,
                       (uint8_t)CONFIG_PLATFORM_UNSELECTED);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error == ESP_OK) {
        error = write_slot(0, canonical, &meta, NULL, 0);
    }
    if (error == ESP_OK) {
        error = nvs_set_u8(s_nvs, CONFIG_KEY_ACTIVE, 0);
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_PENDING);
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_FACTORY_PENDING);
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_PENDING_PLATFORM);
    }
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error != ESP_OK) {
        cJSON_Delete(copy);
        free(canonical);
        return error;
    }
    s_active_slot = 0;
    s_active_root = copy;
    s_active_json = canonical;
    s_active_meta = meta;
    atomic_store(&s_platform, CONFIG_PLATFORM_UNSELECTED);
    return ESP_OK;
}

esp_err_t config_store_get_json(char **json, uint32_t *generation,
                                char digest[CONFIG_STORE_DIGEST_HEX_LENGTH + 1])
{
    if (json == NULL || generation == NULL || digest == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const size_t length = strlen(s_active_json) + 1;
    *json = malloc(length);
    if (*json != NULL) {
        memcpy(*json, s_active_json, length);
    }
    *generation = s_active_meta.generation;
    snprintf(digest, CONFIG_STORE_DIGEST_HEX_LENGTH + 1, "%s", s_active_meta.digest);
    xSemaphoreGive(s_lock);
    return *json != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t config_store_validate(const cJSON *candidate, const char *expected_digest,
                                char *reason, size_t reason_size)
{
    cJSON *root = NULL;
    char *json = NULL;
    slot_meta_t meta;
    esp_err_t error = prepare_candidate(candidate, expected_digest, &root, &json, &meta,
                                        reason, reason_size);
    cJSON_Delete(root);
    free(json);
    if (error == ESP_OK) {
        set_reason(reason, reason_size, "valid");
    }
    return error;
}

esp_err_t config_store_stage(const cJSON *candidate, const char *expected_digest,
                             uint32_t base_generation, char *reason, size_t reason_size)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const config_platform_t pending_platform =
        atomic_load(&s_platform) == CONFIG_PLATFORM_UNSELECTED
            ? CONFIG_PLATFORM_CUSTOM : CONFIG_PLATFORM_UNSELECTED;
    esp_err_t error = stage_locked(candidate, expected_digest, base_generation, true, false,
                                   pending_platform, 0,
                                   reason, reason_size);
    xSemaphoreGive(s_lock);
    return error;
}

bool config_store_poll(bool inputs_neutral)
{
    if (!inputs_neutral || !atomic_load(&s_has_pending) ||
        atomic_load(&s_activation_failed)) {
        return false;
    }
    const TickType_t now = xTaskGetTickCount();
    if (s_activation_retry_at != 0 &&
        (int32_t)(now - s_activation_retry_at) < 0) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_pending_root == NULL) {
        xSemaphoreGive(s_lock);
        return false;
    }
    esp_err_t error = nvs_set_u8(s_nvs, CONFIG_KEY_ACTIVE, s_pending_slot);
    if (error == ESP_OK && s_pending_factory_reset) {
        error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM,
                           (uint8_t)CONFIG_PLATFORM_UNSELECTED);
    }
    if (error == ESP_OK && s_pending_factory_reset) {
        error = nvs_set_u8(s_nvs, CONFIG_KEY_POMODORO_MINUTES, 25);
    }
    if (error == ESP_OK && s_pending_factory_reset) {
        error = nvs_set_u8(s_nvs, CONFIG_KEY_STANDBY_MINUTES, 0);
    }
    if (error == ESP_OK && !s_pending_factory_reset &&
        s_pending_platform != CONFIG_PLATFORM_UNSELECTED) {
        error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM, (uint8_t)s_pending_platform);
    }
    if (error == ESP_OK) {
        error = persist_context_intent(&s_pending_meta);
    }
    char previous_ble_name[WMP_BLE_NAME_MAX_UTF8_BYTES + 1];
    bool ble_name_changed = false;
    if (error == ESP_OK && s_pending_factory_reset) {
        error = read_ble_name_locked(previous_ble_name);
        if (error == ESP_OK && strcmp(previous_ble_name, WMP_BLE_NAME_DEFAULT) != 0) {
            error = nvs_set_str(s_nvs, CONFIG_KEY_BLE_NAME, WMP_BLE_NAME_DEFAULT);
            ble_name_changed = error == ESP_OK;
        }
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_PENDING_PLATFORM);
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_FACTORY_PENDING);
    }
    if (error == ESP_OK) {
        error = erase_optional_key(CONFIG_KEY_PENDING);
    }
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error != ESP_OK) {
        if (ble_name_changed) {
            restore_ble_name_locked(previous_ble_name);
        }
        ++s_activation_failures;
        if (s_activation_failures >= 5) {
            atomic_store(&s_activation_failed, true);
        } else {
            const uint32_t delay_ms = 100U << (s_activation_failures - 1);
            s_activation_retry_at = now + pdMS_TO_TICKS(delay_ms);
        }
        xSemaphoreGive(s_lock);
        return false;
    }
    cJSON_Delete(s_active_root);
    free(s_active_json);
    s_active_root = s_pending_root;
    s_active_json = s_pending_json;
    s_active_meta = s_pending_meta;
    s_active_slot = s_pending_slot;
    s_pending_root = NULL;
    s_pending_json = NULL;
    const bool factory_reset_activated = s_pending_factory_reset;
    apply_context_intent(&s_pending_meta);
    if (factory_reset_activated) {
        atomic_store(&s_platform, CONFIG_PLATFORM_UNSELECTED);
    } else if (s_pending_platform != CONFIG_PLATFORM_UNSELECTED) {
        atomic_store(&s_platform, s_pending_platform);
    }
    s_pending_factory_reset = false;
    s_pending_platform = CONFIG_PLATFORM_UNSELECTED;
    s_activation_failures = 0;
    s_activation_retry_at = 0;
    atomic_store(&s_activation_failed, false);
    atomic_store(&s_has_pending, false);
    if (factory_reset_activated) {
        atomic_store(&s_factory_reset_activated, true);
    }
    memset(&s_pending_meta, 0, sizeof(s_pending_meta));
    xSemaphoreGive(s_lock);
    return true;
}

bool config_store_take_factory_reset_activated(void)
{
    return atomic_exchange(&s_factory_reset_activated, false);
}

bool config_store_has_pending(void)
{
    return atomic_load(&s_has_pending);
}

void config_store_get_status(config_store_status_t *status)
{
    if (status == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memset(status, 0, sizeof(*status));
    status->active_generation = s_active_meta.generation;
    snprintf(status->active_digest, sizeof(status->active_digest), "%s", s_active_meta.digest);
    status->pending = s_pending_root != NULL;
    status->activation_failed = atomic_load(&s_activation_failed);
    if (status->pending) {
        status->pending_generation = s_pending_meta.generation;
        snprintf(status->pending_digest, sizeof(status->pending_digest), "%s", s_pending_meta.digest);
    }
    xSemaphoreGive(s_lock);
}

config_platform_t config_store_get_platform(void)
{
    return (config_platform_t)atomic_load(&s_platform);
}

void config_store_set_platform_context(uint8_t context)
{
    if (context < CONFIG_PLATFORM_CONTEXT_COUNT) {
        atomic_store(&s_platform_context, context);
    }
}

config_platform_t config_store_get_context_platform(uint8_t context)
{
    return context < CONFIG_PLATFORM_CONTEXT_COUNT
        ? (config_platform_t)atomic_load(&s_context_platforms[context])
        : CONFIG_PLATFORM_UNSELECTED;
}

esp_err_t config_store_clear_context_platform(uint8_t context)
{
    if (context >= CONFIG_PLATFORM_CONTEXT_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_pending_root != NULL) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = erase_optional_key(PLATFORM_CONTEXT_KEYS[context]);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error == ESP_OK) {
        atomic_store(&s_context_platforms[context], CONFIG_PLATFORM_UNSELECTED);
    }
    xSemaphoreGive(s_lock);
    return error;
}

static const platform_profile_t *platform_profile(config_platform_t platform)
{
    for (size_t index = 0; index < sizeof(PLATFORM_PROFILES) / sizeof(PLATFORM_PROFILES[0]);
         ++index) {
        if (PLATFORM_PROFILES[index].platform == platform) {
            return &PLATFORM_PROFILES[index];
        }
    }
    return NULL;
}

static config_platform_t platform_for_profile_name(const char *name)
{
    for (size_t index = 0; index < sizeof(PLATFORM_PROFILES) / sizeof(PLATFORM_PROFILES[0]);
         ++index) {
        if (name != NULL && strcmp(name, PLATFORM_PROFILES[index].profile_name) == 0) {
            return PLATFORM_PROFILES[index].platform;
        }
    }
    return CONFIG_PLATFORM_UNSELECTED;
}

static esp_err_t select_profile_locked(uint8_t profile_id,
                                       config_platform_t selected_platform)
{
    if (s_pending_root != NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    const cJSON *selected_profile = NULL;
    cJSON *profile = NULL;
    cJSON_ArrayForEach(profile, field(s_active_root, "profiles")) {
        if (field(profile, "id")->valueint == profile_id) {
            selected_profile = profile;
            break;
        }
    }
    if (selected_profile == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    const uint8_t context_intent = selected_platform == CONFIG_PLATFORM_UNSELECTED
        ? 0 : (uint8_t)(atomic_load(&s_platform_context) + 1);
    if (selected_platform == CONFIG_PLATFORM_UNSELECTED) {
        selected_platform = platform_for_profile_name(
            field(selected_profile, "name")->valuestring);
    }
    if (field(s_active_root, "active_profile")->valueint == profile_id) {
        if (selected_platform == CONFIG_PLATFORM_UNSELECTED ||
            (selected_platform == (config_platform_t)atomic_load(&s_platform) &&
             (context_intent == 0 || selected_platform ==
                config_store_get_context_platform(context_intent - 1)))) {
            return ESP_OK;
        }
        esp_err_t error = nvs_set_u8(s_nvs, CONFIG_KEY_PLATFORM,
                                     (uint8_t)selected_platform);
        if (error == ESP_OK && context_intent != 0) {
            error = nvs_set_u8(s_nvs, PLATFORM_CONTEXT_KEYS[context_intent - 1],
                               (uint8_t)selected_platform);
        }
        if (error == ESP_OK) {
            error = nvs_commit(s_nvs);
        }
        if (error == ESP_OK) {
            atomic_store(&s_platform, selected_platform);
            if (context_intent != 0) {
                atomic_store(&s_context_platforms[context_intent - 1], selected_platform);
            }
        }
        return error;
    }
    cJSON *copy = cJSON_Duplicate(s_active_root, true);
    if (copy == NULL) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_SetIntValue(mutable_field(copy, "active_profile"), profile_id);
    char reason[96];
    const esp_err_t error = stage_locked(copy, NULL, s_active_meta.generation, true, false,
                                         selected_platform, context_intent, reason, sizeof(reason));
    cJSON_Delete(copy);
    return error;
}

esp_err_t config_store_select_platform(config_platform_t platform)
{
    if (platform != CONFIG_PLATFORM_MACOS &&
        platform != CONFIG_PLATFORM_WINDOWS_LINUX) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const platform_profile_t *mapping = platform_profile(platform);
    cJSON *profile = NULL;
    int profile_id = -1;
    cJSON_ArrayForEach(profile, field(s_active_root, "profiles")) {
        if (strcmp(field(profile, "name")->valuestring, mapping->profile_name) == 0) {
            profile_id = field(profile, "id")->valueint;
            break;
        }
    }
    if (profile_id < 0) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NOT_FOUND;
    }
    const esp_err_t error = select_profile_locked((uint8_t)profile_id, platform);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_get_ble_name(char *out, size_t capacity)
{
    if (out == NULL || capacity == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    char name[WMP_BLE_NAME_MAX_UTF8_BYTES + 1];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = read_ble_name_locked(name);
    if (error == ESP_OK) {
        const size_t needed = strlen(name) + 1;
        if (capacity < needed) {
            error = ESP_ERR_INVALID_SIZE;
        } else {
            memcpy(out, name, needed);
        }
    }
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_set_ble_name(const char *name)
{
    if (name == NULL || !ble_name_valid(name, strlen(name))) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (atomic_load(&s_has_pending) || atomic_load(&s_activation_failed)) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_STATE;
    }
    char previous[WMP_BLE_NAME_MAX_UTF8_BYTES + 1];
    esp_err_t error = read_ble_name_locked(previous);
    if (error == ESP_OK && strcmp(previous, name) != 0) {
        error = nvs_set_str(s_nvs, CONFIG_KEY_BLE_NAME, name);
        if (error == ESP_OK) {
            error = nvs_commit(s_nvs);
            if (error != ESP_OK) {
                restore_ble_name_locked(previous);
            }
        }
    }
    xSemaphoreGive(s_lock);
    return error;
}

uint8_t config_store_get_pomodoro_minutes(void)
{
    uint8_t minutes = 25;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t error =
        nvs_get_u8(s_nvs, CONFIG_KEY_POMODORO_MINUTES, &minutes);
    xSemaphoreGive(s_lock);
    return error == ESP_OK && minutes >= 1 && minutes <= 60 ? minutes : 25;
}

esp_err_t config_store_set_pomodoro_minutes(uint8_t minutes)
{
    if (minutes < 1 || minutes > 60) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint8_t saved_minutes = 0;
    const esp_err_t read_error =
        nvs_get_u8(s_nvs, CONFIG_KEY_POMODORO_MINUTES, &saved_minutes);
    if (read_error == ESP_OK && saved_minutes == minutes) {
        xSemaphoreGive(s_lock);
        return ESP_OK;
    }
    esp_err_t error =
        nvs_set_u8(s_nvs, CONFIG_KEY_POMODORO_MINUTES, minutes);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    return error;
}

static bool valid_standby_minutes(uint8_t minutes)
{
    return minutes == 0 || minutes == 5 || minutes == 15 || minutes == 30;
}

uint8_t config_store_get_standby_minutes(void)
{
    uint8_t minutes = 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t error =
        nvs_get_u8(s_nvs, CONFIG_KEY_STANDBY_MINUTES, &minutes);
    xSemaphoreGive(s_lock);
    return error == ESP_OK && valid_standby_minutes(minutes) ? minutes : 0;
}

esp_err_t config_store_set_standby_minutes(uint8_t minutes)
{
    if (!valid_standby_minutes(minutes)) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint8_t saved_minutes = 0;
    const esp_err_t read_error =
        nvs_get_u8(s_nvs, CONFIG_KEY_STANDBY_MINUTES, &saved_minutes);
    if (read_error == ESP_OK && saved_minutes == minutes) {
        xSemaphoreGive(s_lock);
        return ESP_OK;
    }
    esp_err_t error =
        nvs_set_u8(s_nvs, CONFIG_KEY_STANDBY_MINUTES, minutes);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    return error;
}

bool config_store_get_user_powered_on(void)
{
    uint8_t powered_on = 1;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t error =
        nvs_get_u8(s_nvs, CONFIG_KEY_USER_POWERED_ON, &powered_on);
    xSemaphoreGive(s_lock);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        /* No prior formal session: USB power alone must remain in standby. */
        return false;
    }
    return error != ESP_OK || powered_on != 0;
}

esp_err_t config_store_set_user_powered_on(bool powered_on)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint8_t saved = 0;
    const esp_err_t read_error =
        nvs_get_u8(s_nvs, CONFIG_KEY_USER_POWERED_ON, &saved);
    if (read_error == ESP_OK && saved == (uint8_t)powered_on) {
        xSemaphoreGive(s_lock);
        return ESP_OK;
    }
    esp_err_t error = nvs_set_u8(s_nvs, CONFIG_KEY_USER_POWERED_ON,
                                 powered_on ? 1u : 0u);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    return error;
}

static cJSON *parse_defaults(void)
{
    cJSON *defaults = cJSON_ParseWithLength(
        (const char *)default_config_start,
        (size_t)(default_config_end - default_config_start));
    bool changed = false;
    if (defaults == NULL || migrate_matrix12_v1_root(defaults, &changed) != ESP_OK) {
        cJSON_Delete(defaults);
        return NULL;
    }
    return defaults;
}

esp_err_t config_store_factory_default(uint32_t base_generation, const char *confirmation,
                                       char *reason, size_t reason_size)
{
    if (confirmation == NULL || strcmp(confirmation, CONFIG_FACTORY_CONFIRMATION) != 0) {
        set_reason(reason, reason_size, "confirmation must equal FACTORY_DEFAULT");
        return ESP_ERR_INVALID_ARG;
    }
    return config_store_stage_factory_default(base_generation, reason, reason_size);
}

esp_err_t config_store_stage_factory_default(uint32_t base_generation,
                                             char *reason, size_t reason_size)
{
    cJSON *defaults = parse_defaults();
    if (defaults == NULL) {
        return ESP_FAIL;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = stage_locked(defaults, NULL, base_generation, true, true,
                                   CONFIG_PLATFORM_UNSELECTED, 0,
                                   reason, reason_size);
    xSemaphoreGive(s_lock);
    cJSON_Delete(defaults);
    return error;
}

esp_err_t config_store_force_factory_default(void)
{
    cJSON *defaults = parse_defaults();
    if (defaults == NULL) {
        return ESP_FAIL;
    }
    char reason[96];
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_pending_root != NULL) {
        esp_err_t cancel_error = erase_optional_key(CONFIG_KEY_PENDING);
        if (cancel_error == ESP_OK) {
            cancel_error = erase_optional_key(CONFIG_KEY_FACTORY_PENDING);
        }
        if (cancel_error == ESP_OK) {
            cancel_error = erase_optional_key(CONFIG_KEY_PENDING_PLATFORM);
        }
        if (cancel_error == ESP_OK) {
            cancel_error = nvs_commit(s_nvs);
        }
        if (cancel_error != ESP_OK) {
            xSemaphoreGive(s_lock);
            cJSON_Delete(defaults);
            return cancel_error;
        }
        cJSON_Delete(s_pending_root);
        free(s_pending_json);
        s_pending_root = NULL;
        s_pending_json = NULL;
        atomic_store(&s_has_pending, false);
    }
    esp_err_t error = stage_locked(defaults, NULL, s_active_meta.generation, false, true,
                                   CONFIG_PLATFORM_UNSELECTED, 0,
                                   reason, sizeof(reason));
    xSemaphoreGive(s_lock);
    cJSON_Delete(defaults);
    if (error == ESP_OK) {
        if (!config_store_poll(true)) {
            return ESP_FAIL;
        }
    }
    return error;
}

static const cJSON *active_profile_locked(void)
{
    const int id = field(s_active_root, "active_profile")->valueint;
    cJSON *profile = NULL;
    cJSON_ArrayForEach(profile, field(s_active_root, "profiles")) {
        if (field(profile, "id")->valueint == id) {
            return profile;
        }
    }
    return NULL;
}

static void parse_action(const cJSON *mapping, config_action_t *output)
{
    memset(output, 0, sizeof(*output));
    snprintf(output->short_name, sizeof(output->short_name), "%s", field(mapping, "short_name")->valuestring);
    const cJSON *action = field(mapping, "action");
    const char *type = field(action, "type")->valuestring;
    if (strcmp(type, "key") == 0) {
        output->type = CONFIG_ACTION_KEY;
        output->usage = (uint16_t)field(action, "usage")->valueint;
        const cJSON *modifiers = field(action, "modifiers");
        cJSON *modifier = NULL;
        cJSON_ArrayForEach(modifier, modifiers) {
            output->modifiers[output->modifier_count++] = (uint8_t)modifier->valueint;
        }
    } else if (strcmp(type, "consumer") == 0) {
        output->type = CONFIG_ACTION_CONSUMER;
        output->usage = (uint16_t)field(action, "usage")->valueint;
    } else if (strcmp(type, "mouse") == 0) {
        output->type = CONFIG_ACTION_MOUSE;
        output->mouse_buttons = (uint8_t)field(action, "button")->valueint;
        output->mouse_x = (int8_t)field(action, "x")->valueint;
        output->mouse_y = (int8_t)field(action, "y")->valueint;
        output->mouse_wheel = (int8_t)field(action, "wheel")->valueint;
        output->mouse_pan = (int8_t)field(action, "pan")->valueint;
    } else if (strcmp(type, "macro") == 0) {
        output->type = CONFIG_ACTION_MACRO;
        output->object_id = (uint8_t)field(action, "macro_id")->valueint;
    } else if (strcmp(type, "prompt") == 0) {
        output->type = CONFIG_ACTION_PROMPT;
        output->object_id = (uint8_t)field(action, "prompt_id")->valueint;
    } else if (strcmp(type, "profile") == 0) {
        output->type = CONFIG_ACTION_PROFILE;
        output->object_id = (uint8_t)field(action, "profile_id")->valueint;
    } else if (strcmp(type, "device") == 0) {
        output->type = CONFIG_ACTION_DEVICE;
        const char *name = field(action, "name")->valuestring;
        output->device_action = strcmp(name, "lighting_toggle") == 0 ? CONFIG_DEVICE_LIGHTING_TOGGLE :
                                strcmp(name, "haptic_toggle") == 0 ? CONFIG_DEVICE_HAPTIC_TOGGLE :
                                strcmp(name, "display_next") == 0 ? CONFIG_DEVICE_DISPLAY_NEXT :
                                CONFIG_DEVICE_MACRO_CANCEL;
    } else {
        output->type = CONFIG_ACTION_NONE;
    }
}

bool config_store_get_action(const char *control_id, config_action_t *action)
{
    if (control_id == NULL || action == NULL) {
        return false;
    }
    bool found = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const cJSON *profile = active_profile_locked();
    cJSON *mapping = NULL;
    cJSON_ArrayForEach(mapping, field(profile, "mappings")) {
        if (strcmp(field(mapping, "control_id")->valuestring, control_id) == 0) {
            parse_action(mapping, action);
            found = true;
            break;
        }
    }
    xSemaphoreGive(s_lock);
    return found;
}

bool config_store_get_macro_step(uint8_t macro_id, size_t index, config_macro_step_t *step,
                                 size_t *step_count)
{
    if (step_count == NULL) {
        return false;
    }
    bool found = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    cJSON *macro = NULL;
    cJSON_ArrayForEach(macro, field(s_active_root, "macros")) {
        if (field(macro, "id")->valueint != macro_id) {
            continue;
        }
        const cJSON *steps = field(macro, "steps");
        *step_count = (size_t)cJSON_GetArraySize(steps);
        if (step != NULL && index < *step_count) {
            const cJSON *source = cJSON_GetArrayItem(steps, (int)index);
            memset(step, 0, sizeof(*step));
            const char *op = field(source, "op")->valuestring;
            if (strcmp(op, "press") == 0 || strcmp(op, "release") == 0 || strcmp(op, "tap") == 0) {
                step->op = strcmp(op, "press") == 0 ? CONFIG_MACRO_PRESS :
                           strcmp(op, "release") == 0 ? CONFIG_MACRO_RELEASE : CONFIG_MACRO_TAP;
                step->usage = (uint16_t)field(source, "usage")->valueint;
            } else if (strcmp(op, "delay") == 0) {
                step->op = CONFIG_MACRO_DELAY;
                step->duration_ms = (uint16_t)field(source, "duration_ms")->valueint;
            } else {
                step->op = CONFIG_MACRO_TEXT;
                snprintf(step->text, sizeof(step->text), "%s", field(source, "text")->valuestring);
            }
        }
        found = true;
        break;
    }
    xSemaphoreGive(s_lock);
    return found;
}

static config_rgb_t read_rgb(const cJSON *value)
{
    return (config_rgb_t){
        .red = (uint8_t)field(value, "r")->valueint,
        .green = (uint8_t)field(value, "g")->valueint,
        .blue = (uint8_t)field(value, "b")->valueint,
    };
}

bool config_store_get_feedback(config_feedback_t *output)
{
    if (output == NULL) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    memset(output, 0, sizeof(*output));
    const cJSON *lighting = field(s_active_root, "lighting");
    const cJSON *haptic = field(s_active_root, "haptic");
    const cJSON *display = field(s_active_root, "display");
    const cJSON *joystick = field(s_active_root, "joystick");
    output->lighting_enabled = cJSON_IsTrue(field(lighting, "enabled"));
    output->lighting_brightness = (uint8_t)field(lighting, "brightness")->valueint;
    for (int index = 0; index < 8; ++index) {
        output->status_rgb[index] = read_rgb(cJSON_GetArrayItem(field(lighting, "status"), index));
    }
    for (size_t index = 0;
         index < board_under_key_rgb_count() &&
         index < CONFIG_STORE_MAX_UNDER_KEY_RGB; ++index) {
        output->under_key_rgb[index] = read_rgb(cJSON_GetArrayItem(field(lighting, "under_key"), index));
    }
    output->haptic_enabled = cJSON_IsTrue(field(haptic, "enabled"));
    output->haptic_strength = (uint8_t)field(haptic, "strength")->valueint;
    output->haptic_duration_ms = (uint16_t)field(haptic, "duration_ms")->valueint;
    output->haptic_on_press = cJSON_IsTrue(field(haptic, "on_press"));
    output->haptic_on_profile = cJSON_IsTrue(field(haptic, "on_profile"));
    /* Optional channel switches preserve existing documents without rewriting NVS. */
    output->haptic_on_encoder = !cJSON_IsFalse(field(haptic, "on_encoder"));
    output->haptic_on_joystick = !cJSON_IsFalse(field(haptic, "on_joystick"));
    output->haptic_on_task = !cJSON_IsFalse(field(haptic, "on_task"));
    output->display_brightness = (uint8_t)field(display, "brightness")->valueint;
    output->display_rotation = (uint16_t)field(display, "rotation")->valueint;
    output->display_show_control_hints = cJSON_IsTrue(field(display, "show_control_hints"));
    output->joystick_calibrated = cJSON_IsTrue(field(joystick, "calibrated"));
    output->joystick_filter = (uint8_t)field(joystick, "filter")->valueint;
    const cJSON *x = field(joystick, "x");
    const cJSON *y = field(joystick, "y");
    output->joystick_minimum_x = field(x, "minimum")->valueint;
    output->joystick_center_x = field(x, "center")->valueint;
    output->joystick_maximum_x = field(x, "maximum")->valueint;
    output->joystick_minimum_y = field(y, "minimum")->valueint;
    output->joystick_center_y = field(y, "center")->valueint;
    output->joystick_maximum_y = field(y, "maximum")->valueint;
    output->joystick_deadzone_x = field(x, "deadzone")->valueint;
    output->joystick_deadzone_y = field(y, "deadzone")->valueint;
    output->joystick_invert_x = cJSON_IsTrue(field(x, "invert"));
    output->joystick_invert_y = cJSON_IsTrue(field(y, "invert"));
    xSemaphoreGive(s_lock);
    return true;
}

bool config_store_get_active_profile(uint8_t *profile_id, char *name, size_t name_size)
{
    if (profile_id == NULL || name == NULL || name_size == 0) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const cJSON *profile = active_profile_locked();
    *profile_id = (uint8_t)field(profile, "id")->valueint;
    snprintf(name, name_size, "%s", field(profile, "name")->valuestring);
    xSemaphoreGive(s_lock);
    return true;
}

size_t config_store_get_profile_count(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const size_t count = (size_t)cJSON_GetArraySize(field(s_active_root, "profiles"));
    xSemaphoreGive(s_lock);
    return count;
}

bool config_store_get_profile_at(size_t index, uint8_t *profile_id, char *name, size_t name_size)
{
    if (profile_id == NULL && (name == NULL || name_size == 0)) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const cJSON *profile = cJSON_GetArrayItem(field(s_active_root, "profiles"), (int)index);
    if (profile == NULL) {
        xSemaphoreGive(s_lock);
        return false;
    }
    if (profile_id != NULL) {
        *profile_id = (uint8_t)field(profile, "id")->valueint;
    }
    if (name != NULL && name_size > 0) {
        snprintf(name, name_size, "%s", field(profile, "name")->valuestring);
    }
    xSemaphoreGive(s_lock);
    return true;
}

bool config_store_get_active_profile_index(size_t *index)
{
    if (index == NULL) {
        return false;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const int active_id = field(s_active_root, "active_profile")->valueint;
    const cJSON *profiles = field(s_active_root, "profiles");
    const int count = cJSON_GetArraySize(profiles);
    for (int profile_index = 0; profile_index < count; ++profile_index) {
        const cJSON *profile = cJSON_GetArrayItem(profiles, profile_index);
        if (field(profile, "id")->valueint == active_id) {
            *index = (size_t)profile_index;
            xSemaphoreGive(s_lock);
            return true;
        }
    }
    xSemaphoreGive(s_lock);
    return false;
}

size_t config_store_get_quick_preset_count(void)
{
    return CONFIG_QUICK_PRESET_COUNT;
}

const char *config_store_get_quick_preset_name(size_t index)
{
    return index < CONFIG_QUICK_PRESET_COUNT ? QUICK_PRESETS[index].name : "CUSTOM";
}

bool config_store_match_quick_preset(const char *control_id, size_t *index)
{
    if (control_id == NULL || index == NULL) {
        return false;
    }
    config_action_t action;
    if (!config_store_get_action(control_id, &action)) {
        return false;
    }
    for (size_t preset_index = 0; preset_index < CONFIG_QUICK_PRESET_COUNT; ++preset_index) {
        const quick_preset_t *preset = &QUICK_PRESETS[preset_index];
        if (action.type == preset->type && action.usage == preset->usage &&
            action.modifier_count == 0) {
            *index = preset_index;
            return true;
        }
    }
    return false;
}

static esp_err_t mutate_and_stage_locked(cJSON *copy)
{
    char reason[96];
    esp_err_t error = stage_locked(copy, NULL, s_active_meta.generation, true, false,
                                   CONFIG_PLATFORM_UNSELECTED, 0,
                                   reason, sizeof(reason));
    cJSON_Delete(copy);
    return error;
}

esp_err_t config_store_select_profile(uint8_t profile_id)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t error = select_profile_locked(profile_id, CONFIG_PLATFORM_UNSELECTED);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_select_next_profile(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const int active_id = field(s_active_root, "active_profile")->valueint;
    const cJSON *profiles = field(s_active_root, "profiles");
    const int count = cJSON_GetArraySize(profiles);
    if (count <= 1) {
        xSemaphoreGive(s_lock);
        return ESP_OK;
    }
    int next_id = active_id;
    for (int index = 0; index < count; ++index) {
        const cJSON *profile = cJSON_GetArrayItem(profiles, index);
        if (field(profile, "id")->valueint == active_id) {
            const cJSON *next = cJSON_GetArrayItem(profiles, (index + 1) % count);
            next_id = field(next, "id")->valueint;
            break;
        }
    }
    const esp_err_t error = select_profile_locked((uint8_t)next_id,
                                                  CONFIG_PLATFORM_UNSELECTED);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_apply_device_action(config_device_action_t action)
{
    if (action == CONFIG_DEVICE_MACRO_CANCEL) {
        return ESP_OK;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    cJSON *copy = cJSON_Duplicate(s_active_root, true);
    if (copy == NULL) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NO_MEM;
    }
    if (action == CONFIG_DEVICE_LIGHTING_TOGGLE) {
        cJSON *lighting = mutable_field(copy, "lighting");
        cJSON *enabled = mutable_field(lighting, "enabled");
        cJSON_ReplaceItemInObjectCaseSensitive(lighting, "enabled",
                                               cJSON_CreateBool(!cJSON_IsTrue(enabled)));
    } else if (action == CONFIG_DEVICE_HAPTIC_TOGGLE) {
        cJSON *haptic = mutable_field(copy, "haptic");
        cJSON *enabled = mutable_field(haptic, "enabled");
        cJSON_ReplaceItemInObjectCaseSensitive(haptic, "enabled",
                                               cJSON_CreateBool(!cJSON_IsTrue(enabled)));
    } else if (action == CONFIG_DEVICE_DISPLAY_NEXT) {
        cJSON *rotation = mutable_field(mutable_field(copy, "display"), "rotation");
        cJSON_SetIntValue(rotation, (rotation->valueint + 90) % 360);
    }
    esp_err_t error = mutate_and_stage_locked(copy);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_set_quick_preset(uint8_t key_index, size_t preset_index)
{
    if (key_index >= board_key_count() ||
        key_index >= CONFIG_STORE_MAX_UNDER_KEY_RGB ||
        preset_index >= CONFIG_QUICK_PRESET_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }
    board_control_t key_control = BOARD_CONTROL_KEY_1;
    if (!board_control_from_key_index(key_index, &key_control)) {
        return ESP_ERR_INVALID_ARG;
    }
    const char *control_id = board_control_id(key_control);
    if (control_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const quick_preset_t *preset = &QUICK_PRESETS[preset_index];
    cJSON *current_mapping = NULL;
    cJSON_ArrayForEach(current_mapping, field(active_profile_locked(), "mappings")) {
        if (strcmp(field(current_mapping, "control_id")->valuestring, control_id) == 0) {
            config_action_t current_action;
            parse_action(current_mapping, &current_action);
            if (current_action.type == preset->type &&
                current_action.usage == preset->usage &&
                current_action.modifier_count == 0 &&
                strcmp(field(current_mapping, "short_name")->valuestring,
                       preset->name) == 0) {
                xSemaphoreGive(s_lock);
                return ESP_OK;
            }
            break;
        }
    }
    cJSON *copy = cJSON_Duplicate(s_active_root, true);
    if (copy == NULL) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NO_MEM;
    }
    const int active_id = field(copy, "active_profile")->valueint;
    cJSON *active_profile = NULL;
    cJSON *profile = NULL;
    cJSON_ArrayForEach(profile, field(copy, "profiles")) {
        if (field(profile, "id")->valueint == active_id) {
            active_profile = profile;
            break;
        }
    }
    cJSON *target = NULL;
    cJSON *mapping = NULL;
    if (active_profile != NULL) {
        cJSON_ArrayForEach(mapping, field(active_profile, "mappings")) {
            if (strcmp(field(mapping, "control_id")->valuestring, control_id) == 0) {
                target = mapping;
                break;
            }
        }
    }
    if (target == NULL) {
        cJSON_Delete(copy);
        xSemaphoreGive(s_lock);
        return ESP_ERR_NOT_FOUND;
    }
    cJSON *action = cJSON_CreateObject();
    if (action == NULL) {
        cJSON_Delete(copy);
        xSemaphoreGive(s_lock);
        return ESP_ERR_NO_MEM;
    }
    if (preset->type == CONFIG_ACTION_NONE) {
        cJSON_AddStringToObject(action, "type", "none");
    } else {
        cJSON_AddStringToObject(action, "type",
                               preset->type == CONFIG_ACTION_KEY ? "key" : "consumer");
        cJSON_AddNumberToObject(action, "usage", preset->usage);
        if (preset->type == CONFIG_ACTION_KEY) {
            cJSON_AddItemToObject(action, "modifiers", cJSON_CreateArray());
        }
    }
    cJSON_ReplaceItemInObjectCaseSensitive(target, "short_name",
                                           cJSON_CreateString(preset->name));
    cJSON_ReplaceItemInObjectCaseSensitive(target, "action", action);
    const esp_err_t error = mutate_and_stage_locked(copy);
    xSemaphoreGive(s_lock);
    return error;
}

static esp_err_t set_feedback_level(const char *section_name, const char *value_name,
                                    uint8_t percent)
{
    if (percent > 100) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const cJSON *current = field(s_active_root, section_name);
    const bool enabled = cJSON_IsTrue(field(current, "enabled"));
    const int current_percent = field(current, value_name)->valueint;
    if ((percent == 0 && !enabled) ||
        (percent > 0 && enabled && current_percent == percent)) {
        xSemaphoreGive(s_lock);
        return ESP_OK;
    }
    cJSON *copy = cJSON_Duplicate(s_active_root, true);
    if (copy == NULL) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_NO_MEM;
    }
    cJSON *section = mutable_field(copy, section_name);
    cJSON_ReplaceItemInObjectCaseSensitive(section, "enabled",
                                           cJSON_CreateBool(percent > 0));
    if (percent > 0) {
        cJSON_SetIntValue(mutable_field(section, value_name), percent);
    }
    const esp_err_t error = mutate_and_stage_locked(copy);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t config_store_set_lighting_level(uint8_t brightness_percent)
{
    return set_feedback_level("lighting", "brightness", brightness_percent);
}

esp_err_t config_store_set_haptic_level(uint8_t strength_percent)
{
    return set_feedback_level("haptic", "strength", strength_percent);
}

esp_err_t config_store_set_joystick_calibration(
    const config_joystick_calibration_t *calibration,
    uint32_t base_generation, char *reason, size_t reason_size)
{
    if (calibration == NULL ||
        calibration->minimum_x < 0 ||
        calibration->minimum_x >= calibration->center_x ||
        calibration->center_x >= calibration->maximum_x ||
        calibration->maximum_x > 4095 ||
        calibration->minimum_y < 0 ||
        calibration->minimum_y >= calibration->center_y ||
        calibration->center_y >= calibration->maximum_y ||
        calibration->maximum_y > 4095 ||
        calibration->deadzone_x < 0 || calibration->deadzone_x > 1024 ||
        calibration->deadzone_y < 0 || calibration->deadzone_y > 1024) {
        set_reason(reason, reason_size, "joystick calibration is invalid");
        return ESP_ERR_INVALID_ARG;
    }

    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (base_generation != s_active_meta.generation) {
        set_reason(reason, reason_size, "base_generation does not match");
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_VERSION;
    }
    cJSON *copy = cJSON_Duplicate(s_active_root, true);
    if (copy == NULL) {
        set_reason(reason, reason_size, "could not copy active config");
        xSemaphoreGive(s_lock);
        return ESP_ERR_NO_MEM;
    }
    cJSON *joystick = mutable_field(copy, "joystick");
    cJSON *x = mutable_field(joystick, "x");
    cJSON *y = mutable_field(joystick, "y");
    cJSON_ReplaceItemInObjectCaseSensitive(joystick, "calibrated",
                                           cJSON_CreateBool(true));
    cJSON_SetIntValue(mutable_field(x, "minimum"), calibration->minimum_x);
    cJSON_SetIntValue(mutable_field(x, "center"), calibration->center_x);
    cJSON_SetIntValue(mutable_field(x, "maximum"), calibration->maximum_x);
    cJSON_SetIntValue(mutable_field(x, "deadzone"), calibration->deadzone_x);
    cJSON_SetIntValue(mutable_field(y, "minimum"), calibration->minimum_y);
    cJSON_SetIntValue(mutable_field(y, "center"), calibration->center_y);
    cJSON_SetIntValue(mutable_field(y, "maximum"), calibration->maximum_y);
    cJSON_SetIntValue(mutable_field(y, "deadzone"), calibration->deadzone_y);
    const esp_err_t error = stage_locked(
        copy, NULL, base_generation, true, false,
        CONFIG_PLATFORM_UNSELECTED, 0, reason, reason_size);
    cJSON_Delete(copy);
    xSemaphoreGive(s_lock);
    return error;
}
