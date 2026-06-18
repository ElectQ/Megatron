from __future__ import annotations


import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from megatron.web.app import app


@pytest.fixture
def soundwave_payload() -> dict:
    return {
        "date": "2026-06-16",
        "list_id": "1748402774835134821",
        "list_name": "sec_list",
        "crawled_at": "2026-06-16T15:14:56.646933+00:00",
        "count": 2,
        "tweets": [
            {
                "id": "2066900145321472382",
                "author_handle": "0xTriboulet",
                "author_name": "Steve S.",
                "content": "Interesting WSL privesc bug CVE-2018-0743",
                "url": "https://x.com/0xTriboulet/status/2066900145321472382",
                "published_at": "2026-06-16 15:06:10+00:00",
                "collected_at": "2026-06-16 15:14:56+00:00",
                "like_count": 5,
                "retweet_count": 2,
                "reply_count": 0,
                "view_count": 537,
                "hashtags": [],
                "urls": [],
                "media": {"photos": [], "videos": [], "thumbnails": []},
                "is_retweet": True,
                "is_quote": False,
                "raw": {},
            },
            {
                "id": "2066900145321472399",
                "author_handle": "security",
                "author_name": "Sec News",
                "content": "New 0day in apache",
                "url": "https://x.com/security/status/2066900145321472399",
                "published_at": "2026-06-16 16:00:00+00:00",
                "collected_at": "2026-06-16 16:01:00+00:00",
                "like_count": 10,
                "retweet_count": 3,
                "reply_count": 1,
                "view_count": 1000,
                "hashtags": ["0day"],
                "urls": ["https://example.com/advisory"],
                "media": {"photos": [], "videos": [], "thumbnails": []},
                "is_retweet": False,
                "is_quote": False,
                "raw": {},
            },
        ],
    }


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_ingest_push_idempotent(client, soundwave_payload, monkeypatch):
    monkeypatch.setenv("MEGATRON_ADMIN_TOKEN", "test")
    from megatron.core import security
    from megatron.config import ingest_settings

    monkeypatch.setattr(security, "IngestAuth", security.IngestAuth)
    token = ingest_settings.ingest_token

    headers = {"Authorization": f"Bearer {token}"}

    r1 = await client.post("/api/ingest/twitter", json=soundwave_payload, headers=headers)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["ingested"] == 2
    assert body1["duplicated"] == 0

    r2 = await client.post("/api/ingest/twitter", json=soundwave_payload, headers=headers)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["ingested"] == 0
    assert body2["duplicated"] == 2


@pytest.mark.asyncio
async def test_ingest_rejects_bad_token(client, soundwave_payload):
    r = await client.post(
        "/api/ingest/twitter",
        json=soundwave_payload,
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_and_get_items(client, soundwave_payload, monkeypatch):
    from megatron.config import ingest_settings

    token = ingest_settings.ingest_token
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/api/ingest/twitter", json=soundwave_payload, headers=headers)

    r = await client.get("/api/items?limit=10")
    assert r.status_code == 200
    page = r.json()
    assert page["total_returned"] == 2

    first_id = page["items"][0]["id"]
    r2 = await client.get(f"/api/items/{first_id}")
    assert r2.status_code == 200
    detail = r2.json()
    assert detail["item_id"] in ("2066900145321472382", "2066900145321472399")
