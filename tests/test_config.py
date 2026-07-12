from __future__ import annotations

import pytest


def test_ingest_settings_honour_megatron_prefix(monkeypatch):
    """MEGATRON_INGEST_TOKEN must actually reach IngestSettings.

    Regression lock: IngestSettings used to omit env_prefix, so the documented
    MEGATRON_INGEST_TOKEN was silently ignored and every deployment ran the
    ingest endpoint on the default dev token.
    """
    from megatron.config import IngestSettings

    monkeypatch.setenv("MEGATRON_INGEST_TOKEN", "prefixed-token")
    assert IngestSettings().ingest_token == "prefixed-token"


def test_unprefixed_ingest_token_is_ignored(monkeypatch):
    from megatron.config import IngestSettings

    monkeypatch.delenv("MEGATRON_INGEST_TOKEN", raising=False)
    monkeypatch.setenv("INGEST_TOKEN", "unprefixed")
    assert IngestSettings().ingest_token != "unprefixed"


def test_get_ingest_token_reads_env_at_call_time(monkeypatch):
    """Bootstrap persists the token after import, so it must not be cached."""
    from megatron.config import get_ingest_token

    monkeypatch.setenv("MEGATRON_INGEST_TOKEN", "first")
    assert get_ingest_token() == "first"
    monkeypatch.setenv("MEGATRON_INGEST_TOKEN", "second")
    assert get_ingest_token() == "second"


def test_mcp_live_fetch_defaults_to_backfill(monkeypatch):
    from megatron.config import IngestSettings

    monkeypatch.delenv("MEGATRON_MCP_LIVE_FETCH", raising=False)
    assert IngestSettings().mcp_live_fetch == "backfill"


def test_ingest_auto_register_defaults_off(monkeypatch):
    from megatron.config import IngestSettings

    monkeypatch.delenv("MEGATRON_INGEST_AUTO_REGISTER", raising=False)
    assert IngestSettings().ingest_auto_register is False


@pytest.mark.asyncio
async def test_ingest_auth_resolves_token_lazily(monkeypatch):
    """IngestAuth() with no pinned token must follow the env at request time."""
    from fastapi import HTTPException

    from megatron.core.security import IngestAuth

    auth = IngestAuth()
    monkeypatch.setenv("MEGATRON_INGEST_TOKEN", "rotated")

    assert await auth(authorization="Bearer rotated") == "rotated"

    with pytest.raises(HTTPException) as exc:
        await auth(authorization="Bearer stale")
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        await auth(authorization=None)
    assert exc.value.status_code == 401
