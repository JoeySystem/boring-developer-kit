#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Register the encrypted WMP1 BLE configuration service when BLE is built. */
/* Register the shared GATTS observer before codex_micro_init() starts the BLE
 * stack, then register this service after that initialization succeeds. */
void ble_config_service_prepare(void);
esp_err_t ble_config_service_start(void);

/** True while a client has enabled the response indication characteristic. */
bool ble_config_service_connected(void);

#ifdef __cplusplus
}
#endif
