import os, sys, unittest

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from services.excel_parser import REGION_BY_AGENT, _parse_vip, resolve_vip_region


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

    def _frame(self, content, role_id="39801497322782938"):
        return pd.DataFrame({
            "地区": [""], "客服": ["黄英杰"], "游戏": ["测试游戏"],
            "角色ID": [role_id], "角色名": ["Player"], "消息内容": [content],
        })

    def test_system_only_vip_row_is_dropped(self):
        content = "[客服-system][2026-09-17 12:00:00]活动推送"
        sessions, suggestions, error = _parse_vip(self._frame(content),)
        self.assertIsNone(error)
        self.assertEqual(sessions, [])
        self.assertEqual(suggestions, [])

    def test_vip_system_is_dropped_real_agent_kept(self):
        content = "\n".join([
            "[用户][2026-09-17 12:00:00]我需要帮助",
            "[客服-system][2026-09-17 12:01:00]内部通知",
            "[客服-张三][2026-09-17 12:02:00]我来处理",
        ])
        sessions, _, _ = _parse_vip(self._frame(content))
        self.assertEqual(len(sessions), 1)
        self.assertEqual([m["speaker"] for m in sessions[0]["slice_payload"]["messages"]], ["player", "human_agent"])

    def test_vip_role_id_is_decimal_text(self):
        content = "\n".join([
            "[用户][2026-09-17 12:00:00]hello",
            "[客服-张三][2026-09-17 12:01:00]done",
        ])
        sessions, _, _ = _parse_vip(self._frame(content, "3.9801497322782936e+16"))
        self.assertEqual(sessions[0]["session_uid"], "测试游戏|39801497322782936")
        self.assertNotIn("e+", sessions[0]["session_uid"].lower())

if __name__ == "__main__":
    unittest.main()
