from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_secret(env_var: str, file_name: str, default: str) -> str:
    """Read a secret from env var, or fall back to persisted file."""
    val = os.getenv(env_var)
    if val:
        return val
    try:
        with open(f"/app/data/{file_name}") as f:
            return f.read().strip()
    except OSError:
        pass
    return default


def get_admin_token() -> str:
    return _read_secret("MEGATRON_ADMIN_TOKEN", ".admin_token", "dev-admin-token-change-me")


def get_session_secret() -> str:
    return _read_secret(
        "MEGATRON_SESSION_SECRET", ".session_secret", "dev-session-secret-change-me-for-prod"
    )


def get_ingest_token() -> str:
    """Resolve the ingest bearer token at call time.

    Must not be cached: bootstrap generates and persists this token *after*
    this module is imported, so a value snapshotted at import would be stale.
    """
    return _read_secret("MEGATRON_INGEST_TOKEN", ".ingest_token", "dev-ingest-token-change-me")


def get_day_token() -> str:
    """Unguessable key for the daily digest page.

    The page carries personal analysis ("why this matters to you"), so it is not
    world-readable — but it must open from a phone, straight out of a chat
    message, without a login. A capability URL buys both.
    """
    return _read_secret("MEGATRON_DAY_TOKEN", ".day_token", "dev-day-token-change-me")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEGATRON_",
        extra="ignore",
    )

    env: str = "development"
    admin_token: str = "dev-admin-token-change-me"
    session_secret: str = "dev-session-secret-change-me-for-prod"
    master_key: str = ""
    # Empty means "generate a random one at first boot and log it" (see bootstrap).
    admin_password: str = ""

    database_url: str = "sqlite+aiosqlite:///./megatron.db"

    # Where this install answers from, as seen by the person reading the push.
    # Every message ends in a "查看今日详情" link built from this, so a loopback
    # value means every push you send is a dead end on the reader's phone. The
    # default is fine for a laptop and is refused in production — see
    # `validate_runtime_settings`.
    base_url: str = "http://localhost:8000"

    # The product profile: our design as files, not baked into the framework.
    #   config/sources/*.yaml  — source specs (authoritative; DB is a projection)
    #   config/prompts/*.md     — prompt bodies (seeds; UI edits win after)
    #   config/tasks/*.yaml     — analysis tasks (seeds)
    #   config/policy.yaml      — filtering defaults (caps + politics blocklist)
    config_dir: str = "./config"

    @property
    def secret_key_for_sessions(self) -> str:
        return get_session_secret()

    @property
    def sources_dir(self) -> str:
        """Source specs live under the profile; fall back to the legacy top-level
        ``./sources`` so an older deployment keeps working."""
        import os

        preferred = os.path.join(self.config_dir, "sources")
        if os.path.isdir(preferred) or not os.path.isdir("./sources"):
            return preferred
        return "./sources"

    @property
    def policy_path(self) -> str:
        import os

        return os.path.join(self.config_dir, "policy.yaml")

    @property
    def base_url_is_local(self) -> bool:
        """Would a link built from this open on someone else's phone? No."""
        host = urlparse(self.base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""} or host.endswith(".local")


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEGATRON_",
        extra="ignore",
    )

    ingest_token: str = "dev-ingest-token-change-me"
    soundwave_repo_url: str = ""

    # Reject pushes for source_ids that are not registered. When true, an
    # unknown source_id still 404s but a disabled registry row is created so
    # an operator can enable it from the UI instead of hand-writing SQL.
    ingest_auto_register: bool = False

    # off | backfill | always — see docs. `backfill` only touches MCP when the
    # day has zero rows in `items`; the analysis layer always reads the DB.
    mcp_live_fetch: str = "backfill"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_ingest_settings() -> IngestSettings:
    return IngestSettings()


settings = get_settings()
ingest_settings = get_ingest_settings()
