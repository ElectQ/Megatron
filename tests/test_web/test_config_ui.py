"""The config surfaces (digests, policy, system settings) have working APIs."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory

TOKEN = "dev-admin-token-change-me"  # bootstrap hasn't run in tests
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


@pytest_asyncio.fixture(autouse=True)
async def seeded():
    """Seed the digest rows so the API has something to list/edit."""
    from megatron.profile.loader import seed_digests

    async with async_session_factory() as s:
        await seed_digests(s, "config/digests")


def test_digests_list_and_edit(client):
    r = client.get("/api/admin/digests", headers=AUTH)
    assert r.status_code == 200
    styles = {d["style"] for d in r.json()}
    assert {"digest", "feed"} <= styles

    r = client.put(
        "/api/admin/digests/feed",
        headers=AUTH,
        json={
            "body": "⚡ {{ title }}\n{% if day_url %}[详情]({{ day_url }}){% endif %}",
            "is_active": True,
        },
    )
    assert r.status_code == 200
    assert "title" in r.json()["body"]


def test_a_broken_template_is_rejected_not_saved(client):
    r = client.put(
        "/api/admin/digests/feed",
        headers=AUTH,
        json={"body": "{{ this is not valid jinja", "is_active": True},
    )
    assert r.status_code == 400


def test_digest_preview_renders_a_sample(client):
    r = client.post(
        "/api/admin/digests/preview",
        headers=AUTH,
        json={"body": "⚡ {{ title }} · 入库 {{ ingest_total }}", "style": "feed"},
    )
    assert r.status_code == 200 and r.json()["ok"]
    assert "⚡" in r.json()["rendered"]


def test_policy_put_keeps_only_known_caps(client):
    r = client.put(
        "/api/admin/policy",
        headers=AUTH,
        json={"caps": {"must_see_max": 12, "junk": 1}, "politics_blocklist": ["政治", "  ", "x"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["caps"]["must_see_max"] == 12
    assert "junk" not in body["caps"]
    assert body["politics_blocklist"] == ["政治", "x"]


def test_settings_base_url_round_trips_and_flags_loopback(client):
    r = client.put("/api/admin/settings", headers=AUTH, json={"base_url": "http://localhost:8000"})
    assert r.json()["base_url_is_local"] is True

    r = client.put("/api/admin/settings", headers=AUTH, json={"base_url": "https://x.example.com/"})
    assert r.json() == {"base_url": "https://x.example.com", "base_url_is_local": False}
    assert (
        client.get("/api/admin/settings", headers=AUTH).json()["base_url"]
        == "https://x.example.com"
    )


def test_unauthenticated_is_rejected(client):
    assert client.get("/api/admin/digests").status_code == 401
    assert client.get("/api/admin/settings").status_code == 401
