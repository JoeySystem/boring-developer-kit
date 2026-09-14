#include "device_identity_backend.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_log.h"
#include "esp_secure_cert_read.h"
#include "esp_secure_cert_tlv_read.h"
#include "psa/crypto.h"
#include "psa_crypto_driver_esp_rsa_ds.h"

static esp_ds_data_ctx_t *s_ds_context;
static const char *TAG = "device_identity";

static esp_err_t validate_tlv_length(
    esp_secure_cert_tlv_type_t type, uint32_t expected_length,
    device_identity_failure_stage_t failure_stage)
{
    esp_secure_cert_tlv_config_t config = {
        .type = type,
        .subtype = ESP_SECURE_CERT_SUBTYPE_0,
    };
    esp_secure_cert_tlv_info_t info = {0};
    const esp_err_t error = esp_secure_cert_get_tlv_info(&config, &info);
    if (error != ESP_OK || info.data == NULL) {
        device_identity_record_failure(
            failure_stage, error == ESP_OK ? ESP_ERR_NOT_FOUND : error);
        ESP_LOGE(TAG, "stage=%s esp_err=%s (0x%x)",
                 device_identity_failure_stage_name(failure_stage),
                 esp_err_to_name(error == ESP_OK ? ESP_ERR_NOT_FOUND : error),
                 (unsigned)(error == ESP_OK ? ESP_ERR_NOT_FOUND : error));
        (void)esp_secure_cert_free_tlv_info(&info);
        return error == ESP_OK ? ESP_ERR_NOT_FOUND : error;
    }
    if (info.length != expected_length) {
        device_identity_record_failure(failure_stage, (int32_t)info.length);
        ESP_LOGE(TAG, "stage=%s length=%lu expected=%lu",
                 device_identity_failure_stage_name(failure_stage),
                 (unsigned long)info.length, (unsigned long)expected_length);
        (void)esp_secure_cert_free_tlv_info(&info);
        return ESP_ERR_INVALID_SIZE;
    }
    (void)esp_secure_cert_free_tlv_info(&info);
    return ESP_OK;
}

static bool copy_json_string(const cJSON *object, const char *name,
                             char *output, size_t capacity)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (!cJSON_IsString(value) || value->valuestring == NULL ||
        value->valuestring[0] == '\0' ||
        strlen(value->valuestring) >= capacity) {
        return false;
    }
    memcpy(output, value->valuestring, strlen(value->valuestring) + 1u);
    return true;
}

