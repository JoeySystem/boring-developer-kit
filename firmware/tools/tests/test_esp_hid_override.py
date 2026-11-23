"""components/esp_hid must stay ESP-IDF v6.0.2 plus only the BORING additions.

The project-level component silently replaces the IDF one, so an accidental
edit (or a stale copy after an IDF upgrade) would ship unnoticed. Every file
except the documented additions must hash to the recorded upstream copy, and
src/ble_hidd.c must equal upstream once the added include and block are
removed. See components/esp_hid/BORING_CHANGES.md.
"""

import hashlib
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "components/esp_hid"
ADDED_FILES = {"BORING_CHANGES.md", "include/esp_hidd_ble_subscriptions.h"}
ADDED_INCLUDE = '#include "esp_hidd_ble_subscriptions.h"\n'
ADDED_BLOCK_START = "\n/*\n * BORING MIST: per-bond Client Characteristic"
ADDED_BLOCK_END = "\n#endif /* CONFIG_GATTS_ENABLE */"

# sha256 of ESP-IDF v6.0.2 (7101770dc6db2667b3c477cc31365dd1acd6db4e)
# components/esp_hid, test_apps excluded.
UPSTREAM_SHA256 = {
    "CMakeLists.txt": "d4982ed589b727502ad2bd6cc41a72ff0090f583cc722bda74dcb5caac373b11",
    "Kconfig": "bdb477c6ac1e931da6a5a44cf4157558bc522707c3d23d603bd1aab22f041a52",
    "include/esp_hid_common.h": "2810511caa79f7c64aaec58b61b6f3f46020f43637f9bb72b94c3cecd238ba31",
    "include/esp_hidd.h": "8cff8ea2c8955299dbad0d4301b147a7b4dfaac81a37f428de88a4dcd0e3cfa8",
    "include/esp_hidd_gatts.h": "fc7a76bf4d7fc756ae18876dbd465757af4572f60e68e7e114d59810ee80b50e",
    "include/esp_hidd_transport.h": "c575ebebc819ae3110951d5b42713cb83e3af1938a28458303ba0d315f8f5e98",
    "include/esp_hidh.h": "28255423b079dea92965ee4247386d1165d477be88d1049e2517538045a92102",
    "include/esp_hidh_bluedroid.h": "6dce15a8babc99e025ce7c7d78efe146366efd2935c089a86250c064d9fdbbc1",
    "include/esp_hidh_gattc.h": "55f6436ca6c2ade92193bf126ef2e6b1ed67360d14cf620f97704dcbbd119cc7",
    "include/esp_hidh_nimble.h": "fea1da7e533140b7435134adc2f931752304b110968b27a78ddb5fdf47dbfee3",
    "include/esp_hidh_transport.h": "0f8ddf75986de24e3d0f002178d88345a27dd6052f40953e66f6fd8569dc029e",
    "include/esp_private/esp_hidd_private.h": "5a649710692037e0eceea57abd0ce14d72e825548f1d6396a688d58a312e51d2",
    "include/esp_private/esp_hidh_private.h": "7c4c9f267de8235bc1908c231842e0ac70c8c0bb84c42d79fdd4b4fb8b4bb02b",
    "private/ble_hidd.h": "a3375937c68f652eba23477eaf0f1bb3ab1e0ea23d8d7ec7ca21926f1898b6d7",
    "private/ble_hidh.h": "ba39e37e03461f71cf7d40856f5ff9cba535f875eb97e5dd41cc466c362ca1bb",
    "private/bt_hidd.h": "dc951529bd83e1351ca2ac4ca37ea480ac13d221f5973cba8c12d5bc34c978e4",
    "private/bt_hidh.h": "a71b40be817e5f80ffa7cabc320e6516788c5e04053f50142729079b29ab2b3e",
    "src/ble_hidd.c": "fbecd291edd77a34a871f07ad3c5e9f5c74354cbda544c4a667ab5864778d31b",
    "src/ble_hidh.c": "6a6fbe79d413f24ebae3732d62ce55c8bced32329d5773c14acea532997a052e",
    "src/bt_hidd.c": "b404914bd84518c4b1147c217c7e40cdfe688ffced0ce66c540fffb7349eb1f6",
    "src/bt_hidh.c": "c46ce83697e4d9b71ec6fde956d7507c16d7fe06329473acd09bd6d7c1a4c231",
    "src/esp_hid_common.c": "af20d5b8e3c571d9aa003fdcee2eb4f164c3c419b0149941be92f1f3c3322af6",
    "src/esp_hidd.c": "039e8ff516db6d2c6c260dc7111c73a2112a8d44c95290ee3db9510b7fc7180f",
    "src/esp_hidh.c": "169b12cabb820d96f5166880675afd5a87429be1539b07ba3fc73697c08de74b",
    "src/nimble_hidd.c": "d227d89c67e44efef66a8b5d9016db88ead5202838898184603a83dec936c2de",
    "src/nimble_hidh.c": "7e1a3ff2307a6acd387899d59cd6342a8ebb24cdcdf9d3038cfd624b3a0131e8",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def strip_additions(source):
    if source.count(ADDED_INCLUDE) != 1:
        raise AssertionError("added include missing or duplicated")
    source = source.replace(ADDED_INCLUDE, "", 1)
    start = source.index(ADDED_BLOCK_START)
    end = source.index(ADDED_BLOCK_END, start)
    return source[:start] + source[end:]


class EspHidOverrideTests(unittest.TestCase):
    def test_component_contains_exactly_upstream_and_documented_files(self):
        present = {
            str(path.relative_to(COMPONENT))
            for path in COMPONENT.rglob("*") if path.is_file()
        }
        self.assertEqual(present, set(UPSTREAM_SHA256) | ADDED_FILES)

    def test_untouched_files_match_upstream(self):
        for name, expected in UPSTREAM_SHA256.items():
            if name == "src/ble_hidd.c":
                continue
            with self.subTest(name=name):
                self.assertEqual(sha256((COMPONENT / name).read_bytes()),
                                 expected)

    def test_ble_hidd_changes_are_additions_only(self):
        source = (COMPONENT / "src/ble_hidd.c").read_text()
        self.assertEqual(sha256(strip_additions(source).encode()),
                         UPSTREAM_SHA256["src/ble_hidd.c"])

    def test_added_block_never_touches_gatt_attribute_values(self):
        # set_attr_value would post SET_ATTR_VAL_EVT and desync the semaphore
        # input_set() waits on; the restore must be RAM-only.
        source = (COMPONENT / "src/ble_hidd.c").read_text()
        start = source.index(ADDED_BLOCK_START)
        block = source[start:source.index(ADDED_BLOCK_END, start)]
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        self.assertNotIn("esp_ble_gatts_set_attr_value", block)
        self.assertNotIn("esp_ble_gatts_send_indicate", block)


if __name__ == "__main__":
    unittest.main()
