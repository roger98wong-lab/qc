"""Dictionary API with explicit permissions and historic-value protection."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import get_current_user, require_admin
from database import QcSliceKnowledgeSuggestion, QcSliceQualityIssue, SystemDictionary, User, get_db
from services.dictionaries import GROUP_LABELS

router = APIRouter(tags=["dictionaries"])


def _ordered(query):
    return query.order_by(SystemDictionary.sort_order.asc(), SystemDictionary.created_at.asc())


def _serialize(row, references=0):
    return {
        "id": row.id, "group": row.group_key, "group_label": GROUP_LABELS[row.group_key],
        "value": row.value, "display_name": row.display_name, "sort_order": row.sort_order,
        "enabled": bool(row.enabled), "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "created_by": row.created_by, "updated_by": row.updated_by, "reference_count": references,
    }


def _assert_group(group_key: str):
    if group_key not in GROUP_LABELS:
        raise HTTPException(400, "不支持的配置组")


def _reference_count(db: Session, row: SystemDictionary) -> int:
    value = row.value
    if row.group_key == "knowledge_category":
        return db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.human_category == value).count() + db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.human_category == value).count()
    if row.group_key == "knowledge_base":
        return db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.human_knowledge_base == value).count() + db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.human_knowledge_base == value).count()
    if row.group_key == "issue_type":
        return db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.human_issue_type == value).count()
    if row.group_key == "issue_tag":
        # JSON values are stored as text in the existing SQLite schema.
        return db.query(QcSliceKnowledgeSuggestion).filter(QcSliceKnowledgeSuggestion.human_tags_json.like(f'%"{value}"%')).count() + db.query(QcSliceQualityIssue).filter(QcSliceQualityIssue.human_tags_json.like(f'%"{value}"%')).count()
    return 0


@router.get("/api/dictionaries")
def list_active_dictionaries(group: str = Query(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _assert_group(group)
    rows = _ordered(db.query(SystemDictionary).filter(SystemDictionary.group_key == group, SystemDictionary.enabled.is_(True))).all()
    return [_serialize(row) for row in rows]


@router.get("/api/admin/dictionaries")
def list_dictionaries(group: str | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if group is not None:
        _assert_group(group)
    query = db.query(SystemDictionary)
    if group:
        query = query.filter(SystemDictionary.group_key == group)
    return [_serialize(row, _reference_count(db, row)) for row in _ordered(query).all()]


@router.post("/api/admin/dictionaries")
def create_dictionary(data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    group = str(data.get("group") or "").strip()
    value = str(data.get("value") or "").strip()
    display_name = str(data.get("display_name") or value).strip()
    _assert_group(group)
    if not value or not display_name:
        raise HTTPException(400, "配置项值和展示名称不能为空")
    try:
        sort_order = int(data.get("sort_order", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "排序号必须是整数")
    row = SystemDictionary(group_key=group, value=value, display_name=display_name, sort_order=sort_order,
                           enabled=bool(data.get("enabled", True)), created_by=admin.id, updated_by=admin.id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一配置组内的配置项值不能重复")
    db.refresh(row)
    return _serialize(row)


@router.patch("/api/admin/dictionaries/{dictionary_id}")
def update_dictionary(dictionary_id: int, data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SystemDictionary).filter(SystemDictionary.id == dictionary_id).first()
    if not row:
        raise HTTPException(404, "配置项不存在")
    if "group" in data and data["group"] != row.group_key:
        raise HTTPException(400, "配置项创建后不能更改配置组")
    new_value = str(data.get("value", row.value) or "").strip()
    if not new_value:
        raise HTTPException(400, "配置项值不能为空")
    if new_value != row.value and _reference_count(db, row):
        raise HTTPException(409, "该配置项已被历史记录使用，不能修改配置项值；可修改展示名称、排序或停用")
    row.value = new_value
    if "display_name" in data:
        row.display_name = str(data.get("display_name") or "").strip() or row.value
    if "sort_order" in data:
        try: row.sort_order = int(data["sort_order"])
        except (TypeError, ValueError): raise HTTPException(400, "排序号必须是整数")
    if "enabled" in data:
        row.enabled = bool(data["enabled"])
    row.updated_by = admin.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一配置组内的配置项值不能重复")
    db.refresh(row)
    return _serialize(row, _reference_count(db, row))


@router.post("/api/admin/dictionaries/{dictionary_id}/disable")
def disable_dictionary(dictionary_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SystemDictionary).filter(SystemDictionary.id == dictionary_id).first()
    if not row:
        raise HTTPException(404, "配置项不存在")
    row.enabled = False
    row.updated_by = admin.id
    db.commit()
    return _serialize(row, _reference_count(db, row))


@router.delete("/api/admin/dictionaries/{dictionary_id}")
def delete_dictionary(dictionary_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.query(SystemDictionary).filter(SystemDictionary.id == dictionary_id).first()
    if not row:
        raise HTTPException(404, "配置项不存在")
    references = _reference_count(db, row)
    if references:
        raise HTTPException(409, f"该配置项已被 {references} 条历史记录使用，不能删除；请改为停用")
    db.delete(row)
    db.commit()
    return {"message": "已删除"}
