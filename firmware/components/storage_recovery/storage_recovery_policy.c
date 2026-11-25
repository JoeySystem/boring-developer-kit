#include "storage_recovery.h"

#include "nvs_flash.h"

storage_recovery_action_t storage_recovery_decide(esp_err_t init_error,
                                                  bool image_pending_verify)
{
    if (init_error != ESP_ERR_NVS_NO_FREE_PAGES &&
        init_error != ESP_ERR_NVS_NEW_VERSION_FOUND) {
        return STORAGE_RECOVERY_NONE;
    }
    return image_pending_verify ? STORAGE_RECOVERY_ROLLBACK
                                : STORAGE_RECOVERY_KEEP;
}
