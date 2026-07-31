"""Password and opaque bearer-token authentication."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_manager.database import database_session
from campaign_manager.models import AuthToken, User

PASSWORD_MINIMUM_LENGTH = 12
TOKEN_LIFETIME = timedelta(days=30)
_password_hash = PasswordHash.recommended()
_bearer = HTTPBearer(auto_error=False)


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not normalized or "@" not in normalized or len(normalized) > 320:
        raise ValueError("A valid email address is required")
    return normalized


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MINIMUM_LENGTH:
        raise ValueError(f"Password must contain at least {PASSWORD_MINIMUM_LENGTH} characters")


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(database: Session, user: User) -> tuple[str, AuthToken]:
    raw_token = secrets.token_urlsafe(32)
    token = AuthToken(
        token_hash=_token_digest(raw_token),
        user_id=user.id,
        expires_at=datetime.now(UTC) + TOKEN_LIFETIME,
    )
    database.add(token)
    database.commit()
    database.refresh(token)
    return raw_token, token


def authenticate(database: Session, email: str, password: str) -> User | None:
    try:
        normalized = normalize_email(email)
    except ValueError:
        return None
    user = database.scalar(select(User).where(User.email == normalized))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    database: Session = Depends(database_session),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise unauthorized
    token = database.scalar(
        select(AuthToken).where(AuthToken.token_hash == _token_digest(credentials.credentials))
    )
    if token is None:
        raise unauthorized
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        database.delete(token)
        database.commit()
        raise unauthorized
    if not token.user.is_active:
        raise unauthorized
    return token.user


def revoke_token(database: Session, raw_token: str) -> None:
    token = database.scalar(
        select(AuthToken).where(AuthToken.token_hash == _token_digest(raw_token))
    )
    if token is not None:
        database.delete(token)
        database.commit()
