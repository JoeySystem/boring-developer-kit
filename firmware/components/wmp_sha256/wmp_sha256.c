#include "wmp_sha256.h"

#include <string.h>

static const uint32_t K[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u,
    0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u,
    0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
    0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
    0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au,
    0x5b9cca4fu, 0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
};

static uint32_t rotr(uint32_t value, unsigned bits)
{
    return (value >> bits) | (value << (32u - bits));
}

static void compress(uint32_t state[8], const uint8_t block[64])
{
    uint32_t w[64];
    for (unsigned i = 0; i < 16; ++i) {
        w[i] = ((uint32_t)block[i * 4] << 24) | ((uint32_t)block[i * 4 + 1] << 16) |
               ((uint32_t)block[i * 4 + 2] << 8) | (uint32_t)block[i * 4 + 3];
    }
    for (unsigned i = 16; i < 64; ++i) {
        const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
        const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
    for (unsigned i = 0; i < 64; ++i) {
        const uint32_t t1 = h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) +
                            ((e & f) ^ (~e & g)) + K[i] + w[i];
        const uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) +
                            ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

void wmp_sha256_init(wmp_sha256_t *context)
{
    static const uint32_t initial[8] = {
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u,
    };
    memcpy(context->state, initial, sizeof(initial));
    context->length = 0;
    context->used = 0;
}

void wmp_sha256_update(wmp_sha256_t *context, const void *data, size_t length)
{
    const uint8_t *bytes = data;
    context->length += length;
    while (length > 0) {
        const size_t take = sizeof(context->block) - context->used < length
                                ? sizeof(context->block) - context->used
                                : length;
        memcpy(context->block + context->used, bytes, take);
        context->used += take;
        bytes += take;
        length -= take;
        if (context->used == sizeof(context->block)) {
            compress(context->state, context->block);
            context->used = 0;
        }
    }
}

void wmp_sha256_finish(wmp_sha256_t *context,
                       uint8_t digest[WMP_SHA256_DIGEST_BYTES])
{
    const uint64_t bits = context->length * 8u;
    static const uint8_t pad = 0x80;
    static const uint8_t zero = 0;
    wmp_sha256_update(context, &pad, 1);
    while (context->used != 56) {
        wmp_sha256_update(context, &zero, 1);
    }
    uint8_t length_be[8];
    for (unsigned i = 0; i < 8; ++i) {
        length_be[i] = (uint8_t)(bits >> (56 - 8 * i));
    }
    wmp_sha256_update(context, length_be, sizeof(length_be));
    for (unsigned i = 0; i < 8; ++i) {
        digest[i * 4] = (uint8_t)(context->state[i] >> 24);
        digest[i * 4 + 1] = (uint8_t)(context->state[i] >> 16);
        digest[i * 4 + 2] = (uint8_t)(context->state[i] >> 8);
        digest[i * 4 + 3] = (uint8_t)context->state[i];
    }
    memset(context, 0, sizeof(*context));
}

void wmp_sha256(const void *data, size_t length,
                uint8_t digest[WMP_SHA256_DIGEST_BYTES])
{
    wmp_sha256_t context;
    wmp_sha256_init(&context);
    wmp_sha256_update(&context, data, length);
    wmp_sha256_finish(&context, digest);
}
