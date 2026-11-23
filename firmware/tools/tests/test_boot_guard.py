"""Stability items 5 and 6: boot crash-loop guard and safe mode.

Three abnormal resets in a row (panic, interrupt/task/other watchdog, CPU
lock-up) start the next boot in safe mode; a boot that runs out of memory
(or whose BLE start-up does) restarts once straight into safe mode; Key 1 +
Key 12 held from power-up (checked before board_init, confirmed on release)
start that boot in safe mode; a safe-mode boot stores limit - 1
so a restart tries a normal boot and one more crash returns to safe mode. A
normal boot that runs 60 s clears the count. Power-on, software, brownout and
USB resets neither count nor clear. The count is kept in RTC memory and
mirrored to NVS.

Safe mode never reads the stored config: the built-in defaults run read-only
(the user's slots stay untouched), BLE is skipped, USB maintenance stays
available, HELLO reports reason safe_mode and GET_STATUS reports safe_mode.
"""

import ctypes
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/boot_guard"
MODEL = COMPONENT / "boot_guard_model.c"
GLUE = COMPONENT / "boot_guard.c"
APP_MAIN = ROOT / "main/app_main.c"
STORE = ROOT / "components/config_store/config_store.c"
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"
ACTION = ROOT / "components/action_engine/action_engine.c"
BUILD = ROOT / "tools/build.sh"
LIMIT = 3


class Decision(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("safe_mode", ctypes.c_bool),
                ("stored_count", ctypes.c_uint32)]


class LowMemory(ctypes.Structure):
    _fields_ = [("low", ctypes.c_bool), ("low_since_ms", ctypes.c_uint32)]


class Record(ctypes.Structure):
    _fields_ = [("magic", ctypes.c_uint32), ("count", ctypes.c_uint32),
                ("check", ctypes.c_uint32)]


class BootGuardModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        library = Path(cls.temp.name) / "guard.so"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                        "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.boot_guard_decide.restype = Decision
        cls.lib.boot_guard_decide.argtypes = [ctypes.c_uint32, ctypes.c_bool]
        cls.lib.boot_guard_record_load.restype = ctypes.c_bool
        cls.lib.boot_guard_stable.restype = ctypes.c_bool
        cls.lib.boot_guard_stable.argtypes = [ctypes.c_bool, ctypes.c_uint32]
        cls.lib.boot_guard_low_memory_due.restype = ctypes.c_bool
        cls.lib.boot_guard_low_memory_due.argtypes = [ctypes.POINTER(LowMemory), ctypes.c_bool,
                                                      ctypes.c_uint32, ctypes.c_bool,
                                                      ctypes.c_bool]

    def run_boots(self, resets):
        """resets: per boot, whether the reset before it was abnormal."""
        stored, decisions = 0, []
        for abnormal in resets:
            decision = self.lib.boot_guard_decide(stored, abnormal)
            decisions.append(decision)
            stored = decision.stored_count
        return decisions

    def test_three_crashes_in_a_row_enter_safe_mode(self):
        decisions = self.run_boots([True, True, True])
        self.assertEqual([1, 2, 3], [d.count for d in decisions])
        self.assertEqual([False, False, True], [d.safe_mode for d in decisions])

    def test_restart_after_safe_mode_tries_a_normal_boot(self):
        decisions = self.run_boots([True, True, True, False])
        self.assertFalse(decisions[-1].safe_mode)
        self.assertEqual(LIMIT - 1, decisions[-1].count)

    def test_one_more_crash_after_safe_mode_returns_to_safe_mode(self):
        decisions = self.run_boots([True, True, True, False, True])
        self.assertTrue(decisions[-1].safe_mode)

    def test_crash_while_in_safe_mode_stays_in_safe_mode(self):
        decisions = self.run_boots([True, True, True, True])
        self.assertTrue(decisions[-1].safe_mode)

    def test_normal_resets_neither_count_nor_clear(self):
        decisions = self.run_boots([True, False, False, True])
        self.assertEqual([1, 1, 1, 2], [d.count for d in decisions])

    def test_stable_boot_clears_only_outside_safe_mode(self):
        self.assertFalse(self.lib.boot_guard_stable(False, 59999))
        self.assertTrue(self.lib.boot_guard_stable(False, 60000))
        self.assertFalse(self.lib.boot_guard_stable(True, 10 ** 7))

    def test_record_verifies(self):
        record = Record()
        count = ctypes.c_uint32(99)
        self.assertFalse(self.lib.boot_guard_record_load(ctypes.byref(record), ctypes.byref(count)))
        self.assertEqual(0, count.value)
        self.lib.boot_guard_record_store(ctypes.byref(record), 2)
        self.assertTrue(self.lib.boot_guard_record_load(ctypes.byref(record), ctypes.byref(count)))
        self.assertEqual(2, count.value)
        record.count = 3
        self.assertFalse(self.lib.boot_guard_record_load(ctypes.byref(record), ctypes.byref(count)))

    def low_memory(self, samples, safe_mode=False, update_active=False):
        state = LowMemory()
        for now, low in samples:
            if self.lib.boot_guard_low_memory_due(ctypes.byref(state), low, now,
                                                  safe_mode, update_active):
                return now
        return None

    def test_lasting_exhaustion_during_boot_is_a_failure(self):
        samples = [(t, t >= 15000) for t in range(0, 40000, 500)]
        self.assertEqual(25000, self.low_memory(samples))

    def test_short_dips_and_late_exhaustion_are_ignored(self):
        dips = [(t, (t // 5000) % 2 == 1) for t in range(0, 60000, 500)]
        self.assertIsNone(self.low_memory(dips))
        late = [(t, t >= 55000) for t in range(0, 120000, 500)]
        self.assertIsNone(self.low_memory(late))

    def test_never_in_safe_mode_or_during_an_update(self):
        samples = [(t, True) for t in range(0, 40000, 500)]
        self.assertIsNone(self.low_memory(samples, safe_mode=True))
        self.assertIsNone(self.low_memory(samples, update_active=True))

    def test_garbage_count_saturates(self):
        self.assertTrue(self.lib.boot_guard_decide(1000, False).safe_mode)


class BootGuardWiringTests(unittest.TestCase):
    def test_only_crashes_and_watchdogs_count(self):
        abnormal = c_function(GLUE.read_text(), "static bool abnormal_reset(esp_reset_reason_t reason)")
        for reason in ("ESP_RST_PANIC", "ESP_RST_INT_WDT", "ESP_RST_TASK_WDT", "ESP_RST_WDT",
                       "ESP_RST_CPU_LOCKUP"):
            self.assertIn(reason, abnormal)
        for reason in ("ESP_RST_BROWNOUT", "ESP_RST_SW", "ESP_RST_POWERON", "ESP_RST_USB"):
            self.assertNotIn(reason, abnormal)

    def test_rtc_is_trusted_only_across_resets_that_keep_it(self):
        start = c_function(GLUE.read_text(), "void boot_guard_start(void)")
        self.assertIn("rtc_survives(reason) &&", start)
        self.assertIn("const uint32_t previous = rtc_valid ? rtc_count : nvs_count;", start)
        self.assertIn("nvs_mirror_write(decision.stored_count)", start)

    def test_guard_runs_before_any_store_and_selects_the_safe_path(self):
        main = c_function(APP_MAIN.read_text(), "void app_main(void)")
        self.assertLess(main.index("boot_guard_start();"), main.index("board_init()"))
        self.assertLess(main.index("boot_guard_start();"), main.index("config_store_init"))
        self.assertIn("safe_mode ? config_store_init_safe_mode() : config_store_init()", main)
        self.assertIn("boot_guard_poll(", main[main.index("for (;;) {"):])

    def test_unusable_boot_restarts_once_straight_into_safe_mode(self):
        # Review A1: memory exhaustion (or BLE start-up out of memory) marks
        # the next boot as safe mode in RTC memory and restarts once.
        glue = GLUE.read_text()
        restart = c_function(glue, "void boot_guard_restart_into_safe_mode(boot_guard_reason_t reason)")
        self.assertLess(restart.index("if (s_status.safe_mode) {"),
                        restart.index("esp_restart();"))
        self.assertLess(restart.index("s_safe_next.magic = SAFE_NEXT_MAGIC;"),
                        restart.index("esp_restart();"))
        start = c_function(glue, "void boot_guard_start(void)")
        self.assertIn("reason == ESP_RST_SW && s_safe_next.magic == SAFE_NEXT_MAGIC", start)
        self.assertIn("memset(&s_safe_next, 0, sizeof(s_safe_next));", start)
        poll = c_function(glue, "void boot_guard_poll(uint32_t main_loop_ms, bool memory_low, bool update_active)")
        self.assertIn("boot_guard_restart_into_safe_mode(BOOT_GUARD_REASON_LOW_MEMORY);", poll)
        main = c_function(APP_MAIN.read_text(), "void app_main(void)")
        self.assertIn("MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA", main)
        self.assertIn("boot_guard_poll(main_loop_ms, memory_low, firmware_update_is_active());",
                      main)
        self.assertIn("codex_micro_result == ESP_ERR_NO_MEM && !safe_mode", main)
        self.assertIn("boot_guard_restart_into_safe_mode(BOOT_GUARD_REASON_BLE_NO_MEM);", main)

    def test_count_belongs_to_the_running_image(self):
        start = c_function(GLUE.read_text(), "void boot_guard_start(void)")
        self.assertIn("s_record_image == image", start)
        self.assertIn("if (nvs_image != image) {", start)

    def test_manual_combination_is_checked_before_board_init(self):
        source = APP_MAIN.read_text()
        main = c_function(source, "void app_main(void)")
        self.assertLess(main.index("boot_combo_armed_before_board(&boot_combo)"),
                        main.index("ESP_ERROR_CHECK(board_init());"))
        self.assertLess(main.index("ESP_ERROR_CHECK(board_init());"),
                        main.index("boot_combo_confirm(&boot_combo);"))
        self.assertLess(main.index("boot_combo_confirm(&boot_combo);"),
                        main.index("const bool safe_mode = boot_guard_safe_mode();"))
        self.assertLess(main.index("const bool safe_mode = boot_guard_safe_mode();"),
                        main.index("config_store_init"))
        confirm = c_function(source, "static void boot_combo_confirm(boot_combo_t *combo)")
        # Found on 68DE8: board_init leaves the backlight off, so the prompt
        # must light it first.
        self.assertLess(confirm.index("board_set_display_config(BOOT_PROMPT_BRIGHTNESS_PERCENT, 0);"),
                        confirm.index("board_display_show_local("))
        self.assertLess(confirm.index("board_display_show_local("),
                        confirm.index("boot_guard_request_safe_mode();"))
        self.assertIn("if (state == BOOT_COMBO_CONFIRMED) {", confirm)
        sample = c_function(source, "static boot_combo_state_t sample_combo(boot_combo_t *combo, bool at_boot)")
        self.assertIn("BOARD_CONTROL_KEY_1", sample)
        self.assertIn("BOARD_CONTROL_KEY_12", sample)
        self.assertNotIn("ENCODER", sample)
        request = c_function(GLUE.read_text(), "void boot_guard_request_safe_mode(void)")
        self.assertIn("BOOT_GUARD_REASON_MANUAL", request)
        self.assertNotIn("boot_guard_record_store", request)

    def test_safe_mode_allows_only_an_explicit_factory_reset(self):
        # Review B9: the one config write in safe mode; it restarts after the ACK.
        protocol = PROTOCOL.read_text()
        case = protocol[protocol.index("case WMP_MSG_FACTORY_DEFAULT: {"):
                        protocol.index("case WMP_MSG_FW_CONFIRM:")]
        branch = case[case.index("if (store_status.safe_mode) {"):]
        self.assertLess(branch.index("config_store_safe_mode_factory_reset("),
                        branch.index("send_frame(WMP_MSG_ACK"))
        self.assertLess(branch.index("send_frame(WMP_MSG_ACK"),
                        branch.index("atomic_store(&s_safe_mode_restart_pending, true);"))
        reset = c_function(STORE.read_text(), "esp_err_t config_store_safe_mode_factory_reset(")
        self.assertLess(reset.index("if (!s_safe_mode) {"), reset.index("nvs_erase_key("))
        self.assertIn("CONFIG_FACTORY_CONFIRMATION", reset)
        self.assertNotIn("CONFIG_KEY_USER_POWERED_ON", reset)

    def test_safe_mode_store_never_reads_slots(self):
        safe = c_function(STORE.read_text(), "esp_err_t config_store_init_safe_mode(void)")
        self.assertNotIn("load_from_storage", safe)
        self.assertNotIn("read_slot", safe)
        self.assertIn("run_without_storage(", safe)
        self.assertIn("s_safe_mode = true;", safe)

    def test_safe_mode_skips_ble(self):
        source = BLE.read_text()
        init = source[source.index("esp_err_t codex_micro_init("):]
        skip = init.index("if (boot_guard_safe_mode()) {")
        self.assertLess(skip, init.index("start_ble_stack();"))
        # Found on 68DE8: the closed config store failed the BLE name read
        # first, so the skip must come before any BLE storage access.
        self.assertLess(skip, init.index("config_store_get_ble_name("))
        self.assertLess(init.index("s_rx_mutex = xSemaphoreCreateMutex();"), skip)
        self.assertIn("BLE_RECOVERY_RESULT_SKIPPED_SAFE_MODE", init[skip:skip + 300])

    def test_safe_mode_is_reported(self):
        protocol = PROTOCOL.read_text()
        self.assertIn(': status.safe_mode ? "safe_mode"', protocol)
        self.assertIn("add_boot_guard_status(result);", protocol)
        for key in ('"safe_mode"', '"abnormal_resets"', '"reset_reason"', '"stable_cleared"'):
            self.assertIn(key, protocol)

    def test_safe_mode_home_shows_fixed_text(self):
        show = c_function(ACTION.read_text(), "static void show_status(const char *hint)")
        self.assertIn("if (boot_guard_safe_mode()) {", show)
        # Interim product text: repair guidance, no "safe mode".
        self.assertIn('"REPAIR", "PLUG USB", "OPEN APP",', show)

    def test_crash_fault_injection_is_test_only(self):
        build = BUILD.read_text()
        self.assertIn("--crash-fault-injection) crash_fault_injection=true", build)
        self.assertIn("custom_name=crashfault", build)
        self.assertIn('[ "$official_build" = false ] || { echo "Crash fault injection must be a custom test build"',
                      build)
        kconfig = (COMPONENT / "Kconfig").read_text()
        self.assertRegex(kconfig, r"config WMP_CRASH_FAULT_INJECTION\n\s+bool.*\n\s+default n")
        enabling = [p.name for p in ROOT.glob("sdkconfig*.defaults")
                    if "CONFIG_WMP_CRASH_FAULT_INJECTION=y" in p.read_text()]
        self.assertEqual(["sdkconfig.crash-fault-injection.defaults"], enabling)


if __name__ == "__main__":
    unittest.main()
