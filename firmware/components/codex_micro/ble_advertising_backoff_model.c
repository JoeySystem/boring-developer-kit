#include "ble_advertising_backoff_model.h"

uint32_t ble_adv_backoff_delay_ms(uint32_t consecutive)
{
    if (consecutive < BLE_ADV_BACKOFF_FAST_ATTEMPTS) {
        return BLE_ADV_BACKOFF_FAST_MS;
    }
    switch (consecutive - BLE_ADV_BACKOFF_FAST_ATTEMPTS) {
    case 0:
        return 10000u;
    case 1:
        return 30000u;
    default:
        return BLE_ADV_BACKOFF_MAX_MS;
    }
}

void ble_adv_backoff_reset(ble_adv_backoff_t *backoff)
{
    if (backoff != 0) {
        backoff->consecutive = 0;
    }
}

bool ble_adv_backoff_take(ble_adv_backoff_t *backoff, uint32_t now_ms, bool input)
{
    if (backoff == 0) {
        return false;
    }
    const uint32_t elapsed = now_ms - backoff->last_attempt_ms;
    const bool first = backoff->consecutive == 0;
    const bool by_input = input && elapsed >= BLE_ADV_BACKOFF_INPUT_MIN_MS;
    const bool by_time = elapsed >= ble_adv_backoff_delay_ms(
                                        first ? 0u : backoff->consecutive - 1u);
    if (!first && !by_input && !by_time) {
        return false;
    }
    if (!first && by_input && !by_time) {
        ++backoff->input_restarts;
    }
    if (backoff->consecutive < UINT32_MAX) {
        ++backoff->consecutive;
    }
    ++backoff->restarts;
    backoff->last_attempt_ms = now_ms;
    return true;
}
