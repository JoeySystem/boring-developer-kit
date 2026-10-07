#include "usb_service.h"

/*
 * One USB configuration combines CDC ACM with standard HID. The Codex USB
 * variant exposes its vendor protocol on a second HID interface so host HID
 * libraries can open it independently from the keyboard collection.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdatomic.h>
#include <string.h>

#include "class/hid/hid_device.h"
#include "codex_micro_protocol.h"
#include "config_protocol.h"
#include "freertos/FreeRTOS.h"
#include "protocol_contract.h"
#include "tusb.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tinyusb_default_config.h"

enum {
    ITF_NUM_CDC_CONTROL = 0,
    ITF_NUM_CDC_DATA,
    ITF_NUM_HID_STANDARD,
#if CONFIG_CODEX_MICRO_USB_ENABLED
    ITF_NUM_HID_VENDOR,
#endif
    ITF_NUM_TOTAL,
};

enum {
    USB_HID_INSTANCE_STANDARD = 0,
#if CONFIG_CODEX_MICRO_USB_ENABLED
    USB_HID_INSTANCE_VENDOR,
#endif
};

#if CONFIG_CODEX_MICRO_USB_ENABLED
#define CONFIG_TOTAL_LEN                                                     \
    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN +             \
     TUD_HID_INOUT_DESC_LEN)
#define USB_STANDARD_HID_ENDPOINT_SIZE 16u
#define USB_VENDOR_HID_ENDPOINT_SIZE 64u
#else
#define CONFIG_TOTAL_LEN                                                     \
    (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_HID_DESC_LEN)
#define USB_STANDARD_HID_ENDPOINT_SIZE 16u
#endif
#define CDC_TX_QUEUE_MAX_BYTES (24u * 1024u)
#define USB_SERVICE_TINYUSB_TASK_STACK_SIZE 8192
#define HID_USAGE_KEYBOARD_ERROR_ROLLOVER 0x01u
#define HID_NEUTRAL_REPORT_COUNT 3u
#if CONFIG_CODEX_MICRO_USB_ENABLED
#define VENDOR_TX_QUEUE_DEPTH 64u
#endif

typedef enum {
    USB_SERVICE_ROUTE_NONE = 0,
    USB_SERVICE_ROUTE_USB,
    USB_SERVICE_ROUTE_BLE,
} usb_service_route_t;

typedef struct cdc_tx_node {
    struct cdc_tx_node *next;
    size_t length;
    size_t offset;
    uint8_t data[];
} cdc_tx_node_t;

static atomic_bool s_mounted;
static atomic_bool s_standard_enabled = true;
static usb_service_ble_hid_t s_ble_hid;
static usb_service_vendor_hid_t s_vendor_hid;
static usb_service_route_t s_hid_route;
static usb_service_route_t s_hid_target_route;
static bool s_route_transition;
static uint8_t s_neutral_stage;
static uint8_t s_baseline_stage;
static uint32_t s_key_owners[256];
static uint32_t s_modifier_owners[8];
static uint8_t s_modifiers;
static bool s_keyboard_dirty;
/* Complete action snapshots: press and release must both reach the transport. */
static uint8_t s_keyboard_queue[32][8];
static size_t s_keyboard_read;
static size_t s_keyboard_write;
static uint16_t s_consumer_queue[8];
static size_t s_consumer_read;
static size_t s_consumer_write;
static bool s_consumer_release_pending;
typedef struct {
    uint8_t buttons;
    int8_t x;
    int8_t y;
    int8_t wheel;
    int8_t pan;
} mouse_report_t;
typedef struct {
    usb_service_source_t source;
    mouse_report_t report;
} mouse_queue_entry_t;
static mouse_queue_entry_t s_mouse_queue[8];
static size_t s_mouse_read;
static size_t s_mouse_write;
static uint32_t s_mouse_button_owners[8];
static uint8_t s_mouse_buttons;
static bool s_mouse_buttons_dirty;
static char s_serial[18] = "CP01-000000000000";
static uint8_t s_cdc_rx[512];
static cdc_tx_node_t *s_cdc_tx_head;
static cdc_tx_node_t *s_cdc_tx_tail;
static size_t s_cdc_tx_bytes;
static portMUX_TYPE s_cdc_tx_lock = portMUX_INITIALIZER_UNLOCKED;
#if CONFIG_CODEX_MICRO_USB_ENABLED
static uint8_t
    s_vendor_tx_queue[VENDOR_TX_QUEUE_DEPTH][CODEX_MICRO_REPORT_BYTES];
