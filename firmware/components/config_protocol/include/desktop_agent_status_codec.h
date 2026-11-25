#pragma once

/* Strict SET/CLEAR_AGENT_STATUS parsing for source "desktop". */

#include <stdbool.h>
#include <stdint.h>

#include "cJSON.h"
#include "desktop_agent_status_model.h"

bool desktop_agent_is_source(const cJSON *request);
/* Exactly source, states[6], completion_seq[6] and optionally a boolean
 * encoder_navigation; outputs are written only on success. */
bool desktop_agent_parse_snapshot(const cJSON *request,
    desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT],
    bool *encoder_navigation);
bool desktop_agent_parse_clear(const cJSON *request);
