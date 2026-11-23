"""BLE host policy: which remembered computer the transport should be on.

The device remembers up to three computers, one per slot, and the user should
not have to think in slots. Selecting an empty slot while another computer is
remembered opens a pairing window in memory only; an unused window returns to
the computer it came from; and while any computer is remembered, the active
slot (persisted or chosen at boot) points at a remembered computer.

The model is pure, so every function is checked over all eight combinations of
remembered computers and every valid input, plus NULL and out-of-range slots.

It also records, as a table, how the transport accepts or rejects a connecting
computer today at its three decision points. Nothing calls that function yet;
the table is the baseline the next step changes, including the cells where the
three decision points disagree.
"""

import ctypes
import itertools
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/codex_micro"
MODEL = COMPONENT / "ble_host_policy_model.c"
HEADER = COMPONENT / "include/ble_host_policy_model.h"

SLOTS = (1, 2, 3)
INVALID_SLOTS = (0, 4, 5, 127, 255)
# Keys read left to right as slots 1, 2, 3; "1" = that slot holds a computer.
ALL_RECORDS = ("000", "100", "010", "110", "001", "101", "011", "111")

FREE_SLOT = {"000": 1, "100": 2, "010": 1, "110": 3,
             "001": 1, "101": 2, "011": 1, "111": 0}
# NEXT_HOST[records] = where the switch key goes from slot 1, 2, 3 (0 = no
# other computer is remembered).
NEXT_HOST = {"000": (0, 0, 0), "100": (0, 1, 1), "010": (2, 0, 2), "110": (2, 1, 1),
             "001": (3, 3, 0), "101": (3, 3, 1), "011": (2, 3, 2), "111": (2, 3, 1)}

# Columns: persisted (boot) / selected / cleared slot 1, 2, 3.
BOOT_SLOT = {"000": (1, 2, 3), "100": (1, 1, 1), "010": (2, 2, 2), "110": (1, 2, 1),
             "001": (3, 3, 3), "101": (1, 1, 3), "011": (2, 2, 3), "111": (1, 2, 3)}
SELECT_PERSISTS = {"000": (True, True, True), "100": (True, False, False),
                   "010": (False, True, False), "110": (True, True, False),
                   "001": (False, False, True), "101": (True, False, True),
                   "011": (False, True, True), "111": (True, True, True)}
ACTIVE_AFTER_CLEAR = {"000": (1, 2, 3), "100": (1, 1, 1), "010": (2, 2, 2),
                      "110": (1, 1, 1), "001": (3, 3, 3), "101": (1, 1, 1),
                      "011": (2, 2, 2), "111": (1, 1, 1)}
# RETURN_SLOT[records][selected slot] = result for current slot 1, 2, 3.
# Rows that are not listed (the selected slot holds a computer, or nothing is
# remembered) are all 0: there is no pairing window or nowhere to return.
RETURN_SLOT = {"100": {2: (1, 1, 1), 3: (1, 1, 1)},
               "010": {1: (2, 2, 2), 3: (2, 2, 2)},
               "110": {3: (1, 2, 1)},
               "001": {1: (3, 3, 3), 2: (3, 3, 3)},
               "101": {2: (1, 1, 3)},
               "011": {1: (2, 2, 3)}}

WINDOW_FLAGS = ("fast_window_elapsed", "link_connected", "suspended", "busy")
WINDOW_FLAGS_EXPIRED = (True, False, False, False)

CONNECT, SECURITY_REQUEST, AUTH_COMPLETE = STAGES = (0, 1, 2)
ACCEPT, REJECT_SUSPENDED, REJECT_OTHER_SLOT, REJECT_UNKNOWN_HOST = 0, 1, 2, 3
# Present behaviour while BLE is not suspended (suspended rejects everything).
# Key: (active slot holds a computer, host matches the active slot, host
# belongs to another slot). Value: decision at CONNECT, SECURITY_REQUEST,
# AUTH_COMPLETE.
DECISIONS = {
    (False, False, False): (ACCEPT, ACCEPT, ACCEPT),
    (False, False, True): (ACCEPT, ACCEPT, REJECT_OTHER_SLOT),            # stages differ
    (False, True, False): (ACCEPT, ACCEPT, ACCEPT),
    (False, True, True): (ACCEPT, ACCEPT, REJECT_OTHER_SLOT),             # stages differ
    (True, False, False): (REJECT_UNKNOWN_HOST,) * 3,
    (True, False, True): (REJECT_OTHER_SLOT,) * 3,
    (True, True, False): (ACCEPT, ACCEPT, ACCEPT),
    (True, True, True): (ACCEPT, ACCEPT, REJECT_OTHER_SLOT),              # stages differ
}
STAGES_DIFFER = {(False, False, True), (False, True, True), (True, True, True)}


