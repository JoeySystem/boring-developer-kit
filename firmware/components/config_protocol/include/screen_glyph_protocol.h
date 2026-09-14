#pragma once
#include "screen_icon_protocol.h"
void screen_glyph_protocol_add_capability(cJSON *result, bool supported);
void screen_glyph_protocol_handle(uint8_t type, uint32_t request_id,
    const cJSON *request, bool supported, bool session_ready, bool busy,
    screen_icon_response_fn respond);
