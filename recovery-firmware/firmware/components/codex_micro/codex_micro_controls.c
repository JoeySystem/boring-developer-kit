#include "codex_micro_controls.h"

#ifdef ESP_PLATFORM
#include "sdkconfig.h"
#endif

static const codex_micro_key_mapping_t REV_A_KEY_MAPPINGS[] = {
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT07", CODEX_MICRO_AGENT_NONE, "APPROVE"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT08", CODEX_MICRO_AGENT_NONE, "DECLINE"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT10", CODEX_MICRO_AGENT_NONE, "DICTATE"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT12", CODEX_MICRO_AGENT_NONE, "SEND"},
    {CODEX_MICRO_KEY_STANDARD_HID, 41, NULL, CODEX_MICRO_AGENT_NONE, "STOP"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT06", CODEX_MICRO_AGENT_NONE, "FAST"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT09", CODEX_MICRO_AGENT_NONE, "FORK"},
};

static const codex_micro_key_mapping_t MATRIX12_KEY_MAPPINGS[] = {
    {CODEX_MICRO_KEY_VENDOR, 0, "AG00", 0, "AGENT 1"},
    {CODEX_MICRO_KEY_VENDOR, 0, "AG01", 1, "AGENT 2"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT09", CODEX_MICRO_AGENT_NONE, "FORK"},
    {CODEX_MICRO_KEY_VENDOR, 0, "AG02", 2, "AGENT 3"},
    {CODEX_MICRO_KEY_VENDOR, 0, "AG03", 3, "AGENT 4"},
    {CODEX_MICRO_KEY_VENDOR, 0, "AG04", 4, "AGENT 5"},
    {CODEX_MICRO_KEY_VENDOR, 0, "AG05", 5, "AGENT 6"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT10", CODEX_MICRO_AGENT_NONE, "DICTATE"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT07", CODEX_MICRO_AGENT_NONE, "APPROVE"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT08", CODEX_MICRO_AGENT_NONE, "REJECT"},
    {CODEX_MICRO_KEY_VENDOR, 0, "ACT12", CODEX_MICRO_AGENT_NONE, "SEND"},
    {CODEX_MICRO_KEY_STANDARD_HID, 41, NULL, CODEX_MICRO_AGENT_NONE, "STOP"},
};

codex_micro_key_mapping_t codex_micro_map_key_index(
    codex_micro_key_layout_t layout, size_t key_index)
{
    const codex_micro_key_mapping_t *mappings = REV_A_KEY_MAPPINGS;
    size_t mapping_count =
        sizeof(REV_A_KEY_MAPPINGS) / sizeof(REV_A_KEY_MAPPINGS[0]);
    if (layout == CODEX_MICRO_KEY_LAYOUT_MATRIX12) {
        mappings = MATRIX12_KEY_MAPPINGS;
        mapping_count =
            sizeof(MATRIX12_KEY_MAPPINGS) / sizeof(MATRIX12_KEY_MAPPINGS[0]);
    }
    if (key_index >= mapping_count) {
        return (codex_micro_key_mapping_t){0};
    }
    return mappings[key_index];
}

codex_micro_key_layout_t codex_micro_active_key_layout(void)
{
#if (defined(CONFIG_MACROPAD_BOARD_MATRIX12_V1) && CONFIG_MACROPAD_BOARD_MATRIX12_V1) || \
    (defined(CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2) && CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2)
    return CODEX_MICRO_KEY_LAYOUT_MATRIX12;
#else
    return CODEX_MICRO_KEY_LAYOUT_REV_A;
#endif
}
