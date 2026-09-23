from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from auth import (
    account_blocked, account_status, create_access_token, get_current_user,
    hash_password, require_admin, verify_password, DISABLE_GRACE,
)
from database import User, get_db, utcnow
from audit import record_audit
from routers.assignments import force_release_user_open_assignments

router = APIRouter(prefix="/api/auth", tags=["认证"])


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str = "analyst"


class UserUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    role: str | None = None


def _norm_username(value) -> str:
    return str(value or "").strip()


def _norm_email(value) -> str:
    return str(value or "").strip()


def serialize_user(user: User, now=None) -> dict:
    now = now or utcnow()
    status = account_status(user, now)
    effective = user.disable_effective_at
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": status != "disabled",
        "status": status,
        "disable_effective_at": effective.isoformat() if effective else None,
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if getattr(user, "updated_at", None) else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


def _user_or_404(db: Session, user_id: int) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return user


def _username_conflict(db: Session, username: str, exclude_id: int | None = None) -> bool:
    query = db.query(User).filter(User.username == username)
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    return query.first() is not None


def _email_conflict(db: Session, email: str, exclude_id: int | None = None) -> bool:
    query = db.query(User).filter(User.email == email)
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    return query.first() is not None


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter((User.username == form.username) | (User.email == form.username)).first()
    except OperationalError as exc:
        if "locked" in str(exc).lower():
            raise HTTPException(503, "系统正在处理批量任务，请稍后重试登录") from exc
        raise
    if not user or not verify_password(form.password, user.hashed_pwd):
        record_audit(db, "login_failed", "auth", reason="invalid_credentials")
        db.commit()
        raise HTTPException(401, "用户名或密码错误")
    if account_blocked(user):
        record_audit(db, "login_failed", "user", user.id, user=user, reason="account_disabled")
        db.commit()
        raise HTTPException(403, "账号已停用")
    user.last_login = utcnow()
    payload = {
        "access_token": create_access_token({"sub": str(user.id)}),
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "status": account_status(user),
    }
    try:
        record_audit(db, "login_succeeded", "user", user.id, user=user)
        db.commit()
    except Exception:
        db.rollback()
    return payload


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return serialize_user(user)


@router.post("/change-password")
def change_password(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(str(data.get("old_password") or ""), user.hashed_pwd):
        raise HTTPException(400, "原密码错误")
    password = str(data.get("new_password") or "")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    user.hashed_pwd = hash_password(password)
    user.must_change_password = False
    user.updated_at = utcnow()
    record_audit(db, "password_changed", "user", user.id, user=user, reason="本人修改密码")
    db.commit()
    return {"message": "密码修改成功"}


@router.get("/users")
def users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [serialize_user(user) for user in db.query(User).order_by(User.id).all()]


@router.post("/users")
def create_user(data: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    username = _norm_username(data.username)
    email = _norm_email(data.email)
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if not email:
        raise HTTPException(400, "邮箱不能为空")
    if _username_conflict(db, username):
        raise HTTPException(409, "用户名已存在，建议加 123")
    if _email_conflict(db, email):
        raise HTTPException(400, "用户名或邮箱已存在")
    if len(data.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    role = data.role if data.role in {"admin", "analyst"} else "analyst"
    user = User(
        username=username, email=email, hashed_pwd=hash_password(data.password),
        role=role, is_active=True, must_change_password=False,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在，建议加 123")
    record_audit(
        db, "created", "user", user.id, user=admin,
        to_value=f"{user.username}/{user.email}/{user.role}", reason="新增用户",
    )
    db.commit(); db.refresh(user)
    return serialize_user(user)


@router.patch("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    before = f"{user.username}/{user.email}/{user.role}"
    role_changed = False
    if data.username is not None:
        username = _norm_username(data.username)
        if not username:
            raise HTTPException(400, "用户名不能为空")
        if username != user.username and _username_conflict(db, username, user.id):
            raise HTTPException(409, "用户名已存在，建议加 123")
        user.username = username
    if data.email is not None:
        email = _norm_email(data.email)
        if not email:
            raise HTTPException(400, "邮箱不能为空")
        if email != user.email and _email_conflict(db, email, user.id):
            raise HTTPException(400, "邮箱已存在")
        user.email = email
    if data.role is not None:
        role = str(data.role).strip()
        if role not in {"admin", "analyst"}:
            raise HTTPException(400, "角色只能是 admin 或 analyst")
        if role != user.role:
            user.role = role
            role_changed = True
    user.updated_at = utcnow()
    released = 0
    if role_changed:
        released = force_release_user_open_assignments(
            db, user.id, admin, reason=f"用户 {user.username} 角色变更为 {user.role}，释放未完成审核任务",
        )
    after = f"{user.username}/{user.email}/{user.role}"
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在，建议加 123")
    record_audit(db, "updated", "user", user.id, user=admin, from_value=before, to_value=after, reason="编辑用户")
    db.commit(); db.refresh(user)
    payload = serialize_user(user)
    payload["released_assignments"] = released
    payload["relogin_required"] = bool(role_changed and user.id == admin.id)
    return payload


@router.delete("/users/{user_id}")
def delete_user(user_id: int, data: dict | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    reason = str((data or {}).get("reason") or "").strip() or None
    before = f"{user.username}/{user.email}/{user.role}"
    released = force_release_user_open_assignments(
        db, user.id, admin, reason=f"删除用户 {user.username}，释放未完成审核任务",
    )
    deleting_self = user.id == admin.id
    from database import SystemDictionary, ReviewAssignmentLog
    db.query(SystemDictionary).filter(SystemDictionary.created_by == user.id).update({SystemDictionary.created_by: None}, synchronize_session=False)
    db.query(SystemDictionary).filter(SystemDictionary.updated_by == user.id).update({SystemDictionary.updated_by: None}, synchronize_session=False)
    db.query(ReviewAssignmentLog).filter(ReviewAssignmentLog.from_user_id == user.id).update({ReviewAssignmentLog.from_user_id: None}, synchronize_session=False)
    db.query(ReviewAssignmentLog).filter(ReviewAssignmentLog.to_user_id == user.id).update({ReviewAssignmentLog.to_user_id: None}, synchronize_session=False)
    record_audit(db, "deleted", "user", user.id, user=admin, from_value=before, reason=reason or "删除用户")
    db.delete(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "该用户仍被不可置空的历史记录引用，无法删除")
    return {"message": "用户已删除", "id": user_id, "self": deleting_self, "released_assignments": released}


@router.patch("/users/{user_id}/toggle")
def toggle_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    now = utcnow()
    status_before = account_status(user, now)
    if status_before == "active":
        user.is_active = True
        user.disable_effective_at = now + DISABLE_GRACE
        action = "disable_scheduled"
        message = f"已安排停用，将于 {user.disable_effective_at.strftime('%H:%M')} 生效"
    else:
        user.is_active = True
        user.disable_effective_at = None
        action = "enabled"
        message = "账号已启用"
    user.updated_at = now
    record_audit(
        db, action, "user", user.id, user=admin,
        from_value=status_before, to_value=account_status(user, now), reason=message,
    )
    db.commit(); db.refresh(user)
    payload = serialize_user(user, now)
    payload["message"] = message
    return payload


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, data: dict, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _user_or_404(db, user_id)
    password = str(data.get("new_password") or "")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    user.hashed_pwd = hash_password(password)
    user.must_change_password = True
    user.updated_at = utcnow()
    record_audit(db, "password_reset", "user", user.id, user=admin, reason="管理员重置密码，下次登录须改密")
    db.commit()
    return {"message": "密码已重置，用户下次登录必须先修改密码", "must_change_password": True}
