#include "usb_link_model.h"

#include <stddef.h>

static bool elapsed(uint32_t now_ms, uint32_t since_ms, uint32_t duration_ms)
{
    return (uint32_t)(now_ms - since_ms) >= duration_ms;
}

void usb_link_model_init(usb_link_model_t *model, uint32_t now_ms)
{
    if (model == NULL) {
        return;
    }
    *model = (usb_link_model_t){
        .usb_owns_link = true,
        .booting = true,
        .boot_started_ms = now_ms,
    };
}

static bool update_boot(usb_link_model_t *model, uint32_t now_ms,
                        usb_link_input_t input)
{
    if (input == USB_LINK_INPUT_READY) {
        model->booting = false;
        model->ready_seen = true;
        model->ready_since_ms = now_ms;
        model->lost_seen = false;
        return true;
    }
    const uint32_t limit = input == USB_LINK_INPUT_NONE
        ? USB_LINK_BOOT_WAIT_MS : USB_LINK_BOOT_ENUMERATION_MS;
    if (elapsed(now_ms, model->boot_started_ms, limit)) {
        model->booting = false;
        model->usb_owns_link = false;
    }
    return model->usb_owns_link;
}

bool usb_link_model_update(usb_link_model_t *model, uint32_t now_ms,
                           usb_link_input_t input)
{
    if (model == NULL) {
        return false;
    }
    if (model->booting) {
        return update_boot(model, now_ms, input);
    }
    if (input == USB_LINK_INPUT_READY) {
        model->lost_seen = false;
        if (!model->ready_seen) {
            model->ready_seen = true;
            model->ready_since_ms = now_ms;
        }
        if (!model->usb_owns_link &&
            elapsed(now_ms, model->ready_since_ms,
                    USB_LINK_ATTACH_STABLE_MS)) {
            model->usb_owns_link = true;
        }
        return model->usb_owns_link;
    }
    model->ready_seen = false;
    if (!model->usb_owns_link) {
        return false;
    }
    if (!model->lost_seen) {
        model->lost_seen = true;
        model->lost_since_ms = now_ms;
    }
    const uint32_t grace = input == USB_LINK_INPUT_NONE
        ? USB_LINK_DETACH_MS : USB_LINK_SUSPEND_GRACE_MS;
    if (elapsed(now_ms, model->lost_since_ms, grace)) {
        model->usb_owns_link = false;
        model->lost_seen = false;
    }
    return model->usb_owns_link;
}
