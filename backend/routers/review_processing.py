"""Manual review processing fields for issue and knowledge detail drawers."""
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from database import (
    QcSlice, QcSliceKnowledgeSuggestion, QcSliceQualityIssue, ReviewAssignment,
    ReviewAssignmentLog, ReviewProcessingLog, User, get_db, utcnow,
)
from audit import record_audit
from services.dictionaries import ISSUE_TAG, ISSUE_TYPE, KNOWLEDGE_BASE, KNOWLEDGE_CATEGORY, active_values

router = APIRouter(prefix="/api/review-processing", tags=["review processing"])
ITEM_TYPES = {"quality_issue", "knowledge_suggestion"}
SCORES = frozenset({60, 70, 80, 90})
SEVERITIES = frozenset({"一般", "中级", "需人工复核", "严重"})
REVIEW_STATUSES = frozenset({"rejected", "needs_edit", "in_review", "approved", "pending_review"})
DESTINATIONS = frozenset({"转人工", "人工复核", "AI 逻辑优化", "无需处理", "知识库优化"})


def _json(value, fallback):
    try:
        parsed = json.loads(value) if value else fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _text(value):
    return str(value or "").strip()


def _applicable_scope_for_form(parent, human_scope):
    """Prefill knowledge-suggestion scope from saved human values, else the slice.

    Region stays empty when the slice has no region. Never invent a region from
    language, game, channel, or a MaaS applicable_scope of null.
    """
    human = human_scope if isinstance(human_scope, dict) else {}
    has_human = any(_text(human.get(field)) for field in ("channel", "game", "region"))
    if has_human:
        return {
            "channel": _text(human.get("channel")),
            "game": _text(human.get("game")),
            "region": _text(human.get("region")),
        }
    return {
        "channel": _text(getattr(parent, "channel", None)),
        "game": _text(getattr(parent, "game", None)),
        "region": _text(getattr(parent, "region", None)),
    }


def _require_scope(parsed_scope):
    if isinstance(parsed_scope, dict):
        channel, game, region = (_text(parsed_scope.get(field)) for field in ("channel", "game", "region"))
        if not region:
            raise HTTPException(400, "请填写地区")
        if not (channel and game):
            raise HTTPException(400, "请完整填写渠道、游戏和地区")
        return json.dumps({"channel": channel, "game": game, "region": region}, ensure_ascii=False)
    if parsed_scope == "":
        return None
    raise HTTPException(400, "适用范围格式无效")


def _pairs(value, fallback_questions=None, fallback_answer=""):
    """Return the stable editable QA-pair contract without mutating old data."""
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else []
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = []
    pairs = []
    if isinstance(parsed, list):
        for row in parsed:
            if isinstance(row, dict):
                question = str(row.get("question") or "").strip()
                answer = str(row.get("answer") or "").strip()
                if question or answer:
                    pairs.append({"question": question, "answer": answer})
    if pairs:
        return pairs
    questions = fallback_questions if isinstance(fallback_questions, list) else _json(fallback_questions, [])
    answer = str(fallback_answer or "")
    return [{"question": str(question).strip(), "answer": answer.strip()} for question in questions if str(question).strip()]


def _validate_pairs(value):
    if not isinstance(value, list):
        raise HTTPException(400, "人工 QA 对必须是数组")
    pairs = []
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            raise HTTPException(400, f"第 {index} 组 QA 对格式无效")
        question = str(row.get("question") or "").strip()
        answer = str(row.get("answer") or "").strip()
        if not question:
            raise HTTPException(400, f"第 {index} 组 QA 的问题不能为空")
        if not answer:
            raise HTTPException(400, f"第 {index} 组 QA 的答案不能为空")
        pairs.append({"question": question, "answer": answer})
    if not pairs:
        raise HTTPException(400, "至少需要保存一组完整 QA 对")
    return pairs


def _payload_pairs(data: dict):
    """Accept the current pair payload and the legacy editor payload during migration."""
    value = data.get("human_qa_pairs")
    if isinstance(value, list):
        return _validate_pairs(value)
    questions = data.get("human_standard_questions")
    answer = data.get("human_standard_answer") or ""
    if isinstance(questions, list):
        return _validate_pairs([{"question": question, "answer": answer} for question in questions])
    return None


def _audit_qa_changes(db, item_type, item_id, user, before, after):
    """Record pair-level lifecycle events without putting Q/A text in logs."""
    for index in range(max(len(before), len(after))):
        old_pair = before[index] if index < len(before) else None
        new_pair = after[index] if index < len(after) else None
        if old_pair is None and new_pair is not None:
            action, from_value, to_value = "qa_added", "absent", "present"
        elif old_pair is not None and new_pair is None:
            action, from_value, to_value = "qa_deleted", "present", "absent"
        elif old_pair != new_pair:
            action, from_value, to_value = "qa_modified", "present", "present"
        else:
            continue
        record_audit(db, action, item_type, f"{item_id}_qa_pair:{index}", user=user, from_value=from_value, to_value=to_value)


