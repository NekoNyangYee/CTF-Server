from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer()
optional_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def hash_flag(flag: str) -> str:
    return hmac.new(
        settings.flag_hash_secret.encode("utf-8"),
        flag.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_flag(submitted_flag: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_flag(submitted_flag), stored_hash)


def create_access_token(subject_id: int, username: str, role: str = "admin") -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_expire_minutes
    )
    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "username": username,
        "role": role,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc


def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = decode_access_token(credentials.credentials)

    admin_id = payload.get("sub")
    if not admin_id or payload.get("role", "admin") != "admin":
        raise HTTPException(status_code=401, detail="Invalid token")

    admin = db.execute(
        text("SELECT id, username FROM admins WHERE id = :id"),
        {"id": admin_id},
    ).mappings().first()

    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    return dict(admin)


def require_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    payload = decode_access_token(credentials.credentials)

    user_id = payload.get("sub")
    if not user_id or payload.get("role") != "user":
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.execute(
        text("SELECT id, username, nickname, team_id, is_active FROM users WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()

    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


def optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    db: Session = Depends(get_db),
) -> dict[str, Any] | None:
    if credentials is None:
        return None
    payload = decode_access_token(credentials.credentials)
    if payload.get("role") != "user":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None
    user = db.execute(
        text("SELECT id, username, nickname, team_id, is_active FROM users WHERE id = :id"),
        {"id": user_id},
    ).mappings().first()
    if user and not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is disabled")
    return dict(user) if user else None
