#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

bool device_auth_nonce_valid(const char *nonce);
bool device_auth_base64url_encode(const uint8_t *input, size_t length,
                                  char *output, size_t capacity);
bool device_auth_build_signing_object(const char *nonce, const char *serial,
                                      char *output, size_t capacity,
                                      size_t *output_length);
