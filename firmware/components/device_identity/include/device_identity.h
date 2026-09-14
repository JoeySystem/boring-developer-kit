#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DEVICE_IDENTITY_RSA_SIGNATURE_BYTES 384u

typedef enum {
    DEVICE_IDENTITY_FAILURE_NONE = 0,
    DEVICE_IDENTITY_FAILURE_PSA_CRYPTO_INIT,
    DEVICE_IDENTITY_FAILURE_USER_DATA_TLV,
    DEVICE_IDENTITY_FAILURE_JSON_PARSE,
    DEVICE_IDENTITY_FAILURE_JSON_SHAPE,
    DEVICE_IDENTITY_FAILURE_DS_DATA_TLV,
    DEVICE_IDENTITY_FAILURE_DS_CONTEXT_TLV,
    DEVICE_IDENTITY_FAILURE_DS_CONTEXT_ASSEMBLY,
    DEVICE_IDENTITY_FAILURE_RSA_LENGTH,
    DEVICE_IDENTITY_FAILURE_PROVIDER_LOAD,
    DEVICE_IDENTITY_FAILURE_BINDING,
} device_identity_failure_stage_t;

typedef struct {
    device_identity_failure_stage_t stage;
    int32_t detail;
} device_identity_diagnostics_t;

typedef struct {
    uint32_t version;
    char issuer_key_id[65];
    char product_id[65];
    char hardware_id[65];
    char serial[65];
    char public_key_algorithm[16];
    char public_key_spki[769];
    char issuer_signature[513];
    char signature_algorithm[40];
} device_identity_certificate_t;

typedef struct {
    esp_err_t (*load)(device_identity_certificate_t *certificate,
                      void *context);
    esp_err_t (*sign_digest)(const uint8_t digest[32], uint8_t *signature,
                             size_t capacity, size_t *signature_length,
                             void *context);
    void *context;
} device_identity_provider_t;

/* Initialize the product provider and bind the identity to this exact unit. */
esp_err_t device_identity_init(const char *hardware_id, const char *serial);

/* Host-test seam; production code should call device_identity_init(). */
esp_err_t device_identity_init_with_provider(
    const device_identity_provider_t *provider, const char *product_id,
    const char *hardware_id, const char *serial);

bool device_identity_ready(void);
void device_identity_get_diagnostics(device_identity_diagnostics_t *diagnostics);
const char *device_identity_failure_stage_name(
    device_identity_failure_stage_t stage);
esp_err_t device_identity_get_certificate(
    device_identity_certificate_t *certificate);
esp_err_t device_identity_sign_digest(
    const uint8_t digest[32], uint8_t *signature, size_t capacity,
    size_t *signature_length);

#ifdef __cplusplus
}
#endif
