#include "device_auth_codec.h"

#include <stdio.h>
#include <string.h>

#include "protocol_contract.h"

static int base64url_value(unsigned char character)
{
    if (character >= 'A' && character <= 'Z') {
        return character - 'A';
    }
    if (character >= 'a' && character <= 'z') {
        return character - 'a' + 26;
    }
    if (character >= '0' && character <= '9') {
        return character - '0' + 52;
    }
    if (character == '-') {
        return 62;
    }
    if (character == '_') {
        return 63;
    }
    return -1;
}

bool device_auth_nonce_valid(const char *nonce)
{
    if (nonce == NULL ||
        strlen(nonce) != WMP_DEVICE_AUTH_NONCE_BASE64URL_LENGTH) {
        return false;
    }
    for (size_t index = 0;
         index < WMP_DEVICE_AUTH_NONCE_BASE64URL_LENGTH; ++index) {
        if (base64url_value((unsigned char)nonce[index]) < 0) {
            return false;
        }
    }
    /* A canonical unpadded encoding of 32 bytes has two zero pad bits. */
    return (base64url_value((unsigned char)nonce[
                WMP_DEVICE_AUTH_NONCE_BASE64URL_LENGTH - 1u]) & 0x03) == 0;
}

bool device_auth_base64url_encode(const uint8_t *input, size_t length,
                                  char *output, size_t capacity)
{
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    const size_t required = (length / 3u) * 4u +
                            (length % 3u == 0u ? 0u : length % 3u + 1u);
    if (input == NULL || output == NULL || capacity <= required) {
        return false;
    }
    size_t source = 0;
    size_t target = 0;
    while (source + 3u <= length) {
        const uint32_t value = ((uint32_t)input[source] << 16u) |
                               ((uint32_t)input[source + 1u] << 8u) |
                               input[source + 2u];
        output[target++] = alphabet[(value >> 18u) & 0x3fu];
        output[target++] = alphabet[(value >> 12u) & 0x3fu];
        output[target++] = alphabet[(value >> 6u) & 0x3fu];
        output[target++] = alphabet[value & 0x3fu];
        source += 3u;
    }
    if (length - source == 1u) {
        const uint32_t value = (uint32_t)input[source] << 16u;
        output[target++] = alphabet[(value >> 18u) & 0x3fu];
        output[target++] = alphabet[(value >> 12u) & 0x3fu];
    } else if (length - source == 2u) {
        const uint32_t value = ((uint32_t)input[source] << 16u) |
                               ((uint32_t)input[source + 1u] << 8u);
        output[target++] = alphabet[(value >> 18u) & 0x3fu];
        output[target++] = alphabet[(value >> 12u) & 0x3fu];
        output[target++] = alphabet[(value >> 6u) & 0x3fu];
    }
    output[target] = '\0';
    return target == required;
}

bool device_auth_build_signing_object(const char *nonce, const char *serial,
                                      char *output, size_t capacity,
                                      size_t *output_length)
{
    if (!device_auth_nonce_valid(nonce) || serial == NULL || output == NULL ||
        output_length == NULL) {
        return false;
    }
    const int length = snprintf(
        output, capacity,
        "{\"domain\":\"%s\",\"nonce\":\"%s\",\"serial\":\"%s\"}",
        WMP_DEVICE_AUTH_DOMAIN, nonce, serial);
    if (length < 0 || (size_t)length >= capacity) {
        return false;
    }
    *output_length = (size_t)length;
    return true;
}