def _context(db: Session, item_type: str, item_id: int):
    if item_type == "quality_issue":
        item = db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.id == item_id).first()
    elif item_type == "knowledge_suggestion":
        item = db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.id == item_id).first()
    else:
        raise HTTPException(400, "不支持的审核对象类型")
    if not item:
        raise HTTPException(404, "审核对象不存在")
    parent = db.query(QcSlice).filter(QcSlice.id == item.slice_id).first()
    if not parent:
        raise HTTPException(404, "审核对象不存在")
    return item, parent


def _assignment(db: Session, item_type: str, item_id: int):
    return db.query(ReviewAssignment).filter(
        ReviewAssignment.item_type == item_type,
        ReviewAssignment.item_id == item_id,
    ).order_by(ReviewAssignment.id.desc()).first()


ACTIVE_ASSIGNMENT = frozenset({"pending", "in_progress", "returned"})
WRITABLE_ASSIGNMENT = ACTIVE_ASSIGNMENT | {"completed"}
POOL_KB_DECISIONS = frozenset({
    "candidate_ready", "candidate_needs_enrichment", "candidate_pending_feedback",
})
WASTE_KB_DECISIONS = frozenset({"not_candidate", "no_human_answer", "reject"})
KNOWLEDGE_OPTIMIZE_TAG = "知识库优化"


def _tag_list(item, data):
    if data.get("human_tags") is not None:
        return [str(tag) for tag in (data.get("human_tags") or [])]
    return [str(tag) for tag in _json(getattr(item, "human_tags_json", None), [])]



def _kb_entered_pool(item):
    """True only after an approved candidate has stored human QA."""
    if getattr(item, "human_review_status", None) == "rejected":
        return False
    if getattr(item, "decision", None) not in POOL_KB_DECISIONS:
        return False
    pairs = _pairs(item.human_qa_pairs, _json(item.human_standard_questions, []), item.human_standard_answer or "")
    return bool(pairs)


def _complete_assignment(db, assignment, user, decision, comment, now):
    """Mark an in-flight assignment completed. Already-completed rows stay put."""
    if not assignment:
        return False
    if assignment.status not in WRITABLE_ASSIGNMENT:
        return False
    previous = assignment.status
    already_completed = assignment.status == "completed" and assignment.review_decision == decision
    assignment.status = "completed"
    assignment.review_decision = decision
    assignment.review_comment = comment
    assignment.completed_at = assignment.completed_at or now
    if already_completed:
        return False
    db.add(ReviewAssignmentLog(
        assignment_id=assignment.id, action="submitted", operator_id=user.id,
        note=comment, operator_role=user.role,
        target_type="review_assignment", target_id=str(assignment.id),
        from_value=previous, to_value="completed", reason=comment,
    ))
    record_audit(db, "submitted", "review_assignment", assignment.id, user=user, from_value=previous, to_value="completed", reason=comment)
    return True


def _authorize(db: Session, item_type: str, item_id: int, user: User, require_lock: bool = False):
    item, parent = _context(db, item_type, item_id)
    assignment = _assignment(db, item_type, item_id)
    if not require_lock:
        if user.role != "admin":
            if not assignment or assignment.assignee_id != user.id:
                record_audit(db, "access_denied", "review_assignment_not_owned", item_id, user=user, reason=item_type)
                db.commit()
                raise HTTPException(404, "审核对象不存在")
        return item, parent, assignment
    if not assignment or assignment.status not in ACTIVE_ASSIGNMENT:
        raise HTTPException(403, "请先认领或分派后再审核")
    if assignment.assignee_id != user.id:
        if user.role == "admin":
            raise HTTPException(403, "请先认领或分派后再审核")
        record_audit(db, "access_denied", "review_assignment_not_owned", item_id, user=user, reason=item_type)
        db.commit()
        raise HTTPException(404, "审核对象不存在")
    return item, parent, assignment


