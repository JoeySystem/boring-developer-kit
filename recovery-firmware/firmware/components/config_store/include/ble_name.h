#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Validate the complete UTF-8 byte sequence, excluding its terminating NUL. */
bool ble_name_valid(const char *name, size_t length);

#ifdef __cplusplus
}
#endif
