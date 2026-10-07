#include "battery_service.h"

#include <stddef.h>

static bool s_valid;
static uint8_t s_level;
static battery_service_publisher_t s_publisher;
static void *s_publisher_context;

void battery_service_update(uint8_t level)
{
    if (level > 100) {
        level = 100;
    }
    const bool changed = !s_valid || s_level != level;
    s_level = level;
    s_valid = true;
    if (changed && s_publisher != NULL) {
        (void)s_publisher(s_level, s_publisher_context);
    }
}

bool battery_service_latest(uint8_t *level)
{
    if (level == NULL || !s_valid) {
        return false;
    }
    *level = s_level;
    return true;
}

void battery_service_set_publisher(battery_service_publisher_t publisher,
                                   void *context)
{
    s_publisher = publisher;
    s_publisher_context = context;
    if (s_valid && s_publisher != NULL) {
        (void)s_publisher(s_level, s_publisher_context);
    }
}
