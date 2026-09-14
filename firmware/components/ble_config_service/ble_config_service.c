#include "ble_config_service.h"

#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>

#include "codex_micro.h"
#include "config_protocol.h"
#include "sdkconfig.h"

#if CONFIG_CODEX_MICRO_BLE_ENABLED

#include "esp_gatt_common_api.h"
#include "esp_gatts_api.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "protocol_contract.h"

#define BLE_CONFIG_APP_ID 0x3C01u
_Static_assert(BLE_CONFIG_APP_ID <= ESP_APP_ID_MAX,
               "BLE configuration app ID must be accepted by Bluedroid");
#define BLE_CONFIG_SERVICE_INSTANCE 0u
#define BLE_CONFIG_MAX_ATTRIBUTE_BYTES ESP_GATT_MAX_ATTR_LEN
#define BLE_CONFIG_DEFAULT_ATT_MTU 23u
#define BLE_CONFIG_FRAME_BYTES \
    (WMP_PROTOCOL_HEADER_SIZE + WMP_PROTOCOL_MAX_PAYLOAD)

static const char *TAG = "ble_config";

/* Canonical UUIDs are documented in protocol.md. Bluedroid attribute tables
 * store 128-bit UUIDs least-significant byte first. */
static const uint8_t SERVICE_UUID[16] = {
    0x31, 0x47, 0x52, 0x4b, 0x8f, 0x6e, 0x2d, 0x9c,
    0x5b, 0x4a, 0x91, 0x7a, 0x01, 0x00, 0x2f, 0x7e,
};
static const uint8_t RX_UUID[16] = {
    0x31, 0x47, 0x52, 0x4b, 0x8f, 0x6e, 0x2d, 0x9c,
    0x5b, 0x4a, 0x91, 0x7a, 0x02, 0x00, 0x2f, 0x7e,
};
static const uint8_t TX_UUID[16] = {
    0x31, 0x47, 0x52, 0x4b, 0x8f, 0x6e, 0x2d, 0x9c,
    0x5b, 0x4a, 0x91, 0x7a, 0x03, 0x00, 0x2f, 0x7e,
};

enum {
    ATTR_SERVICE = 0,
    ATTR_RX_DECLARATION,
    ATTR_RX_VALUE,
    ATTR_TX_DECLARATION,
    ATTR_TX_VALUE,
    ATTR_TX_CCC,
    ATTR_COUNT,
};

static const uint16_t PRIMARY_SERVICE_UUID = ESP_GATT_UUID_PRI_SERVICE;
static const uint16_t CHARACTER_DECLARATION_UUID = ESP_GATT_UUID_CHAR_DECLARE;
static const uint16_t CLIENT_CONFIG_UUID = ESP_GATT_UUID_CHAR_CLIENT_CONFIG;
static const uint8_t RX_PROPERTIES = ESP_GATT_CHAR_PROP_BIT_WRITE;
static const uint8_t TX_PROPERTIES = ESP_GATT_CHAR_PROP_BIT_INDICATE;
static uint8_t s_empty_value;
static uint8_t s_ccc_value[2];

static const esp_gatts_attr_db_t ATTRIBUTE_TABLE[ATTR_COUNT] = {
    [ATTR_SERVICE] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_16, (uint8_t *)&PRIMARY_SERVICE_UUID, ESP_GATT_PERM_READ,
         sizeof(SERVICE_UUID), sizeof(SERVICE_UUID), (uint8_t *)SERVICE_UUID}},
    [ATTR_RX_DECLARATION] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_16, (uint8_t *)&CHARACTER_DECLARATION_UUID,
         ESP_GATT_PERM_READ, sizeof(RX_PROPERTIES), sizeof(RX_PROPERTIES),
         (uint8_t *)&RX_PROPERTIES}},
    [ATTR_RX_VALUE] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_128, (uint8_t *)RX_UUID, ESP_GATT_PERM_WRITE_ENCRYPTED,
         BLE_CONFIG_MAX_ATTRIBUTE_BYTES, 0, &s_empty_value}},
    [ATTR_TX_DECLARATION] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_16, (uint8_t *)&CHARACTER_DECLARATION_UUID,
         ESP_GATT_PERM_READ, sizeof(TX_PROPERTIES), sizeof(TX_PROPERTIES),
         (uint8_t *)&TX_PROPERTIES}},
    [ATTR_TX_VALUE] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_128, (uint8_t *)TX_UUID, ESP_GATT_PERM_READ_ENCRYPTED,
         BLE_CONFIG_MAX_ATTRIBUTE_BYTES, 0, &s_empty_value}},
    [ATTR_TX_CCC] = {{ESP_GATT_AUTO_RSP},
        {ESP_UUID_LEN_16, (uint8_t *)&CLIENT_CONFIG_UUID,
         ESP_GATT_PERM_READ_ENCRYPTED | ESP_GATT_PERM_WRITE_ENCRYPTED,
         sizeof(s_ccc_value), sizeof(s_ccc_value), s_ccc_value}},
};

