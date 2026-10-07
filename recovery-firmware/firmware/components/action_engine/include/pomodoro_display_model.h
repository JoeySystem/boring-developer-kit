#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "pomodoro_model.h"

/**
 * Format the home-page hint without allowing transient control feedback to
 * hide an active timer.
 *
 * The countdown is placed first when an action hint is present so it remains
 * visible even if the action text must be truncated to fit the display.
 */
void pomodoro_display_format_home_hint(char *output, size_t capacity,
                                       pomodoro_state_t state,
                                       uint32_t remaining_seconds,
                                       bool timer_done,
                                       const char *action_hint,
                                       const char *fallback_hint);
