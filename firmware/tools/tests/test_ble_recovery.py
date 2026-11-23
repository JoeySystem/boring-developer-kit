"""BLE start-up recovery: bounded in-place init retry and a crash-loop guard.

CP01-14C19F200A48 failed BLE controller init after warm resets (NO_MEM, or a
ROM assert that the interrupt watchdog turned into a reset loop) and worked
after power-on. The firmware retries init in place (each failed init powers
the BT domain down) and skips BLE after two consecutive crashes during BLE
start-up, so USB always stays usable. Start-up lasts until the HID services
are ready or start-up fails cleanly, so a controller-task assert after init
returned still counts. The guard lives in RTC memory and only counts when its
magic and check word verify.
"""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/codex_micro"
MODEL = COMPONENT / "ble_recovery_model.c"
BLE = COMPONENT / "codex_micro_ble.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"

LIMIT = 2
RETRIES = 2
ESP_OK = 0
ESP_ERR_NO_MEM = 0x101

# esp_reset_reason_t values used by the harness
POWERON, SW, PANIC, INT_WDT, TASK_WDT, WDT, BROWNOUT = 1, 3, 4, 5, 6, 7, 9


def build(directory, name, code):
    path = Path(directory) / f"{name}.c"
    path.write_text(code)
    library = path.with_suffix(".so")
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-function", "-I", str(COMPONENT / "include"),
                    str(path), str(MODEL), "-o", str(library)],
                   check=True, capture_output=True)
    return ctypes.CDLL(str(library))


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.lib = build(cls.temp.name, "model", r'''
#include <string.h>
#include "ble_recovery_model.h"
static ble_crash_guard_t guard;
static uint32_t loaded_count;
static bool loaded_starting;
void garbage(unsigned magic, unsigned count, unsigned starting, unsigned check) {
    guard.magic = magic; guard.crash_loop_count = count;
    guard.ble_starting = starting; guard.check = check;
}
bool load(void) { return ble_crash_guard_load(&guard, &loaded_count, &loaded_starting); }
unsigned count(void) { return loaded_count; }
bool starting(void) { return loaded_starting; }
void store(unsigned count, bool starting) { ble_crash_guard_store(&guard, count, starting); }
unsigned raw_count(void) { return guard.crash_loop_count; }
unsigned next(unsigned count, bool power_on, bool crash, bool in_ble) {
    return ble_crash_guard_next(count, power_on, crash, in_ble);
}
bool skip(unsigned count) { return ble_crash_guard_skip_ble(count); }
const char *name(int result) { return ble_recovery_result_name((ble_recovery_result_t)result); }
''')
        for name in ("skip", "load", "starting"):
            getattr(cls.lib, name).restype = ctypes.c_bool
        cls.lib.name.restype = ctypes.c_char_p
        cls.lib.next.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
        cls.lib.store.argtypes = [ctypes.c_uint, ctypes.c_bool]

    def test_stored_record_round_trips(self):
        for count in range(LIMIT + 1):
            for starting in (False, True):
                self.lib.store(count, starting)
                self.assertTrue(self.lib.load())
                self.assertEqual((count, starting), (self.lib.count(), self.lib.starting()))

    def test_unverified_memory_reads_as_zero_and_not_starting(self):
        magic = 0x424C4552
        for raw in ((0, 0, 0, 0), (0xFFFFFFFF, 1, 1, 0xFFFFFFFF), (magic, 1, 1, 0),
                    (magic, 99, 0, ~(magic ^ 99) & 0xFFFFFFFF),
                    (magic, 1, 7, ~(magic ^ 1 ^ (7 << 16)) & 0xFFFFFFFF),
                    # a flipped starting bit no longer matches the check word
                    (magic, 1, 1, ~(magic ^ 1) & 0xFFFFFFFF)):
            with self.subTest(raw=[hex(v) for v in raw]):
                self.lib.garbage(*raw)
                self.assertFalse(self.lib.load())
                self.assertEqual((0, False), (self.lib.count(), self.lib.starting()))

    def test_store_saturates_at_the_limit(self):
        self.lib.store(LIMIT + 5, True)
        self.assertEqual(LIMIT, self.lib.raw_count())
        self.assertTrue(self.lib.load())
        self.assertEqual(LIMIT, self.lib.count())

    def test_only_crashes_during_ble_start_count(self):
        self.assertEqual(1, self.lib.next(0, False, True, True))
        self.assertEqual(LIMIT, self.lib.next(LIMIT, False, True, True))
        self.assertEqual(0, self.lib.next(1, True, True, True))    # power-on
        self.assertEqual(0, self.lib.next(1, False, False, True))  # e.g. menu restart
        self.assertEqual(0, self.lib.next(1, False, True, False))  # crash elsewhere

    def test_skip_starts_at_the_limit(self):
        self.assertFalse(self.lib.skip(LIMIT - 1))
        self.assertTrue(self.lib.skip(LIMIT))

    def test_result_names_match_get_status(self):
        self.assertEqual([b"none", b"recovered_in_place", b"failed", b"skipped_after_crash_loop"],
                         [self.lib.name(i) for i in range(4)])


