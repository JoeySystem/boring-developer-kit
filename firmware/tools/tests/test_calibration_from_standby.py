"""The service tool calibrates a factory unit right after its first formal
firmware boot, which leaves it in USB standby / the charge splash or on the
first-boot system selection page. Calibration must start there without
switching the unit on, and hand a borrowed page its input back."""

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_lighting_preview_protocol import c_function


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "components/action_engine/action_engine.c"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"

ENABLED, LOCAL_UI, DIAGNOSTIC, DIAG_WAIT, CALIBRATION, WAIT_NEUTRAL = range(6)


class CalibrationOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = ACTION.read_text()
        start = source.index("static bool s_calibration_borrowed_local_ui;")
        function = c_function(source, "static void poll_calibration_ownership(void)")
        code = r'''
#include <stdbool.h>
#include <stddef.h>
enum { HOST_OUTPUT_ENABLED, HOST_OUTPUT_LOCAL_UI, HOST_OUTPUT_DIAGNOSTIC,
       HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL, HOST_OUTPUT_CALIBRATION,
       HOST_OUTPUT_WAIT_NEUTRAL };
enum { LOCAL_PAGE_NONE, LOCAL_PAGE_SETTINGS };
static int s_host_output_state, s_local_page;
static bool s_quick_config_active, s_shutdown_active, s_usb_standby_active,
    s_battery_splash_active, s_system_select_active, requested, interlock,
    owned, update_active;
static struct { bool active; } s_idle_standby;
static int s_diagnostic_capture, s_diagnostic_encoder_press_active;
static unsigned renders, system_renders;
static bool config_protocol_calibration_ownership_requested(void) { return requested; }
static bool config_protocol_calibration_output_interlock_active(void) { return interlock; }
static void config_protocol_release_calibration_output_interlock(void) { interlock = false; }
static void config_protocol_set_calibration_ownership(bool v) { owned = v; if (v) interlock = true; }
static bool firmware_update_is_active(void) { return update_active; }
static void reset_mode_sensitive_input_state(void) {}
static void codex_micro_set_input_suppressed(bool v) { (void)v; }
static bool diagnostic_capture_model_owns_input(int *c) { (void)c; return false; }
static void diagnostic_capture_model_yield_to_local(int *c) { (void)c; }
static void publish_diagnostic_capture_status(void) {}
static void render_local_page(void) { renders++; }
static void render_system_select(const char *f) { (void)f; system_renders++; }
''' + source[start:source.index("\n", start)] + "\n" + function + r'''
void setup(int state, int page, bool usb_standby, bool splash, bool quick) {
    s_host_output_state = state; s_local_page = page;
    s_usb_standby_active = usb_standby; s_battery_splash_active = splash;
    s_quick_config_active = quick; s_shutdown_active = false; s_idle_standby.active = false;
    s_system_select_active = false; requested = false; interlock = false; owned = false;
    update_active = false; renders = system_renders = 0;
}
void request(bool v) { requested = v; poll_calibration_ownership(); }
int state(void) { return s_host_output_state; }
bool is_owned(void) { return owned; }
bool interlocked(void) { return interlock; }
bool standby(void) { return s_usb_standby_active; }
unsigned page_renders(void) { return renders; }
'''
        path = Path(cls.temp.name) / "ownership.c"
        path.write_text(code)
        library = path.with_suffix(".so")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", str(path), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.setup.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_bool,
                                  ctypes.c_bool, ctypes.c_bool]
        cls.lib.request.argtypes = [ctypes.c_bool]
        for name in ("is_owned", "interlocked", "standby"):
            getattr(cls.lib, name).restype = ctypes.c_bool

    def test_usb_standby_and_splash_grant_calibration_and_stay_off(self):
        for standby, splash in ((True, False), (False, True)):
            with self.subTest(standby=standby, splash=splash):
                self.lib.setup(ENABLED, 0, standby, splash, False)
                self.lib.request(True)
                self.assertEqual(self.lib.state(), CALIBRATION)
                self.assertTrue(self.lib.is_owned())
                self.lib.request(False)
                self.assertEqual(self.lib.state(), WAIT_NEUTRAL)
                self.assertEqual(self.lib.standby(), standby)

    def test_local_page_lends_input_and_gets_it_back(self):
        self.lib.setup(LOCAL_UI, 1, False, False, False)
        self.lib.request(True)
        self.assertEqual(self.lib.state(), CALIBRATION)
        self.lib.request(False)
        self.assertEqual(self.lib.state(), LOCAL_UI)
        self.assertFalse(self.lib.is_owned())
        self.assertFalse(self.lib.interlocked())
        self.assertEqual(self.lib.page_renders(), 1)
        self.lib.request(False)  # idle polls change nothing
        self.assertEqual(self.lib.state(), LOCAL_UI)
        self.assertEqual(self.lib.page_renders(), 1)

    def test_quick_config_still_blocks(self):
        self.lib.setup(ENABLED, 0, False, False, True)
        self.lib.request(True)
        self.assertEqual(self.lib.state(), ENABLED)
        self.assertFalse(self.lib.is_owned())

    def test_protocol_accepts_standby_and_local_ui(self):
        source = PROTOCOL.read_text()
        start = source.index("case WMP_MSG_CALIBRATION_START: {")
        block = source[start:source.index("case WMP_MSG_CALIBRATION_SAMPLE", start)]
        guard = block[block.index("if (status.pending"):block.index("send_nack(request_id, \"CALIBRATION_START\", WMP_ERROR_BUSY")]
        self.assertNotIn("s_action_usb_standby_active", guard)
        self.assertNotIn("s_action_local_page", guard)
        self.assertIn('"local_ui"', block)
        for still_blocking in ("status.pending", "s_action_quick_config_active",
                               "s_action_idle_standby_active", "firmware_update_is_active()"):
            self.assertIn(still_blocking, guard)


