#include "screen_icon_protocol.h"

#include <math.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include "mbedtls/base64.h"
#include "protocol_contract.h"
#include "screen_icon_store.h"

typedef struct {
    uint8_t *pixels;
    uint32_t id;
    uint32_t base_revision;
    uint32_t next_offset;
    uint32_t last_activity_ms;
} screen_icon_upload_t;

static screen_icon_upload_t s_upload;
static atomic_bool s_upload_active;
/* Not reset on HELLO/transport changes: an old connection's token cannot match
 * the next upload in this boot. No persistent upload survives reboot. */
static uint32_t s_next_upload_id = 1;
static uint32_t s_aborted_id;
static uint32_t s_expired_id;
static uint8_t s_chunk[WMP_SCREEN_ICON_MAX_CHUNK_BYTES];
static unsigned char s_encoded[((WMP_SCREEN_ICON_MAX_CHUNK_BYTES + 2) / 3) * 4 + 1];

static void release_upload(void)
{
    free(s_upload.pixels);
    memset(&s_upload, 0, sizeof(s_upload));
    atomic_store(&s_upload_active, false);
}

void screen_icon_protocol_reset_session(void)
{
    release_upload();
    s_aborted_id = 0;
    s_expired_id = 0;
}

void screen_icon_protocol_poll(uint32_t now_ms)
{
    if (s_upload.pixels != NULL &&
        (uint32_t)(now_ms - s_upload.last_activity_ms) >=
            WMP_SCREEN_ICON_SESSION_TIMEOUT_MS) {
        s_expired_id = s_upload.id;
        release_upload();
    }
}

bool screen_icon_protocol_active(void)
{
    return atomic_load(&s_upload_active);
}

const char *screen_icon_protocol_conflicting_command(uint8_t type)
{
    if (!screen_icon_protocol_active()) return NULL;
    switch (type) {
    case WMP_MSG_SET_CONFIG: return "SET_CONFIG";
    case WMP_MSG_SET_PLATFORM: return "SET_PLATFORM";
    case WMP_MSG_SET_PROMPT: return "SET_PROMPT";
    case WMP_MSG_DELETE_PROMPT: return "DELETE_PROMPT";
    case WMP_MSG_CALIBRATION_START: return "CALIBRATION_START";
    case WMP_MSG_FACTORY_DEFAULT: return "FACTORY_DEFAULT";
    case WMP_MSG_FW_BEGIN: return "FW_BEGIN";
    default: return NULL;
    }
}

static bool uint_field(const cJSON *request, const char *name, uint32_t *out)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(request, name);
    if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) ||
        v->valuedouble < 0 || v->valuedouble > UINT32_MAX ||
        floor(v->valuedouble) != v->valuedouble) return false;
    *out = (uint32_t)v->valuedouble;
    return true;
}

static bool string_field(const cJSON *request, const char *name, const char *want)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(request, name);
    return cJSON_IsString(v) && v->valuestring != NULL &&
           strcmp(v->valuestring, want) == 0;
}

static void respond_json(screen_icon_response_fn respond, uint8_t type,
                         uint32_t id, cJSON *root)
{
    char *json = cJSON_PrintUnformatted(root);
    if (json != NULL) respond(type, id, json);
    cJSON_free(json);
    cJSON_Delete(root);
}

static void error_response(screen_icon_response_fn respond, uint32_t id,
                           const char *command, uint16_t code,
                           const char *name, const char *message)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "command", command);
    cJSON *error = cJSON_AddObjectToObject(root, "error");
    cJSON_AddNumberToObject(error, "code", code);
    cJSON_AddStringToObject(error, "name", name);
    cJSON_AddStringToObject(error, "message", message);
    cJSON *details = cJSON_AddObjectToObject(error, "details");
    if (code == WMP_ERROR_GENERATION_CONFLICT) {
        screen_icon_metadata_t metadata;
        screen_icon_store_get_metadata(&metadata);
        cJSON_AddNumberToObject(details, "current_revision", metadata.revision);
    }
    respond_json(respond, WMP_MSG_NACK, id, root);
}

