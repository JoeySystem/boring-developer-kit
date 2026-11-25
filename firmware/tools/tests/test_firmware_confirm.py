"""F-01: a new OTA image stays pending until a host proves the channel works."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

FIRMWARE = Path(__file__).resolve().parents[2]
COMPONENT = FIRMWARE / 'components/config_protocol'
ALLOWED, NOT_AUTHENTICATED, BUILD_MISMATCH, PARTITION_MISMATCH = range(4)
BUILD = b'20261028.01-gabcdef01'


class Session(ctypes.Structure):
    _fields_ = [('hello', ctypes.c_bool), ('authenticated', ctypes.c_bool)]


class FirmwareConfirmModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'confirm.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(COMPONENT / 'include'),
                        str(COMPONENT / 'firmware_confirm_model.c'), '-o', str(lib)],
                       check=True)
        cls.lib = ctypes.CDLL(str(lib))
        L = cls.lib
        L.firmware_confirm_session_trusted.restype = ctypes.c_bool
        L.firmware_confirm_session_trusted.argtypes = [ctypes.POINTER(Session), ctypes.c_bool]
        L.firmware_confirm_auto_due.restype = ctypes.c_bool
        L.firmware_confirm_auto_due.argtypes = [ctypes.POINTER(Session), ctypes.c_bool,
                                                ctypes.c_bool]
        L.firmware_confirm_explicit_check.argtypes = [
            ctypes.POINTER(Session), ctypes.c_bool, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_char_p]

    def setUp(self):
        self.s = Session()
        self.lib.firmware_confirm_session_reset(ctypes.byref(self.s))

    def hello(self):
        self.lib.firmware_confirm_session_hello(ctypes.byref(self.s))

    def auth(self):
        self.lib.firmware_confirm_session_authenticated(ctypes.byref(self.s))

    def auto(self, usb=True, pending=True):
        return self.lib.firmware_confirm_auto_due(ctypes.byref(self.s), usb, pending)

    def explicit(self, build=BUILD, partition=b'ota_1', identity=True):
        return self.lib.firmware_confirm_explicit_check(
            ctypes.byref(self.s), identity, build, BUILD, partition, b'ota_1')

    def test_new_image_is_not_confirmed_without_a_session(self):
        self.assertFalse(self.auto())
        self.assertEqual(NOT_AUTHENTICATED, self.explicit())

    def test_auto_confirm_needs_hello_over_usb_but_not_authentication(self):
        # The Windows updater sends HELLO, PING, GET_CONFIG, GET_STATUS only.
        self.hello()
        self.assertTrue(self.auto())
        self.assertFalse(self.auto(usb=False))          # BLE never auto-confirms
        self.assertFalse(self.auto(pending=False))      # nothing to do

    def test_explicit_confirm_still_needs_authentication_when_provisioned(self):
        self.hello()
        self.assertEqual(NOT_AUTHENTICATED, self.explicit())
        self.auth()
        self.assertEqual(ALLOWED, self.explicit())

    def test_unprovisioned_unit_needs_hello_only_for_explicit_confirm(self):
        self.assertEqual(NOT_AUTHENTICATED, self.explicit(identity=False))
        self.hello()
        self.assertEqual(ALLOWED, self.explicit(identity=False))

    def test_auth_before_hello_and_a_new_hello_do_not_carry_trust(self):
        self.auth()
        self.hello()
        self.assertEqual(NOT_AUTHENTICATED, self.explicit())
        self.auth()
        self.hello()                                     # new logical connection
        self.assertEqual(NOT_AUTHENTICATED, self.explicit())

    def test_reset_clears_trust(self):
        self.hello()
        self.auth()
        self.lib.firmware_confirm_session_reset(ctypes.byref(self.s))
        self.assertFalse(self.auto())
        self.assertEqual(NOT_AUTHENTICATED, self.explicit())

    def test_explicit_requires_matching_build_and_partition(self):
        self.hello()
        self.auth()
        self.assertEqual(BUILD_MISMATCH, self.explicit(build=b'20261028.01-g00000000'))
        self.assertEqual(BUILD_MISMATCH, self.explicit(build=b''))
        self.assertEqual(PARTITION_MISMATCH, self.explicit(partition=b'ota_0'))
        self.assertEqual(PARTITION_MISMATCH, self.explicit(partition=b''))


def c_function(source, signature):
    start = source.index(signature)
    depth = 0
    for index in range(source.index('{', start), len(source)):
        depth += {'{': 1, '}': -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(signature)


class FirmwareConfirmWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = (COMPONENT / 'config_protocol.c').read_text()
        cls.update = (FIRMWARE / 'components/firmware_update/firmware_update.c').read_text()
        cls.app = (FIRMWARE / 'main/app_main.c').read_text()

    def test_startup_no_longer_confirms_a_pending_image(self):
        self.assertIn('ESP_ERROR_CHECK(firmware_update_boot_ready());', self.app)
        self.assertNotIn('firmware_update_confirm_running(', self.app)
        boot = c_function(self.update, 'esp_err_t firmware_update_boot_ready(void)\n{')
        self.assertNotIn('esp_ota_mark_app_valid_cancel_rollback', boot)
        self.assertIn('ESP_OTA_IMG_PENDING_VERIFY', boot)
        self.assertIn('FIRMWARE_CONFIRM_SOURCE_PENDING', boot)

    def test_confirm_is_idempotent_and_records_its_source(self):
        confirm = c_function(
            self.update,
            'esp_err_t firmware_update_confirm_running(firmware_confirm_source_t source)\n{')
        pending_check = confirm.index('if (!atomic_load(&s_confirm_pending)) {')
        self.assertLess(pending_check, confirm.index('esp_ota_mark_app_valid_cancel_rollback()'))
        self.assertIn('current_confirm_error = error;', confirm)
        self.assertIn('atomic_store(&s_confirm_source, source);', confirm)

    def test_get_status_confirms_before_building_its_response(self):
        status = self.protocol[self.protocol.index('    case WMP_MSG_GET_STATUS: {'):]
        status = status[:status.index('    case WMP_MSG_SET_PLATFORM:')]
        # The same response must already report rollback_pending false.
        self.assertLess(status.index('confirm_firmware_for_status();'),
                        status.index('firmware_update_protocol_add_status(result);'))
        auto = c_function(self.protocol, 'static void confirm_firmware_for_status(void)\n{')
        self.assertNotIn('device_identity_available', auto)
        self.assertIn('CONFIG_PROTOCOL_TRANSPORT_USB', auto)
        self.assertIn('firmware_update_confirm_pending()', auto)
        # Stability item 4: GET_STATUS approves; confirmation waits for health.
        self.assertIn('approve_firmware(false, &confirmed_now)', auto)
        approve = c_function(self.protocol,
                             'static esp_err_t approve_firmware(bool by_host, bool *confirmed_now)')
        self.assertIn('FIRMWARE_CONFIRM_SOURCE_SESSION', approve)
        self.assertLess(approve.index('firmware_health_gate_due('),
                        approve.index('firmware_update_confirm_running('))

    def test_session_trust_follows_hello_auth_and_reset(self):
        hello = self.protocol[self.protocol.index('case WMP_MSG_HELLO: {'):
                              self.protocol.index('case WMP_MSG_CAPABILITIES:')]
        self.assertIn('firmware_confirm_session_hello(&s_confirm_session);', hello)
        auth = c_function(self.protocol,
                          'static void send_auth_challenge(uint32_t request_id, const char *nonce)')
        self.assertLess(auth.index('if (!built) {'),
                        auth.index('firmware_confirm_session_authenticated(&s_confirm_session);'))
        reset = c_function(self.protocol, 'void config_protocol_reset(void)')
        self.assertIn('firmware_confirm_session_reset(&s_confirm_session);', reset)

    def test_fw_confirm_command_and_capability(self):
        self.assertIn('case WMP_MSG_FW_CONFIRM:', self.protocol)
        handler = c_function(self.protocol,
                             'static void handle_firmware_confirm(uint32_t request_id, const cJSON *request)')
        for text in ('"build_id"', '"running_partition"', 'WMP_BUILD_ID',
                     'WMP_ERROR_AUTHENTICATION_REQUIRED', 'WMP_ERROR_VALIDATION_FAILED',
                     'WMP_ERROR_STORAGE_FAILURE', 'approve_firmware(true, &confirmed_now)',
                     '\\"was_pending\\"', '\\"rollback_pending\\"',
                     '\\"health_ready\\"', '\\"confirm_in_ms\\"'):
            self.assertIn(text, handler)
        approve = c_function(self.protocol,
                             'static esp_err_t approve_firmware(bool by_host, bool *confirmed_now)')
        self.assertIn('FIRMWARE_CONFIRM_SOURCE_HOST', approve)
        self.assertIn('"firmware_host_confirm"', self.protocol)
        self.assertIn('"firmware_confirm_model.c"', (COMPONENT / 'CMakeLists.txt').read_text())
        fw = (COMPONENT / 'firmware_update_protocol.c').read_text()
        self.assertIn('"confirm_source"', fw)


if __name__ == '__main__':
    unittest.main()
