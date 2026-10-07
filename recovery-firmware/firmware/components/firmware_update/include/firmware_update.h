#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define FIRMWARE_UPDATE_SHA256_HEX_LENGTH 64u
#define FIRMWARE_UPDATE_MAX_CHUNK_BYTES 4096u
#define FIRMWARE_IMAGE_DESCRIPTOR_MAGIC 0x46504D57u
#define FIRMWARE_IMAGE_DESCRIPTOR_VERSION 1u
#define FIRMWARE_UPDATE_ERR_HARDWARE_MISMATCH ESP_ERR_INVALID_RESPONSE

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t descriptor_version;
    uint16_t descriptor_size;
    char product_id[32];
    char hardware_id[32];
} firmware_image_descriptor_t;

typedef enum {
    FIRMWARE_UPDATE_IDLE = 0,
    FIRMWARE_UPDATE_RECEIVING,
    FIRMWARE_UPDATE_FINALIZING,
    FIRMWARE_UPDATE_READY_TO_REBOOT,
    FIRMWARE_UPDATE_FAILED,
} firmware_update_state_t;

typedef enum {
    FIRMWARE_BOOT_STAGE_UNKNOWN = 0,
    FIRMWARE_BOOT_STAGE_SYSTEM_READY,
    FIRMWARE_BOOT_STAGE_APP_ENTRY,
    FIRMWARE_BOOT_STAGE_BOARD_READY,
    FIRMWARE_BOOT_STAGE_CONFIG_READY,
    FIRMWARE_BOOT_STAGE_ACTION_READY,
    FIRMWARE_BOOT_STAGE_UPDATE_READY,
    FIRMWARE_BOOT_STAGE_USB_READY,
    FIRMWARE_BOOT_STAGE_CODEX_READY,
    FIRMWARE_BOOT_STAGE_CONFIRMED,
    FIRMWARE_BOOT_STAGE_MAIN_LOOP,
} firmware_boot_stage_t;

typedef struct {
    firmware_update_state_t state;
    size_t expected_size;
    size_t received_size;
    size_t target_capacity;
    char expected_sha256[FIRMWARE_UPDATE_SHA256_HEX_LENGTH + 1];
    char expected_version[32];
    char running_version[32];
    char running_partition[17];
    char target_partition[17];
    bool rollback_pending;
    bool reboot_pending;
    uint32_t current_boot_sequence;
    firmware_boot_stage_t current_boot_stage;
    int32_t current_confirm_error;
    uint32_t previous_boot_sequence;
    firmware_boot_stage_t previous_boot_stage;
    int32_t previous_reset_reason;
    int32_t previous_confirm_error;
} firmware_update_status_t;

/** Preserve the previous boot stage across software resets and OTA rollback. */
void firmware_update_boot_diagnostics_start(void);

/** Record the latest successfully reached startup boundary. */
void firmware_update_boot_stage(firmware_boot_stage_t stage);

/** Initialize the in-application updater before the USB protocol starts. */
esp_err_t firmware_update_init(void);

/** Confirm a newly booted OTA image after all required services initialize. */
esp_err_t firmware_update_confirm_running(void);

esp_err_t firmware_update_begin(const char *product_id, const char *hardware_id,
                                const char *version, size_t image_size,
                                const char *sha256_hex, char *reason,
                                size_t reason_size);
esp_err_t firmware_update_write(size_t offset, const uint8_t *data, size_t length,
                                char *reason, size_t reason_size);
esp_err_t firmware_update_finish(char *reason, size_t reason_size);
esp_err_t firmware_update_abort(char *reason, size_t reason_size);
void firmware_update_get_status(firmware_update_status_t *status);
/** Cheap activity query for output policy; does not inspect flash or OTA metadata. */
bool firmware_update_is_active(void);
void firmware_update_get_progress(size_t *received_size, size_t *expected_size);

/** Restart only after the final CDC ACK has had time to leave the device. */
void firmware_update_poll(void);

#ifdef __cplusplus
}
#endif