class DiagnosticCaptureFromStandbyTests(unittest.TestCase):
    """After calibration the tool verifies four directions with diagnostic
    capture; it must start in the same states and leave them unchanged."""

    def setUp(self):
        self.engine = ACTION.read_text()
        self.protocol = PROTOCOL.read_text()

    def test_protocol_gate_matches_calibration(self):
        gate = c_function(self.protocol, "static bool diagnostic_capture_start_allowed(void)")
        self.assertNotIn("s_action_usb_standby_active", gate)
        self.assertNotIn("s_action_local_page", gate)
        self.assertIn('"local_ui"', gate)
        for still_blocking in ("s_action_quick_config_active", "s_action_idle_standby_active",
                               "firmware_update_is_active()"):
            self.assertIn(still_blocking, gate)

    def test_engine_gate_and_return_path(self):
        allowed = c_function(self.engine, "static bool diagnostic_capture_can_start_now(void)")
        self.assertNotIn("s_usb_standby_active", allowed)
        self.assertNotIn("s_battery_splash_active", allowed)
        self.assertNotIn("s_local_page", allowed)
        poll = c_function(self.engine, "static void poll_diagnostic_capture(void)")
        borrowed = poll[poll.index("if (s_diagnostic_borrowed_local_ui)"):]
        self.assertIn("s_host_output_state = HOST_OUTPUT_LOCAL_UI;", borrowed)
        self.assertIn("render_local_page();", borrowed)
        # An OFF unit is not drawn back to the home screen.
        self.assertLess(poll.index("if (s_usb_standby_active)"), poll.index("show_status(NULL);"))
        self.assertIn("set_usb_standby_outputs();", poll)
        self.assertIn("!s_battery_splash_active", poll)


class StandbyInterlockReleaseTests(unittest.TestCase):
    """A session that ends in USB standby must not leave the output interlock
    set: GET_STATUS would report inputs_neutral=false forever and the tool's
    idle acceptance after calibration fails."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        source = ACTION.read_text()
        code = r'''
#include <stdbool.h>
enum { HOST_OUTPUT_ENABLED, HOST_OUTPUT_LOCAL_UI, HOST_OUTPUT_DIAGNOSTIC,
       HOST_OUTPUT_DIAGNOSTIC_WAIT_NEUTRAL, HOST_OUTPUT_CALIBRATION,
       HOST_OUTPUT_WAIT_NEUTRAL };
static int s_host_output_state;
static bool requested, interlock, neutral, unsuppressed, redrawn;
static bool config_protocol_calibration_ownership_requested(void) { return requested; }
static bool config_protocol_calibration_output_interlock_active(void) { return interlock; }
static void config_protocol_release_calibration_output_interlock(void) { interlock = false; }
static bool board_keys_neutral(void) { return neutral; }
static void codex_micro_set_input_suppressed(bool v) { if (!v) unsuppressed = true; }
static void show_status(const char *h) { (void)h; redrawn = true; }
''' + c_function(source, "static void poll_standby_input_ownership(void)") + r'''
void setup(int state, bool req, bool lock, bool keys) {
    s_host_output_state = state; requested = req; interlock = lock; neutral = keys;
    unsuppressed = redrawn = false;
}
void poll(void) { poll_standby_input_ownership(); }
int state(void) { return s_host_output_state; }
bool locked(void) { return interlock; }
bool touched_outputs(void) { return unsuppressed || redrawn; }
'''
        path = Path(cls.temp.name) / "standby.c"
        path.write_text(code)
        library = path.with_suffix(".so")
        subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", str(path), "-o", str(library)],
                       check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.setup.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
        cls.lib.locked.restype = ctypes.c_bool
        cls.lib.touched_outputs.restype = ctypes.c_bool

    def test_interlock_released_once_neutral_without_waking(self):
        self.lib.setup(WAIT_NEUTRAL, False, True, True)
        self.lib.poll()
        self.assertFalse(self.lib.locked())
        self.assertEqual(self.lib.state(), ENABLED)
        self.assertFalse(self.lib.touched_outputs())

    def test_waits_for_neutral_and_for_the_session_to_end(self):
        for requested, keys in ((False, False), (True, True)):
            self.lib.setup(WAIT_NEUTRAL, requested, True, keys)
            self.lib.poll()
            self.assertTrue(self.lib.locked())
            self.assertEqual(self.lib.state(), WAIT_NEUTRAL)

    def test_standby_branch_calls_it(self):
        poll = c_function(ACTION.read_text(), "void action_engine_poll(void)")
        branch = poll[poll.index("if (s_usb_standby_active) {"):]
        self.assertLess(branch.index("poll_standby_input_ownership();"),
                        branch.index("return;"))


if __name__ == "__main__":
    unittest.main()
