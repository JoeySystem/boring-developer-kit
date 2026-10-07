#include "claude_code_shortcuts.h"

enum {
    HID_KEY_C = 6,
    HID_KEY_G = 10,
    HID_KEY_L = 15,
    HID_KEY_O = 18,
    HID_KEY_P = 19,
    HID_KEY_R = 21,
    HID_KEY_T = 23,
    HID_KEY_V = 25,
    HID_KEY_ENTER = 40,
    HID_KEY_ESCAPE = 41,
    HID_KEY_TAB = 43,
    HID_KEY_SPACE = 44,
    HID_KEY_ARROW_RIGHT = 79,
    HID_KEY_ARROW_LEFT = 80,
    HID_KEY_ARROW_DOWN = 81,
    HID_KEY_ARROW_UP = 82,
    HID_MODIFIER_LEFT_CONTROL = 224,
    HID_MODIFIER_LEFT_SHIFT = 225,
    HID_MODIFIER_LEFT_ALT = 226,
    HID_MODIFIER_LEFT_GUI = 227,
};

#define SHORTCUT(key, name) \
    {key, {0, 0}, 0, name}
#define MODIFIED_SHORTCUT(key, modifier, name) \
    {key, {modifier, 0}, 1, name}

/*
 * These bindings follow Claude Code's interactive-mode controls.  The last
 * key deliberately uses the host's normal text-paste chord, which is the one
 * platform-specific action in this fixed layer.
 */
static const claude_code_shortcut_t COMMON_KEY_SHORTCUTS[] = {
    SHORTCUT(HID_KEY_ENTER, "SUBMIT"),
    SHORTCUT(HID_KEY_ESCAPE, "INTERRUPT"),
    MODIFIED_SHORTCUT(HID_KEY_C, HID_MODIFIER_LEFT_CONTROL, "CANCEL"),
    MODIFIED_SHORTCUT(HID_KEY_TAB, HID_MODIFIER_LEFT_SHIFT, "PERMISSION"),
    MODIFIED_SHORTCUT(HID_KEY_O, HID_MODIFIER_LEFT_CONTROL, "TRANSCRIPT"),
    MODIFIED_SHORTCUT(HID_KEY_R, HID_MODIFIER_LEFT_CONTROL, "HISTORY"),
    MODIFIED_SHORTCUT(HID_KEY_L, HID_MODIFIER_LEFT_CONTROL, "REDRAW"),
    SHORTCUT(HID_KEY_SPACE, "VOICE"),
    MODIFIED_SHORTCUT(HID_KEY_T, HID_MODIFIER_LEFT_CONTROL, "TASKS"),
    MODIFIED_SHORTCUT(HID_KEY_G, HID_MODIFIER_LEFT_CONTROL, "EDITOR"),
    MODIFIED_SHORTCUT(HID_KEY_P, HID_MODIFIER_LEFT_ALT, "MODEL"),
};

static const claude_code_shortcut_t JOYSTICK_SHORTCUTS[] = {
    SHORTCUT(HID_KEY_ARROW_UP, "HISTORY UP"),
    SHORTCUT(HID_KEY_ARROW_DOWN, "HISTORY DOWN"),
    SHORTCUT(HID_KEY_ARROW_LEFT, "CURSOR LEFT"),
    SHORTCUT(HID_KEY_ARROW_RIGHT, "CURSOR RIGHT"),
};

bool claude_code_shortcut_for_key(bool macos, size_t key_index,
                                  claude_code_shortcut_t *shortcut)
{
    if (shortcut == NULL || key_index >= 12) {
        return false;
    }
    if (key_index < sizeof(COMMON_KEY_SHORTCUTS) /
                        sizeof(COMMON_KEY_SHORTCUTS[0])) {
        *shortcut = COMMON_KEY_SHORTCUTS[key_index];
        return true;
    }
    *shortcut = (claude_code_shortcut_t)MODIFIED_SHORTCUT(
        HID_KEY_V,
        macos ? HID_MODIFIER_LEFT_GUI : HID_MODIFIER_LEFT_CONTROL,
        "PASTE");
    return true;
}

bool claude_code_shortcut_for_joystick_direction(
    size_t direction_index, claude_code_shortcut_t *shortcut)
{
    if (shortcut == NULL ||
        direction_index >= sizeof(JOYSTICK_SHORTCUTS) /
                               sizeof(JOYSTICK_SHORTCUTS[0])) {
        return false;
    }
    *shortcut = JOYSTICK_SHORTCUTS[direction_index];
    return true;
}

bool claude_code_shortcut_for_joystick_press(
    claude_code_shortcut_t *shortcut)
{
    if (shortcut == NULL) {
        return false;
    }
    *shortcut = (claude_code_shortcut_t)SHORTCUT(HID_KEY_TAB, "COMPLETE");
    return true;
}

bool claude_code_shortcut_for_encoder_press(
    claude_code_shortcut_t *shortcut)
{
    if (shortcut == NULL) {
        return false;
    }
    *shortcut = (claude_code_shortcut_t)SHORTCUT(HID_KEY_ENTER, "SUBMIT");
    return true;
}
