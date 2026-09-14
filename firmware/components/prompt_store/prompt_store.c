#include "prompt_store.h"

#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define PROMPT_PARTITION "prompt_nvs"
#define PROMPT_NAMESPACE "prompts"
#define PROMPT_EVENT_QUEUE_DEPTH 8u

static nvs_handle_t s_nvs;
static SemaphoreHandle_t s_lock;
static QueueHandle_t s_events;
static atomic_bool s_ready;
static atomic_bool s_listener_seen;
static atomic_uint_fast32_t s_next_event_id;
static atomic_uint_fast32_t s_listener_seen_tick;

static bool valid_prompt_id(uint8_t prompt_id)
{
    return prompt_id >= 1u && prompt_id <= PROMPT_STORE_SLOT_COUNT;
}

static void prompt_key(uint8_t prompt_id, char key[5])
{
    snprintf(key, 5, "p%02u", (unsigned)prompt_id);
}

esp_err_t prompt_store_init(void)
{
    atomic_store(&s_ready, false);
    esp_err_t error = nvs_flash_init_partition(PROMPT_PARTITION);
    if (error != ESP_OK) {
        return error;
    }
    s_lock = xSemaphoreCreateMutex();
    s_events = xQueueCreate(PROMPT_EVENT_QUEUE_DEPTH,
                            sizeof(prompt_store_event_t));
    if (s_lock == NULL || s_events == NULL) {
        if (s_lock != NULL) {
            vSemaphoreDelete(s_lock);
            s_lock = NULL;
        }
        if (s_events != NULL) {
            vQueueDelete(s_events);
            s_events = NULL;
        }
        return ESP_ERR_NO_MEM;
    }
    error = nvs_open_from_partition(PROMPT_PARTITION, PROMPT_NAMESPACE,
                                    NVS_READWRITE, &s_nvs);
    if (error != ESP_OK) {
        return error;
    }
    atomic_store(&s_next_event_id, 1u);
    atomic_store(&s_listener_seen, false);
    atomic_store(&s_listener_seen_tick, 0u);
    atomic_store(&s_ready, true);
    return ESP_OK;
}

bool prompt_store_ready(void)
{
    return atomic_load(&s_ready);
}

esp_err_t prompt_store_get(uint8_t prompt_id, prompt_store_prompt_t *prompt)
{
    if (!prompt_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!valid_prompt_id(prompt_id) || prompt == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    char key[5];
    prompt_key(prompt_id, key);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    size_t record_length = 0;
    esp_err_t error = nvs_get_blob(s_nvs, key, NULL, &record_length);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        error = ESP_ERR_NOT_FOUND;
    }
    uint8_t *record = NULL;
    if (error == ESP_OK &&
        (record_length < 4u || record_length > PROMPT_RECORD_MAX_BYTES)) {
        error = ESP_ERR_INVALID_SIZE;
    }
    if (error == ESP_OK) {
        record = malloc(record_length);
        if (record == NULL) {
            error = ESP_ERR_NO_MEM;
        }
    }
    if (error == ESP_OK) {
        error = nvs_get_blob(s_nvs, key, record, &record_length);
    }
    if (error == ESP_OK) {
        memset(prompt, 0, sizeof(*prompt));
        prompt->prompt_id = prompt_id;
        if (!prompt_record_decode(record, record_length,
                                  prompt->name, sizeof(prompt->name),
                                  prompt->body, sizeof(prompt->body),
                                  &prompt->body_length)) {
            error = ESP_ERR_INVALID_CRC;
        }
    }
    free(record);
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t prompt_store_set(uint8_t prompt_id, const char *name,
                           const char *body)
{
    if (!prompt_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!valid_prompt_id(prompt_id) || name == NULL || body == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *record = malloc(PROMPT_RECORD_MAX_BYTES);
    if (record == NULL) {
        return ESP_ERR_NO_MEM;
    }
    const size_t record_length = prompt_record_encode(
        name, body, record, PROMPT_RECORD_MAX_BYTES);
    if (record_length == 0) {
        free(record);
        return ESP_ERR_INVALID_ARG;
    }
    char key[5];
    prompt_key(prompt_id, key);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = nvs_set_blob(s_nvs, key, record, record_length);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    free(record);
    return error;
}

esp_err_t prompt_store_delete(uint8_t prompt_id)
{
    if (!prompt_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!valid_prompt_id(prompt_id)) {
        return ESP_ERR_INVALID_ARG;
    }
    char key[5];
    prompt_key(prompt_id, key);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = nvs_erase_key(s_nvs, key);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        error = ESP_ERR_NOT_FOUND;
    }
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    return error;
}

esp_err_t prompt_store_erase_all(void)
{
    if (!prompt_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    esp_err_t error = nvs_erase_all(s_nvs);
    if (error == ESP_OK) {
        error = nvs_commit(s_nvs);
    }
    xSemaphoreGive(s_lock);
    if (error == ESP_OK && s_events != NULL) {
        xQueueReset(s_events);
    }
    return error;
}

bool prompt_store_exists(uint8_t prompt_id)
{
    if (!prompt_store_ready() || !valid_prompt_id(prompt_id)) {
        return false;
    }
    char key[5];
    prompt_key(prompt_id, key);
    size_t record_length = 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    const esp_err_t error = nvs_get_blob(s_nvs, key, NULL, &record_length);
    xSemaphoreGive(s_lock);
    return error == ESP_OK && record_length >= 4u &&
           record_length <= PROMPT_RECORD_MAX_BYTES;
}

uint16_t prompt_store_populated_mask(void)
{
    uint16_t mask = 0;
    for (uint8_t prompt_id = 1; prompt_id <= PROMPT_STORE_SLOT_COUNT;
         ++prompt_id) {
        if (prompt_store_exists(prompt_id)) {
            mask |= (uint16_t)(1u << (prompt_id - 1u));
        }
    }
    return mask;
}

bool prompt_store_listener_poll(prompt_store_event_t *event)
{
    if (!prompt_store_ready()) {
        return false;
    }
    atomic_store(&s_listener_seen_tick, (uint32_t)xTaskGetTickCount());
    atomic_store(&s_listener_seen, true);
    return event != NULL &&
           xQueueReceive(s_events, event, 0) == pdTRUE;
}

void prompt_store_listener_reset(void)
{
    atomic_store(&s_listener_seen, false);
    atomic_store(&s_listener_seen_tick, 0u);
    if (s_events != NULL) {
        xQueueReset(s_events);
    }
}

esp_err_t prompt_store_queue_trigger(uint8_t prompt_id)
{
    if (!prompt_store_ready()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!valid_prompt_id(prompt_id)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!prompt_store_exists(prompt_id)) {
        return ESP_ERR_NOT_FOUND;
    }
    const uint32_t now = (uint32_t)xTaskGetTickCount();
    const uint32_t seen = atomic_load(&s_listener_seen_tick);
    if (!atomic_load(&s_listener_seen) ||
        (uint32_t)(now - seen) > pdMS_TO_TICKS(PROMPT_LISTENER_TIMEOUT_MS)) {
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t event_id = atomic_fetch_add(&s_next_event_id, 1u);
    if (event_id == 0u) {
        event_id = atomic_fetch_add(&s_next_event_id, 1u);
    }
    const prompt_store_event_t event = {
        .event_id = event_id,
        .prompt_id = prompt_id,
    };
    return xQueueSend(s_events, &event, 0) == pdTRUE
               ? ESP_OK : ESP_ERR_NO_MEM;
}
