/*
 * SPDX-FileCopyrightText: 2026 BORING
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * BORING MIST addition to the ESP-IDF v6.0.2 esp_hid component.
 * See ../BORING_CHANGES.md for why this component is overridden.
 */

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "esp_hidd.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * A subscription mask has one bit per BLE input report (Client Characteristic
 * Configuration descriptor), in GATT database order, plus the Battery Level
 * notification in ESP_HIDD_BLE_SUBSCRIPTION_BATTERY. The order is stable for
 * a given report map, so a mask saved for a bonded host can be restored after
 * the device restarts. ESP-IDF keeps these values only in RAM and starts at 0;
 * a bonded host is not required to write them again on reconnection (the
 * server must remember them), and Windows relies on that.
 */
#define ESP_HIDD_BLE_SUBSCRIPTION_BATTERY (UINT32_C(1) << 31)

/* Current subscriptions of the connected (or last connected) host. */
esp_err_t esp_hidd_ble_get_subscriptions(esp_hidd_dev_t *dev, uint32_t *mask);

/* Every subscription bit this device exposes. */
esp_err_t esp_hidd_ble_get_subscription_mask_all(esp_hidd_dev_t *dev,
                                                 uint32_t *mask);

/*
 * Replace the current subscriptions, typically right after a bonded host has
 * re-established encryption. Set bits enable notifications.
 */
esp_err_t esp_hidd_ble_set_subscriptions(esp_hidd_dev_t *dev, uint32_t mask);

/* True when the host has enabled notifications for this input report. */
bool esp_hidd_ble_input_subscribed(esp_hidd_dev_t *dev, size_t report_id);

#ifdef __cplusplus
}
#endif
