import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

sys.modules.setdefault("services", types.ModuleType("services"))
maas = types.ModuleType("services.maas_client")
maas.MaaSClientError = Exception
maas.chat_completion = None
sys.modules["services.maas_client"] = maas
sys.modules.setdefault("config", types.ModuleType("config"))

spec = importlib.util.spec_from_file_location("analyzer_mod", BACKEND / "services" / "analyzer.py")
analyzer_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer_mod)
AnalysisProtocolError = analyzer_mod.AnalysisProtocolError
validate_slice_result = analyzer_mod.validate_slice_result


def payload():
    return {
        "schema_version": "1.0.0",
        "slice_id": "slice-001",
        "channel": "DC",
        "game": "冒险大作战",
        "region": "欧美",
        "messages": [
            {"message_id": "m001", "speaker": "player", "text": "How do I claim?", "sequence": 1},
            {"message_id": "m002", "speaker": "ai", "text": "Open the event.", "sequence": 2},
            {"message_id": "m010", "speaker": "human_agent", "text": "Tap Claim in the event.", "sequence": 3},
            {"message_id": "m011", "speaker": "player", "text": "Thanks, got it.", "sequence": 4},
        ],
    }


def translation(message_id, text="ok"):
    return {
        "message_id": message_id,
        "source_language": "en",
        "translation": {
            "target_language": "zh-CN",
            "translated_text": text,
            "status": "success",
            "translation_version": "v1",
        },
    }


def handoff(**overrides):
    data = {
        "decision": "handoff_reasonable",
        "handoff_occurred": True,
        "reason_type": "需要人工权限或后台处理",
        "confidence": 0.8,
        "evidence_message_ids": ["m010"],
        "reason": "已接入人工客服处理领取问题。",
        "needs_manual_review": False,
        "manual_review_reason": None,
    }
    data.update(overrides)
    return data


