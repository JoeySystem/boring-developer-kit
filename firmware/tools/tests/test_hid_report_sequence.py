"""Run production action dispatch and HID routing C; capture submitted report bytes.

Only board/config lookups, unrelated action effects and USB/BLE drivers are stubs.
The NORMAL encoder branch, synthetic tap scheduler, ownership and HID poll are
compiled from the current sources, not reimplemented in a Python mock device.
"""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def function(source, name):
    import re
    match = re.search(r'^(?:static )?[\w *]+\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    start = match.start()
    pos = source.index('{', match.start()) + 1
    depth = 1
    while depth:
        depth += (source[pos] == '{') - (source[pos] == '}')
        pos += 1
    return source[start:pos]


class HidReportSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / 'esp_err.h').write_text('typedef int esp_err_t;\n#define ESP_OK 0\n')
        (root / 'cJSON.h').write_text('typedef struct cJSON cJSON;\n')
        usb = (ROOT / 'components/usb_device/usb_service.c').read_text()
        action = (ROOT / 'components/action_engine/action_engine.c').read_text()
        declarations = usb[usb.index('typedef enum {\n    USB_SERVICE_ROUTE_NONE'):usb.index('static char s_serial')]
        # Keep production queue capacity declarations as they are introduced.
        import re
        constants = '\n'.join(re.findall(r'^#define (?:HID_|KEYBOARD_)[^\n]*', usb, re.M))
        functions = usb[usb.index('static bool valid_source('):]
        encoder = action[action.index('    if ((event->control == BOARD_CONTROL_ENCODER_CCW'):action.index('\nstatic void route_tap(')]
        code = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdatomic.h>
#include "usb_service.h"
#include "config_store.h"
#define USB_HID_INSTANCE_STANDARD 0
#define CONFIG_CODEX_MICRO_USB_ENABLED 0
typedef void *TaskHandle_t;
''' + constants + '\n' + declarations + r'''
/* Remote wakeup and link diagnostics are covered by test_usb_remote_wakeup. */
static void request_remote_wakeup(void) {}
static void poll_remote_wakeup(void) {}
usb_service_link_state_t usb_service_link_state(void) { return USB_SERVICE_LINK_NONE; }
void config_protocol_set_usb_link_diagnostics(uint8_t state, uint32_t wakeups)
    { (void)state; (void)wakeups; }
static bool ready = true, ble = false;
static bool calibration_interlock;
bool config_protocol_calibration_output_interlock_active(void) {
    return calibration_interlock;
}
static unsigned captured;
static uint8_t report_ids[2048], reports[2048][8], sinks[2048];
static bool capture(unsigned sink, unsigned id, const void *data, size_t size) {
    if (!ready) return false;
    report_ids[captured]=id; sinks[captured]=sink;
    memset(reports[captured],0,8); memcpy(reports[captured],data,size);
    captured++; return true;
}
bool usb_service_is_mounted(void) { return atomic_load(&s_mounted); }
static bool tud_hid_n_ready(unsigned instance) { return ready; }
static bool tud_hid_n_report(unsigned instance, unsigned id, const void *data, uint16_t n) {
    return capture(1,id,data,n);
}
static bool ble_connected(void *ctx) { return ble; }
static int ble_send(uint8_t id, const uint8_t *data, size_t n, void *ctx) {
    return capture(2,id,data,n) ? 0 : -1;
}
static void poll_cdc_tx(void) {}
static void clear_cdc_tx_queue(void) {}
''' + functions + r'''
typedef unsigned board_control_t;
enum { BOARD_CONTROL_ENCODER_CW=12, BOARD_CONTROL_ENCODER_CCW=13, BOARD_CONTROL_COUNT=20 };
typedef struct { board_control_t control; bool pressed; } board_event_t;
static config_action_t configured, s_active_actions[BOARD_CONTROL_COUNT];
static bool s_active_action_valid[BOARD_CONTROL_COUNT];
typedef unsigned TickType_t;
#define KEY_PULSE_HOLD_MS 60u
static bool s_tap_release_pending[BOARD_CONTROL_COUNT];
static TickType_t s_tap_release_at[BOARD_CONTROL_COUNT];
#define MACRO_SOURCE 20
#define MACRO_TRANSIENT_SOURCE 21
#define pdMS_TO_TICKS(ms) (ms)
static struct { bool active; unsigned id; size_t index; unsigned wait_until;
    uint16_t release_usage; bool release_shift; char text[257]; size_t text_index; } s_macro;
