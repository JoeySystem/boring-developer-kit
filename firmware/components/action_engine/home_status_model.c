#include "home_status_model.h"

static const char *mode_label(codex_micro_mode_t mode)
{
    switch (mode) {
    case CODEX_MICRO_MODE_CODEX:
        return "MODE CODEX";
    case CODEX_MICRO_MODE_EDA:
        return "MODE EDA";
    case CODEX_MICRO_MODE_CLAUDE_CODE:
        return "MODE CC";
    case CODEX_MICRO_MODE_NORMAL:
    default:
        return "MODE KEY";
    }
}

home_status_model_t home_status_model_resolve(bool usb_mounted,
                                              bool ble_connected,
                                              codex_micro_mode_t mode)
{
    home_status_model_t status = {
        .output_label = "OUT NONE",
        .mode_label = mode_label(mode),
        .output_connected = false,
    };

    if (usb_mounted) {
        status.output_label = "OUT USB";
        status.output_connected = true;
    } else if (ble_connected) {
        status.output_label = "OUT BLE";
        status.output_connected = true;
    }
    return status;
}