static size_t s_vendor_tx_read;
static size_t s_vendor_tx_write;
static uint32_t s_vendor_tx_generation;
static portMUX_TYPE s_vendor_tx_lock = portMUX_INITIALIZER_UNLOCKED;
static void vendor_tx_clear(void);
#endif

static const tusb_desc_device_t DEVICE_DESCRIPTOR = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = WMP_USB_VID,
#if CONFIG_CODEX_MICRO_USB_ENABLED
    .idProduct = WMP_USB_PID_ENGINEERING,
    .bcdDevice = CODEX_MICRO_COMPAT_BCD_DEVICE,
#else
    /* Rev A intentionally retains the Engineering PID until physical approval. */
    .idProduct = WMP_USB_PID_ENGINEERING,
    .bcdDevice = WMP_USB_BCD_DEVICE,
#endif
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

static const uint8_t HID_STANDARD_REPORT_DESCRIPTOR[] = {
    TUD_HID_REPORT_DESC_KEYBOARD(
        HID_REPORT_ID(USB_SERVICE_REPORT_ID_KEYBOARD)),
    TUD_HID_REPORT_DESC_CONSUMER(
        HID_REPORT_ID(USB_SERVICE_REPORT_ID_CONSUMER)),
    TUD_HID_REPORT_DESC_MOUSE(HID_REPORT_ID(USB_SERVICE_REPORT_ID_MOUSE)),
};

#if CONFIG_CODEX_MICRO_USB_ENABLED
static const uint8_t HID_VENDOR_REPORT_DESCRIPTOR[] = {
    /* Codex vendor input/output channel, report ID 6 with a 63-byte body. */
    0x06, 0x00, 0xFF, /* Usage Page (Vendor Defined 0xFF00) */
    0x09, 0x01,       /* Usage (1) */
    0xA1, 0x01,       /* Collection (Application) */
    0x85, USB_SERVICE_REPORT_ID_CODEX_VENDOR,
    0x15, 0x00,       /* Logical Minimum (0) */
    0x26, 0xFF, 0x00, /* Logical Maximum (255) */
    0x75, 0x08,       /* Report Size (8) */
    0x95, 0x3F,       /* Report Count (63) */
    0x09, 0x01,
    0x81, 0x02,       /* Input (Data, Variable, Absolute) */
    0x95, 0x3F,
    0x09, 0x02,
    0x91, 0x02,       /* Output (Data, Variable, Absolute) */
    0xC0,
};
#endif

static const uint8_t CONFIG_DESCRIPTOR[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0, 250),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC_CONTROL, 4, 0x81, 8, 0x02, 0x82, 64),
    TUD_HID_DESCRIPTOR(ITF_NUM_HID_STANDARD, 5, HID_ITF_PROTOCOL_NONE,
                       sizeof(HID_STANDARD_REPORT_DESCRIPTOR), 0x83,
                       USB_STANDARD_HID_ENDPOINT_SIZE, 1),
#if CONFIG_CODEX_MICRO_USB_ENABLED
    TUD_HID_INOUT_DESCRIPTOR(ITF_NUM_HID_VENDOR, 5, HID_ITF_PROTOCOL_NONE,
                             sizeof(HID_VENDOR_REPORT_DESCRIPTOR), 0x04, 0x84,
                             USB_VENDOR_HID_ENDPOINT_SIZE, 1),
#endif
};

static const char *STRING_DESCRIPTORS[] = {
    (const char[]){0x09, 0x04},
#if CONFIG_CODEX_MICRO_USB_ENABLED
    CODEX_MICRO_COMPAT_MANUFACTURER,
    CODEX_MICRO_COMPAT_PRODUCT,
#else
    WMP_USB_MANUFACTURER,
    WMP_USB_PRODUCT_ENGINEERING,
#endif
    s_serial,
    "WMP Config CDC",
    "WMP HID",
};

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (instance == USB_HID_INSTANCE_VENDOR) {
        return HID_VENDOR_REPORT_DESCRIPTOR;
    }
