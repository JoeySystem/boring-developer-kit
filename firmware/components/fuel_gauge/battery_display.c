#include "battery_display.h"

uint8_t battery_display_percent(float raw)
{
    if (!(raw > 0.0f)) { /* also NaN */
        return 0;
    }
    float shown = raw;
    if (raw >= BATTERY_DISPLAY_FULL_PERCENT) {
        shown = 100.0f;
    } else if (raw > BATTERY_DISPLAY_KNEE_PERCENT) {
        shown = BATTERY_DISPLAY_KNEE_PERCENT +
                (raw - BATTERY_DISPLAY_KNEE_PERCENT) *
                    (100.0f - BATTERY_DISPLAY_KNEE_PERCENT) /
                    (BATTERY_DISPLAY_FULL_PERCENT - BATTERY_DISPLAY_KNEE_PERCENT);
    }
    int rounded = (int)(shown + 0.5f);
    if (rounded > 100) {
        rounded = 100;
    }
    return (uint8_t)rounded;
}
