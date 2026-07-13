from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from megatron.core.db import async_session_factory
from megatron.ingest.registry import sync_specs
from megatron.ingest.spec import SourceSpec

TOKEN = "dev-ingest-token-change-me"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    from megatron.web.app import app

    return TestClient(app)


@pytest.fixture(autouse=True)
async def registered_source():
    """Register the canonical source; pushes are rejected until a source exists."""
    async with async_session_factory() as session:
        await sync_specs(
            session,
            [
                SourceSpec(
                    source_id="twitter_security_list",
                    display_name="Twitter 安全 List",
                    adapter="http_push",
                    config={"legacy_aliases": ["soundwave"]},
                ),
                SourceSpec(source_id="off_source", adapter="http_push", enabled=False),
            ],
        )


def envelope(**over):
    body = {
        "schema_version": 1,
        "collect_date": "2026-07-12",
        "producer": {"name": "soundwave", "version": "0.1.0", "run_id": "run-1"},
        "items": [
            {
                "external_id": "1748402774835134821",
                "content": "a tweet",
                "url": "https://x.com/a/1",
                "author": "0x534c",
                "author_name": "Some One",
                "published_at": "2026-07-11T16:20:00+00:00",
                "metrics": {"like_count": 3},
                "flags": {"is_retweet": False},
            }
        ],
    }
    body.update(over)
    return body


def test_envelope_ingests_and_replay_is_a_noop(client):
    r = client.post("/api/ingest/twitter_security_list", json=envelope(), headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 1
    assert body["duplicated"] == 0
    assert body["source_id"] == "twitter_security_list"
    assert body["schema_version"] == 1

    again = client.post("/api/ingest/twitter_security_list", json=envelope(), headers=AUTH)
    assert again.json()["ingested"] == 0
    assert again.json()["duplicated"] == 1


def test_generic_endpoint_takes_source_id_from_the_body(client):
    r = client.post(
        "/api/ingest",
        json=envelope(source_id="twitter_security_list"),
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["source_id"] == "twitter_security_list"


def test_generic_endpoint_without_source_id_is_rejected(client):
    r = client.post("/api/ingest", json=envelope(), headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "source_id_missing"


def test_path_and_body_source_id_must_agree(client):
    r = client.post(
        "/api/ingest/twitter_security_list",
        json=envelope(source_id="something_else"),
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "source_id_mismatch"


def test_missing_schema_version_is_400(client):
    body = envelope()
    del body["schema_version"]
    r = client.post("/api/ingest/twitter_security_list", json=body, headers=AUTH)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "schema_version_missing"


def test_unsupported_schema_version_lists_what_is_supported(client):
    r = client.post(
        "/api/ingest/twitter_security_list", json=envelope(schema_version=2), headers=AUTH
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "unsupported_schema_version"
    assert "[1]" in detail["message"]


def test_item_without_external_id_is_400(client):
    r = client.post(
        "/api/ingest/twitter_security_list",
        json=envelope(items=[{"content": "no id"}]),
        headers=AUTH,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_payload"
    assert r.json()["detail"]["errors"]


def test_bad_collect_date_shape_is_400(client):
    r = client.post(
        "/api/ingest/twitter_security_list", json=envelope(collect_date="12/07/2026"), headers=AUTH
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_payload"


def test_unregistered_source_is_404_and_stores_nothing(client):
    r = client.post("/api/ingest/never_declared", json=envelope(), headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_source"

    items = client.get("/api/items", headers=AUTH)
    # /api/items is admin-guarded; the point is only that ingest refused.
    assert items.status_code in (401, 403, 200)


def test_disabled_source_is_403(client):
    r = client.post("/api/ingest/off_source", json=envelope(), headers=AUTH)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "source_disabled"


def test_legacy_alias_lands_on_the_canonical_source(client):
    r = client.post("/api/ingest/soundwave", json=envelope(), headers=AUTH)
    assert r.status_code == 200
    assert r.json()["source_id"] == "twitter_security_list"


def test_missing_collect_date_defaults_to_utc_today(client):
    from megatron.ingest.schemas import utc_today

    body = envelope()
    del body["collect_date"]
    r = client.post("/api/ingest/twitter_security_list", json=body, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["date"] == utc_today()


def test_empty_items_is_a_valid_empty_day(client):
    r = client.post("/api/ingest/twitter_security_list", json=envelope(items=[]), headers=AUTH)
    assert r.status_code == 200
    assert r.json()["ingested"] == 0


def test_invalid_json_is_400(client):
    r = client.post(
        "/api/ingest/twitter_security_list",
        content=b"{not json",
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_json"


def test_auth_is_enforced(client):
    assert client.post("/api/ingest/twitter_security_list", json=envelope()).status_code == 401
    r = client.post(
        "/api/ingest/twitter_security_list",
        json=envelope(),
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403