static uint16_t s_handles[ATTR_COUNT];
static esp_gatt_if_t s_gatts_if = ESP_GATT_IF_NONE;
static uint16_t s_connection_id;
static uint16_t s_mtu = BLE_CONFIG_DEFAULT_ATT_MTU;
static atomic_bool s_subscribed;
static uint8_t s_tx_frame[BLE_CONFIG_FRAME_BYTES];
static size_t s_tx_length;
static size_t s_tx_offset;
static bool s_tx_waiting_confirmation;

static void clear_tx(void)
{
    s_tx_length = 0;
    s_tx_offset = 0;
    s_tx_waiting_confirmation = false;
}

static esp_err_t send_next_indication(void)
{
    if (!atomic_load(&s_subscribed) || s_gatts_if == ESP_GATT_IF_NONE ||
        s_tx_offset >= s_tx_length || s_tx_waiting_confirmation) {
        return ESP_ERR_INVALID_STATE;
    }
    const size_t capacity = s_mtu > 3u ? s_mtu - 3u : 20u;
    const size_t remaining = s_tx_length - s_tx_offset;
    const uint16_t length =
        (uint16_t)(remaining < capacity ? remaining : capacity);
    const esp_err_t result = esp_ble_gatts_send_indicate(
        s_gatts_if, s_connection_id, s_handles[ATTR_TX_VALUE], length,
        s_tx_frame + s_tx_offset, true);
    if (result == ESP_OK) {
        s_tx_waiting_confirmation = true;
        s_tx_offset += length;
    }
    return result;
}

static esp_err_t protocol_tx(const uint8_t *data, size_t length, void *context)
{
    (void)context;
    if (!atomic_load(&s_subscribed) || data == NULL || length == 0u ||
        length > sizeof(s_tx_frame) || s_tx_length != 0u) {
        return ESP_ERR_INVALID_STATE;
    }
    memcpy(s_tx_frame, data, length);
    s_tx_length = length;
    s_tx_offset = 0;
    const esp_err_t result = send_next_indication();
    if (result != ESP_OK) {
        clear_tx();
    }
    return result;
}

static void set_subscribed(bool subscribed)
{
    if (subscribed == atomic_load(&s_subscribed)) {
        return;
    }
    clear_tx();
    if (subscribed && config_protocol_claim_transport(
                          CONFIG_PROTOCOL_TRANSPORT_BLE, protocol_tx, NULL)) {
        atomic_store(&s_subscribed, true);
        ESP_LOGI(TAG, "encrypted BLE configuration session ready");
        return;
    }
    atomic_store(&s_subscribed, false);
    config_protocol_release_transport(CONFIG_PROTOCOL_TRANSPORT_BLE);
}

