#include "storage_recovery.h"

#include <stdatomic.h>

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

static const char *TAG = "storage_recovery";
static atomic_uint_least32_t s_unusable_mask;

bool storage_recovery_image_pending_verify(void)
{
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    return running != NULL &&
           esp_ota_get_state_partition(running, &state) == ESP_OK &&
           state == ESP_OTA_IMG_PENDING_VERIFY;
#else
    return false;
#endif
}

esp_err_t storage_recovery_init_partition(const char *label,
                                          uint32_t partition_bit)
{
    const esp_err_t error = nvs_flash_init_partition(label);
    switch (storage_recovery_decide(error, storage_recovery_image_pending_verify())) {
    case STORAGE_RECOVERY_NONE:
        return error;
    case STORAGE_RECOVERY_ROLLBACK:
        ESP_LOGE(TAG, "%s unreadable by this update (%s); rolling back",
                 label, esp_err_to_name(error));
        /* Returns only when no other valid image exists. */
        (void)esp_ota_mark_app_invalid_rollback_and_reboot();
        break;
    case STORAGE_RECOVERY_KEEP:
        break;
    }
    /* Review A2: never erased automatically; the caller runs without it. */
    ESP_LOGE(TAG, "%s unusable (%s); left untouched", label, esp_err_to_name(error));
    atomic_fetch_or(&s_unusable_mask, partition_bit);
    return error;
}

esp_err_t storage_recovery_clear_partition(const char *label)
{
    ESP_LOGW(TAG, "%s cleared on request", label);
    (void)nvs_flash_deinit_partition(label);
    const esp_err_t error = nvs_flash_erase_partition(label);
    return error == ESP_OK ? nvs_flash_init_partition(label) : error;
}

uint32_t storage_recovery_unusable_mask(void)
{
    return atomic_load(&s_unusable_mask);
}
