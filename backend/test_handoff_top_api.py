"""Exercise the actual HTTP query and JSON projection with an isolated SQLite DB."""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database import Base, QcSlice, AnalysisBatch, get_db
from auth import require_admin
from routers.admin import router
from handoff_taxonomy import BACKEND_HANDOFF_REASON


class HandoffTopApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.db.add_all([AnalysisBatch(id=1, name="test"), AnalysisBatch(id=2, name="excluded")])
        for i in range(1, 9):
            handoff = {"reason_type": BACKEND_HANDOFF_REASON, "handoff_occurred": True,
                       "decision": "handoff_reasonable"}
            if i != 2:
                handoff["detail_type"] = "充值金额查询"
            self.db.add(QcSlice(
                id=i, batch_id=2 if i == 5 else 1, slice_id=f"test-{i}",
                game="other" if i == 3 else "game", channel="other" if i == 6 else "DC",
                region="other" if i == 7 else "region",
                completed_at=datetime(2025 if i == 4 else 2026, 10, 14),
                analysis_status="failed" if i == 8 else "completed",
                human_handoff_json=json.dumps(handoff, ensure_ascii=False),
            ))
        self.db.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_admin] = lambda: object()
        self.client = TestClient(app)
        self.url = next(route.path for route in app.routes if route.path.endswith("/audit/handoff-top"))

    def tearDown(self):
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def test_filters_denominator_field_projection_and_legacy_no_write(self):
        params = dict(start="2026-10-01T00:00:00", end="2026-10-31T23:59:59",
                      game="game", channel="DC", region="region", batch_ids="1",
                      reason_type=BACKEND_HANDOFF_REASON, detail_type="充值金额查询")
        response = self.client.get(self.url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["detail_parent_count"], 2)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["slices"][0]["human_handoff"]["detail_type"], "充值金额查询")
        self.assertEqual({item["share"] for item in data["details"]}, {.5})
        params["detail_type"] = "未细分"
        legacy = self.client.get(self.url, params=params).json()
        self.assertEqual(legacy["total"], 1)
        self.assertIsNone(legacy["slices"][0]["human_handoff"]["detail_type"])
        self.assertNotIn("detail_type", json.loads(self.db.get(QcSlice, 2).human_handoff_json))

    def test_classification_review_is_available_in_top_and_detail(self):
        row = self.db.get(QcSlice, 1)
        handoff = json.loads(row.human_handoff_json)
        handoff.update(detail_type="信息不足无法判断", needs_manual_review=True,
                       manual_review_reason="附件缺失，无法确认业务分类")
        row.human_handoff_json = json.dumps(handoff, ensure_ascii=False)
        self.db.commit()
        response = self.client.get(self.url, params={
            "reason_type": BACKEND_HANDOFF_REASON, "detail_type": "信息不足无法判断",
        })
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["summary"]["manual_review"], 0)
        shown = data["slices"][0]["human_handoff"]
        self.assertEqual(shown["decision"], "handoff_reasonable")
        self.assertTrue(shown["needs_manual_review"])
        self.assertEqual(shown["manual_review_reason"], handoff["manual_review_reason"])
        detail_url = self.url.replace("/handoff-top", "/slices/1")
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["human_handoff"], handoff)

    def test_unknown_detail_filters_without_rewriting_original(self):
        for value in ("旧的未知标签", " 充值金额查询 "):
            row = self.db.get(QcSlice, 1)
            handoff = json.loads(row.human_handoff_json)
            handoff["detail_type"] = value
            original = json.dumps(handoff, ensure_ascii=False)
            row.human_handoff_json = original
            self.db.commit()
            response = self.client.get(self.url, params={
                "reason_type": BACKEND_HANDOFF_REASON, "detail_type": "未细分",
            })
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["total"], 2)
            self.assertTrue(any(item["id"] == 1 for item in data["slices"]))
            self.assertEqual(self.db.get(QcSlice, 1).human_handoff_json, original)

    def test_frontend_display_whitelist_matches_backend(self):
        import re
        from handoff_taxonomy import HANDOFF_DETAIL_TYPES
        source = (Path(__file__).resolve().parents[1] / "frontend/src/handoffDetail.ts").read_text(encoding="utf-8")
        block = source.split("export const HANDOFF_DETAIL_TYPES = [", 1)[1].split("] as const", 1)[0]
        self.assertEqual(tuple(re.findall(r"'([^']+)'", block)), HANDOFF_DETAIL_TYPES)


if __name__ == "__main__":
    unittest.main()
