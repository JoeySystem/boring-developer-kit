#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "prompt_record.h"

#define PROMPT_STORE_TOTAL_BODY_MAX_BYTES \
    (PROMPT_STORE_SLOT_COUNT * PROMPT_STORE_BODY_MAX_BYTES)
#define PROMPT_TRIGGER_POLL_AFTER_MS 100u
#define PROMPT_LISTENER_TIMEOUT_MS 1500u

typedef struct {
    uint8_t prompt_id;
    char name[PROMPT_STORE_NAME_MAX_BYTES + 1];
    char body[PROMPT_STORE_BODY_MAX_BYTES + 1];
    size_t body_length;
} prompt_store_prompt_t;

typedef struct {
    uint32_t event_id;
    uint8_t prompt_id;
} prompt_store_event_t;

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t prompt_store_init(void);
bool prompt_store_ready(void);
esp_err_t prompt_store_get(uint8_t prompt_id, prompt_store_prompt_t *prompt);
esp_err_t prompt_store_set(uint8_t prompt_id, const char *name,
                           const char *body);
esp_err_t prompt_store_delete(uint8_t prompt_id);
esp_err_t prompt_store_erase_all(void);
bool prompt_store_exists(uint8_t prompt_id);
uint16_t prompt_store_populated_mask(void);

/** Mark the USB prompt helper as alive and return the oldest queued trigger. */
bool prompt_store_listener_poll(prompt_store_event_t *event);

/** Mark the host helper offline and discard events that were not delivered. */
void prompt_store_listener_reset(void);

/** Queue one trigger only when the prompt exists and the helper is active. */
esp_err_t prompt_store_queue_trigger(uint8_t prompt_id);

#ifdef __cplusplus
}
#endif
