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

from services.analyzer import AnalysisProtocolError, analyze_slice


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


if __name__ == "__main__":
    unittest.main()
