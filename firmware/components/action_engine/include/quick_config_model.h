#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define QUICK_CONFIG_MAX_KEY_COUNT 12

typedef enum {
    QUICK_CONFIG_ITEM_PROFILE = 0,
    QUICK_CONFIG_ITEM_SYSTEM,
    QUICK_CONFIG_ITEM_KEY_PRESET,
    QUICK_CONFIG_ITEM_LIGHTING,
    QUICK_CONFIG_ITEM_HAPTIC,
    QUICK_CONFIG_ITEM_EXIT,
    QUICK_CONFIG_ITEM_COUNT,
} quick_config_item_t;

typedef enum {
    QUICK_CONFIG_COMMAND_NONE = 0,
    QUICK_CONFIG_COMMAND_SET_PROFILE,
    QUICK_CONFIG_COMMAND_SET_PLATFORM,
    QUICK_CONFIG_COMMAND_SET_KEY_PRESET,
    QUICK_CONFIG_COMMAND_SET_LIGHTING,
    QUICK_CONFIG_COMMAND_SET_HAPTIC,
    QUICK_CONFIG_COMMAND_EXIT,
} quick_config_command_type_t;

typedef struct {
    quick_config_command_type_t type;
    size_t value;
    uint8_t key_index;
} quick_config_command_t;

typedef struct {
    quick_config_item_t item;
    size_t profile_index;
    size_t profile_count;
    size_t platform_index;
    size_t platform_count;
    size_t preset_index;
    size_t preset_count;
    uint8_t key_index;
    bool key_selected;
    size_t lighting_level_index;
    size_t lighting_level_count;
    size_t haptic_level_index;
    size_t haptic_level_count;
} quick_config_model_t;

typedef struct {
    size_t profile_index;
    size_t profile_count;
    size_t platform_index;
    size_t platform_count;
    size_t preset_index;
    size_t preset_count;
    size_t lighting_level_index;
    size_t lighting_level_count;
    size_t haptic_level_index;
    size_t haptic_level_count;
} quick_config_model_init_t;

void quick_config_model_begin(quick_config_model_t *model,
                              const quick_config_model_init_t *initial);
void quick_config_model_move_item(quick_config_model_t *model, int direction);
void quick_config_model_adjust(quick_config_model_t *model, int direction);
void quick_config_model_select_key(quick_config_model_t *model, uint8_t key_index,
                                   size_t preset_index);
quick_config_command_t quick_config_model_confirm(quick_config_model_t *model);

#ifdef __cplusplus
}
#endif