class Records(ctypes.Structure):
    _fields_ = [("paired", ctypes.c_bool * 3)]


class Window(ctypes.Structure):
    _fields_ = [("active_slot", ctypes.c_uint8), ("return_slot", ctypes.c_uint8),
                ("fast_window_elapsed", ctypes.c_bool), ("link_connected", ctypes.c_bool),
                ("suspended", ctypes.c_bool), ("busy", ctypes.c_bool)]


class Peer(ctypes.Structure):
    _fields_ = [("stage", ctypes.c_int), ("suspended", ctypes.c_bool),
                ("active_paired", ctypes.c_bool), ("matches_active", ctypes.c_bool),
                ("other_slot", ctypes.c_uint8)]


def records(key):
    return Records((ctypes.c_bool * 3)(*(flag == "1" for flag in key)))


def paired(key, slot):
    return slot in SLOTS and key[slot - 1] == "1"


def load(directory):
    library = Path(directory) / "host_policy.so"
    subprocess.run(["cc", "-std=c11", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror",
                    "-I", str(COMPONENT / "include"), str(MODEL), "-o", str(library)],
                   check=True, capture_output=True)
    lib = ctypes.CDLL(str(library))
    records_p, window_p, u8 = ctypes.POINTER(Records), ctypes.POINTER(Window), ctypes.c_uint8
    for name, restype, argtypes in (
            ("free_slot", u8, [records_p]),
            ("next_host", u8, [records_p, u8]),
            ("boot_slot", u8, [records_p, u8]),
            ("select_persists", ctypes.c_bool, [records_p, u8]),
            ("return_slot", u8, [records_p, u8, u8]),
            ("active_after_clear", u8, [records_p, u8]),
            ("window_expired", ctypes.c_bool, [records_p, window_p])):
        function = getattr(lib, f"ble_host_policy_{name}")
        function.restype, function.argtypes = restype, argtypes
    lib.ble_host_policy_decide.restype = ctypes.c_int
    lib.ble_host_policy_decide.argtypes = [ctypes.POINTER(Peer)]
    return lib


class HostPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.lib = load(cls.temp.name)

    def free_slot(self, key):
        return self.lib.ble_host_policy_free_slot(None if key is None else records(key))

    def next_host(self, key, current):
        return self.lib.ble_host_policy_next_host(None if key is None else records(key),
                                                  current)

    def boot_slot(self, key, persisted):
        return self.lib.ble_host_policy_boot_slot(None if key is None else records(key),
                                                  persisted)

    def select_persists(self, key, slot):
        return self.lib.ble_host_policy_select_persists(
            None if key is None else records(key), slot)

    def return_slot(self, key, current, slot):
        return self.lib.ble_host_policy_return_slot(None if key is None else records(key),
                                                    current, slot)

    def active_after_clear(self, key, cleared):
        return self.lib.ble_host_policy_active_after_clear(
            None if key is None else records(key), cleared)

    def window_expired(self, key, active, back, flags=WINDOW_FLAGS_EXPIRED):
        window = Window(active, back, *flags)
        return self.lib.ble_host_policy_window_expired(records(key), ctypes.byref(window))

    def decide(self, stage, suspended, active_paired, matches_active, other_slot):
        peer = Peer(stage, suspended, active_paired, matches_active, other_slot)
        return self.lib.ble_host_policy_decide(ctypes.byref(peer))


