#include "firmware_update_protocol.h"

#include <stdio.h>
#include <string.h>

#include "esp_ota_ops.h"
#include "firmware_update.h"
#include "mbedtls/base64.h"
#include "protocol_contract.h"

static uint8_t s_chunk[FIRMWARE_UPDATE_MAX_CHUNK_BYTES];

static bool read_uint32_field(const cJSON *object, const char *name,
                              uint32_t *output)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, name);
    if (!cJSON_IsNumber(value) || value->valuedouble != value->valueint ||
        value->valuedouble < 0 || value->valuedouble > UINT32_MAX) {
        return false;
    }
    *output = (uint32_t)value->valuedouble;
    return true;
}

static void send_update_error(
    const firmware_update_protocol_callbacks_t *callbacks,
    uint32_t request_id, const char *command, esp_err_t error,
    const char *reason)
{
    if (error == ESP_ERR_INVALID_STATE) {
        callbacks->send_nack(request_id, command, WMP_ERROR_BUSY, "BUSY", reason);
    } else if (error == ESP_ERR_NOT_FOUND || error == ESP_ERR_NOT_SUPPORTED) {
        callbacks->send_nack(request_id, command, WMP_ERROR_NOT_FOUND,
                             "NOT_FOUND", reason);
    } else if (error == FIRMWARE_UPDATE_ERR_HARDWARE_MISMATCH) {
        callbacks->send_nack(request_id, command, WMP_ERROR_HARDWARE_MISMATCH,
                             "HARDWARE_MISMATCH", reason);
    } else if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_INVALID_CRC ||
               error == ESP_ERR_INVALID_SIZE || error == ESP_ERR_INVALID_VERSION ||
               error == ESP_ERR_OTA_VALIDATE_FAILED) {
        callbacks->send_nack(request_id, command, WMP_ERROR_VALIDATION_FAILED,
                             "VALIDATION_FAILED", reason);
    } else {
        callbacks->send_nack(request_id, command, WMP_ERROR_STORAGE_FAILURE,
                             "STORAGE_FAILURE", reason);
    }
}

static const char *state_name(firmware_update_state_t state)
{
    switch (state) {
    case FIRMWARE_UPDATE_RECEIVING:
        return "RECEIVING";
    case FIRMWARE_UPDATE_FINALIZING:
        return "FINALIZING";
    case FIRMWARE_UPDATE_READY_TO_REBOOT:
        return "READY_TO_REBOOT";
    case FIRMWARE_UPDATE_FAILED:
        return "FAILED";
    case FIRMWARE_UPDATE_IDLE:
    default:
        return "IDLE";
    }
}

static const char *boot_stage_name(firmware_boot_stage_t stage)
{
    switch (stage) {
    case FIRMWARE_BOOT_STAGE_SYSTEM_READY:
        return "SYSTEM_READY";
    case FIRMWARE_BOOT_STAGE_APP_ENTRY:
        return "APP_ENTRY";
    case FIRMWARE_BOOT_STAGE_BOARD_READY:
        return "BOARD_READY";
    case FIRMWARE_BOOT_STAGE_CONFIG_READY:
        return "CONFIG_READY";
    case FIRMWARE_BOOT_STAGE_ACTION_READY:
        return "ACTION_READY";
    case FIRMWARE_BOOT_STAGE_UPDATE_READY:
        return "UPDATE_READY";
    case FIRMWARE_BOOT_STAGE_USB_READY:
        return "USB_READY";
    case FIRMWARE_BOOT_STAGE_CODEX_READY:
        return "CODEX_READY";
    case FIRMWARE_BOOT_STAGE_CONFIRMED:
        return "CONFIRMED";
    case FIRMWARE_BOOT_STAGE_MAIN_LOOP:
        return "MAIN_LOOP";
    case FIRMWARE_BOOT_STAGE_UNKNOWN:
    default:
        return "UNKNOWN";
    }
}

