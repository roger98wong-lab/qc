"""Standard game + region switch for AI/MaaS analysis."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from database import GameAiConfig, MappingConfig, utcnow

SKIP_REASON_AI_DISABLED = "ai_analysis_disabled"

# Business display names grouped by standard region. Tokens after the region
# prefix are resolved against MappingConfig standard game names.
GAME_AI_SEED_LABELS = {
    "东南亚": [
        "东南亚冒险", "东南亚金手指", "东南亚名将", "东南亚巨神",
        "东南亚主宰", "东南亚狩猎", "东南亚巨神军师",
    ],
    "欧美": [
        "欧美冒险", "欧美主宰", "欧美明日", "欧美明日2",
        "欧美热血", "欧美狩猎", "欧美曙光", "欧美勇者",
    ],
    "港台": [
        "港台狩猎", "港台狩猎（二推）", "港台金手指", "港台主宰",
        "港台勇者", "港台热血", "港台指尖", "港台冒险", "港台明日2",
    ],
    "日本": [
        "日本小英", "日本主宰", "日本指尖", "日本金手指", "日本狩猎", "日本冒险",
    ],
    "全球": ["全球曙光"],
}

# Short display tokens -> standard MappingConfig.game names.
# Longer tokens win ("明日2" before "明日", "巨神军师" before "巨神").
# 东南亚巨神 is kept as 巨神 so it does not collapse onto 巨神军师.
GAME_TOKEN_ALIASES = {
    "冒险": "冒险大作战",
    "金手指": "妖怪金手指",
    "名将": "少年名将",
    "主宰": "主宰世界",
    "狩猎": "狩猎使命",
    "狩猎（二推）": "狩猎使命二推",
    "狩猎(二推)": "狩猎使命二推",
    "明日": "明日特攻队",
    "明日2": "明日特攻队2",
    "热血": "热血神剑",
    "曙光": "曙光重临",
    "勇者": "勇者联盟",
    "指尖": "指尖无双",
    "小英": "小小英雄",
    "巨神军师": "巨神军师",
    "巨神": "巨神",
}

# Canonical 31 standard game + region rows. Display names are converted here
# instead of fuzzy-matching MappingConfig, so a missing/duplicate mapping
# cannot block the whole catalog.
GAME_AI_SEED_PAIRS = (
    ("冒险大作战", "东南亚"),
    ("妖怪金手指", "东南亚"),
    ("少年名将", "东南亚"),
    ("巨神", "东南亚"),
    ("主宰世界", "东南亚"),
    ("狩猎使命", "东南亚"),
    ("巨神军师", "东南亚"),
    ("冒险大作战", "欧美"),
    ("主宰世界", "欧美"),
    ("明日特攻队", "欧美"),
    ("明日特攻队2", "欧美"),
    ("热血神剑", "欧美"),
    ("狩猎使命", "欧美"),
    ("曙光重临", "欧美"),
    ("勇者联盟", "欧美"),
    ("狩猎使命", "港台"),
    ("狩猎使命二推", "港台"),
    ("妖怪金手指", "港台"),
    ("主宰世界", "港台"),
    ("勇者联盟", "港台"),
    ("热血神剑", "港台"),
    ("指尖无双", "港台"),
    ("冒险大作战", "港台"),
    ("明日特攻队2", "港台"),
    ("小小英雄", "日本"),
    ("主宰世界", "日本"),
    ("指尖无双", "日本"),
    ("妖怪金手指", "日本"),
    ("狩猎使命", "日本"),
    ("冒险大作战", "日本"),
    ("曙光重临", "全球"),
)

EXPECTED_SEED_REGION_COUNTS = {
    "东南亚": 7,
    "欧美": 8,
    "港台": 9,
    "日本": 6,
    "全球": 1,
}


class GameAiConfigSeedError(ValueError):
    """Raised when a display name cannot uniquely map to standard game+region."""


@dataclass(frozen=True)
class GameAiDecision:
    game: str
    region: str
    analysis_enabled: bool
    config_id: int | None
    config_enabled: bool | None = None

    @property
    def should_analyze(self) -> bool:
        return bool(self.analysis_enabled and self.config_id is not None and self.config_enabled)

    def snapshot(self) -> dict:
        return {
            "id": self.config_id,
            "game": self.game,
            "region": self.region,
            "analysis_enabled": bool(self.analysis_enabled) if self.config_id is not None else False,
            "enabled": bool(self.config_enabled) if self.config_enabled is not None else False,
        }


def _norm(value) -> str:
    return str(value or "").strip()


def mapping_standard_pairs(rows: Iterable) -> dict[str, set[str]]:
    """region -> set of standard game names from MappingConfig."""
    by_region: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        game = _norm(getattr(row, "game", None))
        region = _norm(getattr(row, "target_region", None))
        if game and region:
            by_region[region].add(game)
    return by_region


def _token_for_label(label: str, region: str) -> str:
    text = _norm(label)
    prefix = _norm(region)
    if not text.startswith(prefix):
        raise GameAiConfigSeedError(f"展示名称「{label}」不是标准地区「{region}」的前缀组合，拒绝写入")
    return text[len(prefix):].strip()


def resolve_display_label(label: str, region: str, games_in_region: set[str] | None = None) -> str:
    """Map one business display name to the standard game name."""
    token = _token_for_label(label, region)
    if not token:
        raise GameAiConfigSeedError(f"展示名称「{label}」去掉地区后为空，无法映射标准游戏")
    games_in_region = games_in_region or set()
    if token in GAME_TOKEN_ALIASES:
        return GAME_TOKEN_ALIASES[token]
    if token in games_in_region:
        return token
    contains = sorted(game for game in games_in_region if token and token in game)
    if len(contains) == 1:
        return contains[0]
    raise GameAiConfigSeedError(
        f"展示名称「{label}」无法唯一映射到地区「{region}」的标准游戏。"
        f"token={token!r}，候选={contains or sorted(games_in_region)}"
    )


def resolve_seed_catalog(by_region: dict[str, set[str]] | None = None, *, strict_unique: bool = True) -> list[dict]:
    """Resolve the 31 display names onto unique standard game+region rows."""
    by_region = by_region or {}
    resolved = []
    errors: list[str] = []
    seen: dict[tuple[str, str], str] = {}
    for region, labels in GAME_AI_SEED_LABELS.items():
        games = by_region.get(region, set())
        for label in labels:
            try:
                game = resolve_display_label(label, region, games)
            except GameAiConfigSeedError as exc:
                errors.append(str(exc))
                continue
            key = (game, region)
            if key in seen:
                errors.append(
                    f"展示名称「{label}」与「{seen[key]}」都映射到标准 game=「{game}」、region=「{region}」，"
                    "拒绝静默写入重复或错误配置"
                )
                continue
            seen[key] = label
            resolved.append({"label": label, "game": game, "region": region})
    expected_pairs = list(GAME_AI_SEED_PAIRS)
    expected_total = sum(EXPECTED_SEED_REGION_COUNTS.values())
    label_total = sum(len(v) for v in GAME_AI_SEED_LABELS.values())
    if label_total != expected_total or len(expected_pairs) != expected_total:
        errors.append(f"初始化名单条数是 {label_total}/{len(expected_pairs)}，期望 {expected_total}")
    actual_pairs = [(item["game"], item["region"]) for item in resolved]
    if actual_pairs != expected_pairs:
        errors.append(
            "展示名称转换后的标准 game+region 与预定 31 条目录不一致，"
            f"实际={actual_pairs}"
        )
    region_counts = Counter(item["region"] for item in resolved)
    if strict_unique:
        if errors:
            raise GameAiConfigSeedError("；".join(errors))
        if len(resolved) != expected_total:
            raise GameAiConfigSeedError(
                f"初始化名单应写入 {expected_total} 条唯一配置，实际 {len(resolved)}"
            )
        for region, expected in EXPECTED_SEED_REGION_COUNTS.items():
            actual = region_counts.get(region, 0)
            if actual != expected:
                raise GameAiConfigSeedError(
                    f"地区「{region}」应写入 {expected} 条，实际 {actual}"
                )
    elif errors and not resolved:
        raise GameAiConfigSeedError("；".join(errors))
    return resolved


def load_mapping_pairs(db) -> dict[str, set[str]]:
    rows = db.query(MappingConfig.game, MappingConfig.target_region).filter(
        MappingConfig.game.isnot(None), MappingConfig.game != "",
        MappingConfig.target_region.isnot(None), MappingConfig.target_region != "",
    ).distinct().all()
    return mapping_standard_pairs(SimpleRow(game=game, target_region=region) for game, region in rows)


class SimpleRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def find_active_config(db, game: str | None, region: str | None) -> GameAiConfig | None:
    game_name = _norm(game)
    region_name = _norm(region)
    if not game_name or not region_name:
        return None
    return db.query(GameAiConfig).filter(
        GameAiConfig.game == game_name,
        GameAiConfig.region == region_name,
        GameAiConfig.enabled.is_(True),
    ).first()


def decide_analysis(db, game: str | None, region: str | None) -> GameAiDecision:
    game_name = _norm(game)
    region_name = _norm(region)
    row = find_active_config(db, game_name, region_name)
    if not row:
        return GameAiDecision(
            game=game_name, region=region_name, analysis_enabled=False,
            config_id=None, config_enabled=None,
        )
    return GameAiDecision(
        game=row.game, region=row.region, analysis_enabled=bool(row.analysis_enabled),
        config_id=row.id, config_enabled=bool(row.enabled),
    )


def snapshot_json(decision: GameAiDecision) -> str:
    return json.dumps(decision.snapshot(), ensure_ascii=False, separators=(",", ":"))


def parse_snapshot(raw) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def should_analyze_from_snapshot(slice_row) -> bool:
    """Running tasks honor the stored snapshot, not the live config table.

    Legacy slices without a snapshot keep historical analyze-on-start behavior.
    """
    snapshot = parse_snapshot(getattr(slice_row, "ai_config_snapshot_json", None))
    if snapshot is None and getattr(slice_row, "analysis_enabled_snapshot", None) is None:
        return True
    if snapshot is not None:
        return bool(snapshot.get("id")) and bool(snapshot.get("enabled", True)) and bool(snapshot.get("analysis_enabled"))
    return bool(getattr(slice_row, "analysis_enabled_snapshot", False))


def slice_creation_fields(db, game: str | None, region: str | None) -> dict:
    decision = decide_analysis(db, game, region)
    enabled = decision.should_analyze
    return {
        "analysis_status": "pending" if enabled else "skipped",
        "skip_reason": None if enabled else SKIP_REASON_AI_DISABLED,
        "ai_config_id": decision.config_id,
        "ai_config_snapshot_json": snapshot_json(decision),
        "analysis_enabled_snapshot": enabled,
        "completed_at": None if enabled else utcnow(),
    }


def serialize_config(row: GameAiConfig) -> dict:
    return {
        "id": row.id,
        "game": row.game,
        "region": row.region,
        "analysis_enabled": bool(row.analysis_enabled),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def seed_game_ai_configs(db=None):
    """Insert missing initial AI configs without rewriting admin changes.

    Mapping uniqueness is validated first. Duplicate display names that collapse
    onto the same standard game+region fail loudly instead of writing a wrong row.
    """
    from database import SessionLocal

    owned = db is None
    local = db or SessionLocal()
    try:
        catalog = resolve_seed_catalog(load_mapping_pairs(local), strict_unique=True)
        existing = {
            (_norm(game), _norm(region))
            for game, region in local.query(GameAiConfig.game, GameAiConfig.region).all()
        }
        for item in catalog:
            key = (item["game"], item["region"])
            if key in existing:
                continue
            local.add(GameAiConfig(
                game=item["game"],
                region=item["region"],
                analysis_enabled=True,
                enabled=True,
            ))
            existing.add(key)
        if owned:
            local.commit()
    except GameAiConfigSeedError:
        if owned:
            local.rollback()
        raise
    finally:
        if owned:
            local.close()