#endif
    return instance == USB_HID_INSTANCE_STANDARD
               ? HID_STANDARD_REPORT_DESCRIPTOR
               : NULL;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type, uint8_t *buffer,
                               uint16_t requested_length)
{
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)requested_length;
    return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           const uint8_t *buffer, uint16_t buffer_size)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (instance != USB_HID_INSTANCE_VENDOR) {
        return;
    }
    if ((report_type == HID_REPORT_TYPE_OUTPUT ||
         report_type == HID_REPORT_TYPE_INVALID) &&
        s_vendor_hid.output != NULL && buffer != NULL) {
        const uint8_t *body = buffer;
        size_t body_size = buffer_size;
        uint8_t normalized_report_id = report_id;
        if (normalized_report_id == 0 && body_size > 0 &&
            body[0] == USB_SERVICE_REPORT_ID_CODEX_VENDOR) {
            normalized_report_id = body[0];
            ++body;
            --body_size;
        }
        if (normalized_report_id == USB_SERVICE_REPORT_ID_CODEX_VENDOR) {
            s_vendor_hid.output(normalized_report_id, body, body_size,
                                s_vendor_hid.context);
        }
    }
#else
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)buffer_size;
#endif
}

static void usb_event_callback(tinyusb_event_t *event, void *arg)
{
    (void)arg;
    if (event->id == TINYUSB_EVENT_ATTACHED) {
        atomic_store(&s_mounted, true);
        s_keyboard_dirty = true;
    } else if (event->id == TINYUSB_EVENT_DETACHED) {
        /* The logical HID state is retained so BLE can take over immediately. */
        atomic_store(&s_mounted, false);
#if CONFIG_CODEX_MICRO_USB_ENABLED
        vendor_tx_clear();
#endif
        if (config_protocol_active_transport() ==
            CONFIG_PROTOCOL_TRANSPORT_USB) {
            config_protocol_release_transport(
                CONFIG_PROTOCOL_TRANSPORT_USB);
        }
    }
}