class TableTests(HostPolicyTests):
    def test_tables_cover_every_combination(self):
        every = {"".join(bits) for bits in itertools.product("01", repeat=3)}
        self.assertEqual(every, set(ALL_RECORDS))
        for table in (FREE_SLOT, NEXT_HOST, BOOT_SLOT, SELECT_PERSISTS,
                      ACTIVE_AFTER_CLEAR):
            self.assertEqual(every, set(table))

    def test_free_slot(self):
        for key in ALL_RECORDS:
            with self.subTest(records=key):
                self.assertEqual(FREE_SLOT[key], self.free_slot(key))

    def test_next_host(self):
        for key in ALL_RECORDS:
            for current in SLOTS:
                with self.subTest(records=key, current=current):
                    self.assertEqual(NEXT_HOST[key][current - 1],
                                     self.next_host(key, current))

    def test_next_host_is_another_remembered_computer_or_nothing(self):
        for key in ALL_RECORDS:
            for current in SLOTS:
                with self.subTest(records=key, current=current):
                    others = [slot for slot in SLOTS
                              if slot != current and paired(key, slot)]
                    result = self.next_host(key, current)
                    if others:
                        self.assertIn(result, others)
                    else:
                        self.assertEqual(0, result)

    def test_pressing_the_switch_key_visits_every_computer_in_turn(self):
        for key in ALL_RECORDS:
            remembered = [slot for slot in SLOTS if paired(key, slot)]
            if len(remembered) < 2:
                continue
            with self.subTest(records=key):
                current, visited = remembered[0], []
                for _ in remembered:
                    current = self.next_host(key, current)
                    visited.append(current)
                self.assertEqual(sorted(remembered), sorted(visited))
                self.assertEqual(remembered[0], current)

    def test_next_host_reads_an_invalid_current_slot_as_slot_one(self):
        for key in ALL_RECORDS:
            for current in INVALID_SLOTS:
                with self.subTest(records=key, current=current):
                    self.assertEqual(self.next_host(key, 1),
                                     self.next_host(key, current))
        for current in SLOTS + INVALID_SLOTS:
            self.assertEqual(0, self.next_host(None, current))

    def test_boot_slot(self):
        for key, persisted in itertools.product(ALL_RECORDS, SLOTS):
            with self.subTest(records=key, persisted=persisted):
                self.assertEqual(BOOT_SLOT[key][persisted - 1],
                                 self.boot_slot(key, persisted))

    def test_select_persists(self):
        for key, slot in itertools.product(ALL_RECORDS, SLOTS):
            with self.subTest(records=key, slot=slot):
                self.assertIs(SELECT_PERSISTS[key][slot - 1],
                              self.select_persists(key, slot))

    def test_return_slot(self):
        for key, slot, current in itertools.product(ALL_RECORDS, SLOTS, SLOTS):
            expected = RETURN_SLOT.get(key, {}).get(slot, (0, 0, 0))[current - 1]
            with self.subTest(records=key, current=current, slot=slot):
                self.assertEqual(expected, self.return_slot(key, current, slot))

    def test_active_after_clear(self):
        for key, cleared in itertools.product(ALL_RECORDS, SLOTS):
            with self.subTest(records=key, cleared=cleared):
                self.assertEqual(ACTIVE_AFTER_CLEAR[key][cleared - 1],
                                 self.active_after_clear(key, cleared))

    def test_window_expired(self):
        slots = (0,) + SLOTS + (4, 255)
        for key, active, back in itertools.product(ALL_RECORDS, slots, slots):
            for flags in itertools.product((False, True), repeat=len(WINDOW_FLAGS)):
                expected = (active in SLOTS and not paired(key, active) and
                            paired(key, back) and flags == WINDOW_FLAGS_EXPIRED)
                with self.subTest(records=key, active=active, back=back, flags=flags):
                    self.assertIs(expected, self.window_expired(key, active, back, flags))


