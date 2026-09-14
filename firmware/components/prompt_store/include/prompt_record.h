#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PROMPT_STORE_SLOT_COUNT 12u
#define PROMPT_STORE_NAME_MAX_BYTES 48u
#define PROMPT_STORE_BODY_MAX_BYTES 4096u
#define PROMPT_RECORD_MAX_BYTES \
    (4u + PROMPT_STORE_NAME_MAX_BYTES + PROMPT_STORE_BODY_MAX_BYTES)

#ifdef __cplusplus
extern "C" {
#endif

/** Validate and encode one name/body pair. Returns zero when invalid. */
size_t prompt_record_encode(const char *name, const char *body,
                            uint8_t *output, size_t capacity);

/** Decode one complete record into NUL-terminated output buffers. */
bool prompt_record_decode(const uint8_t *record, size_t record_length,
                          char *name, size_t name_capacity,
                          char *body, size_t body_capacity,
                          size_t *body_length);

#ifdef __cplusplus
}
#endif
