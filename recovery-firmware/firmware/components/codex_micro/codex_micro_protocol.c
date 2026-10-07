#include "codex_micro_protocol.h"

#include <stdio.h>
#include <string.h>

static bool json_object_complete(const char *data, size_t length, bool *valid)
{
    *valid = true;
    bool in_string = false;
    bool escaped = false;
    bool started = false;
    int depth = 0;

    for (size_t index = 0; index < length; ++index) {
        const char value = data[index];
        if (!started) {
            if (value == '{') {
                started = true;
                depth = 1;
            }
            continue;
        }
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (value == '\\') {
                escaped = true;
            } else if (value == '"') {
                in_string = false;
            }
            continue;
        }
        if (value == '"') {
            in_string = true;
        } else if (value == '{' || value == '[') {
            ++depth;
            if (depth > (int)CODEX_MICRO_JSON_MAX_NESTING) {
                *valid = false;
                return false;
            }
        } else if (value == '}' || value == ']') {
            --depth;
            if (depth == 0) {
                for (size_t tail = index + 1; tail < length; ++tail) {
                    const char suffix = data[tail];
                    if (suffix != '\0' && suffix != '\n' && suffix != '\r' &&
                        suffix != ' ' && suffix != '\t') {
                        return false;
                    }
                }
                return !in_string;
            }
            if (depth < 0) {
                return false;
            }
        }
    }
    return false;
}

size_t codex_micro_encode_fragment(const char *json, size_t json_length,
                                   size_t offset,
                                   uint8_t report[CODEX_MICRO_REPORT_BYTES])
{
    if (json == NULL || report == NULL || offset >= json_length) {
        return 0;
    }
    const size_t remaining = json_length - offset;
    const size_t chunk = remaining < CODEX_MICRO_JSON_FRAGMENT_BYTES
                             ? remaining
                             : CODEX_MICRO_JSON_FRAGMENT_BYTES;
    memset(report, 0, CODEX_MICRO_REPORT_BYTES);
    report[0] = 2;
    report[1] = (uint8_t)chunk;
    memcpy(report + 2, json + offset, chunk);
    return chunk;
}

size_t codex_micro_encode_radial_json(
    char buffer[CODEX_MICRO_RADIAL_JSON_BUFFER_BYTES], size_t capacity,
    float angle, bool pressed)
{
    if (buffer == NULL || capacity == 0) {
        return 0;
    }
    buffer[0] = '\0';
    if (!(angle >= 0.0f && angle < 1.0f)) {
        return 0;
    }

    unsigned int fractional = (unsigned int)(angle * 1000000.0f);
    if (fractional > 999999u) {
        fractional = 999999u;
    }
    const int written = snprintf(
        buffer, capacity,
        "{\"method\":\"v.oai.rad\",\"params\":{\"a\":0.%06u,\"d\":%u}}",
        fractional, pressed ? 1u : 0u);
    if (written < 0 || (size_t)written >= capacity ||
        (size_t)written + 1u > CODEX_MICRO_JSON_FRAGMENT_BYTES) {
        buffer[0] = '\0';
        return 0;
    }
    return (size_t)written;
}

bool codex_micro_rx_append(codex_micro_rx_t *rx, const uint8_t *report,
                           size_t report_length, bool *complete)
{
    if (complete != NULL) {
        *complete = false;
    }
    if (rx == NULL || report == NULL || complete == NULL || report_length < 2) {
        return false;
    }

    const size_t base =
        report_length >= 3 && report[0] == CODEX_MICRO_REPORT_ID ? 1u : 0u;
    if (report_length < base + 2 || report[base] != 2) {
        return false;
    }
    const size_t payload_length = report[base + 1];
    if (payload_length > CODEX_MICRO_JSON_FRAGMENT_BYTES ||
        report_length < base + 2 + payload_length) {
        return false;
    }

    const uint8_t *payload = report + base + 2;
    static const char prefix[] = "{\"method\"";
    if (payload_length >= sizeof(prefix) - 1 &&
        memcmp(payload, prefix, sizeof(prefix) - 1) == 0 &&
        rx->length != 0) {
        codex_micro_rx_reset(rx);
    }
    if (rx->length + payload_length >= sizeof(rx->data)) {
        codex_micro_rx_reset(rx);
        return false;
    }
    memcpy(rx->data + rx->length, payload, payload_length);
    rx->length += payload_length;
    rx->data[rx->length] = '\0';
    bool valid = true;
    *complete = json_object_complete(rx->data, rx->length, &valid);
    if (!valid) {
        codex_micro_rx_reset(rx);
        return false;
    }
    return true;
}

void codex_micro_rx_reset(codex_micro_rx_t *rx)
{
    if (rx == NULL) {
        return;
    }
    rx->length = 0;
    rx->data[0] = '\0';
}