class ValidateSliceResultTest(unittest.TestCase):
    def test_quality_issue_without_knowledge_candidate(self):
        result = validate_slice_result({
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002"), translation("m010"), translation("m011")],
            "quality_check": {
                "has_issue": True,
                "issues": [{
                    "issue_id": "issue_001",
                    "issue_type": "答非所问",
                    "severity": "中级",
                    "confidence": 0.9,
                    "ai_message_ids": ["m002"],
                    "evidence_message_ids": ["m001", "m002"],
                    "player_question": {"original": "How do I claim?", "zh_cn": "我如何领取？"},
                    "ai_answer": {"original": "Open the event.", "zh_cn": "打开活动。"},
                    "reason": "未说明领取步骤",
                    "suggestion": "补充领取路径",
                    "revised_reply": None,
                    "revised_reply_zh_cn": None,
                    "needs_manual_review": False,
                    "manual_review_reason": None,
                }],
            },
            "human_handoff": handoff(),
            "knowledge_suggestion": {
                "answer_source": "human_agent",
                "decision": "not_candidate",
                "is_candidate": False,
                "validation_status": "not_applicable",
                "confidence": 0.7,
                "category": None,
                "question_message_ids": [],
                "answer_message_ids": [],
                "feedback_message_ids": [],
                "evidence_message_ids": [],
                "title": None,
                "standard_questions": [],
                "standard_answer": None,
                "applicable_scope": {"channel": "DC", "game": "冒险大作战", "region": "欧美"},
                "keywords": [],
                "reason": "答案依赖当前活动入口，不适合沉淀。",
                "reject_reason": "时效性活动入口",
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }, payload())
        self.assertTrue(result["quality_check"]["has_issue"])
        self.assertEqual(result["knowledge_suggestion"]["decision"], "not_candidate")
        self.assertFalse(result["term_suggestions"]["has_terms"])

    def test_no_quality_issue_with_candidate_ready(self):
        result = validate_slice_result({
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002"), translation("m010"), translation("m011")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(),
            "knowledge_suggestion": {
                "answer_source": "human_agent",
                "decision": "candidate_ready",
                "is_candidate": True,
                "validation_status": "player_validated",
                "confidence": 0.9,
                "category": "游戏玩法",
                "question_message_ids": ["m001"],
                "answer_message_ids": ["m010"],
                "feedback_message_ids": ["m011"],
                "evidence_message_ids": ["m001", "m010", "m011"],
                "title": "活动奖励领取",
                "standard_questions": ["如何领取活动奖励？"],
                "standard_answer": "打开活动页面点击领取。",
                "applicable_scope": {"channel": "DC", "game": "冒险大作战", "region": "欧美"},
                "keywords": ["领取"],
                "reason": "玩家明确认可领取步骤。",
                "reject_reason": None,
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "term_suggestions": {
                "has_terms": True,
                "terms": [{
                    "term_id": "term_001",
                    "suggested_standard_term": "Claim",
                    "zh_cn_meaning": "领取",
                    "term_type": "操作术语",
                    "observed_forms": [{
                        "text": "Claim",
                        "source_language": "en",
                        "form_type": "standard_candidate",
                        "message_ids": ["m010"],
                    }],
                    "evidence_message_ids": ["m010"],
                    "correction_note": None,
                    "confidence": 0.8,
                    "needs_manual_review": False,
                    "manual_review_reason": None,
                }],
            },
            "warnings": [],
            "errors": [],
        }, payload())
        self.assertFalse(result["quality_check"]["has_issue"])
        self.assertTrue(result["knowledge_suggestion"]["is_candidate"])
        self.assertTrue(result["term_suggestions"]["has_terms"])

    def test_pending_feedback_candidate(self):
        result = validate_slice_result({
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002"), translation("m010"), translation("m011")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(),
            "knowledge_suggestion": {
                "answer_source": "human_agent",
                "decision": "candidate_pending_feedback",
                "is_candidate": True,
                "validation_status": "unvalidated",
                "confidence": 0.7,
                "category": "引导流程",
                "question_message_ids": ["m001"],
                "answer_message_ids": ["m010"],
                "feedback_message_ids": [],
                "evidence_message_ids": ["m001", "m010"],
                "title": "活动领取入口",
                "standard_questions": ["奖励在哪里领？"],
                "standard_answer": "打开活动页面点击领取。",
                "applicable_scope": {"channel": "DC", "game": "冒险大作战", "region": "欧美"},
                "keywords": [],
                "reason": "人工答案完整，但没有玩家正向反馈。",
                "reject_reason": None,
                "needs_manual_review": True,
                "manual_review_reason": "尚无明确玩家正向反馈",
            },
            "warnings": [],
            "errors": [],
        }, payload())
        self.assertEqual(result["knowledge_suggestion"]["decision"], "candidate_pending_feedback")
        self.assertEqual(result["knowledge_suggestion"]["validation_status"], "unvalidated")
        self.assertFalse(result["term_suggestions"]["has_terms"])

    def test_missing_handoff_is_rejected(self):
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result({
                "slice_id": "slice-001",
                "analysis_status": "completed",
                "messages": [translation("m001"), translation("m002"), translation("m010"), translation("m011")],
                "quality_check": {"has_issue": False, "issues": []},
                "knowledge_suggestion": {
                    "answer_source": "human_agent",
                    "decision": "no_human_answer",
                    "is_candidate": False,
                    "validation_status": "not_applicable",
                    "confidence": 1,
                    "category": None,
                    "question_message_ids": [],
                    "answer_message_ids": [],
                    "feedback_message_ids": [],
                    "evidence_message_ids": [],
                    "title": None,
                    "standard_questions": [],
                    "standard_answer": None,
                    "reason": "无人工回复",
                    "reject_reason": None,
                    "needs_manual_review": False,
                    "manual_review_reason": None,
                },
            }, payload())


if __name__ == "__main__":
    unittest.main()
