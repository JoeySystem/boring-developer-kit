"""The repair state never asks the user to pick an OS.

Found on 68DE8 (2026-11-11, custom-stability-20261111.04-g6461486b): after
Key1+Key12 boot the screen first showed the OS picker (and its "MAC 1/5"
text page); only after choosing Mac did the repair instruction appear. The
repair state runs the built-in defaults, which have no OS, so both the boot
page and the connection poll started first-time OS setup. A choice there
cannot be saved (store is read-only) and only hides the repair instruction.

This compiles the real enter_initial_page() and poll_platform_context()
with stubs and checks both paths.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "components/action_engine/action_engine.c"


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#define CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 1
typedef enum { CONFIG_PLATFORM_UNSELECTED, CONFIG_PLATFORM_MACOS,
               CONFIG_PLATFORM_WINDOWS_LINUX } config_platform_t;
typedef struct { bool activation_failed; } config_store_status_t;
typedef enum { POMODORO_IDLE, POMODORO_ALERT } pomodoro_state_t;
typedef enum { LOCAL_PAGE_NONE, LOCAL_PAGE_SYSTEM } local_page_t;
enum { STATUS_FEEDBACK_ERROR };

static bool safe_mode, usb_mounted_now, ble_now;
static config_platform_t platform = CONFIG_PLATFORM_UNSELECTED;
static int system_select_calls, os_settings_calls, status_calls, boot_calls;

static bool s_usb_charge_session_at_boot;
static bool s_firmware_error;
static bool s_system_save_pending, s_platform_setup_needed, s_usb_mounted,
    s_ble_connected, s_shutdown_active, s_usb_standby_active,
    s_battery_splash_active, s_boot_animation_active, s_quick_config_active,
    s_function_key_gesture_active;
static struct { bool active; } s_idle_standby;
static int s_diagnostic_capture;
static struct { pomodoro_state_t state; } s_pomodoro;
static local_page_t s_local_page;
static uint8_t s_platform_context = UINT8_MAX;

static bool boot_guard_safe_mode(void) { return safe_mode; }
static bool board_is_product_target(void) { return true; }
static config_platform_t config_store_get_platform(void) { return platform; }
static bool board_display_boot_should_start(bool p, bool e, bool s)
{ (void)p; (void)e; (void)s; return false; }
static void start_usb_battery_splash(void) {}
static void start_boot_animation(void) { ++boot_calls; }
static void show_status(const char *hint) { (void)hint; ++status_calls; }
static void enter_system_select(void) { ++system_select_calls; }
static void enter_os_settings(bool from_settings)
{ (void)from_settings; ++os_settings_calls; }
static bool usb_service_is_mounted(void) { return usb_mounted_now; }
static bool codex_micro_ble_connected(void) { return ble_now; }
static uint8_t codex_micro_active_ble_slot(void) { return 1; }
static void config_store_set_platform_context(uint8_t c) { (void)c; }
static void config_store_get_status(config_store_status_t *s)
{ s->activation_failed = false; }
static void leave_local_input_ownership(void) {}
static void show_feedback(int f, const char *t) { (void)f; (void)t; }
static bool diagnostic_capture_model_owns_input(const int *m)
{ (void)m; return false; }
static bool config_store_has_pending(void) { return false; }
'''

MAIN = r'''
static void reset(bool safe)
{
    safe_mode = safe;
    system_select_calls = os_settings_calls = status_calls = boot_calls = 0;
    s_platform_setup_needed = false;
    s_usb_mounted = false;
    s_ble_connected = false;
}

int main(void)
{
    /* Normal first boot still asks for the OS (unchanged behaviour). */
    reset(false);
    enter_initial_page();
    assert(system_select_calls == 1);

    /* Repair state: straight to the status page, no OS picker. */
    reset(true);
    enter_initial_page();
    assert(system_select_calls == 0);
    assert(status_calls == 1);

    /* Normal: a new USB connection without an OS opens OS setup. */
    reset(false);
    usb_mounted_now = true;
    poll_platform_context();
    assert(os_settings_calls == 1);

    /* Repair state: the same connection never opens OS setup. */
    reset(true);
    usb_mounted_now = true;
    poll_platform_context();
    poll_platform_context();
    assert(os_settings_calls == 0);
    assert(!s_platform_setup_needed);
    puts("ok");
    return 0;
}
'''


class SafeModeSkipsOsSetupTests(unittest.TestCase):
    def test_repair_state_never_opens_os_setup(self):
        source = ACTION.read_text()
        code = (PREAMBLE
                + c_function(source, "static void enter_initial_page(")
                + "\n"
                + c_function(source, "static void poll_platform_context(")
                + MAIN)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_os_setup.c"
            path.write_text(code)
            binary = path.with_suffix("")
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-function", str(path), "-o", str(binary)],
                           check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
