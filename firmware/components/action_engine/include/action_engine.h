#pragma once

#include "board.h"
#include "esp_err.h"

typedef struct {
    const char *host_output_state;
    const char *local_page;
    const char *round_setting_state;
    bool quick_config_active;
    bool local_confirm_selected;
    bool round_feedback_active;
    bool idle_circle_active;
    bool idle_standby_active;
    bool usb_standby_active;
    bool lighting_preview_busy;
} action_engine_diagnostics_t;

/** Initialize runtime action state and apply the active persisted config. */
esp_err_t action_engine_init(void);

/** Execute one logical board event through the active profile mapping. */
void action_engine_handle_event(const board_event_t *event);

/** Advance non-blocking macros and delayed local actions. */
void action_engine_poll(void);

/** Begin the application cleanup phase before the board cuts system power. */
void action_engine_request_shutdown(void);

/** Restart firmware after releasing transient input; keep settings and pairing. */
void action_engine_request_restart(void);

/** Allow a local feedback-only save after its confirming control is released. */
bool action_engine_config_activation_ready(void);

/** Release transient HID state and reapply newly activated configuration. */
void action_engine_config_activated(void);

/** Return the current local-UI ownership state for read-only device diagnostics. */
void action_engine_get_diagnostics(action_engine_diagnostics_t *diagnostics);
