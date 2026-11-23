#include "codex_micro.h"

#include "codex_micro_controls.h"
#include "protocol_contract.h"

static codex_micro_mode_t s_mode = CODEX_MICRO_MODE_NORMAL;
static bool s_input_suppressed;
static bool s_ble_suspended;

bool codex_micro_agent_focus_supported(void)
{
    return false;
}

codex_agent_press_snapshot_t codex_micro_get_agent_press(void)
{
    return (codex_agent_press_snapshot_t){.agent = -1};
}

void codex_micro_clear_agent_press(void)
{
}

bool codex_micro_ble_services_ready(void)
{
    return false;
}

const char *codex_micro_ble_device_name(void)
{
    return WMP_BLE_NAME_DEFAULT;
}

esp_err_t codex_micro_init(const char *serial, bool initially_suspended)
{
    (void)serial;
    (void)initially_suspended;
    s_mode = CODEX_MICRO_MODE_NORMAL;
    return ESP_OK;
}

void codex_micro_register_ble_gatts_observer(
    codex_micro_ble_gatts_observer_fn observer, void *context)
{
    (void)observer;
    (void)context;
}

void codex_micro_hold_advertising_for_services(void)
{
}

void codex_micro_release_advertising_hold(void)
{
}

void codex_micro_register_service_change_sender(
    codex_micro_service_change_fn sender, void *context)
{
    (void)sender;
    (void)context;
}

void codex_micro_note_service_change_result(bool delivered)
{
    (void)delivered;
}

bool codex_micro_take_ble_reauth_needed(uint8_t *slot)
{
    (void)slot;
    return false;
}

void codex_micro_register_usb_hid(const codex_micro_usb_hid_t *transport)
{
    (void)transport;
}

void codex_micro_receive_usb_output(uint8_t report_id, const uint8_t *data,
                                    size_t length)
{
    (void)report_id;
    (void)data;
    (void)length;
}

void codex_micro_poll(void)
{
}

void codex_micro_prepare_soft_restart(void)
{
}

void codex_micro_note_local_input(void)
{
}

void codex_micro_set_input_suppressed(bool suppressed)
{
    s_input_suppressed = suppressed;
}

void codex_micro_set_ble_suspended(bool suspended)
{
    s_ble_suspended = suspended;
}

void codex_micro_set_ble_suspended_for_source(
    bool suspended, codex_micro_ble_disconnect_source_t source)
{
    (void)source;
    s_ble_suspended = suspended;
}

bool codex_micro_connected(void)
{
    return false;
}

bool codex_micro_input_ready(void)
{
    return false;
}

bool codex_micro_ble_connected(void)
{
    return false;
}

esp_err_t codex_micro_select_ble_slot(uint8_t slot)
{
    return slot >= 1u && slot <= CODEX_MICRO_BLE_SLOT_COUNT
               ? ESP_ERR_NOT_SUPPORTED
               : ESP_ERR_INVALID_ARG;
}

esp_err_t codex_micro_clear_ble_slot(uint8_t slot)
{
    return slot >= 1u && slot <= CODEX_MICRO_BLE_SLOT_COUNT
               ? ESP_ERR_NOT_SUPPORTED
               : ESP_ERR_INVALID_ARG;
}

esp_err_t codex_micro_select_ble_slot_from(uint8_t slot,
                                           codex_micro_ble_slot_source_t source)
{
    (void)source;
    return codex_micro_select_ble_slot(slot);
}

esp_err_t codex_micro_clear_ble_slot_from(uint8_t slot,
                                          codex_micro_ble_slot_source_t source)
{
    (void)source;
    return codex_micro_clear_ble_slot(slot);
}

size_t codex_micro_ble_slot_history(codex_micro_ble_slot_history_entry_t *entries,
                                    size_t capacity)
{
    (void)entries;
    (void)capacity;
    return 0;
}

esp_err_t codex_micro_factory_reset(void)
{
    s_mode = CODEX_MICRO_MODE_NORMAL;
    return ESP_OK;
}

uint8_t codex_micro_active_ble_slot(void)
{
    return 1u;
}

bool codex_micro_ble_slot_paired(uint8_t slot)
{
    (void)slot;
    return false;
}

bool codex_micro_ble_slot_connected(uint8_t slot)
{
    (void)slot;
    return false;
}

bool codex_micro_ble_pairing_window(uint8_t *slot, uint8_t *return_slot)
{
    (void)slot;
    (void)return_slot;
    return false;
}

bool codex_micro_take_ble_pairing_return(uint8_t *slot)
{
    (void)slot;
    return false;
}

uint8_t codex_micro_ble_free_slot(void)
{
    return 1u;
}

uint8_t codex_micro_ble_next_host_slot(void)
{
    return 0u;
}

bool codex_micro_take_ble_slot_redirect(uint8_t *slot)
{
    (void)slot;
    return false;
}

bool codex_micro_take_ble_peer_rejection(uint8_t *slot)
{
    (void)slot;
    return false;
}

bool codex_micro_take_ble_slot_clear_result(uint8_t *slot, esp_err_t *error)
{
    (void)slot;
    (void)error;
    return false;
}

bool codex_micro_take_ble_slot_clear_protocol_result(
    uint8_t *slot, esp_err_t *error)
{
    (void)slot;
    (void)error;
    return false;
}

codex_micro_mode_t codex_micro_mode(void)
{
    return s_mode;
}

codex_micro_mode_t codex_micro_toggle_mode(void)
{
    const bool eda_supported =
        codex_micro_active_key_layout() == CODEX_MICRO_KEY_LAYOUT_REV_A;
    /* Claude Code is no longer in the double-click cycle (boring-D46). */
    s_mode = codex_micro_next_mode(s_mode, false, eda_supported, false);
    return s_mode;
}

bool codex_micro_has_active_control(void)
{
    return false;
}

esp_err_t codex_micro_send_standard_report(uint8_t report_id,
                                           const uint8_t *data,
                                           size_t length)
{
    (void)report_id;
    (void)data;
    (void)length;
    return ESP_ERR_INVALID_STATE;
}

bool codex_micro_handle_event(const board_event_t *event)
{
    return s_input_suppressed && event != NULL &&
           event->control < BOARD_CONTROL_COUNT;
}

bool codex_micro_status_rgb(board_rgb_t status[8])
{
    (void)status;
    return false;
}

codex_micro_attention_t codex_micro_take_attention_notification(void)
{
    return CODEX_ATTENTION_NONE;
}

bool codex_micro_status_age_ms(uint32_t *age_ms)
{
    (void)age_ms;
    return false;
}

void codex_micro_get_diagnostics(codex_micro_diagnostics_t *diagnostics)
{
    if (diagnostics != NULL) {
        *diagnostics = (codex_micro_diagnostics_t){
            .connected = false,
            .usb_connected = false,
            .ble_connected = false,
            .mode = s_mode,
        };
    }
}