void firmware_update_protocol_add_status(cJSON *parent)
{
    firmware_update_status_t status;
    firmware_update_get_status(&status);
    cJSON *update = cJSON_AddObjectToObject(parent, "firmware_update");
    cJSON_AddStringToObject(update, "state", state_name(status.state));
    cJSON_AddNumberToObject(update, "expected_size", status.expected_size);
    cJSON_AddNumberToObject(update, "received_size", status.received_size);
    cJSON_AddNumberToObject(update, "target_capacity", status.target_capacity);
    cJSON_AddStringToObject(update, "expected_sha256", status.expected_sha256);
    cJSON_AddStringToObject(update, "expected_version", status.expected_version);
    cJSON_AddStringToObject(update, "running_version", status.running_version);
    cJSON_AddStringToObject(update, "running_partition", status.running_partition);
    cJSON_AddStringToObject(update, "target_partition", status.target_partition);
    cJSON_AddBoolToObject(update, "rollback_pending", status.rollback_pending);
    cJSON_AddBoolToObject(update, "reboot_pending", status.reboot_pending);
    cJSON_AddNumberToObject(update, "current_boot_sequence",
                            status.current_boot_sequence);
    cJSON_AddStringToObject(update, "current_boot_stage",
                            boot_stage_name(status.current_boot_stage));
    cJSON_AddNumberToObject(update, "current_confirm_error",
                            status.current_confirm_error);
    cJSON_AddNumberToObject(update, "previous_boot_sequence",
                            status.previous_boot_sequence);
    cJSON_AddStringToObject(update, "previous_boot_stage",
                            boot_stage_name(status.previous_boot_stage));
    cJSON_AddNumberToObject(update, "previous_reset_reason",
                            status.previous_reset_reason);
    cJSON_AddNumberToObject(update, "previous_confirm_error",
                            status.previous_confirm_error);
}

static void send_status(uint32_t request_id,
                        const firmware_update_protocol_callbacks_t *callbacks)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "command", "FW_STATUS");
    cJSON *result = cJSON_AddObjectToObject(root, "result");
    firmware_update_protocol_add_status(result);
    char *json = cJSON_PrintUnformatted(root);
    if (json == NULL || callbacks->send_ack(request_id, json) != ESP_OK) {
        callbacks->send_nack(request_id, "FW_STATUS", WMP_ERROR_INTERNAL,
                             "INTERNAL", "could not encode response");
    }
    cJSON_free(json);
    cJSON_Delete(root);
}

