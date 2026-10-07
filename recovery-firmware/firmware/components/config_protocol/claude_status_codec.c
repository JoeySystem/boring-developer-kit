#include "claude_status_codec.h"
#include <string.h>

static bool has_claude_source(const cJSON *request, int fields)
{
    const cJSON *source = cJSON_GetObjectItemCaseSensitive(request, "source");
    return cJSON_IsObject(request) && cJSON_GetArraySize(request) == fields &&
        cJSON_IsString(source) && strcmp(source->valuestring, "claude_code") == 0;
}

bool claude_status_parse_snapshot(const cJSON *request,
    claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT])
{
    const cJSON *states = cJSON_GetObjectItemCaseSensitive(request, "states");
    if (!has_claude_source(request, 2) || !cJSON_IsArray(states) ||
        cJSON_GetArraySize(states) != CLAUDE_STATUS_SLOT_COUNT) {
        return false;
    }
    claude_status_state_t parsed[CLAUDE_STATUS_SLOT_COUNT];
    for (unsigned i = 0; i < CLAUDE_STATUS_SLOT_COUNT; ++i) {
        const cJSON *state = cJSON_GetArrayItem(states, i);
        if (!cJSON_IsString(state)) {
            return false;
        }
        unsigned j;
        for (j = 0; j < CLAUDE_STATUS_COUNT; ++j) {
            if (strcmp(state->valuestring, claude_status_state_name(j)) == 0) {
                parsed[i] = (claude_status_state_t)j;
                break;
            }
        }
        if (j == CLAUDE_STATUS_COUNT) {
            return false;
        }
    }
    memcpy(slots, parsed, sizeof(parsed));
    return true;
}

bool claude_status_parse_clear(const cJSON *request)
{
    return has_claude_source(request, 1);
}
