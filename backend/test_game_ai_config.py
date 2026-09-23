# -*- coding: utf-8 -*-
import os, sys, json, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, GameAiConfig, MappingConfig, QcSlice, AnalysisBatch, Session as QcSession, User, utcnow
from routers import analysis as analysis_router
from routers import admin as admin_router
from services.game_ai_config import (
    EXPECTED_SEED_REGION_COUNTS, GAME_AI_SEED_LABELS, GameAiConfigSeedError,
    decide_analysis, resolve_seed_catalog, seed_game_ai_configs,
    should_analyze_from_snapshot, slice_creation_fields, snapshot_json,
    SKIP_REASON_AI_DISABLED,
)


def _memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _complete_mapping_pairs():
    return {
        "东南亚": {"冒险大作战", "妖怪金手指", "少年名将", "巨神", "主宰世界", "狩猎使命", "巨神军师"},
        "欧美": {"冒险大作战", "主宰世界", "明日特攻队", "明日特攻队2", "热血神剑", "狩猎使命", "曙光重临", "勇者联盟"},
        "港台": {"狩猎使命", "狩猎使命二推", "妖怪金手指", "主宰世界", "勇者联盟", "热血神剑", "指尖无双", "冒险大作战", "明日特攻队2"},
        "日本": {"小小英雄", "主宰世界", "指尖无双", "指尖像素城", "妖怪金手指", "狩猎使命", "冒险大作战"},
        "全球": {"曙光重临"},
    }


def _seed_mapping_rows(db, pairs):
    for region, games in pairs.items():
        for game in games:
            db.add(MappingConfig(
                raw_region=region, raw_channel="DC", game=game,
                match_field="none", target_channel="DC", target_region=region, enabled=True,
            ))
    db.flush()


class SeedCatalogTest(unittest.TestCase):
    def test_unique_mapping_writes_31_with_region_counts(self):
        resolved = resolve_seed_catalog(_complete_mapping_pairs(), strict_unique=True)
        self.assertEqual(len(resolved), 31)
        from collections import Counter
        counts = Counter(item["region"] for item in resolved)
        self.assertEqual(counts, EXPECTED_SEED_REGION_COUNTS)
        self.assertEqual(sum(len(v) for v in GAME_AI_SEED_LABELS.values()), 31)
        pairs = {(item["game"], item["region"]) for item in resolved}
        self.assertEqual(len(pairs), 31)
        self.assertIn(("巨神", "东南亚"), pairs)
        self.assertIn(("巨神军师", "东南亚"), pairs)
        self.assertNotIn(("欧美冒险", "欧美"), pairs)
        self.assertIn(("冒险大作战", "欧美"), pairs)

    def test_display_names_do_not_collapse_jushen_onto_jushen_junshi(self):
        current = {
            "东南亚": {"冒险大作战", "妖怪金手指", "少年名将", "巨神军师", "主宰世界", "狩猎使命"},
            "欧美": {"冒险大作战", "主宰世界", "明日特攻队", "明日特攻队2", "热血神剑", "狩猎使命", "曙光重临", "勇者联盟"},
            "港台": {"狩猎使命", "狩猎使命二推", "妖怪金手指", "主宰世界", "勇者联盟", "热血神剑", "指尖无双", "冒险大作战", "明日特攻队2"},
            "日本": {"小小英雄", "主宰世界", "指尖无双", "指尖像素城", "妖怪金手指", "狩猎使命", "冒险大作战"},
            "全球": {"曙光重临"},
        }
        resolved = resolve_seed_catalog(current, strict_unique=True)
        pairs = {(item["game"], item["region"]) for item in resolved}
        self.assertEqual(len(resolved), 31)
        self.assertIn(("巨神", "东南亚"), pairs)
        self.assertIn(("巨神军师", "东南亚"), pairs)

    def test_seed_writes_even_if_mapping_only_has_jushen_junshi(self):
        db = _memory_db()
        _seed_mapping_rows(db, {
            "东南亚": {"冒险大作战", "妖怪金手指", "少年名将", "巨神军师", "主宰世界", "狩猎使命"},
            "欧美": {"冒险大作战", "主宰世界", "明日特攻队", "明日特攻队2", "热血神剑", "狩猎使命", "曙光重临", "勇者联盟"},
            "港台": {"狩猎使命", "狩猎使命二推", "妖怪金手指", "主宰世界", "勇者联盟", "热血神剑", "指尖无双", "冒险大作战", "明日特攻队2"},
            "日本": {"小小英雄", "主宰世界", "指尖无双", "指尖像素城", "妖怪金手指", "狩猎使命", "冒险大作战"},
            "全球": {"曙光重临"},
        })
        seed_game_ai_configs(db)
        self.assertEqual(db.query(GameAiConfig).count(), 31)

    def test_seed_inserts_31_when_mapping_is_unique(self):
        db = _memory_db()
        _seed_mapping_rows(db, _complete_mapping_pairs())
        seed_game_ai_configs(db)
        rows = db.query(GameAiConfig).filter(GameAiConfig.enabled.is_(True)).all()
        self.assertEqual(len(rows), 31)
        self.assertTrue(all(row.analysis_enabled for row in rows))
        sea = [row for row in rows if row.region == "东南亚"]
        self.assertEqual(len(sea), 7)
        self.assertEqual(len([row for row in rows if row.region == "欧美"]), 8)
        self.assertEqual(len([row for row in rows if row.region == "港台"]), 9)
        self.assertEqual(len([row for row in rows if row.region == "日本"]), 6)
        self.assertEqual(len([row for row in rows if row.region == "全球"]), 1)


