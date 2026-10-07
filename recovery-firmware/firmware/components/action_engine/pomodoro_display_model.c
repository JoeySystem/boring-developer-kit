#include "pomodoro_display_model.h"

#include <stdio.h>
#include <string.h>

static const char *short_action_hint(const char *hint)
{
    static const char timer_prefix[] = "TIMER ";
    if (hint != NULL &&
        strncmp(hint, timer_prefix, sizeof(timer_prefix) - 1) == 0) {
        return hint + sizeof(timer_prefix) - 1;
    }
    return hint;
}

void pomodoro_display_format_home_hint(char *output, size_t capacity,
                                       pomodoro_state_t state,
                                       uint32_t remaining_seconds,
                                       bool timer_done,
                                       const char *action_hint,
                                       const char *fallback_hint)
{
    if (output == NULL || capacity == 0) {
        return;
    }

    const unsigned minutes = (unsigned)(remaining_seconds / 60U);
    const unsigned seconds = (unsigned)(remaining_seconds % 60U);
    const char *short_hint = short_action_hint(action_hint);

    if (state == POMODORO_RUNNING) {
        if (short_hint != NULL) {
            (void)snprintf(output, capacity, "%02u:%02u %s", minutes, seconds,
                           short_hint);
        } else {
            (void)snprintf(output, capacity, "TIMER %02u:%02u", minutes,
                           seconds);
        }
        return;
    }

    if (state == POMODORO_PAUSED) {
        if (short_hint == NULL || strcmp(short_hint, "PAUSED") == 0) {
            (void)snprintf(output, capacity, "PAUSED %02u:%02u", minutes,
                           seconds);
        } else {
            (void)snprintf(output, capacity, "P %02u:%02u %s", minutes,
                           seconds, short_hint);
        }
        return;
    }

    const char *resolved = action_hint;
    if (resolved == NULL && timer_done) {
        resolved = "TIMER DONE";
    }
    if (resolved == NULL) {
        resolved = fallback_hint != NULL ? fallback_hint : "";
    }
    (void)snprintf(output, capacity, "%s", resolved);
}
