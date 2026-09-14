#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t timeout_ms;
    uint32_t last_activity_ms;
    bool active;
} idle_standby_model_t;

void idle_standby_model_init(idle_standby_model_t *model,
                             uint8_t timeout_minutes,
                             uint32_t now_ms);
void idle_standby_model_set_timeout(idle_standby_model_t *model,
                                    uint8_t timeout_minutes,
                                    uint32_t now_ms);
void idle_standby_model_note_activity(idle_standby_model_t *model,
                                      uint32_t now_ms);
bool idle_standby_model_poll(idle_standby_model_t *model,
                             uint32_t now_ms,
                             bool blocked);
void idle_standby_model_wake(idle_standby_model_t *model, uint32_t now_ms);

#ifdef __cplusplus
}
#endif
