"""Did each source actually show up today?

A collector that dies silently is indistinguishable from a quiet day — the run
completes, the brief goes out, and nothing says half the inputs were missing.
This is what tells them apart.

Derived from `items` rather than `ingest_logs`: the pull path does not always
populate the log's date column, and the rows themselves cannot lie about whether
data arrived.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..core.models import ItemRecord, SourceConfig

logger = get_logger(__name__)

OK = "ok"
LATE = "late"
MISSING = "missing"
PENDING = "pending"
DISABLED = "disabled"


@dataclass
class SourceArrival:
    source_id: str
    display_name: str
    adapter: str
    enabled: bool
    status: str
    item_count: int = 0
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    deadline: datetime | None = None
    meta: dict = field(default_factory=dict)

    def to_api(self) -> dict:
        return {
            "source_id": self.source_id,
            "display_name": self.display_name,
            "adapter": self.adapter,
            "enabled": self.enabled,
            "status": self.status,
            "item_count": self.item_count,
            "first_seen_at": self.first_seen_at.isoformat() if self.first_seen_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
        }


def arrival_deadline(schedule_expect: dict, date: str) -> datetime | None:
    """When this source is late. None means it has no SLA and can never be late."""
    if not schedule_expect:
        return None
    collect_by = (schedule_expect.get("collect_by") or "").strip()
    if not collect_by:
        return None

    tz_name = schedule_expect.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.warning("health.bad_timezone", timezone=tz_name)
        tz = timezone.utc

    try:
        hh, mm = (int(p) for p in collect_by.split(":", 1))
        local = datetime.strptime(date, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=tz)
    except (ValueError, TypeError):
        logger.warning("health.bad_collect_by", collect_by=collect_by, date=date)
        return None

    return (local + timedelta(minutes=int(schedule_expect.get("sla_minutes") or 0))).astimezone(
        timezone.utc
    )


async def today_arrivals(
    session: AsyncSession,
    date: str | None = None,
    now: datetime | None = None,
) -> list[SourceArrival]:
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = now or datetime.now(timezone.utc)

    counts = {
        row[0]: (row[1], row[2], row[3])
        for row in (
            await session.execute(
                select(
                    ItemRecord.source,
                    func.count(),
                    func.min(ItemRecord.ingested_at),
                    func.max(ItemRecord.ingested_at),
                )
                .where(ItemRecord.collect_date == date)
                .group_by(ItemRecord.source)
            )
        ).all()
    }

    sources = (
        (await session.execute(select(SourceConfig).order_by(SourceConfig.name)))
        .scalars()
        .all()
    )

    out: list[SourceArrival] = []
    for sc in sources:
        count, first_seen, last_seen = counts.get(sc.name, (0, None, None))
        deadline = arrival_deadline(sc.schedule_expect or {}, date)

        if not sc.enabled:
            status = DISABLED
        elif count:
            status = LATE if (deadline and first_seen and _aware(first_seen) > deadline) else OK
        elif deadline and now < deadline:
            status = PENDING  # still within its window; not late yet
        elif deadline:
            status = MISSING
        else:
            status = MISSING if count == 0 else OK

        out.append(
            SourceArrival(
                source_id=sc.name,
                display_name=sc.display_name or sc.name,
                adapter=sc.adapter,
                enabled=sc.enabled,
                status=status,
                item_count=count,
                first_seen_at=_aware(first_seen),
                last_seen_at=_aware(last_seen),
                deadline=deadline,
            )
        )
    return out


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; comparisons need them tz-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def missing_sources(arrivals: list[SourceArrival]) -> list[SourceArrival]:
    return [a for a in arrivals if a.status == MISSING]


__all__ = [
    "DISABLED",
    "LATE",
    "MISSING",
    "OK",
    "PENDING",
    "SourceArrival",
    "arrival_deadline",
    "missing_sources",
    "today_arrivals",
]
