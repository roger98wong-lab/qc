"""Read-only workload aggregates for the manual-review data dashboard."""
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from auth import get_current_user
from database import (
    QAPoolEntry,
    QcSlice,
    QcSliceKnowledgeSuggestion,
    QcSliceQualityIssue,
    ReviewAssignment,
    User,
    get_db, utcnow,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
ACTIVE_ANALYSES = {"completed", "partial", "done"}
ACTIONABLE_KNOWLEDGE_DECISIONS = {"candidate_ready", "candidate_needs_enrichment"}


def _json_list(value):
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _pairs(source, reviewer=False):
    pairs = []
    for row in _json_list(source.human_qa_pairs):
        if isinstance(row, dict):
            question, answer = str(row.get("question") or "").strip(), str(row.get("answer") or "").strip()
            if question and answer:
                pairs.append((question, answer))
    if pairs:
        return pairs
    questions = _json_list(source.human_standard_questions)
    answer = source.human_standard_answer or ""
    if not reviewer and not questions:
        questions, answer = _json_list(source.standard_questions), source.standard_answer or ""
    return [(str(question).strip(), str(answer).strip()) for question in questions if str(question).strip() and str(answer).strip()]


def _parse_day(value: Optional[str], field_name: str, fallback: date):
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"{field_name} 必须为 YYYY-MM-DD")


def _latest_assignments(db: Session):
    result = {}
    rows = db.query(ReviewAssignment).options(
        load_only(
            ReviewAssignment.id, ReviewAssignment.item_type, ReviewAssignment.item_id,
            ReviewAssignment.assignee_id, ReviewAssignment.status, ReviewAssignment.review_decision,
            ReviewAssignment.completed_at,
        )
    ).order_by(ReviewAssignment.id.desc()).all()
    for row in rows:
        result.setdefault((row.item_type, row.item_id), row)
    return result


def _scope_assignment(assignment, user: User, assignee_id: Optional[int]):
    """Return whether a source row belongs to this dashboard scope."""
    if user.role != "admin":
        return assignment is not None and assignment.assignee_id == user.id
    if assignee_id is not None:
        return assignment is not None and assignment.assignee_id == assignee_id
    return True


def _source_rows(db: Session, user: User, assignee_id: Optional[int]):
    assignments = _latest_assignments(db)
    rows = []
    issue_query = db.query(QcSliceQualityIssue, QcSlice).join(QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id).options(
        load_only(QcSliceQualityIssue.id, QcSliceQualityIssue.slice_id, QcSliceQualityIssue.is_primary),
        load_only(QcSlice.id, QcSlice.analysis_status),
    ).filter(
        QcSliceQualityIssue.is_primary.is_(True), QcSlice.analysis_status.in_(ACTIVE_ANALYSES),
    )
    for issue, slice_row in issue_query.all():
        assignment = assignments.get(("quality_issue", issue.id))
        if _scope_assignment(assignment, user, assignee_id):
            rows.append(("quality_issue", issue, slice_row, assignment))
    knowledge_query = db.query(QcSliceKnowledgeSuggestion, QcSlice).join(QcSlice, QcSliceKnowledgeSuggestion.slice_id == QcSlice.id).options(
        load_only(
            QcSliceKnowledgeSuggestion.id, QcSliceKnowledgeSuggestion.slice_id,
            QcSliceKnowledgeSuggestion.decision, QcSliceKnowledgeSuggestion.answer_source,
            QcSliceKnowledgeSuggestion.processing_status, QcSliceKnowledgeSuggestion.human_qa_pairs,
            QcSliceKnowledgeSuggestion.human_standard_questions, QcSliceKnowledgeSuggestion.human_standard_answer,
            QcSliceKnowledgeSuggestion.standard_questions, QcSliceKnowledgeSuggestion.standard_answer,
        ),
        load_only(QcSlice.id, QcSlice.analysis_status),
    ).filter(
        QcSliceKnowledgeSuggestion.decision.in_(ACTIONABLE_KNOWLEDGE_DECISIONS),
        QcSlice.analysis_status.in_(ACTIVE_ANALYSES),
    )
    for suggestion, slice_row in knowledge_query.all():
        assignment = assignments.get(("knowledge_suggestion", suggestion.id))
        if _scope_assignment(assignment, user, assignee_id):
            rows.append(("knowledge_suggestion", suggestion, slice_row, assignment))
    return rows, assignments


