#include "board.h"

#include <string.h>

static const char *const CONTROL_IDS[BOARD_CONTROL_COUNT] = {
    "key.1", "key.2", "key.3", "key.4", "key.5", "key.6", "key.7",
    "encoder.ccw", "encoder.cw", "encoder.press", "joystick.up",
    "joystick.down", "joystick.left", "joystick.right", "joystick.press",
    "key.8", "key.9", "key.10", "key.11", "key.12",
};

__attribute__((weak)) bool board_get_joystick_diagnostics(
    board_joystick_diagnostics_t *diagnostics)
{
    if (diagnostics != NULL) {
        memset(diagnostics, 0, sizeof(*diagnostics));
    }
    return false;
}

__attribute__((weak)) void board_set_joystick_calibration_active(bool active)
{
    (void)active;
}

__attribute__((weak)) bool board_take_shutdown_request(void)
{
    return false;
}

__attribute__((weak)) esp_err_t board_power_request_shutdown(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

const char *board_control_id(board_control_t control)
{
    if (control < BOARD_CONTROL_KEY_1 || control >= BOARD_CONTROL_COUNT) {
        return NULL;
    }
    return CONTROL_IDS[control];
}

bool board_control_from_id(const char *control_id, board_control_t *control)
{
    if (control_id == NULL) {
        return false;
    }
    for (board_control_t candidate = BOARD_CONTROL_KEY_1;
         candidate < BOARD_CONTROL_COUNT;
         candidate = (board_control_t)(candidate + 1)) {
        if (strcmp(control_id, CONTROL_IDS[candidate]) == 0) {
            if (control != NULL) {
                *control = candidate;
            }
            return true;
        }
    }
    return false;
}

bool board_control_supported(board_control_t control)
{
    size_t key_index = 0;
    if (board_control_key_index(control, &key_index)) {
        return key_index < board_key_count();
    }
    return board_is_product_target() &&
           control >= BOARD_CONTROL_ENCODER_CCW &&
           control <= BOARD_CONTROL_JOYSTICK_PRESS;
}

size_t board_control_count(void)
{
    size_t count = 0;
    for (board_control_t control = BOARD_CONTROL_KEY_1;
         control < BOARD_CONTROL_COUNT;
         control = (board_control_t)(control + 1)) {
        if (board_control_supported(control)) {
            ++count;
        }
    }
    return count;
}
