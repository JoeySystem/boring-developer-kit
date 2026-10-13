#include "host_action_events.h"

#include <stdatomic.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#define HOST_ACTION_QUEUE_DEPTH 8u

static QueueHandle_t s_events;
static atomic_bool s_listener_seen;
static atomic_uint_fast32_t s_listener_tick;
static atomic_uint_fast32_t s_next_event_id;

static bool listener_active(uint32_t now)
{
    return atomic_load(&s_listener_seen) &&
           (uint32_t)(now - atomic_load(&s_listener_tick)) <=
               pdMS_TO_TICKS(HOST_ACTION_LISTENER_TIMEOUT_MS);
}

esp_err_t host_action_events_init(void)
{
    s_events = xQueueCreate(HOST_ACTION_QUEUE_DEPTH, sizeof(host_action_event_t));
    if (s_events == NULL) return ESP_ERR_NO_MEM;
    atomic_store(&s_next_event_id, 1u);
    host_action_events_reset();
    return ESP_OK;
}

bool host_action_events_ready(void)
{
    return s_events != NULL;
}

void host_action_events_reset(void)
{
    atomic_store(&s_listener_seen, false);
    atomic_store(&s_listener_tick, 0u);
    if (s_events != NULL) xQueueReset(s_events);
}

bool host_action_events_poll(host_action_event_t *event)
{
    if (!host_action_events_ready()) return false;
    const uint32_t now = (uint32_t)xTaskGetTickCount();
    /* A resumed helper must not execute tasks left over from its old session. */
    if (!listener_active(now)) xQueueReset(s_events);
    atomic_store(&s_listener_tick, now);
    atomic_store(&s_listener_seen, true);
    return event != NULL && xQueueReceive(s_events, event, 0) == pdTRUE;
}

esp_err_t host_action_events_queue(uint8_t action_id, const char *task_token)
{
    if (action_id == 0u || task_token == NULL || strlen(task_token) != 32u)
        return ESP_ERR_INVALID_ARG;
    if (!host_action_events_ready() ||
        !listener_active((uint32_t)xTaskGetTickCount())) {
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t event_id = (uint32_t)atomic_fetch_add(&s_next_event_id, 1u);
    if (event_id == 0u)
        event_id = (uint32_t)atomic_fetch_add(&s_next_event_id, 1u);
    host_action_event_t event = {.event_id = event_id, .action_id = action_id};
    memcpy(event.task_token, task_token, sizeof(event.task_token));
    return xQueueSend(s_events, &event, 0) == pdTRUE ? ESP_OK : ESP_ERR_NO_MEM;
}
