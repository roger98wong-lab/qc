from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from auth import create_access_token, get_current_user, hash_password, require_admin, verify_password
from database import User, get_db, utcnow
from audit import record_audit

router = APIRouter(prefix="/api/auth", tags=["认证"])

class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    role: str = "analyst"

@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    try:
        user = db.query(User).filter((User.username == form.username) | (User.email == form.username)).first()
    except OperationalError as exc:
        # Do not misreport a transient SQLite lock as bad credentials. The
        # client can retry, while the real cause remains visible in server logs.
        if "locked" in str(exc).lower():
            raise HTTPException(503, "系统正在处理批量任务，请稍后重试登录") from exc
        raise
    if not user or not verify_password(form.password, user.hashed_pwd):
        record_audit(db, "login_failed", "auth", reason="invalid_credentials")
        db.commit()
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        record_audit(db, "login_failed", "user", user.id, user=user, reason="account_disabled")
        db.commit()
        raise HTTPException(403, "账号已停用")
    user.last_login = utcnow()
    try:
        record_audit(db, "login_succeeded", "user", user.id, user=user)
        db.commit()
    except Exception:
        # Authentication must remain available if legacy SQLite storage is read-only.
        db.rollback()
    return {"access_token": create_access_token({"sub": str(user.id)}), "token_type": "bearer", "user_id": user.id, "username": user.username, "role": user.role}

@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return serialize_user(user)

@router.post("/change-password")
def change_password(data: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(str(data.get("old_password") or ""), user.hashed_pwd): raise HTTPException(400, "原密码错误")
    password = str(data.get("new_password") or "")
    if len(password) < 6: raise HTTPException(400, "密码至少 6 位")
    user.hashed_pwd = hash_password(password); db.commit()
    return {"message": "密码修改成功"}

@router.get("/users")
def users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [serialize_user(user) for user in db.query(User).order_by(User.id).all()]

@router.post("/users")
def create_user(data: UserCreate, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter((User.username == data.username) | (User.email == data.email)).first(): raise HTTPException(400, "用户名或邮箱已存在")
    if len(data.password) < 6: raise HTTPException(400, "密码至少 6 位")
    user = User(username=data.username, email=data.email, hashed_pwd=hash_password(data.password), role=data.role if data.role in {"admin", "analyst"} else "analyst")
    db.add(user); db.commit(); db.refresh(user)
    return serialize_user(user)

@router.patch("/users/{user_id}/toggle")
def toggle_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(404, "用户不存在")
    if user.id == admin.id: raise HTTPException(400, "不能停用自己")
    user.is_active = not user.is_active; db.commit()
    return {"message": "状态已更新", "is_active": user.is_active}

@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, data: dict, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(404, "用户不存在")
    password = str(data.get("new_password") or "")
    if len(password) < 6: raise HTTPException(400, "密码至少 6 位")
    user.hashed_pwd = hash_password(password); db.commit()
    return {"message": "密码已重置"}

def serialize_user(user: User) -> dict:
    return {"id": user.id, "username": user.username, "email": user.email, "role": user.role, "is_active": user.is_active, "created_at": user.created_at, "last_login": user.last_login}