static esp_err_t esp_provider_load(device_identity_certificate_t *certificate,
                                   void *context)
{
    (void)context;
    if (certificate == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_secure_cert_tlv_config_t config = {
        .type = ESP_SECURE_CERT_USER_DATA_1,
        .subtype = ESP_SECURE_CERT_SUBTYPE_0,
    };
    esp_secure_cert_tlv_info_t info = {0};
    const esp_err_t read_error = esp_secure_cert_get_tlv_info(&config, &info);
    if (read_error != ESP_OK || info.data == NULL || info.length == 0u) {
        const esp_err_t failure =
            read_error == ESP_OK ? ESP_ERR_NOT_FOUND : read_error;
        device_identity_record_failure(DEVICE_IDENTITY_FAILURE_USER_DATA_TLV,
                                       failure);
        ESP_LOGE(TAG, "stage=user_data_tlv esp_err=%s (0x%x)",
                 esp_err_to_name(failure), (unsigned)failure);
        (void)esp_secure_cert_free_tlv_info(&info);
        return failure;
    }

    cJSON *root = cJSON_ParseWithLength(info.data, info.length);
    if (root == NULL) {
        device_identity_record_failure(DEVICE_IDENTITY_FAILURE_JSON_PARSE,
                                       ESP_ERR_INVALID_RESPONSE);
        ESP_LOGE(TAG, "stage=json_parse length=%lu",
                 (unsigned long)info.length);
        (void)esp_secure_cert_free_tlv_info(&info);
        return ESP_ERR_INVALID_RESPONSE;
    }
    const cJSON *public_certificate = root != NULL
                                          ? cJSON_GetObjectItemCaseSensitive(
                                                root, "certificate")
                                          : NULL;
    const cJSON *version = cJSON_IsObject(public_certificate)
                               ? cJSON_GetObjectItemCaseSensitive(
                                     public_certificate, "version")
                               : NULL;
    memset(certificate, 0, sizeof(*certificate));
    const bool valid = cJSON_IsObject(root) && cJSON_GetArraySize(root) == 3 &&
                       cJSON_IsObject(public_certificate) &&
                       cJSON_GetArraySize(public_certificate) == 7 &&
                       cJSON_IsNumber(version) && version->valuedouble == 1 &&
                       copy_json_string(public_certificate, "issuer_key_id",
                                        certificate->issuer_key_id,
                                        sizeof(certificate->issuer_key_id)) &&
                       copy_json_string(public_certificate, "product_id",
                                        certificate->product_id,
                                        sizeof(certificate->product_id)) &&
                       copy_json_string(public_certificate, "hardware_id",
                                        certificate->hardware_id,
                                        sizeof(certificate->hardware_id)) &&
                       copy_json_string(public_certificate, "serial",
                                        certificate->serial,
                                        sizeof(certificate->serial)) &&
                       copy_json_string(public_certificate,
                                        "public_key_algorithm",
                                        certificate->public_key_algorithm,
                                        sizeof(certificate->public_key_algorithm)) &&
                       copy_json_string(public_certificate, "public_key_spki",
                                        certificate->public_key_spki,
                                        sizeof(certificate->public_key_spki)) &&
                       copy_json_string(root, "issuer_signature",
                                        certificate->issuer_signature,
                                        sizeof(certificate->issuer_signature)) &&
                       copy_json_string(root, "signature_algorithm",
                                        certificate->signature_algorithm,
                                        sizeof(certificate->signature_algorithm));
    if (valid) {
        certificate->version = 1u;
    }
    cJSON_Delete(root);
    (void)esp_secure_cert_free_tlv_info(&info);
    if (!valid) {
        device_identity_record_failure(DEVICE_IDENTITY_FAILURE_JSON_SHAPE,
                                       ESP_ERR_INVALID_RESPONSE);
        ESP_LOGE(TAG, "stage=json_shape");
        memset(certificate, 0, sizeof(*certificate));
        return ESP_ERR_INVALID_RESPONSE;
    }

    esp_err_t error = validate_tlv_length(
        ESP_SECURE_CERT_DS_DATA_TLV, sizeof(esp_ds_data_t),
        DEVICE_IDENTITY_FAILURE_DS_DATA_TLV);
    if (error != ESP_OK) {
        memset(certificate, 0, sizeof(*certificate));
        return error;
    }
    error = validate_tlv_length(
        ESP_SECURE_CERT_DS_CONTEXT_TLV, sizeof(esp_ds_data_ctx_t),
        DEVICE_IDENTITY_FAILURE_DS_CONTEXT_TLV);
    if (error != ESP_OK) {
        memset(certificate, 0, sizeof(*certificate));
        return error;
    }
    if (s_ds_context != NULL) {
        esp_secure_cert_free_ds_ctx(s_ds_context);
        s_ds_context = NULL;
    }
    s_ds_context = esp_secure_cert_get_ds_ctx();
    if (s_ds_context == NULL) {
        device_identity_record_failure(
            DEVICE_IDENTITY_FAILURE_DS_CONTEXT_ASSEMBLY, ESP_FAIL);
        ESP_LOGE(TAG, "stage=ds_context_assembly");
        memset(certificate, 0, sizeof(*certificate));
        return ESP_FAIL;
    }
    if (s_ds_context->rsa_length_bits != 3072u) {
        device_identity_record_failure(DEVICE_IDENTITY_FAILURE_RSA_LENGTH,
                                       s_ds_context->rsa_length_bits);
        ESP_LOGE(TAG, "stage=rsa_length bits=%u expected=3072",
                 (unsigned)s_ds_context->rsa_length_bits);
        esp_secure_cert_free_ds_ctx(s_ds_context);
        s_ds_context = NULL;
        memset(certificate, 0, sizeof(*certificate));
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static esp_err_t esp_provider_sign(const uint8_t digest[32], uint8_t *signature,
                                   size_t capacity, size_t *signature_length,
                                   void *context)
{
    (void)context;
    if (s_ds_context == NULL) {
        return ESP_ERR_NOT_FOUND;
    }
    const psa_algorithm_t algorithm = PSA_ALG_RSA_PSS(PSA_ALG_SHA_256);
    esp_rsa_ds_opaque_key_t opaque_key = {
        .ds_data_ctx = s_ds_context,
    };
    psa_key_attributes_t attributes = PSA_KEY_ATTRIBUTES_INIT;
    psa_set_key_type(&attributes, PSA_KEY_TYPE_RSA_KEY_PAIR);
    psa_set_key_bits(&attributes, s_ds_context->rsa_length_bits);
    psa_set_key_usage_flags(&attributes, PSA_KEY_USAGE_SIGN_HASH);
    psa_set_key_algorithm(&attributes, algorithm);
    psa_set_key_lifetime(&attributes, PSA_KEY_LIFETIME_ESP_RSA_DS_VOLATILE);

    psa_key_id_t key_id = 0;
    psa_status_t status = psa_import_key(
        &attributes, (const uint8_t *)&opaque_key, sizeof(opaque_key), &key_id);
    psa_reset_key_attributes(&attributes);
    if (status != PSA_SUCCESS) {
        return ESP_FAIL;
    }
    status = psa_sign_hash(key_id, algorithm, digest, 32u, signature, capacity,
                           signature_length);
    const psa_status_t destroy_status = psa_destroy_key(key_id);
    return status == PSA_SUCCESS && destroy_status == PSA_SUCCESS
               ? ESP_OK
               : ESP_FAIL;
}

esp_err_t device_identity_esp_provider(device_identity_provider_t *provider)
{
    if (provider == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    const psa_status_t status = psa_crypto_init();
    if (status != PSA_SUCCESS) {
        device_identity_record_failure(
            DEVICE_IDENTITY_FAILURE_PSA_CRYPTO_INIT, status);
        ESP_LOGE(TAG, "stage=psa_crypto_init psa_status=%ld", (long)status);
        return ESP_FAIL;
    }
    *provider = (device_identity_provider_t){
        .load = esp_provider_load,
        .sign_digest = esp_provider_sign,
        .context = NULL,
    };
    return ESP_OK;
}
