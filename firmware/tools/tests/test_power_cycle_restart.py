"""OFF/ON restarts the MCU even when USB power keeps it alive, and the BLE
advertising state machine is visible in GET_STATUS.

On Power V2 the OFF pulse does not remove power while USB is attached; the
firmware stayed in quiet standby and ON only woke it. A returned unit kept an
exhausted heap through repeated OFF/ON, and 586EC did not reset on two OFF/ON
cycles. After a slot clear 68DE8 stayed invisible to macOS until a slot was
selected again; the advertising counters tell whether it advertised at all.
"""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / "components/action_engine/action_engine.c"
BLE = ROOT / "components/codex_micro/codex_micro_ble.c"
HEADER = ROOT / "components/codex_micro/include/codex_micro.h"
PROTOCOL = ROOT / "components/config_protocol/config_protocol.c"


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


class PowerCycleRestartTests(unittest.TestCase):
    def setUp(self):
        self.source = ACTION.read_text()

    def test_only_a_power_button_off_arms_the_restart(self):
        enter = function(self.source, "static void enter_usb_standby(usb_standby_entry_reason_t reason)\n{")
        self.assertIn("s_restart_on_standby_wake = reason == USB_STANDBY_ENTRY_POWER_BUTTON;",
                      enter)

    def test_wake_saves_on_intent_then_restarts_once(self):
        wake = function(self.source, "static void wake_from_usb_standby(usb_standby_wake_reason_t reason)\n{")
        armed = wake.index("const bool restart_for_power_cycle")
        self.assertIn("s_restart_on_standby_wake &&", wake[armed:armed + 200])
        self.assertIn("!config_protocol_restart_busy()", wake[armed:armed + 200])
        save = wake.index("config_store_set_user_powered_on(true)")
        restart = wake.index("action_engine_request_restart();")
        self.assertLess(save, restart)
        branch = wake[wake.index("if ((restart_for_name || restart_for_migration || restart_for_power_cycle)"):restart]
        self.assertIn("intent_saved_or_read_only", branch)
        self.assertIn("power_result == ESP_OK || store_status.storage_degraded", wake)
        self.assertIn("s_restart_on_standby_wake = false;", branch)


class AdvertisingDiagnosticsTests(unittest.TestCase):
    def test_counters_follow_the_gap_events(self):
        ble = BLE.read_text()
        start = ble[ble.index("case ESP_GAP_BLE_ADV_START_COMPLETE_EVT:"):]
        start = start[:start.index("break;")]
        self.assertIn("atomic_fetch_add(&s_advertising_starts, 1u);", start)
        self.assertIn("atomic_fetch_add(&s_advertising_start_failures, 1u);", start)
        stop = ble[ble.index("case ESP_GAP_BLE_ADV_STOP_COMPLETE_EVT:"):]
        self.assertIn("atomic_fetch_add(&s_advertising_stops, 1u);", stop[:200])
        retry = function(ble, "static void advertising_retry_task(void *parameter)")
        self.assertIn("atomic_fetch_add(&s_advertising_retries_exhausted, 1u);", retry)

    def test_status_reports_the_state_machine(self):
        for field in ("advertising_starts", "advertising_start_failures",
                      "advertising_last_start_status", "advertising_stops",
                      "advertising_retries_exhausted", "advertising_active",
                      "peer_filter_ready", "peer_filter_state"):
            self.assertIn(field, HEADER.read_text())
        protocol = PROTOCOL.read_text()
        block = protocol[protocol.index('cJSON_AddObjectToObject(ble, "advertising")'):]
        block = block[:block.index("\n        }\n")]
        for key in ('"starts"', '"start_failures"', '"last_start_status"', '"stops"',
                    '"retries_exhausted"', '"active"', '"starting"', '"data_configured"',
                    '"scan_response_configured"', '"peer_filter_ready"',
                    '"peer_filter_state"', '"slow"'):
            self.assertIn(key, block)


if __name__ == "__main__":
    unittest.main()
