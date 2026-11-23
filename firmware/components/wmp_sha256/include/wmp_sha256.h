#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WMP_SHA256_DIGEST_BYTES 32u

/*
 * Portable SHA-256 (FIPS 180-4) whose whole state lives in this struct.
 * It never allocates and cannot fail, so request fingerprints, firmware
 * update hashing and the config digest keep working when internal heap is
 * exhausted (the PSA driver mallocs DMA-capable internal RAM per operation).
 */
typedef struct {
    uint32_t state[8];
    uint64_t length;
    uint8_t block[64];
    size_t used;
} wmp_sha256_t;

void wmp_sha256_init(wmp_sha256_t *context);
void wmp_sha256_update(wmp_sha256_t *context, const void *data, size_t length);
void wmp_sha256_finish(wmp_sha256_t *context,
                       uint8_t digest[WMP_SHA256_DIGEST_BYTES]);
void wmp_sha256(const void *data, size_t length,
                uint8_t digest[WMP_SHA256_DIGEST_BYTES]);

#ifdef __cplusplus
}
#endif
