#include "claude_status.h"
#include "codex_micro.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "sdkconfig.h"

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static claude_status_model_t s_model;

bool claude_status_supported(void)
{
#if (CONFIG_MACROPAD_BOARD_MATRIX12_V1 || CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2) && CONFIG_CODEX_MICRO_BLE_ENABLED && CONFIG_CODEX_MICRO_USB_ENABLED
    return true;
#else
    return false;
#endif
}

static uint32_t now_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

void claude_status_set(const claude_status_state_t slots[CLAUDE_STATUS_SLOT_COUNT])
{
    const bool selected = codex_micro_mode() == CODEX_MICRO_MODE_CLAUDE_CODE;
    const uint32_t now = now_ms();
    portENTER_CRITICAL(&s_lock);
    claude_status_model_update(&s_model, now, slots, selected);
    portEXIT_CRITICAL(&s_lock);
}

void claude_status_clear(void)
{
    portENTER_CRITICAL(&s_lock);
    claude_status_model_clear(&s_model);
    portEXIT_CRITICAL(&s_lock);
}

void claude_status_get(claude_status_model_t *snapshot)
{
    const uint32_t now = now_ms();
    portENTER_CRITICAL(&s_lock);
    claude_status_model_expire(&s_model, now);
    *snapshot = s_model;
    portEXIT_CRITICAL(&s_lock);
}

codex_micro_attention_t claude_status_take_attention(bool selected)
{
    const uint32_t now = now_ms();
    portENTER_CRITICAL(&s_lock);
    const codex_micro_attention_t result =
        claude_status_model_take(&s_model, now, selected);
    portEXIT_CRITICAL(&s_lock);
    return result;
}
