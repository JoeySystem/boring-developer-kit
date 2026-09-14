#include "screen_icon_store.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_attr.h"
#else
#define EXT_RAM_BSS_ATTR
#endif

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"

#define ICON_PARTITION "prompt_nvs"
#define ICON_NAMESPACE "screen_icon"
#define ICON_SELECTOR_KEY "active"
#define ICON_SELECTOR_TAG UINT32_C(0x53490000)

static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static atomic_bool s_ready;
static screen_icon_metadata_t s_metadata;
static uint8_t s_slot;
static bool s_selector_exists;
static uint64_t s_selector;
/* Readers copy under the mutex: no flash reads or dangling image pointers. */
static EXT_RAM_BSS_ATTR uint8_t s_pixels[SCREEN_ICON_TOTAL_BYTES];

static const char *slot_key(uint8_t slot)
{
    return slot == 0u ? "image0" : "image1";
}

static uint64_t selector_value(uint32_t revision, bool custom, uint8_t slot)
{
    return ((uint64_t)revision << 32) | ICON_SELECTOR_TAG |
           (custom ? 1u : 0u) | ((uint32_t)slot << 1);
}

esp_err_t screen_icon_store_init(void)
{
    atomic_store(&s_ready, false);
    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
        if (s_lock == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_nvs != 0) {
        nvs_close(s_nvs);
        s_nvs = 0;
    }
    memset(&s_metadata, 0, sizeof(s_metadata));
    s_slot = 0;
    s_selector_exists = false;
    s_selector = 0;
    esp_err_t error = nvs_flash_init_partition(ICON_PARTITION);
    if (error == ESP_OK) {
        error = nvs_open_from_partition(ICON_PARTITION, ICON_NAMESPACE,
                                        NVS_READWRITE, &s_nvs);
    }
    if (error == ESP_OK) {
        uint64_t selector = 0;
        const esp_err_t selector_error = nvs_get_u64(s_nvs, ICON_SELECTOR_KEY,
                                                    &selector);
        if (selector_error == ESP_OK &&
            ((uint32_t)selector & ~UINT32_C(3)) == ICON_SELECTOR_TAG) {
            s_selector_exists = true;
            s_selector = selector;
            s_metadata.revision = (uint32_t)(selector >> 32);
            s_slot = (uint8_t)((selector >> 1) & 1u);
            if ((selector & 1u) != 0) {
                size_t length = 0;
                if (nvs_get_blob(s_nvs, slot_key(s_slot), NULL, &length) == ESP_OK &&
                    length == sizeof(s_pixels) &&
                    nvs_get_blob(s_nvs, slot_key(s_slot), s_pixels, &length) == ESP_OK &&
                    length == sizeof(s_pixels)) {
                    s_metadata.custom = true;
                }
            }
        }
        /* Missing/invalid image falls back to the built-in icon. Never erase
         * this shared partition, including on NO_FREE_PAGES or read errors. */
        atomic_store(&s_ready, true);
    }
    xSemaphoreGive(s_lock);
    return error;
}

bool screen_icon_store_ready(void)
{
    return atomic_load(&s_ready);
}

void screen_icon_store_get_metadata(screen_icon_metadata_t *metadata)
{
    if (metadata == NULL) {
        return;
    }
    memset(metadata, 0, sizeof(*metadata));
    if (s_lock != NULL) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        *metadata = s_metadata;
        xSemaphoreGive(s_lock);
    }
}

bool screen_icon_store_try_snapshot(screen_icon_metadata_t *in_out_metadata,
                                    uint8_t *pixels, size_t length)
{
    if (!screen_icon_store_ready() || in_out_metadata == NULL || pixels == NULL ||
        length != SCREEN_ICON_TOTAL_BYTES || s_lock == NULL ||
        xSemaphoreTake(s_lock, 0) != pdTRUE) {
        return false;
    }
    const bool changed = in_out_metadata->revision != s_metadata.revision ||
                         in_out_metadata->custom != s_metadata.custom;
    if (changed && s_metadata.custom) {
        memcpy(pixels, s_pixels, sizeof(s_pixels));
    }
    *in_out_metadata = s_metadata;
    xSemaphoreGive(s_lock);
    return true;
}