class AnalysisGateTest(unittest.TestCase):
    def setUp(self):
        self.db = _memory_db()
        self.db.add(GameAiConfig(game="冒险大作战", region="欧美", analysis_enabled=True, enabled=True))
        self.db.add(GameAiConfig(game="主宰世界", region="欧美", analysis_enabled=False, enabled=True))
        self.db.add(GameAiConfig(game="妖怪金手指", region="东南亚", analysis_enabled=True, enabled=False))
        self.db.flush()

    def test_enabled_config_creates_pending_ai_task(self):
        fields = slice_creation_fields(self.db, "冒险大作战", "欧美")
        self.assertEqual(fields["analysis_status"], "pending")
        self.assertIsNone(fields["skip_reason"])
        self.assertTrue(fields["analysis_enabled_snapshot"])
        snapshot = json.loads(fields["ai_config_snapshot_json"])
        self.assertTrue(snapshot["analysis_enabled"])
        self.assertEqual(snapshot["game"], "冒险大作战")
        self.assertEqual(snapshot["region"], "欧美")
        self.assertIsNotNone(snapshot["id"])

    def test_disabled_analysis_skips_maas_without_failed(self):
        fields = slice_creation_fields(self.db, "主宰世界", "欧美")
        self.assertEqual(fields["analysis_status"], "skipped")
        self.assertEqual(fields["skip_reason"], SKIP_REASON_AI_DISABLED)
        self.assertFalse(fields["analysis_enabled_snapshot"])
        self.assertNotEqual(fields["analysis_status"], "failed")
        self.assertNotEqual(fields["analysis_status"], "pending")

    def test_unmatched_defaults_to_skip(self):
        fields = slice_creation_fields(self.db, "闪电突击", "欧美")
        self.assertEqual(fields["analysis_status"], "skipped")
        self.assertEqual(fields["skip_reason"], SKIP_REASON_AI_DISABLED)
        snapshot = json.loads(fields["ai_config_snapshot_json"])
        self.assertIsNone(snapshot["id"])
        self.assertFalse(snapshot["analysis_enabled"])

    def test_same_game_region_ignores_channel(self):
        dc = decide_analysis(self.db, "冒险大作战", "欧美")
        fb = decide_analysis(self.db, "冒险大作战", "欧美")
        self.assertEqual(dc.config_id, fb.config_id)
        self.assertTrue(dc.should_analyze)
        raw = decide_analysis(self.db, "1741860167481", "欧美")
        self.assertFalse(raw.should_analyze)

    def test_skipped_slice_keeps_messages_and_has_no_qc_or_kb(self):
        user = User(username="u", email="u@t.com", hashed_pwd="x", role="admin")
        self.db.add(user); self.db.flush()
        batch = AnalysisBatch(name="t", created_by=user.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        session = QcSession(batch_id=batch.id, event_id="keep-me", channel="DC", game="主宰世界",
                            region="欧美", full_transcript="hello player")
        self.db.add(session); self.db.flush()
        fields = slice_creation_fields(self.db, "主宰世界", "欧美")
        slice_row = QcSlice(
            batch_id=batch.id, session_id=session.id, slice_id="s-skip",
            channel="FB", game="主宰世界", region="欧美",
            messages_json='[{"speaker":"player","text":"hi"}]',
            **fields,
        )
        self.db.add(slice_row); self.db.flush()
        stored = self.db.query(QcSlice).filter(QcSlice.slice_id == "s-skip").one()
        self.assertEqual(stored.analysis_status, "skipped")
        self.assertEqual(json.loads(stored.messages_json)[0]["text"], "hi")
        self.assertEqual(self.db.query(QcSession).filter(QcSession.id == session.id).count(), 1)
        self.assertEqual(stored.quality_issue_count or 0, 0)
        self.assertIsNone(stored.quality_has_issue)
        self.assertIsNone(stored.knowledge_decision)
        self.assertFalse(should_analyze_from_snapshot(stored))

    def test_snapshot_survives_config_change(self):
        fields = slice_creation_fields(self.db, "冒险大作战", "欧美")
        slice_row = SimpleNamespace(
            ai_config_snapshot_json=fields["ai_config_snapshot_json"],
            analysis_enabled_snapshot=fields["analysis_enabled_snapshot"],
            analysis_status="processing",
        )
        config = self.db.query(GameAiConfig).filter(GameAiConfig.game == "冒险大作战").one()
        config.analysis_enabled = False
        self.db.flush()
        self.assertTrue(should_analyze_from_snapshot(slice_row))
        self.assertEqual(slice_row.analysis_status, "processing")
        live = decide_analysis(self.db, "冒险大作战", "欧美")
        self.assertFalse(live.should_analyze)

    def test_running_slice_not_rewritten_when_config_saved(self):
        user = User(username="u2", email="u2@t.com", hashed_pwd="x", role="admin")
        self.db.add(user); self.db.flush()
        batch = AnalysisBatch(name="run", created_by=user.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        fields = slice_creation_fields(self.db, "冒险大作战", "欧美")
        slice_row = QcSlice(
            batch_id=batch.id, slice_id="running", channel="DC", game="冒险大作战", region="欧美",
            messages_json="[]", analysis_status="processing", started_at=utcnow(),
            ai_config_id=fields["ai_config_id"],
            ai_config_snapshot_json=fields["ai_config_snapshot_json"],
            analysis_enabled_snapshot=True,
        )
        self.db.add(slice_row); self.db.flush()
        config = self.db.query(GameAiConfig).filter(GameAiConfig.game == "冒险大作战").one()
        config.analysis_enabled = False
        config.enabled = False
        self.db.flush()
        stored = self.db.query(QcSlice).filter(QcSlice.slice_id == "running").one()
        self.assertEqual(stored.analysis_status, "processing")
        self.assertNotEqual(stored.analysis_status, "failed")
        self.assertNotEqual(stored.analysis_status, "skipped")
        self.assertTrue(should_analyze_from_snapshot(stored))

    def test_claim_skips_disabled_slices(self):
        user = User(username="u3", email="u3@t.com", hashed_pwd="x", role="admin")
        self.db.add(user); self.db.flush()
        batch = AnalysisBatch(name="claim", created_by=user.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        enabled = QcSlice(batch_id=batch.id, slice_id="on", game="冒险大作战", region="欧美",
                          messages_json="[]", **slice_creation_fields(self.db, "冒险大作战", "欧美"))
        disabled = QcSlice(batch_id=batch.id, slice_id="off", game="主宰世界", region="欧美",
                           messages_json="[]", **slice_creation_fields(self.db, "主宰世界", "欧美"))
        unmatched = QcSlice(batch_id=batch.id, slice_id="none", game="闪电突击", region="欧美",
                            messages_json="[]", **slice_creation_fields(self.db, "闪电突击", "欧美"))
        self.db.add_all([enabled, disabled, unmatched]); self.db.flush()
        claimed = analysis_router._claim_slices(self.db, batch.id, {enabled.id, disabled.id, unmatched.id}, 10)
        self.assertEqual(claimed, [enabled.id])
        self.assertEqual(self.db.get(QcSlice, enabled.id).analysis_status, "processing")
        self.assertEqual(self.db.get(QcSlice, disabled.id).analysis_status, "skipped")
        self.assertEqual(self.db.get(QcSlice, unmatched.id).analysis_status, "skipped")
        counts = analysis_router._slice_counts(self.db, batch.id)
        self.assertEqual(counts["runnable_count"], 0)
        self.assertEqual(counts["processed_count"], 2)
        self.assertEqual(counts["pending_count"], 1)

    def test_persist_is_not_used_for_skipped(self):
        user = User(username="u4", email="u4@t.com", hashed_pwd="x", role="admin")
        self.db.add(user); self.db.flush()
        batch = AnalysisBatch(name="p", created_by=user.id, source_task_id="upload", source_region="unknown")
        self.db.add(batch); self.db.flush()
        slice_row = QcSlice(
            batch_id=batch.id, slice_id="no-maas", game="主宰世界", region="欧美",
            messages_json="[]", **slice_creation_fields(self.db, "主宰世界", "欧美"),
        )
        self.db.add(slice_row); self.db.flush()
        self.assertFalse(should_analyze_from_snapshot(slice_row))
        self.assertIsNone(slice_row.quality_has_issue)
        self.assertIsNone(slice_row.knowledge_decision)


class AdminPermissionTest(unittest.TestCase):
    def test_mutating_routes_require_admin(self):
        import inspect
        from fastapi.params import Depends
        from fastapi import HTTPException
        from auth import require_admin
        for fn in (admin_router.create_game_ai_config, admin_router.update_game_ai_config,
                   admin_router.delete_game_ai_config, admin_router.list_game_ai_configs):
            found = any(
                isinstance(param.default, Depends) and param.default.dependency is require_admin
                for param in inspect.signature(fn).parameters.values()
            )
            self.assertTrue(found, f"{fn.__name__} 缺少管理员权限")
        db = MagicMock()
        analyst = SimpleNamespace(id=2, role="analyst", username="a")
        with self.assertRaises(HTTPException) as ctx:
            require_admin(current_user=analyst, db=db)
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
