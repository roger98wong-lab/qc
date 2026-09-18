"""Review task assignment workflow for analysed QC issues and KB suggestions."""
from typing import Optional
import random

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from database import (
    User, QcSlice, QcSliceQualityIssue, QcSliceKnowledgeSuggestion,
    ReviewAssignment, ReviewAssignmentLog, get_db, utcnow,
)
from audit import record_audit

router = APIRouter(prefix="/api/review-assignments", tags=["review assignments"])
ACTIVE = ("pending", "in_progress", "returned")
ITEM_TYPES = {"quality_issue", "knowledge_suggestion"}
QUALITY_DECISIONS = frozenset({"rejected", "needs_review", "confirmed"})
KB_DECISIONS = frozenset({"rejected", "needs_edit", "approved"})


def _reviewers(db: Session):
    return db.query(User).filter(User.is_active.is_(True), User.role.in_(["admin", "analyst"])).order_by(User.id).all()


def _context(db: Session, item_type: str, item_id: int):
    if item_type == "quality_issue":
        item = db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.id == item_id).first()
        parent = db.query(QcSlice).filter(QcSlice.id == item.slice_id).first() if item else None
        return item, parent
    if item_type == "knowledge_suggestion":
        item = db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.id == item_id).first()
        parent = db.query(QcSlice).filter(QcSlice.id == item.slice_id).first() if item else None
        if item and item.decision in {"no_human_answer", "reject", "not_candidate"}:
            return None, None
        return item, parent
    return None, None


def _serialize(db: Session, row: ReviewAssignment):
    item, parent = _context(db, row.item_type, row.item_id)
    assignee = db.query(User).filter(User.id == row.assignee_id).first()
    assigned_by = db.query(User).filter(User.id == row.assigned_by).first()
    out = {
        "id": row.id,
        "batch_id": row.batch_id,
        "item_type": row.item_type,
        "item_id": row.item_id,
        "assignee_id": row.assignee_id,
        "assignee": assignee.username if assignee else None,
        "assigned_by": assigned_by.username if assigned_by else None,
        "status": row.status,
        "assigned_at": row.assigned_at.isoformat() if row.assigned_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "claim_source": row.claim_source,
        "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
        "review_comment": row.review_comment,
        "review_decision": row.review_decision,
        "returned_reason": row.returned_reason,
        "available": bool(item and parent and parent.analysis_status in {"completed", "partial", "done"}),
    }
    if parent:
        out.update({
            "slice_id": parent.slice_id,
            "slice_db_id": parent.id,
            "channel": parent.channel,
            "game": parent.game,
            "region": parent.region,
            "analysis_status": parent.analysis_status,
        })
    if row.item_type == "quality_issue" and item:
        out.update({
            "issue_type": item.issue_type,
            "severity": item.severity,
            "confidence": item.confidence,
            "needs_manual_review": bool(item.needs_manual_review),
        })
    elif item:
        out.update({
            "decision": item.decision,
            "confidence": item.confidence,
            "title": item.title,
            "answer_source": item.answer_source,
        })
    return out


def _log(db: Session, row: ReviewAssignment, action: str, user, note=None, old=None, new=None):
    db.add(ReviewAssignmentLog(
        assignment_id=row.id, action=action, operator_id=user.id,
        from_user_id=old, to_user_id=new, note=note,
        operator_role=user.role, target_type="review_assignment", target_id=str(row.id),
        from_value=str(old) if old is not None else None,
        to_value=str(new) if new is not None else None, reason=note,
    ))
    record_audit(
        db, action, "review_assignment", row.id, user=user,
        from_value=str(old) if old is not None else None,
        to_value=str(new) if new is not None else None, reason=note,
    )


def _find_active(db: Session, item_type: str, item_id: int):
    return db.query(ReviewAssignment).filter(
        ReviewAssignment.item_type == item_type,
        ReviewAssignment.item_id == item_id,
        ReviewAssignment.status.in_(ACTIVE),
    ).first()


