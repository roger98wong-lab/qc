import os, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from services.excel_parser import REGION_BY_AGENT, resolve_vip_region


class VipRegionMappingTest(unittest.TestCase):
    def test_agent_column_maps_to_standard_region(self):
        self.assertEqual(resolve_vip_region("黄英杰"), "欧美")
        self.assertEqual(resolve_vip_region("谭彩雯"), "东南亚")
        self.assertEqual(REGION_BY_AGENT["徐碧芸"], "欧美")

    def test_prefixed_agent_name_maps(self):
        self.assertEqual(resolve_vip_region("客服-黄英杰"), "欧美")
        self.assertEqual(resolve_vip_region("🤵客服-谭彩雯"), "东南亚")

    def test_message_role_fallback(self):
        self.assertEqual(
            resolve_vip_region("", [{"role": "客服-焦思阳"}]),
            "欧美",
        )

    def test_unmapped_agent_is_none_not_vip_backend(self):
        self.assertIsNone(resolve_vip_region("未知客服"))
        self.assertIsNone(resolve_vip_region(""))


if __name__ == "__main__":
    unittest.main()
