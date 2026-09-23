import os, sys, unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from services.excel_parser import (
    _filter_standard_messages, _message_speaker, _parse_standard,
    extract_human_kb_suggestions, parse_standard_dialogue,
)


def _line(role, body, ts="2026-09-17 12:00:00"):
    return f"[{role}] [{ts}]: {body}"


def _frame(*dialogues):
    return pd.DataFrame({"对话记录": list(dialogues)})


class StandardSliceFilterTest(unittest.TestCase):
    def _speakers(self, dialogue):
        msgs = _filter_standard_messages(parse_standard_dialogue(dialogue))
        return [_message_speaker(item) for item in msgs], msgs

    def test_blank_or_unparsed_is_dropped(self):
        sessions, _, _ = _parse_standard(_frame("", "   "), "DC")
        self.assertEqual(sessions, [])

    def test_system_only_is_dropped(self):
        sessions, _, _ = _parse_standard(_frame(_line("system", "内部提示")), "VK")
        self.assertEqual(sessions, [])

    def test_player_only_is_dropped(self):
        sessions, _, _ = _parse_standard(_frame(_line("用户", "你好")), "FB")
        self.assertEqual(sessions, [])

    def test_unknown_only_is_dropped(self):
        sessions, _, _ = _parse_standard(_frame(_line("旁白", "舞台灯光")), "LINE")
        self.assertEqual(sessions, [])

    def test_player_and_ai_kept(self):
        dialogue = "\n".join([_line("用户", "怎么玩"), _line("AI客服", "点这里")])
        sessions, _, _ = _parse_standard(_frame(dialogue), "DC")
        self.assertEqual(len(sessions), 1)
        speakers = [item["speaker"] for item in sessions[0]["slice_payload"]["messages"]]
        self.assertEqual(speakers, ["player", "ai"])

    def test_player_and_human_agent_kept(self):
        dialogue = "\n".join([_line("用户", "怎么玩"), _line("客服-张三", "我来帮你")])
        sessions, _, _ = _parse_standard(_frame(dialogue), "VK")
        self.assertEqual(len(sessions), 1)
        speakers = [item["speaker"] for item in sessions[0]["slice_payload"]["messages"]]
        self.assertEqual(speakers, ["player", "human_agent"])

    def test_player_and_auto_reply_kept(self):
        dialogue = "\n".join([_line("用户", "在吗"), _line("auto_reply", "请稍等")])
        sessions, _, _ = _parse_standard(_frame(dialogue), "FB")
        self.assertEqual(len(sessions), 1)
        messages = sessions[0]["slice_payload"]["messages"]
        self.assertEqual([item["speaker"] for item in messages], ["player", "system"])
        raw = _filter_standard_messages(parse_standard_dialogue(dialogue))
        self.assertTrue(raw[1]["is_auto"])

    def test_non_auto_system_dropped_but_slice_kept(self):
        dialogue = "\n".join([
            _line("用户", "怎么玩"),
            _line("system", "内部提示"),
            _line("客服-李四", "看攻略"),
        ])
        sessions, _, _ = _parse_standard(_frame(dialogue), "LINE")
        self.assertEqual(len(sessions), 1)
        speakers = [item["speaker"] for item in sessions[0]["slice_payload"]["messages"]]
        self.assertEqual(speakers, ["player", "human_agent"])

    def test_empty_line_between_user_and_agent_still_ingested(self):
        dialogue = _line("用户", "你好") + "\n\n" + _line("客服-张三", "在的")
        sessions, _, _ = _parse_standard(_frame(dialogue), "DC")
        self.assertEqual(len(sessions), 1)
        speakers = [item["speaker"] for item in sessions[0]["slice_payload"]["messages"]]
        self.assertEqual(speakers, ["player", "human_agent"])

    def test_uce_push_is_system_not_human_agent(self):
        self.assertEqual(_message_speaker({"role": "客服-uce_push"}), "system")
        self.assertEqual(_message_speaker({"role": " 客服 - UCE_PUSH "}), "system")
        self.assertEqual(_message_speaker({"role": "客服－uce_push"}), "system")
        self.assertEqual(_message_speaker({"role": "客服‐uce_push"}), "system")
        self.assertEqual(_message_speaker({"role": "客服-张三"}), "human_agent")
        self.assertEqual(_message_speaker({"role": "客服 - 李四"}), "human_agent")
        self.assertEqual(_message_speaker({"role": "🤵客服-焦思阳"}), "human_agent")

    def test_player_and_uce_push_only_is_dropped(self):
        dialogue = "\n".join([_line("用户", "你好"), _line("客服-uce_push", "活动推送")])
        sessions, suggestions, _ = _parse_standard(_frame(dialogue), "DC")
        self.assertEqual(sessions, [])
        self.assertEqual(suggestions, [])

    def test_uce_push_variants_are_dropped_but_real_agent_kept(self):
        dialogue = "\n".join([
            _line("用户", "怎么玩"),
            _line("客服 - uce_push", "系统推送"),
            _line("客服-UCE_PUSH", "另一条推送"),
            _line("客服-张三", "我来帮你处理"),
        ])
        sessions, suggestions, _ = _parse_standard(_frame(dialogue), "VK")
        self.assertEqual(len(sessions), 1)
        messages = sessions[0]["slice_payload"]["messages"]
        self.assertEqual([item["speaker"] for item in messages], ["player", "human_agent"])
        self.assertEqual(messages[1]["speaker_source"], "客服-张三")
        self.assertFalse(any("uce_push" in str(item.get("speaker_source") or "").lower() for item in messages))
        self.assertEqual(suggestions, [])

    def test_filtered_uce_push_does_not_create_kb_suggestion(self):
        dialogue = "\n".join([
            _line("用户", "怎么找回账号"),
            _line("客服-uce_push", "感谢你的咨询，活动详情请查看公告。"),
            _line("用户", "谢谢"),
        ])
        msgs = _filter_standard_messages(parse_standard_dialogue(dialogue))
        self.assertEqual(msgs, [])
        self.assertEqual(extract_human_kb_suggestions(msgs, "冒险大作战", "欧美", "DC"), [])


