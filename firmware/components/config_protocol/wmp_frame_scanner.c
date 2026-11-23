#include "wmp_frame_scanner.h"

#include <string.h>

#include "protocol_contract.h"

static uint32_t read_u32_le(const uint8_t *data)
{
    return (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
           ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
}

uint32_t wmp_crc32_ieee(const uint8_t *data, size_t length)
{
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
        }
    }
    return crc ^ UINT32_MAX;
}

static wmp_scan_result_t reject(uint8_t error, size_t consume, bool resync,
                                uint8_t message_type, uint32_t request_id,
                                uint32_t payload_length)
{
    return (wmp_scan_result_t) {
        .kind = WMP_SCAN_REJECT, .consume = consume, .resync = resync,
        .error = error, .message_type = message_type,
        .request_id = request_id, .payload_length = payload_length,
    };
}

wmp_scan_result_t wmp_frame_scan(const uint8_t *data, size_t length)
{
    if (data == NULL || length < WMP_PROTOCOL_HEADER_SIZE) {
        return (wmp_scan_result_t) { .kind = WMP_SCAN_NEED_MORE };
    }
    if (memcmp(data, WMP_PROTOCOL_MAGIC, 4) != 0) {
        size_t skip = length > 3 ? length - 3 : 1;
        for (size_t index = 1; index + 4 <= length; ++index) {
            if (memcmp(data + index, WMP_PROTOCOL_MAGIC, 4) == 0) {
                skip = index;
                break;
            }
        }
        /* Preserve a possible partial magic suffix for the next chunk. */
        return (wmp_scan_result_t) {
            .kind = WMP_SCAN_SKIP, .consume = skip, .resync = true,
        };
    }

    const uint8_t major = data[4];
    const uint8_t minor = data[5];
    const uint8_t message_type = data[6];
    const uint8_t flags = data[7];
    const uint32_t request_id = read_u32_le(data + 8);
    const uint32_t payload_length = read_u32_le(data + 12);
    const uint32_t expected_crc = read_u32_le(data + 16);

    if (payload_length > WMP_PROTOCOL_MAX_PAYLOAD) {
        return reject(WMP_ERROR_PAYLOAD_TOO_LARGE, 4, true, message_type,
                      request_id, payload_length);
    }
    const size_t frame_length = WMP_PROTOCOL_HEADER_SIZE + payload_length;
    if (length < frame_length) {
        return (wmp_scan_result_t) { .kind = WMP_SCAN_NEED_MORE };
    }
    if (wmp_crc32_ieee(data + WMP_PROTOCOL_HEADER_SIZE, payload_length) !=
        expected_crc) {
        /* The length may be the corrupted field: step past this magic only. */
        return reject(WMP_ERROR_CRC_MISMATCH, 1, true, message_type,
                      request_id, payload_length);
    }
    if (request_id == 0 || flags != 0) {
        return reject(WMP_ERROR_INVALID_FRAME, frame_length, false,
                      message_type, request_id, payload_length);
    }
    /* Within one major, minors only add backward-compatible extensions: a
     * client may speak any minor up to the device's own. */
    if (major != WMP_PROTOCOL_MAJOR || minor > WMP_PROTOCOL_MINOR) {
        return reject(WMP_ERROR_UNSUPPORTED_PROTOCOL, frame_length, false,
                      message_type, request_id, payload_length);
    }
    return (wmp_scan_result_t) {
        .kind = WMP_SCAN_FRAME, .consume = frame_length,
        .message_type = message_type, .request_id = request_id,
        .payload_length = payload_length,
    };
}

bool wmp_frame_partial_expired(size_t buffered, uint32_t now_ms,
                               uint32_t last_rx_ms, uint32_t timeout_ms)
{
    return buffered > 0 && now_ms - last_rx_ms >= timeout_ms;
}

bool wmp_json_nesting_within(const uint8_t *data, size_t length,
                             uint32_t max_depth)
{
    bool in_string = false;
    bool escaped = false;
    uint32_t depth = 0;
    for (size_t index = 0; data != NULL && index < length; ++index) {
        const uint8_t value = data[index];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (value == '\\') {
                escaped = true;
            } else if (value == '"') {
                in_string = false;
            }
        } else if (value == '"') {
            in_string = true;
        } else if (value == '{' || value == '[') {
            if (++depth > max_depth) {
                return false;
            }
        } else if ((value == '}' || value == ']') && depth > 0u) {
            --depth;
        }
    }
    return true;
}
