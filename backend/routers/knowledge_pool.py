"""Knowledge-pool APIs.

The pool is a durable projection of reviewer-authored QA pairs.  Source rows
remain authoritative; ``QAPoolEntry`` stores per-pair lifecycle state and the
status log provides an append-only audit trail.
"""
import csv
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import and_
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from audit import record_audit
from database import (
    QAPoolEntry, QAPoolStatusLog, QcSlice, QcSliceKnowledgeSuggestion,
    QcSliceQualityIssue, ReviewAssignment, User, get_db, utcnow,
)

router = APIRouter(prefix="/api/knowledge-pool", tags=["知识池"])
PROCESSING_STATUSES = {"pending_entry", "organized", "excluded", "exported", "uploaded_to_jiuzhang"}
VISIBLE_DECISIONS = {"candidate_ready", "candidate_needs_enrichment", "candidate_pending_feedback"}


def _json_list(value):
    try:
        parsed = json.loads(value) if value else []
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _json_value(value, fallback):
    try:
        parsed = json.loads(value) if value else fallback
        return parsed if type(parsed) is type(fallback) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _source_pairs(source, is_reviewer=False):
    """Prefer new human QA JSON while retaining legacy question fields."""
    stored = _json_list(source.human_qa_pairs)
    pairs = [
        {"question": str(row.get("question") or "").strip(), "answer": str(row.get("answer") or "").strip()}
        for row in stored if isinstance(row, dict)
    ]
    if pairs:
        return pairs
    questions = _json_list(source.human_standard_questions)
    answer = str(source.human_standard_answer or "")
    if not is_reviewer and not questions:
        questions = _json_list(source.standard_questions)
        answer = str(source.standard_answer or "")
    return [{"question": str(question or "").strip(), "answer": answer.strip()}
            for question in questions if str(question or "").strip()]


def _base_query(db: Session, user: User, scope: Optional[str] = None):
    query = db.query(QcSliceKnowledgeSuggestion, QcSlice, ReviewAssignment).join(
        QcSlice, QcSliceKnowledgeSuggestion.slice_id == QcSlice.id
    ).join(
        ReviewAssignment,
        and_(ReviewAssignment.item_type == "knowledge_suggestion",
             ReviewAssignment.item_id == QcSliceKnowledgeSuggestion.id,
             ReviewAssignment.status == "completed",
             ReviewAssignment.review_decision == "approved"),
    ).filter(
        QcSliceKnowledgeSuggestion.answer_source == "human_agent",
        QcSliceKnowledgeSuggestion.decision.in_(VISIBLE_DECISIONS),
        QcSlice.analysis_status.in_(["completed", "partial", "done"]),
    )
    if user.role != "admin" or scope == "mine":
        query = query.filter(ReviewAssignment.assignee_id == user.id)
    return query


def _reviewer_assignment(db: Session, issue_id: int):
    return db.query(ReviewAssignment).filter(
        ReviewAssignment.item_type == "quality_issue", ReviewAssignment.item_id == issue_id
    ).order_by(ReviewAssignment.id.desc()).first()


def _reviewer_rows(db: Session, user: User, scope: Optional[str] = None):
    query = db.query(QcSliceQualityIssue, QcSlice).join(
        QcSlice, QcSliceQualityIssue.slice_id == QcSlice.id
    ).filter(
        QcSliceQualityIssue.qa_source == "quality_reviewer",
        QcSliceQualityIssue.knowledge_pool_status.isnot(None),
        QcSlice.analysis_status.in_(["completed", "partial", "done"]),
    )
    rows = []
    for issue, slice_row in query.order_by(QcSliceQualityIssue.id.desc()).all():
        assignment = _reviewer_assignment(db, issue.id)
        if user.role != "admin" and (scope == "mine" or scope is None):
            if issue.knowledge_pool_created_by != user.id and (not assignment or assignment.assignee_id != user.id):
                continue
        rows.append((issue, slice_row, assignment))
    return rows


