import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

spec = importlib.util.spec_from_file_location("handoff_stats_mod", BACKEND / "services" / "handoff_stats.py")
handoff_stats_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff_stats_mod)
aggregate_handoff_top = handoff_stats_mod.aggregate_handoff_top
matches_scope = handoff_stats_mod.matches_scope
parse_human_handoff = handoff_stats_mod.parse_human_handoff


def row(reason, decision, occurred, slice_id="s1"):
    return {
        "id": abs(hash(slice_id)) % 10000,
        "slice_id": slice_id,
        "human_handoff": {
            "reason_type": reason,
            "decision": decision,
            "handoff_occurred": occurred,
            "reason": reason,
        },
    }


class HandoffStatsTest(unittest.TestCase):
    def test_parse_invalid_json(self):
        self.assertIsNone(parse_human_handoff(""))
        self.assertIsNone(parse_human_handoff("{"))
        self.assertEqual(parse_human_handoff('{"decision":"handoff_reasonable"}')["decision"], "handoff_reasonable")

    def test_scope_match(self):
        self.assertTrue(matches_scope({"handoff_occurred": True}, "occurred"))
        self.assertFalse(matches_scope({"handoff_occurred": False}, "occurred"))
        self.assertTrue(matches_scope({"decision": "handoff_required"}, "required"))
        self.assertTrue(matches_scope({"decision": "handoff_unreasonable"}, "unreasonable"))

    def test_top_defaults_to_occurred_and_ranks_attachment(self):
        records = [
            row("玩家消息包含附件", "handoff_reasonable", True, "a1"),
            row("玩家消息包含附件", "handoff_reasonable", True, "a2"),
            row("玩家发送表情贴纸", "handoff_reasonable", True, "b1"),
            row("玩家明确要求人工", "handoff_required", False, "c1"),
            row("可由AI继续处理", "handoff_unreasonable", True, "d1"),
            row("证据不足", "manual_review", False, "e1"),
        ]
        result = aggregate_handoff_top(records)
        self.assertEqual(result["scope"], "occurred")
        self.assertEqual(result["summary"]["occurred"], 4)
        self.assertEqual(result["summary"]["required"], 1)
        self.assertEqual(result["reasons"][0]["reason_type"], "玩家消息包含附件")
        self.assertEqual(result["reasons"][0]["count"], 2)
        self.assertEqual(result["reasons"][0]["reasonable"], 2)
        self.assertTrue(result["reasons"][0]["is_positive"])
        negative = [item for item in result["reasons"] if item["reason_type"] == "可由AI继续处理"][0]
        self.assertFalse(negative["is_positive"])
        self.assertEqual(result["total"], 4)

    def test_required_scope_and_reason_filter(self):
        records = [
            row("玩家消息包含附件", "handoff_required", False, "a1"),
            row("玩家发送表情贴纸", "handoff_required", False, "b1"),
            row("玩家明确要求人工", "handoff_reasonable", True, "c1"),
        ]
        result = aggregate_handoff_top(records, scope="required", reason_type="玩家消息包含附件")
        self.assertEqual(result["scoped_count"], 2)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["slices"][0]["slice_id"], "a1")
        self.assertEqual(result["selected_reason_type"], "玩家消息包含附件")


if __name__ == "__main__":
    unittest.main()