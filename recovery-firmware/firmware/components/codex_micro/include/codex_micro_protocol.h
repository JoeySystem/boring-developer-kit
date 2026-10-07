#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "protocol_contract.h"

#ifdef __cplusplus
extern "C" {
#endif

#define CODEX_MICRO_COMPAT_VID 0x303Au
#define CODEX_MICRO_COMPAT_PID 0x8360u
#define CODEX_MICRO_COMPAT_BCD_DEVICE 0x0101u
#define CODEX_MICRO_COMPAT_MANUFACTURER "Work Louder"
#define CODEX_MICRO_COMPAT_PRODUCT "Codex Micro"
#define CODEX_MICRO_BLE_DEVICE_NAME_BYTES \
    (WMP_BLE_NAME_MAX_UTF8_BYTES + 1u)
#define CODEX_MICRO_BLE_VID CODEX_MICRO_COMPAT_VID
#define CODEX_MICRO_BLE_PID CODEX_MICRO_COMPAT_PID
#define CODEX_MICRO_REPORT_ID 6u
#define CODEX_MICRO_REPORT_BYTES 63u
#define CODEX_MICRO_JSON_FRAGMENT_BYTES 61u
#define CODEX_MICRO_RPC_BUFFER_BYTES 4096u
#define CODEX_MICRO_JSON_MAX_NESTING 32u
#define CODEX_MICRO_RADIAL_JSON_BUFFER_BYTES CODEX_MICRO_JSON_FRAGMENT_BYTES

typedef struct {
    char data[CODEX_MICRO_RPC_BUFFER_BYTES];
    size_t length;
} codex_micro_rx_t;

size_t codex_micro_encode_fragment(const char *json, size_t json_length,
                                   size_t offset,
                                   uint8_t report[CODEX_MICRO_REPORT_BYTES]);

/**
 * Encode one v.oai.rad message compactly enough for a single HID fragment.
 *
 * The angle must already be normalized to [0, 1). Six fractional digits
 * preserve substantially more resolution than the radial reporting threshold.
 */
size_t codex_micro_encode_radial_json(
    char buffer[CODEX_MICRO_RADIAL_JSON_BUFFER_BYTES], size_t capacity,
    float angle, bool pressed);

/**
 * Append one host output report to the bounded RPC accumulator.
 *
 * Report ID 6 may be present as byte zero or omitted by BLE HOGP. On success,
 * complete is true only when the buffer contains one syntactically complete
 * top-level JSON object.
 */
bool codex_micro_rx_append(codex_micro_rx_t *rx, const uint8_t *report,
                           size_t report_length, bool *complete);
void codex_micro_rx_reset(codex_micro_rx_t *rx);

#ifdef __cplusplus
}
#endif
