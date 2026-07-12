"""The public projection — what a day bundle looks like on the world-readable blog.

The gate is per-item: only items the analysis marked `public: true` are ever
shown, and even those are stripped of the personal framing (`why_for_me`, scores)
— the blog carries objective facts (a disclosed CVE, a released tool), never the
"why this matters to *you*". Everything else stays behind the capability token.

Default private: an item with no `public` flag is treated as private, so a bundle
with nothing public simply does not exist as far as the frontend is concerned.
"""

from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.engine_models import AnalysisRun
from ..engine.bundle import BUNDLE_SCHEMA

# Personal fields removed on the way out. `content` (the original public post/
# repo) and `one_liner` (an objective one-line summary) stay; the personal
# rationale and the private scores do not.
_STRIP = ("why_for_me", "scores")


def _public_item(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in _STRIP and not k.startswith("_")}


def public_items(bundle: dict) -> list[dict]:
    """The public, personal-stripped items of a bundle, in the bundle's order."""
    return [_public_item(i) for i in (bundle.get("items") or []) if i.get("public") is True]


def has_public(bundle: dict) -> bool:
    return any(i.get("public") is True for i in (bundle.get("items") or []))


def public_view(bundle: dict) -> dict:
    """A bundle reduced to its public, stripped items — grouped by tier for render."""
    items = public_items(bundle)
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


async def public_recent(session: AsyncSession, limit: int = 40) -> list[dict]:
    """Recent day bundles that have at least one public item, newest first.

    One entry per (source, date). Scans recent completed runs (JSON result column,
    portable across SQLite/Postgres), like day_api._latest_bundle.
    """
    rows = (
        (
            await session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.status == "completed")
                .order_by(desc(AnalysisRun.id))
                .limit(300)
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
        n = sum(1 for i in (result.get("items") or []) if i.get("public") is True)
        if n == 0:
            continue
        seen.add(key)
        out.append(
            {
                "source_id": source_id,
                "date": date,
                "title": result.get("title") or source_id,
                "count": n,
            }
        )
        if len(out) >= limit:
            break
    return out


__all__ = ["has_public", "public_items", "public_recent", "public_view"]
