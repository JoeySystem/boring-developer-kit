#include "desktop_agent_status_codec.h"

#include <math.h>
#include <string.h>

bool desktop_agent_is_source(const cJSON *request)
{
    const cJSON *source = cJSON_GetObjectItemCaseSensitive(request, "source");
    return cJSON_IsObject(request) && cJSON_IsString(source) &&
           strcmp(source->valuestring, "desktop") == 0;
}

static bool is_uint32(const cJSON *value)
{
    return cJSON_IsNumber(value) && isfinite(value->valuedouble) &&
           value->valuedouble >= 0 && value->valuedouble <= UINT32_MAX &&
           value->valuedouble == (double)(uint32_t)value->valuedouble;
}

bool desktop_agent_parse_snapshot(const cJSON *request,
    desktop_agent_state_t states[DESKTOP_AGENT_SLOT_COUNT],
    uint32_t completion_seq[DESKTOP_AGENT_SLOT_COUNT],
    bool *encoder_navigation)
{
    const cJSON *values = cJSON_GetObjectItemCaseSensitive(request, "states");
    const cJSON *counters =
        cJSON_GetObjectItemCaseSensitive(request, "completion_seq");
    const cJSON *navigation =
        cJSON_GetObjectItemCaseSensitive(request, "encoder_navigation");
    if (!desktop_agent_is_source(request) ||
        cJSON_GetArraySize(request) != (navigation != NULL ? 4 : 3) ||
        (navigation != NULL && !cJSON_IsBool(navigation)) ||
        !cJSON_IsArray(values) || !cJSON_IsArray(counters) ||
        cJSON_GetArraySize(values) != (int)DESKTOP_AGENT_SLOT_COUNT ||
        cJSON_GetArraySize(counters) != (int)DESKTOP_AGENT_SLOT_COUNT) {
        return false;
    }
    desktop_agent_state_t parsed[DESKTOP_AGENT_SLOT_COUNT];
    uint32_t sequence[DESKTOP_AGENT_SLOT_COUNT];
    for (unsigned i = 0; i < DESKTOP_AGENT_SLOT_COUNT; ++i) {
        const cJSON *value = cJSON_GetArrayItem(values, (int)i);
        const cJSON *counter = cJSON_GetArrayItem(counters, (int)i);
        if (!cJSON_IsString(value) || !is_uint32(counter) ||
            !desktop_agent_state_from_name(value->valuestring, &parsed[i])) {
            return false;
        }
        sequence[i] = (uint32_t)counter->valuedouble;
    }
    memcpy(states, parsed, sizeof(parsed));
    memcpy(completion_seq, sequence, sizeof(sequence));
    *encoder_navigation = cJSON_IsTrue(navigation);
    return true;
}

bool desktop_agent_parse_clear(const cJSON *request)
{
    return desktop_agent_is_source(request) && cJSON_GetArraySize(request) == 1;
}
