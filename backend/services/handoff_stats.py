"""Aggregate human_handoff JSON from analyzed slices for the audit TOP view."""
from __future__ import annotations

import json

HANDOFF_TOP_SCOPES = ("occurred", "required", "unreasonable")
NON_POSITIVE_REASON_TYPES = frozenset({"可由AI继续处理", "证据不足"})


def parse_human_handoff(raw):
    if isinstance(raw, dict):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def matches_scope(handoff: dict, scope: str) -> bool:
    if scope == "occurred":
        return handoff.get("handoff_occurred") is True
    if scope == "required":
        return handoff.get("decision") == "handoff_required"
    if scope == "unreasonable":
        return handoff.get("decision") == "handoff_unreasonable"
    return False


def _empty_bucket(reason_type: str) -> dict:
    return {
        "reason_type": reason_type,
        "count": 0,
        "share": 0.0,
        "reasonable": 0,
        "unreasonable": 0,
        "required": 0,
        "is_positive": reason_type not in NON_POSITIVE_REASON_TYPES,
    }


def aggregate_handoff_top(records: list[dict], *, scope: str = "occurred",
                          reason_type: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    if scope not in HANDOFF_TOP_SCOPES:
        scope = "occurred"
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)

    analyzed = []
    for row in records:
        handoff = row.get("human_handoff")
        if not isinstance(handoff, dict) or not handoff.get("reason_type"):
            continue
        analyzed.append(row)

    summary = {
        "analyzed": len(analyzed),
        "occurred": 0,
        "reasonable": 0,
        "unreasonable": 0,
        "required": 0,
        "not_required": 0,
        "manual_review": 0,
    }
    for row in analyzed:
        handoff = row["human_handoff"]
        if handoff.get("handoff_occurred") is True:
            summary["occurred"] += 1
        decision = handoff.get("decision")
        if decision == "handoff_reasonable":
            summary["reasonable"] += 1
        elif decision == "handoff_unreasonable":
            summary["unreasonable"] += 1
        elif decision == "handoff_required":
            summary["required"] += 1
        elif decision == "handoff_not_required":
            summary["not_required"] += 1
        elif decision == "manual_review":
            summary["manual_review"] += 1

    scoped = [row for row in analyzed if matches_scope(row["human_handoff"], scope)]
    buckets: dict[str, dict] = {}
    for row in scoped:
        key = str(row["human_handoff"].get("reason_type") or "").strip() or "未知"
        bucket = buckets.setdefault(key, _empty_bucket(key))
        bucket["count"] += 1
        decision = row["human_handoff"].get("decision")
        if decision == "handoff_reasonable":
            bucket["reasonable"] += 1
        elif decision == "handoff_unreasonable":
            bucket["unreasonable"] += 1
        elif decision == "handoff_required":
            bucket["required"] += 1

    total_scoped = len(scoped)
    reasons = sorted(buckets.values(), key=lambda item: (-item["count"], item["reason_type"]))
    for bucket in reasons:
        bucket["share"] = round(bucket["count"] / total_scoped, 4) if total_scoped else 0.0

    selected = (reason_type or "").strip() or None
    filtered = scoped
    if selected:
        filtered = [row for row in scoped if str(row["human_handoff"].get("reason_type") or "") == selected]
    total = len(filtered)
    start = (page - 1) * page_size
    slices = filtered[start:start + page_size]
    return {
        "scope": scope,
        "selected_reason_type": selected,
        "summary": summary,
        "scoped_count": total_scoped,
        "reasons": reasons,
        "slices": slices,
        "total": total,
        "page": page,
        "page_size": page_size,
    }