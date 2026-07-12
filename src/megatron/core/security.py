from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

import bcrypt
from fastapi import Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_admin_token, get_session_secret, settings


ENC_PREFIX = "enc:v1:"
SENSITIVE_CONFIG_KEYS = (
    "api_key",
    "bot_token",
    "webhook_url",
    "secret",
    "key",
    "access_token",
    "token",
)


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


def is_sensitive_key(key: str) -> bool:
    """Return whether a config key likely contains a secret."""
    low = key.lower()
    return any(s in low for s in SENSITIVE_CONFIG_KEYS)


def _fernet():
    """Build a Fernet instance from MEGATRON_MASTER_KEY.

    Existing plaintext DB rows remain readable. Encryption is activated only
    when a master key is configured, so local/dev installs do not break during
    upgrade.
    """
    if not settings.master_key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise RuntimeError(
            "Secret encryption requires the 'cryptography' package. "
            "Install project dependencies after adding MEGATRON_MASTER_KEY."
        ) from e

    raw = settings.master_key.strip()
    try:
        return Fernet(raw.encode())
    except Exception:
        digest = hashlib.sha256(raw.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    """Encrypt a single secret value when MEGATRON_MASTER_KEY is configured."""
    if not value or value.startswith(ENC_PREFIX):
        return value
    f = _fernet()
    if not f:
        return value
    return ENC_PREFIX + f.encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Decrypt an encrypted secret; return legacy plaintext unchanged."""
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return value
    f = _fernet()
    if not f:
        raise RuntimeError("Encrypted secret found, but MEGATRON_MASTER_KEY is not configured")
    token = value.removeprefix(ENC_PREFIX).encode()
    return f.decrypt(token).decode()


def encrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Encrypt sensitive top-level string fields in a plugin config."""
    out: dict[str, Any] = {}
    for k, v in (config or {}).items():
        if is_sensitive_key(k) and isinstance(v, str):
            out[k] = encrypt_secret(v)
        else:
            out[k] = v
    return out


def decrypt_config(config: dict[str, Any]) -> dict[str, Any]:
    """Decrypt sensitive top-level string fields in a plugin config."""
    out: dict[str, Any] = {}
    for k, v in (config or {}).items():
        if is_sensitive_key(k) and isinstance(v, str):
            out[k] = decrypt_secret(v)
        else:
            out[k] = v
    return out


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a display-safe copy of config with sensitive values masked."""
    visible = decrypt_config(config or {})
    for k, v in list(visible.items()):
        if is_sensitive_key(k) and isinstance(v, str) and v:
            visible[k] = mask(v)
    return visible


def validate_runtime_settings() -> None:
    """Fail fast on unsafe production defaults."""
    if settings.env.lower() not in {"prod", "production"}:
        return
    weak = []
    if get_admin_token() == "dev-admin-token-change-me" or get_admin_token().startswith(
        "change-me"
    ):
        weak.append("MEGATRON_ADMIN_TOKEN")
    if get_session_secret() == "dev-session-secret-change-me-for-prod" or (
        get_session_secret().startswith("change-me")
    ):
        weak.append("MEGATRON_SESSION_SECRET")
    if not settings.master_key or settings.master_key.startswith("change-me"):
        weak.append("MEGATRON_MASTER_KEY")
    if not settings.admin_password:
        weak.append("MEGATRON_ADMIN_PASSWORD")
    from ..config import get_ingest_token

    ingest_token = get_ingest_token()
    if ingest_token == "dev-ingest-token-change-me" or ingest_token.startswith("change-me"):
        weak.append("MEGATRON_INGEST_TOKEN")
    if weak:
        raise RuntimeError("Unsafe production configuration: " + ", ".join(weak))


class IngestAuth:
    """Bearer token auth for ingest endpoints (Soundwave push).

    ``expected=None`` resolves the token per request. Bootstrap generates and
    persists the token after import time, so a value pinned at construction
    would keep the stale default alive.
    """

    def __init__(self, expected: str | None = None):
        self._expected = expected

    @property
    def expected(self) -> str:
        if self._expected is not None:
            return self._expected
        from ..config import get_ingest_token

        return get_ingest_token()

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
        if not safe_eq(token, self.expected):
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
            if safe_eq(token, get_admin_token()):
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
    "is_sensitive_key",
    "encrypt_secret",
    "decrypt_secret",
    "encrypt_config",
    "decrypt_config",
    "mask_config",
    "validate_runtime_settings",
    "authenticate_user",
    "IngestAuth",
    "SessionAuth",
    "admin_auth",
]
