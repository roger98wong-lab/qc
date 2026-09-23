import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT / "backend")

from database import OVERSEAS_GAME_PRODUCT_MAPPINGS
from services.excel_parser import _apply_mapping, _cell_text, _force_mushroom_rush_to_sea_adventure, _parse_overseas


def _rule(**kwargs):
    defaults = dict(
        raw_region=None,
        raw_channel="官网客服",
        game="明日特攻队2",
        match_field="gameProductId",
        match_value="1741860167481",
        target_channel="官网客服",
        target_region="欧美",
        enabled=True,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class MushroomRushAliasTest(unittest.TestCase):
    def test_force_rewrites_to_sea_adventure(self):
        game, region = _force_mushroom_rush_to_sea_adventure("Mushroom Rush", "英语区")
        self.assertEqual(game, "冒险大作战")
        self.assertEqual(region, "东南亚")
        game, region = _force_mushroom_rush_to_sea_adventure("mushroom rush", None)
        self.assertEqual((game, region), ("冒险大作战", "东南亚"))

    def test_apply_mapping_does_not_rewrite_mushroom_rush_game(self):
        rows = [
            _rule(raw_region="英语区", raw_channel="DC", match_field="none", game=None, match_value=None, target_channel="DC", target_region="欧美"),
        ]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("英语区", "DC", "Mushroom Rush", "", "hello")
        self.assertEqual(channel, "DC")
        self.assertEqual(region, "欧美")
        self.assertIsNone(game)


class CellTextProductIdTest(unittest.TestCase):
    def test_integer_float_product_id_stays_digits(self):
        self.assertEqual(_cell_text(1741860167481.0), "1741860167481")
        self.assertEqual(_cell_text(1741860167481), "1741860167481")


class ApplyGameProductIdMappingTest(unittest.TestCase):
    def test_maps_standard_game_and_region(self):
        rows = [
            _rule(raw_channel=None, match_field="none", game=None, match_value=None, target_channel="DC", target_region="欧美"),
            _rule(),
            _rule(match_value="1751018108611", game="主宰世界", target_region="欧美"),
        ]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping(None, "官网客服", "1741860167481", "", "")
        self.assertEqual(channel, "官网客服")
        self.assertEqual(region, "欧美")
        self.assertEqual(game, "明日特攻队2")

    def test_unknown_product_id_keeps_digits(self):
        rows = [_rule()]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping(None, "官网客服", "1779344175941", "", "")
        self.assertEqual(channel, "官网客服")
        self.assertIsNone(game)
        self.assertIsNone(region)

    def test_game_product_id_ignores_rule_raw_region(self):
        rows = [_rule(raw_region="欧美", match_value="1779344175941", game="勇者联盟", target_region="欧美")]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping(None, "官网客服", "1779344175941", "", "")
        self.assertEqual(channel, "官网客服")
        self.assertEqual(region, "欧美")
        self.assertEqual(game, "勇者联盟")

    def test_non_product_id_still_requires_raw_region(self):
        rows = [
            _rule(raw_region="英语区", raw_channel="DC", match_field="none", game="冒险大作战", match_value=None, target_channel="DC", target_region="欧美"),
        ]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("日本", "DC", "冒险大作战", "", "hello")
        self.assertEqual(channel, "DC")
        self.assertEqual(region, "日本")
        self.assertIsNone(game)

    def test_does_not_rewrite_dc_game_names(self):
        rows = [
            _rule(raw_channel="DC", match_field="none", game="冒险大作战", match_value=None, target_channel="DC", target_region="欧美"),
            _rule(),
        ]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("英语区", "DC", "冒险大作战", "", "hello")
        self.assertEqual(channel, "DC")
        self.assertEqual(region, "欧美")
        self.assertIsNone(game)

    def _sea_channel_rows(self, include_fb=True, include_any=True):
        rows = []
        if include_any:
            rows.append(_rule(raw_region="东南亚", raw_channel=None, match_field="none", game=None, match_value=None, target_channel="DC", target_region="东南亚"))
        if include_fb:
            rows.append(_rule(raw_region="东南亚", raw_channel="FB", match_field="none", game=None, match_value=None, target_channel="FB", target_region="东南亚"))
        return rows

    def test_fb_sea_prefix_beats_empty_channel_rule(self):
        rows = self._sea_channel_rows()
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("东南亚", "FB-Sea", "Mushroom Rush", "", "")
        self.assertEqual((channel, region, game), ("FB", "东南亚", None))

    def test_fb_still_matches_fb_rule(self):
        rows = self._sea_channel_rows()
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("东南亚", "FB", "Mushroom Rush", "", "")
        self.assertEqual((channel, region, game), ("FB", "东南亚", None))

    def test_dc_sea_does_not_match_fb_rule(self):
        rows = self._sea_channel_rows()
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("东南亚", "DC-Sea", "Mushroom Rush", "", "")
        self.assertEqual((channel, region, game), ("DC", "东南亚", None))

    def test_empty_channel_rule_used_only_without_specific_channel(self):
        rows = self._sea_channel_rows(include_fb=False, include_any=True)
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("东南亚", "FB-Sea", "Mushroom Rush", "", "")
        self.assertEqual((channel, region, game), ("DC", "东南亚", None))

    def test_exact_channel_beats_prefix_rule(self):
        rows = [
            _rule(raw_region="东南亚", raw_channel="FB", match_field="none", game=None, match_value=None, target_channel="FB", target_region="东南亚"),
            _rule(raw_region="东南亚", raw_channel="FB-Sea", match_field="none", game=None, match_value=None, target_channel="FB", target_region="东南亚"),
            _rule(raw_region="东南亚", raw_channel=None, match_field="none", game=None, match_value=None, target_channel="DC", target_region="东南亚"),
        ]
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("东南亚", "FB-Sea", "Mushroom Rush", "", "")
        self.assertEqual((channel, region, game), ("FB", "东南亚", None))

    def test_non_product_id_channel_rule_still_requires_raw_region(self):
        rows = self._sea_channel_rows()
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            channel, region, game = _apply_mapping("日本", "FB-Sea", "Mushroom Rush", "", "")
        self.assertEqual(channel, "FB-Sea")
        self.assertEqual(region, "日本")
        self.assertIsNone(game)


class ParseOverseasGameMappingTest(unittest.TestCase):
    def test_parse_replaces_product_id_with_standard_game(self):
        rows = [_rule(), _rule(match_value="1751018108611", game="主宰世界", target_region="欧美")]
        frame = pd.DataFrame([{
            "sessionId": "sess-mapped",
            "senderType": "2",
            "createdAt": "2026-09-17 10:00:00",
            "contentType": 0,
            "content": '{"text": "hello"}',
            "gameProductId": 1741860167481.0,
            "_id": "msg-mapped",
        }])
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            sessions, _, error = _parse_overseas(frame)
        self.assertIsNone(error)
        self.assertEqual(sessions[0]["channel"], "官网客服")
        self.assertEqual(sessions[0]["game"], "明日特攻队2")
        self.assertEqual(sessions[0]["region"], "欧美")
        self.assertEqual(sessions[0]["slice_payload"]["game"], "明日特攻队2")
        self.assertEqual(sessions[0]["slice_payload"]["region"], "欧美")

    def test_unmapped_product_id_stays_id(self):
        rows = [_rule()]
        frame = pd.DataFrame([{
            "sessionId": "sess-unmapped",
            "senderType": "2",
            "createdAt": "2026-09-17 10:00:00",
            "contentType": 0,
            "content": '{"text": "hello"}',
            "gameProductId": "1779344175941",
            "_id": "msg-unmapped",
        }])
        with patch("database.SessionLocal") as session_cls:
            session_cls.return_value.query.return_value.filter.return_value.all.return_value = rows
            sessions, _, error = _parse_overseas(frame)
        self.assertIsNone(error)
        self.assertEqual(sessions[0]["game"], "1779344175941")
        self.assertEqual(sessions[0]["region"], "")


class SeedOverseasMappingsTest(unittest.TestCase):
    def test_expected_count_and_dirty_codes_are_not_stored(self):
        self.assertEqual(len(OVERSEAS_GAME_PRODUCT_MAPPINGS), 22)
        values = {item[0]: item[1:] for item in OVERSEAS_GAME_PRODUCT_MAPPINGS}
        self.assertEqual(values["1741860167481"], ("明日特攻队2", "欧美"))
        self.assertEqual(values["1751018108611"], ("主宰世界", "欧美"))
        games = {item[1] for item in OVERSEAS_GAME_PRODUCT_MAPPINGS}
        self.assertNotIn("2", games)
        self.assertNotIn("sydh", games)
        self.assertNotIn("1779344175941", values)
        self.assertNotIn("1705652915101", values)
        self.assertNotIn("1778158005621", values)


if __name__ == "__main__":
    unittest.main()
