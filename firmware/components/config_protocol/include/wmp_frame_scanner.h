#pragma once

/*
 * WMP request-frame scanner (pure logic, host-testable).
 *
 * The CRC covers the payload only, so a header that fails the CRC is never
 * trusted to say how many bytes to drop: the scanner steps one byte past its
 * magic and resynchronises on the next "WMP1". A header that passes the CRC
 * but breaks a rule is dropped whole, as before.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* An incomplete frame older than this (since the last received byte) is
 * dropped so the parser can never wait forever on a corrupted length. */
#define WMP_PARTIAL_FRAME_TIMEOUT_USB_MS 500U
#define WMP_PARTIAL_FRAME_TIMEOUT_BLE_MS 2000U

typedef enum {
    WMP_SCAN_NEED_MORE = 0, /* Wait for more bytes. */
    WMP_SCAN_SKIP,          /* Drop `consume` bytes of noise; no frame. */
    WMP_SCAN_REJECT,        /* NACK `error` for a nonzero request_id; drop `consume`. */
    WMP_SCAN_FRAME,         /* One valid request frame of `consume` bytes. */
} wmp_scan_kind_t;

typedef struct {
    wmp_scan_kind_t kind;
    size_t consume;
    /* Bytes are dropped without trusting the header length. */
    bool resync;
    uint8_t error; /* WMP_ERROR_* for WMP_SCAN_REJECT. */
    uint8_t message_type;
    uint32_t request_id;
    uint32_t payload_length;
} wmp_scan_result_t;

uint32_t wmp_crc32_ieee(const uint8_t *data, size_t length);

/* Classify the bytes at the head of the receive buffer. */
wmp_scan_result_t wmp_frame_scan(const uint8_t *data, size_t length);

/* cJSON parses arrays and objects recursively, about 64 bytes of stack per
 * level, and its own limit is far above what the parser tasks can hold. A
 * valid request never nests deeper than a handful of levels. */
#define WMP_JSON_MAX_NESTING 32U

/* True when no array or object in the payload is nested deeper than
 * max_depth. Brackets inside strings do not count. */
bool wmp_json_nesting_within(const uint8_t *data, size_t length,
                             uint32_t max_depth);

/* True when buffered bytes have waited at least timeout_ms since the last
 * received byte. Wrap-safe. */
bool wmp_frame_partial_expired(size_t buffered, uint32_t now_ms,
                               uint32_t last_rx_ms, uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
