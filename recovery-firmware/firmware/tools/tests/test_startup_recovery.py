"""Run production boot decisions and delayed self-test with hardware stubs."""
import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path
def c_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    while ";" in source[start:opening]:
        start = source.index(signature, start + len(signature))
        opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function: {signature}")

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "components/action_engine/action_engine.c"

class StartupRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = SOURCE.read_text()
        code = r'''#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "under_key_self_test.h"
#define CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 1
#define ESP_RST_SW 3
#define STATUS_INDICATOR_LED_COUNT 6
static int reset_reason;
static bool held, saved_on, sample_valid;
static float voltage;
typedef struct { bool present; float voltage_v; } fuel_gauge_sample_t;
static int esp_reset_reason(void) { return reset_reason; }
static bool encoder_power_button_pressed_at_boot(void) { return held; }
static bool config_store_get_user_powered_on(void) { return saved_on; }
static int config_store_set_user_powered_on(bool v) { saved_on=v; return 0; }
static bool fuel_gauge_latest(fuel_gauge_sample_t *b) {
    b->present=sample_valid; b->voltage_v=voltage; return sample_valid;
}
typedef uint32_t TickType_t;
typedef under_key_self_test_rgb_t board_rgb_t;
static bool s_under_key_self_test_active;
static size_t s_under_key_self_test_last_step;
static TickType_t s_under_key_self_test_started_at, now;
static board_rgb_t visible[12];
static unsigned restore_count;
static TickType_t xTaskGetTickCount(void) { return now; }
static uint32_t ticks_to_ms(TickType_t v) { return v; }
static size_t board_under_key_rgb_count(void) { return 12; }
static void apply_feedback_config(void) { restore_count++; memset(visible,0,sizeof(visible)); }
static void render_status_output(board_rgb_t *v) { memset(v,0,6*sizeof(*v)); }
static int board_apply_rgb(const board_rgb_t *status, const board_rgb_t *keys) {
    memcpy(visible,keys,sizeof(visible)); return 0;
}
'''
        code += c_function(source, "static bool should_start_usb_charge_session(void)")
        code += c_function(source, "static void update_under_key_self_test(void)")
        code += r'''
int boot(int reason, bool pressed, bool on, bool valid, float volts) {
    reset_reason=reason; held=pressed; saved_on=on; sample_valid=valid; voltage=volts;
    return should_start_usb_charge_session();
}
int saved(void) { return saved_on; }
void reset_lights(void) {
    now=0; s_under_key_self_test_started_at=0;
    s_under_key_self_test_active=true; s_under_key_self_test_last_step=SIZE_MAX;
    restore_count=0; memset(visible,0,sizeof(visible));
}
int tick(uint32_t t) {
    now=t; update_under_key_self_test();
    for(int i=0;i<12;i++) if(visible[i].red || visible[i].green || visible[i].blue) return i;
    return -1;
}
int active(void) { return s_under_key_self_test_active; }
'''
        path = Path(cls.temp.name)/"startup.c"
        path.write_text(code)
        lib = path.with_suffix(".dylib")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-I",
            str(ROOT/"components/action_engine/include"), str(path),
            str(ROOT/"components/action_engine/under_key_self_test.c"),
            "-o", str(lib)],check=True,capture_output=True)
        cls.lib=ctypes.CDLL(str(lib))
        cls.lib.boot.argtypes=[ctypes.c_int,ctypes.c_bool,ctypes.c_bool,ctypes.c_bool,ctypes.c_float]
        cls.lib.tick.argtypes=[ctypes.c_uint32]

    def test_unavailable_first_sample_preserves_and_resumes_saved_on(self):
        for reason in (1,4,5,6,9):
            with self.subTest(reason=reason):
                self.assertEqual(self.lib.boot(reason,False,True,False,0),0)
                self.assertEqual(self.lib.saved(),1)

    def test_explicit_off_remains_off_even_if_gauge_unavailable(self):
        for valid in (False,True):
            self.assertEqual(self.lib.boot(1,False,False,valid,3.8),1)
            self.assertEqual(self.lib.saved(),0)

    def test_button_and_software_restart_keep_existing_behavior(self):
        self.assertEqual(self.lib.boot(1,True,False,False,0),0)
        self.assertEqual(self.lib.saved(),1)
        for on in (False,True):
            self.assertEqual(self.lib.boot(3,False,on,False,0),not on)
            self.assertEqual(self.lib.saved(),on)

    def test_valid_battery_presence_policy_is_unchanged(self):
        self.assertEqual(self.lib.boot(1,False,True,True,3.8),0)
        self.assertEqual(self.lib.boot(1,False,True,True,0),1)

    def test_slow_initialization_does_not_consume_self_test(self):
        for delay in (1500,5000):
            self.lib.reset_lights()
            for i in range(12):
                self.assertEqual(self.lib.tick(delay+i*120),i)
            self.assertEqual(self.lib.tick(delay+1440),-1)
            self.assertEqual(self.lib.active(),0)

    def test_action_init_defers_self_test_until_polling(self):
        init=c_function(SOURCE.read_text(),"esp_err_t action_engine_init(void)")
        self.assertNotIn("update_under_key_self_test();",init)

if __name__ == "__main__":
    unittest.main()
