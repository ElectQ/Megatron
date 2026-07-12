from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from megatron.core.db import async_session_factory
from megatron.core.types import Item
from megatron.ingest.health import (
    DISABLED,
    LATE,
    MISSING,
    OK,
    PENDING,
    arrival_deadline,
    today_arrivals,
)
from megatron.ingest.registry import sync_specs
from megatron.ingest.service import IngestService
from megatron.ingest.spec import SourceSpec

DATE = "2026-07-12"
SLA = {"timezone": "Asia/Shanghai", "collect_by": "06:00", "sla_minutes": 90}
# 06:00 +0800 + 90min = 07:30 +0800 = 23:30 UTC on the *previous* day.
DEADLINE = datetime(2026, 7, 11, 23, 30, tzinfo=timezone.utc)


async def register(*specs: SourceSpec):
    async with async_session_factory() as session:
        await sync_specs(session, list(specs))


async def ingest_one(source: str, at: datetime):
    async with async_session_factory() as session:
        await IngestService(session).ingest_items(
            [
                Item(
                    id=f"i-{source}-{at.timestamp()}",
                    source=source,
                    source_ref="",
                    content="c",
                    url="https://x/1",
                    author="a",
                    published_at=at,
                    collected_at=at,
                    collect_date=DATE,
                )
            ]
        )
        # ingested_at defaults to now(); force it so arrival timing is testable.
        from sqlalchemy import text

        await session.execute(
            text("UPDATE items SET ingested_at = :at WHERE source = :s"),
            {"at": at, "s": source},
        )
        await session.commit()


def test_deadline_is_collect_by_plus_sla_in_utc():
    assert arrival_deadline(SLA, DATE) == DEADLINE


def test_no_collect_by_means_no_deadline():
    assert arrival_deadline({"timezone": "UTC"}, DATE) is None
    assert arrival_deadline({}, DATE) is None


@pytest.mark.asyncio
async def test_on_time_arrival_is_ok():
    await register(SourceSpec(source_id="s_ok", schedule_expect=SLA))
    await ingest_one("s_ok", DEADLINE - timedelta(hours=1))

    async with async_session_factory() as session:
        arrivals = {a.source_id: a for a in await today_arrivals(session, DATE)}

    assert arrivals["s_ok"].status == OK
    assert arrivals["s_ok"].item_count == 1


@pytest.mark.asyncio
async def test_arrival_after_the_deadline_is_late_not_missing():
    await register(SourceSpec(source_id="s_late", schedule_expect=SLA))
    await ingest_one("s_late", DEADLINE + timedelta(hours=2))

    async with async_session_factory() as session:
        arrivals = {a.source_id: a for a in await today_arrivals(session, DATE)}

    assert arrivals["s_late"].status == LATE


@pytest.mark.asyncio
async def test_nothing_yet_but_still_inside_the_window_is_pending():
    await register(SourceSpec(source_id="s_wait", schedule_expect=SLA))

    async with async_session_factory() as session:
        arrivals = {
            a.source_id: a
            for a in await today_arrivals(session, DATE, now=DEADLINE - timedelta(minutes=30))
        }

    assert arrivals["s_wait"].status == PENDING


@pytest.mark.asyncio
async def test_nothing_after_the_deadline_is_missing():
    await register(SourceSpec(source_id="s_gone", schedule_expect=SLA))

    async with async_session_factory() as session:
        arrivals = {
            a.source_id: a
            for a in await today_arrivals(session, DATE, now=DEADLINE + timedelta(hours=1))
        }

    assert arrivals["s_gone"].status == MISSING


@pytest.mark.asyncio
async def test_a_disabled_source_is_not_reported_missing():
    await register(SourceSpec(source_id="s_off", enabled=False, schedule_expect=SLA))

    async with async_session_factory() as session:
        arrivals = {
            a.source_id: a
            for a in await today_arrivals(session, DATE, now=DEADLINE + timedelta(hours=5))
        }

    assert arrivals["s_off"].status == DISABLED


@pytest.mark.asyncio
async def test_a_source_with_no_sla_is_never_late():
    await register(SourceSpec(source_id="s_nosla"))
    await ingest_one("s_nosla", datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc))

    async with async_session_factory() as session:
        arrivals = {a.source_id: a for a in await today_arrivals(session, DATE)}

    assert arrivals["s_nosla"].status == OK