static esp_err_t protocol_tx(const uint8_t *data, size_t length, void *context)
{
    (void)context;
    if (data == NULL || length == 0 || length > CDC_TX_QUEUE_MAX_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    cdc_tx_node_t *node = malloc(sizeof(*node) + length);
    if (node == NULL) {
        return ESP_ERR_NO_MEM;
    }
    node->next = NULL;
    node->length = length;
    node->offset = 0;
    memcpy(node->data, data, length);

    portENTER_CRITICAL(&s_cdc_tx_lock);
    if (!atomic_load(&s_mounted) ||
        s_cdc_tx_bytes + length > CDC_TX_QUEUE_MAX_BYTES) {
        portEXIT_CRITICAL(&s_cdc_tx_lock);
        free(node);
        return ESP_ERR_NO_MEM;
    }
    if (s_cdc_tx_tail != NULL) {
        s_cdc_tx_tail->next = node;
    } else {
        s_cdc_tx_head = node;
    }
    s_cdc_tx_tail = node;
    s_cdc_tx_bytes += length;
    portEXIT_CRITICAL(&s_cdc_tx_lock);
    return ESP_OK;
}

static void clear_cdc_tx_queue(void)
{
    portENTER_CRITICAL(&s_cdc_tx_lock);
    cdc_tx_node_t *node = s_cdc_tx_head;
    s_cdc_tx_head = NULL;
    s_cdc_tx_tail = NULL;
    s_cdc_tx_bytes = 0;
    portEXIT_CRITICAL(&s_cdc_tx_lock);
    while (node != NULL) {
        cdc_tx_node_t *next = node->next;
        free(node);
        node = next;
    }
}

static void poll_cdc_tx(void)
{
    portENTER_CRITICAL(&s_cdc_tx_lock);
    cdc_tx_node_t *node = s_cdc_tx_head;
    portEXIT_CRITICAL(&s_cdc_tx_lock);
    if (node == NULL) {
        return;
    }
    const size_t remaining = node->length - node->offset;
    const size_t queued = tinyusb_cdcacm_write_queue(
        TINYUSB_CDC_ACM_0, node->data + node->offset, remaining);
    if (queued == 0) {
        (void)tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
        return;
    }
    node->offset += queued;
    (void)tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
    if (node->offset < node->length) {
        return;
    }
    portENTER_CRITICAL(&s_cdc_tx_lock);
    s_cdc_tx_head = node->next;
    if (s_cdc_tx_head == NULL) {
        s_cdc_tx_tail = NULL;
    }
    s_cdc_tx_bytes -= node->length;
    portEXIT_CRITICAL(&s_cdc_tx_lock);
    free(node);
}

static void cdc_rx_callback(int interface, cdcacm_event_t *event)
{
    (void)event;
    size_t received = 0;
    if (tinyusb_cdcacm_read(interface, s_cdc_rx, sizeof(s_cdc_rx), &received) == ESP_OK && received > 0) {
        config_protocol_feed_transport(CONFIG_PROTOCOL_TRANSPORT_USB,
                                       s_cdc_rx, received);
    }
}

esp_err_t usb_service_init(const char *serial)
{
    if (serial != NULL) {
        snprintf(s_serial, sizeof(s_serial), "%s", serial);
    }
    config_protocol_init(s_serial, protocol_tx, NULL);

    tinyusb_config_t usb_config = TINYUSB_DEFAULT_CONFIG();
    usb_config.descriptor.device = &DEVICE_DESCRIPTOR;
    usb_config.descriptor.full_speed_config = CONFIG_DESCRIPTOR;
    usb_config.descriptor.string = STRING_DESCRIPTORS;
    usb_config.descriptor.string_count = sizeof(STRING_DESCRIPTORS) / sizeof(STRING_DESCRIPTORS[0]);
    usb_config.event_cb = usb_event_callback;
    usb_config.task = TINYUSB_TASK_CUSTOM(
        USB_SERVICE_TINYUSB_TASK_STACK_SIZE,
        TINYUSB_DEFAULT_TASK_PRIO,
        TINYUSB_DEFAULT_TASK_AFFINITY);
    esp_err_t error = tinyusb_driver_install(&usb_config);
    if (error != ESP_OK) {
        return error;
    }

    const tinyusb_config_cdcacm_t cdc_config = {
        .cdc_port = TINYUSB_CDC_ACM_0,
        .callback_rx = cdc_rx_callback,
        .callback_rx_wanted_char = NULL,
        .callback_line_state_changed = NULL,
        .callback_line_coding_changed = NULL,
    };
    return tinyusb_cdcacm_init(&cdc_config);
}

bool usb_service_is_mounted(void)
{
    /*
     * This target remains powered from its battery after VBUS disappears.
     * Without a dedicated VBUS sense input, the detach callback is not
     * guaranteed to clear the cached mount flag. TinyUSB still marks the bus
     * suspended when SOF traffic stops, so require a transfer-ready bus here.
     */
    return atomic_load(&s_mounted) && tud_ready();
}

bool usb_service_config_session_active(void)
{
    return config_protocol_session_active();
}

void usb_service_register_ble_hid(const usb_service_ble_hid_t *transport)
{
    s_ble_hid = transport != NULL ? *transport : (usb_service_ble_hid_t){0};
}

void usb_service_register_vendor_hid(
    const usb_service_vendor_hid_t *transport)
{
    s_vendor_hid =
        transport != NULL ? *transport : (usb_service_vendor_hid_t){0};
}

#if CONFIG_CODEX_MICRO_USB_ENABLED
static void vendor_tx_clear(void)
{
    portENTER_CRITICAL(&s_vendor_tx_lock);
    s_vendor_tx_read = 0;
    s_vendor_tx_write = 0;
    ++s_vendor_tx_generation;
    portEXIT_CRITICAL(&s_vendor_tx_lock);
}

static esp_err_t vendor_tx_enqueue(const uint8_t *data)
{
    esp_err_t result = ESP_OK;
    portENTER_CRITICAL(&s_vendor_tx_lock);
    const size_t next = (s_vendor_tx_write + 1u) % VENDOR_TX_QUEUE_DEPTH;
    if (!atomic_load(&s_mounted)) {
        result = ESP_ERR_INVALID_STATE;
    } else if (next == s_vendor_tx_read) {
        result = ESP_ERR_NO_MEM;
    } else {
        memcpy(s_vendor_tx_queue[s_vendor_tx_write], data,
               CODEX_MICRO_REPORT_BYTES);
        s_vendor_tx_write = next;
    }
    portEXIT_CRITICAL(&s_vendor_tx_lock);
    return result;
}

static void poll_vendor_tx(void)
{
    uint8_t report[CODEX_MICRO_REPORT_BYTES];
    size_t read_index = 0;
    uint32_t generation = 0;

    portENTER_CRITICAL(&s_vendor_tx_lock);
    if (s_vendor_tx_read == s_vendor_tx_write) {
        portEXIT_CRITICAL(&s_vendor_tx_lock);
        return;
    }
    read_index = s_vendor_tx_read;
    generation = s_vendor_tx_generation;
    memcpy(report, s_vendor_tx_queue[read_index], sizeof(report));
    portEXIT_CRITICAL(&s_vendor_tx_lock);

    if (!atomic_load(&s_mounted) ||
        !tud_hid_n_ready(USB_HID_INSTANCE_VENDOR) ||
        !tud_hid_n_report(USB_HID_INSTANCE_VENDOR,
                          USB_SERVICE_REPORT_ID_CODEX_VENDOR, report,
                          sizeof(report))) {
        return;
    }

    portENTER_CRITICAL(&s_vendor_tx_lock);
    if (s_vendor_tx_generation == generation &&
        s_vendor_tx_read == read_index) {
        s_vendor_tx_read = (s_vendor_tx_read + 1u) % VENDOR_TX_QUEUE_DEPTH;
    }
    portEXIT_CRITICAL(&s_vendor_tx_lock);
}
#endif

esp_err_t usb_service_send_vendor_report(const uint8_t *data, size_t length)
{
#if CONFIG_CODEX_MICRO_USB_ENABLED
    if (data == NULL || length != 63u) {
        return ESP_ERR_INVALID_ARG;
    }
    return vendor_tx_enqueue(data);
#else
    (void)data;
    (void)length;
    return ESP_ERR_NOT_SUPPORTED;
#endif
}

void usb_service_set_standard_enabled(bool enabled)
{
    if (atomic_exchange(&s_standard_enabled, enabled) == enabled) {
        return;
    }
    /*
     * Mode changes are a hard ownership boundary. Clear the logical state and
     * let the route transition send neutral reports before it changes sinks.
     */
    usb_service_release_all();
}

static bool valid_source(usb_service_source_t source)
{
    return source < USB_SERVICE_SOURCE_COUNT;
}

static uint32_t source_mask(usb_service_source_t source)
{
    return 1UL << source;
}

void usb_service_set_key(usb_service_source_t source, uint8_t hid_usage, bool pressed)
{
    if (!valid_source(source) || hid_usage == 0) {
        return;
    }
    const uint32_t mask = source_mask(source);
    const uint32_t updated = pressed ? (s_key_owners[hid_usage] | mask)
                                     : (s_key_owners[hid_usage] & ~mask);
    if (updated != s_key_owners[hid_usage]) {
        s_key_owners[hid_usage] = updated;
        s_keyboard_dirty = true;
    }
}

void usb_service_set_modifier(usb_service_source_t source, uint8_t hid_usage, bool pressed)
{
    if (!valid_source(source) || hid_usage < 224 || hid_usage > 231) {
        return;
    }
    const size_t index = hid_usage - 224;
    const uint32_t mask = source_mask(source);
    const uint32_t owners = pressed ? (s_modifier_owners[index] | mask)
                                    : (s_modifier_owners[index] & ~mask);
    if (owners == s_modifier_owners[index]) {
        return;
    }
    s_modifier_owners[index] = owners;
    const uint8_t modifier_mask = (uint8_t)(1u << index);
    const uint8_t updated = owners != 0 ? (uint8_t)(s_modifiers | modifier_mask)
                                       : (uint8_t)(s_modifiers & (uint8_t)~modifier_mask);
    if (updated != s_modifiers) {
        s_modifiers = updated;
        s_keyboard_dirty = true;
    }
}

static void keyboard_report(uint8_t report[8])
{
    uint8_t keys[6] = {0};
    size_t count = 0;
    for (size_t usage = 1; usage < sizeof(s_key_owners) / sizeof(s_key_owners[0]); ++usage) {
        if (s_key_owners[usage] != 0) {
            if (count < sizeof(keys)) {
                keys[count] = usage;
            }
            ++count;
        }
    }
    if (count > sizeof(keys)) {
        memset(keys, HID_USAGE_KEYBOARD_ERROR_ROLLOVER, sizeof(keys));
    }
    memset(report, 0, 8);
    report[0] = s_modifiers;
    memcpy(report + 2, keys, sizeof(keys));
}

bool usb_service_keyboard_pending(void)
{
    return s_keyboard_dirty || s_keyboard_read != s_keyboard_write;
}

void usb_service_commit_keyboard(void)
{
    if (!s_keyboard_dirty) {
        return;
    }
    const size_t next = (s_keyboard_write + 1) %
        (sizeof(s_keyboard_queue) / sizeof(s_keyboard_queue[0]));
    if (next == s_keyboard_read) {
        /* Bounded backpressure: retain dirty state for a final resync/release. */
        return;
    }
    keyboard_report(s_keyboard_queue[s_keyboard_write]);
    s_keyboard_write = next;
    s_keyboard_dirty = false;
}

void usb_service_tap_consumer(uint16_t hid_usage)
{
    if (hid_usage == 0) {
        return;
    }
    const size_t next = (s_consumer_write + 1) % (sizeof(s_consumer_queue) / sizeof(s_consumer_queue[0]));
    if (next == s_consumer_read) {
        return;
    }
    s_consumer_queue[s_consumer_write] = hid_usage;
    s_consumer_write = next;
}

static bool enqueue_mouse(usb_service_source_t source, uint8_t buttons,
                          int8_t x, int8_t y, int8_t wheel, int8_t pan)
{
    const size_t next = (s_mouse_write + 1) % (sizeof(s_mouse_queue) / sizeof(s_mouse_queue[0]));
    if (next == s_mouse_read) {
        return false;
    }
    s_mouse_queue[s_mouse_write] = (mouse_queue_entry_t) {
        .source = source,
        .report = {buttons, x, y, wheel, pan},
    };
    s_mouse_write = next;
    return true;
}

void usb_service_send_mouse(usb_service_source_t source, uint8_t buttons,
                            int8_t x, int8_t y, int8_t wheel, int8_t pan)
{
    if (!valid_source(source)) {
        return;
    }
    const uint32_t mask = source_mask(source);
    uint8_t updated_buttons = 0;
    for (size_t index = 0; index < 8; ++index) {
        s_mouse_button_owners[index] = (buttons & (1u << index)) != 0
                                          ? (s_mouse_button_owners[index] | mask)
                                          : (s_mouse_button_owners[index] & ~mask);
        if (s_mouse_button_owners[index] != 0) {
            updated_buttons |= (uint8_t)(1u << index);
        }
    }
    if (updated_buttons != s_mouse_buttons) {
        s_mouse_buttons = updated_buttons;
        s_mouse_buttons_dirty = true;
    }
    if (s_mouse_buttons_dirty || x != 0 || y != 0 || wheel != 0 || pan != 0) {
        if (enqueue_mouse(source, s_mouse_buttons, x, y, wheel, pan)) {
            s_mouse_buttons_dirty = false;
        }
    }
}

void usb_service_discard_mouse_source(usb_service_source_t source)
{
    if (!valid_source(source) || s_mouse_read == s_mouse_write) {
        return;
    }
    mouse_queue_entry_t retained[
        sizeof(s_mouse_queue) / sizeof(s_mouse_queue[0])];
    size_t retained_count = 0;
    for (size_t index = s_mouse_read; index != s_mouse_write;
         index = (index + 1) %
                 (sizeof(s_mouse_queue) / sizeof(s_mouse_queue[0]))) {
        if (s_mouse_queue[index].source != source) {
            retained[retained_count] = s_mouse_queue[index];
            retained[retained_count].report.buttons = s_mouse_buttons;
            ++retained_count;
        }
    }
    memcpy(s_mouse_queue, retained,
           retained_count * sizeof(s_mouse_queue[0]));
    s_mouse_read = 0;
    s_mouse_write = retained_count;
}

void usb_service_release_source(usb_service_source_t source)
{
    if (!valid_source(source)) {
        return;
    }
    bool keyboard_released = false;
    const uint32_t keep_mask = ~source_mask(source);
    for (size_t usage = 1; usage < sizeof(s_key_owners) / sizeof(s_key_owners[0]); ++usage) {
        const uint32_t updated = s_key_owners[usage] & keep_mask;
        if (updated != s_key_owners[usage]) {
            s_key_owners[usage] = updated;
            s_keyboard_dirty = true;
            keyboard_released = true;
        }
    }
    for (size_t index = 0; index < 8; ++index) {
        const uint32_t updated_modifier = s_modifier_owners[index] & keep_mask;
        if (updated_modifier != s_modifier_owners[index]) {
            s_modifier_owners[index] = updated_modifier;
            if (updated_modifier == 0) {
                s_modifiers &= (uint8_t)~(1u << index);
            }
            s_keyboard_dirty = true;
            keyboard_released = true;
        }
        s_mouse_button_owners[index] &= keep_mask;
    }
    if (keyboard_released) {
        s_keyboard_read = s_keyboard_write = 0;
    }
    uint8_t updated_buttons = 0;
    for (size_t index = 0; index < 8; ++index) {
        if (s_mouse_button_owners[index] != 0) {
            updated_buttons |= (uint8_t)(1u << index);
        }
    }
    if (updated_buttons != s_mouse_buttons) {
        s_mouse_buttons = updated_buttons;
        s_mouse_buttons_dirty = true;
    }
}

void usb_service_release_all(void)
{
    memset(s_key_owners, 0, sizeof(s_key_owners));
    memset(s_modifier_owners, 0, sizeof(s_modifier_owners));
    s_modifiers = 0;
    s_keyboard_read = s_keyboard_write = 0;
    s_keyboard_dirty = true;
    s_consumer_read = 0;
    s_consumer_write = 0;
    s_consumer_release_pending = false;
    s_mouse_read = 0;
    s_mouse_write = 0;
    memset(s_mouse_button_owners, 0, sizeof(s_mouse_button_owners));
    s_mouse_buttons = 0;
    s_mouse_buttons_dirty = true;
}

static bool ble_hid_connected(void)
{
    return s_ble_hid.connected != NULL &&
           s_ble_hid.connected(s_ble_hid.context);
}

static usb_service_route_t desired_hid_route(void)
{
    if (!atomic_load(&s_standard_enabled)) {
        return USB_SERVICE_ROUTE_NONE;
    }
    if (usb_service_is_mounted()) {
        return USB_SERVICE_ROUTE_USB;
    }
    return ble_hid_connected() ? USB_SERVICE_ROUTE_BLE
                               : USB_SERVICE_ROUTE_NONE;
}

static bool route_ready(usb_service_route_t route)
{
    if (route == USB_SERVICE_ROUTE_USB) {
        return usb_service_is_mounted() &&
               tud_hid_n_ready(USB_HID_INSTANCE_STANDARD);
    }
    if (route == USB_SERVICE_ROUTE_BLE) {
        return ble_hid_connected() && s_ble_hid.send != NULL;
    }
    return false;
}

static bool route_send_report(usb_service_route_t route, uint8_t report_id,
                              const void *data, size_t length)
{
    if (!route_ready(route) || data == NULL || length == 0) {
        return false;
    }
    if (route == USB_SERVICE_ROUTE_USB) {
        return tud_hid_n_report(USB_HID_INSTANCE_STANDARD, report_id, data,
                                (uint16_t)length);
    }
    return s_ble_hid.send(report_id, data, length, s_ble_hid.context) == ESP_OK;
}

static bool send_neutral_report(usb_service_route_t route, uint8_t stage)
{
    if (stage == 0) {
        const uint8_t keyboard[8] = {0};
        return route_send_report(route, USB_SERVICE_REPORT_ID_KEYBOARD,
                                 keyboard, sizeof(keyboard));
    }
    if (stage == 1) {
        const uint16_t consumer = 0;
        return route_send_report(route, USB_SERVICE_REPORT_ID_CONSUMER,
                                 &consumer, sizeof(consumer));
    }
    const mouse_report_t mouse = {0};
    return route_send_report(route, USB_SERVICE_REPORT_ID_MOUSE, &mouse,
                             sizeof(mouse));
}

static bool update_hid_route(void)
{
    const usb_service_route_t desired = desired_hid_route();
    if (!s_route_transition && desired != s_hid_route) {
        /* Do not replay queued input into a different host connection. */
        s_mouse_read = s_mouse_write = 0;
        s_keyboard_read = s_keyboard_write = 0;
        s_keyboard_dirty = true;
        s_hid_target_route = desired;
        s_route_transition = true;
        s_neutral_stage = 0;
    } else if (s_route_transition && desired != s_hid_target_route) {
        s_hid_target_route = desired;
        s_neutral_stage = 0;
        if (desired == s_hid_route) {
            s_route_transition = false;
        }
    }
    if (!s_route_transition) {
        return true;
    }

    if (s_hid_route != USB_SERVICE_ROUTE_NONE && route_ready(s_hid_route)) {
        if (s_neutral_stage < HID_NEUTRAL_REPORT_COUNT) {
            if (send_neutral_report(s_hid_route, s_neutral_stage)) {
                ++s_neutral_stage;
            }
            return false;
        }
    }

    s_hid_route = s_hid_target_route;
    s_route_transition = false;
    s_baseline_stage =
        s_hid_route == USB_SERVICE_ROUTE_NONE ? HID_NEUTRAL_REPORT_COUNT : 0;
    s_keyboard_dirty = true;
    s_mouse_buttons_dirty = true;
    return true;
}

void usb_service_poll(void)
{
    if (usb_service_is_mounted()) {
        poll_cdc_tx();
#if CONFIG_CODEX_MICRO_USB_ENABLED
        poll_vendor_tx();
#endif
    } else {
        clear_cdc_tx_queue();
    }

    if (!update_hid_route() || s_hid_route == USB_SERVICE_ROUTE_NONE ||
        !route_ready(s_hid_route)) {
        return;
    }
    if (s_baseline_stage < HID_NEUTRAL_REPORT_COUNT) {
        if (send_neutral_report(s_hid_route, s_baseline_stage)) {
            ++s_baseline_stage;
        }
        return;
    }

    if (s_consumer_release_pending) {
        /* Consumer controls are taps: every non-zero usage is followed by zero. */
        const uint16_t usage = 0;
        if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_CONSUMER,
                              &usage, sizeof(usage))) {
            s_consumer_release_pending = false;
        }
        return;
    }
    if (s_consumer_read != s_consumer_write) {
        const uint16_t usage = s_consumer_queue[s_consumer_read];
        if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_CONSUMER,
                              &usage, sizeof(usage))) {
            s_consumer_read =
                (s_consumer_read + 1) %
                (sizeof(s_consumer_queue) / sizeof(s_consumer_queue[0]));
            s_consumer_release_pending = true;
        }
        return;
    }

    if (s_mouse_read != s_mouse_write) {
        const mouse_report_t report = s_mouse_queue[s_mouse_read].report;
        if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_MOUSE,
                              &report, sizeof(report))) {
            s_mouse_read =
                (s_mouse_read + 1) %
                (sizeof(s_mouse_queue) / sizeof(s_mouse_queue[0]));
        }
        return;
    }
    if (s_mouse_buttons_dirty) {
        const mouse_report_t report = {.buttons = s_mouse_buttons};
        if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_MOUSE,
                              &report, sizeof(report))) {
            s_mouse_buttons_dirty = false;
        }
        return;
    }

    if (s_keyboard_read != s_keyboard_write) {
        if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_KEYBOARD,
                              s_keyboard_queue[s_keyboard_read], 8)) {
            s_keyboard_read = (s_keyboard_read + 1) %
                (sizeof(s_keyboard_queue) / sizeof(s_keyboard_queue[0]));
        }
        return;
    }
    if (!s_keyboard_dirty) {
        return;
    }
    uint8_t report[8];
    keyboard_report(report);
    if (route_send_report(s_hid_route, USB_SERVICE_REPORT_ID_KEYBOARD,
                          report, sizeof(report))) {
        s_keyboard_dirty = false;
    }
}
