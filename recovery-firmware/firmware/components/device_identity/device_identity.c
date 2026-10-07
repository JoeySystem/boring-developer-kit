#include "device_identity.h"

#include <string.h>

#include "device_identity_backend.h"

#define DEVICE_AUTH_PRODUCT_ID "wired-macro-pad-v1"
#define DEVICE_AUTH_PUBLIC_KEY_ALGORITHM "rsa-3072"
#define DEVICE_AUTH_SIGNATURE_ALGORITHM "rsa-pss-sha256-salt32"

static device_identity_provider_t s_provider;
static device_identity_certificate_t s_certificate;
static bool s_ready;
static device_identity_diagnostics_t s_diagnostics;

void device_identity_record_failure(device_identity_failure_stage_t stage,
                                    int32_t detail)
{
    s_diagnostics.stage = stage;
    s_diagnostics.detail = detail;
}

static void diagnostics_clear(void)
{
    device_identity_record_failure(DEVICE_IDENTITY_FAILURE_NONE, 0);
}

static bool bounded_string(const char *value, size_t capacity)
{
    return value != NULL && value[0] != '\0' &&
           memchr(value, '\0', capacity) != NULL;
}

static bool base64url_string(const char *value, size_t capacity)
{
    if (!bounded_string(value, capacity)) {
        return false;
    }
    for (const unsigned char *cursor = (const unsigned char *)value;
         *cursor != '\0'; ++cursor) {
        const bool valid = (*cursor >= 'A' && *cursor <= 'Z') ||
                           (*cursor >= 'a' && *cursor <= 'z') ||
                           (*cursor >= '0' && *cursor <= '9') ||
                           *cursor == '-' || *cursor == '_';
        if (!valid) {
            return false;
        }
    }
    return true;
}

static bool identity_string(const char *value, size_t capacity)
{
    if (!bounded_string(value, capacity)) {
        return false;
    }
    for (const unsigned char *cursor = (const unsigned char *)value;
         *cursor != '\0'; ++cursor) {
        const bool valid = (*cursor >= 'A' && *cursor <= 'Z') ||
                           (*cursor >= 'a' && *cursor <= 'z') ||
                           (*cursor >= '0' && *cursor <= '9') ||
                           *cursor == '-' || *cursor == '_' ||
                           *cursor == '.' || *cursor == ':';
        if (!valid) {
            return false;
        }
    }
    return true;
}

static bool certificate_valid(const device_identity_certificate_t *certificate,
                              const char *product_id,
                              const char *hardware_id, const char *serial)
{
    return certificate != NULL && product_id != NULL && hardware_id != NULL &&
           serial != NULL && certificate->version == 1u &&
           identity_string(certificate->issuer_key_id,
                           sizeof(certificate->issuer_key_id)) &&
           identity_string(certificate->product_id,
                           sizeof(certificate->product_id)) &&
           identity_string(certificate->hardware_id,
                           sizeof(certificate->hardware_id)) &&
           identity_string(certificate->serial, sizeof(certificate->serial)) &&
           bounded_string(certificate->public_key_algorithm,
                          sizeof(certificate->public_key_algorithm)) &&
           base64url_string(certificate->public_key_spki,
                            sizeof(certificate->public_key_spki)) &&
           base64url_string(certificate->issuer_signature,
                            sizeof(certificate->issuer_signature)) &&
           bounded_string(certificate->signature_algorithm,
                          sizeof(certificate->signature_algorithm)) &&
           strcmp(certificate->product_id, product_id) == 0 &&
           strcmp(certificate->hardware_id, hardware_id) == 0 &&
           strcmp(certificate->serial, serial) == 0 &&
           strcmp(certificate->public_key_algorithm,
                  DEVICE_AUTH_PUBLIC_KEY_ALGORITHM) == 0 &&
           strcmp(certificate->signature_algorithm,
                  DEVICE_AUTH_SIGNATURE_ALGORITHM) == 0 &&
           strlen(certificate->issuer_signature) == 512u;
}