@router.post("/claim")
def claim(data: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Atomically claim an unassigned item with a persistent processing lock."""
    if user.role not in {"admin", "analyst"}:
        raise HTTPException(403, "无权认领")
    try:
        item_type = str(data.get("item_type") or "")
        item_id = int(data.get("item_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "item_type 和 item_id 参数无效")
    item, parent = _context(db, item_type, item_id)
    if not item or not parent:
        raise HTTPException(404, "审核对象不存在")
    active = db.query(ReviewAssignment).with_for_update().filter(
        ReviewAssignment.item_type == item_type,
        ReviewAssignment.item_id == item_id,
        ReviewAssignment.status.in_(ACTIVE),
    ).first()
    if active:
        if active.assignee_id == user.id:
            return _serialize(db, active)
        raise HTTPException(409, "该问题已被其他人锁定")
    completed = db.query(ReviewAssignment).filter(
        ReviewAssignment.item_type == item_type,
        ReviewAssignment.item_id == item_id,
        ReviewAssignment.status == "completed",
    ).first()
    if completed:
        raise HTTPException(409, "该问题已处理完成")
    now = utcnow()
    row = ReviewAssignment(
        batch_id=parent.batch_id, item_type=item_type, item_id=item_id,
        assignee_id=user.id, assigned_by=user.id, status="in_progress",
        started_at=now, assigned_at=now, claim_source="self_claim", claimed_at=now,
    )
    db.add(row)
    db.flush()
    _log(db, row, "claimed", user, note="自助认领", new=user.id)
    db.commit()
    return _serialize(db, row)


@router.get("")
def list_assignments(
    batch_id: Optional[int] = None, item_type: Optional[str] = None, status: Optional[str] = None,
    assignee_id: Optional[int] = None, current_user=Depends(get_current_user), db: Session = Depends(get_db),
):
    q = db.query(ReviewAssignment)
    if current_user.role != "admin":
        q = q.filter(ReviewAssignment.assignee_id == current_user.id)
    if batch_id:
        q = q.filter(ReviewAssignment.batch_id == batch_id)
    if item_type:
        q = q.filter(ReviewAssignment.item_type == item_type)
    if status:
        q = q.filter(ReviewAssignment.status == status)
    if assignee_id:
        q = q.filter(ReviewAssignment.assignee_id == assignee_id)
    return [_serialize(db, x) for x in q.order_by(ReviewAssignment.id.desc()).limit(500).all()]


@router.get("/mine")
def mine(status: Optional[str] = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ReviewAssignment).filter(ReviewAssignment.assignee_id == user.id)
    if status:
        q = q.filter(ReviewAssignment.status == status)
    return [_serialize(db, x) for x in q.order_by(ReviewAssignment.id.desc()).limit(500).all()]


@router.get("/reviewers")
def reviewers(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": x.id, "username": x.username, "role": x.role, "is_active": x.is_active} for x in _reviewers(db)]


@router.post("/assign")
def assign(data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "items must be a non-empty list")
    strategy = data.get("strategy") or "manual"
    if strategy not in {"manual", "round_robin", "least_load", "random"}:
        raise HTTPException(400, "unsupported strategy")
    people = _reviewers(db)
    if not people:
        raise HTTPException(400, "no active reviewer available")
    try:
        aid = int(data.get("assignee_id")) if data.get("assignee_id") is not None else None
    except (TypeError, ValueError):
        aid = None
    if strategy == "manual":
        if aid is None:
            raise HTTPException(400, "assignee_id is required")
        if not any(x.id == aid for x in people):
            raise HTTPException(400, "invalid assignee")
    created, skipped, idx = [], [], 0
    loads = {
        x.id: db.query(ReviewAssignment).filter(
            ReviewAssignment.assignee_id == x.id,
            ReviewAssignment.status.in_(("pending", "in_progress")),
        ).count()
        for x in people
    }
    for raw in items:
        try:
            typ = str(raw.get("item_type"))
            iid = int(raw.get("item_id"))
        except (TypeError, ValueError, AttributeError):
            skipped.append({"item": raw, "reason": "invalid item"})
            continue
        item, parent = _context(db, typ, iid)
        if not item or not parent or typ not in ITEM_TYPES:
            skipped.append({"item_type": typ, "item_id": iid, "reason": "item unavailable"})
            continue
        if _find_active(db, typ, iid):
            skipped.append({"item_type": typ, "item_id": iid, "reason": "already assigned"})
            continue
        if strategy == "manual":
            target = aid
        elif strategy == "round_robin":
            target = people[idx % len(people)].id
            idx += 1
        elif strategy == "random":
            target = random.choice(people).id
        else:
            target = min(people, key=lambda x: (loads.get(x.id, 0), x.id)).id
            loads[target] = loads.get(target, 0) + 1
        row = ReviewAssignment(
            batch_id=parent.batch_id, item_type=typ, item_id=iid, assignee_id=target,
            assigned_by=admin.id, status="pending", claim_source="admin_assignment",
        )
        db.add(row)
        db.flush()
        _log(db, row, "assigned", admin, new=target)
        created.append(_serialize(db, row))
    db.commit()
    return {"created": created, "created_count": len(created), "skipped": skipped}


def _owned(db: Session, aid: int, user: User):
    row = db.query(ReviewAssignment).filter(ReviewAssignment.id == aid).first()
    if not row or (user.role != "admin" and row.assignee_id != user.id):
        raise HTTPException(404, "assignment not found")
    return row


@router.post("/{assignment_id}/reassign")
def reassign(assignment_id: int, data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if not row or row.status in {"completed", "cancelled"}:
        raise HTTPException(404, "assignment is not reassignable")
    try:
        target_id = int(data.get("assignee_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "assignee_id is required")
    target = db.query(User).filter(User.id == target_id, User.is_active.is_(True), User.role.in_(["admin", "analyst"])).first()
    if not target:
        raise HTTPException(400, "invalid assignee")
    old = row.assignee_id
    row.assignee_id = target_id
    row.status = "pending"
    row.started_at = None
    row.returned_reason = None
    row.claim_source = "admin_assignment"
    row.claimed_at = None
    _log(db, row, "reassigned", admin, old=old, new=target_id)
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/cancel")
def cancel(assignment_id: int, data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if not row:
        raise HTTPException(404, "assignment not found")
    if row.status == "completed":
        raise HTTPException(400, "completed assignment cannot be cancelled")
    row.status = "cancelled"
    row.returned_reason = data.get("reason")
    _log(db, row, "cancelled", admin, note=data.get("reason"))
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/release")
def release(assignment_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned(db, assignment_id, user)
    if row.status not in ACTIVE:
        raise HTTPException(400, "该问题当前没有可释放的处理锁")
    previous = row.assignee_id
    row.status = "cancelled"
    row.returned_reason = "本人主动释放"
    _log(db, row, "released", user, old=previous)
    record_audit(db, "released", row.item_type, row.item_id, user=user, from_value=str(previous), to_value="unassigned", reason="本人主动释放")
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/force-release")
def force_release(assignment_id: int, data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if not row:
        raise HTTPException(404, "assignment not found")
    if row.status not in ACTIVE:
        raise HTTPException(400, "该问题当前没有可释放的处理锁")
    reason = str(data.get("reason") or "").strip() or "管理员强制释放"
    previous = row.assignee_id
    row.status = "cancelled"
    row.returned_reason = reason
    _log(db, row, "force_released", admin, old=previous)
    record_audit(db, "force_released", row.item_type, row.item_id, user=admin, from_value=str(previous), to_value="unassigned", reason=reason)
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/start")
def start(assignment_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned(db, assignment_id, user)
    if row.status not in {"pending", "returned"}:
        raise HTTPException(400, "assignment cannot be started")
    row.status = "in_progress"
    row.started_at = utcnow()
    _log(db, row, "started", user)
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/submit")
def submit(assignment_id: int, data: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned(db, assignment_id, user)
    if row.status not in ACTIVE:
        raise HTTPException(400, "assignment cannot be submitted")
    decision = str(data.get("decision") or "")
    allowed = QUALITY_DECISIONS if row.item_type == "quality_issue" else KB_DECISIONS
    if decision not in allowed:
        raise HTTPException(400, "invalid review decision")
    row.review_decision = decision
    row.review_comment = data.get("comment")
    row.status = "completed"
    row.completed_at = utcnow()
    _log(db, row, "submitted", user)
    db.commit()
    return _serialize(db, row)


@router.post("/{assignment_id}/return")
def return_task(assignment_id: int, data: dict, user=Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned(db, assignment_id, user)
    reason = str(data.get("reason") or "").strip()
    if row.status not in {"pending", "in_progress"}:
        raise HTTPException(400, "assignment cannot be returned")
    if not reason:
        raise HTTPException(400, "return reason is required")
    row.status = "returned"
    row.returned_reason = reason
    _log(db, row, "returned", user, note=reason)
    db.commit()
    return _serialize(db, row)


@router.get("/{assignment_id}/logs")
def logs(assignment_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(ReviewAssignment).filter(ReviewAssignment.id == assignment_id).first()
    if not row:
        raise HTTPException(404, "assignment not found")
    record_audit(db, "view_logs", "review_assignment", assignment_id, user=admin)
    db.commit()
    rows = db.query(ReviewAssignmentLog).filter(ReviewAssignmentLog.assignment_id == assignment_id).order_by(ReviewAssignmentLog.id).all()
    return [{
        "id": x.id, "assignment_id": x.assignment_id, "action": x.action,
        "operator_id": x.operator_id, "from_user_id": x.from_user_id, "to_user_id": x.to_user_id,
        "note": x.note, "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in rows]
