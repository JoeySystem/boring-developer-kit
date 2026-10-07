import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path


FIRMWARE_ROOT = Path(__file__).resolve().parents[2]
ACTION_ROOT = FIRMWARE_ROOT / "components/action_engine"
BOARD_ROOT = FIRMWARE_ROOT / "components/board"
USB_ROOT = FIRMWARE_ROOT / "components/usb_device"


class UsbConnectionFeedback(ctypes.Structure):
    _fields_ = [
        ("label", ctypes.c_char_p),
        ("red", ctypes.c_uint8),
        ("green", ctypes.c_uint8),
        ("blue", ctypes.c_uint8),
    ]


class PowerBootPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        source = (ACTION_ROOT / "action_engine.c").read_text()
        start = source.index("static bool should_start_usb_charge_session(void)")
        policy = source[start:source.index("static bool key_status_index_for_key", start)]
        shutdown_start = source.index("void action_engine_request_shutdown(void)")
        retry_start = source.index("    if (s_shutdown_active) {", shutdown_start)
        retry = source[retry_start:source.index(
            "\n#if CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2\n    (void)config_store_set_user_powered_on(false);",
            retry_start)]
        harness = root / "power_boot.c"
        harness.write_text(r'''
#include <stdbool.h>
#define CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 1
enum { ESP_RST_POWERON=1, ESP_RST_SW=3, ESP_RST_BROWNOUT=9 };
typedef struct { bool present, valid; float voltage_v; } fuel_gauge_sample_t;
static fuel_gauge_sample_t battery;
static bool fuel_gauge_latest(fuel_gauge_sample_t *out) {
    *out=battery; return battery.valid;
}
void set_battery(bool present, bool valid, float voltage) {
    battery=(fuel_gauge_sample_t){present, valid, voltage};
}
static int reason;
static bool saved_on, button;
static int esp_reset_reason(void) { return reason; }
static bool config_store_get_user_powered_on(void) { return saved_on; }
static int config_store_set_user_powered_on(bool on) { saved_on=on; return 0; }
static bool encoder_power_button_pressed_at_boot(void) { return button; }
''' + policy + r'''
int boot(int reset, bool old_on, bool pressed) {
    reason=reset; saved_on=old_on; button=pressed;
    return should_start_usb_charge_session();
}
int persisted_on(void) { return saved_on; }
static bool s_shutdown_active, s_shutdown_pulsed;
static int resumed_standby;
static void codex_micro_prepare_soft_restart(void) {}
static void esp_restart(void) { resumed_standby=should_start_usb_charge_session(); }
static void retry_power_button(void) {
''' + retry + r'''
}
int charger_second_long_press(void) {
    reason=ESP_RST_SW; saved_on=false; button=true;
    s_shutdown_active=s_shutdown_pulsed=true;
    resumed_standby=-1;
    retry_power_button();
    return resumed_standby;
}
''')
        library = root / "power_boot.dylib"
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", str(harness),
                        "-o", str(library)], check=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.boot.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_bool]
        cls.lib.set_battery.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_float]

    def setUp(self):
        self.lib.set_battery(False, False, 0)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_unavailable_gauge_preserves_last_power_intent_across_restart(self):
        for reason in (1, 9):
            for old_on in (False, True):
                with self.subTest(reason=reason, old_on=old_on):
                    self.assertEqual(self.lib.boot(reason, old_on, False), not old_on)
                    self.assertEqual(self.lib.persisted_on(), old_on)
                    self.assertEqual(self.lib.boot(3, self.lib.persisted_on(), False), not old_on)

    def test_software_restart_restores_both_power_states_even_if_control_is_held(self):
        for old_on in (False, True):
            for pressed in (False, True):
                with self.subTest(old_on=old_on, pressed=pressed):
                    self.assertEqual(self.lib.boot(3, old_on, pressed), not old_on)
                    self.assertEqual(self.lib.persisted_on(), old_on)

    def test_physical_power_button_cold_boot_still_turns_on(self):
        for old_on in (False, True):
            self.assertEqual(self.lib.boot(1, old_on, True), 0)
            self.assertEqual(self.lib.persisted_on(), 1)

    def test_battery_on_session_survives_usb_attach_and_detach_resets(self):
        self.lib.set_battery(True, True, 3.8)
        # Start on the battery by the physical power key. Then model MCU
        # restarts at USB attach and detach with the key released.
        self.assertEqual(self.lib.boot(1, False, True), 0)
        for reason in (1, 9, 1):
            with self.subTest(reason=reason):
                self.assertEqual(self.lib.boot(reason, self.lib.persisted_on(), False), 0)
                self.assertEqual(self.lib.persisted_on(), 1)

    def test_explicit_off_stays_off_with_a_valid_battery(self):
        self.lib.set_battery(True, True, 4.2)
        for reason in (1, 3, 9):
            with self.subTest(reason=reason):
                self.assertEqual(self.lib.boot(reason, False, False), 1)
                self.assertEqual(self.lib.persisted_on(), 0)

    def test_readable_gauge_without_usable_cell_voltage_cannot_resume_stale_on(self):
        for present, valid, voltage in (
            (False, True, 3.8),
            (True, True, 0), (True, True, 2.4), (True, True, 5.0),
        ):
            with self.subTest(present=present, valid=valid, voltage=voltage):
                self.lib.set_battery(present, valid, voltage)
                self.assertEqual(self.lib.boot(1, True, False), 1)
                self.assertEqual(self.lib.persisted_on(), 0)

    def test_unavailable_sample_resumes_saved_on_then_accepts_recovered_gauge(self):
        for present, voltage in ((False, 0), (True, 3.8)):
            self.lib.set_battery(present, False, voltage)
            self.assertEqual(self.lib.boot(1, True, False), 0)
            self.assertEqual(self.lib.persisted_on(), 1)
            self.lib.set_battery(True, True, 3.8)
            self.assertEqual(self.lib.boot(1, self.lib.persisted_on(), False), 0)

    def test_software_restart_keeps_intent_when_battery_sample_is_unavailable(self):
        self.lib.set_battery(True, False, 0)
        self.assertEqual(self.lib.boot(3, True, False), 0)
        self.assertEqual(self.lib.persisted_on(), 1)

    def test_shutdown_in_progress_cannot_restart_or_persist_on(self):
        self.assertEqual(self.lib.charger_second_long_press(), -1)
        self.assertEqual(self.lib.persisted_on(), 0)

    def test_menu_host_restart_and_ota_do_not_force_user_on(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        restart = source[source.index("void action_engine_request_restart(void)"):
                         source.index("void action_engine_request_shutdown(void)")]
        self.assertNotIn("config_store_set_user_powered_on(true)", restart)
        self.assertIn("esp_restart();", restart)
        ota = (FIRMWARE_ROOT / "components/firmware_update/firmware_update.c").read_text()
        self.assertIn("esp_restart();", ota)
        self.assertNotIn("config_store_set_user_powered_on", ota)


class UsbConnectionFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        library = Path(cls.temp_dir.name) / "libusb_connection_feedback.dylib"
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-shared",
                "-fPIC",
                "-I",
                str(ACTION_ROOT / "include"),
                str(ACTION_ROOT / "usb_connection_feedback.c"),
                "-o",
                str(library),
            ],
            check=True,
        )
        cls.library = ctypes.CDLL(str(library))
        cls.library.usb_connection_feedback.argtypes = [ctypes.c_bool]
        cls.library.usb_connection_feedback.restype = UsbConnectionFeedback

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_unmounted_host_is_visibly_offline(self):
        feedback = self.library.usb_connection_feedback(False)
        self.assertEqual(feedback.label, b"USB OFFLINE")
        self.assertGreater(feedback.red, feedback.green)
        self.assertEqual(feedback.blue, 0)

    def test_mounted_host_is_visibly_ready(self):
        feedback = self.library.usb_connection_feedback(True)
        self.assertEqual(feedback.label, b"USB READY")
        self.assertGreater(feedback.green, feedback.red)
        self.assertGreater(feedback.green, feedback.blue)

    def test_usb_mount_state_requires_a_live_ready_bus(self):
        source = (USB_ROOT / "usb_service.c").read_text()
        self.assertIn("static atomic_bool s_mounted", source)
        self.assertIn("atomic_store(&s_mounted", source)
        mounted_start = source.index("bool usb_service_is_mounted(void)")
        mounted_end = source.index(
            "bool usb_service_config_session_active(void)", mounted_start
        )
        mounted = source[mounted_start:mounted_end]
        self.assertIn("atomic_load(&s_mounted)", mounted)
        self.assertIn("tud_ready()", mounted)

        desired_start = source.index("static usb_service_route_t desired_hid_route")
        desired_end = source.index("static bool route_ready", desired_start)
        desired = source[desired_start:desired_end]
        self.assertIn("usb_service_is_mounted()", desired)
        self.assertNotIn("atomic_load(&s_mounted)", desired)

    def test_status_chain_is_committed_in_one_refresh(self):
        engine_source = (ACTION_ROOT / "action_engine.c").read_text()
        board_source = (BOARD_ROOT / "board_rev_a.c").read_text()
        apply_start = engine_source.index("static void apply_status_output(void)")
        apply_status = engine_source[
            apply_start : engine_source.index("static void apply_lighting_output(void)", apply_start)
        ]
        self.assertIn("board_apply_status_rgb(status)", apply_status)
        self.assertIn("esp_err_t board_apply_status_rgb", board_source)
        self.assertIn("return led_strip_refresh(s_status_strip)", board_source)

    def test_home_is_ready_only_when_the_active_output_is_connected(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        self.assertIn(
            'status.output_connected ? "READY" : "CHECK CABLE"',
            source,
        )

    def test_user_off_usb_wake_uses_battery_splash_but_power_button_boots(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        config_header = (
            FIRMWARE_ROOT / "components/config_store/include/config_store.h"
        ).read_text()
        config_source = (
            FIRMWARE_ROOT / "components/config_store/config_store.c"
        ).read_text()
        infer_start = source.index("static bool should_start_usb_charge_session(")
        infer_end = source.index("static bool key_status_index_for_key", infer_start)
        infer = source[infer_start:infer_end]
        wake_start = source.index(
            "static bool encoder_power_button_pressed_at_boot(void)"
        )
        wake_end = source.index(
            "static bool should_start_usb_charge_session(", wake_start
        )
        wake = source[wake_start:wake_end]
        initial_start = source.index("static void enter_initial_page(void)\n{")
        initial_end = source.index("static void finish_battery_splash", initial_start)
        initial = source[initial_start:initial_end]
        splash_start = source.index("static void start_usb_battery_splash(void)\n{")
        splash_end = source.index("static void enter_usb_standby", splash_start)
        splash = source[splash_start:splash_end]
        finish_start = source.index("static void finish_battery_splash(void)")
        finish_end = source.index("static void poll_round_feedback(void)", finish_start)
        finish = source[finish_start:finish_end]
        shutdown_start = source.index("void action_engine_request_shutdown(void)")
        shutdown_end = source.index("void action_engine_poll(void)", shutdown_start)
        shutdown = source[shutdown_start:shutdown_end]

        self.assertIn("board_get_active_controls", wake)
        self.assertIn("BOARD_CONTROL_ENCODER_PRESS", wake)
        self.assertIn("esp_reset_reason() == ESP_RST_SW", infer)
        self.assertIn("return !config_store_get_user_powered_on();", infer)
        self.assertIn("if (encoder_power_button_pressed_at_boot())", infer)
        self.assertIn("config_store_set_user_powered_on(true)", infer)
        self.assertLess(
            infer.index("esp_reset_reason() == ESP_RST_SW"),
            infer.index("if (encoder_power_button_pressed_at_boot())"),
        )
        self.assertNotIn("ESP_RST_POWERON", infer)
        self.assertIn("config_store_set_user_powered_on(false)", infer)
        self.assertIn("if (s_usb_charge_session_at_boot)", initial)
        self.assertIn("start_usb_battery_splash();", initial)
        self.assertLess(
            initial.index("start_usb_battery_splash();"),
            initial.index("board_display_boot_should_start("),
        )
        self.assertNotIn("mist_start_motion", splash)
        self.assertIn("render_mist_battery();", splash)
        battery_render_start = source.index("static void render_mist_battery(void)\n{")
        battery_render = source[battery_render_start:source.index("static void render_mist_connection(void)\n{", battery_render_start)]
        self.assertIn(".page = BOARD_MIST_BATTERY", battery_render)
        self.assertIn(".a = current_battery_percent(NULL)", battery_render)
        self.assertIn(".b = s_battery_splash_active ? 1 : 0", battery_render)
        self.assertIn(
            "board_set_display_config(s_feedback.display_brightness,",
            splash,
        )
        self.assertIn("s_feedback.display_rotation);", splash)
        self.assertLess(
            splash.index("board_set_display_config("),
            splash.index("render_mist_battery();"),
        )
        self.assertNotIn("show_status(NULL)", splash)
        self.assertIn("enter_usb_standby();", finish)
        self.assertIn("s_usb_standby_active || s_battery_splash_active", shutdown)
        self.assertIn("wake_from_usb_standby();", shutdown)
        self.assertNotIn("usb_service_is_mounted()", shutdown)
        self.assertIn("config_store_set_user_powered_on(false)", shutdown)
        self.assertNotIn("enter_usb_standby();", shutdown)
        shutdown_poll = source[source.index("void action_engine_poll(void)"):]
        self.assertLess(shutdown_poll.index("board_power_request_shutdown();"),
                        shutdown_poll.index("enter_usb_standby();"))
        wake_start = source.index("static void wake_from_usb_standby(void)\n{")
        wake_end = source.index("static void poll_round_feedback", wake_start)
        wake = source[wake_start:wake_end]
        self.assertIn("config_store_set_user_powered_on(true)", wake)

        self.assertIn("bool config_store_get_user_powered_on(void);", config_header)
        self.assertIn(
            "esp_err_t config_store_set_user_powered_on(bool powered_on);",
            config_header,
        )
        self.assertIn('#define CONFIG_KEY_USER_POWERED_ON "user_on"', config_source)
        self.assertNotIn(
            "board_power_button_is_pressed",
            (BOARD_ROOT / "include/board.h").read_text(),
        )

    def test_missing_saved_power_state_defaults_to_user_off(self):
        source = (
            FIRMWARE_ROOT / "components/config_store/config_store.c"
        ).read_text()
        getter_start = source.index("bool config_store_get_user_powered_on(void)")
        getter_end = source.index(
            "esp_err_t config_store_set_user_powered_on", getter_start
        )
        getter = source[getter_start:getter_end]

        missing_state = getter.index("error == ESP_ERR_NVS_NOT_FOUND")
        saved_state = getter.index("return error != ESP_OK || powered_on != 0;")
        self.assertLess(missing_state, saved_state)
        self.assertIn("return false;", getter[missing_state:saved_state])

    def test_formal_session_usb_changes_cannot_enter_charge_standby(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        sync_start = source.index("static void sync_connection_and_mode_state(void)\n{")
        sync_end = source.index("esp_err_t action_engine_init(void)", sync_start)
        sync = source[sync_start:sync_end]

        self.assertIn("usb_service_is_mounted()", sync)
        self.assertIn("show_status(NULL)", sync)
        self.assertNotIn("start_usb_battery_splash", sync)
        self.assertNotIn("enter_usb_standby", sync)
        self.assertNotIn("set_usb_standby_outputs", sync)

    def test_user_off_standby_shows_battery_again_on_a_new_usb_attach(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        start = source[
            source.index("static void start_usb_battery_splash(void)\n{") :
            source.index("static void enter_usb_standby", source.index(
                "static void start_usb_battery_splash(void)\n{"
            ))
        ]
        standby_poll = source[
            source.index("static void poll_usb_standby(void)\n{") :
            source.index("static void enter_idle_standby", source.index(
                "static void poll_usb_standby(void)\n{"
            ))
        ]
        poll = source[
            source.index("void action_engine_poll(void)\n{") :
            source.index("bool action_engine_config_activation_ready", source.index(
                "void action_engine_poll(void)\n{"
            ))
        ]

        self.assertIn("s_usb_standby_active = false;", start)
        self.assertIn("usb_service_is_mounted()", standby_poll)
        self.assertIn("mounted && !s_usb_mounted", standby_poll)
        self.assertIn("start_usb_battery_splash();", standby_poll)
        self.assertIn("poll_usb_standby();", poll)

    def test_usb_battery_splash_retries_until_percentage_is_valid(self):
        source = (ACTION_ROOT / "action_engine.c").read_text()
        fuel_header = (
            FIRMWARE_ROOT / "components/fuel_gauge/include/cw2015.h"
        ).read_text()
        fuel_source = (
            FIRMWARE_ROOT / "components/fuel_gauge/cw2015.c"
        ).read_text()
        start = source[
            source.index("static void start_usb_battery_splash(void)\n{") :
            source.index("static void enter_usb_standby", source.index(
                "static void start_usb_battery_splash(void)\n{"
            ))
        ]
        poll = source[
            source.index("static void poll_battery_status(void)\n{") :
            source.index("static uint32_t ticks_to_ms", source.index(
                "static void poll_battery_status(void)\n{"
            ))
        ]

        self.assertIn("#define BATTERY_SPLASH_RETRY_MS 200", source)
        self.assertIn("#define BATTERY_SPLASH_MAX_WAIT_MS 3000", source)
        self.assertIn("#define BATTERY_SPLASH_VALID_DURATION_MS 3000", source)
        self.assertIn("fuel_gauge_request_immediate_poll();", start)
        self.assertIn("fuel_gauge_request_immediate_poll();", poll)
        self.assertIn("s_battery_splash_valid_at", poll)
        self.assertIn("s_battery_splash_next_retry_at", poll)
        self.assertIn("void fuel_gauge_request_immediate_poll(void);", fuel_header)
        self.assertIn("s_next_poll_ms = 0;", fuel_source[
            fuel_source.index("void fuel_gauge_request_immediate_poll(void)") :
            fuel_source.index("bool fuel_gauge_latest(")
        ])


if __name__ == "__main__":
    unittest.main()
