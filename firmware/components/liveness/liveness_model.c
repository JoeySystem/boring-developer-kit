#include "liveness_model.h"

#include <string.h>

#define LIVENESS_STALL_MAGIC 0x4C495645u /* "LIVE" */

static bool valid_channel(int channel)
{
    return channel >= 0 && channel < LIVENESS_CHANNEL_COUNT;
}

void liveness_model_arm(liveness_model_t *model, liveness_channel_t channel,
                        uint32_t timeout_ms, uint32_t now_ms, bool resets)
{
    if (model == 0 || !valid_channel(channel)) {
        return;
    }
    liveness_slot_t *slot = &model->slots[channel];
    slot->armed = true;
    slot->arm_on_beat = false;
    slot->resets = resets;
    slot->timeout_ms = timeout_ms;
    slot->last_beat_ms = now_ms;
}

void liveness_model_arm_on_beat(liveness_model_t *model, liveness_channel_t channel,
                                uint32_t timeout_ms, bool resets)
{
    if (model == 0 || !valid_channel(channel) || model->slots[channel].armed) {
        return;
    }
    model->slots[channel].arm_on_beat = true;
    model->slots[channel].resets = resets;
    model->slots[channel].timeout_ms = timeout_ms;
}

void liveness_model_disarm(liveness_model_t *model, liveness_channel_t channel)
{
    if (model != 0 && valid_channel(channel)) {
        model->slots[channel].armed = false;
        model->slots[channel].arm_on_beat = false;
    }
}

void liveness_model_beat(liveness_model_t *model, liveness_channel_t channel,
                         uint32_t now_ms)
{
    if (model == 0 || !valid_channel(channel)) {
        return;
    }
    liveness_slot_t *slot = &model->slots[channel];
    if (slot->arm_on_beat) {
        slot->arm_on_beat = false;
        slot->armed = true;
    } else if (slot->armed) {
        const uint32_t gap = now_ms - slot->last_beat_ms;
        if (gap > slot->max_gap_ms) {
            slot->max_gap_ms = gap;
        }
    }
    slot->last_beat_ms = now_ms;
    ++slot->beats;
}

int liveness_model_stalled(const liveness_model_t *model, uint32_t now_ms)
{
    if (model == 0) {
        return LIVENESS_NONE;
    }
    for (int channel = 0; channel < LIVENESS_CHANNEL_COUNT; ++channel) {
        const liveness_slot_t *slot = &model->slots[channel];
        if (slot->armed && slot->resets &&
            (uint32_t)(now_ms - slot->last_beat_ms) > slot->timeout_ms) {
            return channel;
        }
    }
    return LIVENESS_NONE;
}

void liveness_model_note_overdue(liveness_model_t *model, uint32_t now_ms)
{
    if (model == 0) {
        return;
    }
    for (int channel = 0; channel < LIVENESS_CHANNEL_COUNT; ++channel) {
        liveness_slot_t *slot = &model->slots[channel];
        const bool overdue = slot->armed &&
                             (uint32_t)(now_ms - slot->last_beat_ms) > slot->timeout_ms;
        if (overdue && !slot->overdue) {
            ++slot->overdue_count;
        }
        slot->overdue = overdue;
    }
}

const char *liveness_channel_name(int channel)
{
    switch (channel) {
    case LIVENESS_MAIN:
        return "main_loop";
    case LIVENESS_USB_RX:
        return "usb_config_rx";
    case LIVENESS_USB_STACK:
        return "usb_stack";
    case LIVENESS_BLE_HOST:
        return "ble_host";
    default:
        return "none";
    }
}

static uint32_t record_check(uint32_t channel, uint32_t uptime_ms)
{
    return ~(LIVENESS_STALL_MAGIC ^ (channel << 24) ^ uptime_ms);
}

void liveness_stall_record_store(liveness_stall_record_t *record, int channel,
                                 uint32_t uptime_ms)
{
    if (record == 0 || !valid_channel(channel)) {
        return;
    }
    record->magic = LIVENESS_STALL_MAGIC;
    record->channel = (uint32_t)channel;
    record->uptime_ms = uptime_ms;
    record->check = record_check(record->channel, uptime_ms);
}

int liveness_stall_record_load(const liveness_stall_record_t *record,
                               uint32_t *uptime_ms)
{
    if (record == 0 || record->magic != LIVENESS_STALL_MAGIC ||
        !valid_channel((int)record->channel) ||
        record->check != record_check(record->channel, record->uptime_ms)) {
        if (uptime_ms != 0) {
            *uptime_ms = 0;
        }
        return LIVENESS_NONE;
    }
    if (uptime_ms != 0) {
        *uptime_ms = record->uptime_ms;
    }
    return (int)record->channel;
}

void liveness_stall_record_clear(liveness_stall_record_t *record)
{
    if (record != 0) {
        memset(record, 0, sizeof(*record));
    }
}
