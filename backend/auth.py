from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from database import get_db, User, utcnow
from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
DISABLE_GRACE = timedelta(minutes=5)
PASSWORD_CHANGE_ALLOWLIST = {
    "/api/auth/change-password",
    "/api/auth/me",
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _naive(value):
    if value is None:
        return None
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value


def account_blocked(user: User, now=None) -> bool:
    """True when login and existing tokens must be rejected."""
    now = _naive(now or utcnow())
    effective = _naive(getattr(user, "disable_effective_at", None))
    if effective is not None:
        return now >= effective
    return not bool(user.is_active)


def account_status(user: User, now=None) -> str:
    now = _naive(now or utcnow())
    effective = _naive(getattr(user, "disable_effective_at", None))
    if effective is not None and now < effective:
        return "pending_disable"
    if account_blocked(user, now):
        return "disabled"
    return "active"


def get_current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录已过期，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id = int(sub)
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or account_blocked(user):
        raise credentials_exception
    if bool(getattr(user, "must_change_password", False)):
        path = request.url.path.rstrip("/") or request.url.path
        if path not in PASSWORD_CHANGE_ALLOWLIST:
            raise HTTPException(status_code=403, detail="请先修改密码后再使用系统")
    return user


def require_admin(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    if current_user.role != "admin":
        from audit import record_audit
        record_audit(db, "access_denied", "admin_endpoint", reason="admin_role_required", user=current_user)
        db.commit()
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user
