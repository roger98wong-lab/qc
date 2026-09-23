"""Small shared helper for security-relevant audit records.

Values are intentionally caller-supplied summaries; callers must never pass
passwords, bearer tokens, API keys, or raw conversation contents.
"""
from datetime import datetime, timedelta
from typing import Optional
import re

from sqlalchemy import desc
from sqlalchemy.orm import Session

from database import AuditLog, User, utcnow

AUDIT_WINDOW = timedelta(days=30)
_SENSITIVE_RE = re.compile(
    r"(password|passwd|secret|token|bearer|api[_-]?key|app[_-]?key|authorization)",
    re.IGNORECASE,
)
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9._-]{10,}")

ACTION_LABELS = {
    "login_succeeded": "登录成功",
    "login_failed": "登录失败",
    "access_denied": "权限拒绝",
    "released": "释放",
    "force_released": "强制释放",
    "submitted": "提交审核",
    "view_logs": "查看审核日志",
    "claimed": "认领",
    "assigned": "分派",
    "reassigned": "改派",
    "cancelled": "取消分派",
    "started": "开始处理",
    "returned": "退回",
    "review_status_changed": "审核状态变更",
    "saved": "保存审核",
    "approve": "通过审核",
    "reject": "不通过",
    "qa_added": "新增 QA",
    "qa_deleted": "删除 QA",
    "qa_modified": "修改 QA",
    "knowledge_pool_status_changed": "知识库状态变更",
    "knowledge_pool_exported": "导出知识库 QA",
    "export_logs": "导出操作日志",
    "apply_game_product_mappings": "回写游戏产品ID映射",
    "created": "新增",
    "updated": "更新",
    "deleted": "删除",
    "password_reset": "重置密码",
    "password_changed": "修改密码",
    "disable_scheduled": "安排停用",
    "enabled": "启用账号",
}

TARGET_TYPE_LABELS = {
    "user": "用户",
    "auth": "登录",
    "admin_endpoint": "管理接口",
    "review_assignment": "审核任务",
    "review_assignment_not_owned": "未授权审核任务",
    "quality_issue": "质检问题",
    "knowledge_suggestion": "知识库建议",
    "qa_pool_entry": "知识库条目",
    "audit_logs": "操作日志",
    "mapping_config": "地区渠道映射",
    "game_ai_config": "游戏AI分析配置",
}


class AuditQueryError(ValueError):
    pass


def record_audit(db, action: str, target_type: str, target_id: Optional[object] = None,
                 user=None, from_value: Optional[str] = None, to_value: Optional[str] = None,
                 reason: Optional[str] = None, request_id: Optional[str] = None):
    db.add(AuditLog(
        operator_id=getattr(user, "id", None), operator_role=getattr(user, "role", None),
        action=action, target_type=target_type, target_id=str(target_id) if target_id is not None else None,
        from_value=from_value, to_value=to_value, reason=reason, request_id=request_id,
    ))


def action_label(action: Optional[str]) -> str:
    value = str(action or "").strip()
    return ACTION_LABELS.get(value, value)


def target_type_label(target_type: Optional[str]) -> str:
    value = str(target_type or "").strip()
    return TARGET_TYPE_LABELS.get(value, value)


def target_label(target_type: Optional[str], target_id: Optional[str]) -> str:
    type_text = target_type_label(target_type)
    identity = str(target_id).strip() if target_id is not None and str(target_id).strip() else ""
    if identity:
        return f"{type_text} {identity}".strip()
    return type_text or "—"


def sanitize_audit_text(value: Optional[str], limit: int = 120) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if _SENSITIVE_RE.search(text) or _JWT_RE.search(text):
        return "[已隐藏]"
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value


def resolve_audit_window(start: Optional[datetime], end: Optional[datetime], now: Optional[datetime] = None):
    now = _naive(now or utcnow())
    earliest = now - AUDIT_WINDOW
    if start is None and end is None:
        return earliest, now
    start = _naive(start) if start is not None else earliest
    end = _naive(end) if end is not None else now
    if start > end:
        raise AuditQueryError("开始时间不能晚于结束时间")
    if start < earliest or end < earliest:
        raise AuditQueryError("查询时间不能早于近 30 天")
    if end - start > AUDIT_WINDOW:
        raise AuditQueryError("查询时间范围不能超过 30 天")
    return start, end


def audit_log_query(db: Session, start: datetime, end: datetime, operator_id: Optional[int] = None,
                    action: Optional[str] = None, target_type: Optional[str] = None):
    query = db.query(AuditLog).filter(AuditLog.created_at >= start, AuditLog.created_at <= end)
    if operator_id is not None:
        query = query.filter(AuditLog.operator_id == operator_id)
    if action:
        query = query.filter(AuditLog.action == action)
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)
    return query.order_by(desc(AuditLog.created_at), desc(AuditLog.id))


def serialize_audit_log(row: AuditLog, username: Optional[str] = None) -> dict:
    created = row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else None
    operator_name = username or "系统/未知"
    return {
        "id": row.id,
        "created_at": created,
        "operator_id": row.operator_id,
        "operator_name": operator_name,
        "operator_role": row.operator_role,
        "action": row.action,
        "action_label": action_label(row.action),
        "target_type": row.target_type,
        "target_type_label": target_type_label(row.target_type),
        "target_id": row.target_id,
        "target_label": target_label(row.target_type, row.target_id),
        "from_value": sanitize_audit_text(row.from_value),
        "to_value": sanitize_audit_text(row.to_value),
        "reason": sanitize_audit_text(row.reason),
    }


def serialize_audit_logs(db: Session, rows) -> list:
    user_ids = {row.operator_id for row in rows if row.operator_id}
    names = {}
    if user_ids:
        names = {user.id: user.username for user in db.query(User).filter(User.id.in_(user_ids)).all()}
    return [serialize_audit_log(row, names.get(row.operator_id)) for row in rows]
