from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.core.models import ItemRecord
from megatron.web.app import app

AUTH = {"Authorization": "Bearer dev-admin-token-change-me"}


@pytest.fixture
def client():
    return TestClient(app)


@pytest_asyncio.fixture
async def item():
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        record = ItemRecord(
            item_id="external-1",
            source="twitter_security_list",
            source_ref="list-1",
            title="Stored title",
            content="Stored raw security content",
            url="https://x.example/status/1",
            author="researcher",
            author_name="Security Researcher",
            published_at=now,
            collected_at=now,
            collect_date="2026-07-11",
            tags=["cve"],
            metrics={"like_count": 3},
            raw={"original": "payload"},
        )
        session.add(record)
        await session.commit()
        return record.id


def test_collected_detail_requires_admin(client):
    response = client.get("/ui/data/collected/1", follow_redirects=False)
    assert response.status_code in (303, 307)


def test_collected_list_separates_detail_and_original_links(client, item):
    response = client.get("/ui/data/collected", headers=AUTH)
    assert response.status_code == 200
    assert f'/ui/data/collected/{item}' in response.text
    assert 'href="https://x.example/status/1"' in response.text
    assert "View" in response.text
    assert "Original" in response.text


def test_collected_detail_shows_raw_fields_and_preserves_back_filters(client, item):
    response = client.get(
        f"/ui/data/collected/{item}?keyword=security&author=researcher&collect_date=2026-07-11",
        headers=AUTH,
    )
    assert response.status_code == 200
    assert "Stored title" in response.text
    assert "Stored raw security content" in response.text
    assert "external-1" in response.text
    assert "payload" in response.text
    assert "/ui/data/collected?keyword=security&amp;author=researcher&amp;collect_date=2026-07-11" in response.text
    assert 'href="https://x.example/status/1"' in response.text


def test_collected_detail_missing_item_is_404(client):
    response = client.get("/ui/data/collected/999999", headers=AUTH)
    assert response.status_code == 404
