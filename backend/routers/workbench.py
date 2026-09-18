"""Unified review workbench read model for the /report review workbench."""
import json
import re
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from database import (
    QcSlice, QcSliceKnowledgeSuggestion, QcSliceQualityIssue,
    ReviewAssignment, Session as ConversationSession, User, get_db,
)

router = APIRouter(prefix="/api/review-workbench", tags=["review workbench"])
KNOWLEDGE_DECISIONS = frozenset({
    "manual_review", "candidate_needs_enrichment", "no_human_answer",
    "not_candidate", "candidate_ready",
})
REVIEWABLE_KNOWLEDGE_DECISIONS = {"candidate_ready", "candidate_needs_enrichment"}
ACTIONABLE_KB = frozenset({"candidate_ready", "candidate_needs_enrichment", "manual_review"})
EXCLUDED_KB = frozenset({"not_candidate", "no_human_answer", "reject"})
ALLOWED_KB_FILTER = ACTIONABLE_KB | {"not_candidate", "no_human_answer"}



def _normalize_kb_decisions(decision):
    """Accept repeated query keys or a comma-separated string.

    Empty / omitted means the default actionable set. ``reject`` is treated as
    ``not_candidate``. Invalid values 400.
    """
    raw = []
    if decision is None:
        raw = []
    elif isinstance(decision, str):
        raw = [part.strip() for part in decision.split(",") if part.strip()]
    else:
        for item in decision:
            raw.extend(part.strip() for part in str(item or "").split(",") if part.strip())
    selected = []
    seen = set()
    for value in raw:
        if value == "reject":
            value = "not_candidate"
        if value not in ALLOWED_KB_FILTER:
            raise HTTPException(400, "decision 参数无效")
        if value not in seen:
            seen.add(value)
            selected.append(value)
    return selected


def _kb_list_decisions(selected):
    values = list(selected or ACTIONABLE_KB)
    if "not_candidate" in values:
        values.append("reject")
    return values


def _normalized_kb_decision(value):
    return "not_candidate" if value == "reject" else value

_INVALID_SOURCE_LANGUAGES = frozenset({
    "unknown", "und", "null", "none", "n/a", "na", "",
})



