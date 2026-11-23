"""Power V2 Key 8 voice defaults.

NORMAL (Doubao voice-input default): click taps Right Option (macOS) / Right
Alt (Windows) + Space to start or stop dictation, double-click taps Enter.
Persisted configs that still hold the untouched 20261016.01-20261026.01 factory
gesture (click F13 / double Enter) or the unreleased 2c764bc "Voice hold" key
migrate; anything the user chose stays. Claude Code: Command+D / Ctrl+D held.
"""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_hid_report_sequence import function

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / 'components/config_store'
CJSON = ROOT / 'managed_components/espressif__cjson/cJSON'
LEGACY = {"control_id": "key.8", "short_name": "F13/Send",
          "action": {"type": "key_gesture", "usage": 104, "modifiers": [],
                     "double_usage": 40, "double_modifiers": []}}
VOICE = {"type": "key_gesture", "usage": 44, "modifiers": [230],
         "double_usage": 40, "double_modifiers": []}
CANDIDATE = {"control_id": "key.8", "short_name": "Voice hold",
             "action": {"type": "key", "usage": 44, "modifiers": [230]}}


class VoiceKeyDefaultTests(unittest.TestCase):
    def test_factory_profiles_hold_right_alt_space(self):
        config = json.loads((STORE / 'default_config_matrix12_power_v2.json').read_text())
        self.assertEqual(['Codex macOS', 'Codex Windows'], [p['name'] for p in config['profiles']])
        for profile in config['profiles']:
            key8 = next(m for m in profile['mappings'] if m['control_id'] == 'key.8')
            self.assertEqual(('Voice/Send', VOICE), (key8['short_name'], key8['action']))
        self.assertNotIn('voice_action', config)  # CODEX keeps official dictation


class VoiceKeyMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        out = Path(cls.tmp.name)
        source = (STORE / 'config_store.c').read_text()
        start = source.index('#define POWER_V2_VOICE_SHORT_NAME')
        defines = source[start:source.index('\n\n', start)]
        code = r'''
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "cJSON.h"
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_STATE 0x103
#define CONFIG_MACROPAD_BOARD_MATRIX12_POWER_V2 1
''' + defines + '\n'
        for name in ('field', 'mutable_field', 'profile_mapping',
                     'empty_or_missing_array', 'mapping_is_legacy_power_v2_voice_gesture',
                     'mapping_is_candidate_power_v2_voice_hold', 'replace_with_voice_gesture',
                     'migrate_power_v2_default_voice_key'):
            code += function(source, name) + '\n'
        code += r'''
const char *migrate(const char *json, int *changed) {
    static char *last;
    free(last); last = NULL;
    cJSON *root = cJSON_Parse(json);
    bool did = false;
    if (root == NULL || migrate_power_v2_default_voice_key(root, &did) != ESP_OK) return NULL;
    *changed = did;
    last = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return last;
}
'''
        path = out / 'migrate.c'
        path.write_text(code)
        library = out / 'migrate.so'
        subprocess.run(['cc', '-std=c11', '-shared', '-fPIC', '-I', str(CJSON), str(path),
                        str(CJSON / 'cJSON.c'), '-o', str(library)], check=True,
                       capture_output=True)
        cls.lib = ctypes.CDLL(str(library))
        cls.lib.migrate.restype = ctypes.c_char_p
        cls.lib.migrate.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]

    def run_migration(self, profiles, **root):
        changed = ctypes.c_int(0)
        result = self.lib.migrate(json.dumps({**root, 'profiles': profiles}).encode(),
                                  ctypes.byref(changed))
        return json.loads(result), bool(changed.value)

    @staticmethod
    def profile(name, key8):
        return {'id': 0, 'name': name, 'mappings': [
            {'control_id': 'key.7', 'short_name': 'Mic hold',
             'action': {'type': 'key', 'usage': 7, 'modifiers': [224, 225]}}, key8]}

    def test_untouched_factory_gesture_moves_in_both_factory_profiles(self):
        bare = {**LEGACY, 'action': {'type': 'key_gesture', 'usage': 104, 'double_usage': 40}}
        root, changed = self.run_migration([self.profile('Codex macOS', LEGACY),
                                            self.profile('Codex Windows', bare)])
        self.assertTrue(changed)
        for profile in root['profiles']:
            key8 = profile['mappings'][1]
            self.assertEqual(('Voice/Send', VOICE), (key8['short_name'], key8['action']))
            self.assertEqual(7, profile['mappings'][0]['action']['usage'])

    def test_unreleased_hold_candidate_moves(self):
        root, changed = self.run_migration([self.profile('Codex Windows', CANDIDATE)])
        self.assertTrue(changed)
        key8 = root['profiles'][0]['mappings'][1]
        self.assertEqual(('Voice/Send', VOICE), (key8['short_name'], key8['action']))

    def test_user_choices_stay(self):
        cases = [
            self.profile('Codex macOS', {**LEGACY, 'short_name': 'Typeless'}),
            self.profile('Codex macOS', {**LEGACY, 'action': {**LEGACY['action'], 'usage': 105}}),
            self.profile('Codex macOS', {**LEGACY, 'action': {**LEGACY['action'], 'modifiers': [224]}}),
            self.profile('Codex macOS', {**LEGACY, 'action': {**LEGACY['action'], 'double_usage': 44}}),
            self.profile('Codex macOS', {'control_id': 'key.8', 'short_name': 'F13/Send',
                                         'action': {'type': 'key', 'usage': 104, 'modifiers': []}}),
            self.profile('My profile', LEGACY),
            self.profile('Codex macOS', {**CANDIDATE, 'short_name': 'Doubao'}),
            self.profile('Codex macOS', {**CANDIDATE, 'action': {**CANDIDATE['action'],
                                                                'modifiers': [226]}}),
        ]
        for profile in cases:
            with self.subTest(profile=profile):
                root, changed = self.run_migration([profile])
                self.assertFalse(changed)
                self.assertEqual(profile, root['profiles'][0])

    def test_migrated_config_is_stable(self):
        root, _ = self.run_migration([self.profile('Codex macOS', LEGACY)])
        again, changed = self.run_migration(root['profiles'])
        self.assertFalse(changed)
        self.assertEqual(root, again)

    def test_runs_on_every_load_path(self):
        source = (STORE / 'config_store.c').read_text()
        root = function(source, 'migrate_matrix12_v1_root')
        self.assertIn('migrate_power_v2_default_voice_key(root, changed)', root)
        self.assertLess(root.index('migrate_legacy_matrix12_extra_keys'),
                        root.index('migrate_power_v2_default_voice_key'))


if __name__ == '__main__':
    unittest.main()