GUARD_HARNESS = r'''
#include <stdbool.h>
#include <stdint.h>
#include "ble_recovery_model.h"
typedef int esp_reset_reason_t;
enum { ESP_RST_POWERON = 1, ESP_RST_SW = 3, ESP_RST_PANIC = 4, ESP_RST_INT_WDT = 5,
       ESP_RST_TASK_WDT = 6, ESP_RST_WDT = 7, ESP_RST_BROWNOUT = 9 };
static ble_crash_guard_t s_ble_crash_guard;
static uint32_t s_ble_crash_loop_count;
static int reason;
static esp_reset_reason_t esp_reset_reason(void) { return reason; }
'''


class CrashLoopGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE.read_text()
        code = GUARD_HARNESS
        for signature in ("static bool reset_was_crash(esp_reset_reason_t reason)",
                          "static bool ble_crash_loop_guard_trips(void)",
                          "static void ble_start_ended(void)\n{"):
            code += c_function(source, signature) + "\n"
        code += r'''
/* One boot: the guard runs; if BLE is started, it either finishes (services
 * ready or a clean error) or the chip resets while it is still starting. */
bool boot(int r) { reason = r; return ble_crash_loop_guard_trips(); }
void finish(void) { ble_start_ended(); }
unsigned count(void) { return s_ble_crash_loop_count; }
void scramble(void) { s_ble_crash_guard.magic = 0x12345678; s_ble_crash_guard.check = 1; }
'''
        cls.lib = build(cls.temp.name, "guard", code)
        cls.lib.boot.restype = ctypes.c_bool

    def setUp(self):
        self.lib.boot(POWERON)
        self.lib.finish()

    def crash_while_starting(self, kind=INT_WDT):
        """Previous boot was reset while BLE was still starting."""
        return self.lib.boot(kind)

    def test_two_crashes_during_ble_start_skip_the_third_boot(self):
        self.lib.boot(POWERON)                      # start, then crash
        self.assertFalse(self.crash_while_starting())
        self.assertTrue(self.crash_while_starting())
        self.assertEqual(LIMIT, self.lib.count())

    def test_every_crash_kind_counts(self):
        for kind in (PANIC, INT_WDT, TASK_WDT, WDT):
            with self.subTest(kind=kind):
                self.lib.boot(POWERON)
                self.crash_while_starting(kind)
                self.assertTrue(self.crash_while_starting(kind))

    def test_crash_after_services_ready_does_not_count(self):
        self.lib.boot(POWERON)
        self.lib.finish()                           # HID services ready
        self.assertFalse(self.lib.boot(INT_WDT))
        self.assertEqual(0, self.lib.count())

    def test_clean_init_failure_then_crash_does_not_count(self):
        self.lib.boot(POWERON)
        self.lib.finish()                           # start_ble_stack() error
        self.lib.boot(PANIC)
        self.assertEqual(0, self.lib.count())

    def test_skipped_boot_then_restart_tries_ble_again(self):
        self.lib.boot(POWERON)
        self.crash_while_starting()
        self.assertTrue(self.crash_while_starting())
        # The skipped boot does not start BLE, so a later crash or restart
        # gives BLE another try.
        self.assertFalse(self.lib.boot(SW))
        self.assertFalse(self.lib.boot(INT_WDT))

    def test_power_on_brownout_and_menu_restart_do_not_count(self):
        for kind in (POWERON, BROWNOUT, SW):
            with self.subTest(kind=kind):
                self.lib.boot(POWERON)
                self.crash_while_starting()
                self.assertFalse(self.lib.boot(kind))
                self.assertEqual(0, self.lib.count())

    def test_scrambled_rtc_memory_starts_from_zero(self):
        self.lib.boot(POWERON)
        self.crash_while_starting()
        self.lib.scramble()
        self.assertFalse(self.lib.boot(INT_WDT))
        self.assertEqual(0, self.lib.count())


