#pragma once
#include "cJSON.h"
#include "claude_status_model.h"

bool claude_status_parse_snapshot(const cJSON *request,
    claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT]);
bool claude_status_parse_clear(const cJSON *request);
