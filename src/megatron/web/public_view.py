"""The public projection — what a day bundle looks like on the world-readable blog.

Three decisions, in strict order of authority:

    1. the source's `audience`   — hard gate; `personal` never publishes, at all
    2. the operator's override   — within a publishable source, the operator wins
    3. the model's `public` flag — absent means publish; False means hold back

The primary gate is the *source*, not the item, because that is where the risk
actually lives. The items are not the secret: a tweet and a GitHub star are
already world-readable. What a stream leaks is its *curation* — the GitHub feed's
every event is public, yet publishing it exposes who you follow. That is a
property of the stream, so it is settled in the source config (a deliberate edit),
where no mis-marked item and no stray click in the admin can undo it.

Inside a publishable source the default is to publish: the content is already
public and the personal framing (`why_for_me`, scores) is stripped on the way out
regardless, so a `False` from the model is it saying "this specific one is
sensitive" rather than the blog's only line of defence.

Overrides live in `publication_overrides`, never by rewriting the run — the run is
the record of what the model actually said, and that record is what tells you the
prompt needs fixing. A day-level override (`item_id == ""`) set to false takes the
whole day off the blog regardless of its items.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.engine_models import AnalysisRun, PublicationOverride
from ..core.models import SourceConfig
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


@dataclass
class Policy:
    """Who may publish, and who has the final say within them.

    `audience` is the hard gate and it is deliberately *not* overridable: what a
    stream reveals is a property of the stream, not of any one item. The GitHub
    feed's items are individually public GitHub events, yet publishing them
    exposes *who you follow* — so the whole source stays personal, and no
    mis-marked item and no stray click in the admin can leak it. Changing that
    means editing the source's config, which is the deliberate act it should be.
    """

    # source_id -> personal | public | both
    audience: dict[str, str] = field(default_factory=dict)
    overrides: Overrides = field(default_factory=Overrides)

    def source_public(self, source_id: str) -> bool:
        return self.audience.get(source_id, "personal") != "personal"


EMPTY = Policy()


async def load_policy(session: AsyncSession) -> Policy:
    """Load the source audiences and every operator override (both are small
    tables, read whole rather than joined per bundle)."""
    audience = {
        sc.name: (sc.audience or "personal")
        for sc in (await session.execute(select(SourceConfig))).scalars().all()
    }
    ov = Overrides()
    for r in (await session.execute(select(PublicationOverride))).scalars().all():
        key = (r.source_id, r.date)
        if r.item_id:
            ov.items.setdefault(key, {})[r.item_id] = r.published
        else:
            ov.days[key] = r.published
    return Policy(audience=audience, overrides=ov)


def _public_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _STRIP and not k.startswith("_")}


def is_public(item: dict, source_id: str, date: str, policy: Policy = EMPTY) -> bool:
    """The effective decision for one item.

        source is personal   → never (hard gate, beats the operator)
        operator overrode it → the operator
        otherwise            → publish, unless the model explicitly held it back

    Note the default inside a publishable source: `public` absent (None) means
    publish. The content is already-public security news and the personal framing
    is stripped on the way out, so the blog should carry the day by default; an
    explicit `False` is the model saying "this one is genuinely sensitive".
    """
    if not policy.source_public(source_id):
        return False
    override = policy.overrides.item(source_id, date, str(item.get("id", "")))
    if override is not None:
        return override
    return item.get("public") is not False


def public_items(bundle: dict, policy: Policy = EMPTY) -> list[dict]:
    """The public, personal-stripped items of a bundle, in the bundle's order."""
    source_id = bundle.get("source_id", "")
    date = bundle.get("date", "")
    if not policy.source_public(source_id):
        return []
    if policy.overrides.day_hidden(source_id, date):
        return []
    return [
        _public_item(i)
        for i in (bundle.get("items") or [])
        if is_public(i, source_id, date, policy)
    ]


def has_public(bundle: dict, policy: Policy = EMPTY) -> bool:
    return bool(public_items(bundle, policy))


def public_view(bundle: dict, policy: Policy = EMPTY) -> dict:
    """A bundle reduced to its public, stripped items — grouped by tier for render."""
    items = public_items(bundle, policy)
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
    policy = await load_policy(session)
    out: list[dict] = []
    for result in await latest_bundles(session):
        pubs = public_items(result, policy)
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
    "Policy",
    "has_public",
    "is_public",
    "latest_bundles",
    "load_policy",
    "public_days",
    "public_items",
    "public_recent",
    "public_view",
]
