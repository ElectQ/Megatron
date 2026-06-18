from __future__ import annotations

import hmac
import secrets

import bcrypt
from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings


def safe_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


def generate_token(nbytes: int = 32) -> str:
    """Generate a random URL-safe token (for admin/ingest auth)."""
    return secrets.token_urlsafe(nbytes)


def hash_password(plain: str) -> str:
    """Hash a password with bcrypt (truncates to 72 bytes per bcrypt spec)."""
    return bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode()[:72], hashed.encode())
    except Exception:
        return False


def mask(value: str, keep: int = 4) -> str:
    """Mask a secret for UI display, keeping only the last `keep` chars."""
    if not value or len(value) <= keep:
        return "*" * len(value) if value else ""
    return "*" * (len(value) - keep) + value[-keep:]


class IngestAuth:
    """Bearer token auth for ingest endpoints (Soundwave push)."""

    def __init__(self, expected: str):
        self._expected = expected

    async def __call__(
        self,
        authorization: str | None = Header(default=None),
    ) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not safe_eq(token, self._expected):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid ingest token",
            )
        return token


async def authenticate_user(session: AsyncSession, username: str, password: str):
    """Verify username+password against DB. Returns User or None."""
    from .engine_models import User

    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


class SessionAuth:
    """Session-based auth via signed cookie. All UI/admin routes require this.

    Auth flow: POST /ui/login with username+password → verify against DB →
    set signed session cookie. Subsequent requests carry the cookie.
    Fallback: bearer admin_token (for API automation).

    Behavior on auth failure:
    - /ui/* routes → raise RedirectLoginException (caught → 303 to /ui/login)
    - /api/* routes → raise HTTPException 401 JSON
    """

    async def __call__(self, request: Request) -> dict:
        session_user = request.session.get("user")
        if session_user:
            return session_user

        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()
            if safe_eq(token, settings.admin_token):
                return {"username": "token-api", "display_name": "API"}

        # UI routes: redirect to login page (browser-friendly)
        if request.url.path.startswith("/ui"):
            raise RedirectLoginException()

        # API routes: 401 JSON (programmatic-friendly)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )


class RedirectLoginException(Exception):
    """Raised by SessionAuth for UI routes to trigger a redirect to /ui/login."""


admin_auth = SessionAuth()


__all__ = [
    "safe_eq",
    "generate_token",
    "hash_password",
    "verify_password",
    "mask",
    "authenticate_user",
    "IngestAuth",
    "SessionAuth",
    "admin_auth",
]
