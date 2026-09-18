import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from services.excel_parser import (  # noqa: E402
    _parse_overseas,
    normalize_content_type,
    parse_excel_to_sessions,
    structured_content_to_readable_text,
)


def _row(**overrides):
    base = {
        "sessionId": "sess-1",
        "senderType": "2",
        "createdAt": "2026-09-16 10:00:00",
        "contentType": 0,
        "content": json.dumps({"text": "hello"}, ensure_ascii=False),
        "gameProductId": "game-a",
        "_id": "msg-1",
    }
    base.update(overrides)
    return base


def _parse_rows(rows):
    return _parse_overseas(pd.DataFrame(rows))


class NormalizeContentTypeTest(unittest.TestCase):
    def test_known_aliases(self):
        for value, expected in [
            (1, "1"),
            (1.0, "1"),
            ("1", "1"),
            ("1.0", "1"),
            (3, "3"),
            (3.0, "3"),
            ("3", "3"),
            ("3.0", "3"),
            (0, "0"),
            ("0.0", "0"),
            (2, "2"),
            ("2.0", "2"),
            (" 1.0 ", "1"),
        ]:
            self.assertEqual(normalize_content_type(value), expected)

    def test_empty_and_unknown_are_preserved(self):
        self.assertEqual(normalize_content_type(None), "")
        self.assertEqual(normalize_content_type(""), "")
        self.assertEqual(normalize_content_type("  "), "")
        self.assertEqual(normalize_content_type("9"), "9")
        self.assertEqual(normalize_content_type("abc"), "abc")


