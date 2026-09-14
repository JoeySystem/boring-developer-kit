#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"
#include "esp_err.h"

typedef struct {
    esp_err_t (*send_ack)(uint32_t request_id, const char *json);
    void (*send_nack)(uint32_t request_id, const char *command, uint16_t code,
                      const char *name, const char *message);
} firmware_update_protocol_callbacks_t;

/** Add the shared firmware_update status object to GET_STATUS responses. */
void firmware_update_protocol_add_status(cJSON *parent);

/** Handle one FW_* command using the caller's replay-protected transport. */
bool firmware_update_protocol_handle(
    uint8_t message_type, uint32_t request_id, const cJSON *request,
    const firmware_update_protocol_callbacks_t *callbacks);
