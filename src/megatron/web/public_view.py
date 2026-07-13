"""The public projection — what a day bundle looks like on the world-readable blog.

The gate is per-item: only items marked public are ever shown, and even those are
stripped of the personal framing (`why_for_me`, scores) — the blog carries
objective facts (a disclosed CVE, a released tool), never the "why this matters to
*you*". Everything else stays behind the capability token.

Default private: an item with no `public` flag is treated as private, so a bundle
with nothing public simply does not exist as far as the frontend is concerned.

Two voices decide "public", and the operator's wins:

    effective_public(item) = operator override, if any, else the analysis's flag

Overrides live in `publication_overrides` (see the model), never by rewriting the
run — the run is the record of what the model actually said, and that record is
what tells you the prompt needs fixing. A day-level override (`item_id == ""`) set
to false takes the whole day off the blog regardless of its items.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.engine_models import AnalysisRun, PublicationOverride
from ..engine.bundle import BUNDLE_SCHEMA

# Personal fields removed on the way out. `content` (the original public post/
# repo) and `one_liner` (an objective one-line summary) stay; the personal
# rationale and the private scores do not.
_STRIP = ("why_for_me", "scores")

# Tier priority — lower is more prominent. Used to pick a day's headline teaser.
_TIER_RANK = {"must_see_push": 0, "must_see_page": 1, "recommend": 2, "skim": 3}


@dataclass
class Overrides:
    """Operator publish decisions, indexed for lookup by (source, date)."""

    # (source_id, date) -> published?   — the whole day
    days: dict[tuple[str, str], bool] = field(default_factory=dict)
    # (source_id, date) -> {item_id: published?}
    items: dict[tuple[str, str], dict[str, bool]] = field(default_factory=dict)

    def day_hidden(self, source_id: str, date: str) -> bool:
        return self.days.get((source_id, date)) is False

    def item(self, source_id: str, date: str, item_id: str) -> bool | None:
        return self.items.get((source_id, date), {}).get(item_id)


EMPTY = Overrides()


async def load_overrides(session: AsyncSession) -> Overrides:
    """Load every operator override. Small table (one row per decision), so it is
    read whole rather than joined per bundle."""
    rows = (await session.execute(select(PublicationOverride))).scalars().all()
    ov = Overrides()
    for r in rows:
        key = (r.source_id, r.date)
        if r.item_id:
            ov.items.setdefault(key, {})[r.item_id] = r.published
        else:
            ov.days[key] = r.published
    return ov


def _public_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _STRIP and not k.startswith("_")}


def is_public(item: dict, source_id: str, date: str, ov: Overrides = EMPTY) -> bool:
    """The effective decision for one item: the operator's, else the analysis's."""
    override = ov.item(source_id, date, str(item.get("id", "")))
    return item.get("public") is True if override is None else override


def public_items(bundle: dict, ov: Overrides = EMPTY) -> list[dict]:
    """The public, personal-stripped items of a bundle, in the bundle's order."""
    source_id = bundle.get("source_id", "")
    date = bundle.get("date", "")
    if ov.day_hidden(source_id, date):
        return []
    return [
        _public_item(i)
        for i in (bundle.get("items") or [])
        if is_public(i, source_id, date, ov)
    ]


def has_public(bundle: dict, ov: Overrides = EMPTY) -> bool:
    return bool(public_items(bundle, ov))


def public_view(bundle: dict, ov: Overrides = EMPTY) -> dict:
    """A bundle reduced to its public, stripped items — grouped by tier for render."""
    items = public_items(bundle, ov)
    grouped: dict[str, list[dict]] = {}
    for it in items:
        grouped.setdefault(it.get("tier", "skim"), []).append(it)
    return {
        "source_id": bundle.get("source_id", ""),
        "date": bundle.get("date", ""),
        "title": bundle.get("title") or bundle.get("source_id", ""),
        "items": items,
        "grouped": grouped,
        "count": len(items),
    }


async def latest_bundles(session: AsyncSession, limit_runs: int = 300) -> list[dict]:
    """The authoritative bundle per (source, date), newest run first.

    The newest run for a (source, date) wins — the same one the post page renders
    via `_latest_bundle`. Claiming the key here stops an older run from
    resurrecting a day the latest run no longer publishes (which would leave the
    home page listing a day whose article 404s).
    """
    rows = (
        (
            await session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.status == "completed")
                .order_by(desc(AnalysisRun.id))
                .limit(limit_runs)
            )
        )
        .scalars()
        .all()
    )
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for run in rows:
        result = run.result or {}
        if result.get("schema") != BUNDLE_SCHEMA:
            continue
        source_id = result.get("source_id") or ""
        date = result.get("date") or ""
        key = (source_id, date)
        if not source_id or not date or key in seen:
            continue
        seen.add(key)
        out.append(result)
    return out


async def public_recent(session: AsyncSession, limit: int = 40) -> list[dict]:
    """Recent day bundles with at least one effectively-public item, newest first."""
    ov = await load_overrides(session)
    out: list[dict] = []
    for result in await latest_bundles(session):
        pubs = public_items(result, ov)
        if not pubs:
            continue
        # The day's headline item (highest tier) gives the card its teaser + tags.
        lead = min(pubs, key=lambda i: _TIER_RANK.get(i.get("tier", "skim"), 9))
        out.append(
            {
                "source_id": result.get("source_id", ""),
                "date": result.get("date", ""),
                "title": result.get("title") or result.get("source_id", ""),
                "count": len(pubs),
                "teaser": lead.get("one_liner") or "",
                "tags": [t for t in (lead.get("topics") or [])][:3],
            }
        )
        if len(out) >= limit:
            break
    return out


async def public_days(session: AsyncSession, limit_days: int = 30) -> list[dict]:
    """Public digests grouped by date (newest first) — the blog's day-at-a-glance.

    Each day lists every source (安全推送流) that published something that day, so
    a reader can browse the whole day across streams.
    """
    flat = await public_recent(session, limit=200)
    by_date: dict[str, list[dict]] = {}
    for entry in flat:
        by_date.setdefault(entry["date"], []).append(entry)
    days = [
        {"date": date, "streams": sorted(streams, key=lambda s: s["source_id"])}
        for date, streams in by_date.items()
    ]
    days.sort(key=lambda d: d["date"], reverse=True)
    return days[:limit_days]


__all__ = [
    "EMPTY",
    "Overrides",
    "has_public",
    "is_public",
    "latest_bundles",
    "load_overrides",
    "public_days",
    "public_items",
    "public_recent",
    "public_view",
]
