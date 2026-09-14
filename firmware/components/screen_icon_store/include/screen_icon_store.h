#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define SCREEN_ICON_WIDTH 128u
#define SCREEN_ICON_HEIGHT 128u
#define SCREEN_ICON_TOTAL_BYTES 32768u

typedef struct {
    uint32_t revision;
    bool custom;
} screen_icon_metadata_t;

esp_err_t screen_icon_store_init(void);
bool screen_icon_store_ready(void);
void screen_icon_store_get_metadata(screen_icon_metadata_t *metadata);
/* Display polling only: never wait on an in-progress NVS transaction. Copies
 * pixels only when the caller's metadata changed and the new source is custom. */
bool screen_icon_store_try_snapshot(screen_icon_metadata_t *in_out_metadata,
                                    uint8_t *pixels, size_t length);
esp_err_t screen_icon_store_read(uint32_t revision, size_t offset,
                                 uint8_t *output, size_t length);
esp_err_t screen_icon_store_commit(uint32_t base_revision,
                                   const uint8_t *data, size_t length);
esp_err_t screen_icon_store_reset(uint32_t base_revision);
/* Complete Factory Default only; affects screen_icon, not prompts. */
esp_err_t screen_icon_store_erase_all(void);
