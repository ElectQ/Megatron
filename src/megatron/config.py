from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MEGATRON_",
        extra="ignore",
    )

    env: str = "development"
    admin_token: str = "dev-admin-token-change-me"
    session_secret: str = "dev-session-secret-change-me-for-prod"

    database_url: str = "sqlite+aiosqlite:///./megatron.db"
    base_url: str = "http://localhost:8000"

    @property
    def secret_key_for_sessions(self) -> str:
        return self.session_secret or "dev-session-secret-change-me-for-prod"


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
