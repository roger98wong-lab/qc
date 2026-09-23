import os, sys, unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from routers import admin as admin_router


class MappingGapLogicTest(unittest.TestCase):
    def test_standard_specific_rule_covers(self):
        row = SimpleNamespace(match_field="none", raw_channel="VK", game="妖怪金手指", raw_region=None, enabled=True)
        self.assertTrue(admin_router._standard_rule_covers(row, "英语区", "VK", "妖怪金手指"))

    def test_wildcard_does_not_cover_specific_gap(self):
        row = SimpleNamespace(match_field="none", raw_channel="VK", game=None, raw_region=None, enabled=True)
        self.assertFalse(admin_router._standard_rule_covers(row, "英语区", "VK", "妖怪金手指"))

    def test_norm_channel(self):
        self.assertEqual(admin_router._norm_channel("VK-English"), "VK")
        self.assertEqual(admin_router._norm_channel("官网客服"), "官网客服")


class MappingPrepareTest(unittest.TestCase):
    def test_prepare_game_product_clears_raw_region(self):
        payload = admin_router._prepare_mapping_fields({
            "raw_region": "欧美",
            "raw_channel": "官网客服",
            "game": "勇者联盟",
            "match_field": "gameProductId",
            "match_value": "1779344175941",
            "target_channel": "官网客服",
            "target_region": "欧美",
            "enabled": True,
        })
        self.assertIsNone(payload["raw_region"])
        self.assertEqual(payload["match_value"], "1779344175941")

    def test_prepare_other_fields_keep_raw_region(self):
        payload = admin_router._prepare_mapping_fields({
            "raw_region": "英语区",
            "raw_channel": "DC",
            "match_field": "none",
            "target_channel": "DC",
        })
        self.assertEqual(payload["raw_region"], "英语区")


if __name__ == "__main__":
    unittest.main()