static void store_error(screen_icon_response_fn respond, uint32_t id,
                        const char *command, esp_err_t error)
{
    uint16_t code = WMP_ERROR_STORAGE_FAILURE;
    const char *name = "STORAGE_FAILURE";
    if (error == ESP_ERR_INVALID_VERSION) {
        code = WMP_ERROR_GENERATION_CONFLICT; name = "GENERATION_CONFLICT";
    } else if (error == ESP_ERR_NOT_FOUND) {
        code = WMP_ERROR_NOT_FOUND; name = "NOT_FOUND";
    } else if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_INVALID_SIZE) {
        code = WMP_ERROR_VALIDATION_FAILED; name = "VALIDATION_FAILED";
    }
    error_response(respond, id, command, code, name, "screen icon operation failed");
}

static void add_format(cJSON *object)
{
    cJSON_AddStringToObject(object, "target", WMP_SCREEN_ICON_TARGET);
    cJSON_AddStringToObject(object, "format", WMP_SCREEN_ICON_FORMAT);
    cJSON_AddNumberToObject(object, "width", WMP_SCREEN_ICON_WIDTH);
    cJSON_AddNumberToObject(object, "height", WMP_SCREEN_ICON_HEIGHT);
}

void screen_icon_protocol_add_capability(cJSON *result, bool supported)
{
    cJSON *features = cJSON_GetObjectItemCaseSensitive(result, "features");
    cJSON_AddBoolToObject(features, "custom_home_icon", supported);
    if (!supported) return;
    cJSON *icon = cJSON_AddObjectToObject(result, "screen_icon");
    add_format(icon);
    cJSON_AddNumberToObject(icon, "version", WMP_SCREEN_ICON_VERSION);
    cJSON_AddNumberToObject(icon, "total_bytes", WMP_SCREEN_ICON_TOTAL_BYTES);
    cJSON_AddNumberToObject(icon, "max_chunk_bytes", WMP_SCREEN_ICON_MAX_CHUNK_BYTES);
    cJSON_AddNumberToObject(icon, "session_timeout_ms", WMP_SCREEN_ICON_SESSION_TIMEOUT_MS);
}

static cJSON *response_root(const char *command)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "command", command);
    cJSON_AddObjectToObject(root, "result");
    return root;
}

static void metadata_response(screen_icon_response_fn respond, uint32_t id,
                              const char *command)
{
    screen_icon_metadata_t metadata;
    screen_icon_store_get_metadata(&metadata);
    cJSON *root = response_root(command);
    cJSON *result = cJSON_GetObjectItemCaseSensitive(root, "result");
    add_format(result);
    cJSON_AddNumberToObject(result, "revision", metadata.revision);
    cJSON_AddStringToObject(result, "source", metadata.custom ? "custom" : "default");
    cJSON_AddNumberToObject(result, "total_bytes",
                            metadata.custom ? WMP_SCREEN_ICON_TOTAL_BYTES : 0);
    respond_json(respond, WMP_MSG_ACK, id, root);
}

static bool decode_chunk(const cJSON *request, size_t *length)
{
    const cJSON *data = cJSON_GetObjectItemCaseSensitive(request, "data");
    if (!cJSON_IsString(data) || data->valuestring == NULL) return false;
    const size_t encoded_length = strlen(data->valuestring);
    if (encoded_length == 0 || encoded_length >= sizeof(s_encoded) ||
        mbedtls_base64_decode(s_chunk, sizeof(s_chunk), length,
            (const unsigned char *)data->valuestring, encoded_length) != 0 ||
        *length == 0) return false;
    /* Reject whitespace, missing padding and noncanonical trailing bits. */
    size_t canonical_length = 0;
    return mbedtls_base64_encode(s_encoded, sizeof(s_encoded), &canonical_length,
                                  s_chunk, *length) == 0 &&
           canonical_length == encoded_length &&
           memcmp(s_encoded, data->valuestring, encoded_length) == 0;
}