def _pool_id(qa_source: str, source_id: int, qa_index: int) -> str:
    return f"{qa_source}:{source_id}:{qa_index}"


def _sync_entries(db: Session, source, qa_source: str, creator_id=None, is_reviewer=False):
    pairs = _source_pairs(source, is_reviewer)
    item_type = "quality_issue" if is_reviewer else "knowledge_suggestion"
    existing = {
        row.qa_index: row for row in db.query(QAPoolEntry).filter(
            QAPoolEntry.qa_source == qa_source, QAPoolEntry.source_item_id == source.id
        ).all()
    }
    now = utcnow()
    for index, pair in enumerate(pairs):
        row = existing.pop(index, None)
        if row is None:
            legacy_status = (source.knowledge_pool_status if is_reviewer else source.processing_status) or "pending_entry"
            if legacy_status not in PROCESSING_STATUSES:
                legacy_status = "pending_entry"
            row = QAPoolEntry(
                pool_id=_pool_id(qa_source, source.id, index), qa_source=qa_source,
                source_item_type=item_type, source_item_id=source.id, qa_index=index,
                question=pair["question"], answer=pair["answer"], created_by=creator_id,
                processing_status=legacy_status,
            )
            db.add(row)
        else:
            row.question, row.answer = pair["question"], pair["answer"]
            row.source_pair_active, row.updated_at = True, now
    for row in existing.values():
        if row.source_pair_active:
            row.source_pair_active, row.updated_at = False, now


def _sync_visible_entries(db: Session, user: User, scope=None):
    sources = {}
    for suggestion, slice_row, assignment in _base_query(db, user, scope).all():
        sources.setdefault(("human_agent", suggestion.id), (suggestion, slice_row, assignment, "human_agent", False))
    for issue, slice_row, assignment in _reviewer_rows(db, user, scope):
        sources.setdefault(("quality_reviewer", issue.id), (issue, slice_row, assignment, "quality_reviewer", True))
    for source, _, assignment, qa_source, is_reviewer in sources.values():
        creator_id = source.knowledge_pool_created_by if is_reviewer else (source.human_updated_by or (assignment.assignee_id if assignment else None))
        _sync_entries(db, source, qa_source, creator_id, is_reviewer)
    db.commit()
    return list(sources.values())


def _serialize(entry, source, slice_row, assignment, db: Session, is_reviewer=False):
    creator = db.query(User).filter(User.id == entry.created_by).first() if entry.created_by else None
    reviewer = db.query(User).filter(User.id == assignment.assignee_id).first() if assignment else None
    if is_reviewer:
        category, answer_source, knowledge_base = source.human_category or "", "quality_reviewer", source.human_knowledge_base or ""
        scope = _json_value(source.human_applicable_scope, {}) if source.human_applicable_scope else {}
        reason = source.reason or ""
    else:
        category = source.human_category or source.category or ""
        answer_source, knowledge_base = source.answer_source or "", source.human_knowledge_base or ""
        scope = _json_value(source.human_applicable_scope or source.applicable_scope, {})
        reason = source.reason or ""
    return {
        "id": entry.id, "pool_id": entry.pool_id, "qa_source": entry.qa_source,
        "qa_index": entry.qa_index, "quality_issue_id": source.id if is_reviewer else None,
        "knowledge_suggestion_id": None if is_reviewer else source.id, "source_item_id": source.id,
        "batch_id": slice_row.batch_id, "slice_id": slice_row.slice_id,
        "game": slice_row.game or "", "region": slice_row.region or "", "channel": slice_row.channel or "",
        "question": entry.question, "answer": entry.answer,
        "standard_questions": [entry.question], "standard_answer": entry.answer,
        "category": category, "decision": "" if is_reviewer else (source.decision or ""),
        "answer_source": answer_source, "knowledge_base": knowledge_base, "applicable_scope": scope,
        "reason": reason, "processing_status": entry.processing_status,
        "review_assignment_id": assignment.id if assignment else None,
        "reviewer": reviewer.username if reviewer else "",
        "reviewed_at": assignment.completed_at.isoformat() if assignment and assignment.completed_at else None,
        "created_by": creator.username if creator else "",
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
    }