def _serialize(item_type: str, item, parent, assignment, db: Session):
    out = {
        "item_type": item_type,
        "item_id": item.id,
        "slice_id": parent.slice_id,
        "batch_id": parent.batch_id,
        "human_tags": _json(item.human_tags_json, []),
        "human_review_status": item.human_review_status or "pending_review",
        "human_review_comment": item.human_review_comment or "",
        "human_updated_by": item.human_updated_by,
        "human_updated_at": item.human_updated_at.isoformat() if item.human_updated_at else None,
        "assignment_id": assignment.id if assignment else None,
        "assignment_status": assignment.status if assignment else "unassigned",
        "assignee_id": assignment.assignee_id if assignment else None,
        "assignee": None,
    }
    if assignment:
        person = db.query(User).filter(User.id == assignment.assignee_id).first()
        out["assignee"] = person.username if person else None
    if item_type == "quality_issue":
        out.update({
            "maas_issue_type": item.issue_type,
            "maas_severity": item.severity,
            "maas_confidence": item.confidence,
            "human_ai_score": item.human_ai_score,
            "human_issue_type": item.human_issue_type,
            "human_severity": item.human_severity,
            "human_processing_destination": item.human_processing_destination,
            "qa_source": item.qa_source,
            "human_standard_questions": _json(item.human_standard_questions, []),
            "human_standard_answer": item.human_standard_answer,
            "human_qa_pairs": _pairs(item.human_qa_pairs, _json(item.human_standard_questions, []), item.human_standard_answer or ""),
            "knowledge_base": item.human_knowledge_base,
            "human_category": item.human_category,
            "human_applicable_scope": _json(item.human_applicable_scope, {}),
            "knowledge_pool_status": item.knowledge_pool_status,
            "knowledge_pool_created_at": item.knowledge_pool_created_at.isoformat() if item.knowledge_pool_created_at else None,
            "knowledge_pool_created_by": item.knowledge_pool_created_by,
            "has_ai_answer": bool((_json(item.ai_answer_json, {}) or {}).get("text") or (_json(item.ai_answer_json, {}) or {}).get("original")),
            "entered_knowledge_pool": bool(item.knowledge_pool_status),
        })
    else:
        out.update({
            "answer_source": item.answer_source or "human_agent",
            "maas_decision": item.decision,
            "maas_category": item.category,
            "maas_standard_questions": _json(item.standard_questions, []),
            "maas_standard_answer": item.standard_answer,
            "qa_source": getattr(item, "qa_source", None),
            "human_standard_questions": _json(item.human_standard_questions, []),
            "human_standard_answer": item.human_standard_answer,
            "human_qa_pairs": _pairs(item.human_qa_pairs, _json(item.human_standard_questions, []), item.human_standard_answer or item.standard_answer or ""),
            "knowledge_base": item.human_knowledge_base,
            "human_category": item.human_category,
            "human_applicable_scope": _applicable_scope_for_form(parent, _json(item.human_applicable_scope, {})),
            "channel": _text(parent.channel),
            "game": _text(parent.game),
            "region": _text(parent.region),
            "maas_applicable_scope": _json(item.applicable_scope, {}),
            "entered_knowledge_pool": _kb_entered_pool(item),
        })
    return out


def _log(db: Session, item_type: str, item_id: int, user, action: str, from_status=None, to_status=None, reason=None):
    db.add(ReviewProcessingLog(
        item_type=item_type, item_id=item_id, action=action,
        from_status=from_status, to_status=to_status,
        operator_id=user.id, operator_name=user.username, reason=reason,
        operator_role=user.role, target_type=item_type, target_id=str(item_id),
        from_value=from_status, to_value=to_status,
    ))
    record_audit(db, action, item_type, item_id, user=user, from_value=from_status, to_value=to_status, reason=reason)