def _parse_timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("Z", "")
    if "+" in text[10:]:
        text = text.split("+", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format_timestamp(value):
    parsed = _parse_timestamp(value)
    return parsed.strftime("%Y-%m-%d %H:%M:%S") if parsed else None


def _session_time(messages, conversation=None):
    """First parseable message time; else session reply_time. Never invent."""
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        formatted = _format_timestamp(message.get("created_at") or message.get("ts"))
        if formatted:
            return formatted
    reply = None
    if conversation is not None:
        reply = getattr(conversation, "reply_time", None)
        if callable(reply):
            reply = conversation.reply_time
        if reply is None:
            reply = getattr(conversation, "started_at", None)
    return _format_timestamp(reply)



_PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _issue_priority(severity):
    """Same mapping as reports list: severe=P0, medium=P1, else P2."""
    if severity == "严重":
        return "P0"
    if severity == "中级":
        return "P1"
    if severity == "需人工复核":
        return "P3"
    return "P2"


def _last_modified_at(item, assignment):
    """Later of human review update and assignment lock timestamps."""
    candidates = [getattr(item, "human_updated_at", None)]
    if assignment is not None:
        candidates.extend([
            getattr(assignment, "assigned_at", None),
            getattr(assignment, "claimed_at", None),
            getattr(assignment, "started_at", None),
            getattr(assignment, "completed_at", None),
        ])
    parsed = [stamp for stamp in (_parse_timestamp(value) for value in candidates) if stamp]
    if not parsed:
        return None
    return max(parsed).strftime("%Y-%m-%d %H:%M:%S")

def _valid_source_language(value):
    """Return a displayable language code, or None for unknown/empty tokens."""
    text = str(value or "").strip()
    if not text or text.lower() in _INVALID_SOURCE_LANGUAGES:
        return None
    return text


def _slice_language(messages):
    """Aggregate valid source_language values actually present on messages.

    Language is intentionally not inferred from text. Invalid tokens such as
    ``unknown`` are dropped, and duplicate codes collapse in appearance order
    so ``en, unknown, en`` becomes ``en``.
    """
    values = []
    seen = set()
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        value = _valid_source_language(message.get("source_language"))
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return ", ".join(values) or None


_AGENT_NAME_PREFIXES = ("人工客服", "human_agent", "operator", "agent", "客服")
# Hyphen-like characters occur in Excel exports from different sources. Keep
# this set deliberately explicit instead of normalizing person names broadly.
_AGENT_NAME_SEPARATORS = "-－‐‑‒–—―:：/"
_AGENT_NAME_DISCARDED = frozenset({
    "system", "auto_reply", "ai", "unknown", "系统", "用户", "玩家", "客服", "人工客服",
    "operator", "agent", "human_agent", "human",
})
_AGENT_NAME_DISCARDED_NORMALIZED = frozenset(item.lower() for item in _AGENT_NAME_DISCARDED)
_AGENT_NAME_ROLE_WORDS = tuple(sorted(
    set(_AGENT_NAME_PREFIXES) | set(_AGENT_NAME_DISCARDED), key=len, reverse=True,
))
_AGENT_NAME_ROLE_PREFIX = re.compile(
    r"^(?:" + "|".join(re.escape(item) for item in _AGENT_NAME_ROLE_WORDS) + r")"
    + r"(?:[\s" + re.escape(_AGENT_NAME_SEPARATORS + "、") + r"]+)",
    re.IGNORECASE,
)
# VIP Excel roles often look like ``🤵客服-焦思阳``. Strip leading emoji /
# punctuation so the existing 客服- prefix rule can run. Python ``\w`` keeps
# CJK letters, so real names are not stripped here.
_AGENT_NAME_DECORATION_PREFIX = re.compile(r"^[^\w]+", re.UNICODE)


def _clean_human_agent_name(raw):
    """Keep the real person name; strip role prefixes and role-only labels."""
    value = str(raw or "").strip()
    if not value:
        return None
    # A source may contain nested role labels, e.g. ``客服 - 客服-张三``.
    # VIP channels also prefix the role with an emoji. Strip decorations and
    # role words until only the person name remains.
    while True:
        stripped = _AGENT_NAME_DECORATION_PREFIX.sub("", value, count=1).strip()
        stripped = _AGENT_NAME_ROLE_PREFIX.sub("", stripped, count=1).strip()
        if stripped == value:
            break
        value = stripped
    if not value or value.lower() in _AGENT_NAME_DISCARDED_NORMALIZED:
        return None
    return value


def _human_agent_name(messages):
    """Extract human-agent person names for list/detail summaries.

    Only ``speaker == human_agent`` messages are considered, so a player
    ``speaker_source=用户`` never becomes part of the name. Role prefixes such
    as ``客服-`` are stripped. Conversation bubbles already display the person
    name and must not be changed for this summary field.
    """
    names = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if str(message.get("speaker") or "").strip() != "human_agent":
            continue
        explicit = str(message.get("human_agent_name") or message.get("agent_name") or "").strip()
        source = str(message.get("speaker_source") or "").strip()
        candidate = _clean_human_agent_name(explicit) or _clean_human_agent_name(source)
        if candidate:
            names.append(candidate)
    return "、".join(dict.fromkeys(names)) or None


def _json(value, fallback):
    try:
        data = json.loads(value) if value else fallback
        return data if isinstance(data, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _status(row):
    if not row:
        return "unassigned"
    return "assigned" if row.status == "pending" else row.status


def _assignment_map(db: Session, item_type: str):
    result = {}
    rows = db.query(ReviewAssignment).filter(ReviewAssignment.item_type == item_type).order_by(ReviewAssignment.id.desc()).all()
    for row in rows:
        if row.status == "cancelled":
            continue
        result.setdefault(row.item_id, row)
    return result


def _matches(assignment, user, scope, status):
    """List visibility is broader than edit ownership.

    Analysts browse the full workbench. Knowledge waste cases are filtered
    separately via decision. Write/claim stays assignment-gated.
    Only assignment_scope=mine narrows the list to the current user.
    """
    if scope == "mine":
        if not assignment or assignment.assignee_id != user.id:
            return False
    elif user.role == "admin" and scope == "unassigned":
        if assignment:
            return False
    return _matches_status(assignment, status)


def _matches_status(assignment, status):
    if status in (None, "all"):
        return True
    expected = _status(assignment)
    if status == "unassigned":
        return expected == "unassigned"
    if status == "uncompleted":
        return (assignment.status if assignment else None) in {"pending", "in_progress", "returned"}
    if status == "assigned":
        return expected in {"assigned", "pending"}
    return expected == status


def _assignment_fields(db: Session, assignment):
    person = db.query(User).filter(User.id == assignment.assignee_id).first() if assignment else None
    return {
        "assignment_id": assignment.id if assignment else None,
        "assignment_status": _status(assignment),
        "assignee_id": assignment.assignee_id if assignment else None,
        "assignee": person.username if person else None,
    }


@router.get("/items")
def list_items(
    scope: Optional[str] = Query(None, alias="assignment_scope"),
    status: Optional[str] = Query(None, alias="assignment_status"),
    batch_id: Optional[int] = Query(None),
    item_type: Optional[str] = Query(None),
    assignee_id: Optional[int] = Query(None),
    issue_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    decision: Optional[List[str]] = Query(None),
    channel: Optional[str] = Query(None),
    game: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        scope = None
    if scope not in {None, "all", "mine", "unassigned"}:
        raise HTTPException(400, "assignment_scope 参数无效")
    issue_assignments = _assignment_map(db, "quality_issue")
    kb_assignments = _assignment_map(db, "knowledge_suggestion")
    items = []
    selected_kb = _normalize_kb_decisions(decision)
    list_kb_decisions = set(_kb_list_decisions(selected_kb))
    kb_actionable_count = 0
    if item_type in (None, "quality_issue"):
        query = db.query(QcSliceQualityIssue, QcSlice, ConversationSession).join(
            QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id
        ).outerjoin(
            ConversationSession, QcSlice.session_id == ConversationSession.id
        ).filter(
            QcSliceQualityIssue.is_primary.is_(True),
            QcSlice.analysis_status.in_(["completed", "partial"]),
        )
        if batch_id:
            query = query.filter(QcSlice.batch_id == batch_id)
        if issue_type:
            query = query.filter(QcSliceQualityIssue.issue_type.contains(issue_type))
        if severity:
            query = query.filter(QcSliceQualityIssue.severity == severity)
        if channel:
            query = query.filter(QcSlice.channel == channel)
        if game:
            query = query.filter(QcSlice.game.contains(game))
        if region:
            query = query.filter(QcSlice.region.contains(region))
        for issue, slice_row, conversation in query.order_by(QcSliceQualityIssue.created_at.desc()).all():
            assignment = issue_assignments.get(issue.id)
            if user.role == "admin" and assignee_id and (not assignment or assignment.assignee_id != assignee_id):
                continue
            if not _matches(assignment, user, scope, status):
                continue
            related = db.query(QcSliceQualityIssue).filter(
                QcSliceQualityIssue.slice_id == slice_row.id
            ).order_by(QcSliceQualityIssue.confidence.desc()).all()
            messages = _json(slice_row.messages_json, [])
            items.append({
                "id": issue.id,
                "item_id": issue.id,
                "item_type": "quality_issue",
                "batch_id": slice_row.batch_id,
                "slice_id": slice_row.slice_id,
                "issue_type": issue.human_issue_type or issue.issue_type,
                "severity": issue.human_severity or issue.severity,
                "priority": _issue_priority(issue.human_severity or issue.severity),
                "maas_issue_type": issue.issue_type,
                "maas_severity": issue.severity,
                "confidence": issue.confidence,
                "human_ai_score": issue.human_ai_score,
                "human_tags": _json(issue.human_tags_json, []),
                "human_review_status": issue.human_review_status or "pending_review",
                "human_processing_destination": issue.human_processing_destination or "",
                "human_review_comment": issue.human_review_comment or "",
                "channel": slice_row.channel,
                "game": slice_row.game,
                "region": slice_row.region,
                "session_link": conversation.session_link if conversation else None,
                "messages": messages,
                "session_time": _session_time(messages, conversation),
                "last_modified_at": _last_modified_at(issue, assignment),
                "all_issues": [{
                    "id": x.id,
                    "issue_id": x.issue_id,
                    "issue_type": x.human_issue_type or x.issue_type,
                    "severity": x.human_severity or x.severity,
                    "maas_issue_type": x.issue_type,
                    "maas_severity": x.severity,
                    "confidence": x.confidence,
                    "human_ai_score": x.human_ai_score,
                    "human_tags": _json(x.human_tags_json, []),
                    "human_review_status": x.human_review_status or "pending_review",
                    "ai_message_ids": _json(x.ai_message_ids, []),
                    "evidence_message_ids": _json(x.evidence_message_ids, []),
                    "player_question": _json(x.player_question_json, {}),
                    "ai_answer": _json(x.ai_answer_json, {}),
                    "reason": x.reason or "",
                    "suggestion": x.suggestion or "",
                    "revised_reply": x.revised_reply,
                    "revised_reply_zh_cn": x.revised_reply_zh_cn,
                    "needs_manual_review": bool(x.needs_manual_review),
                    "manual_review_reason": x.manual_review_reason,
                } for x in related],
                **_assignment_fields(db, assignment),
            })
    query = db.query(QcSliceKnowledgeSuggestion, QcSlice, ConversationSession).join(
        QcSlice, QcSliceKnowledgeSuggestion.slice_id == QcSlice.id
    ).outerjoin(
        ConversationSession, QcSlice.session_id == ConversationSession.id
    ).filter(
        QcSliceKnowledgeSuggestion.decision.in_(KNOWLEDGE_DECISIONS | {"reject"}),
        QcSlice.analysis_status.in_(["completed", "partial"]),
    )
    if batch_id:
        query = query.filter(QcSlice.batch_id == batch_id)
    if channel:
        query = query.filter(QcSlice.channel == channel)
    if game:
        query = query.filter(QcSlice.game.contains(game))
    if region:
        query = query.filter(QcSlice.region.contains(region))
    include_kb_rows = item_type in (None, "knowledge_suggestion")
    for suggestion, slice_row, conversation in query.order_by(QcSliceKnowledgeSuggestion.created_at.desc()).all():
        assignment = kb_assignments.get(suggestion.id)
        if user.role == "admin" and assignee_id and (not assignment or assignment.assignee_id != assignee_id):
            continue
        if not _matches(assignment, user, scope, status):
            continue
        normalized = _normalized_kb_decision(suggestion.decision)
        if normalized in ACTIONABLE_KB:
            kb_actionable_count += 1
        if (not include_kb_rows) or (normalized not in list_kb_decisions):
            continue
        messages = _json(slice_row.messages_json, [])
        items.append({
                "id": suggestion.id,
                "item_id": suggestion.id,
                "item_type": "knowledge_suggestion",
                "batch_id": slice_row.batch_id,
                "slice_id": slice_row.slice_id,
                "channel": slice_row.channel,
                "game": slice_row.game,
                "region": slice_row.region,
                "language": _slice_language(messages),
                "decision": "not_candidate" if suggestion.decision == "reject" else suggestion.decision,
                "maas_decision": suggestion.decision,
                "confidence": suggestion.confidence,
                "answer_source": suggestion.answer_source,
                "human_agent_name": _human_agent_name(messages),
                "category": suggestion.human_category or suggestion.category,
                "title": suggestion.title,
                "standard_questions": _json(suggestion.human_standard_questions, []) or _json(suggestion.standard_questions, []),
                "maas_standard_questions": _json(suggestion.standard_questions, []),
                "standard_answer": suggestion.human_standard_answer if suggestion.human_standard_answer is not None else suggestion.standard_answer,
                "maas_standard_answer": suggestion.standard_answer,
                "applicable_scope": _json(suggestion.human_applicable_scope, {}) if suggestion.human_applicable_scope else _json(suggestion.applicable_scope, {}),
                "knowledge_base": suggestion.human_knowledge_base,
                "human_tags": _json(suggestion.human_tags_json, []),
                "human_review_status": suggestion.human_review_status or "pending_review",
                "human_review_comment": suggestion.human_review_comment or "",
                "reason": suggestion.reason,
                "reject_reason": suggestion.reject_reason,
                "needs_manual_review": bool(suggestion.needs_manual_review),
                "manual_review_reason": suggestion.manual_review_reason,
                "question_message_ids": _json(suggestion.question_message_ids, []),
                "answer_message_ids": _json(suggestion.answer_message_ids, []),
                "evidence_message_ids": _json(suggestion.evidence_message_ids, []),
                "created_at": suggestion.created_at.isoformat() if suggestion.created_at else None,
                "messages": messages,
                "session_link": conversation.session_link if conversation else None,
                "session_time": _session_time(messages, conversation),
                "last_modified_at": _last_modified_at(suggestion, assignment),
                **_assignment_fields(db, assignment),
            })
    items.sort(key=lambda item: (
        0 if item.get("item_type") == "quality_issue" else 1,
        _PRIORITY_RANK.get(item.get("priority") or "P2", 9) if item.get("item_type") == "quality_issue" else 0,
        -(item.get("confidence") or 0),
    ))
    total = len(items)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "kb_actionable_count": kb_actionable_count,
    }
