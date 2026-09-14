#include "prompt_record.h"

#include <string.h>

#define PROMPT_RECORD_VERSION 1u
#define PROMPT_RECORD_HEADER_BYTES 4u

static bool continuation(uint8_t value)
{
    return value >= 0x80u && value <= 0xbfu;
}

static bool utf8_text_valid(const uint8_t *text, size_t length,
                            bool allow_layout_controls)
{
    size_t index = 0;
    while (index < length) {
        const uint8_t first = text[index];
        if (first < 0x80u) {
            if ((first < 0x20u &&
                 !(allow_layout_controls &&
                   (first == '\t' || first == '\n' || first == '\r'))) ||
                first == 0x7fu) {
                return false;
            }
            ++index;
            continue;
        }
        if (first >= 0xc2u && first <= 0xdfu) {
            if (index + 1 >= length || !continuation(text[index + 1])) {
                return false;
            }
            index += 2;
            continue;
        }
        if (first >= 0xe0u && first <= 0xefu) {
            if (index + 2 >= length || !continuation(text[index + 2])) {
                return false;
            }
            const uint8_t second = text[index + 1];
            if ((first == 0xe0u && (second < 0xa0u || second > 0xbfu)) ||
                (first == 0xedu && (second < 0x80u || second > 0x9fu)) ||
                (first != 0xe0u && first != 0xedu && !continuation(second))) {
                return false;
            }
            index += 3;
            continue;
        }
        if (first >= 0xf0u && first <= 0xf4u) {
            if (index + 3 >= length || !continuation(text[index + 2]) ||
                !continuation(text[index + 3])) {
                return false;
            }
            const uint8_t second = text[index + 1];
            if ((first == 0xf0u && (second < 0x90u || second > 0xbfu)) ||
                (first == 0xf4u && (second < 0x80u || second > 0x8fu)) ||
                (first != 0xf0u && first != 0xf4u && !continuation(second))) {
                return false;
            }
            index += 4;
            continue;
        }
        return false;
    }
    return true;
}

size_t prompt_record_encode(const char *name, const char *body,
                            uint8_t *output, size_t capacity)
{
    if (name == NULL || body == NULL || output == NULL) {
        return 0;
    }
    const size_t name_length = strlen(name);
    const size_t body_length = strlen(body);
    const size_t encoded_length =
        PROMPT_RECORD_HEADER_BYTES + name_length + body_length;
    if (name_length == 0 || name_length > PROMPT_STORE_NAME_MAX_BYTES ||
        body_length == 0 || body_length > PROMPT_STORE_BODY_MAX_BYTES ||
        encoded_length > capacity ||
        !utf8_text_valid((const uint8_t *)name, name_length, false) ||
        !utf8_text_valid((const uint8_t *)body, body_length, true)) {
        return 0;
    }
    output[0] = PROMPT_RECORD_VERSION;
    output[1] = (uint8_t)name_length;
    output[2] = (uint8_t)body_length;
    output[3] = (uint8_t)(body_length >> 8);
    memcpy(output + PROMPT_RECORD_HEADER_BYTES, name, name_length);
    memcpy(output + PROMPT_RECORD_HEADER_BYTES + name_length, body, body_length);
    return encoded_length;
}

bool prompt_record_decode(const uint8_t *record, size_t record_length,
                          char *name, size_t name_capacity,
                          char *body, size_t body_capacity,
                          size_t *body_length)
{
    if (record == NULL || name == NULL || body == NULL || body_length == NULL ||
        record_length < PROMPT_RECORD_HEADER_BYTES ||
        record[0] != PROMPT_RECORD_VERSION) {
        return false;
    }
    const size_t stored_name_length = record[1];
    const size_t stored_body_length =
        (size_t)record[2] | ((size_t)record[3] << 8);
    if (stored_name_length == 0 ||
        stored_name_length > PROMPT_STORE_NAME_MAX_BYTES ||
        stored_body_length == 0 ||
        stored_body_length > PROMPT_STORE_BODY_MAX_BYTES ||
        record_length != PROMPT_RECORD_HEADER_BYTES + stored_name_length +
                             stored_body_length ||
        name_capacity <= stored_name_length ||
        body_capacity <= stored_body_length) {
        return false;
    }
    const uint8_t *stored_name = record + PROMPT_RECORD_HEADER_BYTES;
    const uint8_t *stored_body = stored_name + stored_name_length;
    if (!utf8_text_valid(stored_name, stored_name_length, false) ||
        !utf8_text_valid(stored_body, stored_body_length, true)) {
        return false;
    }
    memcpy(name, stored_name, stored_name_length);
    name[stored_name_length] = '\0';
    memcpy(body, stored_body, stored_body_length);
    body[stored_body_length] = '\0';
    *body_length = stored_body_length;
    return true;
}