@router.get("/{item_type}/{item_id}")
def get_processing(item_type: str, item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item, parent, assignment = _authorize(db, item_type, item_id, user)
    return _serialize(item_type, item, parent, assignment, db)


@router.patch("/{item_type}/{item_id}")
def save_processing(item_type: str, item_id: int, data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item, parent, assignment = _authorize(db, item_type, item_id, user, require_lock=True)
    before_pairs = _pairs(item.human_qa_pairs, _json(item.human_standard_questions, []), item.human_standard_answer or getattr(item, "standard_answer", "") or "")
    tags = data.get("human_tags")
    if item_type == "quality_issue" and tags is not None:
        if not isinstance(tags, list) or any(str(tag) not in active_values(db, ISSUE_TAG) for tag in tags):
            raise HTTPException(400, "问题标签包含不支持的值")
        item.human_tags_json = json.dumps([str(tag) for tag in tags], ensure_ascii=False)
    action = str(data.get("review_action") or "approve").strip().lower()
    if action not in {"approve", "reject"}:
        raise HTTPException(400, "审核动作无效")
    reject = action == "reject"
    previous_review_status = item.human_review_status or "pending_review"
    next_status = "rejected" if reject else "approved"
    item.human_review_status = next_status
    if previous_review_status != next_status:
        _log(db, item_type, item_id, user, "review_status_changed", previous_review_status, next_status)
    if item_type == "quality_issue":
        score = data.get("human_ai_score")
        if score is not None:
            try:
                score = int(score)
            except (TypeError, ValueError):
                raise HTTPException(400, "AI 回复评分无效")
            if score not in SCORES:
                raise HTTPException(400, "AI 回复评分必须为 60、70、80 或 90")
            parsed = _json(item.ai_answer_json, {})
            if not (parsed.get("text") or parsed.get("original")):
                raise HTTPException(400, "AI 回复不存在时不能评分")
            item.human_ai_score = score
        issue_type = data.get("human_issue_type")
        if issue_type is not None:
            issue_type = str(issue_type).strip()
            if issue_type and issue_type not in active_values(db, ISSUE_TYPE):
                raise HTTPException(400, "问题类型无效")
            item.human_issue_type = issue_type or None
        severity = data.get("human_severity")
        if severity is not None:
            severity = str(severity).strip()
            if severity and severity not in SEVERITIES:
                raise HTTPException(400, "严重程度无效")
            item.human_severity = severity or None
        destination = data.get("human_processing_destination")
        if destination is not None:
            destination = str(destination).strip()
            if destination and destination not in DESTINATIONS:
                raise HTTPException(400, "处理去向无效")
            item.human_processing_destination = destination or None
    optimize = (not reject) and KNOWLEDGE_OPTIMIZE_TAG in _tag_list(item, data)
    pairs = None
    if (not reject) and (item_type == "knowledge_suggestion" or optimize):
        pairs = _payload_pairs(data)
        if pairs is None:
            raise HTTPException(400, "至少需要保存一组完整 QA 对")
        item.human_qa_pairs = json.dumps(pairs, ensure_ascii=False)
        item.human_standard_questions = json.dumps([row["question"] for row in pairs], ensure_ascii=False)
        item.human_standard_answer = pairs[0]["answer"] if pairs else None
        if item_type == "quality_issue":
            item.qa_source = "quality_reviewer"
        _audit_qa_changes(db, item_type, item_id, user, before_pairs, pairs)
    if (not reject) and item_type == "quality_issue" and optimize:
        base = data.get("human_knowledge_base")
        category = data.get("human_category")
        if base is not None:
            base = str(base).strip()
            if base and base not in active_values(db, KNOWLEDGE_BASE):
                raise HTTPException(400, "知识库选项无效")
            item.human_knowledge_base = base or None
        if category is not None:
            category = str(category).strip()
            if category and category not in active_values(db, KNOWLEDGE_CATEGORY):
                raise HTTPException(400, "知识分类无效")
            item.human_category = category or None
        parsed_scope = data.get("human_applicable_scope")
        if parsed_scope is not None:
            item.human_applicable_scope = _require_scope(parsed_scope)
        if not item.human_knowledge_base or not item.human_category:
            raise HTTPException(400, "请选择知识库选项和知识分类")
        if not item.knowledge_pool_status:
            item.knowledge_pool_status = "pending_entry"
            item.knowledge_pool_created_by = user.id
            item.knowledge_pool_created_at = utcnow()
    if (not reject) and item_type == "knowledge_suggestion":
        if item.decision in WASTE_KB_DECISIONS:
            raise HTTPException(400, "废案不能进入知识池")
        base = data.get("human_knowledge_base")
        category = data.get("human_category")
        if base is not None:
            base = str(base).strip()
            if base and base not in active_values(db, KNOWLEDGE_BASE):
                raise HTTPException(400, "知识库选项无效")
            item.human_knowledge_base = base or None
        if category is not None:
            category = str(category).strip()
            if category and category not in active_values(db, KNOWLEDGE_CATEGORY):
                raise HTTPException(400, "知识分类无效")
            item.human_category = category or None
        parsed_scope = data.get("human_applicable_scope")
        if parsed_scope is not None:
            item.human_applicable_scope = _require_scope(parsed_scope)
        if not item.human_knowledge_base or not item.human_category:
            raise HTTPException(400, "请选择知识库选项和知识分类")
    comment = data.get("human_review_comment")
    if comment is not None:
        item.human_review_comment = str(comment)
    now = utcnow()
    item.human_updated_by = user.id
    item.human_updated_at = now
    _log(db, item_type, item_id, user, "saved", item.human_review_status, item.human_review_status)
    if reject:
        decision = "rejected"
    else:
        decision = "confirmed" if item_type == "quality_issue" else "approved"
    _complete_assignment(db, assignment, user, decision, item.human_review_comment, now)
    db.commit()
    return _serialize(item_type, item, parent, assignment, db)


@router.get("/{item_type}/{item_id}/logs")
def processing_logs(item_type: str, item_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)):
    _context(db, item_type, item_id)
    record_audit(db, "view_logs", item_type, item_id, user=user)
    db.commit()
    rows = db.query(ReviewProcessingLog).filter(
        ReviewProcessingLog.item_type == item_type,
        ReviewProcessingLog.item_id == item_id,
    ).order_by(ReviewProcessingLog.id).all()
    return [{
        "id": row.id, "action": row.action, "from_status": row.from_status, "to_status": row.to_status,
        "operator_name": row.operator_name, "reason": row.reason or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]
