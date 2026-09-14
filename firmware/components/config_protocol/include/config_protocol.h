#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "board.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef esp_err_t (*config_protocol_tx_fn)(const uint8_t *data, size_t length, void *context);

/** Keep config activation separate from an in-progress image transaction. */
bool config_protocol_screen_icon_upload_active(void);
/** Factory Default only; serialize namespace erase against protocol writes. */
esp_err_t config_protocol_screen_icon_factory_default(void);

typedef enum {
    CONFIG_PROTOCOL_TRANSPORT_USB = 0,
    CONFIG_PROTOCOL_TRANSPORT_BLE = 1,
} config_protocol_transport_t;

typedef enum {
    CONFIG_PROTOCOL_LIGHTING_PREVIEW_SET = 0,
    CONFIG_PROTOCOL_LIGHTING_PREVIEW_CLEAR,
} config_protocol_lighting_preview_operation_t;

typedef struct {
    config_protocol_lighting_preview_operation_t operation;
    bool enabled;
    uint8_t brightness;
    size_t under_key_count;
    board_rgb_t under_key[BOARD_MAX_UNDER_KEY_RGB_COUNT];
} config_protocol_lighting_preview_request_t;

#define CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_TIMEOUT_MS 3000u

typedef enum {
    CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_START = 0,
    CONFIG_PROTOCOL_DIAGNOSTIC_CAPTURE_STOP,
} config_protocol_diagnostic_capture_operation_t;

typedef struct {
    config_protocol_diagnostic_capture_operation_t operation;
} config_protocol_diagnostic_capture_request_t;

void config_protocol_init(const char *serial, config_protocol_tx_fn tx, void *tx_context);

/** Publish BLE availability only after the configuration service has started. */
void config_protocol_set_ble_service_ready(bool ready);
void config_protocol_feed(const uint8_t *data, size_t length);
/** Claim the single WMP session for one transport and reset volatile state. */
bool config_protocol_claim_transport(config_protocol_transport_t transport,
                                     config_protocol_tx_fn tx,
                                     void *tx_context);
/** Release a non-USB owner and restore the registered USB transport. */
void config_protocol_release_transport(config_protocol_transport_t transport);
/** Feed bytes only when the caller currently owns the WMP session. */
void config_protocol_feed_transport(config_protocol_transport_t transport,
                                    const uint8_t *data, size_t length);
config_protocol_transport_t config_protocol_active_transport(void);
/** Capture joystick samples and expire abandoned calibration sessions. */
void config_protocol_poll(void);
void config_protocol_reset(void);

/**
 * True after a compatible HELLO response has been queued during the current
 * USB attachment. A detach/reset clears the session.
 */
bool config_protocol_session_active(void);

/** Consume the newest volatile lighting command published by the CDC task. */
bool config_protocol_take_lighting_preview_request(
    config_protocol_lighting_preview_request_t *request);

/** Consume the newest diagnostic ownership request from the CDC task. */
bool config_protocol_take_diagnostic_capture_request(
    config_protocol_diagnostic_capture_request_t *request);

/** Update the immediate SET preview admission state. */
void config_protocol_set_lighting_preview_busy(bool busy);

/** Publish Action Engine diagnostic-capture state for admission and status. */
void config_protocol_set_diagnostic_capture_status(
    bool active, bool waiting_for_neutral, uint32_t event_sequence,
    const char *last_control, bool last_pressed);

/** Publish existing Action Engine state through the read-only GET_STATUS diagnostics. */
void config_protocol_set_action_diagnostics(
    const char *host_output_state, const char *local_page,
    const char *round_setting_state, bool quick_config_active,
    bool local_confirm_selected, bool round_feedback_active,
    bool idle_circle_active, bool idle_standby_active,
    bool usb_standby_active, bool lighting_preview_busy);

#ifdef __cplusplus
}
#endif
