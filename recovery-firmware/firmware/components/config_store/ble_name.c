#include "ble_name.h"

#include <stdint.h>

#include "protocol_contract.h"

static bool white_space(uint32_t codepoint)
{
    return (codepoint >= 0x09 && codepoint <= 0x0d) || codepoint == 0x20 ||
           codepoint == 0x85 || codepoint == 0xa0 || codepoint == 0x1680 ||
           (codepoint >= 0x2000 && codepoint <= 0x200a) ||
           codepoint == 0x2028 || codepoint == 0x2029 || codepoint == 0x202f ||
           codepoint == 0x205f || codepoint == 0x3000;
}

bool ble_name_valid(const char *name, size_t length)
{
    if (name == NULL || length == 0 || length > WMP_BLE_NAME_MAX_UTF8_BYTES) {
        return false;
    }
    const unsigned char *bytes = (const unsigned char *)name;
    for (size_t index = 0; index < length;) {
        const size_t start = index;
        uint32_t codepoint = bytes[index++];
        size_t continuation = 0;
        uint32_t minimum = 0;
        if (codepoint <= 0x7f) {
            /* ASCII is already decoded. */
        } else if (codepoint >= 0xc2 && codepoint <= 0xdf) {
            codepoint &= 0x1f;
            continuation = 1;
            minimum = 0x80;
        } else if (codepoint >= 0xe0 && codepoint <= 0xef) {
            codepoint &= 0x0f;
            continuation = 2;
            minimum = 0x800;
        } else if (codepoint >= 0xf0 && codepoint <= 0xf4) {
            codepoint &= 0x07;
            continuation = 3;
            minimum = 0x10000;
        } else {
            return false;
        }
        if (continuation > length - index) {
            return false;
        }
        for (size_t count = 0; count < continuation; ++count) {
            const unsigned char next = bytes[index++];
            if ((next & 0xc0) != 0x80) {
                return false;
            }
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        if (codepoint < minimum || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff) || codepoint < 0x20 ||
            (codepoint >= 0x7f && codepoint <= 0x9f) ||
            codepoint == 0x2028 || codepoint == 0x2029 ||
            ((start == 0 || index == length) && white_space(codepoint))) {
            return false;
        }
    }
    return true;
}
