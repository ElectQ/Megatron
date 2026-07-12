from __future__ import annotations

import os
from functools import lru_cache

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
    return _read_secret("MEGATRON_SESSION_SECRET", ".session_secret", "dev-session-secret-change-me-for-prod")


def get_ingest_token() -> str:
    """Resolve the ingest bearer token at call time.

    Must not be cached: bootstrap generates and persists this token *after*
    this module is imported, so a value snapshotted at import would be stale.
    """
    return _read_secret("MEGATRON_INGEST_TOKEN", ".ingest_token", "dev-ingest-token-change-me")


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
    base_url: str = "http://localhost:8000"

    # Declarative source specs. This directory is the source of truth for every
    # source it declares; source_configs rows are a projection of it.
    sources_dir: str = "./sources"

    @property
    def secret_key_for_sessions(self) -> str:
        return get_session_secret()


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