RETRY_HARNESS = r'''
#include <stdbool.h>
#include <stdint.h>
#include "ble_recovery_model.h"
typedef int esp_err_t;
#define ESP_OK 0
typedef struct { int marker; } esp_bt_controller_config_t;
#define BT_CONTROLLER_INIT_CONFIG_DEFAULT() {.marker = 42}
#define ESP_LOGW(tag, ...) ((void)(tag))
#define pdMS_TO_TICKS(ms) (ms)
static const char *TAG = "test";
static const char *esp_err_to_name(esp_err_t e) { (void)e; return "e"; }
static unsigned delays, delay_ms, calls, fresh_configs;
static esp_err_t script[8];
static void vTaskDelay(unsigned ms) { delays++; delay_ms = ms; }
static esp_err_t esp_bt_controller_init(esp_bt_controller_config_t *cfg)
{
    if (cfg->marker == 42) fresh_configs++;
    cfg->marker = 0; /* init may scribble on its argument */
    return script[calls++];
}
static ble_recovery_result_t s_ble_recovery_result;
static uint32_t s_ble_recovery_attempts;
static esp_err_t s_ble_recovery_last_error;
static bool ble_fault_fail_init(void) { return false; }
#define ESP_ERR_NO_MEM 0x101
'''


class InPlaceRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = BLE.read_text()
        code = (RETRY_HARNESS
                + c_function(source, "static esp_err_t controller_init_once(") + "\n"
                + c_function(source, "static esp_err_t init_controller_with_retry(void)")
                + r'''
int run(int a, int b, int c) {
    script[0] = a; script[1] = b; script[2] = c; script[3] = 99;
    delays = calls = fresh_configs = 0; delay_ms = 0;
    s_ble_recovery_result = BLE_RECOVERY_RESULT_NONE;
    s_ble_recovery_attempts = 0; s_ble_recovery_last_error = 0;
    return init_controller_with_retry();
}
unsigned n_calls(void) { return calls; }
unsigned n_delays(void) { return delays; }
unsigned last_delay(void) { return delay_ms; }
unsigned n_fresh(void) { return fresh_configs; }
int result(void) { return s_ble_recovery_result; }
unsigned attempts(void) { return s_ble_recovery_attempts; }
int last_error(void) { return s_ble_recovery_last_error; }
''')
        cls.lib = build(cls.temp.name, "retry", code)

    def test_success_first_time_records_nothing(self):
        self.assertEqual(ESP_OK, self.lib.run(ESP_OK, 0, 0))
        self.assertEqual((1, 0, 0), (self.lib.n_calls(), self.lib.n_delays(), self.lib.result()))

    def test_second_attempt_recovers_in_place(self):
        self.assertEqual(ESP_OK, self.lib.run(ESP_ERR_NO_MEM, ESP_OK, 0))
        self.assertEqual(2, self.lib.n_calls())
        self.assertEqual(1, self.lib.attempts())
        self.assertEqual(1, self.lib.result())              # recovered_in_place
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.last_error())
        self.assertEqual(100, self.lib.last_delay())

    def test_retries_stop_after_two_and_report_failed(self):
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.run(ESP_ERR_NO_MEM, ESP_ERR_NO_MEM, ESP_ERR_NO_MEM))
        self.assertEqual(1 + RETRIES, self.lib.n_calls())
        self.assertEqual(RETRIES, self.lib.attempts())
        self.assertEqual(2, self.lib.result())              # failed
        self.assertEqual(ESP_ERR_NO_MEM, self.lib.last_error())

    def test_each_attempt_gets_a_fresh_default_config(self):
        self.lib.run(ESP_ERR_NO_MEM, ESP_ERR_NO_MEM, ESP_OK)
        self.assertEqual(self.lib.n_calls(), self.lib.n_fresh())


