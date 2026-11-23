#pragma once

/*
 * NVS partitions that cannot be opened:
 *
 * - ESP_ERR_NVS_NO_FREE_PAGES: the partition is full or was truncated.
 * - ESP_ERR_NVS_NEW_VERSION_FOUND: it was written by a newer NVS format,
 *   for example before a rollback to an older image.
 *
 * On a freshly updated image that has not been confirmed yet, the previous
 * image wrote this data and can still read it, so the update is rolled back.
 * On a confirmed image the partition is left untouched (review A2, Joey
 * 2026-11-10; it used to be erased): the caller runs without it (config
 * read-only on the built-in defaults, BLE off) and GET_STATUS reports it.
 * Clearing it is an explicit host or rescue action.
 */

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    STORAGE_RECOVERY_NONE = 0,
    STORAGE_RECOVERY_ROLLBACK,
    STORAGE_RECOVERY_KEEP,     /* confirmed image: report, never erase */
} storage_recovery_action_t;

/* Bits of storage_recovery_unusable_mask(). */
#define STORAGE_RECOVERY_PARTITION_BLE (1u << 0)    /* "nvs": bonds, slots */
#define STORAGE_RECOVERY_PARTITION_CONFIG (1u << 1) /* "config_nvs" */

/* Pure policy, exposed for host tests. */
storage_recovery_action_t storage_recovery_decide(esp_err_t init_error,
                                                  bool image_pending_verify);

/*
 * nvs_flash_init_partition() with the policy above. @partition_bit is
 * recorded in storage_recovery_unusable_mask() when the partition is left
 * unusable.
 */
esp_err_t storage_recovery_init_partition(const char *label,
                                          uint32_t partition_bit);

/* Explicit clear (host or rescue request only): erase and re-initialise. */
esp_err_t storage_recovery_clear_partition(const char *label);

/* Partitions left unusable since boot. */
uint32_t storage_recovery_unusable_mask(void);

/* True while the running image is a fresh update that has not been confirmed. */
bool storage_recovery_image_pending_verify(void);

#ifdef __cplusplus
}
#endif
