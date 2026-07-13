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


# ------------------------------------------------------------------- base_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://0.0.0.0:8000",
        "http://megatron.local",
        "",
    ],
)
def test_a_loopback_base_url_is_recognised_as_unreachable(monkeypatch, url):
    from megatron.config import Settings

    monkeypatch.setenv("MEGATRON_BASE_URL", url)
    assert Settings().base_url_is_local


@pytest.mark.parametrize(
    "url", ["https://megatron.example.com", "http://203.0.113.10:8000", "https://x.y.z/megatron"]
)
def test_a_real_address_is_not(monkeypatch, url):
    from megatron.config import Settings

    monkeypatch.setenv("MEGATRON_BASE_URL", url)
    assert not Settings().base_url_is_local


def test_prod_boots_on_a_loopback_base_url_but_warns(monkeypatch):
    """base_url is a UI setting now (系统设置 → 域名), so refusing to boot would
    trap the operator out of the very page where they'd fix it. It warns instead
    and lets the app come up."""
    import megatron.core.security as sec

    monkeypatch.setattr(sec.settings, "env", "prod")
    monkeypatch.setattr(sec.settings, "master_key", "k" * 32)
    monkeypatch.setattr(sec.settings, "admin_password", "pw")
    monkeypatch.setenv("MEGATRON_ADMIN_TOKEN", "strong-admin")
    monkeypatch.setenv("MEGATRON_SESSION_SECRET", "strong-session")
    monkeypatch.setenv("MEGATRON_INGEST_TOKEN", "strong-ingest")
    monkeypatch.setattr(sec.settings, "base_url", "http://localhost:8000")

    sec.validate_runtime_settings()  # loopback base_url no longer raises

    # A genuinely weak secret still refuses.
    monkeypatch.setattr(sec.settings, "admin_password", "")
    with pytest.raises(RuntimeError):
        sec.validate_runtime_settings()


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