class InvalidInputTests(HostPolicyTests):
    def test_free_slot_without_records_offers_nothing(self):
        self.assertEqual(0, self.free_slot(None))

    def test_boot_slot_reads_any_invalid_byte_as_slot_one(self):
        for key, persisted in itertools.product(ALL_RECORDS, range(256)):
            normalized = persisted if persisted in SLOTS else 1
            with self.subTest(records=key, persisted=persisted):
                self.assertEqual(BOOT_SLOT[key][normalized - 1],
                                 self.boot_slot(key, persisted))

    def test_boot_slot_without_records_keeps_the_normalized_slot(self):
        for persisted in range(256):
            with self.subTest(persisted=persisted):
                self.assertEqual(persisted if persisted in SLOTS else 1,
                                 self.boot_slot(None, persisted))

    def test_select_never_persists_an_invalid_slot(self):
        for key, slot in itertools.product(ALL_RECORDS, INVALID_SLOTS):
            with self.subTest(records=key, slot=slot):
                self.assertIs(False, self.select_persists(key, slot))
        for slot in SLOTS + INVALID_SLOTS:
            with self.subTest(records=None, slot=slot):
                self.assertIs(False, self.select_persists(None, slot))

    def test_return_slot_is_zero_for_an_invalid_selection(self):
        currents = SLOTS + INVALID_SLOTS
        for key, current, slot in itertools.product(ALL_RECORDS, currents, INVALID_SLOTS):
            with self.subTest(records=key, current=current, slot=slot):
                self.assertEqual(0, self.return_slot(key, current, slot))
        for current, slot in itertools.product(currents, currents):
            with self.subTest(records=None, current=current, slot=slot):
                self.assertEqual(0, self.return_slot(None, current, slot))

    def test_return_slot_treats_an_invalid_current_slot_as_empty(self):
        for key, current, slot in itertools.product(ALL_RECORDS, INVALID_SLOTS, SLOTS):
            others = [other for other in SLOTS if other != slot and paired(key, other)]
            expected = 0 if paired(key, slot) or not others else others[0]
            with self.subTest(records=key, current=current, slot=slot):
                self.assertEqual(expected, self.return_slot(key, current, slot))

    def test_active_after_clear_never_returns_an_invalid_slot(self):
        for key, cleared in itertools.product(ALL_RECORDS, INVALID_SLOTS):
            with self.subTest(records=key, cleared=cleared):
                self.assertEqual(ACTIVE_AFTER_CLEAR[key][0],
                                 self.active_after_clear(key, cleared))
        for cleared in SLOTS + INVALID_SLOTS:
            with self.subTest(records=None, cleared=cleared):
                self.assertEqual(cleared if cleared in SLOTS else 1,
                                 self.active_after_clear(None, cleared))

    def test_window_never_expires_without_records_or_window(self):
        window = Window(2, 1, *WINDOW_FLAGS_EXPIRED)
        self.assertIs(True, self.lib.ble_host_policy_window_expired(
            records("100"), ctypes.byref(window)))
        self.assertIs(False, self.lib.ble_host_policy_window_expired(
            None, ctypes.byref(window)))
        self.assertIs(False, self.lib.ble_host_policy_window_expired(records("100"), None))
        self.assertIs(False, self.lib.ble_host_policy_window_expired(None, None))


class InvariantTests(HostPolicyTests):
    """The rules the callers rely on, asserted directly rather than via tables."""

    def test_boot_lands_on_a_remembered_computer_whenever_there_is_one(self):
        for key, persisted in itertools.product(ALL_RECORDS, range(256)):
            slot = self.boot_slot(key, persisted)
            with self.subTest(records=key, persisted=persisted):
                self.assertIn(slot, SLOTS)
                if "1" in key:
                    self.assertTrue(paired(key, slot))
                else:
                    self.assertEqual(persisted if persisted in SLOTS else 1, slot)

    def test_boot_keeps_a_persisted_slot_that_holds_a_computer(self):
        for key, persisted in itertools.product(ALL_RECORDS, SLOTS):
            if paired(key, persisted):
                with self.subTest(records=key, persisted=persisted):
                    self.assertEqual(persisted, self.boot_slot(key, persisted))

    def test_a_memory_only_selection_always_has_somewhere_to_return(self):
        for key, slot in itertools.product(ALL_RECORDS, SLOTS):
            if self.select_persists(key, slot):
                continue
            self.assertFalse(paired(key, slot))
            for current in SLOTS + INVALID_SLOTS:
                back = self.return_slot(key, current, slot)
                with self.subTest(records=key, current=current, slot=slot):
                    self.assertNotEqual(0, back)
                    self.assertNotEqual(slot, back)
                    self.assertTrue(paired(key, back))

    def test_a_persisted_selection_never_leaves_a_computer_behind(self):
        # Writing an empty slot as active is only allowed when nothing is
        # remembered at all.
        for key, slot in itertools.product(ALL_RECORDS, SLOTS):
            if self.select_persists(key, slot):
                with self.subTest(records=key, slot=slot):
                    self.assertTrue(paired(key, slot) or "1" not in key)

    def test_return_prefers_the_current_computer(self):
        for key, current, slot in itertools.product(ALL_RECORDS, SLOTS, SLOTS):
            if paired(key, current) and not paired(key, slot):
                with self.subTest(records=key, current=current, slot=slot):
                    self.assertEqual(current, self.return_slot(key, current, slot))

    def test_active_after_clear_points_at_a_remembered_computer(self):
        for key, cleared in itertools.product(ALL_RECORDS, SLOTS + INVALID_SLOTS):
            slot = self.active_after_clear(key, cleared)
            with self.subTest(records=key, cleared=cleared):
                self.assertIn(slot, SLOTS)
                if "1" in key:
                    self.assertTrue(paired(key, slot))
                elif cleared in SLOTS:
                    self.assertEqual(cleared, slot)

    def test_free_slot_is_the_lowest_empty_slot(self):
        for key in ALL_RECORDS:
            slot = self.free_slot(key)
            with self.subTest(records=key):
                if slot == 0:
                    self.assertEqual("111", key)
                else:
                    self.assertFalse(paired(key, slot))
                    self.assertTrue(all(paired(key, lower) for lower in range(1, slot)))

    def test_an_expired_window_returns_from_an_empty_slot_to_a_computer(self):
        slots = range(256)
        expired = 0
        for key, active, back in itertools.product(ALL_RECORDS, slots, slots):
            if self.window_expired(key, active, back):
                expired += 1
                with self.subTest(records=key, active=active, back=back):
                    self.assertIn(active, SLOTS)
                    self.assertFalse(paired(key, active))
                    self.assertTrue(paired(key, back))
        # Empty active slot x remembered return slot, summed over the eight
        # combinations: 3 choose k has k * (3 - k) such pairs.
        self.assertEqual(3 * 2 + 3 * 2, expired)

    def test_every_one_of_the_six_conditions_is_required(self):
        for key, active, back in itertools.product(ALL_RECORDS, SLOTS, SLOTS):
            if not self.window_expired(key, active, back):
                continue
            broken = {
                "active slot holds a computer":
                    self.window_expired(key, back, back),
                "return slot is empty":
                    self.window_expired(key, active, active),
                "no return slot":
                    self.window_expired(key, active, 0),
            }
            for index, name in enumerate(WINDOW_FLAGS):
                flags = list(WINDOW_FLAGS_EXPIRED)
                flags[index] = not flags[index]
                broken[name] = self.window_expired(key, active, back, tuple(flags))
            for name, result in broken.items():
                with self.subTest(records=key, active=active, back=back, missing=name):
                    self.assertIs(False, result)

    def test_returning_closes_the_window(self):
        # Once the transport is back on the return slot, nothing expires again.
        for key, active, back in itertools.product(ALL_RECORDS, SLOTS, SLOTS):
            if self.window_expired(key, active, back):
                with self.subTest(records=key, active=active, back=back):
                    self.assertIs(False, self.window_expired(key, back, 0))
                    self.assertIs(False, self.window_expired(key, back, back))


