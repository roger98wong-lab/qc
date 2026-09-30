import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from services.analyzer import AnalysisProtocolError, analyze_slice, validate_slice_result


class AnalyzerPromptTest(unittest.TestCase):
    def test_user_prompt_starts_with_searchable_slice_id_title(self):
        payload = {
            "schema_version": "1.0.0",
            "slice_id": "mbackend:2:718332",
            "channel": "M后台",
            "game": "妖怪金手指",
            "region": None,
            "messages": [{
                "message_id": "msg-1",
                "speaker": "player",
                "content": "hello",
            }],
        }
        response = {
            "slice_id": payload["slice_id"],
            "analysis_status": "completed",
        }
        completion = SimpleNamespace(
            content=json.dumps(response, ensure_ascii=False),
            response_metadata={},
        )

        with patch("services.analyzer.chat_completion", new=AsyncMock(return_value=completion)) as mocked_chat, \
                patch("services.analyzer.validate_slice_result", return_value=response) as mocked_validate:
            asyncio.run(analyze_slice(payload))

        messages = mocked_chat.await_args.args[0]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        title, body = messages[0]["content"].split("\n\n", 1)
        self.assertEqual(title, f"slice_id={payload['slice_id']}")
        self.assertTrue(body.startswith("{"))
        self.assertEqual(json.loads(body), payload)
        self.assertNotIn("title", json.loads(body))
        self.assertEqual(mocked_validate.call_count, 1)
        validated_result, validated_payload = mocked_validate.call_args.args
        self.assertEqual(validated_result["slice_id"], payload["slice_id"])
        self.assertEqual(validated_result["analysis_status"], "completed")
        self.assertIs(validated_payload, payload)

    def test_missing_slice_id_remains_invalid_slice_input(self):
        payload = {
            "schema_version": "1.0.0",
            "messages": [{"message_id": "msg-1", "speaker": "player", "content": "hello"}],
        }

        with self.assertRaises(AnalysisProtocolError) as raised:
            asyncio.run(analyze_slice(payload))

        self.assertEqual(raised.exception.code, "invalid_slice_input")

    def test_term_suggestions_accept_simple_text_zh_cn(self):
        payload = {
            "schema_version": "1.0.0",
            "slice_id": "slice-kb-ready",
            "channel": "DC",
            "game": "冒险大作战",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "How do I claim?", "sequence": 1},
                {"message_id": "m002", "speaker": "human_agent", "text": "Open Event Board.", "sequence": 2},
                {"message_id": "m003", "speaker": "player", "text": "Thanks, I got it.", "sequence": 3},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-kb-ready",
            "analysis_status": "completed",
            "messages": [
                {"message_id": "m001", "source_language": "en", "translation": {"target_language": "zh-CN", "translated_text": "我如何领取？", "status": "success", "translation_version": "v1"}},
                {"message_id": "m002", "source_language": "en", "translation": {"target_language": "zh-CN", "translated_text": "打开活动面板。", "status": "success", "translation_version": "v1"}},
                {"message_id": "m003", "source_language": "en", "translation": {"target_language": "zh-CN", "translated_text": "谢谢，我拿到了。", "status": "success", "translation_version": "v1"}},
            ],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": {
                "decision": "handoff_not_required",
                "handoff_occurred": False,
                "reason_type": "可由AI继续处理",
                "confidence": 0.8,
                "evidence_message_ids": ["m001", "m002"],
                "reason": "纯人工回复，无转接动作。",
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "knowledge_suggestion": {
                "answer_source": "human_agent",
                "decision": "candidate_ready",
                "is_candidate": True,
                "validation_status": "player_validated",
                "confidence": 0.9,
                "category": "引导流程",
                "question_message_ids": ["m001"],
                "answer_message_ids": ["m002"],
                "feedback_message_ids": ["m003"],
                "evidence_message_ids": ["m001", "m002", "m003"],
                "title": "活动奖励领取方式",
                "standard_questions": ["如何领取活动奖励？"],
                "standard_answer": "打开活动页面并点击领取。",
                "applicable_scope": {"channel": "DC", "game": "冒险大作战", "region": "欧美"},
                "keywords": ["领取"],
                "reason": "人工客服给出通用步骤，玩家已确认。",
                "reject_reason": None,
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "term_suggestions": {
                "has_terms": True,
                "terms": [{"text": "Event Board", "zh_cn": "活动面板"}],
            },
            "warnings": [],
            "errors": [],
        }
        validated = validate_slice_result(result, payload)
        self.assertEqual(validated["term_suggestions"], {"has_terms": True, "terms": [{"text": "Event Board", "zh_cn": "活动面板"}]})

    def test_term_suggestions_compat_old_fields(self):
        payload = {
            "schema_version": "1.0.0",
            "slice_id": "slice-kb-ready",
            "channel": "DC",
            "game": "冒险大作战",
            "region": "欧美",
            "messages": [
                {"message_id": "m001", "speaker": "player", "text": "q", "sequence": 1},
                {"message_id": "m002", "speaker": "human_agent", "text": "a", "sequence": 2},
                {"message_id": "m003", "speaker": "player", "text": "thanks", "sequence": 3},
            ],
        }
        result = {
            "schema_version": "2.0.0",
            "slice_id": "slice-kb-ready",
            "analysis_status": "completed",
            "messages": [
                {"message_id": mid, "source_language": "en", "translation": {"target_language": "zh-CN", "translated_text": "x", "status": "success", "translation_version": "v1"}}
                for mid in ("m001", "m002", "m003")
            ],
            "quality_check": {"has_issue": False, "issues": []},
            "human_handoff": {
                "decision": "handoff_not_required",
                "handoff_occurred": False,
                "reason_type": "可由AI继续处理",
                "confidence": 0.8,
                "evidence_message_ids": ["m002"],
                "reason": "纯人工回复，无转接动作。",
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "knowledge_suggestion": {
                "answer_source": "human_agent",
                "decision": "candidate_ready",
                "is_candidate": True,
                "validation_status": "player_validated",
                "confidence": 0.9,
                "category": "引导流程",
                "question_message_ids": ["m001"],
                "answer_message_ids": ["m002"],
                "feedback_message_ids": ["m003"],
                "evidence_message_ids": ["m001", "m002", "m003"],
                "title": "t",
                "standard_questions": ["q"],
                "standard_answer": "a",
                "applicable_scope": {"channel": "DC", "game": "冒险大作战", "region": "欧美"},
                "keywords": [],
                "reason": "ok",
                "reject_reason": None,
                "needs_manual_review": False,
                "manual_review_reason": None,
            },
            "term_suggestions": {
                "has_terms": True,
                "terms": [{
                    "term_id": "term_001",
                    "suggested_standard_term": "Season Pass",
                    "zh_cn_meaning": "赛季通行证",
                    "term_type": "功能名称",
                    "observed_forms": [{"text": "Season Pass", "source_language": "en", "form_type": "standard_candidate", "message_ids": ["m002"]}],
                    "evidence_message_ids": ["m002"],
                    "correction_note": None,
                    "confidence": 0.8,
                    "needs_manual_review": False,
                    "manual_review_reason": None,
                }],
            },
            "warnings": [],
            "errors": [],
        }
        validated = validate_slice_result(result, payload)
        self.assertEqual(validated["term_suggestions"]["terms"], [{"text": "Season Pass", "zh_cn": "赛季通行证"}])


if __name__ == "__main__":
    unittest.main()
