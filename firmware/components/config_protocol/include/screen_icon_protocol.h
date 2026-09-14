#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"

/* All methods except active() are called under the existing transport mutex. */
typedef void (*screen_icon_response_fn)(uint8_t type, uint32_t request_id,
                                        const char *json);

void screen_icon_protocol_add_capability(cJSON *result, bool supported);
void screen_icon_protocol_handle(uint8_t type, uint32_t request_id,
                                const cJSON *request, uint32_t now_ms,
                                bool supported, bool session_ready,
                                bool maintenance_busy,
                                screen_icon_response_fn respond);
void screen_icon_protocol_reset_session(void);
void screen_icon_protocol_poll(uint32_t now_ms);
bool screen_icon_protocol_active(void);
const char *screen_icon_protocol_conflicting_command(uint8_t type);