class DecisionTests(HostPolicyTests):
    """How a connecting computer is accepted today, cell by cell."""

    def test_table_covers_every_cell(self):
        self.assertEqual(set(itertools.product((False, True), repeat=3)), set(DECISIONS))

    def test_present_behaviour(self):
        for (active_paired, matches, other), expected in DECISIONS.items():
            for stage, other_slot in itertools.product(STAGES, SLOTS if other else (0,)):
                with self.subTest(stage=stage, active_paired=active_paired,
                                  matches=matches, other_slot=other_slot):
                    self.assertEqual(expected[stage], self.decide(
                        stage, False, active_paired, matches, other_slot))

    def test_suspended_rejects_everything_first(self):
        for stage, active_paired, matches, other_slot in itertools.product(
                STAGES, (False, True), (False, True), (0,) + SLOTS):
            with self.subTest(stage=stage, active_paired=active_paired,
                              matches=matches, other_slot=other_slot):
                self.assertEqual(REJECT_SUSPENDED, self.decide(
                    stage, True, active_paired, matches, other_slot))

    def test_stages_differ_only_in_the_recorded_cells(self):
        differing = {cell for cell, decisions in DECISIONS.items()
                     if len(set(decisions)) > 1}
        self.assertEqual(STAGES_DIFFER, differing)
        for cell, decisions in DECISIONS.items():
            with self.subTest(cell=cell):
                # Connect and the security request always agree; only the
                # completed pairing rejects a computer held by another slot
                # that the earlier stages let through.
                self.assertEqual(decisions[CONNECT], decisions[SECURITY_REQUEST])
                if cell in STAGES_DIFFER:
                    self.assertEqual((ACCEPT, REJECT_OTHER_SLOT),
                                     (decisions[CONNECT], decisions[AUTH_COMPLETE]))

    def test_a_computer_outside_a_remembered_active_slot_is_never_accepted(self):
        for stage, other_slot in itertools.product(STAGES, (0,) + SLOTS):
            with self.subTest(stage=stage, other_slot=other_slot):
                self.assertNotEqual(ACCEPT, self.decide(stage, False, True, False, other_slot))

    def test_match_is_ignored_while_the_active_slot_is_empty(self):
        for stage, other_slot in itertools.product(STAGES, (0,) + SLOTS):
            with self.subTest(stage=stage, other_slot=other_slot):
                self.assertEqual(self.decide(stage, False, False, False, other_slot),
                                 self.decide(stage, False, False, True, other_slot))

    def test_any_non_zero_other_slot_counts(self):
        for stage, active_paired, matches, other_slot in itertools.product(
                STAGES, (False, True), (False, True), (4, 127, 255)):
            with self.subTest(stage=stage, active_paired=active_paired,
                              matches=matches, other_slot=other_slot):
                self.assertEqual(DECISIONS[(active_paired, matches, True)][stage],
                                 self.decide(stage, False, active_paired, matches, other_slot))

    def test_invalid_input_is_rejected_without_side_effects(self):
        self.assertEqual(REJECT_SUSPENDED, self.lib.ble_host_policy_decide(None))
        for stage, active_paired, matches, other_slot in itertools.product(
                (-1, 3, 255), (False, True), (False, True), (0, 2)):
            with self.subTest(stage=stage, active_paired=active_paired,
                              matches=matches, other_slot=other_slot):
                self.assertEqual(REJECT_SUSPENDED, self.decide(
                    stage, False, active_paired, matches, other_slot))