esp_err_t screen_icon_store_read(uint32_t revision, size_t offset,
                                 uint8_t *output, size_t length)
{
    if (!screen_icon_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (output == NULL || length == 0 || offset > SCREEN_ICON_TOTAL_BYTES ||
        length > SCREEN_ICON_TOTAL_BYTES - offset) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = ESP_OK;
    if (revision != s_metadata.revision) {
        error = ESP_ERR_INVALID_VERSION;
    } else if (!s_metadata.custom) {
        error = ESP_ERR_NOT_FOUND;
    } else {
        memcpy(output, s_pixels + offset, length);
    }
    xSemaphoreGive(s_lock);
    return error;
}

/* The selector is one atomic NVS scalar, not a set of independently updated
 * keys. IDF 6.0.2 writes at set_u64; commit is a valid-handle no-op. Keep the
 * explicit commit for the public NVS API and restore the previous selection
 * if it reports an error. The image buffers never change on a failed write. */
static esp_err_t select_record(uint64_t next)
{
    esp_err_t error = nvs_set_u64(s_nvs, ICON_SELECTOR_KEY, next);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error != ESP_OK) {
        esp_err_t restore = s_selector_exists
            ? nvs_set_u64(s_nvs, ICON_SELECTOR_KEY, s_selector)
            : nvs_erase_key(s_nvs, ICON_SELECTOR_KEY);
        if (restore == ESP_ERR_NVS_NOT_FOUND && !s_selector_exists) {
            restore = ESP_OK;
        }
        if (restore == ESP_OK) {
            restore = nvs_commit(s_nvs);
        }
        if (restore != ESP_OK) {
            /* Do not acknowledge subsequent writes after an unknown
             * persistent outcome. Reinitialization reads actual NVS. */
            atomic_store(&s_ready, false);
        }
    }
    if (error == ESP_OK) {
        s_selector = next;
        s_selector_exists = true;
    }
    return error;
}

static esp_err_t check_revision(uint32_t base_revision)
{
    if (base_revision != s_metadata.revision) {
        return ESP_ERR_INVALID_VERSION;
    }
    return base_revision == UINT32_MAX ? ESP_ERR_INVALID_STATE : ESP_OK;
}

esp_err_t screen_icon_store_commit(uint32_t base_revision,
                                   const uint8_t *data, size_t length)
{
    if (!screen_icon_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (data == NULL || length != SCREEN_ICON_TOTAL_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *readback = malloc(SCREEN_ICON_TOTAL_BYTES);
    if (readback == NULL) {
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = check_revision(base_revision);
    const uint8_t next_slot = s_slot ^ 1u;
    if (error == ESP_OK) {
        error = nvs_set_blob(s_nvs, slot_key(next_slot), data, length);
    }
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error == ESP_OK) {
        size_t read_length = SCREEN_ICON_TOTAL_BYTES;
        error = nvs_get_blob(s_nvs, slot_key(next_slot), readback, &read_length);
        if (error == ESP_OK && (read_length != length ||
                                memcmp(readback, data, length) != 0)) {
            error = ESP_ERR_INVALID_RESPONSE;
        }
    }
    if (error == ESP_OK) {
        error = select_record(selector_value(base_revision + 1u, true, next_slot));
    }
    if (error == ESP_OK) {
        memcpy(s_pixels, data, length);
        s_slot = next_slot;
        s_metadata.revision = base_revision + 1u;
        s_metadata.custom = true;
    }
    xSemaphoreGive(s_lock);
    free(readback);
    return error;
}

esp_err_t screen_icon_store_reset(uint32_t base_revision)
{
    if (!screen_icon_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = check_revision(base_revision);
    if (error == ESP_OK) {
        error = select_record(selector_value(base_revision + 1u, false, s_slot));
    }
    if (error == ESP_OK) {
        s_metadata.revision = base_revision + 1u;
        s_metadata.custom = false;
    }
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t screen_icon_store_erase_all(void)
{
    if (!screen_icon_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = nvs_erase_all(s_nvs);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    if (error == ESP_OK) {
        memset(&s_metadata, 0, sizeof(s_metadata));
        s_slot = 0;
        s_selector_exists = false;
        s_selector = 0;
    }
    xSemaphoreGive(s_lock);
    return error;
}