def _visible_entry_map(db: Session, user: User, scope=None):
    sources = _sync_visible_entries(db, user, scope)
    source_map = {(qa_source, source.id): (source, slice_row, assignment, is_reviewer)
                  for source, slice_row, assignment, qa_source, is_reviewer in sources}
    result = {}
    for entry in db.query(QAPoolEntry).filter(QAPoolEntry.source_pair_active.is_(True)).all():
        context = source_map.get((entry.qa_source, entry.source_item_id))
        if context:
            result[entry.pool_id] = (entry, *context)
    return result


@router.get("/items")
def list_pool_items(
    game: Optional[str] = Query(None), region: Optional[str] = Query(None), channel: Optional[str] = Query(None),
    processing_status: Optional[str] = Query("pending_entry"), assignment_scope: Optional[str] = Query(None),
    page: int = Query(1), page_size: int = Query(50), user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    if assignment_scope not in (None, "all", "mine"):
        raise HTTPException(400, "assignment_scope 参数无效")
    if user.role != "admin": assignment_scope = "mine"
    if processing_status not in PROCESSING_STATUSES and processing_status != "all":
        raise HTTPException(400, "processing_status 参数无效")
    rows = []
    for entry, source, slice_row, assignment, is_reviewer in _visible_entry_map(db, user, assignment_scope).values():
        if game and game not in (slice_row.game or ""): continue
        if region and region not in (slice_row.region or ""): continue
        if channel and channel != (slice_row.channel or ""): continue
        if processing_status != "all" and entry.processing_status != processing_status: continue
        rows.append(_serialize(entry, source, slice_row, assignment, db, is_reviewer))
    rows.sort(key=lambda row: (row.get("created_at") or "", row["pool_id"]), reverse=True)
    options = {key: sorted({row[key] for row in rows if row.get(key)}) for key in ("game", "region", "channel")}
    total = len(rows); page = max(page, 1); page_size = max(page_size, 1)
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size, "filter_options": options}


def _resolve_entries(db, user, ids, scope):
    visible = _visible_entry_map(db, user, scope)
    return {item_id: visible[item_id] for item_id in ids if item_id in visible}


def _set_status(db, entry, user, target, reason, action="knowledge_pool_status_changed"):
    current = entry.processing_status or "pending_entry"
    allowed = ((current == "pending_entry" and target in {"organized", "excluded", "exported", "uploaded_to_jiuzhang"})
               or (current == "organized" and target in {"exported", "excluded"})
               or (user.role == "admin" and current == "exported" and target == "uploaded_to_jiuzhang")
               or (user.role == "admin" and current in {"organized", "excluded", "exported"} and target == "pending_entry"))
    if not allowed: return current, False, f"当前状态 {current} 不能变为 {target}"
    now = utcnow(); entry.processing_status, entry.updated_at = target, now
    if target == "organized": entry.organized_by, entry.organized_at = user.id, now
    if target == "exported": entry.exported_by, entry.exported_at = user.id, now
    if target == "uploaded_to_jiuzhang": entry.jiuzhang_uploaded_by, entry.jiuzhang_uploaded_at = user.id, now
    db.add(QAPoolStatusLog(pool_entry_id=entry.id, action=action, from_status=current, to_status=target,
                           operator_id=user.id, operator_name=user.username, reason=reason,
                           operator_role=user.role, target_type="qa_pool_entry", target_id=entry.pool_id,
                           from_value=current, to_value=target))
    record_audit(db, action, "qa_pool_entry", entry.pool_id, user=user, from_value=current, to_value=target, reason=reason)
    return current, True, ""


