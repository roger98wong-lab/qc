"""Small shared helper for security-relevant audit records.

Values are intentionally caller-supplied summaries; callers must never pass
passwords, bearer tokens, API keys, or raw conversation contents.
"""
from typing import Optional

from database import AuditLog


def record_audit(db, action: str, target_type: str, target_id: Optional[object] = None,
                 user=None, from_value: Optional[str] = None, to_value: Optional[str] = None,
                 reason: Optional[str] = None, request_id: Optional[str] = None):
    db.add(AuditLog(
        operator_id=getattr(user, "id", None), operator_role=getattr(user, "role", None),
        action=action, target_type=target_type, target_id=str(target_id) if target_id is not None else None,
        from_value=from_value, to_value=to_value, reason=reason, request_id=request_id,
    ))
