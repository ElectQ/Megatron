"""The daily digest page — the thing the doorbell links to.

Not behind the admin login: it has to open from a phone, straight out of a chat
message. But it carries personal analysis ("why this matters to *you*"), so it
is not world-readable either. The compromise is a capability URL: the page is
served only with the right `?k=` and the token is unguessable and rotatable.

The page renders whatever the latest day-bundle run produced for that date, so it
reflects the most recent analysis without anything having to be regenerated.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_day_token
from ..core.db import get_session
from ..core.engine_models import AnalysisRun
from ..core.logging import get_logger
from ..engine.bundle import BUNDLE_SCHEMA, TIERS
from ..ingest.registry import get_source

logger = get_logger(__name__)

router = APIRouter(tags=["day"])
templates = Jinja2Templates(directory="src/megatron/web/templates")

# 必看 and 推荐 both go out over the webhook now, so "已推送" no longer distinguishes
# anything. The lead is just the top of 必看.
TIER_LABEL = {
    "must_see_push": "必看 · 头条",
    "must_see_page": "必看",
    "recommend": "推荐",
    "skim": "速览",
}


def _authorized(key: str) -> bool:
    expected = get_day_token()
    return bool(key) and hmac.compare_digest(key, expected)


async def _latest_bundle(session: AsyncSession, date: str, source_id: str = "") -> dict | None:
    """The most recent completed day-bundle run for `date` (and, if given, source).

    Scans recent runs rather than filtering in SQL: `result` is a JSON column and
    the containment operators differ between SQLite and Postgres. A handful of
    rows is cheap, and it keeps the query portable.
    """
    rows = (
        (
            await session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.status == "completed")
                .order_by(desc(AnalysisRun.id))
                .limit(200)
            )
        )
        .scalars()
        .all()
    )

    for run in rows:
        result = run.result or {}
        if result.get("schema") != BUNDLE_SCHEMA or result.get("date") != date:
            continue
        if not source_id:
            return result
        # A run may cover several sources; it serves this page if it holds any of
        # this source's items.
        if source_id in (result.get("stats") or {}).get("by_source", {}):
            return result
        if result.get("source_id") == source_id:
            return result
    return None


@router.get("/day/{date}")
async def day_page_legacy(date: str, k: str = "", session: AsyncSession = Depends(get_session)):
    """Old source-less URL. Links already sent out must keep working."""
    if not _authorized(k):
        raise HTTPException(status_code=404, detail="Not found")
    bundle = await _latest_bundle(session, date)
    source_id = (bundle or {}).get("source_id") or ""
    if not source_id:
        raise HTTPException(status_code=404, detail="Not found")
    return RedirectResponse(f"/day/{source_id}/{date}?k={k}", status_code=302)


@router.get("/day/{source_id}/{date}", response_class=HTMLResponse)
async def day_page(
    source_id: str,
    date: str,
    request: Request,
    k: str = "",
    session: AsyncSession = Depends(get_session),
):
    if not _authorized(k):
        # 404, not 403: a wrong key should not confirm that the date exists.
        raise HTTPException(status_code=404, detail="Not found")

    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")

    source = await get_source(session, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Not found")

    token_qs = f"?k={k}"
    nav = {
        "prev_date": (day - timedelta(days=1)).strftime("%Y-%m-%d"),
        "next_date": (day + timedelta(days=1)).strftime("%Y-%m-%d"),
        "today": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "token_qs": token_qs,
    }

    # Which layout this source renders with is a data field on the source
    # (`config.page_layout`), not a hardcoded branch on `kind`. A star/fork feed
    # (`feed`) gets the repo-centric aggregation page; everything else the tiered
    # digest. A new layout = a template + a LAYOUTS entry + a source field.
    layout = (source.config or {}).get("page_layout", "digest")
    if layout == "feed":
        return await _github_page(request, session, source, source_id, date, nav)

    bundle = await _latest_bundle(session, date, source_id)

    grouped = {tier: [] for tier in TIERS if tier != "drop"}
    if bundle:
        for item in bundle.get("items") or []:
            # One page per source: a multi-source run renders once per source.
            if item.get("source_id") != source_id:
                continue
            grouped.setdefault(item.get("tier", "skim"), []).append(item)

    return templates.TemplateResponse(
        request,
        "day.html",
        {
            "request": request,
            "date": date,
            "source_id": source_id,
            "title": source.display_name or source_id,
            "bundle": bundle,
            "grouped": grouped,
            "shown": sum(len(v) for v in grouped.values()),
            "tier_label": TIER_LABEL,
            "tier_order": [t for t in TIERS if t != "drop"],
            **nav,
        },
    )


async def _github_page(request, session, source, source_id: str, date: str, nav: dict):
    """The repo-centric page for a star/fork feed.

    The skeleton (repos ranked by convergence, by-who, timeline) is computed
    deterministically from the day's rows; the latest LLM bundle only supplies the
    "what is this repo" annotations, joined on by repo name — so the page renders
    even before any analysis has run.
    """
    from ..core.models import ItemRecord
    from ..engine.github_feed import aggregate_github_day, build_annotations

    records = (
        (
            await session.execute(
                select(ItemRecord)
                .where(ItemRecord.source == source_id, ItemRecord.collect_date == date)
                .order_by(ItemRecord.published_at.desc())
            )
        )
        .scalars()
        .all()
    )
    annotations = build_annotations(await _latest_bundle(session, date, source_id))
    ctx = aggregate_github_day(records, annotations)

    return templates.TemplateResponse(
        request,
        "day_github.html",
        {
            "request": request,
            "date": date,
            "source_id": source_id,
            "title": source.display_name or source_id,
            **ctx,
            **nav,
        },
    )


__all__ = ["router"]