@router.patch("/status")
def update_pool_status(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = data.get("ids") or []; target = str(data.get("status") or ""); reason = str(data.get("reason") or "").strip()
    if not isinstance(ids, list) or not ids: raise HTTPException(400, "ids 必须是非空数组")
    if target not in PROCESSING_STATUSES - {"exported"}: raise HTTPException(400, "目标状态无效")
    if target in {"excluded", "pending_entry", "uploaded_to_jiuzhang"} and user.role != "admin":
        raise HTTPException(403, "只有管理员可以执行排除、恢复或九章上传操作")
    if target in {"excluded", "pending_entry"} and not reason: raise HTTPException(400, "排除或恢复状态必须填写原因")
    ids = [str(item) for item in ids]; rows = _resolve_entries(db, user, ids, "all" if user.role == "admin" else "mine")
    results = []
    for item_id in ids:
        context = rows.get(item_id)
        if not context:
            results.append({"id": item_id, "success": False, "reason": "无权访问或记录不存在"}); continue
        current, success, detail = _set_status(db, context[0], user, target, reason)
        results.append({"id": item_id, "success": success, "from_status": current, "to_status": target, "reason": detail or reason})
    db.commit()
    return {"success_count": sum(row["success"] for row in results), "failed_count": sum(not row["success"] for row in results), "results": results}


@router.post("/export.csv")
def export_pool_csv(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = data.get("ids") or []
    if not isinstance(ids, list) or not ids: raise HTTPException(400, "请选择一个 QA")
    ids = [str(item) for item in ids]; rows = _resolve_entries(db, user, ids, "all" if user.role == "admin" else "mine")
    eligible, skipped = [], []
    for item_id in ids:
        context = rows.get(item_id)
        if not context: skipped.append({"id": item_id, "reason": "无权访问或记录不存在"}); continue
        entry = context[0]
        if not entry.question or not entry.answer: skipped.append({"id": item_id, "reason": "问题或答案为空"}); continue
        if entry.processing_status == "excluded": skipped.append({"id": item_id, "reason": "记录已排除"}); continue
        eligible.append((item_id, context))
    if not eligible: raise HTTPException(400, "所选记录没有符合导出条件的 QA")
    output = io.StringIO(newline=""); writer = csv.writer(output, lineterminator="\r\n")
    # Downstream still expects three category columns. Level-1 is the reviewed
    # knowledge category (知识分类), never the hardcoded label "知识库".
    writer.writerow(["问题", "答案", "一级类别", "二级类别", "三级类别"])
    for _, context in eligible:
        entry, source, slice_row, assignment, is_reviewer = context
        serialized = _serialize(entry, source, slice_row, assignment, db, is_reviewer)
        writer.writerow([entry.question, entry.answer, serialized.get("category") or "", "", ""])
    now = utcnow()
    for _, context in eligible:
        entry = context[0]
        if entry.processing_status != "exported": _set_status(db, entry, user, "exported", "导出 QA", "knowledge_pool_exported")
    db.commit()
    response = Response(content=output.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="knowledge_qa_{now.strftime("%Y%m%d_%H%M%S")}.csv"'
    response.headers["X-QA-Skipped-Count"] = str(len(skipped)); response.headers["X-QA-Skipped-Ids"] = ",".join(str(x["id"]) for x in skipped)
    return response


@router.get("/{pool_id}/logs")
def list_pool_logs(pool_id: str, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    entry = db.query(QAPoolEntry).filter(QAPoolEntry.pool_id == pool_id).first()
    if not entry: raise HTTPException(404, "QA 条目不存在")
    record_audit(db, "view_logs", "qa_pool_entry", pool_id, user=user); db.commit()
    rows = db.query(QAPoolStatusLog).filter(QAPoolStatusLog.pool_entry_id == entry.id).order_by(QAPoolStatusLog.id).all()
    return [{"id": row.id, "pool_id": pool_id, "action": row.action, "from_status": row.from_status,
             "to_status": row.to_status, "operator_id": row.operator_id, "operator_name": row.operator_name or "",
             "reason": row.reason or "", "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]