esp_err_t device_identity_init_with_provider(
    const device_identity_provider_t *provider, const char *product_id,
    const char *hardware_id, const char *serial)
{
    memset(&s_provider, 0, sizeof(s_provider));
    memset(&s_certificate, 0, sizeof(s_certificate));
    s_ready = false;
    diagnostics_clear();
    if (provider == NULL || provider->load == NULL ||
        provider->sign_digest == NULL || product_id == NULL ||
        hardware_id == NULL || serial == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    device_identity_certificate_t certificate = {0};
    const esp_err_t error = provider->load(&certificate, provider->context);
    if (error != ESP_OK) {
        if (s_diagnostics.stage == DEVICE_IDENTITY_FAILURE_NONE) {
            device_identity_record_failure(
                DEVICE_IDENTITY_FAILURE_PROVIDER_LOAD, error);
        }
        return error;
    }
    if (!certificate_valid(&certificate, product_id, hardware_id, serial)) {
        device_identity_record_failure(DEVICE_IDENTITY_FAILURE_BINDING,
                                       ESP_ERR_INVALID_RESPONSE);
        return ESP_ERR_INVALID_RESPONSE;
    }
    s_provider = *provider;
    s_certificate = certificate;
    s_ready = true;
    return ESP_OK;
}

esp_err_t device_identity_init(const char *hardware_id, const char *serial)
{
    diagnostics_clear();
    device_identity_provider_t provider = {0};
    const esp_err_t error = device_identity_esp_provider(&provider);
    if (error != ESP_OK) {
        return error;
    }
    return device_identity_init_with_provider(
        &provider, DEVICE_AUTH_PRODUCT_ID, hardware_id, serial);
}

bool device_identity_ready(void)
{
    return s_ready;
}

void device_identity_get_diagnostics(device_identity_diagnostics_t *diagnostics)
{
    if (diagnostics != NULL) {
        *diagnostics = s_diagnostics;
    }
}

const char *device_identity_failure_stage_name(
    device_identity_failure_stage_t stage)
{
    switch (stage) {
    case DEVICE_IDENTITY_FAILURE_NONE:
        return "none";
    case DEVICE_IDENTITY_FAILURE_PSA_CRYPTO_INIT:
        return "psa_crypto_init";
    case DEVICE_IDENTITY_FAILURE_USER_DATA_TLV:
        return "user_data_tlv";
    case DEVICE_IDENTITY_FAILURE_JSON_PARSE:
        return "json_parse";
    case DEVICE_IDENTITY_FAILURE_JSON_SHAPE:
        return "json_shape";
    case DEVICE_IDENTITY_FAILURE_DS_DATA_TLV:
        return "ds_data_tlv";
    case DEVICE_IDENTITY_FAILURE_DS_CONTEXT_TLV:
        return "ds_context_tlv";
    case DEVICE_IDENTITY_FAILURE_DS_CONTEXT_ASSEMBLY:
        return "ds_context_assembly";
    case DEVICE_IDENTITY_FAILURE_RSA_LENGTH:
        return "rsa_length";
    case DEVICE_IDENTITY_FAILURE_PROVIDER_LOAD:
        return "provider_load";
    case DEVICE_IDENTITY_FAILURE_BINDING:
        return "identity_binding";
    default:
        return "unknown";
    }
}

esp_err_t device_identity_get_certificate(
    device_identity_certificate_t *certificate)
{
    if (certificate == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        memset(certificate, 0, sizeof(*certificate));
        return ESP_ERR_NOT_FOUND;
    }
    *certificate = s_certificate;
    return ESP_OK;
}

esp_err_t device_identity_sign_digest(
    const uint8_t digest[32], uint8_t *signature, size_t capacity,
    size_t *signature_length)
{
    if (signature_length != NULL) {
        *signature_length = 0;
    }
    if (digest == NULL || signature == NULL || signature_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ready) {
        return ESP_ERR_NOT_FOUND;
    }
    if (capacity < DEVICE_IDENTITY_RSA_SIGNATURE_BYTES) {
        return ESP_ERR_INVALID_SIZE;
    }
    const esp_err_t error = s_provider.sign_digest(
        digest, signature, capacity, signature_length, s_provider.context);
    if (error != ESP_OK ||
        *signature_length != DEVICE_IDENTITY_RSA_SIGNATURE_BYTES) {
        memset(signature, 0, capacity);
        *signature_length = 0;
        return error != ESP_OK ? error : ESP_ERR_INVALID_RESPONSE;
    }
    return ESP_OK;
}