def _case_statuses(rows):
    counts = Counter()
    for _, _, _, assignment in rows:
        status = assignment.status if assignment else "unassigned"
        if status in {"unassigned", "pending", "returned", "cancelled"}:
            counts["unprocessed"] += 1
        elif status == "in_progress":
            counts["processing"] += 1
        elif status == "completed":
            counts["processed"] += 1
    return counts


def _entry_statuses(db: Session):
    result = {}
    for qa_source, source_item_id, qa_index, processing_status in db.query(
        QAPoolEntry.qa_source, QAPoolEntry.source_item_id, QAPoolEntry.qa_index, QAPoolEntry.processing_status
    ).filter(QAPoolEntry.source_pair_active.is_(True)).all():
        result[(qa_source, source_item_id, qa_index)] = processing_status or "pending_entry"
    return result


def _pending_qa_counts(db: Session, user: User, assignee_id: Optional[int], source_rows, assignments):
    """Count active QA pairs without writing/materializing pool rows."""
    statuses = _entry_statuses(db)
    case_pending = knowledge_pending = 0
    reviewer_query = db.query(QcSliceQualityIssue, QcSlice).join(QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id).options(
        load_only(
            QcSliceQualityIssue.id, QcSliceQualityIssue.qa_source, QcSliceQualityIssue.knowledge_pool_status,
            QcSliceQualityIssue.human_qa_pairs, QcSliceQualityIssue.human_standard_questions,
            QcSliceQualityIssue.human_standard_answer,
        ),
        load_only(QcSlice.id, QcSlice.analysis_status),
    ).filter(
        QcSliceQualityIssue.qa_source == "quality_reviewer",
        QcSliceQualityIssue.knowledge_pool_status.isnot(None),
        QcSlice.analysis_status.in_(ACTIVE_ANALYSES),
    )
    for issue, _slice_row in reviewer_query.all():
        # A non-admin must only see pairs for a case they own; admins may
        # narrow the same source by its review assignment.
        assignment = assignments.get(("quality_issue", issue.id))
        if not _scope_assignment(assignment, user, assignee_id):
            continue
        for index, _pair in enumerate(_pairs(issue, reviewer=True)):
            status = statuses.get(("quality_reviewer", issue.id, index), issue.knowledge_pool_status or "pending_entry")
            if status == "pending_entry":
                case_pending += 1
    for item_type, source, _slice_row, _assignment in source_rows:
        if item_type != "knowledge_suggestion":
            continue
        # Match the existing publication-pool boundary: customer-service QA
        # becomes pending-entry work only after its assigned review is approved.
        if not (
            source.answer_source == "human_agent"
            and _assignment is not None
            and _assignment.status == "completed"
            and _assignment.review_decision == "approved"
        ):
            continue
        for index, _pair in enumerate(_pairs(source)):
            status = statuses.get(("human_agent", source.id, index), source.processing_status or "pending_entry")
            if status == "pending_entry":
                knowledge_pending += 1
    return case_pending, knowledge_pending


def _tag_scope_allowed(issue_id: int, assignments, user: User, assignee_id: Optional[int]):
    return _scope_assignment(assignments.get(("quality_issue", issue_id)), user, assignee_id)


def _tag_metrics(db: Session, user: User, assignee_id: Optional[int], start_day: date, end_day: date, assignments):
    trend = defaultdict(Counter)
    totals = Counter()
    tagged_cases = 0
    start_dt = datetime.combine(start_day, datetime.min.time())
    end_dt = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
    rows = db.query(QcSliceQualityIssue).join(QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id).options(
        load_only(
            QcSliceQualityIssue.id, QcSliceQualityIssue.human_tags_json,
            QcSliceQualityIssue.human_updated_at, QcSliceQualityIssue.created_at,
        )
    ).filter(
        QcSliceQualityIssue.is_primary.is_(True),
        QcSlice.analysis_status.in_(ACTIVE_ANALYSES),
    ).all()
    for issue in rows:
        if not _tag_scope_allowed(issue.id, assignments, user, assignee_id):
            continue
        tags = [str(tag).strip() for tag in _json_list(issue.human_tags_json) if str(tag).strip()]
        tagged_at = issue.human_updated_at or issue.created_at
        if not tags or not tagged_at or not (start_dt <= tagged_at < end_dt):
            continue
        tagged_cases += 1
        day = tagged_at.date().isoformat()
        for tag in tags:
            trend[day][tag] += 1
            totals[tag] += 1
    days = []
    cursor = start_day
    while cursor <= end_day:
        days.append({"date": cursor.isoformat(), "tags": dict(sorted(trend[cursor.isoformat()].items()))})
        cursor += timedelta(days=1)
    ordered_totals = [{"tag": tag, "count": count} for tag, count in sorted(totals.items(), key=lambda row: (-row[1], row[0]))]
    return tagged_cases, days, ordered_totals


