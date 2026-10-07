#include "firmware_update.h"

#include "protocol_contract.h"
#include "sdkconfig.h"

#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2
#define FIRMWARE_HARDWARE_ID "WMP-S3-MATRIX12-POWER-V2"
#elif CONFIG_MACROPAD_BOARD_MATRIX12_V1
#define FIRMWARE_HARDWARE_ID "WMP-S3-MATRIX12-V1"
#elif CONFIG_MACROPAD_BOARD_TARGET_REV_A
#define FIRMWARE_HARDWARE_ID "WMP-S3-REV-A"
#else
/* Devkit OTA is disabled and must never be accepted by a Rev-A product. */
#define FIRMWARE_HARDWARE_ID "WMP-S3-DEVKIT"
#endif

/*
 * ESP-IDF places this directly after esp_app_desc_t at a fixed image offset.
 * The running firmware checks it before accepting the first OTA data chunk, so
 * a valid Rev A image cannot be installed on Matrix12 (or vice versa). The
 * generic devkit target rejects in-application updates before this check.
 */
const __attribute__((section(".rodata_custom_desc")))
    firmware_image_descriptor_t custom_app_desc = {
        .magic = FIRMWARE_IMAGE_DESCRIPTOR_MAGIC,
        .descriptor_version = FIRMWARE_IMAGE_DESCRIPTOR_VERSION,
        .descriptor_size = sizeof(firmware_image_descriptor_t),
        .product_id = WMP_PRODUCT_ID,
        .hardware_id = FIRMWARE_HARDWARE_ID,
};
