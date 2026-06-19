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
    admin_password: str = "Ch@ngeMe#2024!Secure"

    database_url: str = "sqlite+aiosqlite:///./megatron.db"
    base_url: str = "http://localhost:8000"

    @property
    def secret_key_for_sessions(self) -> str:
        return get_session_secret()


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ingest_token: str = "dev-ingest-token-change-me"
    soundwave_repo_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_ingest_settings() -> IngestSettings:
    return IngestSettings()


settings = get_settings()
ingest_settings = get_ingest_settings()