static unsigned now;
static unsigned xTaskGetTickCount(void) { return now; }
static config_macro_step_t macro_steps[8];
static size_t macro_count;
bool config_store_get_macro_step(uint8_t id,size_t index,config_macro_step_t *out,size_t *count) {
    *count=macro_count; if(index>=macro_count) return false;
    *out=macro_steps[index]; return true;
}
#define STATUS_FEEDBACK_SUCCESS 0
#define STATUS_FEEDBACK_ERROR 1
static void macro_cancel(void) {
    memset(&s_macro,0,sizeof(s_macro));
    usb_service_release_source(MACRO_SOURCE); usb_service_release_source(MACRO_TRANSIENT_SOURCE);
}
#define CODEX_MICRO_MODE_NORMAL 0
static int task_mode;
static unsigned host_task_count, host_task_id;
static int codex_micro_mode(void) {return task_mode;}
static int host_action_events_queue(unsigned id, const char *token) {host_task_count++;host_task_id=id;return 0;}
static int prompt_store_queue_trigger(unsigned id) { return 0; }
static void show_feedback(int status,const char *text) {}
esp_err_t config_store_select_profile(uint8_t id) { return 0; }
esp_err_t config_store_apply_device_action(config_device_action_t a) { return 0; }
static const char *board_control_id(unsigned control) { return "test.control"; }
bool config_store_get_action(const char *id, config_action_t *out) { *out=configured; return true; }
''' + function(action, 'set_hid_usage') + '\n' + function(action, 'execute_action') + r'''
static void dispatch_action_for_control(const config_action_t *a,unsigned c,bool p,const char *label) {
    execute_action(a,p,c);
}
''' + function(action, 'dispatch_control') + '\n' + r'''
static void route_main_event(const board_event_t *event) {
''' + encoder + '\nstatic void schedule_tap_release(board_control_t control);\n' + '\n'.join(
            function(action, n) for n in ('route_tap','schedule_tap_release','poll_tap_releases'))
        code += '\n' + function(action, 'ascii_usage')
        macro_tail = action[action.index('    if (!s_macro.active || (int32_t)'):action.index('\nbool action_engine_config_activation_ready')]
        code += '\nstatic void poll_macro(void) {\n' + macro_tail
        code += r'''
void poll_once(void) { now+=10; poll_tap_releases(); poll_macro(); usb_service_poll(); }
void reset_test(int use_ble) {
    memset(s_active_action_valid,0,sizeof(s_active_action_valid));
    memset(s_tap_release_pending,0,sizeof(s_tap_release_pending));
    memset(s_tap_release_at,0,sizeof(s_tap_release_at));
    memset(&s_macro,0,sizeof(s_macro)); now=0;
    usb_service_release_all();
    calibration_interlock=false; s_calibration_interlock_seen=false;
    ready=true; ble=use_ble; atomic_store(&s_mounted,!use_ble);
    s_ble_hid=(usb_service_ble_hid_t){ble_connected,ble_send,NULL};
    s_hid_route=USB_SERVICE_ROUTE_NONE; s_route_transition=false;
    for(int i=0;i<20;i++) usb_service_poll();
    captured=0; task_mode=0; host_task_count=host_task_id=0;
    configured=(config_action_t){.type=CONFIG_ACTION_KEY,.usage=17,.modifiers={227},.modifier_count=1};
}
void start_macro(int chord) {
    memset(&s_macro,0,sizeof(s_macro)); s_macro.active=true;
    macro_steps[0]=(config_macro_step_t){.op=CONFIG_MACRO_TAP,.usage=17};
    macro_count=1;
    if(chord) {
        macro_steps[0]=(config_macro_step_t){.op=CONFIG_MACRO_PRESS,.usage=227};
        macro_steps[1]=(config_macro_step_t){.op=CONFIG_MACRO_PRESS,.usage=17};
        macro_steps[2]=(config_macro_step_t){.op=CONFIG_MACRO_RELEASE,.usage=17};
        macro_steps[3]=(config_macro_step_t){.op=CONFIG_MACRO_RELEASE,.usage=227};
        macro_count=4;
    }
}
void host_task_mapping(void) {configured=(config_action_t){.type=CONFIG_ACTION_HOST_ACTION,.object_id=255,.task_token="0123456789abcdef0123456789abcdef"};}
void task_mode_set(int mode) {task_mode=mode;}
unsigned task_count(void) {return host_task_count;}
unsigned task_id(void) {return host_task_id;}
void voice_modifier(unsigned usage) {configured=(config_action_t){.type=CONFIG_ACTION_KEY,.usage=usage};}
void physical(int pressed) { dispatch_control(0,pressed); }
void encoder_event(void) { board_event_t e={BOARD_CONTROL_ENCODER_CW,true}; route_main_event(&e); }
void synthetic(void) { route_tap(1); }
void set_ready(int r) { ready=r; }
void mouse_mapping(void) { configured=(config_action_t){.type=CONFIG_ACTION_MOUSE,.mouse_buttons=1}; }
void wheel_mapping(void) { configured=(config_action_t){.type=CONFIG_ACTION_MOUSE,.mouse_wheel=1}; }
void consumer_traffic(void) { usb_service_tap_consumer(0xE9); usb_service_tap_consumer(0xEA); }
void hold_other(void) { usb_service_set_key(2,4,true); usb_service_commit_keyboard(); }
void release_other(void) { usb_service_release_source(2); }
void disconnect(void) { ble=false; atomic_store(&s_mounted,false); }
void connect_ble(void) { ble=true; }
void cancel_all(void) { usb_service_release_all(); }
void set_calibration_interlock(int active) { calibration_interlock=active; }
unsigned count(void) { return captured; }
unsigned report_id(unsigned i) { return report_ids[i]; }
unsigned report_byte(unsigned i,unsigned j) { return reports[i][j]; }
unsigned report_sink(unsigned i) { return sinks[i]; }
'''
        (root / 'capture.c').write_text(code)
        subprocess.run(['cc','-std=c11','-shared','-fPIC','-I',str(root),
                        '-I',str(ROOT/'components/usb_device/include'),
                        '-I',str(ROOT/'components/config_store/include'),
                        str(root/'capture.c'),'-o',str(root/'capture.dylib')],check=True)
        cls.lib=ctypes.CDLL(str(root/'capture.dylib'))

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def poll(self, count=30):
        for _ in range(count): self.lib.poll_once()

    def reports(self, report_id=1):
        return [bytes(self.lib.report_byte(i,j) for j in range(8))
                for i in range(self.lib.count()) if self.lib.report_id(i)==report_id]

    # A chord leaves like a person types it: Cmd, Cmd+O, Cmd, nothing.
    CHORD = [bytes([8,0,0,0,0,0,0,0]), bytes([8,0,17,0,0,0,0,0]),
             bytes([8,0,0,0,0,0,0,0]), bytes(8)]

    def assert_chord(self, times=1):
        self.assertEqual(self.reports(), self.CHORD*times)

    def test_host_task_press_only_in_normal_mode_without_hid_output(self):
        self.lib.reset_test(0)
        self.lib.host_task_mapping()
        self.lib.physical(1)
        self.lib.physical(0)
        self.assertEqual(1, self.lib.task_count())
        self.assertEqual(255, self.lib.task_id())
        for mode in (1, 2, 3):
            self.lib.task_mode_set(mode)
            self.lib.physical(1)
            self.lib.physical(0)
        self.assertEqual(1, self.lib.task_count())
        self.poll()
        self.assertEqual(0, self.lib.count())

    def test_voice_modifier_pulse_reaches_usb_and_ble_then_releases(self):
        for ble in (0, 1):
            for usage in (228, 230, 231):
                with self.subTest(ble=ble, usage=usage):
                    self.lib.reset_test(ble)
                    self.lib.voice_modifier(usage)
                    self.lib.physical(1)
                    self.lib.physical(0)
                    self.poll()
                    self.assertEqual(self.reports(), [bytes([1 << (usage-224),0,0,0,0,0,0,0]), bytes(8)])
                    self.assertTrue(all(self.lib.report_sink(i)==ble+1 for i in range(self.lib.count())))

    def test_physical_hold_then_release(self):
        for ble in (0,1):
            self.lib.reset_test(ble); self.lib.physical(1); self.poll()
            self.lib.physical(0); self.poll(); self.assert_chord()
            self.assertTrue(all(self.lib.report_sink(i)==ble+1 for i in range(self.lib.count())))

    def test_encoder_chord_reports_press_before_release(self):
        for ble in (0,1):
            with self.subTest(ble=ble):
                self.lib.reset_test(ble); self.lib.encoder_event(); self.poll(); self.assert_chord()

    def test_encoder_burst_keeps_each_detent(self):
        self.lib.reset_test(0)
        for _ in range(4): self.lib.encoder_event()
        self.poll(); self.assert_chord(4)

    def test_synthetic_tap_survives_consumer_priority_and_busy_sink(self):
        for ble in (0,1):
            with self.subTest(ble=ble):
                self.lib.reset_test(ble); self.lib.set_ready(0)
                self.lib.consumer_traffic(); self.lib.synthetic(); self.poll(5)
                self.assertEqual(self.lib.count(),0)
                self.lib.set_ready(1); self.poll(); self.assert_chord()

    def test_encoder_mouse_button_click_has_both_edges(self):
        self.lib.reset_test(0); self.lib.mouse_mapping(); self.lib.encoder_event(); self.poll()
        self.assertEqual([r[0] for r in self.reports(3)],[1,0])

    def test_default_encoder_wheel_keeps_relative_movement(self):
        self.lib.reset_test(0); self.lib.wheel_mapping(); self.lib.encoder_event(); self.poll()
        self.assertEqual([r[3] for r in self.reports(3)],[1])

    def test_macro_tap_waits_for_actual_output_under_backpressure(self):
        for ble in (0,1):
            with self.subTest(ble=ble):
                self.lib.reset_test(ble); self.lib.set_ready(0); self.lib.start_macro(0)
                self.poll(20); self.assertEqual(self.lib.count(), 0)
                self.lib.set_ready(1); self.poll()
                self.assertEqual(self.reports(), [bytes([0,0,17,0,0,0,0,0]), bytes(8)])

    def test_macro_chord_preserves_each_output_step(self):
        self.lib.reset_test(0); self.lib.set_ready(0); self.lib.start_macro(1)
        self.poll(20); self.lib.set_ready(1); self.poll()
        self.assertEqual(self.reports(), [bytes([8,0,0,0,0,0,0,0]),
            bytes([8,0,17,0,0,0,0,0]), bytes([8,0,0,0,0,0,0,0]), bytes(8)])

    def test_chord_preserves_another_held_key(self):
        self.lib.reset_test(0); self.lib.hold_other(); self.poll()
        self.lib.encoder_event(); self.poll()
        self.assertEqual(self.reports(), [bytes([0,0,4,0,0,0,0,0]),
            bytes([8,0,4,0,0,0,0,0]), bytes([8,0,4,17,0,0,0,0]),
            bytes([8,0,4,0,0,0,0,0]), bytes([0,0,4,0,0,0,0,0])])
        self.lib.release_other(); self.poll()
        self.assertEqual(self.reports()[-1], bytes(8))

    def test_synthetic_tap_is_held_like_a_person(self):
        # 10 ms per poll in this harness; the release waits KEY_PULSE_HOLD_MS.
        self.lib.reset_test(0); self.lib.synthetic()
        self.poll(5)
        self.assertEqual(self.reports(), self.CHORD[:2])
        self.poll(2)
        self.assert_chord()

    def test_route_change_discards_unsent_tap(self):
        self.lib.reset_test(0); self.lib.set_ready(0); self.lib.encoder_event()
        self.lib.disconnect(); self.poll()
        self.lib.connect_ble(); self.lib.set_ready(1); self.poll()
        self.assertTrue(all(not any(r) for r in self.reports()))

    def test_route_change_discards_unsent_mouse_click(self):
        self.lib.reset_test(0); self.lib.set_ready(0); self.lib.mouse_mapping()
        self.lib.encoder_event(); self.lib.disconnect(); self.poll()
        self.lib.connect_ble(); self.lib.set_ready(1); self.poll()
        self.assertTrue(all(not any(r) for r in self.reports(3)))

    def test_queue_backpressure_eventually_releases(self):
        self.lib.reset_test(0); self.lib.set_ready(0)
        for _ in range(50): self.lib.encoder_event()
        self.lib.set_ready(1); self.poll(150)
        self.assertEqual(self.reports()[-1], bytes(8))

    def test_cancel_discards_unsent_input(self):
        self.lib.reset_test(0); self.lib.set_ready(0); self.lib.encoder_event()
        self.lib.cancel_all(); self.lib.set_ready(1); self.poll()
        self.assertTrue(all(not any(r) for r in self.reports()))

    def test_calibration_interlock_releases_queued_host_input_without_replay(self):
        for ble in (0, 1):
            for kind in ("keyboard", "consumer", "mouse"):
                with self.subTest(ble=ble, kind=kind):
                    self.lib.reset_test(ble)
                    if kind == "keyboard":
                        self.lib.physical(1)
                    elif kind == "consumer":
                        self.lib.consumer_traffic()
                    else:
                        self.lib.mouse_mapping()
                        self.lib.physical(1)
                    self.lib.set_calibration_interlock(1)
                    self.poll()
                    self.assertTrue(
                        all(not any(self.lib.report_byte(i, j) for j in range(8))
                            for i in range(self.lib.count()))
                    )
                    self.lib.set_calibration_interlock(0)
                    self.poll()
                    self.assertTrue(
                        all(not any(self.lib.report_byte(i, j) for j in range(8))
                            for i in range(self.lib.count()))
                    )


if __name__=='__main__': unittest.main()