class ModuleContractTests(unittest.TestCase):
    def test_model_is_pure_and_registered(self):
        source = MODEL.read_text()
        header = HEADER.read_text()
        includes = [line for text in (source, header) for line in text.splitlines()
                    if line.startswith("#include")]
        self.assertEqual(['#include "ble_host_policy_model.h"', "#include <stddef.h>",
                          "#include <stdbool.h>", "#include <stdint.h>"], includes)
        for forbidden in ("malloc", "calloc", "esp_"):
            self.assertNotIn(forbidden, source)
        self.assertIn('"ble_host_policy_model.c"',
                      (COMPONENT / "CMakeLists.txt").read_text())

    def test_agreed_signatures_are_unchanged(self):
        header = " ".join(HEADER.read_text().split())
        for signature in (
                "#define BLE_HOST_POLICY_SLOT_COUNT 3u",
                "typedef struct { bool paired[BLE_HOST_POLICY_SLOT_COUNT]; "
                "/* index = slot - 1 */ } ble_host_records_t;",
                "uint8_t ble_host_policy_free_slot(const ble_host_records_t *records);",
                "uint8_t ble_host_policy_boot_slot(const ble_host_records_t *records, "
                "uint8_t persisted_slot);",
                "bool ble_host_policy_select_persists(const ble_host_records_t *records, "
                "uint8_t slot);",
                "uint8_t ble_host_policy_return_slot(const ble_host_records_t *records, "
                "uint8_t current, uint8_t slot);",
                "uint8_t ble_host_policy_active_after_clear(const ble_host_records_t *records, "
                "uint8_t cleared_slot);",
                "bool ble_host_policy_window_expired(const ble_host_records_t *records, "
                "const ble_host_window_t *window);"):
            with self.subTest(signature=signature):
                self.assertIn(signature, header)
        self.assertIn("typedef enum { BLE_HOST_STAGE_CONNECT, /* ESP_GATTS_CONNECT_EVT */ "
                      "BLE_HOST_STAGE_SECURITY_REQUEST, /* ESP_GAP_BLE_SEC_REQ_EVT */ "
                      "BLE_HOST_STAGE_AUTH_COMPLETE, /* ESP_GAP_BLE_AUTH_CMPL_EVT, success */ "
                      "} ble_host_stage_t;", header)
        self.assertIn("typedef enum { BLE_HOST_ACCEPT = 0, BLE_HOST_REJECT_SUSPENDED, "
                      "BLE_HOST_REJECT_OTHER_SLOT, BLE_HOST_REJECT_UNKNOWN_HOST, "
                      "} ble_host_decision_t;", header)
        self.assertIn("ble_host_decision_t ble_host_policy_decide(const ble_host_peer_t *peer);",
                      header)
        fields = header[header.index("typedef struct { uint8_t active_slot;"):
                        header.index("} ble_host_window_t;")]
        order = [fields.index(field) for field in (
            "uint8_t active_slot;", "uint8_t return_slot;", "bool fast_window_elapsed;",
            "bool link_connected;", "bool suspended;", "bool busy;")]
        self.assertEqual(sorted(order), order)
        self.assertEqual(6, fields.count(";"))


if __name__ == "__main__":
    unittest.main()