bool firmware_update_protocol_handle(
    uint8_t message_type, uint32_t request_id, const cJSON *request,
    const firmware_update_protocol_callbacks_t *callbacks)
{
    if (callbacks == NULL || callbacks->send_ack == NULL ||
        callbacks->send_nack == NULL) {
        return false;
    }
    char response[320];
    switch (message_type) {
    case WMP_MSG_FW_BEGIN: {
        const cJSON *product_id =
            cJSON_GetObjectItemCaseSensitive(request, "product_id");
        const cJSON *hardware_id =
            cJSON_GetObjectItemCaseSensitive(request, "hardware_id");
        const cJSON *version =
            cJSON_GetObjectItemCaseSensitive(request, "version");
        const cJSON *sha256 =
            cJSON_GetObjectItemCaseSensitive(request, "sha256");
        uint32_t image_size = 0;
        if (!cJSON_IsString(product_id) || !cJSON_IsString(hardware_id) ||
            !cJSON_IsString(version) || !cJSON_IsString(sha256) ||
            !read_uint32_field(request, "size", &image_size)) {
            callbacks->send_nack(
                request_id, "FW_BEGIN", WMP_ERROR_VALIDATION_FAILED,
                "VALIDATION_FAILED",
                "product_id, hardware_id, version, size, and sha256 are required");
            return true;
        }
        char reason[160] = "firmware update could not start";
        const esp_err_t error = firmware_update_begin(
            product_id->valuestring, hardware_id->valuestring,
            version->valuestring, image_size, sha256->valuestring, reason,
            sizeof(reason));
        if (error != ESP_OK) {
            send_update_error(callbacks, request_id, "FW_BEGIN", error, reason);
            return true;
        }
        firmware_update_status_t status;
        firmware_update_get_status(&status);
        snprintf(response, sizeof(response),
                 "{\"command\":\"FW_BEGIN\",\"result\":{\"state\":\"RECEIVING\",\"offset\":%u,\"chunk_bytes\":%u,\"target_partition\":\"%s\"}}",
                 (unsigned)status.received_size,
                 (unsigned)FIRMWARE_UPDATE_MAX_CHUNK_BYTES,
                 status.target_partition);
        (void)callbacks->send_ack(request_id, response);
        return true;
    }
    case WMP_MSG_FW_DATA: {
        uint32_t offset = 0;
        const cJSON *encoded =
            cJSON_GetObjectItemCaseSensitive(request, "data");
        if (!read_uint32_field(request, "offset", &offset) ||
            !cJSON_IsString(encoded)) {
            callbacks->send_nack(request_id, "FW_DATA",
                                 WMP_ERROR_VALIDATION_FAILED,
                                 "VALIDATION_FAILED",
                                 "offset and base64 data are required");
            return true;
        }
        const size_t encoded_length = strlen(encoded->valuestring);
        if (encoded_length == 0) {
            callbacks->send_nack(request_id, "FW_DATA",
                                 WMP_ERROR_VALIDATION_FAILED,
                                 "VALIDATION_FAILED", "firmware chunk is empty");
            return true;
        }
        if (encoded_length >
            ((FIRMWARE_UPDATE_MAX_CHUNK_BYTES + 2u) / 3u) * 4u) {
            callbacks->send_nack(request_id, "FW_DATA",
                                 WMP_ERROR_PAYLOAD_TOO_LARGE,
                                 "PAYLOAD_TOO_LARGE",
                                 "firmware chunk exceeds device limit");
            return true;
        }
        size_t decoded_length = 0;
        if (mbedtls_base64_decode(s_chunk, sizeof(s_chunk), &decoded_length,
                                  (const unsigned char *)encoded->valuestring,
                                  encoded_length) != 0 ||
            decoded_length == 0) {
            callbacks->send_nack(request_id, "FW_DATA",
                                 WMP_ERROR_VALIDATION_FAILED,
                                 "VALIDATION_FAILED",
                                 "firmware data is not valid base64");
            return true;
        }
        char reason[160] = "firmware chunk could not be written";
        const esp_err_t error = firmware_update_write(
            offset, s_chunk, decoded_length, reason, sizeof(reason));
        if (error != ESP_OK) {
            send_update_error(callbacks, request_id, "FW_DATA", error, reason);
            return true;
        }
        size_t received_size = 0;
        size_t expected_size = 0;
        firmware_update_get_progress(&received_size, &expected_size);
        snprintf(response, sizeof(response),
                 "{\"command\":\"FW_DATA\",\"result\":{\"state\":\"RECEIVING\",\"received_size\":%u,\"expected_size\":%u}}",
                 (unsigned)received_size, (unsigned)expected_size);
        (void)callbacks->send_ack(request_id, response);
        return true;
    }
    case WMP_MSG_FW_STATUS:
        send_status(request_id, callbacks);
        return true;
    case WMP_MSG_FW_END: {
        char reason[160] = "firmware update could not be finalized";
        const esp_err_t error = firmware_update_finish(reason, sizeof(reason));
        if (error != ESP_OK) {
            send_update_error(callbacks, request_id, "FW_END", error, reason);
            return true;
        }
        firmware_update_status_t status;
        firmware_update_get_status(&status);
        snprintf(response, sizeof(response),
                 "{\"command\":\"FW_END\",\"result\":{\"state\":\"FINALIZING\",\"version\":\"%s\",\"reboot_scheduled\":false}}",
                 status.expected_version);
        (void)callbacks->send_ack(request_id, response);
        return true;
    }
    case WMP_MSG_FW_ABORT: {
        char reason[160] = "firmware update could not be aborted";
        const esp_err_t error = firmware_update_abort(reason, sizeof(reason));
        if (error != ESP_OK) {
            send_update_error(callbacks, request_id, "FW_ABORT", error, reason);
            return true;
        }
        (void)callbacks->send_ack(
            request_id,
            "{\"command\":\"FW_ABORT\",\"result\":{\"state\":\"IDLE\"}}");
        return true;
    }
    default:
        return false;
    }
}