class WiringTests(unittest.TestCase):
    def test_guard_runs_before_ble_starts_and_clean_failures_end_start_up(self):
        source = BLE.read_text()
        init = c_function(source, "esp_err_t codex_micro_init(")
        guard = init.index("if (ble_crash_loop_guard_trips()) {")
        self.assertLess(init.index("result = load_ble_peer_slots();"), guard)
        self.assertLess(guard, init.index("result = start_ble_stack();"))
        self.assertIn("result = start_ble_stack();\n    if (result != ESP_OK) {\n"
                      "        ble_start_ended();\n    }\n    return s_init_error = result;", init)
        stack = c_function(source, "static esp_err_t start_ble_stack(void)")
        self.assertLess(stack.index("esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT)"),
                        stack.index("result = init_controller_with_retry();"))
        self.assertNotIn("s_init_error", stack)

    def test_services_ready_ends_start_up(self):
        handler = c_function(BLE.read_text(), "static void hid_event(void *handler_args")
        start = handler[handler.index("case ESP_HIDD_START_EVENT:"):]
        start = start[:start.index("break;")]
        self.assertLess(start.index("atomic_store(&s_hid_services_ready, true);"),
                        start.index("ble_start_ended();"))

    def test_guard_memory_survives_warm_resets(self):
        self.assertIn("static RTC_NOINIT_ATTR ble_crash_guard_t s_ble_crash_guard;", BLE.read_text())

    def test_get_status_reports_ble_recovery(self):
        protocol = PROTOCOL.read_text()
        for key in ('"ble_recovery"', '"attempts_in_place"', '"last_error"',
                    '"reset_reason"', '"crash_loop_count"'):
            self.assertIn(key, protocol)
        self.assertIn(': "none");', protocol)


class FaultInjectionBuildTests(unittest.TestCase):
    """The scripted faults exist only in the blefault test build."""

    def test_switch_defaults_off_and_only_the_test_defaults_enable_it(self):
        kconfig = (COMPONENT / "Kconfig").read_text()
        block = kconfig[kconfig.index("config CODEX_MICRO_BLE_FAULT_INJECTION"):]
        self.assertIn("default n", block.split("help")[0])
        enabling = [path.name for path in ROOT.glob("sdkconfig*.defaults")
                    if "CONFIG_CODEX_MICRO_BLE_FAULT_INJECTION=y" in path.read_text()]
        self.assertEqual(["sdkconfig.ble-fault-injection.defaults"], enabling)

    def test_build_script_makes_it_a_fixed_custom_power_v2_build(self):
        build = (ROOT / "tools/build.sh").read_text()
        guard = build[build.index('if [ "$ble_fault_injection" = true ]; then'):]
        guard = guard[:guard.index("\nfi\n")]
        self.assertIn('[ "$official_build" = false ]', guard)
        self.assertIn('[ "$custom_selected" = false ]', guard)
        self.assertIn("matrix12-power-v2-codex-usb", guard)
        self.assertIn("custom_name=blefault", guard)
        self.assertIn('sdkconfig_defaults="$sdkconfig_defaults;sdkconfig.ble-fault-injection.defaults"', build)

    def test_release_builds_compile_the_hooks_to_nothing(self):
        source = BLE.read_text()
        inert = source[source.index("#else\nstatic inline void ble_fault_next_boot(void) {}"):]
        inert = inert[:inert.index("#endif")]
        self.assertIn("static inline bool ble_fault_fail_init(void) { return false; }", inert)
        self.assertIn("static inline void ble_fault_panic_at(uint32_t step) { (void)step; }", inert)
        stack = c_function(source, "static esp_err_t start_ble_stack(void)")
        self.assertIn("ble_fault_panic_at(3);", stack)
        handler = c_function(source, "static void hid_event(void *handler_args")
        self.assertLess(handler.index("ble_fault_panic_at(4);"), handler.index("ble_start_ended();"))


if __name__ == "__main__":
    unittest.main()
