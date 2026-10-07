#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define HOST_ACTION_POLL_AFTER_MS 100u
#define HOST_ACTION_LISTENER_TIMEOUT_MS 1500u

typedef struct {
    uint32_t event_id;
    uint8_t action_id;
    char task_token[33];
} host_action_event_t;

/* RAM-only USB events. No prompt storage or text is involved. */
esp_err_t host_action_events_init(void);
bool host_action_events_ready(void);
bool host_action_events_poll(host_action_event_t *event);
void host_action_events_reset(void);
esp_err_t host_action_events_queue(uint8_t action_id, const char *task_token);
