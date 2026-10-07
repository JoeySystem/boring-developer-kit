#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t hid_usage;
    uint8_t modifiers[2];
    uint8_t modifier_count;
    const char *short_name;
} claude_code_shortcut_t;

/** Resolve one fixed Claude Code shortcut for a zero-based Matrix12 key. */
bool claude_code_shortcut_for_key(bool macos, size_t key_index,
                                  claude_code_shortcut_t *shortcut);

/** Resolve Up, Down, Left, or Right terminal navigation (indices 0-3). */
bool claude_code_shortcut_for_joystick_direction(
    size_t direction_index, claude_code_shortcut_t *shortcut);

/** Resolve the joystick press to terminal completion (Tab). */
bool claude_code_shortcut_for_joystick_press(
    claude_code_shortcut_t *shortcut);

/** Resolve the encoder single press to prompt submission (Enter). */
bool claude_code_shortcut_for_encoder_press(
    claude_code_shortcut_t *shortcut);

#ifdef __cplusplus
}
#endif
