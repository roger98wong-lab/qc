import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from database import Base, MappingConfig, QcSlice, Session, QcIssue, KbSuggestion, AnalysisBatch
from routers import admin as admin_router


def _make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class ApplyGameProductMappingsTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_db()
        self.db.add(AnalysisBatch(id=1, name="t", source_task_id="upload", source_region="unknown", created_by=1, status="analyzing"))
        self.db.add(MappingConfig(
            raw_region="欧美",
            raw_channel="官网客服",
            game="勇者联盟",
            match_field="gameProductId",
            match_value="1779344175941",
            target_channel="官网客服",
            target_region="欧美",
            enabled=True,
        ))
        self.db.add(MappingConfig(
            raw_region="日本",
            raw_channel="官网客服",
            game="冒险大作战",
            match_field="gameProductId",
            match_value="1705652915101",
            target_channel="官网客服",
            target_region="日本",
            enabled=True,
        ))
        self.db.add(QcSlice(batch_id=1, slice_id="s1", channel="官网客服", game="1779344175941", region="", analysis_status="analyzing"))
        self.db.add(QcSlice(batch_id=1, slice_id="s2", channel="官网客服", game="1705652915101", region=None, analysis_status="done"))
        self.db.add(QcSlice(batch_id=1, slice_id="s3", channel="官网客服", game="勇者联盟", region="欧美", analysis_status="done"))
        self.db.add(QcSlice(batch_id=1, slice_id="s4", channel="DC", game="1779344175941", region="", analysis_status="done"))
        self.db.add(Session(batch_id=1, event_id="e1", channel="官网客服", game="1779344175941", region=""))
        self.db.add(QcIssue(batch_id=1, game="1779344175941", region="", channel="官网客服", status="pending"))
        self.db.add(KbSuggestion(batch_id=1, game="1705652915101", region="", channel="官网客服"))
        self.db.commit()

    def test_rewrites_numeric_games_and_skips_standard_names(self):
        summary = admin_router.apply_enabled_game_product_mappings(self.db)
        self.db.commit()
        self.assertEqual(sorted(summary["match_values"]), ["1705652915101", "1779344175941"])
        self.assertEqual(summary["slices_updated"], 2)
        rows = {row.slice_id: row for row in self.db.query(QcSlice).all()}
        self.assertEqual(rows["s1"].game, "勇者联盟")
        self.assertEqual(rows["s1"].region, "欧美")
        self.assertEqual(rows["s1"].analysis_status, "analyzing")
        self.assertEqual(rows["s2"].game, "冒险大作战")
        self.assertEqual(rows["s2"].region, "日本")
        self.assertEqual(rows["s3"].game, "勇者联盟")
        self.assertEqual(rows["s4"].game, "1779344175941")
        session = self.db.query(Session).one()
        self.assertEqual(session.game, "勇者联盟")
        self.assertEqual(session.region, "欧美")
        issue = self.db.query(QcIssue).one()
        self.assertEqual(issue.game, "勇者联盟")
        kb = self.db.query(KbSuggestion).one()
        self.assertEqual(kb.game, "冒险大作战")
        self.assertEqual(kb.region, "日本")

    def test_does_not_rewrite_protected_channels(self):
        self.db.add(QcSlice(batch_id=1, slice_id="s5", channel="DC", game="1779344175941", region=""))
        self.db.commit()
        admin_router.apply_enabled_game_product_mappings(self.db, ["1779344175941"])
        self.db.commit()
        row = self.db.query(QcSlice).filter(QcSlice.slice_id == "s5").one()
        self.assertEqual(row.channel, "DC")
        self.assertEqual(row.game, "1779344175941")


if __name__ == "__main__":
    unittest.main()
