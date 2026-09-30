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


def payload_without_human():
    data = payload()
    data["messages"] = [item for item in data["messages"] if item["speaker"] != "human_agent"]
    return data


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



def knowledge_skip():
    return {
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
    }


def analyzed_result(handoff_data, source=None):
    src = source or payload()
    return {
        "schema_version": "2.0.0",
        "slice_id": "slice-001",
        "analysis_status": "completed",
        "messages": [translation(item["message_id"]) for item in src["messages"]],
        "quality_check": {"has_issue": False, "issues": []},
        "human_handoff": handoff_data,
        "knowledge_suggestion": knowledge_skip(),
        "term_suggestions": {"has_terms": False, "terms": []},
        "warnings": [],
        "errors": [],
    }


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

    def test_attachment_required_before_handoff(self):
        no_human = payload_without_human()
        result = validate_slice_result(analyzed_result(handoff(
            decision="handoff_required",
            handoff_occurred=False,
            reason_type="玩家消息包含附件",
            evidence_message_ids=["m001"],
            reason="玩家发送了截图。",
        ), no_human), no_human)
        self.assertEqual(result["human_handoff"]["reason_type"], "玩家消息包含附件")
        self.assertFalse(result["human_handoff"]["handoff_occurred"])

    def test_sticker_reasonable_after_handoff(self):
        result = validate_slice_result(analyzed_result(handoff(
            reason_type="玩家发送表情贴纸",
            evidence_message_ids=["m001", "m010"],
            reason="玩家发送了表情贴纸后接入人工。",
        )), payload())
        self.assertEqual(result["human_handoff"]["reason_type"], "玩家发送表情贴纸")
        self.assertTrue(result["human_handoff"]["handoff_occurred"])

    def test_attachment_rejects_not_required(self):
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(analyzed_result(handoff(
                decision="handoff_not_required",
                handoff_occurred=False,
                reason_type="玩家消息包含附件",
                evidence_message_ids=["m001"],
            )), payload())

    def test_sticker_rejects_unreasonable(self):
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(analyzed_result(handoff(
                decision="handoff_unreasonable",
                handoff_occurred=True,
                reason_type="玩家发送表情贴纸",
                evidence_message_ids=["m001"],
            )), payload())

    def test_attachment_requires_player_evidence(self):
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(analyzed_result(handoff(
                reason_type="玩家消息包含附件",
                evidence_message_ids=["m010"],
            )), payload())

    def test_unknown_reason_type_still_rejected(self):
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(analyzed_result(handoff(
                reason_type="玩家消息涉及附件",
                evidence_message_ids=["m001"],
            )), payload())



    def test_player_and_human_cannot_be_handoff_required(self):
        with self.assertRaises(AnalysisProtocolError) as raised:
            validate_slice_result(analyzed_result(handoff(
                decision="handoff_required",
                handoff_occurred=False,
                reason_type="玩家明确要求人工",
                evidence_message_ids=["m001"],
                reason="应转未转。",
            )), payload())
        self.assertIn("应转未转", str(raised.exception))

    def test_player_and_ai_can_be_handoff_required(self):
        no_human = payload_without_human()
        result = validate_slice_result(analyzed_result(handoff(
            decision="handoff_required",
            handoff_occurred=False,
            reason_type="玩家明确要求人工",
            evidence_message_ids=["m001"],
            reason="玩家要求人工但尚未转接。",
        ), no_human), no_human)
        self.assertEqual(result["human_handoff"]["decision"], "handoff_required")
        self.assertFalse(result["human_handoff"]["handoff_occurred"])

    def test_human_only_cannot_mark_handoff_occurred(self):

        human_only = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "DC",
            "game": "冒险大作战",
            "region": "欧美",
            "messages": [
                {"message_id": "m010", "speaker": "human_agent", "text": "LUNAR MECHA PALACE", "sequence": 1},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m010")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(evidence_message_ids=["m010"]),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        with self.assertRaises(AnalysisProtocolError) as raised:
            validate_slice_result(result, human_only)
        self.assertIn("不得判定已发生转人工", str(raised.exception))

    def test_human_without_transfer_form_cannot_infer_occurred(self):
        no_form = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "DC",
            "game": "冒险大作战",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "How do I claim?", "sequence": 1},
                {"message_id": "m010", "speaker": "human_agent", "text": "Tap Claim in the event.", "sequence": 2},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m010")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(evidence_message_ids=["m010"]),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(result, no_form)

    def test_ai_assistant_greeting_system_cannot_mark_occurred(self):
        greeting = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "VK",
            "game": "热血神剑",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "hello", "sequence": 1},
                {"message_id": "m002", "speaker": "system", "text": "您好，我是 AI 助手，帮助回答您的问题~", "sequence": 2},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(evidence_message_ids=["m002"], reason="系统已出现。"),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        with self.assertRaises(AnalysisProtocolError):
            validate_slice_result(result, greeting)

    def test_offline_registration_queue_allows_occurred_without_human(self):

        queued = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "VK",
            "game": "热血神剑",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "account stolen", "sequence": 1},
                {"message_id": "m002", "speaker": "ai", "text": "please wait", "sequence": 2},
                {"message_id": "m005", "speaker": "system", "text": "Operator is currently offline. I have registered your issue.", "sequence": 3},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002"), translation("m005")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(
                evidence_message_ids=["m001", "m005"],
                reason="系统已登记并排队等待人工上线。",
            ),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        validated = validate_slice_result(result, queued)
        self.assertTrue(validated["human_handoff"]["handoff_occurred"])

    def test_system_transfer_then_human_allows_occurred_without_ai(self):

        line_payload = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "LINE",
            "game": "主宰世界",
            "region": "日本",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "錬丹達人", "sequence": 1},
                {"message_id": "m002", "speaker": "system", "text": "担当者におつなぎしました。しばらくお待ちください", "sequence": 2},
                {"message_id": "m010", "speaker": "human_agent", "text": "こんにちは。", "sequence": 3},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m002"), translation("m010")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(evidence_message_ids=["m002", "m010"], reason="系统已转接且人工已回复。"),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        validated = validate_slice_result(result, line_payload)
        self.assertTrue(validated["human_handoff"]["handoff_occurred"])

    def test_transfer_form_submitted_allows_occurred_without_ai(self):
        form_payload = {
            "schema_version": "1.0.0",
            "slice_id": "slice-001",
            "channel": "官网客服",
            "game": "冒险大作战",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "login failed", "sequence": 1},
                {
                    "message_id": "m003",
                    "speaker": "system",
                    "text": "转人工表单-已提交并生成工单",
                    "content_type": "3",
                    "event_type": "transfer_form_submitted",
                    "sequence": 2,
                },
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-001",
            "analysis_status": "completed",
            "messages": [translation("m001"), translation("m003")],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": handoff(evidence_message_ids=["m003"], reason="端内转人工表单已提交。"),
            "knowledge_suggestion": knowledge_skip(),
            "term_suggestions": {"has_terms": False, "terms": []},
            "warnings": [],
            "errors": [],
        }
        validated = validate_slice_result(result, form_payload)
        self.assertTrue(validated["human_handoff"]["handoff_occurred"])


if __name__ == "__main__":
    unittest.main()
