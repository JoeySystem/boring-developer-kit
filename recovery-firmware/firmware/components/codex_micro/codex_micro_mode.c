#include "codex_micro_mode.h"

codex_micro_mode_t codex_micro_next_mode(codex_micro_mode_t current,
                                         bool codex_supported,
                                         bool eda_supported,
                                         bool claude_code_supported)
{
    switch (current) {
    case CODEX_MICRO_MODE_NORMAL:
        if (codex_supported) {
            return CODEX_MICRO_MODE_CODEX;
        }
        return eda_supported ? CODEX_MICRO_MODE_EDA
            : claude_code_supported ? CODEX_MICRO_MODE_CLAUDE_CODE
                                    : CODEX_MICRO_MODE_NORMAL;
    case CODEX_MICRO_MODE_CODEX:
        return eda_supported ? CODEX_MICRO_MODE_EDA
            : claude_code_supported ? CODEX_MICRO_MODE_CLAUDE_CODE
                                    : CODEX_MICRO_MODE_NORMAL;
    case CODEX_MICRO_MODE_EDA:
        return claude_code_supported ? CODEX_MICRO_MODE_CLAUDE_CODE
                                     : CODEX_MICRO_MODE_NORMAL;
    case CODEX_MICRO_MODE_CLAUDE_CODE:
    default:
        return CODEX_MICRO_MODE_NORMAL;
    }
}

const char *codex_micro_mode_name(codex_micro_mode_t mode)
{
    switch (mode) {
    case CODEX_MICRO_MODE_CODEX:
        return "codex";
    case CODEX_MICRO_MODE_EDA:
        return "eda";
    case CODEX_MICRO_MODE_CLAUDE_CODE:
        return "claude_code";
    case CODEX_MICRO_MODE_NORMAL:
    default:
        return "normal";
    }
}
