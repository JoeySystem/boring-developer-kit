import unittest
from pathlib import Path


FIRMWARE_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_SOURCE = (
    FIRMWARE_ROOT / "components/config_protocol/config_protocol.c"
)
PROTOCOL_HEADER = (
    FIRMWARE_ROOT / "components/config_protocol/include/config_protocol.h"
)
ENGINE_SOURCE = FIRMWARE_ROOT / "components/action_engine/action_engine.c"
MAIN_SOURCE = FIRMWARE_ROOT / "main/app_main.c"


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


class LightingPreviewProtocolTests(unittest.TestCase):
    def test_protocol_validates_complete_snapshot_before_mailbox_publish(self):
        source = PROTOCOL_SOURCE.read_text()
        parser = c_function(source, "static bool parse_lighting_preview_request")
        handler = source[
            source.index("case WMP_MSG_SET_LIGHTING_PREVIEW"):
            source.index("case WMP_MSG_CLEAR_LIGHTING_PREVIEW")
        ]

        self.assertIn("cJSON_GetArraySize(request) != 3", parser)
        self.assertIn("cJSON_IsBool(enabled)", parser)
        self.assertIn("cJSON_GetArraySize(under_key) != (int)expected_count", parser)
        self.assertIn("read_bounded_uint8(brightness, 100", parser)
        self.assertIn("read_lighting_preview_rgb", parser)
        self.assertLess(
            handler.index("parse_lighting_preview_request"),
            handler.index("publish_lighting_preview_request"),
        )
        self.assertIn("WMP_ERROR_VALIDATION_FAILED", handler)
        self.assertIn("WMP_ERROR_BUSY", handler)
        self.assertIn('\\"state\\":\\"ACTIVE\\"', handler)
        self.assertNotIn("board_apply_rgb", handler)

    def test_clear_is_empty_idempotent_mailbox_request(self):
        source = PROTOCOL_SOURCE.read_text()
        handler = source[
            source.index("case WMP_MSG_CLEAR_LIGHTING_PREVIEW"):
            source.index("case WMP_MSG_FACTORY_DEFAULT")
        ]
        reset = c_function(source, "void config_protocol_reset")

        self.assertIn("cJSON_GetArraySize(request) != 0", handler)
        self.assertIn("CONFIG_PROTOCOL_LIGHTING_PREVIEW_CLEAR", handler)
        self.assertIn("publish_lighting_preview_request(&clear)", handler)
        self.assertIn('\\"state\\":\\"CLEARED\\"', handler)
        self.assertNotIn("s_lighting_preview_busy", handler)
        self.assertIn("publish_lighting_preview_request(&clear_preview)", reset)

    def test_cdc_to_main_loop_boundary_is_a_single_latest_value_mailbox(self):
        source = PROTOCOL_SOURCE.read_text()
        header = PROTOCOL_HEADER.read_text()
        publish = c_function(source, "static void publish_lighting_preview_request")
        take = c_function(source, "bool config_protocol_take_lighting_preview_request")

        self.assertIn("config_protocol_lighting_preview_request_t", header)
        self.assertIn("s_lighting_preview_mailbox = *request", publish)
        self.assertIn("s_lighting_preview_mailbox_pending = true", publish)
        self.assertIn("portENTER_CRITICAL", publish)
        self.assertIn("*request = s_lighting_preview_mailbox", take)
        self.assertIn("s_lighting_preview_mailbox_pending = false", take)

    def test_action_engine_owns_output_and_restores_all_required_boundaries(self):
        source = ENGINE_SOURCE.read_text()
        apply_preview = c_function(source, "static void apply_host_lighting_preview")
        poll_preview = c_function(source, "static void poll_host_lighting_preview")
        enter_local = c_function(source, "static void enter_quick_config")
        activate = c_function(source, "void action_engine_config_activated")
        poll = c_function(source, "void action_engine_poll")

        self.assertIn("wake_from_idle_standby();", apply_preview)
        self.assertIn("HOST_LIGHTING_PREVIEW_LEASE_MS", apply_preview)
        self.assertIn("idle_standby_model_note_activity", apply_preview)
        self.assertIn("!host_config_link_active()", poll_preview)
        self.assertIn("codex_micro_mode() != s_codex_mode", poll_preview)
        self.assertIn("lighting_host_preview_expire", poll_preview)
        self.assertIn("clear_host_lighting_preview();", enter_local)
        self.assertIn("lighting_host_preview_clear", activate)
        self.assertLess(
            poll.index("poll_host_lighting_preview();"),
            poll.index("poll_idle_standby();"),
        )

    def test_local_and_power_states_publish_busy_and_agent_overlay_stays_last(self):
        source = ENGINE_SOURCE.read_text()
        busy = c_function(source, "static bool host_lighting_preview_output_busy")
        render = c_function(source, "static void render_under_key_output")
        diagnostics = c_function(source, "void action_engine_get_diagnostics")
        main = MAIN_SOURCE.read_text()

        for state in (
            "s_shutdown_active",
            "s_usb_standby_active",
            "s_battery_splash_active",
            "s_under_key_self_test_active",
            "QUICK_CONFIG_ITEM_LIGHTING",
        ):
            self.assertIn(state, busy)
        self.assertIn("lighting_preview_busy = host_lighting_preview_output_busy()", diagnostics)
        self.assertIn("action_diagnostics.lighting_preview_busy", main)
        restart = c_function(source, "void action_engine_request_restart")
        shutdown = c_function(source, "void action_engine_request_shutdown")
        self.assertIn("begin_shutdown(&view, 250, true, USB_STANDBY_ENTRY_POWER_BUTTON);", shutdown)
        begin = c_function(source, "static void begin_shutdown(const board_mist_view_t *view")
        usb_standby = c_function(source, "static void enter_usb_standby")
        for transition in (restart, begin, usb_standby):
            self.assertIn(
                "config_protocol_set_lighting_preview_busy(true)", transition
            )
        self.assertLess(
            render.index("lighting_host_preview_effective_rgb"),
            render.index("if (!s_codex_key_status_active)"),
        )


if __name__ == "__main__":
    unittest.main()
