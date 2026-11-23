"""WMP request-frame scanner: resync, partial-frame timeout (F-02)."""
import ctypes
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest
import zlib

FIRMWARE = Path(__file__).resolve().parents[2]
COMPONENT = FIRMWARE / 'components/config_protocol'
GENERATED = FIRMWARE.parent / 'protocol/generated'
SCANNER_HEADER = (COMPONENT / 'include/wmp_frame_scanner.h').read_text()

NEED_MORE, SKIP, REJECT, FRAME = range(4)
CONTRACT = (GENERATED / 'protocol_contract.h').read_text()


def error_code(name):
    return int(re.search(rf'WMP_ERROR_{name} = (\d+),', CONTRACT).group(1))


ERROR_INVALID_FRAME = error_code('INVALID_FRAME')
ERROR_CRC_MISMATCH = error_code('CRC_MISMATCH')
ERROR_PAYLOAD_TOO_LARGE = error_code('PAYLOAD_TOO_LARGE')
ERROR_UNSUPPORTED_PROTOCOL = error_code('UNSUPPORTED_PROTOCOL')
HEADER = struct.Struct('<4sBBBBIII')
USB_TIMEOUT_MS = 500
JSON_MAX_NESTING = 32
BLE_TIMEOUT_MS = 2000


def frame(request_id, payload=None, minor=0, flags=0, major=1, message_type=0x01,
          length=None, crc=None):
    body = json.dumps(payload if payload is not None else {'id': request_id}).encode()
    return HEADER.pack(b'WMP1', major, minor, message_type, flags, request_id,
                       len(body) if length is None else length,
                       zlib.crc32(body) if crc is None else crc) + body


class ScanResult(ctypes.Structure):
    _fields_ = [('kind', ctypes.c_int), ('consume', ctypes.c_size_t),
                ('resync', ctypes.c_bool), ('error', ctypes.c_uint8),
                ('message_type', ctypes.c_uint8), ('request_id', ctypes.c_uint32),
                ('payload_length', ctypes.c_uint32)]


class Parser:
    """Mirror of parse_available() and expire_partial_frame() over the C scanner."""

    def __init__(self, lib, timeout_ms=USB_TIMEOUT_MS):
        self.lib, self.timeout_ms = lib, timeout_ms
        self.buffer, self.last_rx_ms = bytearray(), 0
        self.frames, self.nacks = [], []
        self.stats = {'bad_frames': 0, 'partial_timeouts': 0, 'resyncs': 0}

    def scan(self):
        data = bytes(self.buffer)
        return self.lib.wmp_frame_scan(data, len(data))

    def parse(self):
        while True:
            result = self.scan()
            if result.kind == NEED_MORE:
                return
            if result.kind == FRAME:
                self.frames.append(result.request_id)
            else:
                if result.kind == REJECT:
                    self.stats['bad_frames'] += 1
                    if result.request_id:
                        self.nacks.append((result.request_id, result.error))
                if result.resync:
                    self.stats['resyncs'] += 1
            del self.buffer[:result.consume]

    def feed(self, data, now_ms):
        self.buffer += data
        self.last_rx_ms = now_ms
        self.parse()

    def poll(self, now_ms):
        if self.lib.wmp_frame_partial_expired(len(self.buffer), now_ms,
                                              self.last_rx_ms, self.timeout_ms):
            self.stats['partial_timeouts'] += 1
            del self.buffer[:1]
            self.parse()


class WmpFrameScannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        lib = Path(cls.tmp.name) / 'scanner.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(COMPONENT / 'include'), '-I', str(GENERATED),
                        str(COMPONENT / 'wmp_frame_scanner.c'), '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.wmp_frame_scan.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.wmp_frame_scan.restype = ScanResult
        cls.lib.wmp_frame_partial_expired.argtypes = [ctypes.c_size_t, ctypes.c_uint32,
                                                      ctypes.c_uint32, ctypes.c_uint32]
        cls.lib.wmp_frame_partial_expired.restype = ctypes.c_bool
        cls.lib.wmp_crc32_ieee.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.wmp_crc32_ieee.restype = ctypes.c_uint32
        cls.lib.wmp_json_nesting_within.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                                    ctypes.c_uint32]
        cls.lib.wmp_json_nesting_within.restype = ctypes.c_bool

    def nesting_ok(self, payload, limit=JSON_MAX_NESTING):
        return self.lib.wmp_json_nesting_within(payload, len(payload), limit)

    def test_json_nesting_is_bounded_before_cjson_parse(self):
        """cJSON recurses ~64 bytes of stack per level on an 8 KB task; its own limit is 1000."""
        self.assertIn(f'WMP_JSON_MAX_NESTING {JSON_MAX_NESTING}U', SCANNER_HEADER)
        self.assertTrue(self.nesting_ok(b'{"a":' + b'[' * 31 + b'0' + b']' * 31 + b'}'))
        self.assertFalse(self.nesting_ok(b'{"a":' + b'[' * 32))
        self.assertFalse(self.nesting_ok(b'[' * 125))
        self.assertFalse(self.nesting_ok(b'{"a":' * 33))
        # Depth, not count: siblings and brackets inside strings are free.
        self.assertTrue(self.nesting_ok(b'{"a":[' + b'[],' * 500 + b'[]]}'))
        self.assertTrue(self.nesting_ok(b'{"a":"' + b'[' * 200 + b'"}'))
        self.assertTrue(self.nesting_ok(b'{"a":"\\"' + b'[' * 200 + b'"}'))
        self.assertFalse(self.nesting_ok(b'{"a":"\\\\"' + b'[' * 200))
        self.assertTrue(self.nesting_ok(b''))

    def test_request_parser_checks_nesting_before_cjson(self):
        source = (COMPONENT / 'config_protocol.c').read_text()
        body = source[source.index('static cJSON *parse_json_object('):]
        self.assertLess(body.index('wmp_json_nesting_within('),
                        body.index('cJSON_ParseWithLengthOpts('))
        cmake = (FIRMWARE / 'CMakeLists.txt').read_text()
        self.assertIn('CJSON_NESTING_LIMIT=64', cmake)

    def test_main_task_stack_matches_the_transport_tasks(self):
        """config_protocol_poll dispatches a buffered request on the main task."""
        defaults = (FIRMWARE / 'sdkconfig.defaults').read_text()
        self.assertIn('CONFIG_ESP_MAIN_TASK_STACK_SIZE=8192', defaults)

    def parser(self, timeout_ms=USB_TIMEOUT_MS):
        return Parser(self.lib, timeout_ms)

    def test_timeouts_match_the_plan(self):
        self.assertIn(f'WMP_PARTIAL_FRAME_TIMEOUT_USB_MS {USB_TIMEOUT_MS}U', SCANNER_HEADER)
        self.assertIn(f'WMP_PARTIAL_FRAME_TIMEOUT_BLE_MS {BLE_TIMEOUT_MS}U', SCANNER_HEADER)

    def test_crc_matches_zlib(self):
        for data in (b'', b'{}', bytes(range(256))):
            self.assertEqual(zlib.crc32(data), self.lib.wmp_crc32_ieee(data, len(data)))

    def test_good_crc_error_good_answers_both_good_requests(self):
        parser = self.parser()
        bad = frame(2, crc=0xDEADBEEF)
        parser.feed(frame(1) + bad + frame(3), 0)
        self.assertEqual([1, 3], parser.frames)
        self.assertEqual([(2, ERROR_CRC_MISMATCH)], parser.nacks)
        self.assertEqual(b'', parser.buffer)

    def test_crc_error_steps_one_byte_instead_of_trusting_the_length(self):
        result = self.lib.wmp_frame_scan(frame(2, crc=1) + frame(3), 200)
        self.assertEqual((REJECT, 1, True), (result.kind, result.consume, result.resync))

    def test_enlarged_length_is_recovered_within_the_partial_timeout(self):
        good = frame(5)
        corrupt = frame(4, length=len(json.dumps({'id': 4})) + 500)
        parser = self.parser()
        parser.feed(corrupt + good, 0)
        self.assertEqual([], parser.frames)          # waiting on the bogus length
        parser.poll(USB_TIMEOUT_MS - 1)
        self.assertEqual([], parser.frames)
        parser.poll(USB_TIMEOUT_MS)
        self.assertEqual([5], parser.frames)
        self.assertEqual(1, parser.stats['partial_timeouts'])

    def test_half_frame_expires_then_next_request_is_processed(self):
        parser = self.parser(BLE_TIMEOUT_MS)
        parser.feed(frame(6)[:12], 0)
        now = BLE_TIMEOUT_MS
        while parser.buffer:
            parser.poll(now)
            now += 1
            self.assertLess(now, BLE_TIMEOUT_MS + 50)
        parser.feed(frame(7), now)
        self.assertEqual([7], parser.frames)

    def test_steady_byte_flow_never_expires(self):
        parser = self.parser()
        data = frame(8, {'pad': 'x' * 400})
        for index, byte in enumerate(data[:-1]):
            parser.feed(bytes([byte]), index * 10)
            parser.poll(index * 10 + 9)
        self.assertEqual(0, parser.stats['partial_timeouts'])
        parser.feed(data[-1:], len(data) * 10)
        self.assertEqual([8], parser.frames)

    def test_expiry_is_wrap_safe_and_needs_buffered_bytes(self):
        expired = self.lib.wmp_frame_partial_expired
        self.assertFalse(expired(0, 10000, 0, 500))
        self.assertTrue(expired(1, 0x100, 0xFFFFFF00, 500))
        self.assertFalse(expired(1, 0x10, 0xFFFFFF00, 500))

    def test_header_errors_with_good_crc_drop_the_whole_frame(self):
        for bad, error in ((frame(9, flags=0x04), ERROR_INVALID_FRAME),
                           (frame(9, flags=0x01), ERROR_INVALID_FRAME),
                           (frame(0), ERROR_INVALID_FRAME),
                           (frame(9, major=2), ERROR_UNSUPPORTED_PROTOCOL)):
            result = self.lib.wmp_frame_scan(bad, len(bad))
            self.assertEqual((REJECT, len(bad), False, error),
                             (result.kind, result.consume, result.resync, result.error))
        parser = self.parser()
        parser.feed(frame(0) + frame(10), 0)
        self.assertEqual([10], parser.frames)
        self.assertEqual([], parser.nacks)            # request ID 0 is never answered

    def test_header_errors_with_bad_crc_resync(self):
        bad = frame(11, flags=0x04, crc=1)
        result = self.lib.wmp_frame_scan(bad, len(bad))
        self.assertEqual((REJECT, 1, True), (result.kind, result.consume, result.resync))

    def test_oversized_payload_skips_the_magic(self):
        bad = HEADER.pack(b'WMP1', 1, 0, 1, 0, 12, 20 * 1024 + 1, 0)
        result = self.lib.wmp_frame_scan(bad, len(bad))
        self.assertEqual((REJECT, 4, True, ERROR_PAYLOAD_TOO_LARGE),
                         (result.kind, result.consume, result.resync, result.error))

    def test_noise_split_and_coalesced_frames(self):
        parser = self.parser()
        stream = b'xxWM' + frame(13) + b'\x00' * 7 + frame(14) + frame(15)
        for start in range(0, len(stream), 5):
            parser.feed(stream[start:start + 5], start)
        self.assertEqual([13, 14, 15], parser.frames)
        self.assertGreaterEqual(parser.stats['resyncs'], 2)

    def test_shipped_contract_rejects_a_newer_minor(self):
        self.assertIn('#define WMP_PROTOCOL_MINOR 0u', CONTRACT)
        data = frame(22, minor=1)
        result = self.lib.wmp_frame_scan(data, len(data))
        self.assertEqual((REJECT, ERROR_UNSUPPORTED_PROTOCOL), (result.kind, result.error))

    def test_partial_magic_suffix_is_kept_for_the_next_chunk(self):
        data = b'z' * 20 + b'WMP'
        result = self.lib.wmp_frame_scan(data, len(data))
        self.assertEqual((SKIP, 20), (result.kind, result.consume))