static bool gatts_event(int raw_event, uint16_t raw_gatts_if,
                        void *event_data, void *context)
{
    (void)context;
    const esp_gatts_cb_event_t event = (esp_gatts_cb_event_t)raw_event;
    const esp_gatt_if_t gatts_if = (esp_gatt_if_t)raw_gatts_if;
    esp_ble_gatts_cb_param_t *parameter = event_data;
    if (parameter == NULL) {
        return false;
    }
    if (event == ESP_GATTS_REG_EVT) {
        if (parameter->reg.app_id != BLE_CONFIG_APP_ID) {
            return false;
        }
        if (parameter->reg.status != ESP_GATT_OK) {
            ESP_LOGE(TAG, "GATT registration failed: %d", parameter->reg.status);
            return true;
        }
        s_gatts_if = gatts_if;
        const esp_err_t result = esp_ble_gatts_create_attr_tab(
            ATTRIBUTE_TABLE, gatts_if, ATTR_COUNT,
            BLE_CONFIG_SERVICE_INSTANCE);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "could not create GATT table: %s",
                     esp_err_to_name(result));
        }
        return true;
    }
    if (s_gatts_if == ESP_GATT_IF_NONE || gatts_if != s_gatts_if) {
        return false;
    }
    switch (event) {
    case ESP_GATTS_START_EVT:
        if (parameter->start.service_handle == s_handles[ATTR_SERVICE]) {
            const bool ready = parameter->start.status == ESP_GATT_OK;
            config_protocol_set_ble_service_ready(ready);
            if (!ready) {
                ESP_LOGE(TAG, "GATT service start failed: %d",
                         parameter->start.status);
            }
        }
        break;
    case ESP_GATTS_CREAT_ATTR_TAB_EVT:
        if (parameter->add_attr_tab.status == ESP_GATT_OK &&
            parameter->add_attr_tab.num_handle == ATTR_COUNT) {
            memcpy(s_handles, parameter->add_attr_tab.handles,
                   sizeof(s_handles));
            (void)esp_ble_gatts_start_service(s_handles[ATTR_SERVICE]);
        }
        break;
    case ESP_GATTS_CONNECT_EVT:
        s_connection_id = parameter->connect.conn_id;
        s_mtu = BLE_CONFIG_DEFAULT_ATT_MTU;
        set_subscribed(false);
        /* A bonded host may cache the formerly-last HID service through
         * 0xffff. Ask it to refresh before accessing our appended service. */
        (void)esp_ble_gatts_send_service_change_indication(
            gatts_if, parameter->connect.remote_bda);
        break;
    case ESP_GATTS_SEND_SERVICE_CHANGE_EVT:
        if (parameter->service_change.status != ESP_GATT_OK) {
            ESP_LOGW(TAG, "service change indication failed: %d",
                     parameter->service_change.status);
        }
        break;
    case ESP_GATTS_DISCONNECT_EVT:
        set_subscribed(false);
        break;
    case ESP_GATTS_MTU_EVT:
        if (parameter->mtu.conn_id == s_connection_id) {
            s_mtu = parameter->mtu.mtu;
        }
        break;
    case ESP_GATTS_WRITE_EVT:
        if (parameter->write.handle == s_handles[ATTR_TX_CCC] &&
            parameter->write.len == 2u) {
            const uint16_t value = (uint16_t)parameter->write.value[0] |
                                   ((uint16_t)parameter->write.value[1] << 8u);
            set_subscribed(value == 0x0002u);
        } else if (parameter->write.handle == s_handles[ATTR_RX_VALUE] &&
                   !parameter->write.is_prep &&
                   atomic_load(&s_subscribed)) {
            config_protocol_feed_transport(CONFIG_PROTOCOL_TRANSPORT_BLE,
                                           parameter->write.value,
                                           parameter->write.len);
        }
        break;
    case ESP_GATTS_CONF_EVT:
        if (s_tx_waiting_confirmation &&
            parameter->conf.handle == s_handles[ATTR_TX_VALUE]) {
            s_tx_waiting_confirmation = false;
            if (parameter->conf.status != ESP_GATT_OK) {
                clear_tx();
            } else if (s_tx_offset < s_tx_length) {
                if (send_next_indication() != ESP_OK) {
                    clear_tx();
                }
            } else {
                clear_tx();
            }
        }
        break;
    default:
        break;
    }
    return true;
}

void ble_config_service_prepare(void)
{
    config_protocol_set_ble_service_ready(false);
    atomic_store(&s_subscribed, false);
    clear_tx();
    codex_micro_register_ble_gatts_observer(gatts_event, NULL);
}

esp_err_t ble_config_service_start(void)
{
    /* Append after HID's complete database to preserve existing bonded handles.
     * codex_micro_init starts that database asynchronously. */
    const TickType_t started = xTaskGetTickCount();
    while (!codex_micro_ble_services_ready()) {
        if (xTaskGetTickCount() - started >= pdMS_TO_TICKS(3000)) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    const esp_err_t mtu_result = esp_ble_gatt_set_local_mtu(517u);
    if (mtu_result != ESP_OK) {
        ESP_LOGW(TAG, "could not set local ATT MTU: %s",
                 esp_err_to_name(mtu_result));
    }
    return esp_ble_gatts_app_register(BLE_CONFIG_APP_ID);
}

bool ble_config_service_connected(void)
{
    return atomic_load(&s_subscribed);
}

#else

void ble_config_service_prepare(void)
{
}

esp_err_t ble_config_service_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

bool ble_config_service_connected(void)
{
    return false;
}

#endif
