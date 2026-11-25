#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"
#include "esp_err.h"
#include "firmware_update.h"

typedef struct {
    esp_err_t (*send_ack)(uint32_t request_id, const char *json);
    void (*send_nack)(uint32_t request_id, const char *command, uint16_t code,
                      const char *name, const char *message);
} firmware_update_protocol_callbacks_t;

/** Stable wire name for FW_STATUS.confirm_source. */
const char *firmware_update_protocol_confirm_source_name(
    firmware_confirm_source_t source);

/** Add the shared firmware_update status object to GET_STATUS responses. */
void firmware_update_protocol_add_status(cJSON *parent);
/* Adds fields to FW_STATUS.result.firmware_update (stability item 4). */
typedef void (*firmware_update_protocol_status_extension_t)(cJSON *update);
void firmware_update_protocol_set_status_extension(
    firmware_update_protocol_status_extension_t extension);

/** Handle one FW_* command using the caller's replay-protected transport. */
bool firmware_update_protocol_handle(
    uint8_t message_type, uint32_t request_id, const cJSON *request,
    const firmware_update_protocol_callbacks_t *callbacks);
