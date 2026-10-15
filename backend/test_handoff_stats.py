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



class DetailStatsTest(unittest.TestCase):
    parent = "需要人工权限或后台处理"

    def records(self):
        records = [row(self.parent, "handoff_reasonable", True, str(i)) for i in range(5)]
        records[0]["human_handoff"]["detail_type"] = "充值金额查询"
        records[1]["human_handoff"]["detail_type"] = "充值金额查询"
        records[2]["human_handoff"]["detail_type"] = "VIP认证"
        records[4]["human_handoff"]["detail_type"] = None
        records += [row("玩家明确要求人工", "handoff_reasonable", True, "other")]
        return records

    def test_parent_denominator_includes_legacy_and_ignores_pagination(self):
        records = self.records()
        result = aggregate_handoff_top(records, reason_type=self.parent,
                                       detail_type="充值金额查询", page=2, page_size=1)
        self.assertEqual(result["scoped_count"], 6)
        self.assertEqual(result["detail_parent_count"], 5)
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["slices"]), 1)
        details = {item["detail_type"]: item for item in result["details"]}
        self.assertEqual(details["充值金额查询"]["share"], .4)
        self.assertEqual(details["未细分"]["count"], 2)
        self.assertEqual(sum(item["count"] for item in result["details"]), 5)
        self.assertNotIn("detail_type", records[3]["human_handoff"])

    def test_unclassified_can_be_filtered_and_malformed_legacy_is_safe(self):
        records = self.records()
        for bad in ("", "旧的未知标签", [], {"value": "充值金额查询"}):
            records[4]["human_handoff"]["detail_type"] = bad
            result = aggregate_handoff_top(records, reason_type=self.parent, detail_type="未细分")
            self.assertEqual(result["total"], 2)
            self.assertEqual(result["detail_parent_count"], 5)

    def test_other_parent_never_uses_detail_filter(self):
        result = aggregate_handoff_top(self.records(), reason_type="玩家明确要求人工",
                                       detail_type="充值金额查询")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["details"], [])
        self.assertIsNone(result["selected_detail_type"])

    def test_scopes_and_empty_parent(self):
        records = self.records()
        records[0]["human_handoff"].update(decision="handoff_required", handoff_occurred=False)
        records[1]["human_handoff"].update(decision="handoff_unreasonable")
        for scope in ("required", "unreasonable"):
            result = aggregate_handoff_top(records, scope=scope, reason_type=self.parent)
            self.assertEqual(result["detail_parent_count"], 1)
            self.assertEqual(result["details"][0]["share"], 1)
        empty = aggregate_handoff_top([], reason_type=self.parent)
        self.assertEqual(empty["details"], [])
        self.assertEqual(empty["detail_parent_count"], 0)


if __name__ == "__main__":
    unittest.main()