def _top_handler(db: Session, user: User, assignee_id: Optional[int], start_day: date, end_day: date):
    start_dt = datetime.combine(start_day, datetime.min.time())
    end_dt = datetime.combine(end_day + timedelta(days=1), datetime.min.time())
    counts = Counter()
    query = db.query(User.username, func.count(ReviewAssignment.id)).join(
        ReviewAssignment, ReviewAssignment.assignee_id == User.id
    ).filter(
        ReviewAssignment.status == "completed",
        ReviewAssignment.completed_at.isnot(None),
        ReviewAssignment.completed_at >= start_dt,
        ReviewAssignment.completed_at < end_dt,
    )
    if user.role != "admin":
        query = query.filter(ReviewAssignment.assignee_id == user.id)
    elif assignee_id is not None:
        query = query.filter(ReviewAssignment.assignee_id == assignee_id)
    for name, count in query.group_by(User.username).all():
        if name:
            counts[name] = int(count or 0)
    if not counts:
        return {"name": None, "count": 0}
    name, count = sorted(counts.items(), key=lambda row: (-row[1], row[0]))[0]
    return {"name": name, "count": count}


def review_workload(db: Session, user: User, start_day: date, end_day: date, requested_assignee_id: Optional[int]):
    assignee_id = requested_assignee_id if user.role == "admin" else user.id
    if assignee_id is not None and not db.query(User).filter(User.id == assignee_id, User.is_active.is_(True)).first():
        raise HTTPException(400, "负责人不存在或已停用")
    rows, assignments = _source_rows(db, user, assignee_id)
    cases = _case_statuses(rows)
    pending_entry, knowledge_pending_entry = _pending_qa_counts(db, user, assignee_id, rows, assignments)
    tagged_cases, tag_trend, tag_totals = _tag_metrics(db, user, assignee_id, start_day, end_day, assignments)
    top_tag = tag_totals[0] if tag_totals else {"tag": None, "count": 0}
    people = db.query(User).filter(User.is_active.is_(True), User.role.in_(["admin", "analyst"])).order_by(User.id).all()
    if user.role != "admin":
        people = [person for person in people if person.id == user.id]
    return {
        "filters": {
            "start_date": start_day.isoformat(), "end_date": end_day.isoformat(), "assignee_id": assignee_id,
            "assignees": [{"id": person.id, "username": person.username} for person in people],
        },
        "summary": {
            "unprocessed": cases["unprocessed"], "processing": cases["processing"],
            "pending_entry": pending_entry, "processed": cases["processed"],
            "tagged_cases": tagged_cases, "knowledge_pending_entry": knowledge_pending_entry,
            "top_tag": {"name": top_tag.get("tag"), "count": top_tag["count"]},
            "top_handler": _top_handler(db, user, assignee_id, start_day, end_day),
        },
        "tag_trend": tag_trend,
        "tag_totals": tag_totals,
    }


@router.get("/review-workload")
def get_review_workload(
    start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None),
    assignee_id: Optional[int] = Query(None),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    today = utcnow().date()
    end_day = _parse_day(end_date, "end_date", today)
    start_day = _parse_day(start_date, "start_date", end_day - timedelta(days=29))
    if start_day > end_day:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    if (end_day - start_day).days > 366:
        raise HTTPException(400, "时间范围不能超过 366 天")
    return review_workload(db, user, start_day, end_day, assignee_id)