class MinorCompatibilityTests(unittest.TestCase):
    """F-03: same major, any request minor up to the device's own."""

    DEVICE_MINOR = 3

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        shim = Path(cls.tmp.name) / 'include'
        shim.mkdir()
        # Build the real scanner against a contract whose device minor is 3.
        contract = re.sub(r'#define WMP_PROTOCOL_MINOR \d+u',
                          f'#define WMP_PROTOCOL_MINOR {cls.DEVICE_MINOR}u', CONTRACT)
        (shim / 'protocol_contract.h').write_text(contract)
        lib = Path(cls.tmp.name) / 'scanner_minor.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                        '-I', str(shim), '-I', str(COMPONENT / 'include'),
                        str(COMPONENT / 'wmp_frame_scanner.c'), '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.wmp_frame_scan.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
        cls.lib.wmp_frame_scan.restype = ScanResult

    def scan(self, data):
        return self.lib.wmp_frame_scan(data, len(data))

    def test_lower_and_equal_minor_are_processed(self):
        for minor in range(self.DEVICE_MINOR + 1):
            self.assertEqual(FRAME, self.scan(frame(20, minor=minor)).kind, minor)

    def test_higher_minor_or_other_major_is_unsupported(self):
        for data in (frame(21, minor=self.DEVICE_MINOR + 1), frame(21, major=2),
                     frame(21, major=0)):
            result = self.scan(data)
            self.assertEqual((REJECT, ERROR_UNSUPPORTED_PROTOCOL, len(data)),
                             (result.kind, result.error, result.consume))


