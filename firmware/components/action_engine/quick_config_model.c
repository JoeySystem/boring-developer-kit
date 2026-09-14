#include "quick_config_model.h"

#include <string.h>

static size_t wrap(size_t value, size_t count, int direction)
{
    if (count == 0 || direction == 0) {
        return value;
    }
    return direction > 0 ? (value + 1) % count : (value + count - 1) % count;
}

static size_t clamp(size_t value, size_t count, int direction)
{
    if (count == 0 || direction == 0) {
        return value;
    }
    if (direction > 0) {
        return value + 1 < count ? value + 1 : count - 1;
    }
    return value > 0 ? value - 1 : 0;
}

void quick_config_model_begin(quick_config_model_t *model,
                              const quick_config_model_init_t *initial)
{
    if (model == NULL || initial == NULL) {
        return;
    }
    memset(model, 0, sizeof(*model));
    model->profile_count = initial->profile_count;
    model->profile_index = initial->profile_count == 0 ? 0
        : initial->profile_index % initial->profile_count;
    model->platform_count = initial->platform_count;
    model->platform_index = initial->platform_count == 0 ? 0
        : initial->platform_index % initial->platform_count;
    model->preset_count = initial->preset_count;
    model->preset_index = initial->preset_index <= initial->preset_count
        ? initial->preset_index : initial->preset_count;
    model->lighting_level_count = initial->lighting_level_count;
    model->lighting_level_index = initial->lighting_level_count == 0 ? 0
        : initial->lighting_level_index % initial->lighting_level_count;
    model->haptic_level_count = initial->haptic_level_count;
    model->haptic_level_index = initial->haptic_level_count == 0 ? 0
        : initial->haptic_level_index % initial->haptic_level_count;
}

void quick_config_model_move_item(quick_config_model_t *model, int direction)
{
    if (model == NULL) {
        return;
    }
    model->item = (quick_config_item_t)wrap(model->item, QUICK_CONFIG_ITEM_COUNT, direction);
    model->key_selected = false;
}

void quick_config_model_adjust(quick_config_model_t *model, int direction)
{
    if (model == NULL || direction == 0) {
        return;
    }
    if (model->item == QUICK_CONFIG_ITEM_PROFILE) {
        model->profile_index = wrap(model->profile_index, model->profile_count, direction);
    } else if (model->item == QUICK_CONFIG_ITEM_SYSTEM) {
        model->platform_index = wrap(model->platform_index, model->platform_count,
                                     direction);
    } else if (model->item == QUICK_CONFIG_ITEM_KEY_PRESET) {
        if (!model->key_selected || model->preset_count == 0) {
            return;
        }
        if (model->preset_index >= model->preset_count) {
            model->preset_index = direction > 0 ? 0 : model->preset_count - 1;
        } else {
            model->preset_index = wrap(model->preset_index, model->preset_count, direction);
        }
    } else if (model->item == QUICK_CONFIG_ITEM_LIGHTING) {
        model->lighting_level_index = clamp(model->lighting_level_index,
                                            model->lighting_level_count, direction);
    } else if (model->item == QUICK_CONFIG_ITEM_HAPTIC) {
        model->haptic_level_index = clamp(model->haptic_level_index,
                                          model->haptic_level_count, direction);
    }
}

void quick_config_model_select_key(quick_config_model_t *model, uint8_t key_index,
                                   size_t preset_index)
{
    if (model == NULL || key_index >= QUICK_CONFIG_MAX_KEY_COUNT) {
        return;
    }
    model->key_index = key_index;
    model->key_selected = true;
    model->preset_index = preset_index <= model->preset_count ? preset_index
                                                              : model->preset_count;
}

quick_config_command_t quick_config_model_confirm(quick_config_model_t *model)
{
    quick_config_command_t command = {0};
    if (model == NULL) {
        return command;
    }
    if (model->item == QUICK_CONFIG_ITEM_PROFILE && model->profile_count > 0) {
        command.type = QUICK_CONFIG_COMMAND_SET_PROFILE;
        command.value = model->profile_index;
    } else if (model->item == QUICK_CONFIG_ITEM_SYSTEM &&
               model->platform_count > 0) {
        command.type = QUICK_CONFIG_COMMAND_SET_PLATFORM;
        command.value = model->platform_index;
    } else if (model->item == QUICK_CONFIG_ITEM_KEY_PRESET &&
               model->key_selected &&
               model->preset_index < model->preset_count) {
        command.type = QUICK_CONFIG_COMMAND_SET_KEY_PRESET;
        command.value = model->preset_index;
        command.key_index = model->key_index;
    } else if (model->item == QUICK_CONFIG_ITEM_LIGHTING &&
               model->lighting_level_count > 0) {
        command.type = QUICK_CONFIG_COMMAND_SET_LIGHTING;
        command.value = model->lighting_level_index;
    } else if (model->item == QUICK_CONFIG_ITEM_HAPTIC && model->haptic_level_count > 0) {
        command.type = QUICK_CONFIG_COMMAND_SET_HAPTIC;
        command.value = model->haptic_level_index;
    } else if (model->item == QUICK_CONFIG_ITEM_EXIT) {
        command.type = QUICK_CONFIG_COMMAND_EXIT;
    }
    return command;
}
