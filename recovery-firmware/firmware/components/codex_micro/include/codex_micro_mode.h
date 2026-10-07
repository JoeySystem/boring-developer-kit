#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CODEX_MICRO_MODE_NORMAL = 0,
    CODEX_MICRO_MODE_CODEX,
    CODEX_MICRO_MODE_EDA,
    CODEX_MICRO_MODE_CLAUDE_CODE,
} codex_micro_mode_t;

/** Resolve the next volatile mode from supported features, not connection state. */
codex_micro_mode_t codex_micro_next_mode(codex_micro_mode_t current,
                                         bool codex_supported,
                                         bool eda_supported,
                                         bool claude_code_supported);

/** Stable lowercase value used by diagnostics and the configuration protocol. */
const char *codex_micro_mode_name(codex_micro_mode_t mode);

#ifdef __cplusplus
}
#endif