class TransferFormParseTest(unittest.TestCase):
    def test_content_type_1_text_json(self):
        sessions, _, error = _parse_rows([_row(
            contentType=1,
            content=json.dumps({"text": "请填写角色名"}, ensure_ascii=False),
            senderType="2",
            _id="form-1",
        )])
        self.assertIsNone(error)
        self.assertEqual(len(sessions), 1)
        message = sessions[0]["slice_payload"]["messages"][0]
        self.assertEqual(message["content_type"], "1")
        self.assertEqual(message["event_type"], "transfer_form_started")
        self.assertEqual(message["event_label"], "转人工表单-引导填写")
        self.assertIn("[转人工表单-引导填写]", message["text"])
        self.assertNotEqual(message["text"].strip(), message["raw_content"])
        self.assertEqual(message["raw_content"], '{"text": "请填写角色名"}')
        self.assertEqual(message["structured_content"], {"text": "请填写角色名"})
        self.assertEqual(sessions[0]["_parse_stats"]["valid_messages"], 1)
        self.assertFalse(sessions[0]["_parse_stats"].get("parse_failed"))

    def test_content_type_3_ticket_json(self):
        payload = {"inquiries": "账号无法登录", "ticketId": "123"}
        sessions, _, error = _parse_rows([_row(
            contentType="3.0",
            content=json.dumps(payload, ensure_ascii=False),
            senderType="2",
            _id="form-3",
        )])
        self.assertIsNone(error)
        message = sessions[0]["slice_payload"]["messages"][0]
        self.assertEqual(message["content_type"], "3")
        self.assertEqual(message["event_type"], "transfer_form_submitted")
        self.assertIn("[转人工表单-已提交并生成工单]", message["text"])
        self.assertIn("inquiries：账号无法登录", message["text"])
        self.assertIn("ticketId：123", message["text"])
        self.assertNotIn(json.dumps(payload, ensure_ascii=False), message["text"])
        self.assertEqual(message["raw_content"], json.dumps(payload, ensure_ascii=False))
        self.assertEqual(message["structured_content"], payload)

    def test_numeric_and_string_aliases_are_recognized(self):
        rows = [
            _row(sessionId="s1", contentType=1.0, content='{"text":"a"}', _id="a", createdAt="2026-09-16 10:00:01"),
            _row(sessionId="s1", contentType="1.0", content='{"text":"b"}', _id="b", createdAt="2026-09-16 10:00:02"),
            _row(sessionId="s1", contentType=3.0, content='{"ticketId":"1"}', _id="c", createdAt="2026-09-16 10:00:03"),
            _row(sessionId="s1", contentType="3.0", content='{"ticketId":"2"}', _id="d", createdAt="2026-09-16 10:00:04"),
        ]
        sessions, _, error = _parse_rows(rows)
        self.assertIsNone(error)
        types = [message["content_type"] for message in sessions[0]["slice_payload"]["messages"]]
        events = [message["event_type"] for message in sessions[0]["slice_payload"]["messages"]]
        self.assertEqual(types, ["1", "1", "3", "3"])
        self.assertEqual(events, [
            "transfer_form_started", "transfer_form_started",
            "transfer_form_submitted", "transfer_form_submitted",
        ])

    def test_mixed_session_keeps_order_and_normal_messages(self):
        rows = [
            _row(contentType=0, content='{"text":"玩家问题"}', senderType="1", _id="m1", createdAt="2026-09-16 10:00:01"),
            _row(contentType=1, content='{"text":"引导填写"}', senderType="2", _id="m2", createdAt="2026-09-16 10:00:02"),
            _row(contentType=0, content='{"text":"确认提交"}', senderType="1", _id="m3", createdAt="2026-09-16 10:00:03"),
            _row(contentType=3, content='{"inquiries":"账号无法登录","ticketId":"123"}', senderType="2", _id="m4", createdAt="2026-09-16 10:00:04"),
            _row(contentType=0, content='{"text":"AI回复"}', senderType="2", _id="m5", createdAt="2026-09-16 10:00:05"),
        ]
        sessions, _, error = _parse_rows(rows)
        self.assertIsNone(error)
        messages = sessions[0]["slice_payload"]["messages"]
        self.assertEqual(len(messages), 5)
        self.assertEqual([m["content_type"] for m in messages], ["0", "1", "0", "3", "0"])
        self.assertEqual(messages[0]["speaker"], "player")
        self.assertEqual(messages[0]["text"], "玩家问题")
        self.assertIsNone(messages[0]["event_type"])
        self.assertNotIn("[转人工表单-引导填写]", messages[0]["text"])
        self.assertEqual(messages[1]["event_type"], "transfer_form_started")
        self.assertEqual(messages[3]["event_type"], "transfer_form_submitted")
        self.assertEqual(messages[3]["speaker"], "ai")
        self.assertNotIn("raw_content", messages[0])
        self.assertIn("raw_content", messages[1])

    def test_form_only_session_still_creates_slice(self):
        rows = [
            _row(contentType=1, content='{"text":"引导"}', _id="only-1", createdAt="2026-09-16 10:00:01"),
            _row(contentType=3, content='{"ticketId":"9"}', _id="only-3", createdAt="2026-09-16 10:00:02"),
        ]
        sessions, _, error = _parse_rows(rows)
        self.assertIsNone(error)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]["slice_payload"]["messages"]), 2)
        self.assertEqual(sessions[0]["_source_format"], "overseas_in_app")
        self.assertEqual(sessions[0]["_parse_stats"]["valid_messages"], 2)

    def test_content_type_0_unchanged(self):
        sessions, _, error = _parse_rows([_row(contentType=0, content='{"text":"普通消息"}', senderType="1")])
        message = sessions[0]["slice_payload"]["messages"][0]
        self.assertIsNone(error)
        self.assertEqual(message["text"], "普通消息")
        self.assertEqual(message["content_type"], "0")
        self.assertIsNone(message["event_type"])
        self.assertIsNone(message["event_label"])
        self.assertNotIn("raw_content", message)

    def test_content_type_2_is_not_transfer_form(self):
        sessions, _, error = _parse_rows([_row(contentType=2, content='{"text":"loading"}', senderType="2")])
        self.assertIsNone(error)
        message = sessions[0]["slice_payload"]["messages"][0]
        self.assertEqual(message["content_type"], "2")
        self.assertIsNone(message["event_type"])
        self.assertEqual(message["text"], "loading")
        self.assertNotIn("[转人工表单-引导填写]", message["text"])
        self.assertNotIn("[转人工表单-已提交并生成工单]", message["text"])

    def test_malformed_json_keeps_raw_and_warning(self):
        sessions, _, error = _parse_rows([
            _row(contentType=1, content="{not-json", _id="bad", createdAt="2026-09-16 10:00:01"),
            _row(contentType=0, content='{"text":"后续普通消息"}', senderType="1", _id="ok", createdAt="2026-09-16 10:00:02"),
        ])
        self.assertIsNone(error)
        self.assertEqual(len(sessions), 1)
        messages = sessions[0]["slice_payload"]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["raw_content"], "{not-json")
        self.assertIn("[转人工表单-引导填写]", messages[0]["text"])
        self.assertIn("{not-json", messages[0]["text"])
        self.assertNotIn("structured_content", messages[0])
        self.assertTrue(any("不是有效 JSON" in item for item in sessions[0]["_parse_warnings"]))
        self.assertEqual(messages[1]["text"], "后续普通消息")

    def test_readable_text_uses_dynamic_keys(self):
        text = structured_content_to_readable_text({"问题类型": "账号问题", "服务器": "s1"})
        self.assertEqual(text, "问题类型：账号问题\n服务器：s1")

    def test_excel_workbook_form_only_is_not_parse_failed(self):
        rows = [
            _row(contentType=1, content='{"text":"引导"}', _id="e1", createdAt="2026-09-16 10:00:01"),
            _row(contentType=3, content='{"ticketId":"88"}', _id="e3", createdAt="2026-09-16 10:00:02"),
        ]
        frame = pd.DataFrame(rows)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overseas.xlsx"
            frame.to_excel(path, index=False)
            sessions, _, error = parse_excel_to_sessions(str(path), "海外客服")
        self.assertIsNone(error)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]["slice_payload"]["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
