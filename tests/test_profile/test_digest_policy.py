"""DB-backed digest templates + policy: seeded from files, editable, DB wins."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from megatron.core.engine_models import DigestTemplate, Policy
from megatron.profile.loader import (
    resolve_digest_body,
    seed_digests,
    seed_policy,
)
from megatron.profile.policy import resolve_policy


@pytest_asyncio.fixture
async def session():
    from megatron.core.db import async_session_factory

    async with async_session_factory() as s:
        yield s


# --------------------------------------------------------------- digests


@pytest.mark.asyncio
async def test_seed_digests_from_the_shipped_files(session):
    r = await seed_digests(session, "config/digests")
    assert set(r["seeded"]) >= {"digest", "feed"}
    rows = (await session.execute(select(DigestTemplate))).scalars().all()
    assert {x.style for x in rows} >= {"digest", "feed"}

    # Idempotent — second run seeds nothing.
    r2 = await seed_digests(session, "config/digests")
    assert r2["seeded"] == []


@pytest.mark.asyncio
async def test_the_db_body_wins_over_the_file(session):
    await seed_digests(session, "config/digests")
    row = (
        await session.execute(select(DigestTemplate).where(DigestTemplate.style == "feed"))
    ).scalar_one()
    row.body = "EDITED IN UI {{ day_url }}"
    await session.commit()

    body = await resolve_digest_body(session, "feed", "config")
    assert body == "EDITED IN UI {{ day_url }}"


@pytest.mark.asyncio
async def test_an_unseeded_style_falls_back_to_the_file(session):
    # No DB rows; resolver reads config/digests/feed.md from disk.
    body = await resolve_digest_body(session, "feed", "config")
    assert "查看今日详情" in body


# ---------------------------------------------------------------- policy


@pytest.mark.asyncio
async def test_seed_policy_once_then_resolve_reads_the_row(session):
    r = await seed_policy(session, "config/policy.yaml")
    assert r["seeded"] is True

    row = (await session.execute(select(Policy))).scalar_one()
    row.caps = {**row.caps, "must_see_max": 99}
    await session.commit()

    pol = await resolve_policy(session)
    assert pol["caps"]["must_see_max"] == 99, "the edited DB row wins"
    assert "政治" in pol["politics_blocklist"]


@pytest.mark.asyncio
async def test_resolve_policy_falls_back_to_the_file_when_unseeded(session):
    pol = await resolve_policy(session)  # no Policy row
    assert pol["caps"]["must_see_max"] == 8, "from config/policy.yaml"