void screen_icon_protocol_handle(uint8_t type, uint32_t request_id,
                                const cJSON *request, uint32_t now_ms,
                                bool supported, bool session_ready,
                                bool maintenance_busy,
                                screen_icon_response_fn respond)
{
    static const char *const commands[] = {
        "SCREEN_ICON_GET", "SCREEN_ICON_READ", "SCREEN_ICON_BEGIN",
        "SCREEN_ICON_DATA", "SCREEN_ICON_COMMIT", "SCREEN_ICON_ABORT", "SCREEN_ICON_RESET",
    };
    if (type < WMP_MSG_SCREEN_ICON_GET || type > WMP_MSG_SCREEN_ICON_RESET ||
        respond == NULL) return;
    const char *command = commands[type - WMP_MSG_SCREEN_ICON_GET];
    screen_icon_protocol_poll(now_ms);
    if (!supported) {
        error_response(respond, request_id, command, WMP_ERROR_NOT_FOUND,
                        "NOT_FOUND", "custom home icons unavailable on this device");
        return;
    }
    if (!session_ready) {
        error_response(respond, request_id, command, WMP_ERROR_BUSY,
                        "BUSY", "establish HELLO on this connection first");
        return;
    }
    const int count = cJSON_GetArraySize(request);
    uint32_t revision = 0, offset = 0, length = 0, upload_id = 0;
    screen_icon_metadata_t metadata;
    screen_icon_store_get_metadata(&metadata);
    if (type == WMP_MSG_SCREEN_ICON_GET) {
        if (count != 0) goto invalid;
        metadata_response(respond, request_id, command);
        return;
    }
    if (type == WMP_MSG_SCREEN_ICON_READ) {
        if (count != 3 || !uint_field(request, "revision", &revision) ||
            !uint_field(request, "offset", &offset) ||
            !uint_field(request, "length", &length) || length == 0 ||
            length > sizeof(s_chunk) || offset > WMP_SCREEN_ICON_TOTAL_BYTES ||
            length > WMP_SCREEN_ICON_TOTAL_BYTES - offset) goto invalid;
        esp_err_t error = screen_icon_store_read(revision, offset, s_chunk, length);
        if (error != ESP_OK) { store_error(respond, request_id, command, error); return; }
        size_t encoded_length = 0;
        if (mbedtls_base64_encode(s_encoded, sizeof(s_encoded), &encoded_length,
                                  s_chunk, length) != 0) goto invalid;
        cJSON *root = response_root(command);
        cJSON *result = cJSON_GetObjectItemCaseSensitive(root, "result");
        cJSON_AddNumberToObject(result, "revision", revision);
        cJSON_AddNumberToObject(result, "offset", offset);
        cJSON_AddStringToObject(result, "data", (const char *)s_encoded);
        respond_json(respond, WMP_MSG_ACK, request_id, root);
        return;
    }
    if (type == WMP_MSG_SCREEN_ICON_BEGIN || type == WMP_MSG_SCREEN_ICON_RESET) {
        if (!uint_field(request, "base_revision", &revision)) goto invalid;
        if (type == WMP_MSG_SCREEN_ICON_BEGIN) {
            uint32_t width, height;
            if (count != 6 || !string_field(request, "target", WMP_SCREEN_ICON_TARGET) ||
                !string_field(request, "format", WMP_SCREEN_ICON_FORMAT) ||
                !uint_field(request, "width", &width) || width != WMP_SCREEN_ICON_WIDTH ||
                !uint_field(request, "height", &height) || height != WMP_SCREEN_ICON_HEIGHT ||
                !uint_field(request, "total_bytes", &length) || length != WMP_SCREEN_ICON_TOTAL_BYTES)
                goto invalid;
        } else if (count != 1) goto invalid;
        if (maintenance_busy || screen_icon_protocol_active()) goto busy;
        if (revision != metadata.revision) {
            store_error(respond, request_id, command, ESP_ERR_INVALID_VERSION); return;
        }
        if (revision == UINT32_MAX) {
            store_error(respond, request_id, command, ESP_FAIL); return;
        }
        if (type == WMP_MSG_SCREEN_ICON_RESET) {
            esp_err_t error = screen_icon_store_reset(revision);
            if (error != ESP_OK) { store_error(respond, request_id, command, error); return; }
            metadata_response(respond, request_id, command);
            return;
        }
        if (s_next_upload_id == 0) goto busy;
        s_upload.pixels = malloc(WMP_SCREEN_ICON_TOTAL_BYTES);
        if (s_upload.pixels == NULL) {
            store_error(respond, request_id, command, ESP_ERR_NO_MEM); return;
        }
        s_upload.id = s_next_upload_id++;
        s_aborted_id = 0;
        s_expired_id = 0;
        s_upload.base_revision = revision;
        s_upload.next_offset = 0;
        s_upload.last_activity_ms = now_ms;
        atomic_store(&s_upload_active, true);
        cJSON *root = response_root(command);
        cJSON *result = cJSON_GetObjectItemCaseSensitive(root, "result");
        cJSON_AddNumberToObject(result, "upload_id", s_upload.id);
        cJSON_AddNumberToObject(result, "next_offset", 0);
        cJSON_AddNumberToObject(result, "max_chunk_bytes", WMP_SCREEN_ICON_MAX_CHUNK_BYTES);
        respond_json(respond, WMP_MSG_ACK, request_id, root);
        return;
    }
    if (!uint_field(request, "upload_id", &upload_id) || upload_id == 0 ||
        count != (type == WMP_MSG_SCREEN_ICON_DATA ? 3 : 1)) goto invalid;
    if (type == WMP_MSG_SCREEN_ICON_ABORT && upload_id == s_aborted_id) {
        cJSON *root = response_root(command);
        cJSON_AddBoolToObject(cJSON_GetObjectItemCaseSensitive(root, "result"), "aborted", true);
        respond_json(respond, WMP_MSG_ACK, request_id, root);
        return;
    }
    if (s_upload.pixels == NULL || upload_id != s_upload.id) {
        error_response(respond, request_id, command,
            upload_id == s_expired_id ? WMP_ERROR_TIMEOUT : WMP_ERROR_NOT_FOUND,
            upload_id == s_expired_id ? "TIMEOUT" : "NOT_FOUND", "upload session is no longer active");
        return;
    }
    if (type == WMP_MSG_SCREEN_ICON_ABORT) {
        s_aborted_id = upload_id;
        release_upload();
        cJSON *root = response_root(command);
        cJSON_AddBoolToObject(cJSON_GetObjectItemCaseSensitive(root, "result"), "aborted", true);
        respond_json(respond, WMP_MSG_ACK, request_id, root);
        return;
    }
    if (maintenance_busy) goto busy;
    if (type == WMP_MSG_SCREEN_ICON_DATA) {
        size_t received = 0;
        if (!uint_field(request, "offset", &offset) || !decode_chunk(request, &received) ||
            offset > s_upload.next_offset || offset > WMP_SCREEN_ICON_TOTAL_BYTES ||
            received > WMP_SCREEN_ICON_TOTAL_BYTES - offset) goto invalid;
        if (offset < s_upload.next_offset) {
            if (received > s_upload.next_offset - offset ||
                memcmp(s_upload.pixels + offset, s_chunk, received) != 0) goto invalid;
        } else {
            memcpy(s_upload.pixels + offset, s_chunk, received);
            s_upload.next_offset += (uint32_t)received;
        }
        s_upload.last_activity_ms = now_ms;
        cJSON *root = response_root(command);
        cJSON_AddNumberToObject(cJSON_GetObjectItemCaseSensitive(root, "result"),
                                "next_offset", s_upload.next_offset);
        respond_json(respond, WMP_MSG_ACK, request_id, root);
        return;
    }
    if (s_upload.next_offset != WMP_SCREEN_ICON_TOTAL_BYTES) goto invalid;
    /* Store rechecks base_revision while holding its lock, before any write. */
    esp_err_t error = screen_icon_store_commit(s_upload.base_revision,
                                               s_upload.pixels, s_upload.next_offset);
    release_upload();
    if (error != ESP_OK) { store_error(respond, request_id, command, error); return; }
    metadata_response(respond, request_id, command);
    return;
invalid:
    error_response(respond, request_id, command, WMP_ERROR_VALIDATION_FAILED,
                    "VALIDATION_FAILED", "invalid screen icon fields, data, or offset");
    return;
busy:
    error_response(respond, request_id, command, WMP_ERROR_BUSY,
                    "BUSY", "another device maintenance operation is active");
}
