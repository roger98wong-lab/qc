import os, sys, unittest
from types import SimpleNamespace
from pathlib import Path
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from routers.workbench import ACTIONABLE_KB, _human_agent_name, _kb_list_decisions, _last_modified_at, _matches, _normalize_kb_decisions, _session_time, _slice_language
from routers import review_processing as review_router
from routers import admin as admin_router

from routers import assignments as assignment_router

from routers.knowledge_pool import _serialize as serialize_pool


class KnowledgeSuggestionDisplayTest(unittest.TestCase):
    def test_human_agent_name_strips_role_prefix_and_ignores_player(self):
        messages = [
            {"speaker": "player", "speaker_source": "用户", "source_language": "fr"},
            {"speaker": "human_agent", "speaker_source": "客服-谢艺", "source_language": "fr"},
            {"speaker": "player", "speaker_source": "用户", "source_language": "fr"},
        ]
        self.assertEqual(_human_agent_name(messages), "谢艺")

    def test_human_agent_name_dedupes_multiple_agents(self):
        messages = [
            {"speaker": "human_agent", "speaker_source": "客服-张三"},
            {"speaker": "human_agent", "speaker_source": "客服-李四"},
            {"speaker": "player", "speaker_source": "用户"},
        ]
        self.assertEqual(_human_agent_name(messages), "张三、李四")

    def test_human_agent_name_drops_system_and_strips_unicode_role_prefixes(self):
        messages = [
            {"speaker": "human_agent", "speaker_source": "system"},
            {"speaker": "human_agent", "speaker_source": "客服 － 客服‐谭彩灵"},
            {"speaker": "system", "speaker_source": "客服-不应出现"},
        ]
        self.assertEqual(_human_agent_name(messages), "谭彩灵")

    def test_human_agent_name_strips_vip_emoji_and_role_prefix(self):
        self.assertEqual(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "🤵客服-焦思阳"},
        ]), "焦思阳")
        self.assertEqual(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "💁客服 - 焦思阳"},
        ]), "焦思阳")

    def test_human_agent_name_strips_plain_hyphen_prefix(self):
        self.assertEqual(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "客服-谭彩灵"},
        ]), "谭彩灵")

    def test_human_agent_name_joins_system_label_with_prefixed_human_agent(self):
        messages = [
            {"speaker": "human_agent", "speaker_source": "system"},
            {"speaker": "human_agent", "speaker_source": "客服-谭彩灵"},
        ]
        self.assertEqual(_human_agent_name(messages), "谭彩灵")

    def test_human_agent_name_ignores_system_speaker_even_with_agent_name(self):
        messages = [
            {"speaker": "system", "speaker_source": "system", "agent_name": "system"},
            {"speaker": "auto_reply", "speaker_source": "auto_reply"},
        ]
        self.assertIsNone(_human_agent_name(messages))

    def test_human_agent_name_returns_none_for_role_only_values(self):
        messages = [
            {"speaker": "human_agent", "speaker_source": "auto_reply"},
            {"speaker": "human_agent", "speaker_source": "系统"},
            {"speaker": "system", "speaker_source": "客服-不应出现"},
        ]
        self.assertIsNone(_human_agent_name(messages))

    def test_human_agent_name_drops_discord_command_identity(self):
        self.assertIsNone(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "command"},
        ]))
        self.assertIsNone(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "客服-command"},
        ]))
        self.assertEqual(_human_agent_name([
            {"speaker": "human_agent", "speaker_source": "客服-command"},
            {"speaker": "human_agent", "speaker_source": "客服-谢艺"},
        ]), "谢艺")

    def test_slice_language_dedupes_in_order(self):
        messages = [
            {"source_language": "fr"},
            {"source_language": "fr"},
            {"source_language": "fr"},
        ]
        self.assertEqual(_slice_language(messages), "fr")

    def test_slice_language_drops_unknown_and_does_not_guess(self):
        from routers.reports import _slice_language as reports_slice_language
        messages = [
            {"source_language": "en"},
            {"source_language": "unknown"},
            {"source_language": "en"},
        ]
        self.assertEqual(_slice_language(messages), "en")
        self.assertEqual(reports_slice_language(messages), "en")
        self.assertIsNone(_slice_language([
            {"source_language": "unknown"},
            {"source_language": "Unknown"},
            {"source_language": ""},
            {"original_text": "Hello world"},
        ]))
        mixed = _slice_language([
            {"source_language": "en"},
            {"source_language": "fr"},
        ])
        self.assertEqual(mixed, "en, fr")

    def test_scope_prefill_keeps_empty_region(self):
        parent = SimpleNamespace(channel="官网客服", game="妖怪金手指", region="")
        scope = review_router._applicable_scope_for_form(parent, {})
        self.assertEqual(scope, {"channel": "官网客服", "game": "妖怪金手指", "region": ""})

    def test_scope_prefill_prefers_saved_human_region(self):
        parent = SimpleNamespace(channel="官网客服", game="妖怪金手指", region="")
        scope = review_router._applicable_scope_for_form(parent, {
            "channel": "官网客服",
            "game": "妖怪金手指",
            "region": "欧美",
        })
        self.assertEqual(scope["region"], "欧美")

    def test_analyst_workbench_lists_unassigned_items(self):
        analyst = SimpleNamespace(id=9, role="analyst")
        unassigned = None
        owned = SimpleNamespace(assignee_id=9, status="pending")
        other = SimpleNamespace(assignee_id=3, status="pending")
        self.assertTrue(_matches(unassigned, analyst, None, "all"))
        self.assertTrue(_matches(owned, analyst, None, "all"))
        self.assertTrue(_matches(other, analyst, None, "all"))
        self.assertFalse(_matches(unassigned, analyst, "mine", "all"))
        self.assertTrue(_matches(owned, analyst, "mine", "all"))
        self.assertFalse(_matches(other, analyst, "mine", "all"))

    def test_admin_workbench_still_supports_unassigned_scope(self):
        admin = SimpleNamespace(id=1, role="admin")
        self.assertTrue(_matches(None, admin, "unassigned", "all"))
        self.assertFalse(_matches(SimpleNamespace(assignee_id=9, status="pending"), admin, "unassigned", "all"))

    def test_kb_default_decisions_exclude_waste(self):
        self.assertEqual(set(_normalize_kb_decisions(None)), set())
        self.assertEqual(set(_kb_list_decisions([])), set(ACTIONABLE_KB))
        self.assertNotIn("not_candidate", _kb_list_decisions([]))
        self.assertNotIn("no_human_answer", _kb_list_decisions([]))

    def test_kb_not_candidate_filter_includes_reject(self):
        selected = _normalize_kb_decisions(["not_candidate"])
        self.assertEqual(selected, ["not_candidate"])
        self.assertIn("reject", _kb_list_decisions(selected))
        self.assertIn("not_candidate", _kb_list_decisions(selected))
        self.assertNotIn("no_human_answer", _kb_list_decisions(selected))

    def test_kb_comma_separated_and_invalid_decision(self):
        selected = _normalize_kb_decisions("candidate_ready,manual_review")
        self.assertEqual(selected, ["candidate_ready", "manual_review"])
        with self.assertRaises(HTTPException) as raised:
            _normalize_kb_decisions("unknown_decision")
        self.assertEqual(raised.exception.status_code, 400)

    def test_complete_assignment_is_idempotent(self):
        class FakeDb:
            def __init__(self):
                self.added = []
            def add(self, row):
                self.added.append(row)
        assignment = SimpleNamespace(id=9, status="in_progress", review_decision=None, review_comment=None, completed_at=None)
        db = FakeDb()
        user = SimpleNamespace(id=2, role="analyst")
        original = review_router.record_audit
        review_router.record_audit = lambda *args, **kwargs: None
        try:
            first = review_router._complete_assignment(db, assignment, user, "confirmed", "ok", "now")
            second = review_router._complete_assignment(db, assignment, user, "confirmed", "again", "later")
        finally:
            review_router.record_audit = original
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(assignment.status, "completed")
        self.assertEqual(assignment.review_decision, "confirmed")
        self.assertEqual(len(db.added), 1)

    def test_kb_entered_pool_requires_human_qa_and_actionable_decision(self):
        ready = SimpleNamespace(decision="candidate_ready", human_qa_pairs='[{"question":"q","answer":"a"}]', human_standard_questions="[]", human_standard_answer="")
        pending = SimpleNamespace(decision="candidate_pending_feedback", human_qa_pairs='[{"question":"q","answer":"a"}]', human_standard_questions="[]", human_standard_answer="")
        review = SimpleNamespace(decision="manual_review", human_qa_pairs='[{"question":"q","answer":"a"}]', human_standard_questions="[]", human_standard_answer="")
        empty = SimpleNamespace(decision="candidate_ready", human_qa_pairs=None, human_standard_questions="[]", human_standard_answer="")
        self.assertTrue(review_router._kb_entered_pool(ready))
        self.assertTrue(review_router._kb_entered_pool(pending))
        self.assertFalse(review_router._kb_entered_pool(review))
        self.assertFalse(review_router._kb_entered_pool(empty))

    def test_optimize_tag_is_required_for_issue_pool_path(self):
        item = SimpleNamespace(human_tags_json='["其他"]')
        self.assertNotIn(review_router.KNOWLEDGE_OPTIMIZE_TAG, review_router._tag_list(item, {}))
        self.assertIn(review_router.KNOWLEDGE_OPTIMIZE_TAG, review_router._tag_list(item, {"human_tags": ["知识库优化"]}))

    def test_pool_export_level1_uses_knowledge_category(self):
        source = SimpleNamespace(
            id=144, human_category="账号登录类", category="游戏玩法",
            answer_source="human_agent", human_knowledge_base="通用业务知识库",
            human_applicable_scope=None, applicable_scope=None, reason="",
            decision="candidate_ready",
        )
        entry = SimpleNamespace(id=1, pool_id="human_agent:144:0", qa_source="human_agent", qa_index=0,
                                question="新服什么时候开？", answer="开放时间不固定", processing_status="pending_entry",
                                created_by=None, created_at=None, updated_at=None)
        slice_row = SimpleNamespace(batch_id=3, slice_id="b3-f15-s2024", game="冒险大作战", region="欧美", channel="DC")
        assignment = SimpleNamespace(id=2, assignee_id=None, completed_at=None)
        class FakeDb:
            def query(self, *_args, **_kwargs):
                class Q:
                    def filter(self, *_a, **_k):
                        return self
                    def first(self):
                        return None
                return Q()
        row = serialize_pool(entry, source, slice_row, assignment, FakeDb(), False)
        self.assertEqual(row["category"], "账号登录类")
        self.assertNotEqual(row["category"], "知识库")

    def test_session_time_uses_first_message_then_reply_time(self):
        messages = [
            {"created_at": ""},
            {"created_at": "2026-09-08 11:56:26"},
            {"created_at": "2026-09-08 12:00:00"},
        ]
        self.assertEqual(_session_time(messages), "2026-09-08 11:56:26")
        conversation = SimpleNamespace(reply_time="2026-09-07 09:43:45", started_at=None)
        self.assertEqual(_session_time([], conversation), "2026-09-07 09:43:45")
        self.assertIsNone(_session_time([], SimpleNamespace(reply_time=None, started_at=None)))

    def test_last_modified_at_takes_max_or_none(self):
        item = SimpleNamespace(human_updated_at="2026-09-17 18:00:00")
        assignment = SimpleNamespace(assigned_at="2026-09-17 17:00:00", claimed_at=None, started_at=None, completed_at="2026-09-17 19:00:00")
        self.assertEqual(_last_modified_at(item, assignment), "2026-09-17 19:00:00")
        self.assertEqual(_last_modified_at(item, None), "2026-09-17 18:00:00")
        self.assertIsNone(_last_modified_at(SimpleNamespace(human_updated_at=None), None))

    def test_mapping_batch_enable_skips_pending_and_already_enabled(self):
        pending = SimpleNamespace(id=1, enabled=False, remark="[待补充映射] 标准渠道", match_field="none", raw_channel="DC", game="冒险大作战")
        enabled = SimpleNamespace(id=2, enabled=True, remark="", match_field="none", raw_channel="DC", game="冒险大作战")
        disabled = SimpleNamespace(id=3, enabled=False, remark="", match_field="none", raw_channel="DC", game="冒险大作战")
        illegal = SimpleNamespace(id=4, enabled=False, remark="", match_field="gameProductId", raw_channel="官网客服", game="1741860167481")
        self.assertEqual(admin_router._apply_mapping_enabled(pending, True)[0], "skipped")
        self.assertEqual(admin_router._apply_mapping_enabled(enabled, True)[0], "skipped")
        self.assertEqual(admin_router._apply_mapping_enabled(disabled, True)[0], "enabled")
        self.assertTrue(disabled.enabled)
        self.assertEqual(admin_router._apply_mapping_enabled(illegal, True)[0], "failed")
        self.assertEqual(admin_router._apply_mapping_enabled(SimpleNamespace(id=5, enabled=False, remark=""), False)[0], "skipped")

    def test_admin_unassigned_cannot_edit(self):
        # Mirrors Report.tsx canEdit: lock + owner required, including admin.
        def can_edit(row, user_id):
            writable = row.get("assignment_status") in {"pending", "in_progress", "returned"}
            return bool(row.get("assignment_id")) and writable and row.get("assignee_id") == user_id
        self.assertFalse(can_edit({"assignment_status": "unassigned", "assignee_id": None}, 1))
        self.assertTrue(can_edit({"assignment_id": 9, "assignment_status": "in_progress", "assignee_id": 1}, 1))
        self.assertFalse(can_edit({"assignment_id": 9, "assignment_status": "in_progress", "assignee_id": 2}, 1))

    def test_save_without_region_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            review_router._require_scope({"channel": "官网客服", "game": "妖怪金手指", "region": ""})
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("地区", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