class IssuePairDefaultTest(unittest.TestCase):
    def test_issue_pair_defaults_use_revised_zh_and_keep_saved(self):
        # Mirror ReviewProcessingPanel.issuePairs without importing TS.
        def issue_pairs(row, fallback_question="", fallback_answer=""):
            pairs = row.get("human_qa_pairs")
            if isinstance(pairs, list) and pairs:
                return [
                    {"question": str(item.get("question") or ""), "answer": str(item.get("answer") or "")}
                    for item in pairs if isinstance(item, dict)
                ]
            questions = row.get("human_standard_questions") if isinstance(row.get("human_standard_questions"), list) else []
            if questions:
                return [{"question": str(item or ""), "answer": str(row.get("human_standard_answer") or "")} for item in questions]
            return [{"question": fallback_question, "answer": fallback_answer}]

        empty = issue_pairs({}, "玩家问题中文", "参考回复中文")
        self.assertEqual(empty, [{"question": "玩家问题中文", "answer": "参考回复中文"}])
        saved = issue_pairs({"human_qa_pairs": [{"question": "人工Q", "answer": "人工A"}]}, "玩家问题中文", "参考回复中文")
        self.assertEqual(saved, [{"question": "人工Q", "answer": "人工A"}])
        saved_legacy = issue_pairs({"human_standard_questions": ["旧问题"], "human_standard_answer": "旧答案"}, "玩家问题中文", "参考回复中文")
        self.assertEqual(saved_legacy, [{"question": "旧问题", "answer": "旧答案"}])


if __name__ == "__main__":
    unittest.main()
