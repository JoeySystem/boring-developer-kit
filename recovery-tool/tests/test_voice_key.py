from __future__ import annotations

from copy import deepcopy
import unittest

from provisioner.errors import VerificationError
from provisioner.voice_key import (
    SET_CONFIG,
    VOICE_KEY_MAPPING,
    apply_voice_key_default,
    config_digest,
    ensure_voice_key_default,
)


def profile(name, key8=None, extra=()):
    mappings = [{"control_id": "key.7", "short_name": "Mic hold",
                 "action": {"type": "key", "usage": 7, "modifiers": [224, 225]}}]
    if key8 is not None:
        mappings.append(key8)
    mappings.extend(extra)
    return {"id": 0, "name": name, "mappings": mappings}


SPACE = {"control_id": "key.8", "short_name": "Space",
         "action": {"type": "key", "usage": 44, "modifiers": []}}
F13 = {"control_id": "key.8", "short_name": "F13/Send",
       "action": {"type": "key_gesture", "usage": 104, "modifiers": [],
                  "double_usage": 40, "double_modifiers": []}}


def config(*profiles, **root):
    return {"schema_version": 1, "active_profile": 0, "profiles": list(profiles), **root}


class ApplyTests(unittest.TestCase):
    def test_both_factory_profiles_get_the_gesture_from_any_old_value(self):
        source = config(profile("Codex macOS", SPACE), profile("Codex Windows", F13))
        before = deepcopy(source)
        updated, changes = apply_voice_key_default(source)
        self.assertEqual(source, before)  # input untouched
        for item in updated["profiles"]:
            self.assertIn(VOICE_KEY_MAPPING, item["mappings"])
            self.assertEqual(item["mappings"][0]["short_name"], "Mic hold")
        self.assertEqual(len(changes), 2)

    def test_gesture_matches_firmware_factory_default(self):
        self.assertEqual(VOICE_KEY_MAPPING["short_name"], "Voice/Send")
        self.assertEqual(VOICE_KEY_MAPPING["action"], {
            "type": "key_gesture", "usage": 44, "modifiers": [230],
            "double_usage": 40, "double_modifiers": [],
        })

    def test_missing_key8_is_added_and_custom_profiles_are_kept(self):
        custom = profile("My profile", SPACE)
        updated, changes = apply_voice_key_default(
            config(profile("Codex macOS"), profile("Codex Windows", VOICE_KEY_MAPPING), custom))
        self.assertIn(VOICE_KEY_MAPPING, updated["profiles"][0]["mappings"])
        self.assertEqual(updated["profiles"][2], custom)
        self.assertEqual(changes, ["Codex macOS：新增第 8 键"])

    def test_global_voice_action_is_removed(self):
        source = config(profile("Codex macOS", VOICE_KEY_MAPPING),
                        profile("Codex Windows", VOICE_KEY_MAPPING),
                        voice_action={"type": "key", "usage": 104})
        updated, changes = apply_voice_key_default(source)
        self.assertNotIn("voice_action", updated)
        self.assertEqual(len(changes), 1)

    def test_already_default_is_unchanged(self):
        source = config(profile("Codex macOS", VOICE_KEY_MAPPING),
                        profile("Codex Windows", VOICE_KEY_MAPPING))
        updated, changes = apply_voice_key_default(source)
        self.assertEqual(updated, source)
        self.assertEqual(changes, [])

    def test_missing_factory_profile_is_refused(self):
        with self.assertRaises(VerificationError):
            apply_voice_key_default(config(profile("Codex macOS", SPACE)))


class FakeDevice:
    def __init__(self, cfg, *, generation=6, activate_after=1, neutral=True):
        self.config = cfg
        self.generation = generation
        self.digest = config_digest(cfg)
        self.pending = None
        self.polls_left = activate_after
        self.neutral = neutral
        self.sets = []

    def get_config(self):
        return {"config": deepcopy(self.config), "generation": self.generation,
                "digest": self.digest}

    def exchange(self, message_type, payload):
        assert message_type == SET_CONFIG
        self.sets.append(payload)
        assert payload["base_generation"] == self.generation
        assert payload["digest"] == config_digest(payload["config"])
        self.pending = (payload["config"], self.generation + 1, payload["digest"])
        return {"command": "SET_CONFIG", "result": {
            "state": "PENDING_ACTIVATION", "generation": self.generation + 1,
            "digest": payload["digest"]}}

    def get_status(self):
        if self.pending and self.neutral:
            if self.polls_left <= 0:
                self.config, self.generation, self.digest = self.pending
                self.pending = None
            self.polls_left -= 1
        pending = None if not self.pending else {
            "generation": self.pending[1], "digest": self.pending[2]}
        return {"active": {"generation": self.generation, "digest": self.digest},
                "pending": pending, "inputs_neutral": self.neutral,
                "activation_failed": False}


class EnsureTests(unittest.TestCase):
    def clock(self):
        t = [0.0]
        return (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))

    def test_writes_waits_and_reads_back(self):
        device = FakeDevice(config(profile("Codex macOS", SPACE), profile("Codex Windows", F13)))
        now, sleep = self.clock()
        receipt = ensure_voice_key_default(device, now=now, sleep=sleep)
        self.assertTrue(receipt["changed"])
        self.assertEqual((receipt["previous_generation"], receipt["generation"]), (6, 7))
        self.assertEqual(len(device.sets), 1)
        for item in device.config["profiles"]:
            self.assertIn(VOICE_KEY_MAPPING, item["mappings"])

    def test_already_default_writes_nothing(self):
        device = FakeDevice(config(profile("Codex macOS", VOICE_KEY_MAPPING),
                                   profile("Codex Windows", VOICE_KEY_MAPPING)))
        receipt = ensure_voice_key_default(device)
        self.assertFalse(receipt["changed"])
        self.assertEqual(device.sets, [])

    def test_digest_mismatch_refuses_to_write(self):
        device = FakeDevice(config(profile("Codex macOS", SPACE), profile("Codex Windows", SPACE)))
        device.digest = "0" * 64
        with self.assertRaises(VerificationError):
            ensure_voice_key_default(device)
        self.assertEqual(device.sets, [])

    def test_held_input_times_out_with_a_hint(self):
        device = FakeDevice(config(profile("Codex macOS", SPACE), profile("Codex Windows", SPACE)),
                            neutral=False)
        now, sleep = self.clock()
        with self.assertRaisesRegex(VerificationError, "松开所有按键"):
            ensure_voice_key_default(device, now=now, sleep=sleep)


if __name__ == "__main__":
    unittest.main()