def c_function(source, signature):
    start = source.index(signature)
    depth = 0
    for index in range(source.index('{', start), len(source)):
        depth += {'{': 1, '}': -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(signature)


class ParserWiringTests(unittest.TestCase):
    """config_protocol.c drives every received byte through the scanner."""

    @classmethod
    def setUpClass(cls):
        cls.source = (COMPONENT / 'config_protocol.c').read_text()

    def test_parser_uses_the_scanner_and_counts(self):
        parser = c_function(self.source, 'static void parse_available(void)\n{')
        self.assertIn('wmp_frame_scan(s_rx, s_rx_length)', parser)
        self.assertIn('++s_rx_stats.bad_frames;', parser)
        self.assertIn('++s_rx_stats.resyncs;', parser)
        self.assertIn('consume(scan.consume);', parser)
        self.assertNotIn('crc32', parser)
        self.assertIn('wmp_frame_scanner.c', (COMPONENT / 'CMakeLists.txt').read_text())

    def test_poll_expires_partial_frames_under_the_transport_mutex(self):
        poll = c_function(self.source, 'void config_protocol_poll(void)\n{')
        self.assertLess(poll.index('xSemaphoreTake(s_transport_mutex, 0)'),
                        poll.index('expire_partial_frame('))
        expire = c_function(self.source, 'static void expire_partial_frame(uint32_t now_ms)\n{')
        self.assertIn('WMP_PARTIAL_FRAME_TIMEOUT_BLE_MS', expire)
        self.assertIn('WMP_PARTIAL_FRAME_TIMEOUT_USB_MS', expire)
        self.assertIn('++s_rx_stats.partial_timeouts;', expire)

    def test_full_buffer_resyncs_instead_of_resetting_the_session(self):
        feed = c_function(self.source, 'void config_protocol_feed_transport(')
        full = feed[feed.index('if (available == 0) {'):feed.index('const size_t copy_length')]
        self.assertIn('++s_rx_stats.overflows;', full)
        self.assertIn('consume(1);', full)
        self.assertNotIn('config_protocol_reset()', full)
        self.assertIn('s_rx_last_ms = protocol_uptime_ms();', feed)

    def test_hello_accepts_an_older_client_minor_and_reports_its_own(self):
        hello = self.source[self.source.index('case WMP_MSG_HELLO: {'):
                            self.source.index('case WMP_MSG_CAPABILITIES:')]
        gate = hello[hello.index('if (min_major->valueint > WMP_PROTOCOL_MAJOR'):]
        gate = gate[:gate.index('{')]
        self.assertNotIn('max_minor', gate)
        self.assertIn('"max_minor"', hello)                 # shape still validated
        self.assertIn('\\"protocol_minor\\":%u', hello)
        self.assertIn('(unsigned)WMP_PROTOCOL_MAJOR, (unsigned)WMP_PROTOCOL_MINOR', hello)

    def test_get_status_reports_parser_counters(self):
        status = c_function(self.source, 'static void add_protocol_rx_status(cJSON *result)\n{')
        for name in ('bad_frames', 'partial_timeouts', 'resyncs', 'overflows'):
            self.assertIn(f'"{name}"', status)
        self.assertIn('add_protocol_rx_status(result);', self.source)


if __name__ == '__main__':
    unittest.main()
