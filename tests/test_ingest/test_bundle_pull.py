"""bundle_pull against a real Soundwave bundle.

The fixture is a byte-faithful slice of
https://github.com/ElectQ/Soundwave/blob/master/bundles/2026-07-11.json — same
keys, same timestamp formats, real tweets. If Soundwave changes its contract,
these tests are what notices.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from megatron.plugins.sources.bundle_pull import BundlePullSource

FIXTURE = Path(__file__).parent.parent / "fixtures" / "soundwave_bundle_2026-07-11.json"
INDEX_URL = "https://raw.githubusercontent.com/ElectQ/Soundwave/master/bundles/index.json"
DAY_URL = "https://raw.githubusercontent.com/ElectQ/Soundwave/master/bundles/2026-07-11.json"

BUNDLE_BYTES = FIXTURE.read_bytes()
BUNDLE_SHA = hashlib.sha256(BUNDLE_BYTES).hexdigest()


def make_index(sha: str = BUNDLE_SHA, dates=("2026-07-11",)) -> bytes:
    return json.dumps(
        {
            "source_id": "twitter_security_list",
            "schema_version": 1,
            "latest": dates[-1],
            "days": [{"date": d, "count": 3, "sha256": sha} for d in dates],
        }
    ).encode()


def transport(index_bytes: bytes = None, day_status: int = 200):
    index_bytes = index_bytes if index_bytes is not None else make_index()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == INDEX_URL:
            return httpx.Response(200, content=index_bytes)
        if url == DAY_URL:
            if day_status != 200:
                return httpx.Response(day_status)
            return httpx.Response(200, content=BUNDLE_BYTES)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def patched_client(tr):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = tr
        return original(*args, **kwargs)

    return patch("megatron.plugins.sources.bundle_pull.httpx.AsyncClient", factory)


def source(**over):
    return BundlePullSource(source_label="twitter_security_list", index_url=INDEX_URL, **over)


@pytest.mark.asyncio
async def test_real_bundle_maps_without_any_field_mapping():
    """Soundwave's bundle *is* the ingest envelope — no mapping layer involved."""
    with patched_client(transport()):
        items = await source().fetch()

    assert len(items) == 3
    it = items[0]
    assert it.source == "twitter_security_list"  # the source_id, not the plugin name
    assert it.id == "2075834083335147974"  # external_id -> dedup key
    assert it.author == "_RastaMouse"
    assert it.author_name == "Rasta Mouse"
    assert it.collect_date == "2026-07-11"
    assert it.url.startswith("https://x.com/")
    assert it.metrics["view_count"] == 256
    assert it.is_retweet is False
    assert it.tags == ["list:sec_list"]
    assert it.published_at.year == 2026


@pytest.mark.asyncio
async def test_every_item_has_the_dedup_key():
    with patched_client(transport()):
        items = await source().fetch()
    assert all(i.id and i.source for i in items)
    assert len({i.id for i in items}) == len(items)


@pytest.mark.asyncio
async def test_sha_mismatch_skips_the_day_rather_than_ingesting_a_torn_read():
    """index.json and <date>.json are separate CDN objects and can disagree."""
    with patched_client(transport(index_bytes=make_index(sha="0" * 64))):
        items = await source().fetch()
    assert items == []


@pytest.mark.asyncio
async def test_sha_check_can_be_turned_off():
    with patched_client(transport(index_bytes=make_index(sha="0" * 64))):
        items = await source(verify_sha256=False).fetch()
    assert len(items) == 3


@pytest.mark.asyncio
async def test_day_listed_in_index_but_not_yet_published_is_skipped():
    with patched_client(transport(day_status=404)):
        items = await source().fetch()
    assert items == []


@pytest.mark.asyncio
async def test_watermark_skips_days_already_ingested():
    from datetime import datetime, timezone

    with patched_client(transport()):
        items = await source().fetch(
            since=datetime(2026, 7, 12, tzinfo=timezone.utc)  # day after the bundle
        )
    assert items == []


@pytest.mark.asyncio
async def test_max_days_caps_a_cold_start_backfill():
    index = make_index(dates=("2026-07-09", "2026-07-10", "2026-07-11"))
    with patched_client(transport(index_bytes=index)):
        items = await source(max_days=1).fetch()
    # Only the newest day is fetched; the other two URLs 404 in the transport
    # and would have raised had they been requested.
    assert len(items) == 3
